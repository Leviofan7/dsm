"""
Самоанализ прогона: что система знает о себе после каждой задачи.

Зачем отдельный модуль: `services/artifacts.py` отвечает за ФАКТЫ (summary, errors, metrics),
а здесь — их ИНТЕРПРЕТАЦИЯ. Она детерминированная (без LLM): её можно проверить тестом и она
не зависит от доступности облака. Поэтому отчёт помещается в журнал всегда — даже если LLM
недоступна.

Два источника, которые НЕ подменяют друг друга:
  * детерминированные блоки (этот модуль) — из данных прогона; воспроизводимы;
  * `llm_review` — текст от meta_analyst. Дорогой и невоспроизводимый, поэтому
    при пересборке отчёта мы его СОХРАНЯЕМ, а не перезаписываем.

Блоки ровно те, что нужны для улучшения системы:
  outcome           — чем закончилось (статус + сводка);
  failure_mode      — класс проблемы по данным, а не «на глаз»;
  used_tools/models — чем реально работали;
  signals           — сырые числа (ошибки, тул-коллы, длительность, токены, верификация);
  improvement_hints — что конкретно стоит поправить (по этим числам).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from models import ExecutionTrace, RunJournal

logger = logging.getLogger("contextus.run_review")

_SUCCESS_STATUSES = frozenset({"success", "completed"})

#: Что делать при каждом классе проблемы. Тексты — императивные и проверяемые,
#: а не «обратите внимание»: hint должен превращаться в действие.
_HINT_BY_MODE: dict[str, str] = {
    "provider_unavailable": (
        "Провайдер модели недоступен — проверить ключи и баланс: "
        "docker compose exec -T fastapi_backend python -B /app/scratch/check_providers.py"
    ),
    "stub_response": (
        "Модель отвечала заглушкой вместо работы — проверить, что для задачи выбрана "
        "модель с tool-calling и что инструменты ей выданы"
    ),
    "timeout": "Прогон не уложился в лимит раундов — уменьшить объём задачи или повысить max_tool_rounds",
    "verification_failed": "Инструмент отчитался об успехе, но эффекта нет — проверить реализацию и аргументы",
    "no_traces": "Прогон умер до первого шага — смотреть логи воркера (импорт/бюджет/краш)",
    "model_declined_tools": "Модель не вызвала ни одного инструмента — проверить промпт роли и выдачу tools",
    "cancelled": "Прогон отменён — если это не ожидалось, проверить, кто вызвал отмену",
    "unknown": "Причина не классифицирована — смотреть errors и execution_traces",
}

#: Порог «долгого» прогона для подсказки (мс). Не претендует на истину — только сигнал.
_LONG_RUN_MS = 120_000
#: Бюджет инструментов по умолчанию (max_tool_rounds в execute/execute_stream).
_TOOL_BUDGET = 15


def _now() -> str:
    return datetime.utcnow().isoformat()


def parse_self_review(raw: str | None) -> dict[str, Any] | None:
    """Разбор `RunJournal.self_review`: ожидаем JSON-объект, иначе None (старых значений нет)."""
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def classify_failure(
    status: str | None,
    metrics: dict[str, Any] | None,
    errors: list | None,
    *,
    verification_failures: int = 0,
) -> str:
    """
    Класс проблемы по данным прогона.

    Порядок проверок — от наиболее конкретного признака к общему: у прогона может быть
    и «заглушка», и ошибки инструментов одновременно, но исправлять надо первопричину.
    """
    normalized = (status or "").lower()
    if normalized in _SUCCESS_STATUSES:
        return "none"
    if normalized == "cancelled":
        return "cancelled"

    metrics = metrics or {}
    trace_statuses = {str(s).lower() for s in (metrics.get("statuses") or [])}
    blob = " ".join(str(e).lower() for e in (errors or []))

    if "stub_response" in trace_statuses or any(
        k in blob for k in ("stub", "как языковая модель", "не имею доступа", "я языковая модель")
    ):
        return "stub_response"
    if "timeout" in trace_statuses or any(k in blob for k in ("timeout", "превышен лимит")):
        return "timeout"
    if any(k in blob for k in ("все модели недоступны", "модель не ответила", "нет доступной модели")):
        return "provider_unavailable"
    if verification_failures:
        return "verification_failed"
    if not metrics.get("traces"):
        # Трейсов нет вовсе — прогон не дошёл до инференса (импорт, бюджет, краш воркера).
        return "no_traces"
    if int(metrics.get("tool_calls") or 0) == 0:
        return "model_declined_tools"
    return "unknown"


def improvement_hints(
    mode: str, metrics: dict[str, Any] | None, status: str | None
) -> list[str]:
    """Подсказки по фактам: класс проблемы + пороги, которые видно в метриках."""
    hints: list[str] = []
    if mode in _HINT_BY_MODE:
        hints.append(_HINT_BY_MODE[mode])

    metrics = metrics or {}
    tool_calls = int(metrics.get("tool_calls") or 0)
    duration = int(metrics.get("duration_ms") or 0)

    if tool_calls >= _TOOL_BUDGET:
        hints.append(
            f"Израсходован бюджет инструментов ({tool_calls}) — задача, вероятно, слишком широкая для одного прогона"
        )
    if duration >= _LONG_RUN_MS:
        hints.append(f"Долгий прогон ({duration // 1000} c) — проверить, нет ли ожидания внешних сервисов")

    if (status or "").lower() in _SUCCESS_STATUSES and tool_calls > 0:
        hints.append("Прогон удачный с вызовами инструментов — можно предложить сценарий (POST /tasks/{task_id}/scenario)")

    return hints or ["Отклонений не найдено"]


def _verification_failures(db: Session, task_id: str) -> list[str]:
    """Трейсы, где инструмент отчитался об успехе, а проверка эффекта не прошла."""
    rows = (
        db.query(ExecutionTrace)
        .filter(ExecutionTrace.task_id == task_id, ExecutionTrace.tool_verified.is_(False))
        .all()
    )
    out: list[str] = []
    for r in rows:
        detail = (r.tool_verification_details or "").strip()
        if detail and detail not in out:
            out.append(detail[:200])
    return out


def build_self_review(
    db: Session,
    task_id: str,
    *,
    status: str,
    summary: str | None = None,
    errors: list | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Структурированный самоанализ прогона по фактам. LLM не вызывается."""
    metrics = metrics or {}
    errors = list(errors or [])
    verification = _verification_failures(db, task_id)

    mode = classify_failure(status, metrics, errors, verification_failures=len(verification))
    signals: dict[str, Any] = {
        "errors": len(errors),
        "tool_calls": int(metrics.get("tool_calls") or 0),
        "traces": int(metrics.get("traces") or 0),
        "duration_ms": int(metrics.get("duration_ms") or 0),
        "tokens_in": int(metrics.get("tokens_in") or 0),
        "tokens_out": int(metrics.get("tokens_out") or 0),
        "trace_statuses": list(metrics.get("statuses") or []),
        "verification_failures": len(verification),
    }
    if verification:
        signals["verification_details"] = verification[:3]

    return {
        "outcome": {"status": status, "summary": summary},
        "failure_mode": mode,
        "used_tools": list(metrics.get("tools") or []),
        "models": list(metrics.get("models") or []),
        "signals": signals,
        "improvement_hints": improvement_hints(mode, metrics, status),
        "generated_at": _now(),
    }


