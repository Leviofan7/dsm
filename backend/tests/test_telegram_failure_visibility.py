"""
Молчаливый провал TG-прогона — это баг, а не «просто тишина».

Инцидент 26.09: второй за день запрос сохранился в БД как user-сообщение и ответа не получил —
ни текста ошибки, ни трейса, ни строки в логе про «не смог». Для человека это выглядит как
«бот просто не ответил». Теперь конвейер обёрнут: исключение → трейсбек в лог + явное
сообщение в Telegram, чтобы тишина означала ровно одно — сообщение до бота не дошло.
"""

import asyncio
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

import main  # noqa: E402


def test_pipeline_failure_is_reported_to_user(monkeypatch):
    sent = []

    async def boom(chat_id, text):
        raise RuntimeError("имитация падения прогона")

    async def fake_send(chat_id, text):
        sent.append((chat_id, text))

    monkeypatch.setattr(main, "process_telegram_message", boom)
    monkeypatch.setattr(main, "send_telegram_message", fake_send)

    asyncio.run(main._guarded_process_telegram_message(424242, "тестовый запрос"))

    assert sent, "человек должен получить сообщение об ошибке, а не тишину"
    assert sent[0][0] == 424242
    assert "⚠️" in sent[0][1]
    assert "RuntimeError" in sent[0][1], "тип исключения полезен в тексте"


def test_pipeline_success_sends_nothing_extra(monkeypatch):
    called = []

    async def ok(chat_id, text):
        called.append((chat_id, text))

    async def fail_send(chat_id, text):
        raise AssertionError("при успешном прогоне уведомлений об ошибке быть не должно")

    monkeypatch.setattr(main, "process_telegram_message", ok)
    monkeypatch.setattr(main, "send_telegram_message", fail_send)

    asyncio.run(main._guarded_process_telegram_message(424242, "ок"))
    assert called == [(424242, "ок")]
