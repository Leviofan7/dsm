"""
Уровни доступа агента (permissions) — единый контракт бэкенда и UI.

Повод. Режим «Ученик» был бинарным: либо одобряй КАЖДЫЙ вызов (включая `list_dir`
и `read_file`, которые ничего не меняют), либо не одобряй ничего. На практике это
давало два плохих исхода: 6 карточек подтверждения на простой осмотр репозитория
(«защита не даёт работать») и полное отсутствие гейта в режиме `auto`.

Здесь три уровня — как в меню разрешений IDE:

  * `ask`       — «Спрашивать»: план перед действием + подтверждение только для
                  действий, которые МЕНЯЮТ состояние (write/privileged). Read-only
                  инструменты идут без вопроса: они не могут ничего испортить.
  * `allow_all` — «Разрешить всё»: инструменты выполняются без вопросов, но план
                  по-прежнему показывается (наблюдаемость без остановок).
  * `autopilot` — «Автопилот»: без плана и без подтверждений; единственные границы —
                  ролевой whitelist инструментов и `write_scope`.

Исторические значения (`apprentice`/`auto`) остаются валидными: первый маппится в
`ask`, второй — в `allow_all`, чтобы старые беседы и сохранённый localStorage не
ломались и не оказывались неожиданно строже или мягче задуманного.
"""

from __future__ import annotations

import os

#: «Спрашивать» — уровень по умолчанию для всего, что меняет состояние.
LEVEL_ASK = "ask"
LEVEL_ALLOW_ALL = "allow_all"
LEVEL_AUTOPILOT = "autopilot"

#: Порядок важен для UI: от самого строгого к самому свободному.
LEVELS: tuple[str, ...] = (LEVEL_ASK, LEVEL_ALLOW_ALL, LEVEL_AUTOPILOT)

#: Человекочитаемое описание уровней. Используется и в UI (через API), и в логах.
LEVEL_META: dict[str, dict[str, str]] = {
    LEVEL_ASK: {
        "label": "Спрашивать",
        "description": "План перед действием; подтверждение только для записи и привилегированных действий",
    },
    LEVEL_ALLOW_ALL: {
        "label": "Разрешить всё",
        "description": "Инструменты выполняются без вопросов; план показывается для контроля",
    },
    LEVEL_AUTOPILOT: {
        "label": "Автопилот",
        "description": "Работает автономно в рамках прав роли: без плана и без подтверждений",
    },
}

#: Старые значения режима → новые уровни (обратная совместимость).
_ALIASES: dict[str, str] = {
    "apprentice": LEVEL_ASK,
    "auto": LEVEL_ALLOW_ALL,
    "": LEVEL_ALLOW_ALL,
    "none": LEVEL_ALLOW_ALL,
}

#: Инструменты, которые только читают. Список намеренно узкий: всё, чего здесь нет,
#: считается изменяющим и требует подтверждения на уровне `ask`. Ошибка в сторону
#: строгости дешевле, чем незамеченная запись в репозиторий пользователя.
READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "list_dir",
    "search_files",
    "outline_file",
    "search_knowledge_base",
    "list_run_artifacts",
    "get_dom_map",
    "get_page_text",
    "get_raw_html",
    "web_search",
    "read_url",
})

#: Сентинел: шаг «план действий», а не вызов инструмента. Живёт в `proposed_tool`,
#: чтобы не делать миграцию БД ради одного поля.
PLAN_TOOL = "__plan__"

#: Сколько секунд ждём решения человека. Дольше — прогон завершается честно
#: (`timeout`), а не держит слот воркера вечно (раньше ожидание было бесконечным).
WAIT_TIMEOUT_SECONDS: float = float(os.getenv("APPRENTICE_WAIT_TIMEOUT", "900"))


def normalize_level(mode: str | None) -> str:
    """Приводит любое историческое значение режима к каноническому уровню."""
    value = (mode or "").strip().lower()
    if value in LEVEL_META:
        return value
    return _ALIASES.get(value, LEVEL_ALLOW_ALL)


def level_label(level: str) -> str:
    return LEVEL_META.get(normalize_level(level), {}).get("label", level)


def level_description(level: str) -> str:
    return LEVEL_META.get(normalize_level(level), {}).get("description", "")


def is_read_only(tool_name: str | None) -> bool:
    """True, если вызов ничего не меняет: такой шаг не должен останавливать прогон."""
    return bool(tool_name) and tool_name in READ_ONLY_TOOLS


def requires_approval(level: str, tool_name: str | None) -> bool:
    """
    Нужно ли подтверждение человека для конкретного вызова.

    `autopilot` не спрашивает никогда; `allow_all` тоже, но план всё равно покажет;
    `ask` спрашивает только про изменяющие действия. `None` в `tool_name` — финальный
    текстовый ответ: на уровне `ask` его тоже показываем перед отправкой.
    """
    resolved = normalize_level(level)
    if resolved in (LEVEL_ALLOW_ALL, LEVEL_AUTOPILOT):
        return False
    if tool_name is None:
        return True
    return not is_read_only(tool_name)


def plan_enabled(level: str) -> bool:
    """Нужен ли план из 2–3 шагов перед действием (автопилот работает без остановок)."""
    return normalize_level(level) != LEVEL_AUTOPILOT


def plan_requires_approval(level: str) -> bool:
    """План — всегда информация; останавливаться на нём просим только в режиме `ask`."""
    return normalize_level(level) == LEVEL_ASK


def levels_payload() -> list[dict[str, str]]:
    """Список уровней для UI: id, label, description — в порядке от строгого к свободному."""
    return [
        {"id": level, "label": LEVEL_META[level]["label"], "description": LEVEL_META[level]["description"]}
        for level in LEVELS
    ]
