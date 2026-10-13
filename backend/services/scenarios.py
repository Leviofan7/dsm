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


def steps_digest(steps: list[dict[str, Any]]) -> str:
    """Канонический хэш набора шагов (sort_keys) — инвариант дедупа предложений."""
    canonical = json.dumps(steps, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def content_hash(steps_text: str | None) -> str:
    """
    sha256 по СЫРОМУ тексту `scenario.steps` — ровно то, что сверяет `LinearScenarioExecutor`
    (`hashlib.sha256(scenario.steps.encode())`).

    ВАЖНО, здесь легко ошибиться: у сценария ДВА разных хэша и они не взаимозаменяемы.
    `steps_digest` канонический (sort_keys) — инвариант дедупа предложений; `content_hash`
    raw-строковый — инвариант целостности при исполнении. Если записать в `approved_hash`
    канонический хэш, исполнитель посчитает утверждённые шаги подменёнными и заблокирует
    запуск. Держим оба в одном месте, чтобы связь была видна.
    """
    return hashlib.sha256((steps_text or "").encode("utf-8")).hexdigest()


def _schema_properties(schema: dict[str, Any] | None) -> dict[str, Any]:
    props = (schema or {}).get("properties")
    return props if isinstance(props, dict) else {}


def _schema_required(schema: dict[str, Any] | None) -> list[str]:
    req = (schema or {}).get("required")
    return [str(r) for r in req] if isinstance(req, list) else []


def validate_scenario_steps(
    steps: Any,
    schemas: dict[str, dict[str, Any]] | None = None,
    *,
    require_args: bool = True,
) -> dict[str, Any]:
    """
    Разбор шагов сценария ПЕРЕД сохранением/активацией. Возвращает отчёт, а не бросает:
    вызывающий сам решает, 422 это или 403, и показывает человеку точный список проблем.

    Почему отчёт, а не проверка в момент исполнения: сценарий с незаполненными аргументами
    выглядит рабочим и запускается «молча» — инструменты получают пустые параметры и делают
    что попало. Отказ обязан случиться до записи статуса `active`.

    `schemas` — входные схемы инструментов из реестра MCP. Если `None` (MCP ещё не поднят),
    проверяем только структуру и непустые аргументы, честно помечая в отчёте
    `schema_checked=False`; делать вид, что сверили имена и обязательные поля, — враньё.
    """
    from agent.mcp_manager import PRIVILEGED_TOOLS  # локально: сервис не тянет MCP при импорте

    report: dict[str, Any] = {
        "valid": True,
        "schema_checked": schemas is not None,
        "errors": [],
        "empty_args": [],
        "blank_values": {},
        "missing_required": {},
        "unknown_args": {},
        "privileged_tools": [],
    }

    if not isinstance(steps, list) or not steps:
        report["valid"] = False
        report["errors"].append("steps должен быть непустым списком шагов")
        return report

    for idx, step in enumerate(steps):
        pos = f"шаг {idx + 1}"
        if not isinstance(step, dict):
            report["errors"].append(f"{pos}: ожидался объект с полем tool")
            continue

        tool = str(step.get("tool") or "").strip()
        if not tool:
            report["errors"].append(f"{pos}: не указан tool")
            continue
        if tool in PRIVILEGED_TOOLS:
            report["privileged_tools"].append(tool)
            continue

        args = step.get("args_template")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            report["errors"].append(f"{pos} ({tool}): args_template должен быть объектом")
            args = {}

        if not args:
            report["empty_args"].append(tool)

        for key, value in args.items():
            # Пустая строка — не «аргумент», а его отсутствие: инструмент получит ""
            # и сделает бессмысленный вызов, который никто не заметит.
            if isinstance(value, str) and not value.strip():
                report["blank_values"].setdefault(tool, []).append(key)

        if schemas is not None:
            if tool not in schemas:
                report["errors"].append(f"{pos} ({tool}): инструмент не зарегистрирован")
                continue
            schema = schemas.get(tool)
            for req in _schema_required(schema):
                if req not in args:
                    report["missing_required"].setdefault(tool, []).append(req)
            props = _schema_properties(schema)
            # Строгость к лишним ключам — только когда схема реально описывает свойства
            # и не разрешает дополнительные явно: иначе опечатка в имени аргумента
            # («quier» вместо «query») молча уедет в инструмент.
            if props and (schema or {}).get("additionalProperties") is not True:
                unknown = [k for k in args if k not in props]
                if unknown:
                    report["unknown_args"].setdefault(tool, unknown)

    if (
        report["errors"]
        or report["empty_args"]
        or report["blank_values"]
        or report["missing_required"]
        or report["unknown_args"]
        or report["privileged_tools"]
    ):
        report["valid"] = False
    return report


def validation_message(report: dict[str, Any]) -> str:
    """Человекочитаемая причина отказа: что именно человеку надо заполнить."""
    parts: list[str] = []
    if report.get("privileged_tools"):
        parts.append("привилегированные инструменты: " + ", ".join(report["privileged_tools"]))
    if report.get("empty_args"):
        parts.append("не заполнены аргументы: " + ", ".join(report["empty_args"]))
    for tool, fields in (report.get("missing_required") or {}).items():
        parts.append(f"{tool}: отсутствуют обязательные поля {', '.join(fields)}")
    for tool, fields in (report.get("blank_values") or {}).items():
        parts.append(f"{tool}: пустые значения {', '.join(fields)}")
    for tool, fields in (report.get("unknown_args") or {}).items():
        parts.append(f"{tool}: неизвестные аргументы {', '.join(fields)}")
    parts.extend(report.get("errors") or [])
    return "; ".join(parts) or "шаги не прошли валидацию"


def _default_reason(task_id: str, status: str, tools: list[str], summary: str | None) -> str:
    """Почему предложен сценарий — по фактам прогона, а не «система так решила»."""
    chain = " → ".join(tools[:8]) + (" → …" if len(tools) > 8 else "")
    reason = (
        f"Прогон {task_id} завершился успешно (статус {status}); "
        f"вызвано инструментов: {len(tools)} ({chain})"
    )
    if summary:
        reason += f". Итог прогона: {summary[:200]}"
    return reason


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
    digest = steps_digest(steps)

    query = (task.query or "прогон").strip()[:60]
    summary = (journal.summary if journal and journal.summary else "").strip()
    reason = _default_reason(task_id, status, tools, summary)

    existing = (
        db.query(ScenarioDefinition)
        .filter(ScenarioDefinition.scenario_hash == digest)
        .first()
    )
    if existing is not None:
        # Сценарий уже есть, но предложение из НОВОГО прогона — это ещё один факт
        # происхождения: если причина не записана, фиксируем её (не перезаписываем).
        if not existing.proposal_reason:
            existing.proposal_reason = reason
            db.commit()
        _link_journal(db, task_id, journal, existing.id)
        logger.info(f"📋 Сценарий уже есть по хэшу {digest[:8]} (id={existing.id})")
        return existing, False

    scenario = ScenarioDefinition(
        id=str(uuid.uuid4()),
        name=(name or f"{query} — из прогона {datetime.utcnow():%d.%m %H:%M}")[:255],
        description=description
        or (summary or f"Сценарий, повторяющий последовательность инструментов прогона {task_id}."),
        proposal_reason=reason,
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


def scenario_card(
    scenario: ScenarioDefinition,
    *,
    created: bool = False,
    tool_schemas: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Карточка предложения для API/UI. `needs_args` — честная пометка, что нужен человек.

    `tool_schemas` (из живого реестра MCP) добавляет к каждому шагу `required`/`properties`:
    редактор аргументов должен показывать, ЧТО заполнять, а не заставлять человека угадывать
    и ловить 422. Схем нет — списки пустые, и это видно (`schema_checked`), а не выглядит
    как «обязательных полей нет».
    """
    try:
        steps = json.loads(scenario.steps or "[]")
    except Exception:
        steps = []
    missing_args = [s.get("tool") for s in steps if not s.get("args_template")]
    return {
        "id": scenario.id,
        "name": scenario.name,
        "description": scenario.description,
        "proposal_reason": scenario.proposal_reason,
        "source_task_id": scenario.source_task_id,
        "status": scenario.status,
        "steps": len(steps),
        "tools": [s.get("tool") for s in steps],
        "schema_checked": tool_schemas is not None,
        # Детали шагов — для редактора аргументов: без них человек не видит, что заполнять.
        "steps_detail": [
            {
                "tool": s.get("tool"),
                "description": s.get("description"),
                "args_template": s.get("args_template") or {},
                "required": _schema_required((tool_schemas or {}).get(s.get("tool"))),
                "properties": sorted(_schema_properties((tool_schemas or {}).get(s.get("tool"))).keys()),
            }
            for s in steps
        ],
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
