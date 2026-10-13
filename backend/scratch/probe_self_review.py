"""
Проба: самоанализ прогонов заполняется и отдаётся API. Живой прогон на РЕАЛЬНОЙ БД.

Проверяет сквозной путь:
  1. упавший прогон без трейсов → `failure_mode=no_traces` + конкретная подсказка;
  2. успешный прогон с инструментами → `failure_mode=none` + подсказка про сценарий;
  3. ошибка «модель недоступна» → `provider_unavailable` (+ путь к check_providers.py);
  4. LLM-ревизия (`llm_review`) сохраняется и ПЕРЕЖИВАЕТ повторную финализацию;
  5. `GET /tasks/{id}/journal` отдаёт `self_review` разобранным объектом;
  6. `GET /journals/patterns` агрегирует `by_failure_mode` и `top_hints` по серии прогонов.

Запуск: docker compose exec -T fastapi_backend python -B /app/scratch/probe_self_review.py
Проба убирает за собой все строки (включая audit/web_sessions).
"""

import json
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import models
from auth import create_session_token
from services import artifacts as artifacts_service
from services import review as review_service

BASE = "http://localhost:8000"
OWNER = "probe-review"
MARK = "Проба самоанализа (probe)"


def _cleanup() -> None:
    db = database.SessionLocal()
    try:
        uids = [u.id for u in db.query(models.User).filter(models.User.display_name == MARK).all()]
        task_ids = [
            t.id for t in db.query(models.AgentTask).filter(models.AgentTask.owner_id == OWNER).all()
        ]
        if task_ids:
            db.query(models.RunJournal).filter(models.RunJournal.task_id.in_(task_ids)).delete(synchronize_session=False)
            # execution_traces.task_id → SET NULL, поэтому чистим явно, иначе останутся сироты
            db.query(models.ExecutionTrace).filter(models.ExecutionTrace.task_id.in_(task_ids)).delete(synchronize_session=False)
            for t in db.query(models.AgentTask).filter(models.AgentTask.id.in_(task_ids)).all():
                db.delete(t)
        if uids:
            db.query(models.AuditLog).filter(models.AuditLog.user_id.in_(uids)).delete(synchronize_session=False)
            db.query(models.WebSession).filter(models.WebSession.user_id.in_(uids)).delete(synchronize_session=False)
            db.query(models.User).filter(models.User.id.in_(uids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _task(db, *, status, task_type="research"):
    task = models.AgentTask(owner_id=OWNER, status=status, task_type=task_type, query="проба самоанализа")
    db.add(task)
    db.commit()
    db.refresh(task)
    return task.id


def _trace(db, task_id, *, status, tools):
    db.add(models.ExecutionTrace(
        session_id=task_id, task_id=task_id,
        task_type_classified="research", task_type_final="research",
        model_used="deepseek-chat", model_selected="deepseek-chat", planner_enabled=False,
        tools_available=len(tools), tools_called=len(tools),
        tools_called_names=json.dumps(list(tools)), actions_log=json.dumps(list(tools)),
        stage_durations=json.dumps({"executor": 1200}), duration_ms=1200,
        final_status=status,
    ))
    db.commit()


def main() -> None:
    _cleanup()

    db = database.SessionLocal()
    try:
        user = models.User(role="admin", display_name=MARK)
        db.add(user)
        db.commit()
        db.refresh(user)
        uid = user.id

        # 1. Упал до первого шага
        died = _task(db, status="failed")
        j_died = artifacts_service.finalize_journal(db, died, status="failed", errors=["ImportError: boom"])

        # 2. Успешный прогон с инструментами
        ok = _task(db, status="completed")
        _trace(db, ok, status="success", tools=("web_search", "goto_url"))
        j_ok = artifacts_service.finalize_journal(db, ok, status="success")

        # 3. Провайдер недоступен
        prov = _task(db, status="failed")
        j_prov = artifacts_service.finalize_journal(
            db, prov, status="failed", errors=["Ошибка: все модели недоступны."]
        )

        # 4. LLM-ревизия + повторная финализация (не должна её потерять)
        review_service.attach_llm_review(db, ok, "Ревизор: цепочка инструментов выбрана верно")
        j_ok2 = artifacts_service.finalize_journal(db, ok, status="success")

        died_review = artifacts_service.journal_card(j_died)["self_review"]
        ok_review = artifacts_service.journal_card(j_ok2)["self_review"]
        prov_review = artifacts_service.journal_card(j_prov)["self_review"]
    finally:
        db.close()

    print(f"@@ упал до шага: mode={died_review['failure_mode']} hints={died_review['improvement_hints']}")
    assert died_review["failure_mode"] == "no_traces"
    assert died_review["improvement_hints"], "подсказка обязана быть"

    print(f"@@ успех: mode={ok_review['failure_mode']} tools={ok_review['used_tools']} "
          f"hints={ok_review['improvement_hints']}")
    assert ok_review["failure_mode"] == "none"
    assert ok_review["used_tools"] == ["goto_url", "web_search"]
    assert any("сценарий" in h for h in ok_review["improvement_hints"])

    print(f"@@ провайдер: mode={prov_review['failure_mode']}")
    assert prov_review["failure_mode"] == "provider_unavailable"
    assert any("check_providers.py" in h for h in prov_review["improvement_hints"])

    llm_text = (ok_review.get("llm_review") or {}).get("text", "")
    print(f"@@ llm_review пережил пересборку: {llm_text[:60]!r}")
    assert llm_text.startswith("Ревизор"), "повторная финализация потеряла LLM-ревизию"

    # 5-6. Проверяем контракт API поверх реального сервера
    cookie = create_session_token(uid)
    c = httpx.Client(base_url=BASE, cookies={"contextus_session": cookie}, timeout=30)

    r = c.get(f"/tasks/{ok}/journal")
    assert r.status_code == 200, r.text
    api_review = r.json()["self_review"]
    print(f"@@ GET /tasks/{{id}}/journal: self_review тип={type(api_review).__name__} "
          f"mode={api_review['failure_mode']}")
    assert isinstance(api_review, dict) and api_review["failure_mode"] == "none"

    r = c.get("/journals/patterns", params={"limit": 200})
    assert r.status_code == 200, r.text
    patterns = r.json()
    print(f"@@ GET /journals/patterns: window={patterns['window']} "
          f"by_failure_mode={patterns['by_failure_mode']} top_hints={len(patterns['top_hints'])}")
    assert patterns["by_failure_mode"].get("provider_unavailable", 0) >= 1
    assert patterns["top_hints"], "подсказки обязаны агрегироваться"

    c.close()
    _cleanup()
    print("@@ RESULT: self_review_pipeline = True")


main()
