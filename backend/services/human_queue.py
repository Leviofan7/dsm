"""
human_queue.py — единая очередь подтверждений (ActionRequest) и то, что делает approve.

Зачем модуль. До этого было ДВЕ очереди: ActionRequest (Gate 2.0, писали request_*-инструменты
кодера) и PendingPrivilegedAction (предложения meta-analyst + эскалация apprentice). Разные
таблицы = разные экраны, разные кнопки, разные способы узнать о запросе. Здесь собран один
вход в очередь для писателей и одна точка решения.

Главное различие, которое нельзя терять при миграции — КТО продолжает работу после approve:

  * `gate` (request_diff_apply / request_command_execution / request_plan_review): живой
    инструмент кодера ЗАМОРОЖЕН на asyncio.Event внутри процесса агента. Approve = разбудить
    его; исполнять что-либо здесь нельзя, иначе действие выполнится дважды.
  * `meta_analyst` (prompt_update / coder_task): ждущего процесса НЕТ, сессия анализа давно
    завершена. Approve = точка ЗАПУСКА: применяем промпт или гоняем кодера в песочнице.
    Без этого approve молча менял бы статус и ничего не делал — очередь, которая выглядит
    рабочей и не работает.
  * `apprentice` (эскалация шага с привилегированным инструментом): продолжение живёт на
    СЕРВЕРЕ — `LLMManager.execute_apprentice_step` раз в секунду опрашивает `ApprenticeStep`
    в БД и ждёт `human_decision`. Никакой клиентский retry его не разбудит, поэтому approve
    обязан выставить именно это поле (раньше его не выставлял никто: путь был мёртв).
"""

import json
import logging
import os
from datetime import datetime

import database

logger = logging.getLogger("contextus.human_queue")

# httpx логирует URL целиком, а в URL Telegram Bot API лежит САМ ТОКЕН
# (https://api.telegram.org/bot<token>/sendMessage). Этот модуль зовётся и из backend,
# и из MCP-сервера analyst_mcp (stdout/stderr которого читает бэкенд), поэтому приглушение
# делается здесь, а не только в main.py — иначе токен утечёт в общий лог.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _tg_result(resp, context: str) -> bool:
    """
    Проверяет ответ Telegram и делает провал ВИДИМЫМ.

    Без этой проверки любая ошибка (ротированный токен, отозванный бот, блокировка) молча
    проглатывалась — httpx на 4xx исключение не бросает, а уведомление — единственный канал,
    которым человек узнаёт о запросе. Тихая потеря уведомления = зависшее подтверждение.
    """
    try:
        payload = resp.json()
    except Exception:
        payload = {}
    if resp.status_code == 200 and payload.get("ok"):
        return True
    logger.error(
        f"❌ Telegram не принял {context}: HTTP {resp.status_code} — "
        f"{payload.get('description') or resp.text[:120]}"
    )
    return False


def _session():
    """
    Фабрика сессий по позднему связыванию: `database.SessionLocal()`, а не защёлкнутая
    при импорте ссылка. Иначе тесты не могут подменить БД (этот же анти-паттерн мы уже
    ловили в api/action_requests.py — там он приводил к записи в рабочую contextus.db).
    """
    return database.SessionLocal()

#: Типы, где работу продолжает живой процесс (approve только разблокирует Event)
GATE_ACTION_TYPES = frozenset({
    "request_diff_apply",
    "request_command_execution",
    "request_plan_review",
})

#: Типы-предложения: approve должен САМ исполнить действие
PROPOSAL_ACTION_TYPES = frozenset({"prompt_update", "coder_task"})

ORIGIN_GATE = "gate"
ORIGIN_META_ANALYST = "meta_analyst"
ORIGIN_APPRENTICE = "apprentice"


# ── Payload ───────────────────────────────────────────────────────

def read_payload(req) -> dict:
    """payload записи как dict (невалидный/пустой JSON → {})."""
    try:
        data = json.loads(req.payload) if req.payload else {}
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def write_payload(req, payload: dict) -> None:
    req.payload = json.dumps(payload, ensure_ascii=False)


