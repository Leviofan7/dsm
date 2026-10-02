"""
Пункт 4 ТЗ — кнопки Telegram и, главное, что approve действительно что-то делает.

Проверяем именно ту регрессию, из-за которой миграция могла бы создать видимость работы:
  * gate-запись (request_diff_apply): approve = разбудить ожидающий Event, исполнения нет;
  * proposal-запись (prompt_update): approve = РЕАЛЬНО применить промпт к роли;
  * proposal-запись (coder_task): approve = запустить кодера в песочнице (статус coder_running);
  * apprentice-эскалация: approve/reject = выставить human_decision, иначе агент ждёт вечно.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from database import Base, get_db
import database
import main
import models
from api import action_requests as ar
from services import human_queue
from auth import create_session_token

engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


@pytest.fixture
def client(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = override_get_db
    orig_main_sl, orig_db_sl = main.SessionLocal, database.SessionLocal
    main.SessionLocal = TestingSessionLocal
    database.SessionLocal = TestingSessionLocal

    # Изолированный каталог ролей: применяем промпт к своей копии, не к рабочей
    from services import role_permissions as rp

    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "probe_role.yaml").write_text(
        "name: Probe\nplanner: false\nwrite_scope: none\nintent_access: write_own\n"
        "can_create_intent: false\ntools: []\nsystem_instruction: 'старый промпт'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rp, "ROLES_DIR", roles_dir)

    yield TestClient(main.app)

    main.app.dependency_overrides.clear()
    main.SessionLocal, database.SessionLocal = orig_main_sl, orig_db_sl
    Base.metadata.drop_all(bind=engine)


def _admin(db) -> models.User:
    user = models.User(role="admin", display_name="Queue Admin")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _admin_cookie(db) -> str:
    return create_session_token(_admin(db).id)


def _row(db, **kw):
    row = models.ActionRequest(
        action_type=kw.get("action_type", "prompt_update"),
        payload=json.dumps(kw.get("payload", {})),
        status=kw.get("status", "pending_friend_call"),
        supervisor_notes=kw.get("summary", ""),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ── gate-путь: approve не исполняет, только снимает ожидание ─────

def test_gate_approve_unblocks_event_without_execution(client):
    db = TestingSessionLocal()
    admin = _admin(db)
    row = _row(db, action_type="request_diff_apply", payload={"origin": "gate"})
    event = ar.get_event(row.id)
    db.close()

    resp = client.post(
        f"/api/action-requests/{row.id}/approve",
        cookies={"contextus_session": create_session_token(admin.id)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["dispatched"] is False and body["outcome"] == "unblocked"
    assert event.is_set(), "gate-инструмент должен быть разбужен"


# ── proposal-путь: approve РЕАЛЬНО исполняет ─────────────────────

def test_prompt_update_approve_really_applies_prompt(client, monkeypatch):
    """Главный тест: не «статус поменялся», а файл роли реально обновлён."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "")

    db = TestingSessionLocal()
    admin = _admin(db)
    row = _row(db, action_type="prompt_update", payload={
        "origin": "meta_analyst",
        "target": "probe_role",
        "instruction": "НОВЫЙ промпт из предложения",
        "reasoning": "потому что",
    })
    db.close()

    resp = client.post(
        f"/api/action-requests/{row.id}/approve",
        cookies={"contextus_session": create_session_token(admin.id)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dispatched"] is True and body["outcome"] == "applied"

    from services import role_permissions as rp
    from services import role_permissions

    saved = rp.load_role_yaml("probe_role")
    assert saved["system_instruction"] == "НОВЫЙ промпт из предложения", "промпт не применён — approve был статусным"
    # канонические поля не потерялись при записи
    assert saved["write_scope"] == "none" and saved["intent_access"] == "write_own"


def test_coder_task_approve_starts_sandbox_run(client, monkeypatch):
    """coder_task: approve обязан запустить кодера, а не только поставить «approved»."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "")

    started = []

    async def fake_run(request_id: str):
        started.append(request_id)

    monkeypatch.setattr(ar.human_queue, "run_coder_task", fake_run)

    db = TestingSessionLocal()
    admin = _admin(db)
    row = _row(db, action_type="coder_task", payload={
        "origin": "meta_analyst", "target": "agent/tool_router.py", "instruction": "добавь LoopDetector",
    })
    db.close()

    resp = client.post(
        f"/api/action-requests/{row.id}/approve",
        cookies={"contextus_session": create_session_token(admin.id)},
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "coder_running"
    assert started == [row.id], "исполнитель кодера не запущен"

    db = TestingSessionLocal()
    assert db.query(models.ActionRequest).filter(models.ActionRequest.id == row.id).first().status == "coder_running"
    db.close()


# ── apprentice: approve/reject должны будить ждущий шаг ──────────

def test_apprentice_approve_sets_human_decision(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "")

    db = TestingSessionLocal()
    admin = _admin(db)
    step = models.ApprenticeStep(session_id="s-1", proposed_tool="run_terminal_command",
                                 proposed_args="{}", proposed_reasoning="надо")
    db.add(step)
    db.commit()
    db.refresh(step)
    row = _row(db, action_type="terminal_command", payload={
        "origin": "apprentice", "tool": "run_terminal_command", "step_id": step.id,
    })
    step_id = step.id
    db.close()

    resp = client.post(
        f"/api/action-requests/{row.id}/approve",
        cookies={"contextus_session": create_session_token(admin.id)},
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "step_accepted"

    db = TestingSessionLocal()
    step = db.query(models.ApprenticeStep).filter(models.ApprenticeStep.id == step_id).first()
    assert step.human_decision == "accepted", "агент остался бы ждать вечно: human_decision не выставлен"
    db.close()


def test_apprentice_reject_sets_human_decision(client):
    db = TestingSessionLocal()
    admin = _admin(db)
    step = models.ApprenticeStep(session_id="s-2", proposed_tool="run_terminal_command",
                                 proposed_args="{}", proposed_reasoning="надо")
    db.add(step)
    db.commit()
    db.refresh(step)
    row = _row(db, action_type="terminal_command", payload={"origin": "apprentice", "step_id": step.id})
    step_id = step.id
    db.close()

    resp = client.post(
        f"/api/action-requests/{row.id}/reject",
        json={"reason": "не надо"},
        cookies={"contextus_session": create_session_token(admin.id)},
    )
    assert resp.status_code == 200

    db = TestingSessionLocal()
    step = db.query(models.ApprenticeStep).filter(models.ApprenticeStep.id == step_id).first()
    assert step.human_decision == "rejected"
    db.close()


# ── очередь: ссылки и защита ─────────────────────────────────────

def test_human_queue_creates_pending_friend_call_row(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "")

    request_id = human_queue.create_human_request(
        action_type="prompt_update",
        payload={"target": "probe_role", "instruction": "x"},
        origin=human_queue.ORIGIN_META_ANALYST,
        session_id="sess-1",
        summary="тест",
    )

    db = TestingSessionLocal()
    row = db.query(models.ActionRequest).filter(models.ActionRequest.id == request_id).first()
    assert row is not None and row.status == "pending_friend_call"
    payload = human_queue.read_payload(row)
    assert payload["origin"] == "meta_analyst" and payload["session_id"] == "sess-1"
    db.close()


def test_pending_endpoint_requires_admin(client):
    assert client.get("/api/action-requests/pending").status_code == 401

    db = TestingSessionLocal()
    cookie = {"contextus_session": _admin_cookie(db)}
    db.close()
    assert client.get("/api/action-requests/pending", cookies=cookie).status_code == 200


def test_failed_notification_is_visible_not_silent(monkeypatch, caplog):
    """
    Провал отправки в Telegram обязан быть видимым.

    httpx на 4xx исключения НЕ бросает, поэтому без проверки статуса ротированный токен
    молча съедал бы уведомления — а уведомление единственный канал, которым человек
    узнаёт о запросе.
    """
    import logging as _logging

    class _Resp:
        status_code = 401
        text = '{"ok":false,"description":"Unauthorized"}'

        def json(self):
            return {"ok": False, "description": "Unauthorized"}

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "post", lambda *a, **kw: _Resp())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "42")

    with caplog.at_level(_logging.ERROR, logger="contextus.human_queue"):
        human_queue.notify_human_sync("probe-id", "coder_task", "тест")

    assert any("Telegram не принял" in r.message for r in caplog.records), [r.message for r in caplog.records]


def test_apply_diff_endpoint_uses_payload_diff(client, monkeypatch):
    """Apply — отдельный шаг: берёт дифф из payload и зовёт git apply."""
    applied = {}

    def fake_apply(diff_text):
        applied["diff"] = diff_text
        return True, ""

    from services import sandbox as sandbox_service
    monkeypatch.setattr(sandbox_service, "apply_patch_to_project", fake_apply)

    db = TestingSessionLocal()
    admin = _admin(db)
    row = _row(db, action_type="coder_task", status="diff_ready", payload={
        "origin": "meta_analyst", "diff_content": "diff --git a/x b/x\n",
    })
    db.close()

    resp = client.post(
        f"/api/action-requests/{row.id}/apply-diff",
        cookies={"contextus_session": create_session_token(admin.id)},
    )
    assert resp.status_code == 200 and resp.json()["status"] == "applied"
    assert applied["diff"].startswith("diff --git")

    db = TestingSessionLocal()
    assert db.query(models.ActionRequest).filter(models.ActionRequest.id == row.id).first().status == "applied"
    db.close()


# ── Пункт 3b: реестр сессий и предупреждение об истечении ─────────

def test_session_registry_records_and_skips_throttled_writes(client):
    """Реестр помнит срок сессии, но не переписывает строку на каждый запрос."""
    from services import session_registry as sr
    from datetime import timedelta as _td

    sr.reset_throttle_cache()
    db = TestingSessionLocal()
    user = _admin(db)
    db.close()

    import datetime as _dt
    expires = _dt.datetime.utcnow() + _td(days=7)

    sr.touch(user.id, expires)
    db = TestingSessionLocal()
    row = db.query(models.WebSession).filter(models.WebSession.user_id == user.id).first()
    assert row is not None and row.expires_at is not None
    first_expires = row.expires_at
    db.close()

    # Второй вызов с тем же сроком — троттлится (строка не переписывается)
    sr.touch(user.id, expires)
    db = TestingSessionLocal()
    row = db.query(models.WebSession).filter(models.WebSession.user_id == user.id).first()
    assert row.expires_at == first_expires
    assert sr._last_write.get(user.id) is not None
    db.close()


def test_expiring_sessions_selected_once(client):
    """В выборку поллера попадают только сессии, истекающие в окне, и только один раз."""
    from services import session_registry as sr
    import datetime as _dt

    db = TestingSessionLocal()
    soon = _admin(db)
    later = _admin(db)
    db.add(models.WebSession(user_id=soon.id, expires_at=_dt.datetime.utcnow() + _dt.timedelta(hours=3)))
    db.add(models.WebSession(user_id=later.id, expires_at=_dt.datetime.utcnow() + _dt.timedelta(days=6)))
    db.commit()
    soon_id, later_id = soon.id, later.id
    db.close()

    selected = {item["user_id"] for item in sr.expiring_within(hours=24)}
    assert soon_id in selected and later_id not in selected

    sr.mark_notified(soon_id)
    assert soon_id not in {item["user_id"] for item in sr.expiring_within(hours=24)}, "повторное предупреждение"


def test_expiring_session_warning_sends_login_link(client, monkeypatch):
    """Поллер отправляет предупреждение со свежей одноразовой ссылкой входа."""
    from services import session_registry as sr
    import datetime as _dt

    db = TestingSessionLocal()
    user = _admin(db)
    db.add(models.WebSession(user_id=user.id, expires_at=_dt.datetime.utcnow() + _dt.timedelta(hours=2)))
    db.commit()
    user_id = user.id
    db.close()

    sent = []

    async def fake_send(chat_id, text):
        sent.append((chat_id, text))

    monkeypatch.setattr(main, "send_telegram_message", fake_send)
    monkeypatch.setattr(main, "ALLOWED_TELEGRAM_USER_ID", "1338181864")

    import asyncio
    asyncio.run(main._warn_expiring_sessions())

    assert sent, "предупреждение не отправлено"
    assert "/login?token=" in sent[0][1], "в предупреждении нет ссылки на продление"

    db = TestingSessionLocal()
    u = db.query(models.User).filter(models.User.id == user_id).first()
    assert u.login_token, "ссылка не создана"
    assert db.query(models.WebSession).filter(models.WebSession.user_id == user_id).first().notified_at is not None
    db.close()
