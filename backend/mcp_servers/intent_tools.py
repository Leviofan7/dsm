"""
intent_tools.py — MCP-сервер `intent-tools` (Фаза B).

Экспонирует `services/intent.py` ролям: чтение и запись intent.md из чата.

Как передаётся роль вызывающего:
  * Роль идёт служебным каналом MCP — `_meta` запроса (`contextus/role`), а НЕ аргументом
    инструмента. Причина: параметр с ведущим `_` FastMCP запрещает, а главное — служебный
    канал вообще не попадает в схему инструмента, поэтому модель его структурно не видит
    и не может ни сгенерировать, ни перезаписать (в отличие от обычного аргумента).
  * Заполняет его оркестратор (`llm_manager`) перед вызовом: `MCPManager.call_tool(..., meta=...)`.
  * Нет роли в meta → отказ (fail-closed), а не «разрешаем как систему».

Решение о доступе — по единой политике `role_permissions.allowed_intent_tools(role)`
(write_own → чтение + своя запись; структурные операции → write_any; создание →
can_create_intent). Здесь правило только применяется, не дублируется.

Любой deny: ответ в LLM, warning в системный лог и человекочитаемая запись
`DENY role=... tool=... ...` в журнал intent.
"""

import sys
import os
import json
import logging
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import Context, FastMCP

from services import intent as intent_service
from services.role_permissions import (
    CALLER_ROLE_META_KEY,
    INTENT_CREATE_TOOL,
    allowed_intent_tools,
    intent_access_denial,
)

logger = logging.getLogger("contextus.intent_tools")

mcp = FastMCP("intent-tools", instructions="Работа с intent.md: чтение и запись по правам роли")


# ── Служебное ─────────────────────────────────────────────────────

def _caller_role(ctx: Context) -> str:
    """Роль вызывающего из _meta запроса. Пустая строка — оркестратор её не передал."""
    try:
        meta = ctx.request_context.meta
    except Exception:
        return ""
    if not meta:
        return ""

    value = None
    if isinstance(meta, dict):
        value = meta.get(CALLER_ROLE_META_KEY)
    else:  # pydantic-модель Meta с extra="allow"
        value = getattr(meta, CALLER_ROLE_META_KEY, None)
        if value is None:
            extra = getattr(meta, "model_extra", None)
            if isinstance(extra, dict):
                value = extra.get(CALLER_ROLE_META_KEY)
    return str(value or "").strip()


def _deny(role: str, tool: str, reason: str, intent_id: str | None = None) -> str:
    """Единый путь отказа: лог + запись в журнал intent + ответ в LLM."""
    message = reason or f"role={role} tool={tool}"
    logger.warning(f"⛔ DENY {message}")

    if intent_id:
        try:
            intent_service.append_journal(intent_id, "system", f"DENY {message}")
        except Exception as e:  # intent может быть недоступен/повреждён — отказ всё равно состоялся
            logger.error(f"Не удалось записать DENY в журнал {intent_id}: {e}")

    return json.dumps({
        "denied": True,
        "role": role,
        "tool": tool,
        "error": f"Доступ запрещён: {message}",
    }, ensure_ascii=False)


def _guard(tool: str, role: str, intent_id: str | None = None) -> str | None:
    """Проверка прав на инструмент. Возвращает строку-отказ или None, если можно."""
    if not role:
        return _deny(role, tool, f"tool={tool} role не определена (оркестратор не передал {CALLER_ROLE_META_KEY})", intent_id)

    reason = intent_access_denial(role, tool)
    if reason:
        return _deny(role, tool, reason, intent_id)
    return None


def _step_owner(intent_id: str, step_id: str) -> str | None:
    """Роль, которой принадлежит шаг плана (None — шаг не найден)."""
    try:
        plan = intent_service.read(intent_id).get("plan") or []
    except intent_service.IntentError:
        return None
    for step in plan:
        if isinstance(step, dict) and str(step.get("id")) == str(step_id):
            return str(step.get("role") or "")
    return None


def _ok(payload) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


# ── Инструменты (10) ──────────────────────────────────────────────

@mcp.tool()
def intent_create(intent_id: str, title: str, ctx: Context) -> str:
    """Создаёт intent.md по шаблону. Требует can_create_intent=true (сейчас только doorman)."""
    role = _caller_role(ctx)
    denied = _guard(INTENT_CREATE_TOOL, role)
    if denied:
        return denied
    try:
        path = intent_service.create(intent_id, title, role)
        return _ok({"created": True, "intent_id": intent_id, "path": str(path), "created_by": role})
    except intent_service.IntentError as e:
        return _ok({"error": str(e)})


