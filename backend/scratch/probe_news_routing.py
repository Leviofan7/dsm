"""
Живая проверка маршрутизации: быстрый детектор + реальный облачный классификатор.

Запуск: docker compose exec -T fastapi_backend python -B /app/scratch/probe_news_routing.py
Стоимость: 1-2 коротких вызова DeepSeek (новостные запросы ловит детектор, без LLM).

Проверяет именно то, что сломалось 26.09: новостной запрос обязан уходить в research,
а не «отвечаться по памяти» слабой лёгкой моделью.
"""

import asyncio
import os
import sys

os.chdir("/tmp")  # временная БД (./contextus.db), проект не трогаем
sys.path.insert(0, "/app")

QUERIES = {
    "новости (ждём детектор)": "Новости по прилетам НПЗ в России и оценка ущерба",
    "удары (ждём детектор)": "удары по нпз сегодня украина нанесла дронами",
    "факт без ключевых слов (ждём LLM)": "какой сейчас курс доллара к рублю",
    "код (ждём LLM)": "напиши функцию на Python для сортировки списка",
}


async def main() -> None:
    from agent.llm_manager import LLMManager

    manager = LLMManager()
    await manager.initialize()

    llm_calls: list[str] = []
    original_raw = manager._raw_generate

    async def counting_raw(model, prompt, json_mode=False):
        llm_calls.append(model.name)
        return await original_raw(model, prompt, json_mode=json_mode)

    manager._raw_generate = counting_raw

    for label, query in QUERIES.items():
        before = len(llm_calls)
        result = await manager.route_intent(query)
        used = llm_calls[-1] if len(llm_calls) > before else "keyword (без LLM)"
        print(f"@@ {label:34} → {result.get('task_type'):18} | {used} | {query[:45]}")
        if result.get("error"):
            print(f"@@    ошибка классификатора: {result['error']}")

    print(f"@@ всего LLM-вызовов на всю проверку: {len(llm_calls)}")


if __name__ == "__main__":
    asyncio.run(main())
