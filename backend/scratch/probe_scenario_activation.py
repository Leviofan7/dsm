"""
Проба: активацию сценария нельзя «включить молча». Живой HTTP-прогон на РЕАЛЬНОЙ БД.

Проверяет контракт целиком:
  1. предложенный из прогона сценарий — черновик с пустыми аргументами;
  2. activate БЕЗ confirm=true → 400;
  3. PUT /steps с пустыми аргументами → 422 и отчёт с точным списком обязательных полей
     (схемы берутся из ЖИВОГО реестра MCP: `schema_checked=true` доказывает, что проверка
     реальная, а не «флаг по умолчанию»);
  4. PUT /steps с валидными аргументами → сохранено как draft (не активирует само);
  5. activate с confirm=true → active + approved_hash == sha256(сырой текст steps),
     то есть ровно то, что сверяет LinearScenarioExecutor;
  6. анонимный activate → 401.

Запуск: docker compose exec -T fastapi_backend python -B /app/scratch/probe_scenario_activation.py
Проба убирает за собой ВСЕ строки, включая audit/web_sessions.
"""

import hashlib
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import models
from auth import create_session_token

BASE = "http://localhost:8000"
OWNER = "probe-scenario"
MARK = "Проба сценария (probe)"

TOOLS = ("web_search", "goto_url")


def _cleanup() -> None:
    db = database.SessionLocal()
    try:
        uids = [u.id for u in db.query(models.User).filter(models.User.display_name == MARK).all()]
        task_ids = [
            t.id for t in db.query(models.AgentTask).filter(models.AgentTask.owner_id == OWNER).all()
        ]
        if task_ids:
            db.query(models.ScenarioDefinition).filter(
                models.ScenarioDefinition.source_task_id.in_(task_ids)
            ).delete(synchronize_session=False)
            db.query(models.RunJournal).filter(
                models.RunJournal.task_id.in_(task_ids)
            ).delete(synchronize_session=False)
            for t in db.query(models.AgentTask).filter(models.AgentTask.id.in_(task_ids)).all():
                db.delete(t)  # ORM-каскад снимет events/subtasks
        if uids:
            db.query(models.AuditLog).filter(models.AuditLog.user_id.in_(uids)).delete(synchronize_session=False)
            db.query(models.WebSession).filter(models.WebSession.user_id.in_(uids)).delete(synchronize_session=False)
            db.query(models.User).filter(models.User.id.in_(uids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _seed() -> tuple[int, str]:
    db = database.SessionLocal()
    try:
        user = models.User(role="admin", display_name=MARK)
        db.add(user)
        db.flush()

        task = models.AgentTask(owner_id=OWNER, status="completed", query="проба сценария: собери новости")
        db.add(task)
        db.flush()

        seq = 0
        for tool in TOOLS:
            db.add(models.AgentTaskEvent(
                task_id=task.id, sequence_number=seq, event_type="stream",
                payload="data: " + json.dumps({"type": "step", "step": f"tool_{tool}"}),
            ))
            seq += 1
            db.add(models.AgentTaskEvent(
                task_id=task.id, sequence_number=seq, event_type="stream",
                payload="data: " + json.dumps({"type": "step", "step": f"tool_result_{tool}"}),
            ))
            seq += 1
        db.add(models.RunJournal(task_id=task.id, status="success", summary="проба: 2 шага"))
        db.commit()
        return user.id, task.id
    finally:
        db.close()


def main() -> None:
    _cleanup()
    uid, task_id = _seed()
    cookie = create_session_token(uid)
    c = httpx.Client(base_url=BASE, cookies={"contextus_session": cookie}, timeout=30)

    # 1. Предложение из удачного прогона
    r = c.post(f"/tasks/{task_id}/scenario")
    assert r.status_code == 200, r.text
    card = r.json()
    sid = card["id"]
    print(f"@@ предложение: status={card['status']} needs_args={card['needs_args']} "
          f"reason={'есть' if card['proposal_reason'] else 'НЕТ'}")
    assert card["status"] == "draft" and card["needs_args"] is True
    assert card["proposal_reason"], "причина предложения обязана сохраняться"

    # 2. Активация без явного подтверждения
    r = c.post(f"/scenarios/{sid}/activate", json={})
    print(f"@@ activate без confirm: HTTP {r.status_code}")
    assert r.status_code == 400, r.text

    # 3. Заполнение аргументов «наугад» (пусто) → 422 + отчёт по живым схемам
    r = c.put(f"/scenarios/{sid}/steps", json={"steps": [{"tool": "web_search", "args_template": {}}]})
    assert r.status_code == 422, r.text
    report = r.json()["detail"]["report"]
    print(f"@@ PUT steps без args: HTTP {r.status_code} | schema_checked={report['schema_checked']} | "
          f"missing={report['missing_required']} | empty={report['empty_args']}")
    assert report["schema_checked"] is True, "живой MCP обязан отдать схемы — иначе проверка полей фиктивна"
    assert "query" in report["missing_required"].get("web_search", [])

    # 4. Валидные аргументы (по реальным схемам) сохраняются, но НЕ активируют сценарий
    steps = [
        {"tool": "web_search", "args_template": {"query": "{{query}}"}, "description": "поиск"},
        {"tool": "goto_url", "args_template": {"url": "https://example.com"}, "description": "страница"},
    ]
    r = c.put(f"/scenarios/{sid}/steps", json={"steps": steps})
    assert r.status_code == 200, r.text
    print(f"@@ PUT steps с args: HTTP {r.status_code} status={r.json()['status']}")
    assert r.json()["status"] == "validated"

    # 5. Явное подтверждение → active
    r = c.post(f"/scenarios/{sid}/activate", json={"confirm": True})
    assert r.status_code == 200, r.text
    print(f"@@ activate с confirm: HTTP {r.status_code} status={r.json()['status']}")
    assert r.json()["status"] == "activated"

    # 6. approved_hash = sha256(сырой текст steps) — как проверяет исполнитель
    db = database.SessionLocal()
    try:
        row = db.query(models.ScenarioDefinition).filter(models.ScenarioDefinition.id == sid).first()
        expected = hashlib.sha256(row.steps.encode("utf-8")).hexdigest()
        hash_ok = row.approved_hash == expected
        meta_ok = row.approved_by == str(uid) and row.approved_at is not None
        status = row.status
    finally:
        db.close()
    print(f"@@ approved_hash совпал с проверкой исполнителя: {hash_ok} | "
          f"approved_by/at заполнены: {meta_ok} | status={status}")
    assert hash_ok and meta_ok and status == "active"

    # 7. Аноним активировать не может
    anon = httpx.post(f"{BASE}/scenarios/{sid}/activate", json={"confirm": True}, timeout=15)
    print(f"@@ аноним activate: HTTP {anon.status_code}")
    assert anon.status_code == 401

    c.close()
    _cleanup()
    print("@@ RESULT: scenario_activation_contract = True")


main()
