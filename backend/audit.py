from sqlalchemy.orm import Session
import models

def log_action(db: Session, user_id: int | None, source: str, action: str, target_id: str | None, detail: str, result: str):
    """
    Записывает действие в AuditLog.
    source: "telegram" | "web_ui"
    action: "approve_action", "reject_action", "edit_role", и т.д.
    result: "success" | "denied"
    """
    log_entry = models.AuditLog(
        user_id=user_id,
        source=source,
        action=action,
        target_id=target_id,
        detail=detail,
        result=result
    )
    db.add(log_entry)
    db.commit()
