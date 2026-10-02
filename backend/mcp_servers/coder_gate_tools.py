"""
coder_gate_tools.py — MCP-сервер с инструментами запроса разрешений для Кодера.

Кодер (OmniCoder) использует эти инструменты вместо прямого выполнения команд.
Каждый инструмент:
  1. Создаёт ActionRequest в БД со статусом pending_supervisor.
  2. Блокирует выполнение Кодера через asyncio.Event.
  3. Возвращает результат (approved/rejected + комментарий) когда Gate принял решение.

Надсмотрщик (supervisor_14b) или человек-оператор снимают блокировку через:
  - mcp_approve_action / mcp_escalate_to_friend_call / mcp_reject_action (api/action_requests.py)
"""

import asyncio
import json
import logging
import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from database import SessionLocal
from models import ActionRequest
from api.action_requests import get_event
from services.sandbox import (
    auto_checkpoint_enabled,
    baseline_warning,
    create_stash_checkpoint,
    materialize_files,
    registered_sandbox_dir,
    sandbox_dir,
)

logger = logging.getLogger("contextus.coder_gate")

mcp = FastMCP(
    "coder_gate",
    instructions="Инструменты запроса разрешений для автономного Кодера (Apprentice-Gate 2.0)"
)

_POLL_INTERVAL = 2.0    # секунды между проверками статуса
_GATE_TIMEOUT = 3600.0  # максимум ожидания (1 час)

# Директория песочницы — берётся из services.sandbox (env CODER_SANDBOX_DIR)
_SANDBOX_DIR = os.getenv("CODER_SANDBOX_DIR", "/tmp/coder_sandbox")


async def _wait_for_decision(request_id: str) -> dict:
    """
    Блокирует выполнение кодера до тех пор, пока Gate не примет решение.
    Возвращает dict с полями: status, feedback (для rejected), error.
    """
    event = get_event(request_id)
    try:
        await asyncio.wait_for(event.wait(), timeout=_GATE_TIMEOUT)
    except asyncio.TimeoutError:
        return {"status": "rejected", "feedback": f"Таймаут ожидания решения Gate ({_GATE_TIMEOUT}с)"}

    # Читаем финальный статус из БД
    db = SessionLocal()
    try:
        req = db.query(ActionRequest).filter(ActionRequest.id == request_id).first()
        if not req:
            return {"status": "rejected", "feedback": "ActionRequest не найден в БД после разблокировки"}
        return {
            "status": req.status,
            "feedback": req.human_comment or req.supervisor_notes or "",
        }
    finally:
        db.close()


def _create_action_request(coder_task_id: str, action_type: str, payload: dict) -> str:
    """Создаёт ActionRequest в БД, ставит задачу для Надсмотрщика в ARQ и возвращает ID."""
    import uuid
    from datetime import datetime
    db = SessionLocal()
    try:
        req = ActionRequest(
            id=str(uuid.uuid4()),
            coder_task_id=coder_task_id,
            action_type=action_type,
            payload=json.dumps(payload, ensure_ascii=False),
            status="pending_supervisor",
        )
        db.add(req)
        db.commit()
        db.refresh(req)
        logger.info(f"📋 ActionRequest создан: {req.id} | type={action_type}")
        request_id = req.id
    finally:
        db.close()

    # Асинхронно ставим задачу Надсмотрщику в ARQ
    async def _enqueue():
        try:
            import redis.asyncio as aioredis
            from arq import create_pool
            from arq.connections import RedisSettings
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            pool = await create_pool(RedisSettings.from_dsn(redis_url))
            await pool.enqueue_job("run_supervisor_review", request_id)
            await pool.aclose()
            logger.info(f"🤖 ARQ: run_supervisor_review поставлена в очередь для {request_id}")
        except Exception as e:
            logger.error(f"Ошибка постановки задачи Надсмотрщику: {e}")

    # Запускаем в фоне — не блокируем создание запроса
    asyncio.create_task(_enqueue())
    return request_id


