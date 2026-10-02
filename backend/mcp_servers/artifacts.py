"""
artifacts.py — MCP-сервер `artifacts` (фаза 3).

Даёт роли два инструмента: опубликовать результат прогона в панель «Артефакты» и посмотреть,
что уже опубликовано. Логика лежит в `services/artifacts.py` — здесь только тонкий MCP-слой,
поэтому API и агент публикуют артефакты одним и тем же кодом.

Чьи это артефакты: id прогона идёт служебным каналом `_meta` (`contextus/task_id`), который
заполняет оркестратор (`llm_manager._role_meta`). Аргументом его НЕ делаем намеренно:
аргументы генерирует LLM (в apprentice-режиме их может подменить оператор), и тогда агент
мог бы складывать результаты в чужую задачу. Нет id в meta → отказ (fail-closed): артефакт
без владельца нельзя ни показать в панели, ни прибрать вместе с задачей.
"""

import json
import logging
import mimetypes
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import Context, FastMCP

from database import SessionLocal
from services import artifacts as artifacts_service
from services.mcp_meta import caller_role, caller_task_id

logger = logging.getLogger("contextus.artifacts_mcp")

mcp = FastMCP(
    "artifacts",
    instructions="Результаты прогона: публикация и просмотр артефактов задачи",
)


def _err(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _origin(role: str) -> str:
    """Кто создал артефакт: роль видна в карточке, чтобы отличать источники."""
    return f"agent:{role}" if role else "agent"


def _task_or_error(ctx: Context) -> tuple[str, str | None]:
    """(task_id, ответ-отказ). Прогон без id — не «система», а неопределённость."""
    task_id = caller_task_id(ctx)
    if not task_id:
        return "", _err(
            "не передан id прогона (служебный _meta contextus/task_id): "
            "артефакт некуда привязать — публикация отменена"
        )
    return task_id, None


@mcp.tool()
def publish_artifact(
    ctx: Context,
    kind: str,
    title: str,
    content: str | None = None,
    url: str | None = None,
    path: str | None = None,
    description: str | None = None,
) -> str:
    """
    Публикует результат работы в панель «Артефакты» прогона.

    Что передавать:
      * content — текст результата (отчёт, сводка, найденные факты, таблица);
      * url — ссылка на источник/страницу;
      * path — путь к файлу ВНУТРИ проекта (например, из сандбокса) — файл будет скопирован
        в артефакты прогона и станет доступен для скачивания.
    kind: plan | file | image | text | report | link | error_dump (по умолчанию text/file/link).
    """
    task_id, error = _task_or_error(ctx)
    if error:
        return error
    role = caller_role(ctx)

    resolved_kind = (kind or "").strip() or ("link" if url else "file" if path else "text")
    if resolved_kind not in artifacts_service.KINDS:
        return _err(
            f"неизвестный kind={resolved_kind!r}, допустимые: {', '.join(artifacts_service.KINDS)}"
        )
    if not (title or "").strip():
        return _err("title обязателен: карточка без заголовка бесполезна в панели")
    if not any([(content or "").strip(), (url or "").strip(), (path or "").strip()]):
        return _err("нужен один из источников: content (текст), url (ссылка) или path (файл проекта)")

    meta = {"role": role or None, "description": (description or "").strip() or None}
    db = SessionLocal()
    try:
        if path:
            data = artifacts_service.read_agent_file(path)  # проверки пути внутри
            mime = mimetypes.guess_type(str(path))[0]
            row = artifacts_service.save_file(
                db,
                task_id=task_id,
                filename=Path(path).name,
                data=data,
                kind=resolved_kind,
                title=title,
                mime=mime,
                origin=_origin(role),
                meta={**meta, "source_path": str(path)},
            )
        else:
            row = artifacts_service.publish(
                db,
                task_id=task_id,
                kind=resolved_kind,
                title=title,
                content=content,
                url=url,
                origin=_origin(role),
                meta=meta,
            )
    except ValueError as e:
        # Ожидаемые отказы (путь вне проекта, нет задачи, кривой kind) — это ответ модели,
        # а не падение сервера: агент должен увидеть причину и попробовать иначе.
        logger.warning(f"⛔ publish_artifact отклонён: {e}")
        return _err(str(e))
    except Exception as e:
        logger.exception("publish_artifact: неожиданная ошибка")
        return _err(f"не удалось опубликовать артефакт: {e}")
    finally:
        db.close()

    return json.dumps(
        {
            "published": True,
            "artifact_id": row.id,
            "kind": row.kind,
            "title": row.title,
            "storage": row.storage,
            "size_bytes": row.size_bytes,
            "hint": "Результат виден в правой панели → вкладка «Артефакты».",
        },
        ensure_ascii=False,
    )


@mcp.tool()
def list_run_artifacts(ctx: Context) -> str:
    """
    Что уже опубликовано в этом прогоне: id, тип, заголовок, размер.

    Полезно перед публикацией: не дублировать один и тот же результат и видеть,
    что уже собрано.
    """
    task_id, error = _task_or_error(ctx)
    if error:
        return error

    db = SessionLocal()
    try:
        card = artifacts_service.list_for_task(db, task_id)
    finally:
        db.close()

    if card is None:
        return _err(f"прогон {task_id} не найден")

    return json.dumps(
        {
            "task_id": task_id,
            "status": card["task"]["status"],
            "plan_steps": len(card["plan"]["steps"]) if card["plan"] else 0,
            "journal_status": (card["journal"] or {}).get("status"),
            "artifacts": [
                {
                    "id": a["id"],
                    "kind": a["kind"],
                    "title": a["title"],
                    "size_bytes": a["size_bytes"],
                    "origin": a["origin"],
                }
                for a in card["artifacts"]
            ],
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    mcp.run()