@mcp.tool()
def intent_read(intent_id: str, ctx: Context) -> str:
    """Возвращает структуру intent'а: frontmatter, секции, план, журнал, видения ролей."""
    role = _caller_role(ctx)
    denied = _guard("intent_read", role, intent_id)
    if denied:
        return denied
    try:
        return _ok(intent_service.read(intent_id))
    except intent_service.IntentError as e:
        return _ok({"error": str(e)})


@mcp.tool()
def intent_list(status: str = "", ctx: Context = None) -> str:
    """Список intent'ов (по умолчанию активные; status=archived — архив)."""
    role = _caller_role(ctx)
    denied = _guard("intent_list", role)
    if denied:
        return denied
    try:
        return _ok({"intents": intent_service.list_intents(status or None)})
    except intent_service.IntentError as e:
        return _ok({"error": str(e)})


@mcp.tool()
def intent_append_journal(intent_id: str, text: str, ctx: Context) -> str:
    """Дописывает строку в журнал intent от имени своей роли."""
    role = _caller_role(ctx)
    denied = _guard("intent_append_journal", role, intent_id)
    if denied:
        return denied
    try:
        intent_service.append_journal(intent_id, role, text)
        return _ok({"appended": True, "role": role})
    except intent_service.IntentError as e:
        return _ok({"error": str(e)})


@mcp.tool()
def intent_append_vision(intent_id: str, text: str, ctx: Context) -> str:
    """Дописывает текст в подсекцию своей роли в «Видениях ролей»."""
    role = _caller_role(ctx)
    denied = _guard("intent_append_vision", role, intent_id)
    if denied:
        return denied
    try:
        intent_service.append_vision(intent_id, role, text)
        return _ok({"appended": True, "role": role, "section": "Видения ролей"})
    except intent_service.IntentError as e:
        return _ok({"error": str(e)})


@mcp.tool()
def intent_update_step(intent_id: str, step_id: str, fields: dict, ctx: Context) -> str:
    """
    Меняет поля шага плана (например {"status": "done", "report": "..."}).
    При intent_access=write_own — только шаг, где role совпадает с твоей ролью.
    """
    role = _caller_role(ctx)
    denied = _guard("intent_update_step", role, intent_id)
    if denied:
        return denied

    fields = dict(fields or {})
    if "role" in fields:
        return _deny(
            role,
            "intent_update_step",
            f"role={role} step={step_id} попытка сменить владельца шага (структурная операция: пишет планировщик)",
            intent_id,
        )

    owner = _step_owner(intent_id, step_id)
    if owner is not None and owner != role:
        return _deny(role, "intent_update_step", f"role={role} step={step_id} принадлежит роли {owner}", intent_id)

    try:
        intent_service.update_step(intent_id, step_id, **fields)
        return _ok({"updated": True, "step_id": step_id, "fields": fields})
    except intent_service.IntentError as e:
        return _ok({"error": str(e)})


@mcp.tool()
def intent_update_section(intent_id: str, section_name: str, new_text: str, ctx: Context) -> str:
    """Заменяет содержимое секции (Цель / DoD / Ограничения / структура плана). Только write_any."""
    role = _caller_role(ctx)
    denied = _guard("intent_update_section", role, intent_id)
    if denied:
        return denied
    try:
        intent_service.update_section(intent_id, section_name, new_text)
        return _ok({"updated": True, "section": section_name})
    except intent_service.IntentError as e:
        return _ok({"error": str(e)})


@mcp.tool()
def intent_update_status(intent_id: str, new_status: str, ctx: Context) -> str:
    """Меняет frontmatter.status (active | blocked | done). Только write_any."""
    role = _caller_role(ctx)
    denied = _guard("intent_update_status", role, intent_id)
    if denied:
        return denied
    try:
        intent_service.update_status(intent_id, new_status)
        return _ok({"updated": True, "status": new_status})
    except intent_service.IntentError as e:
        return _ok({"error": str(e)})


@mcp.tool()
def intent_update_overrides(intent_id: str, role: str, restrict: str, ctx: Context) -> str:
    """Сужает права роли-цели в permissions_overrides. Только write_any, только сужение."""
    caller_role = _caller_role(ctx)
    denied = _guard("intent_update_overrides", caller_role, intent_id)
    if denied:
        return denied
    try:
        intent_service.update_overrides(intent_id, role, restrict)
        return _ok({"updated": True, "role": role, "restrict": restrict})
    except intent_service.IntentError as e:
        return _ok({"error": str(e)})


@mcp.tool()
def intent_archive(intent_id: str, ctx: Context) -> str:
    """Переносит intent в archive/ и обновляет INDEX.md. Только write_any."""
    role = _caller_role(ctx)
    denied = _guard("intent_archive", role, intent_id)
    if denied:
        return denied
    try:
        intent_service.archive(intent_id)
        return _ok({"archived": True, "intent_id": intent_id})
    except intent_service.IntentError as e:
        return _ok({"error": str(e)})


if __name__ == "__main__":
    mcp.run()
