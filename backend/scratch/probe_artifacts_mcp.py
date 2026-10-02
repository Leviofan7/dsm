"""
Проба: публикация артефакта агентом через НАСТОЯЩИЙ MCP-транспорт (не прямой вызов функции).

Что проверяем живьём:
  1. сервер `artifacts` поднят и отдаёт свои инструменты;
  2. служебный канал `_meta` доносит id прогона до подпроцесса (иначе публикация откажет);
  3. без id прогона — отказ, а не «артефакт без владельца»;
  4. список артефактов и (если браузер поднят) скриншот, который раньше уезжал только в Telegram.

Проба пишет в рабочую БД и УБИРАЕТ за собой: задача и её артефакты удаляются в конце.
Запуск: docker compose exec -T fastapi_backend python /app/scratch/probe_artifacts_mcp.py
"""

import asyncio
import json
import sys

sys.path.insert(0, "/app")

from agent.mcp_manager import MCPManager
from database import SessionLocal
import models
from services.mcp_meta import CALLER_TASK_META_KEY
from services.role_permissions import CALLER_ROLE_META_KEY


async def main() -> None:
    db = SessionLocal()
    task = models.AgentTask(owner_id="probe", status="running", query="проба канала _meta (артефакты)")
    db.add(task)
    db.commit()
    db.refresh(task)
    task_id = task.id
    db.close()
    print(f"прогон для пробы: {task_id}")

    mcp = MCPManager()
    await mcp.start()
    servers = sorted({entry["server"] for entry in mcp._tool_registry.values()})
    print("серверы в реестре:", servers)
    print("артефакт-тулы:", [n for n in mcp._tool_registry if n in ("publish_artifact", "list_run_artifacts")])

    meta = {CALLER_ROLE_META_KEY: "web_researcher", CALLER_TASK_META_KEY: task_id}

    published = await mcp.call_tool(
        "publish_artifact",
        {"kind": "report", "title": "Проба MCP", "content": "# Проба\nканал _meta работает"},
        meta=meta,
    )
    print("publish_artifact →", published)

    listed = await mcp.call_tool("list_run_artifacts", {}, meta=meta)
    print("list_run_artifacts →", listed)

    without_run = await mcp.call_tool(
        "publish_artifact", {"kind": "text", "title": "Без прогона", "content": "x"}, meta={CALLER_ROLE_META_KEY: "web_researcher"}
    )
    print("без task_id →", without_run)

    try:
        shot = await mcp.call_tool("take_screenshot", {}, meta=meta)
        print("take_screenshot →", str(shot)[:70])
    except Exception as e:  # браузер может быть не поднят — это не провал пробы
        print("take_screenshot не вышел:", e)

    await mcp.shutdown()

    db = SessionLocal()
    rows = db.query(models.Artifact).filter(models.Artifact.task_id == task_id).all()
    print("артефактов в БД:", [(r.kind, r.title, r.storage, r.origin) for r in rows])
    for row in rows:
        db.delete(row)
    db.query(models.AgentTask).filter(models.AgentTask.id == task_id).delete()
    db.commit()
    print("уборка: задача и артефакты пробы удалены")
    db.close()

    print("ИТОГ:", json.dumps({"published": "published" in published, "refused_without_task": "error" in without_run}, ensure_ascii=False))


asyncio.run(main())
