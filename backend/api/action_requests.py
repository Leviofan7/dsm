"""
action_requests.py — MCP-инструменты для Надсмотрщика (Supervisor 14B) и
API-эндпоинты для шлюза «Звонок другу» (HITL / Friend Call).

MCP-инструменты (для Supervisor):
  - approve_action(request_id)
  - escalate_to_friend_call(request_id, summary)
  - reject_action(request_id, feedback)

REST API (для фронтенда и Telegram):
  GET  /api/action-requests/pending
  POST /api/action-requests/{id}/approve
  POST /api/action-requests/{id}/reject
"""

import asyncio
import json
import logging
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from models import ActionRequest
from auth import require_admin
from services import human_queue
import audit

logger = logging.getLogger("contextus.action_requests")

# ─── Глобальный реестр asyncio.Event для разморозки ждущих инструментов ───────
# Ключ: action_request_id → asyncio.Event
# Когда человек или надсмотрщик принимают решение, event.set() разблокирует кодер.
_pending_events: dict[str, asyncio.Event] = {}


def get_event(request_id: str) -> asyncio.Event:
    """Возвращает или создаёт Event для данного action_request_id."""
    if request_id not in _pending_events:
        _pending_events[request_id] = asyncio.Event()
    return _pending_events[request_id]


def resolve_event(request_id: str):
    """Разблокирует ждущий инструмент и чистит реестр."""
    event = _pending_events.pop(request_id, None)
    if event:
        event.set()


# ─── Pydantic schemas ──────────────────────────────────────────────────────────

class RejectBody(BaseModel):
    reason: str = ""


# ─── FastAPI Router ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/action-requests", tags=["action-requests"])


@router.get("/pending", dependencies=[Depends(require_admin)])
def list_pending(db: Session = Depends(get_db)):
    """
    Возвращает список ActionRequest'ов в статусе pending_friend_call.
    Используется Web UI для отображения карточек ожидания.

    Только для админа: payload содержит то, что кот пытается сделать — команды и диффы,
    то есть содержимое, которое само по себе чувствительно.
    """
    requests = (
        db.query(ActionRequest)
        .filter(ActionRequest.status == "pending_friend_call")
        .order_by(ActionRequest.created_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "coder_task_id": r.coder_task_id,
            "action_type": r.action_type,
            "payload": json.loads(r.payload) if r.payload else {},
            "supervisor_notes": r.supervisor_notes,
            "created_at": r.created_at.isoformat(),
        }
        for r in requests
    ]


@router.post("/{request_id}/approve")
def approve(
    request_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
    background_tasks: BackgroundTasks = None,
):
    """
    Одобряет ActionRequest.
    Меняет статус на 'approved' и разблокирует asyncio.Event,
    чтобы ждущий MCP-инструмент в кодере продолжил работу.
    """
    req = db.query(ActionRequest).filter(ActionRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="ActionRequest not found")
    if req.status not in ("pending_friend_call", "pending_supervisor"):
        raise HTTPException(status_code=409, detail=f"Cannot approve: status is '{req.status}'")

    req.status = "approved"
    req.updated_at = datetime.utcnow()
    db.commit()

    # Развилка по типу: gate-записи только будят ожидающий инструмент, а предложения
    # meta-analyst / эскалации apprentice надо ИСПОЛНИТЬ — иначе approve молча меняет
    # статус и ничего не делает (см. services/human_queue.py).
    outcome = human_queue.dispatch_approval(req, current_user.id)

    if outcome.get("dispatched") and outcome.get("status") == "coder_running":
        background_tasks.add_task(human_queue.run_coder_task, req.id)
    elif outcome.get("status") == "unblocked":
        resolve_event(request_id)

    logger.info(f"✅ ActionRequest {request_id} approved by human → {outcome}")
    # status — состояние записи, outcome — что именно произошло при approve
    # (unblocked / applied / coder_running / step_accepted). Не смешиваем.
    return {"status": req.status, "outcome": outcome.get("status"), "dispatched": outcome.get("dispatched"),
            "step_id": outcome.get("step_id"), "error": outcome.get("error")}