def origin_of(req) -> str:
    return str(read_payload(req).get("origin") or ORIGIN_GATE)


# ── Создание запроса ──────────────────────────────────────────────

def create_human_request(
    *,
    action_type: str,
    payload: dict,
    origin: str,
    session_id: str | None = None,
    summary: str = "",
    notify: bool = True,
) -> str:
    """
    Кладёт запрос в единую очередь сразу в статусе `pending_friend_call` — то есть «нужен человек».

    Отличие от gate-пути: там статус ставит надсмотрщик и запись ждёт своего Event; здесь
    ждущего процесса нет, поэтому запись создаётся уже для человека и человек же её закрывает.
    """
    from models import ActionRequest

    db = _session()
    try:
        row = ActionRequest(
            coder_task_id=None,
            action_type=action_type,
            payload=json.dumps({**payload, "origin": origin, "session_id": session_id}, ensure_ascii=False),
            status="pending_friend_call",
            supervisor_notes=summary,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        request_id = row.id
    finally:
        db.close()

    logger.warning(f"📥 ActionRequest создан: {request_id} | type={action_type} | origin={origin}")
    if notify:
        notify_human_sync(request_id, action_type, summary)
    return request_id


# ── Уведомление человека ──────────────────────────────────────────

def _friend_call_text(action_type: str, summary: str, request_id: str) -> str:
    return (
        "🚨 *Требуется подтверждение*\n\n"
        f"Тип: `{action_type}`\n"
        f"{summary}\n\n"
        f"id: `{request_id}`\n"
        "Что делаем?"
    )


def _approve_reject_markup(request_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Разрешить", "callback_data": f"ar_approve_{request_id}"},
            {"text": "❌ Отклонить", "callback_data": f"ar_reject_{request_id}"},
        ]]
    }


def _diff_ready_markup(request_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "📦 Применить", "callback_data": f"ar_apply_{request_id}"},
            {"text": "❌ Отклонить", "callback_data": f"ar_reject_{request_id}"},
        ]]
    }


def notify_human_sync(request_id: str, action_type: str, summary: str = "") -> None:
    """
    Синхронное уведомление — для sync-MCP-инструментов (meta-analyst): в их потоке нет
    запущенного event loop, поэтому asyncio-вариант недоступен.
    """
    import httpx

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("ALLOWED_TELEGRAM_USER_ID", "")
    if not token or not chat_id:
        logger.info(f"ℹ️ Уведомление по {request_id} пропущено: Telegram не настроен")
        return
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": _friend_call_text(action_type, summary, request_id),
                "parse_mode": "Markdown",
                "reply_markup": _approve_reject_markup(request_id),
            },
            timeout=15,
        )
        _tg_result(resp, f"уведомление {request_id}")
        _remember_message_id(request_id, resp)
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление по {request_id}: {e}")


async def notify_human_async(request_id: str, action_type: str, summary: str = "", *, diff_ready: bool = False) -> None:
    """Асинхронное уведомление (из FastAPI/воркера). diff_ready=True → кнопка «Применить»."""
    import httpx

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("ALLOWED_TELEGRAM_USER_ID", "")
    if not token or not chat_id:
        logger.info(f"ℹ️ Уведомление по {request_id} пропущено: Telegram не настроен")
        return

    text = _friend_call_text(action_type, summary, request_id)
    markup = _diff_ready_markup(request_id) if diff_ready else _approve_reject_markup(request_id)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "reply_markup": markup},
            )
        _tg_result(resp, f"уведомление {request_id}")
        _remember_message_id(request_id, resp)
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление по {request_id}: {e}")


def _remember_message_id(request_id: str, resp) -> None:
    """
    Запоминает id отправленного сообщения — чтобы потом ПРАВИТЬ карточку, а не плодить новые.
    Храним в payload: у ActionRequest нет отдельного поля под telegram-сообщение.
    """
    from models import ActionRequest

    try:
        message_id = (resp.json().get("result") or {}).get("message_id")
    except Exception:
        return
    if not message_id:
        return

    db = _session()
    try:
        row = db.query(ActionRequest).filter(ActionRequest.id == request_id).first()
        if row is None:
            return
        write_payload(row, {**read_payload(row), "tg_message_id": message_id})
        db.commit()
    except Exception as e:
        logger.error(f"Не удалось сохранить message_id для {request_id}: {e}")
    finally:
        db.close()


