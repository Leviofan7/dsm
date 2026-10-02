"""Проба Фазы B: intent-tools + _meta-канал роли. Запуск в контейнере, строки '@@'."""
import os
import sys
import re
import json
import asyncio
import logging

os.environ["INTENTS_DIR"] = "/tmp/intent_probe"
logging.disable(logging.INFO)
sys.path.insert(0, "/app")

ID = "2026-09-25-meta-probe"
ID2 = "2026-09-25-meta-wins"
KEY = "contextus/role"


def show(text, limit=260):
    return re.sub(r"\s+", " ", str(text)).strip()[:limit]


async def main() -> None:
    from services.role_permissions import CALLER_ROLE_META_KEY
    from services import intent as intent_service
    from agent.mcp_manager import MCPManager
    assert CALLER_ROLE_META_KEY == KEY, CALLER_ROLE_META_KEY

    mcp = MCPManager()
    await mcp.start()
    try:
        it = [s for s in mcp.get_servers_status() if s["name"] == "intent-tools"]
        print("@@ REG:", (it[0]["active"], len(it[0]["tools"])) if it else "НЕТ СЕРВЕРА")
        print("@@ TOOLS_TOTAL:", len(mcp.tool_names))
        schema = [t for t in mcp.get_tools_for_llm() if t["function"]["name"] == "intent_update_step"][0]
        print("@@ SCHEMA update_step:", sorted(schema["function"]["parameters"].get("properties", {})))

        # ── 1. Роль не пришла вовсе / подделана в аргументах ──
        forged = {"intent_id": ID, "title": "Проба", "_role": "doorman", "role": "doorman", "created_by": "doorman"}
        print("@@ 1 NO-META:", show(await mcp.call_tool("intent_create", forged)))
        print("@@ 1 FORGED-ONLY:", show(await mcp.call_tool("intent_create", forged, meta={})))
        print("@@ 1 WRONG-KEY:", show(await mcp.call_tool("intent_create", forged, meta={"role": "doorman"})))

        # ── 2. can_create_intent ──
        print("@@ 2 CODER-create:", show(await mcp.call_tool("intent_create", {"intent_id": ID, "title": "Проба"}, meta={KEY: "coder"})))
        print("@@ 2 TESTER-create:", show(await mcp.call_tool("intent_create", {"intent_id": ID, "title": "Проба"}, meta={KEY: "tester"})))
        print("@@ 2 DOORMAN-create:", show(await mcp.call_tool("intent_create", {"intent_id": ID, "title": "Проба"}, meta={KEY: "doorman"})))
        print("@@ 2 CREATED_BY:", show(json.loads(await mcp.call_tool("intent_read", {"intent_id": ID}, meta={KEY: "doorman"})).get("created_by")))

        # ── 3. Роль решает _meta, а не аргументы ──
        sneaky = {"intent_id": ID2, "title": "Подделка", "_role": "doorman", "created_by": "doorman"}
        print("@@ 3 META-WINS:", show(await mcp.call_tool("intent_create", sneaky, meta={KEY: "supervisor_14b"})))
        print("@@ 3 CREATED_BY:", show(json.loads(await mcp.call_tool("intent_read", {"intent_id": ID2}, meta={KEY: "doorman"})).get("created_by")))
        print("@@ 3 DUP:", show(await mcp.call_tool("intent_create", {"intent_id": ID2, "title": "x"}, meta={KEY: "doorman"})))

        # ── 4. Матрица write_own (сид плана — прямой вызов сервиса, у ролей нет write_any) ──
        plan_yaml = "```yaml\nplan:\n  - id: s1\n    role: tester\n    status: pending\n  - id: s2\n    role: coder\n    status: pending\n```"
        intent_service.update_section(ID, intent_service.SECTION_PLAN, plan_yaml)
        seeded = json.loads(await mcp.call_tool("intent_read", {"intent_id": ID}, meta={KEY: "doorman"}))
        print("@@ 4 PLAN-SEEDED:", show(seeded.get("plan")))
        print("@@ 4 READ-as-tester:", show(await mcp.call_tool("intent_read", {"intent_id": ID}, meta={KEY: "tester"})))
        print("@@ 4 VISION-as-tester:", show(await mcp.call_tool("intent_append_vision", {"intent_id": ID, "text": "нужен линтер"}, meta={KEY: "tester"})))
        print("@@ 4 JOURNAL-as-tester:", show(await mcp.call_tool("intent_append_journal", {"intent_id": ID, "text": "проверил сборку"}, meta={KEY: "tester"})))
        print("@@ 4 STEP-own-as-tester:", show(await mcp.call_tool("intent_update_step", {"intent_id": ID, "step_id": "s1", "fields": {"status": "done"}}, meta={KEY: "tester"})))
        print("@@ 4 STEP-foreign-as-tester:", show(await mcp.call_tool("intent_update_step", {"intent_id": ID, "step_id": "s2", "fields": {"status": "done"}}, meta={KEY: "tester"})))
        print("@@ 4 STEP-steal-as-tester:", show(await mcp.call_tool("intent_update_step", {"intent_id": ID, "step_id": "s1", "fields": {"role": "coder"}}, meta={KEY: "tester"})))
        print("@@ 4 SECTION-as-tester:", show(await mcp.call_tool("intent_update_section", {"intent_id": ID, "section_name": "Цель", "new_text": "х"}, meta={KEY: "tester"})))
        print("@@ 4 STATUS-as-tester:", show(await mcp.call_tool("intent_update_status", {"intent_id": ID, "new_status": "done"}, meta={KEY: "tester"})))
        print("@@ 4 OVERRIDES-as-tester:", show(await mcp.call_tool("intent_update_overrides", {"intent_id": ID, "role": "coder", "restrict": "write"} , meta={KEY: "tester"})))
        print("@@ 4 ARCHIVE-as-coder:", show(await mcp.call_tool("intent_archive", {"intent_id": ID}, meta={KEY: "coder"})))
        print("@@ 4 LIST-as-coder:", show(await mcp.call_tool("intent_list", {}, meta={KEY: "coder"})))

        # ── 5. Неизвестная роль: fail-closed дефолты ──
        print("@@ 5 GHOST-read:", show(await mcp.call_tool("intent_read", {"intent_id": ID}, meta={KEY: "ghost"})))
        print("@@ 5 GHOST-step:", show(await mcp.call_tool("intent_update_step", {"intent_id": ID, "step_id": "s1", "fields": {"status": "done"}}, meta={KEY: "ghost"})))

        # ── 6. Аудит ──
        doc = json.loads(await mcp.call_tool("intent_read", {"intent_id": ID}, meta={KEY: "doorman"}))
        denies = [ln for ln in doc.get("journal", []) if "DENY" in str(ln)]
        print("@@ 6 DENY-in-journal:", len(denies))
        for ln in denies[:4]:
            print("    *", show(ln, 150))
        print("@@ 6 VISIONS:", show([v for v in doc.get("visions", []) if "tester" in str(v)]))
    finally:
        await mcp.shutdown()


asyncio.run(main())
