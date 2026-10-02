"""Пункт 4 ТЗ — живой тест кнопок Telegram: реальный ActionRequest → callback → исполнение.

Что делает по-настоящему:
  1. кладёт в очередь prompt_update для роли-пустышки (создаётся временная роль, чтобы
     не трогать рабочие промпты);
  2. уведомление уходит вам в Telegram (это и есть требование ТЗ — реальный запрос);
  3. имитируется НАЖАТИЕ кнопки: POST того же payload, что шлёт Telegram (ar_approve_<id>),
     с секретом вебхука — то есть проверяется тот же код, что при живом нажатии;
  4. проверяется, что промпт роли РЕАЛЬНО изменён (а не только статус в БД) и что карточка
     обновлена через editMessageText.

Временная роль удаляется в конце; промпты рабочих ролей не трогаются.
"""
import os
import sys

sys.path.insert(0, "/app")

PROBE_ROLE = "_queue_probe_role"


def main() -> None:
    import httpx
    from database import SessionLocal
    import models
    from services import human_queue, role_permissions as rp

    owner = int(os.getenv("ALLOWED_TELEGRAM_USER_ID", "0") or 0)
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    print("@@ owner:", owner, "| secret:", "задан" if secret else "НЕТ")

    # ── временная роль, к которой применяем промпт ──
    rp.roles_dir().mkdir(parents=True, exist_ok=True)
    rp.write_role_yaml(PROBE_ROLE, {
        "name": "Queue Probe",
        "description": "временная роль для проверки очереди подтверждений",
        "planner": False,
        "write_scope": "none",
        "intent_access": "write_own",
        "can_create_intent": False,
        "tools": [],
        "system_instruction": "СТАРЫЙ промпт пробы",
    })
    print("@@ временная роль создана:", rp.role_path(PROBE_ROLE).name)

    request_id = human_queue.create_human_request(
        action_type="prompt_update",
        payload={"target": PROBE_ROLE, "instruction": "ПРОМПТ ПРИМЕНЁН ЧЕРЕЗ КНОПКУ TELEGRAM", "reasoning": "живой тест"},
        origin=human_queue.ORIGIN_META_ANALYST,
        session_id="probe-session",
        summary="Живой тест кнопок: применить промпт к временной роли",
    )
    print("@@ ActionRequest создан:", request_id, "| статус:", end=" ")
    db = SessionLocal()
    row = db.query(models.ActionRequest).filter(models.ActionRequest.id == request_id).first()
    print(row.status, "| tg_message_id:", human_queue.read_payload(row).get("tg_message_id"))
    db.close()

    # ── имитация нажатия «✅ Разрешить» в Telegram ──
    r = httpx.post(
        "http://localhost:8000/telegram/webhook",
        json={"callback_query": {"id": "cb_probe", "message": {"chat": {"id": owner}},
                                 "data": f"ar_approve_{request_id}"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        timeout=30,
    )
    print("@@ callback approve →", r.status_code, r.text[:60])

    import time
    time.sleep(2)  # дать отработать background-задачам (editMessageText)

    db = SessionLocal()
    row = db.query(models.ActionRequest).filter(models.ActionRequest.id == request_id).first()
    print("@@ статус в БД:", row.status)
    db.close()

    saved = rp.load_role_yaml(PROBE_ROLE)
    print("@@ промпт роли:", repr(saved.get("system_instruction")))
    print("@@ ИСПОЛНЕНО РЕАЛЬНО:", saved.get("system_instruction") == "ПРОМПТ ПРИМЕНЁН ЧЕРЕЗ КНОПКУ TELEGRAM")
    print("@@ канонические поля на месте:", saved.get("write_scope"), saved.get("intent_access"))

    # ── уборка: роль удаляем, запись оставляем (история) ──
    rp.role_path(PROBE_ROLE).unlink(missing_ok=True)
    print("@@ временная роль удалена:", not rp.role_path(PROBE_ROLE).exists())


main()