def preserve_llm_review(db: Session, task_id: str, review: dict[str, Any]) -> dict[str, Any]:
    """
    Сохраняет ранее прикреплённую LLM-ревизию при пересборке отчёта.

    LLM-ревизия дорогая и невоспроизводимая: перезапись детерминированным блоком была бы
    потерей работы (и денег). Поэтому `llm_review` живёт в том же JSON, но переживает
    повторный `finalize_journal`.
    """
    row = db.query(RunJournal).filter(RunJournal.task_id == task_id).first()
    if row is not None:
        old = parse_self_review(row.self_review) or {}
        if old.get("llm_review"):
            review["llm_review"] = old["llm_review"]
    return review


def attach_llm_review(db: Session, task_id: str, text: str) -> None:
    """
    Прикрепляет текст LLM-ревизии к журналу прогона.

    Повод: `run_meta_analyst` вызывался через `asyncio.create_task` и результат ВЫБРАСЫВАЛСЯ —
    ревизия стоила денег и исчезала. Теперь она остаётся в `self_review.llm_review`.
    """
    row = db.query(RunJournal).filter(RunJournal.task_id == task_id).first()
    if row is None:
        row = RunJournal(task_id=task_id, status="running")
        db.add(row)
        db.commit()
        db.refresh(row)

    review = parse_self_review(row.self_review) or {}
    review["llm_review"] = {"text": text, "generated_at": _now()}
    row.self_review = json.dumps(review, ensure_ascii=False)
    db.commit()
    logger.info(f"🧠 LLM-ревизия прикреплена к прогону {task_id} ({len(text)} символов)")