@mcp.tool()
async def request_command_execution(
    command: str = Field(..., description="bash-команда для выполнения в песочнице"),
    reason: str = Field(..., description="Зачем нужна эта команда? Контекст для Надсмотрщика"),
    coder_task_id: str = Field(..., description="ID текущей задачи кодера"),
) -> str:
    """
    Запрашивает выполнение bash-команды в песочнице.
    Кодер блокируется до решения Надсмотрщика или человека.
    Если одобрено — выполняет команду и возвращает stdout/stderr.
    """
    request_id = _create_action_request(
        coder_task_id=coder_task_id,
        action_type="RUN_COMMAND",
        payload={"command": command, "reason": reason},
    )

    logger.info(f"⏸ Кодер ждёт решения Gate для команды: {command[:80]}...")
    decision = await _wait_for_decision(request_id)

    if decision["status"] != "approved":
        return json.dumps({
            "success": False,
            "stdout": "",
            "stderr": "",
            "returncode": -1,
            "error": f"Команда отклонена Gate. Причина: {decision.get('feedback', 'нет комментария')}",
        })

    # Выполняем команду ТОЛЬКО внутри песочницы задачи (git-worktree)
    sandbox = registered_sandbox_dir(coder_task_id)
    if not sandbox:
        return json.dumps({
            "success": False,
            "stdout": "",
            "stderr": "",
            "returncode": -1,
            "error": (
                "Песочница для задачи не создана: сначала согласуй изменения через "
                "request_diff_apply (после одобрения backend создаёт git-worktree)."
            ),
        })
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(sandbox),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return json.dumps({
            "success": result.returncode == 0,
            "stdout": result.stdout[-4000:],    # обрезаем для LLM
            "stderr": result.stderr[-2000:],
            "returncode": result.returncode,
            "cwd": str(sandbox),
            "error": None,
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"success": False, "stdout": "", "stderr": "", "returncode": -1, "error": "Таймаут выполнения команды"})
    except Exception as e:
        return json.dumps({"success": False, "stdout": "", "stderr": "", "returncode": -1, "error": str(e)})


@mcp.tool()
async def request_plan_review(
    plan_spec: str = Field(..., description="Markdown-спецификация плана для утверждения"),
    coder_task_id: str = Field(..., description="ID текущей задачи кодера"),
) -> str:
    """
    Запрашивает утверждение сформированного плана/спецификации перед началом кодинга.
    Всегда эскалируется к человеку через шлюз «Звонок другу».
    Кодер блокируется до решения.
    """
    request_id = _create_action_request(
        coder_task_id=coder_task_id,
        action_type="REVIEW_PLAN",
        payload={"plan_spec": plan_spec},
    )

    logger.info(f"📋 Кодер ждёт утверждения плана (request_id={request_id})")
    decision = await _wait_for_decision(request_id)

    return json.dumps({
        "approved": decision["status"] == "approved",
        "feedback": decision.get("feedback", ""),
        "message": "План утверждён, можно приступать к реализации." if decision["status"] == "approved"
                   else f"План отклонён. Комментарий: {decision.get('feedback', 'нет комментария')}",
    })


@mcp.tool()
async def request_diff_apply(
    summary: str = Field(..., description="Краткое описание изменений"),
    files_diff: str = Field(..., description="JSON: {filename: new_content} или unified diff"),
    coder_task_id: str = Field(..., description="ID текущей задачи кодера"),
) -> str:
    """
    Запрашивает разрешение на изменение файлов проекта.
    Всегда эскалируется к человеку через шлюз «Звонок другу».
    Кодер блокируется до решения Во время ожидания.

    После одобрения backend:
      1) (опционально) делает git-stash checkpoint, если auto_checkpoint=true;
      2) создаёт git-worktree задачи (/tmp/coder_sandbox/<coder_task_id>);
      3) переносит предложенные файлы в песочницу;
      4) возвращает sandbox_id — его надо передавать в write_file / run_terminal_command.
    В рабочий проект ничего не пишется: применение патча — отдельный шаг «Apply» человеком.
    """
    request_id = _create_action_request(
        coder_task_id=coder_task_id,
        action_type="APPLY_DIFF",
        payload={"summary": summary, "files_diff": files_diff},
    )

    logger.info(f"📂 Кодер ждёт разрешения на запись файлов (request_id={request_id})")
    decision = await _wait_for_decision(request_id)

    if decision["status"] != "approved":
        return json.dumps({
            "approved": False,
            "feedback": decision.get("feedback", ""),
            "message": "Запись отклонена. Комментарий: " + (decision.get("feedback") or "нет комментария"),
        })

    # Одобрено: разворачиваем песочницу и переносим туда файлы
    checkpoint, cp_err = create_stash_checkpoint(coder_task_id)
    if cp_err:
        logger.warning(f"Checkpoint не создан: {cp_err}")

    written, provision_err = materialize_files(coder_task_id, files_diff)
    sandbox = sandbox_dir(coder_task_id)

    # Baseline песочницы — HEAD: незакоммиченные правки проекта в неё не попадают.
    # Сообщаем об этом отдельным полем, авто-stash не делаем.
    wip_warning = baseline_warning()
    if wip_warning:
        logger.warning(f"WIP-warning для {coder_task_id}: {wip_warning[:120]}")

    if provision_err:
        return json.dumps({
            "approved": True,
            "sandbox_id": coder_task_id if sandbox.exists() else None,
            "files_written": written,
            "warning": wip_warning,
            "error": provision_err,
            "message": ("Изменения одобрены, но песочницу развернуть не удалось. "
                        "Исправь files_diff (JSON {путь: содержимое} или unified diff) и вызови инструмент снова."),
        })

    return json.dumps({
        "approved": True,
        "sandbox_id": coder_task_id,
        "sandbox_dir": str(sandbox),
        "files_written": written,
        "checkpoint": checkpoint,
        "checkpoint_enabled": auto_checkpoint_enabled(),
        "warning": wip_warning,
        "message": (
            "Изменения одобрены и перенесены в песочницу проекта. "
            f"Дальше правь файлы через write_file(..., sandbox_id=\"{coder_task_id}\") "
            f"и запускай проверки через run_terminal_command(..., sandbox_id=\"{coder_task_id}\"). "
            "В рабочий проект изменения попадут только после шага «Apply» у человека."
        ),
    })


