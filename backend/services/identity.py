"""
Identity и карта системы — единый контекст для ВСЕХ ролей.

Повод (запрос 15.10 «скачай видео… ищи выход, выполняй задачу»): швейцар ответил
«я не могу физически скачать файл… у меня нет доступа к твоей файловой системе».
Причина не в модели, а в промпте: `roles/doorman.yaml` описывал только правила
классификации из двух категорий. В нём не было ни того, ГДЕ работает агент, ни того,
ЧТО умеет система, ни того, КАКИЕ агенты существуют. Модель честно ответила на вопрос,
которого ей не задали: «что ты умеешь?» — и ответила «ничего».

Это пункт 1 ТЗ «Папка как живое окружение»: один и тот же identity-контекст добавляется
централизованным resolver-ом во все роли (Doorman / роли подзадач / Telegram-путь).

ЧЕСТНОСТЬ — главное правило этого модуля: описание возможностей совпадает с тем, что
реально есть в системе, и прямо называет границы. Иначе identity-блок сам начнёт врать
(«скачаем и положим на телефон»), а это хуже исходного отказа.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("contextus.identity")

ROLES_DIR = Path(__file__).resolve().parent.parent / "roles"

#: Роли, которым НЕЛЬЗЯ передавать пользовательскую задачу: служебные и сам швейцар.
#: meta_analyst/supervisor_14b работают по внутренним событиям, а doorman не может
#: делегировать сам себе — иначе цикл.
NON_DELEGATABLE = frozenset({"doorman", "supervisor_14b", "meta_analyst"})

#: Что система реально умеет. Сверено с реестром MCP-серверов (logs: 52 инструмента).
#: Здесь — назначение, а не имена тулов: имена ролей видят и без нас.
CAPABILITIES: list[tuple[str, str]] = [
    ("Браузер", "реальный Chromium: открыть сайт, кликнуть, прочитать текст/HTML, снять скриншот"),
    ("Поиск в интернете", "web_search + разбор найденных страниц"),
    ("Файлы и терминал", "чтение; запись и команды — только в песочнице задачи и только ролям с правом записи"),
    ("База знаний (RAG)", "поиск по подключённым источникам проекта с цитатами"),
    ("Артефакты", "результаты прогона сохраняются в панель «Артефакты» и скачиваются оттуда"),
    ("Планировщик", "cron-задачи (scheduler-mcp)"),
]


def _load_roles() -> list[dict[str, Any]]:
    """Читает roles/*.yaml. Битый файл пропускается — identity не должен ронять прогон."""
    if not ROLES_DIR.exists():
        return []
    roles: list[dict[str, Any]] = []
    for path in sorted(ROLES_DIR.glob("*.yaml")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception as e:  # noqa: BLE001 — диагностика важнее строгости
            logger.warning(f"identity: не удалось прочитать {path.name}: {e}")
            continue
        if not isinstance(cfg, dict):
            continue
        role_id = str(cfg.get("id") or path.stem)
        roles.append({
            "id": role_id,
            "name": str(cfg.get("name") or role_id),
            "description": " ".join(str(cfg.get("description") or "").split()),
            "tools": cfg.get("tools") or [],
        })
    return roles


def agent_roster() -> list[dict[str, Any]]:
    """Агенты, которым можно передать задачу (без служебных и без самого швейцара)."""
    return [r for r in _load_roles() if r["id"] not in NON_DELEGATABLE]


def render_agents(roles: list[dict[str, Any]] | None = None) -> str:
    roles = agent_roster() if roles is None else roles
    if not roles:
        return ""
    return "\n".join(
        f"- `{r['id']}` — {r['name']}: {r['description']}" if r["description"]
        else f"- `{r['id']}` — {r['name']}"
        for r in roles
    )


def render_capabilities() -> str:
    return "\n".join(f"- **{name}** — {desc}" for name, desc in CAPABILITIES)


def system_context() -> str:
    """Кто мы и что умеем. Без списка агентов (он нужен только делегирующим ролям)."""
    return (
        "=== ЧТО ЭТО ЗА СИСТЕМА ===\n"
        "Ты работаешь внутри Contextus — автономной системы, которая выполняет задачи, а не "
        "только обсуждает их. У системы есть реальные инструменты:\n"
        f"{render_capabilities()}\n"
        "\nГРАНИЦЫ (не обещай больше, чем есть):\n"
        "- система работает на СЕРВЕРЕ, а не на устройстве пользователя: «положить файл "
        "прямо на телефон/компьютер» напрямую нельзя;\n"
        "- всё, что получено или создано, сохраняется как артефакт прогона — пользователь "
        "скачивает его из панели «Артефакты»;\n"
        "- запись в файлы и команды терминала разрешены только в песочнице задачи.\n"
        "==========================="
    )


def identity_block(*, include_agents: bool = False) -> str:
    """
    Полный identity-блок для вставки в system prompt.

    `include_agents=True` — для ролей, которые делегируют (швейцар): без карты агентов
    модель не знает, кому передавать задачу, и отвечает сама.
    """
    parts = [system_context()]
    if include_agents:
        roster = render_agents()
        if roster:
            parts.append(
                "=== КОМУ МОЖНО ПЕРЕДАТЬ ЗАДАЧУ ===\n"
                f"{roster}\n"
                "================================="
            )
    return "\n\n".join(parts)


#: Короткая форма для тестов/логов: какие id реально доступны для делегирования.
def delegatable_ids() -> list[str]:
    return [r["id"] for r in agent_roster()]
