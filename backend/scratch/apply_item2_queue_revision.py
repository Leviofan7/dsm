"""Пункт 2 ТЗ: ревизия зависших запросов в PendingPrivilegedAction + перевод в intent.

Что делает:
  * 34efde6b (prompt_update: дедуп вызовов)  → rejected, причина «Реализовано в ядре»
  * 35bf7ada (coder_task: LoopDetector)      → rejected, причина «Реализовано в ядре»
  * b2a70b83 (coder_task: web-stealth)       → закрывается, проблема оформляется новым intent
                                               по стандарту: intents/active/2026-09-26-web-stealth-false-block-detection/

Почему причина пишется в audit, а не в строку: у PendingPrivilegedAction НЕТ колонки комментария
(только action_type/target/instruction/reasoning/session_id/status/diff_content). Пишем в audit_log —
канонический журнал «кто и почему решил» (и это же аргумент за переход на ActionRequest,
где human_comment есть изначально).
"""
import sys

sys.path.insert(0, "/app")

REJECTED_IN_CORE = "Реализовано в ядре"
INTENT_ID = "2026-09-26-web-stealth-false-block-detection"

INTENT_TITLE = "Ложные детекции блокировок и битые ссылки в web-stealth (SPA-рендеринг)"
INTENT_TEXT = """Web-stealth агент регулярно выдаёт ложные срабатывания на SPA-страницах.

Наблюдаемые проблемы (из предложения meta-analyst b2a70b83 от 2026-09-01):

1. **Ложная детекция блокировки.** Агент запрашивает помощь человека (captcha/Cloudflare)
   там, где никакой блокировки нет: страница отдаёт пустой DOM, пока клиентский JS не
   дорисовал контент, и это принимается за защиту.
2. **Битые ссылки.** Часть ссылок, собранных из такой недорисованной страницы, ведёт в никуда —
   навигация происходит до готовности SPA-роутов.

Почему это важно: каждый ложный запрос впустую поднимает человека (HITL) и останавливает
автономную работу; битые переходы дают ложные результаты исследования, которые дальше
попадают в ответы агента.
"""


def main() -> None:
    import shutil
    import time

    from database import SessionLocal
    import models
    import audit
    from services import intent as intent_service

    shutil.copy2("/app/contextus.db", f"/tmp/contextus.db.bak-{int(time.time())}")
    print("@@ BACKUP: /tmp/contextus.db.bak-*")

    db = SessionLocal()
    try:
        for row_id, note in (("34efde6b", REJECTED_IN_CORE), ("35bf7ada", REJECTED_IN_CORE)):
            row = db.query(models.PendingPrivilegedAction).filter(
                models.PendingPrivilegedAction.id.like(f"{row_id}%")
            ).first()
            if not row:
                print(f"@@ НЕ НАЙДЕНО: {row_id}")
                continue
            was = row.status
            row.status = "rejected"
            db.commit()
            audit.log_action(
                db, 1, "web_ui", "reject_action", row.id,
                f"{note} (ревизия очереди подтверждений: было {was})", "success",
            )
            print(f"@@ {row.id[:8]} {row.action_type:14} {was} → rejected  (audit: {note})")

        # Третья запись: проблема переезжает в intent, строку закрываем, чтобы очередь не висела
        third = db.query(models.PendingPrivilegedAction).filter(
            models.PendingPrivilegedAction.id.like("b2a70b83%")
        ).first()
        third_id = third.id if third else None
        if third:
            third.status = "rejected"
            db.commit()
            audit.log_action(
                db, 1, "web_ui", "reject_action", third.id,
                f"Переведено в intent {INTENT_ID} (очередь подтверждений чистится, история сохраняется)",
                "success",
            )
            print(f"@@ {third.id[:8]} {third.action_type:14} → rejected (переведено в intent)")
    finally:
        db.close()

    # ── intent по действующему стандарту (services/intent.py) ──
    try:
        path = intent_service.create(INTENT_ID, INTENT_TITLE, "operator")
        print(f"@@ INTENT создан: {path}")
    except Exception as e:
        print(f"@@ INTENT уже существует или ошибка: {e}")

    intent_service.update_section(INTENT_ID, intent_service.SECTION_GOAL, INTENT_TEXT.strip())
    intent_service.append_journal(
        INTENT_ID, "operator",
        f"заведено из предложения meta-analyst {third_id} (создано 2026-09-01, висело без решения)",
    )

    doc = intent_service.read(INTENT_ID)
    print(f"@@ INTENT status={doc['frontmatter']['status']} created_by={doc['frontmatter']['created_by']}")
    print(f"@@ INTENT Цель: {doc['goal'][:80]!r}... ({len(doc['goal'])} символов)")
    print(f"@@ INTENT журнал: {doc['journal']}")

    db = SessionLocal()
    try:
        left = db.query(models.PendingPrivilegedAction).all()
        print("@@ ОЧЕРЕДЬ после ревизии:", [(r.id[:8], r.action_type, r.status) for r in left])
    finally:
        db.close()


main()