async def edit_card_status(request_id: str, status_text: str) -> None:
    """
    Правит текст уже отправленной карточки на итоговый статус (кнопки убираются).

    Отдельное сообщение вместо правки (как было) оставляет в чате висящую карточку с кнопками,
    которая выглядит как нерешённый запрос — человек не понимает, обработалось ли действие.
    """
    import httpx
    from models import ActionRequest

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("ALLOWED_TELEGRAM_USER_ID", "")
    if not token or not chat_id:
        return

    db = _session()
    try:
        row = db.query(ActionRequest).filter(ActionRequest.id == request_id).first()
        message_id = read_payload(row).get("tg_message_id") if row else None
        action_type = row.action_type if row else "?"
    finally:
        db.close()

    if not message_id:
        logger.info(f"ℹ️ Карточку {request_id} не правлю: message_id неизвестен")
        return

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"{status_text}\n\nтип: `{action_type}`\nid: `{request_id}`",
                    "parse_mode": "Markdown",
                },
            )
        _tg_result(resp, f"правку карточки {request_id}")
    except Exception as e:
        logger.error(f"Не удалось обновить карточку {request_id}: {e}")


# ── Исполнение после approve ──────────────────────────────────────

def dispatch_approval(req, user_id: int | None) -> dict:
    """
    Решает, что означает approve для конкретной записи, и делает это.

    Возвращает {"dispatched": bool, "status": str, ...} — чтобы вызывающий (API/Telegram)
    понимал, был ли запущен процесс исполнения или только снят флаг ожидания.
    """
    import audit
    from models import ActionRequest

    origin = origin_of(req)
    payload = read_payload(req)

    db = _session()
    try:
        row = db.query(ActionRequest).filter(ActionRequest.id == req.id).first()
        if row is None:
            return {"dispatched": False, "status": "missing"}

        if origin == ORIGIN_APPRENTICE:
            # Продолжение живёт на сервере: execute_apprentice_step опрашивает human_decision в БД
            from models import ApprenticeStep

            step_id = payload.get("step_id")
            step = db.query(ApprenticeStep).filter(ApprenticeStep.id == step_id).first()
            if step is None:
                return {"dispatched": False, "status": "step_missing", "step_id": step_id}
            step.human_decision = "accepted"
            step.decided_at = datetime.utcnow()
            db.commit()
            audit.log_action(db, user_id, "web_ui", "approve_action", row.id,
                             f"Разрешена эскалация apprentice-шага {step_id}: {payload.get('tool')}", "success")
            logger.info(f"▶️ Apprentice-шаг {step_id} разрешён, агент продолжит по human_decision")
            return {"dispatched": True, "status": "step_accepted", "step_id": step_id}

        if row.action_type == "prompt_update":
            ok, err = apply_prompt_update(row)
            return {"dispatched": True, "status": "applied" if ok else "apply_failed", "error": err}

        if row.action_type == "coder_task":
            # Тяжёлая работа — в фон: сначала помечаем «в работе», запуск делает вызывающий
            row.status = "coder_running"
            row.updated_at = datetime.utcnow()
            db.commit()
            audit.log_action(db, user_id, "web_ui", "approve_action", row.id,
                             f"Запуск кодера в песочнице: {payload.get('target')}", "success")
            return {"dispatched": True, "status": "coder_running"}

        # gate-типы: живой процесс ждёт Event — здесь только фиксируем факт
        audit.log_action(db, user_id, "web_ui", "approve_action", row.id, "Разблокировка ожидающего инструмента", "success")
        return {"dispatched": False, "status": "unblocked"}
    finally:
        db.close()


