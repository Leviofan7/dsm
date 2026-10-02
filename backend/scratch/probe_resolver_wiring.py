"""4.3 — живая проверка: резолвер режима подключён к реальному планированию ролей.

Что реально: реестр моделей (config/models.yaml + Ollama), agent_configs.json, MCP-серверы,
роли, БД (временная, /tmp), полный путь execute_stream: планировщик → AgentSubtask → executor.
Что заглушено: только инференс (`_chat`) — чтобы проверка была бесплатной и детерминированной.
"""
import os
import re
import sys
import json
import asyncio
import logging

os.chdir("/tmp")  # БД создаётся как ./contextus.db — пишем во временный каталог, не в проект
sys.path.insert(0, "/app")
logging.disable(logging.INFO)

PLAN = json.dumps([
    {"topic": "Разведать рынок", "prompt_instruction": "Найди цены на сайтах", "target_role": "web_researcher"},
    {"topic": "Правка кода", "prompt_instruction": "Измени код в нескольких файлах", "target_role": "coder"},
])


def show(text, limit=300):
    return re.sub(r"\s+", " ", str(text)).strip()[:limit]


def provider_response(model, content: str) -> dict:
    """Ответ в формате того провайдера, которому принадлежит модель (иначе код его не распарсит)."""
    if model.provider_type == "ollama":
        return {"message": {"role": "assistant", "content": content}}
    if model.provider_type == "anthropic":
        return {"content": [{"type": "text", "text": content}]}
    return {
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


async def main() -> None:
    from database import Base, engine, SessionLocal
    import models as models_mod

    Base.metadata.create_all(engine)

    from agent.llm_manager import LLMManager
    from agent.mcp_manager import allowed_privileged_tools

    manager = LLMManager()
    await manager.initialize()
    try:
        # ── Заглушки: только инференс, всё остальное настоящее ──
        calls = {"plan": 0, "steps": []}

        async def fake_chat(model, messages, tools=None, **kwargs):
            if tools is None:
                calls["plan"] += 1
                calls.setdefault("plan_calls", []).append({
                    "model": model.name,
                    "last_user": show(messages[-1].get("content"), 70),
                    "messages": len(messages),
                })
                return provider_response(model, PLAN)
            sys_text = next((m["content"] for m in messages if m.get("role") == "system"), "")
            role_hint = re.search(r"Твоя задача \(роль ([^)]+)\)", messages[-1]["content"])
            calls["steps"].append({
                "role": role_hint.group(1) if role_hint else "?",
                "model": model.name,
                "tools": len(tools or []),
                "gate": sorted(allowed_privileged_tools(role_hint.group(1)) if role_hint else []),
                "servers_hint": sorted({
                    s for s in ("web-stealth", "fs-tools", "coder-gate-tools", "deepseek-harness", "gemini-cli-mcp")
                    if s in sys_text or s.replace("-", "_") in sys_text
                }),
            })
            return provider_response(model, "готово")

        manager._chat = fake_chat
        manager.run_meta_analyst = lambda session_id: asyncio.sleep(0)

        # Запись решения резолвера по каждому шагу (обёртка вокруг настоящего метода)
        decisions = []
        original = manager._resolve_role_target

        def wrapped(role, query="", **kwargs):
            result = original(role, query, **kwargs)
            model, conf, servers, mode, reason = result
            decisions.append({
                "role": role,
                "query": show(query, 60),
                "mode": mode,
                "reason": reason,
                "model": getattr(model, "name", None),
                "servers": servers,
                "base_servers": kwargs.get("base_servers"),
            })
            return result

        manager._resolve_role_target = wrapped

        # Реальная задача в реальной (временной) БД — иначе planner-ветка не включается
        db = SessionLocal()
        task = models_mod.AgentTask(id="probe-resolver-wiring", query="проверка резолвера", status="pending")
        db.add(task)
        db.commit()
        db.close()

        events = []
        async for event in manager.execute_stream(
            query="Разведай рынок и внеси правку в код",
            task_id="probe-resolver-wiring",
            target_agent="general",
            complexity="auto",
        ):
            events.append(event)

        subtasks = [e for e in events if '"step": "subtask_' in e]
        print("@@ PLAN_CALLS:", calls["plan"])
        for pc in calls.get("plan_calls", []):
            print("    вызов без tools:", show(json.dumps(pc, ensure_ascii=False), 220), flush=True)
        print("@@ SUBTASK_EVENTS:")
        for e in subtasks:
            try:
                payload = json.loads(e.replace("data: ", "").strip())
                print("   ", payload["step"], "|", payload["message"], flush=True)
            except Exception:
                print("   ", show(e, 220), flush=True)

        print("@@ RESOLVER_DECISIONS:")
        for d in decisions:
            print("   ", show(json.dumps(d, ensure_ascii=False, default=str), 400))

        print("@@ SUBTASK_EXECUTION:")
        for s in calls["steps"]:
            print("   ", show(json.dumps(s, ensure_ascii=False), 300))

        # Что было бы по старому коду: registry.resolve_model(role) + серверы родителя
        print("@@ OLD_VS_NEW:")
        parent_servers = ["workspace", "fs-tools", "coder", "contextus-rag", "analyst-mcp"]
        for role in ("web_researcher", "coder"):
            old_model = manager.registry.resolve_model(role)
            new = next((d for d in decisions if d["role"] == role), None)
            print(
                f"    [{role}] было: модель={getattr(old_model, 'name', None)}, серверы={parent_servers} "
                f"| стало: модель={new['model'] if new else None}, режим={new['mode'] if new else None}, "
                f"серверы={new['servers'] if new else None}"
            )

        print("@@ DB_SUBTASKS:")
        db = SessionLocal()
        rows = db.query(models_mod.AgentSubtask).filter(
            models_mod.AgentSubtask.task_id == "probe-resolver-wiring"
        ).all()
        for r in rows:
            print("   ", r.execution_order, r.target_role, "|", show(r.topic, 40))
        db.close()

        # ── Сценарий 2: швейцар делегирует БЕЗ поля complexity (должно стать auto) ──
        import agent.roles_impl.base_agent as base_mod

        class FakeDoorman:
            """Стаб на границе BaseAgent: отдаёт делегирование без complexity."""

            def __init__(self, llm, cfg, model, tools):
                self.model = model

            async def run_stream(self, query, history):
                yield 'data: ' + json.dumps({
                    "type": "delegation", "agent": "coder",
                    "prompt": "поправь код в нескольких файлах",
                })

        base_mod.BaseAgent = FakeDoorman
        decisions.clear()

        db = SessionLocal()
        db.add(models_mod.AgentTask(id="probe-doorman-auto", query="q", status="pending"))
        db.commit()
        db.close()

        scenario2 = []
        async for event in manager.execute_stream(
            query="поправь код", task_id="probe-doorman-auto", target_agent="auto", complexity="auto"
        ):
            scenario2.append(event)

        print("@@ DOORMAN_DELEGATION:")
        for e in scenario2:
            try:
                payload = json.loads(e.replace("data: ", "").strip())
            except Exception:
                continue
            if payload.get("step") in ("doorman_delegated", "model_selected"):
                print("   ", payload["step"], "|", payload.get("message"), flush=True)

        print("@@ DECISION_AFTER_DELEGATION:")
        for d in decisions:
            print("   ", show(json.dumps({k: d[k] for k in ("role", "mode", "reason", "model")}, ensure_ascii=False), 200), flush=True)
    finally:
        await manager.shutdown()


asyncio.run(main())