@mcp.tool()
async def propose_and_compile_scenario(
    plan_description: str = Field(..., description="Описание плана словами"),
    steps: list = Field(..., description="Массив шагов сценария, каждый шаг - словарь с ключами tool и args_template"),
    coder_task_id: str = Field(..., description="ID текущей задачи кодера"),
    mode: str = Field("apprentice", description="Режим выполнения: 'apprentice' или 'auto'"),
) -> str:
    """
    Генерирует и утверждает Сценарий (ScenarioDefinition).
    В режиме Apprentice ждет подтверждения человека в чате (с возможностью правок).
    В режиме Auto отправляет на проверку Supervisor'у.
    Возвращает scenario_id, если утверждено, или ошибку 'rejected_with_corrections'.
    """
    steps_json = json.dumps(steps, ensure_ascii=False, sort_keys=True)
    scenario_hash = hashlib.sha256(steps_json.encode("utf-8")).hexdigest()

    db = SessionLocal()
    from models import ScenarioDefinition
    try:
        # 1. Быстрый поиск в кэше
        existing = db.query(ScenarioDefinition).filter(
            ScenarioDefinition.scenario_hash == scenario_hash,
            ScenarioDefinition.status == "active"
        ).first()
        if existing:
            return json.dumps({
                "status": "approved",
                "scenario_id": existing.id,
                "message": "Сценарий уже утвержден ранее (взят из кэша по хэшу)."
            })
    finally:
        db.close()

    # 2. Не найден в кэше — запрашиваем утверждение (у человека или Supervisor)
    # Используем REVIEW_PLAN action
    request_id = _create_action_request(
        coder_task_id=coder_task_id,
        action_type="REVIEW_PLAN",
        payload={
            "plan_description": plan_description, 
            "steps": steps, 
            "scenario_hash": scenario_hash,
            "mode": mode
        },
    )

    logger.info(f"📋 Ожидание утверждения сценария {scenario_hash[:8]} (request_id={request_id})")
    decision = await _wait_for_decision(request_id)

    if decision["status"] == "approved":
        db = SessionLocal()
        import uuid
        from datetime import datetime
        try:
            # Создаем ScenarioDefinition
            new_id = str(uuid.uuid4())
            new_scenario = ScenarioDefinition(
                id=new_id,
                name=f"Сценарий от {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
                description=plan_description,
                steps=steps_json,
                scenario_hash=scenario_hash,
                approved_hash=scenario_hash,
                status="active"
            )
            db.add(new_scenario)
            db.commit()
            return json.dumps({
                "status": "approved",
                "scenario_id": new_id,
                "message": "Сценарий успешно утвержден и сохранен."
            })
        finally:
            db.close()
    elif decision["status"] == "rejected_with_corrections":
        return json.dumps({
            "status": "rejected_with_corrections",
            "feedback": decision.get("feedback", ""),
            "message": "План отклонен с правками. Пожалуйста, исправьте шаги согласно фидбеку и вызовите инструмент заново."
        })
    else:
        return json.dumps({
            "status": "rejected",
            "feedback": decision.get("feedback", ""),
            "message": "Сценарий полностью отклонен. Причина: " + decision.get("feedback", "")
        })

if __name__ == "__main__":
    mcp.run()
