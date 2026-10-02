"""
Сценарии из удачных прогонов (фаза 4).

Проверяем три обещания, каждое из которых легко нарушить незаметно:
  1. сценарий предлагается ТОЛЬКО по удачному прогону и только если прогон реально
     вызывал инструменты (иначе это «сценарий» из ничего);
  2. шаги берутся из фактических вызовов, порядок сохраняется, `tool_result_*` в план
     не попадает (иначе сценарий «вызывал» бы результаты);
  3. черновик без аргументов нельзя активировать: прогон не сохраняет аргументы вызовов,
     поэтому запускать такую заготовку наугад опасно.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from auth import create_session_token
import database
from database import Base, get_db
import main
import models
from services import scenarios as scenarios_service

engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _schema():
    """Схема нужна и сервисным тестам (без клиента таблиц бы не было вовсе)."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
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

    yield TestClient(main.app)

    main.app.dependency_overrides.clear()
    main.SessionLocal, database.SessionLocal = orig_main_sl, orig_db_sl


def _admin_cookie(db) -> str:
    user = models.User(role="admin", display_name="Scenarios Admin")
    db.add(user)
    db.commit()
    db.refresh(user)
    return create_session_token(user.id)


def _event(task_id: str, seq: int, payload: dict) -> models.AgentTaskEvent:
    return models.AgentTaskEvent(
        task_id=task_id,
        sequence_number=seq,
        event_type="stream",
        payload="data: " + json.dumps(payload, ensure_ascii=False),
    )


def _seed_run(db, *, task_status="completed", journal_status="success", tools=("web_search", "goto_url")):
    task = models.AgentTask(owner_id="1", status=task_status, query="собери новости по прилётам НПЗ")
    db.add(task)
    db.flush()

    seq = 0
    for tool in tools:
        db.add(_event(task.id, seq, {"type": "step", "step": f"tool_{tool}"}))
        seq += 1
        db.add(_event(task.id, seq, {"type": "step", "step": f"tool_result_{tool}"}))
        seq += 1

    if journal_status:
        db.add(models.RunJournal(task_id=task.id, status=journal_status, summary="план: 2/2 шагов"))
    db.commit()
    return task.id


def test_proposes_draft_from_successful_run():
    db = TestingSessionLocal()
    task_id = _seed_run(db)
    scenario, created = scenarios_service.propose_from_run(db, task_id)
    card = scenarios_service.scenario_card(scenario, created=created)
    journal = db.query(models.RunJournal).filter(models.RunJournal.task_id == task_id).first()
    db.close()

    assert created is True
    assert scenario.status == "draft", "предложение ничего не активирует"
    assert scenario.source_task_id == task_id
    assert card["tools"] == ["web_search", "goto_url"], "порядок вызовов и никаких tool_result_*"
    assert card["steps"] == 2
    assert card["needs_args"] is True and card["warning"]
    assert journal.scenario_proposed_id == scenario.id, "связка с журналом обязана появиться"


def test_refuses_failed_run():
    db = TestingSessionLocal()
    task_id = _seed_run(db, task_status="failed", journal_status="failed")
    with pytest.raises(ValueError, match="удачному прогону"):
        scenarios_service.propose_from_run(db, task_id)
    db.close()


def test_refuses_run_without_tools():
    db = TestingSessionLocal()
    task_id = _seed_run(db, tools=())
    with pytest.raises(ValueError, match="не вызывал ни одного инструмента"):
        scenarios_service.propose_from_run(db, task_id)
    db.close()


def test_same_tool_sequence_reuses_scenario():
    db = TestingSessionLocal()
    first_id = _seed_run(db)
    second_id = _seed_run(db)

    first, created_first = scenarios_service.propose_from_run(db, first_id)
    second, created_second = scenarios_service.propose_from_run(db, second_id)
    counts = db.query(models.ScenarioDefinition).count()
    db.close()

    assert created_first is True
    assert created_second is False and second.id == first.id, "тот же набор шагов не плодит копии"
    assert counts == 1


def test_activation_blocked_until_args_filled(client):
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    task_id = _seed_run(db)
    db.close()

    proposed = client.post(f"/tasks/{task_id}/scenario", cookies={"contextus_session": cookie})
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert body["status"] == "draft" and body["needs_args"] is True

    blocked = client.post(
        f"/scenarios/{body['id']}/activate", cookies={"contextus_session": cookie}
    )
    assert blocked.status_code == 400, blocked.text
    assert "не заполнены аргументы" in blocked.json()["detail"]

    # Человек заполнил аргументы — сценарий активируется (инструменты не привилегированные)
    db = TestingSessionLocal()
    scenario = db.query(models.ScenarioDefinition).filter(models.ScenarioDefinition.id == body["id"]).first()
    scenario.steps = json.dumps(
        [
            {"tool": "web_search", "args_template": {"query": "{{query}}"}, "description": "поиск"},
            {"tool": "goto_url", "args_template": {"url": "https://example.com"}, "description": "страница"},
        ],
        ensure_ascii=False,
    )
    db.commit()
    db.close()

    activated = client.post(f"/scenarios/{body['id']}/activate", cookies={"contextus_session": cookie})
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "activated"


def test_api_refuses_bad_run_with_reason(client):
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    task_id = _seed_run(db, task_status="failed", journal_status="failed")
    db.close()

    resp = client.post(f"/tasks/{task_id}/scenario", cookies={"contextus_session": cookie})
    assert resp.status_code == 400
    assert "удачному прогону" in resp.json()["detail"]
