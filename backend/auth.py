import os
import hmac
import hashlib
import json
import base64
import time
from typing import Optional
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
import models
import audit
from database import get_db

WEB_AUTH_SECRET = os.getenv("WEB_AUTH_SECRET") or os.getenv("TELEGRAM_WEBHOOK_SECRET") or "fallback_secret_change_me_12345"

def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")

def _b64decode(data: str) -> bytes:
    padding = 4 - (len(data) % 4)
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)

def create_session_token(user_id: int, ttl_seconds: int = 7 * 86400) -> str:
    """Создаёт подписанный HMAC-SHA256 токен сессии."""
    payload = {
        "uid": user_id,
        "exp": int(time.time()) + ttl_seconds
    }
    payload_bytes = json.dumps(payload, separators=(',', ':')).encode("utf-8")
    payload_b64 = _b64encode(payload_bytes)
    
    sig = hmac.new(WEB_AUTH_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = _b64encode(sig)
    
    return f"{payload_b64}.{sig_b64}"

def verify_session_token(token: str) -> Optional[int]:
    """Проверяет подпись и срок действия токена, возвращает user_id или None."""
    if not token or "." not in token:
        return None
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        payload_b64, sig_b64 = parts[0], parts[1]
        
        expected_sig = hmac.new(WEB_AUTH_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
        actual_sig = _b64decode(sig_b64)
        
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
            
        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
        if payload.get("exp", 0) < time.time():
            return None
            
        return payload.get("uid")
    except Exception:
        return None

def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[models.User]:
    """Извлекает и валидирует User из cookie contextus_session или Authorization Bearer."""
    token = request.cookies.get("contextus_session")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            
    if not token:
        return None
        
    user_id = verify_session_token(token)
    if not user_id:
        return None
        
    user = db.query(models.User).filter(models.User.id == user_id).first()
    return user

def require_user(request: Request, db: Session = Depends(get_db)) -> models.User:
    """Dependency: требует любого аутентифицированного пользователя."""
    user = get_current_user(request, db)
    if not user:
        audit.log_action(db, None, "web_ui", "access_denied", str(request.url.path), "Authentication required", "denied")
        raise HTTPException(status_code=401, detail="Authentication required")
    return user

def require_admin(request: Request, db: Session = Depends(get_db)) -> models.User:
    """Dependency: требует пользователя с ролью 'admin', логируя отказы в AuditLog."""
    user = get_current_user(request, db)
    if not user:
        audit.log_action(db, None, "web_ui", "access_denied", str(request.url.path), "Authentication required (admin route)", "denied")
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.role != "admin":
        audit.log_action(db, user.id, "web_ui", "access_denied", str(request.url.path), f"User {user.id} role '{user.role}' != 'admin'", "denied")
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
