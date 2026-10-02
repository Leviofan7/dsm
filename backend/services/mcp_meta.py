"""
Служебный канал MCP `_meta` — то, что система знает о вызове, но модель знать не должна.

Зачем отдельный модуль: у нас уже есть один такой ключ (`contextus/role`, Фаза B) и
появился второй — `contextus/task_id` (фаза 3 артефактов). Оба читаются ОДИНАКОВО, а
разбор `meta` неочевиден (dict или pydantic-модель с extra="allow"), поэтому логика
лежит в одном месте, а не копией в каждом MCP-сервере.

Почему это `_meta`, а не аргумент инструмента:
  * аргументы генерирует LLM (в apprentice-режиме их может подменить оператор) → id задачи
    и роль были бы управляемы извне. Например, агент мог бы опубликовать артефакт в чужую
    задачу или представиться другой ролью;
  * `_meta` — protocol-level канал: в схему инструмента он не попадает вовсе, поэтому
    модель его структурно не видит и не может ни сгенерировать, ни перезаписать;
  * заполняет его оркестратор (`llm_manager._role_meta`) перед вызовом MCP-инструмента.

Отсутствие значения — всегда «нет», а не «разрешаем»: инструменты обязаны отказывать
(fail-closed), если оркестратор ничего не передал.
"""

from __future__ import annotations

import logging
from typing import Any

from services.role_permissions import CALLER_ROLE_META_KEY

logger = logging.getLogger("contextus.mcp_meta")

#: id прогона (задачи) — владельца артефактов; тот же канал, что и роль
CALLER_TASK_META_KEY = "contextus/task_id"

#: все служебные ключи, которые оркестратор вправе передавать
META_KEYS = (CALLER_ROLE_META_KEY, CALLER_TASK_META_KEY)


def raw_meta(ctx: Any) -> Any:
    """Сырой `meta` запроса или None, если его нет/контекст недоступен."""
    try:
        return ctx.request_context.meta
    except Exception:
        return None


def meta_value(ctx: Any, key: str) -> str:
    """
    Значение служебного ключа из `meta` запроса (пустая строка — не передали).

    MCP SDK отдаёт meta либо обычным dict, либо pydantic-моделью с extra="allow":
    обрабатываем оба варианта, иначе значение молча пропадает на одной из версий.
    """
    meta = raw_meta(ctx)
    if not meta:
        return ""

    value = None
    if isinstance(meta, dict):
        value = meta.get(key)
    else:
        value = getattr(meta, key, None)
        if value is None:
            extra = getattr(meta, "model_extra", None)
            if isinstance(extra, dict):
                value = extra.get(key)
    return str(value or "").strip()


def caller_role(ctx: Any) -> str:
    """Роль вызывающего (пустая строка — оркестратор её не передал)."""
    return meta_value(ctx, CALLER_ROLE_META_KEY)


def caller_task_id(ctx: Any) -> str:
    """Id прогона-владельца (пустая строка — прогона нет: например, Telegram-путь)."""
    return meta_value(ctx, CALLER_TASK_META_KEY)
