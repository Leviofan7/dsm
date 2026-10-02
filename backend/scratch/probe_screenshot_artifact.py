"""
Проба: скриншот браузера попадает в артефакты прогона (а не только в Telegram).

Поднимает браузер на реальной странице (та самая хостовáя Chrome-сессия), делает скриншот
и проверяет, что в БД появился артефакт kind=image со storage=disk. В конце убирает за собой.

Если браузер не поднимается (демон не запущен) — это видно по выводу: проба не «зелёная вслепую».
Запуск: docker compose exec -T fastapi_backend python /app/scratch/probe_screenshot_artifact.py
"""

import asyncio
import sys

sys.path.insert(0, "/app")

from agent.mcp_manager import MCPManager
from database import SessionLocal
import models
from services.mcp_meta import CALLER_TASK_META_KEY
from services.role_permissions import CALLER_ROLE_META_KEY


async def main() -> None:
    db = SessionLocal()
    task = models.AgentTask(owner_id="probe", status="running", query="проба скриншота в артефактах")
    db.add(task)
    db.commit()
    db.refresh(task)
    task_id = task.id
    db.close()
    print(f"прогон для пробы: {task_id}")

    mcp = MCPManager()
    await mcp.start()
    meta = {CALLER_ROLE_META_KEY: "web_researcher", CALLER_TASK_META_KEY: task_id}

    nav = await mcp.call_tool("goto_url", {"url": "https://example.com"}, meta=meta)
    print("goto_url →", str(nav)[:80].replace("\n", " "))

    shot = await mcp.call_tool("take_screenshot", {}, meta=meta)
    print("take_screenshot → длина ответа:", len(str(shot)))
    await mcp.shutdown()

    db = SessionLocal()
    rows = db.query(models.Artifact).filter(models.Artifact.task_id == task_id).all()
    for row in rows:
        print(
            "артефакт:", row.kind, "|", row.title, "| storage:", row.storage,
            "| байт:", row.size_bytes, "| mime:", row.mime,
        )
    ok = any(r.kind == "image" and r.storage == "disk" for r in rows)
    for row in rows:
        db.delete(row)
    db.query(models.AgentTask).filter(models.AgentTask.id == task_id).delete()
    db.commit()
    db.close()
    print("уборка выполнена. ИТОГ:", {"screenshot_saved_as_artifact": ok})


asyncio.run(main())
