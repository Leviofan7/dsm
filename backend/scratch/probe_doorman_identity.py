"""
Проба: швейцар по ТОМУ ЖЕ запросу больше не отказывает, а делегирует.

Воспроизводим реальный кейс 15.10 (задача 3d5b6d70): запрос «скачай видео» → отказ,
затем «я не могу найти, давай ты это сделаешь ищи выход, выполняй задачу» → снова отказ,
`delegate_task` не вызван, журнал записал success.

Проверяем на РЕАЛЬНОЙ модели и реальном конфиге:
  1. промпт содержит identity и запрет отказов (без LLM — строковая проверка);
  2. модель НЕ отвечает заглушкой (иначе `final_status=stub_response`);
  3. печатаем, что именно она сделала: делегировала/уточнила.

Стоимость: 1–2 вызова deepseek-chat. Инструменты не исполняются — проба останавливается
на решении швейцара (delegation), а не запускает браузер.

Запуск: docker compose exec -T fastapi_backend python -B /app/scratch/probe_doorman_identity.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

QUERY = "я не могу найти, давай ты это сделаешь ищи выход , выполняй задачу"
HISTORY = [
    {"role": "user", "content": "скачай видео"},
    {"role": "assistant", "content": (
        "Я — автономный веб-исследователь, и у меня нет технической возможности напрямую "
        "скачивать файлы на ваше устройство или предоставлять прямые ссылки на скачивание видеофайлов."
    )},
]

#: Можно передать свой запрос: `python probe_doorman_identity.py "скачай видео"`.
#: История тогда пустая — как в реальном прогоне 21:25, где делегирования не случилось.
if len(sys.argv) > 1:
    QUERY = " ".join(sys.argv[1:])
    HISTORY = []


def _parse(events):
    out = []
    for e in events:
        try:
            out.append(json.loads(e.replace("data: ", "").strip()))
        except Exception:
            continue
    return out


async def main():
    from agent.llm_manager import (
        LLMManager, build_delegate_tool, load_agent_configs,
        resolve_agent_model,
    )
    from agent.roles_impl.base_agent import BaseAgent

    manager = LLMManager()
    await manager.initialize()
    try:
        role_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "roles", "doorman.yaml"
        )
        with open(role_path, "r", encoding="utf-8") as f:
            role_cfg = yaml.safe_load(f)

        model, _conf = resolve_agent_model(manager.registry, "doorman", "light", query=QUERY)
        assert model, "нет модели для швейцара"
        tools = build_delegate_tool(model.provider_type)

        agent = BaseAgent(manager, role_cfg, model, tools)
        prompt = agent.build_system_prompt()

        old_prompt = role_cfg.get("system_instruction", "")
        print(f"@@ модель швейцара: {model.name}")
        print(f"@@ промпт: было {len(old_prompt)} символов → стало {len(prompt)}")
        print(f"@@ identity в промпте: {'ЧТО ЭТО ЗА СИСТЕМА' in prompt}")
        print(f"@@ карта агентов: {'КОМУ МОЖНО ПЕРЕДАТЬ ЗАДАЧУ' in prompt}")
        print(f"@@ запрет отказов: {'ЗАПРЕТ ОТКАЗОВ' in prompt}")
        print(f"@@ ростер: {tools[0]['function']['parameters']['properties']['target_agent'].get('enum')}")
        assert "ЧТО ЭТО ЗА СИСТЕМА" in prompt and "ЗАПРЕТ ОТКАЗОВ" in prompt

        raw = [e async for e in agent.run_stream(QUERY, HISTORY)]
        events = _parse(raw)

        steps = [e.get("step") for e in events if e.get("type") == "step"]
        delegation = next((e for e in events if e.get("type") == "delegation"), None)
        result = next((e.get("content") for e in events if e.get("type") == "result"), None)

        print(f"@@ шаги: {steps}")
        print(f"@@ final_status: {agent.final_status}")
        if delegation:
            print(f"@@ ДЕЛЕГИРОВАНО → {delegation['agent']} ({delegation.get('complexity')})")
            print(f"@@ ТЗ агенту: {str(delegation.get('prompt'))[:300]}")
        elif result:
            print(f"@@ ответ в чат: {str(result)[:300]}")

        assert agent.final_status != "stub_response", "модель снова отказалась — фикс не сработал"
        print("@@ RESULT: doorman_no_refusal = True")
    finally:
        await manager.shutdown()


asyncio.run(main())
