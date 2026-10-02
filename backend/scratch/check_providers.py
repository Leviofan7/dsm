"""
Быстрая проверка «не кончились ли деньги/ключи у ИИ» — одна команда.

Запуск: docker compose exec -T fastapi_backend python -B /app/scratch/check_providers.py
Только чтение, ничего не меняет. Секреты не печатаются — только «задан/пуст».

Повод: 26.09 пользователь предположил, что тихий провал TG-запроса был из-за
закончившихся денег на облачном ИИ. Проверять это глазами в логах долго и ненадёжно
(логи контейнера стираются при `up -d`), поэтому — отдельный инструмент.
"""

import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

key = os.getenv("DEEPSEEK_API_KEY", "")
print(f"@@ DEEPSEEK_API_KEY задан: {bool(key)}")

if key:
    try:
        r = httpx.get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20,
        )
        if r.status_code == 200:
            d = r.json()
            info = (d.get("balance_infos") or [{}])[0]
            print(
                f"@@ баланс DeepSeek: доступно={d.get('is_available')} "
                f"{info.get('total_balance')} {info.get('currency')}"
            )
        else:
            print(f"@@ баланс DeepSeek: HTTP {r.status_code} — {r.text[:160]}")
    except Exception as e:
        print(f"@@ баланс DeepSeek: ошибка {type(e).__name__}: {e}")

    # Баланс — ещё не всё: ключ может быть отозван / квота исчерпана / сервис лежит.
    try:
        r = httpx.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            timeout=30,
        )
        verdict = "✅ работает" if r.status_code == 200 else f"❌ {r.text[:200]}"
        print(f"@@ живой вызов deepseek-chat: HTTP {r.status_code} {verdict}")
    except Exception as e:
        print(f"@@ живой вызов deepseek-chat: ошибка {type(e).__name__}: {e}")

for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
    value = os.getenv(name, "")
    print(f"@@ {name}: {'задан' if value else 'ПУСТ'}")

try:
    r = httpx.get(f"{os.getenv('OLLAMA_URL', 'http://localhost:11434')}/api/tags", timeout=15)
    names = [m.get("name") for m in (r.json().get("models") or [])]
    print(f"@@ Ollama: HTTP {r.status_code} | локальных моделей: {len(names)} | примеры: {names[:5]}")
except Exception as e:
    print(f"@@ Ollama: ошибка {type(e).__name__}: {e}")
