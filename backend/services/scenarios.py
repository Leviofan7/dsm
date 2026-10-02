"""
Сценарии из удачных прогонов (фаза 4).

Что здесь и почему так:
  * `scenario_definitions` в проекте уже есть — новых таблиц не заводим, только связка
    `run_journal.scenario_proposed_id`;
  * шаги берём из ФАКТИЧЕСКИХ вызовов инструментов прогона (`agent_task_events`) — это то,
    что действительно сработало, а не пересказ плана;
  * аргументы вызовов нигде не сохраняются структурно (в трейсах только имена инструментов),
    поэтому предложенный сценарий создаётся ЧЕРНОВИКОМ с пустыми `args_template`:
    активировать его без заполнения аргументов нельзя (проверка в `/scenarios/{id}/activate`).
    Обещать автоматическое воспроизведение прогона, когда в данных нет аргументов, было бы
    враньём — а «сценарий», который вызывает инструменты без параметров, опаснее, чем его нет;
  * предложение НИЧЕГО не активирует: draft → active делает человек существующим путём.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from models import AgentTask, AgentTaskEvent, ExecutionTrace, RunJournal, ScenarioDefinition
from services import artifacts as artifacts_service

logger = logging.getLogger("contextus.run_scenarios")

#: Шаг события с вызовом инструмента: `{"type": "step", "step": "tool_<name>"}`.
#: `tool_result_*` — результат того же вызова, в последовательность не попадает.
_TOOL_STEP_PREFIX = "tool_"
_TOOL_RESULT_PREFIX = "tool_result_"


def _payload_object(raw: str | None) -> dict[str, Any] | None:
    """Разбирает payload события: в БД лежит либо JSON, либо SSE-строка `data: {...}`."""
    text = (raw or "").strip()
    if text.startswith("data:"):
        text = text[5:].strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _tools_from_traces(db: Session, task_id: str) -> list[str]:
    """Запасной источник: имена инструментов из трейсов (когда событий шагов нет)."""
    names: list[str] = []
    for t in db.query(ExecutionTrace).filter(ExecutionTrace.task_id == task_id).all():
        try:
            called = json.loads(t.tools_called_names) if t.tools_called_names else []
        except Exception:
            called = []
        if isinstance(called, list):
            names.extend(str(n) for n in called)
    return list(dict.fromkeys(names))


def observed_tools(db: Session, task_id: str) -> list[str]:
    """
    Последовательность вызовов инструментов прогона (подряд идущие повторы схлопываются).

    Источник — события задачи: там каждый вызов отмечен шагом `tool_<name>`. Если событий
    нет (старый прогон или путь без стрима), падаем на трейсы, но там порядок не гарантирован.
    """
    names: list[str] = []
    events = (
        db.query(AgentTaskEvent)
        .filter(AgentTaskEvent.task_id == task_id)
        .order_by(AgentTaskEvent.sequence_number)
        .all()
    )
    for event in events:
        obj = _payload_object(event.payload)
        if not obj or obj.get("type") != "step":
            continue
        step = str(obj.get("step") or "")
        if not step.startswith(_TOOL_STEP_PREFIX) or step.startswith(_TOOL_RESULT_PREFIX):
            continue
        name = step[len(_TOOL_STEP_PREFIX):].strip()
        if name and (not names or names[-1] != name):
            names.append(name)

    if names:
        return names
    return _tools_from_traces(db, task_id)


def _link_journal(db: Session, task_id: str, journal: RunJournal | None, scenario_id: str) -> RunJournal:
    """Привязывает сценарий к журналу прогона (журнала нет — создаём: связка и есть его смысл)."""
    if journal is None:
        journal = RunJournal(task_id=task_id, status="success")
        db.add(journal)
    journal.scenario_proposed_id = scenario_id
    db.commit()
    db.refresh(journal)
    return journal


def _steps_digest(steps: list[dict[str, Any]]) -> str:
    canonical = json.dumps(steps, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def propose_from_run(
    db: Session,
    task_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
) -> tuple[ScenarioDefinition, bool]:
    """
    Предлагает сценарий по удачному прогону: (сценарий, создан_ли_сейчас).

    Отказ вместо пустышки — в двух случаях: прогон не удачный (повторять нечего) и прогон
    не вызывал инструментов (сценарий из одних ответов модели бессмысленен — его роль
    играет сама модель). Дедуп по `scenario_hash`: тот же набор шагов не плодит копии.
    """
    task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
    if task is None:
        raise ValueError(f"задача {task_id} не найдена")

    journal = db.query(RunJournal).filter(RunJournal.task_id == task_id).first()
    status = (journal.status if journal else None) or task.status
    if status not in ("success", "completed"):
        raise ValueError(
            f"сценарий предлагается только по удачному прогону (статус: {status})"
        )

    tools = observed_tools(db, task_id)
    if not tools:
        raise ValueError("прогон не вызывал ни одного инструмента — воспроизводить нечего")

    steps = [
        {
            "tool": tool,
            "args_template": {},
            "description": f"шаг {i + 1}: {tool}",
        }
        for i, tool in enumerate(tools)
    ]
    digest = _steps_digest(steps)

    existing = (
        db.query(ScenarioDefinition)
        .filter(ScenarioDefinition.scenario_hash == digest)
        .first()
    )
    if existing is not None:
        _link_journal(db, task_id, journal, existing.id)
        logger.info(f"📋 Сценарий уже есть по хэшу {digest[:8]} (id={existing.id})")
        return existing, False

    query = (task.query or "прогон").strip()[:60]
    summary = (journal.summary if journal and journal.summary else "").strip()
    scenario = ScenarioDefinition(
        id=str(uuid.uuid4()),
        name=(name or f"{query} — из прогона {datetime.utcnow():%d.%m %H:%M}")[:255],
        description=description
        or (summary or f"Сценарий, повторяющий последовательность инструментов прогона {task_id}."),
        steps=json.dumps(steps, ensure_ascii=False),
        source_task_id=task_id,
        scenario_hash=digest,
        mode="auto",
        status="draft",
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    _link_journal(db, task_id, journal, scenario.id)
    logger.info(f"📋 Предложен сценарий {scenario.id} из прогона {task_id}: {len(steps)} шаг(ов)")
    return scenario, True


def scenario_card(scenario: ScenarioDefinition, *, created: bool = False) -> dict[str, Any]:
    """Карточка предложения для API/UI. `needs_args` — честная пометка, что нужен человек."""
    try:
        steps = json.loads(scenario.steps or "[]")
    except Exception:
        steps = []
    missing_args = [s.get("tool") for s in steps if not s.get("args_template")]
    return {
        "id": scenario.id,
        "name": scenario.name,
        "description": scenario.description,
        "status": scenario.status,
        "steps": len(steps),
        "tools": [s.get("tool") for s in steps],
        "needs_args": bool(missing_args),
        "missing_args_tools": missing_args,
        "created": created,
        "warning": (
            "Черновик повторяет последовательность инструментов, но аргументы вызовов "
            "в прогоне не сохранялись — заполните их и активируйте сценарий вручную."
        )
        if missing_args
        else None,
    }
