"""Живая проверка режима тишины Telegram: посторонний не получает ничего и не оставляет следов.

Владельцем НЕ пишем: это запустило бы настоящую задачу агента и отправило вам
сообщение в Telegram. Владельческий путь проверяется тестами (test_auth_and_security.py).
Секрет вебхука читается из env и не печатается.
"""
import os
import sys

import httpx

sys.path.insert(0, "/app")

BASE = "http://localhost:8000"
STRANGER = 555000111  # чужой chat_id, в БД его быть не должно


def main() -> None:
    from database import SessionLocal
    import models

    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    print("@@ WEBHOOK_SECRET:", "задан" if secret else "НЕ ЗАДАН")
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret}

    def count_traces() -> tuple[int, int]:
        db = SessionLocal()
        try:
            users = db.query(models.User).filter(models.User.telegram_chat_id == str(STRANGER)).count()
            convs = db.query(models.Conversation).filter(
                models.Conversation.telegram_chat_id == str(STRANGER)
            ).count()
            return users, convs
        finally:
            db.close()

    before = count_traces()

    msg = httpx.post(
        f"{BASE}/telegram/webhook",
        json={"message": {"chat": {"id": STRANGER}, "text": "привет, что ты умеешь?"}},
        headers=headers,
        timeout=20,
    )
    print("@@ STRANGER_MESSAGE:", msg.status_code, msg.text[:120])

    cb = httpx.post(
        f"{BASE}/telegram/webhook",
        json={
            "callback_query": {
                "id": "cb_probe_silence",
                "message": {"chat": {"id": STRANGER}},
                "data": "ar_approve_probe",
            }
        },
        headers=headers,
        timeout=20,
    )
    print("@@ STRANGER_CALLBACK:", cb.status_code, cb.text[:120])

    # вебхук проверяет секрет до всего остального — отдельно убеждаемся, что он не ослаб
    bad = httpx.post(
        f"{BASE}/telegram/webhook",
        json={"message": {"chat": {"id": STRANGER}, "text": "x"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        timeout=20,
    )
    print("@@ WRONG_SECRET:", bad.status_code, bad.text[:80])

    after = count_traces()
    print(f"@@ TRACES_в_БД: до={before} после={after} (ожидаем (0, 0) и отсутствие роста)")

    db = SessionLocal()
    try:
        audit_rows = db.query(models.AuditLog).filter(models.AuditLog.source == "telegram").count()
        print("@@ AUDIT_telegram_записей:", audit_rows)
    finally:
        db.close()


main()