def reject_side_effects(req, user_id: int | None, reason: str = "") -> None:
    """Отказ тоже должен доехать до того, кто ждёт: у apprentice это снова human_decision."""
    if origin_of(req) != ORIGIN_APPRENTICE:
        return

    from models import ApprenticeStep

    payload = read_payload(req)
    db = _session()
    try:
        step = db.query(ApprenticeStep).filter(ApprenticeStep.id == payload.get("step_id")).first()
        if step is None:
            return
        step.human_decision = "rejected"
        step.corrected_args = json.dumps({"reason": reason}) if reason else None
        step.decided_at = datetime.utcnow()
        db.commit()
        logger.info(f"⛔ Apprentice-шаг {step.id} отклонён человеком")
    finally:
        db.close()


def apply_prompt_update(req) -> tuple[bool, str]:
    """
    Применяет предложенный промпт к роли каноническим writer'ом (тем же, что UI и миграции).

    Diff не храним отдельно (у ActionRequest нет такой колонки, а предложение и так видно
    в payload) — для истории остаётся запись в audit.
    """
    from services import role_permissions as rp

    payload = read_payload(req)
    target = str(payload.get("target") or "")
    new_prompt = str(payload.get("instruction") or "")
    if not target or not new_prompt:
        return False, "в payload нет target/instruction"

    data = rp.load_role_yaml(target)
    if not data:
        return False, f"роль '{target}' не найдена"

    data["system_instruction"] = new_prompt
    rp.write_role_yaml(target, data)
    logger.warning(f"📝 Промпт роли '{target}' обновлён по подтверждённому предложению")
    return True, ""


async def run_coder_task(req_id: str) -> None:
    """
    Кодер в git-worktree (порт legacy `_run_coder_in_sandbox` на ActionRequest).

    В рабочий проект ничего не пишется: worktree → run_claude_coder → unified diff в payload,
    статус diff_ready. Применение патча — отдельный шаг человека (см. apply_patch).
    """
    from services import sandbox as sandbox_service
    from models import ActionRequest

    db = _session()
    try:
        row = db.query(ActionRequest).filter(ActionRequest.id == req_id).first()
        if row is None:
            return
        payload = read_payload(row)
        sandbox_key = req_id
        try:
            sandbox_path, err = sandbox_service.create_sandbox(sandbox_key)
            if not sandbox_path:
                row.status = "apply_failed"
                write_payload(row, {**payload, "error": f"Sandbox error: {err}"})
                db.commit()
                return

            try:
                # Ленивый импорт: main импортирует этот модуль, поэтому на уровне модуля нельзя
                from main import llm_manager as _lm

                await _lm.mcp.call_privileged_tool("run_claude_coder", {
                    "target_dir": str(sandbox_path),
                    "instruction": str(payload.get("instruction") or ""),
                })
            except Exception as e:
                logger.error(f"run_claude_coder error: {e}")
                write_payload(row, {**payload, "coder_error": str(e)})
                payload = read_payload(row)

            diff_text, diff_err = sandbox_service.sandbox_diff(sandbox_key)
            if diff_err:
                diff_text = f"Error generating diff: {diff_err}"

            write_payload(row, {**payload, "diff_content": diff_text})
            row.status = "diff_ready"
            row.updated_at = datetime.utcnow()
            db.commit()
            logger.warning(f"📦 Дифф по запросу {req_id} готов ({len(diff_text)} символов), ждём Apply")
            await notify_human_async(req_id, "coder_task", f"Дифф готов ({len(diff_text)} символов)", diff_ready=True)
        except Exception as e:
            logger.error(f"Error in sandbox for {req_id}: {e}")
            row.status = "apply_failed"
            write_payload(row, {**payload, "error": str(e)})
            db.commit()
        finally:
            sandbox_service.remove_sandbox(sandbox_key)
    finally:
        db.close()


def apply_patch(req) -> tuple[bool, str]:
    """Применяет готовый дифф к рабочему проекту (git apply --check + apply, без коммита)."""
    from services import sandbox as sandbox_service

    payload = read_payload(req)
    diff_text = str(payload.get("diff_content") or "")
    if not diff_text:
        return False, "дифф пуст"
    return sandbox_service.apply_patch_to_project(diff_text)