@router.post("/{request_id}/reject")
def reject(request_id: str, body: RejectBody, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Отклоняет ActionRequest с необязательным комментарием.
    Меняет статус на 'rejected' и разблокирует asyncio.Event,
    чтобы кодер получил сигнал об отказе и мог скорректировать курс.
    """
    req = db.query(ActionRequest).filter(ActionRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="ActionRequest not found")
    if req.status not in ("pending_friend_call", "pending_supervisor"):
        raise HTTPException(status_code=409, detail=f"Cannot reject: status is '{req.status}'")

    req.status = "rejected"
    req.human_comment = body.reason
    req.updated_at = datetime.utcnow()
    db.commit()

    audit.log_action(db, current_user.id, "web_ui", "reject_action", request_id, body.reason, "success")

    # Отказ тоже должен доехать до ждущего: у apprentice это поле human_decision в БД,
    # иначе фоновый агент будет вечно ждать решения по отклонённому запросу.
    human_queue.reject_side_effects(req, current_user.id, body.reason)

    resolve_event(request_id)
    logger.info(f"❌ ActionRequest {request_id} rejected by human. Reason: {body.reason}")
    return {"status": "rejected"}


@router.post("/{request_id}/apply-diff")
def apply_diff(request_id: str, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Шаг «Применить» для готового диффа (только для coder_task-предложений).

    Отдельный шаг сохранён сознательно (наследие legacy /privileged-actions): approve лишь
    запускает кодера в песочнице, а перенос изменений в рабочий проект — второе осознанное
    действие человека. Коммит не делается (см. sandbox.apply_patch_to_project).
    """
    req = db.query(ActionRequest).filter(ActionRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="ActionRequest not found")
    if req.status != "diff_ready":
        raise HTTPException(status_code=409, detail=f"Cannot apply: status is '{req.status}'")

    ok, err = human_queue.apply_patch(req)
    if not ok:
        req.status = "apply_failed"
        db.commit()
        audit.log_action(db, current_user.id, "web_ui", "apply_diff", request_id, f"Git apply failed: {err}", "denied")
        raise HTTPException(status_code=422, detail=f"Git apply failed: {err}")

    req.status = "applied"
    req.updated_at = datetime.utcnow()
    db.commit()
    audit.log_action(db, current_user.id, "web_ui", "apply_diff", request_id, "Патч применён к проекту", "success")
    logger.warning(f"📦 Патч по запросу {request_id} применён к рабочему проекту")
    return {"status": "applied"}


# ─── MCP-инструменты для Надсмотрщика (импортируются в analyst_mcp.py) ────────

async def mcp_approve_action(request_id: str) -> str:
    """
    MCP-инструмент для Надсмотрщика: одобрить ActionRequest автоматически.
    Кодер немедленно разблокируется.
    """
    db = SessionLocal()
    try:
        req = db.query(ActionRequest).filter(ActionRequest.id == request_id).first()
        if not req:
            return json.dumps({"success": False, "error": f"ActionRequest {request_id} not found"})

        req.status = "approved"
        req.supervisor_notes = (req.supervisor_notes or "") + "\n[AUTO-APPROVED by Supervisor]"
        req.updated_at = datetime.utcnow()
        db.commit()
        resolve_event(request_id)
        logger.info(f"🤖 ActionRequest {request_id} auto-approved by Supervisor 14B")
        return json.dumps({"success": True, "status": "approved"})
    finally:
        db.close()


async def mcp_escalate_to_friend_call(request_id: str, summary: str) -> str:
    """
    MCP-инструмент для Надсмотрщика: эскалировать ActionRequest к человеку.
    Кодер остаётся заморожен — Event не снимается. Человек должен нажать Approve/Reject в UI или Telegram.
    """
    import os
    import httpx
    db = SessionLocal()
    try:
        req = db.query(ActionRequest).filter(ActionRequest.id == request_id).first()
        if not req:
            return json.dumps({"success": False, "error": f"ActionRequest {request_id} not found"})

        req.status = "pending_friend_call"
        req.supervisor_notes = summary
        req.updated_at = datetime.utcnow()
        db.commit()
        logger.warning(f"📞 ActionRequest {request_id} escalated to human. Summary: {summary[:100]}")

        # Уведомление в Telegram
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        tg_chat_id = os.getenv("ALLOWED_TELEGRAM_USER_ID", "")
        if tg_token and tg_chat_id:
            try:
                url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
                text = f"🚨 *Запрос разрешения Кодера*\n\nТип: `{req.action_type}`\nЗаметки: {summary}\n\nЧто делаем?"
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": "✅ Разрешить", "callback_data": f"ar_approve_{request_id}"},
                            {"text": "❌ Отклонить", "callback_data": f"ar_reject_{request_id}"}
                        ]
                    ]
                }
                async with httpx.AsyncClient() as client:
                    await client.post(url, json={"chat_id": tg_chat_id, "text": text, "parse_mode": "Markdown", "reply_markup": reply_markup})
            except Exception as e:
                logger.error(f"Failed to send Telegram escalation: {e}")

        return json.dumps({"success": True, "status": "pending_friend_call", "message": "Эскалировано человеку"})
    finally:
        db.close()



async def mcp_reject_action(request_id: str, feedback: str) -> str:
    """
    MCP-инструмент для Надсмотрщика: отклонить ActionRequest с обратной связью.
    Кодер разблокируется и получает текст ошибки для исправления.
    """
    db = SessionLocal()
    try:
        req = db.query(ActionRequest).filter(ActionRequest.id == request_id).first()
        if not req:
            return json.dumps({"success": False, "error": f"ActionRequest {request_id} not found"})

        req.status = "rejected"
        req.supervisor_notes = feedback
        req.updated_at = datetime.utcnow()
        db.commit()
        resolve_event(request_id)
        logger.info(f"🔴 ActionRequest {request_id} rejected by Supervisor. Feedback: {feedback[:100]}")
        return json.dumps({"success": True, "status": "rejected", "feedback": feedback})
    finally:
        db.close()
