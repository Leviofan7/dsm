"""
Сценарии из удачных прогонов (фаза 4) + контракт активации.

Проверяем обещания, каждое из которых легко нарушить незаметно:
  1. сценарий предлагается ТОЛЬКО по удачному прогону и только если прогон реально
     вызывал инструменты (иначе это «сценарий» из ничего);
  2. шаги берутся из фактических вызовов, порядок сохраняется, `tool_result_*` в план
     не попадает (иначе сценарий «вызывал» бы результаты);
  3. черновик без аргументов нельзя активировать: прогон не сохраняет аргументы вызовов,
     поэтому запускать такую заготовку наугад опасно;
  4. активация требует ЯВНОГО подтверждения (`confirm=true`) — молчаливого включения нет;
  5. аргументы валидируются по схеме инструмента: отсутствие обязательного поля, пустое
     значение и опечатка в имени аргумента — это 422 с точным отчётом, а не тихий запуск;
  6. `approved_hash` совпадает с проверкой исполнителя (sha256 СЫРОГО текста steps),
     иначе проверка целостности у LinearScenarioExecutor была бы мёртвой.
"""

import hashlib
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


#: Схемы инструментов для тестов валидации. В проде они берутся из реестра MCP,
#: в тестах MCP не поднят — поэтому подменяем источник, а не саму логику.
_TOOL_SCHEMAS = {
    "web_search": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    "goto_url": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
}

_GOOD_STEPS = [
    {"tool": "web_search", "args_template": {"query": "{{query}}"}, "description": "поиск"},
    {"tool": "goto_url", "args_template": {"url": "https://example.com"}, "description": "страница"},
]


def _propose(client, cookie, task_id) -> str:
    resp = client.post(f"/tasks/{task_id}/scenario", cookies={"contextus_session": cookie})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "draft" and body["needs_args"] is True
    return body["id"]


def test_activation_requires_explicit_confirmation(client, monkeypatch):
    """Активация без confirm=true не проходит, даже если аргументы уже валидны."""
    monkeypatch.setattr(main, "_scenario_tool_schemas", lambda: _TOOL_SCHEMAS)
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    db.add(
        models.ScenarioDefinition(
            id="sc-no-confirm",
            name="Сценарий без подтверждения",
            steps=json.dumps(_GOOD_STEPS, ensure_ascii=False),
            status="draft",
        )
    )
    db.commit()
    db.close()

    # Тело пустое → подтверждения нет
    resp = client.post(
        "/scenarios/sc-no-confirm/activate", cookies={"contextus_session": cookie}, json={}
    )
    assert resp.status_code == 400, resp.text
    assert "confirm" in resp.json()["detail"]

    # Тело вообще не передано → тоже отказ, а не 200 по умолчанию
    resp2 = client.post("/scenarios/sc-no-confirm/activate", cookies={"contextus_session": cookie})
    assert resp2.status_code == 400, resp2.text

    # Явное подтверждение с уже сохранёнными валидными аргументами — проходит
    ok = client.post(
        "/scenarios/sc-no-confirm/activate",
        cookies={"contextus_session": cookie},
        json={"confirm": True},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "activated"


def test_activation_rejects_missing_required_args(client, monkeypatch):
    """422 с точным отчётом: пустые аргументы, отсутствующее обязательное поле, лишний ключ."""
    monkeypatch.setattr(main, "_scenario_tool_schemas", lambda: _TOOL_SCHEMAS)
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    task_id = _seed_run(db)
    db.close()

    sid = _propose(client, cookie, task_id)
    resp = client.post(
        f"/scenarios/{sid}/activate",
        cookies={"contextus_session": cookie},
        json={
            "confirm": True,
            "steps": [
                {"tool": "web_search", "args_template": {}},
                {"tool": "goto_url", "args_template": {"urlx": "https://example.com"}},
            ],
        },
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    report = detail["report"]
    assert report["schema_checked"] is True
    assert "web_search" in report["empty_args"], "пустые аргументы обязаны быть названы"
    assert report["missing_required"]["web_search"] == ["query"]
    assert report["missing_required"]["goto_url"] == ["url"], "опечатка в имени не заменяет обязательное поле"
    assert report["unknown_args"]["goto_url"] == ["urlx"]
    assert "query" in detail["message"] and "url" in detail["message"]


def test_activation_blocks_privileged_tool(client):
    """Привилегированный инструмент в сценарии — 403, независимо от аргументов."""
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    task_id = _seed_run(db)
    db.close()

    sid = _propose(client, cookie, task_id)
    resp = client.post(
        f"/scenarios/{sid}/activate",
        cookies={"contextus_session": cookie},
        json={"confirm": True, "steps": [{"tool": "write_file", "args_template": {"path": "a", "content": "b"}}]},
    )
    assert resp.status_code == 403, resp.text
    assert "write_file" in json.dumps(resp.json()["detail"], ensure_ascii=False)


def test_put_steps_validates_then_saves_without_activating(client, monkeypatch):
    """PUT /steps — рабочий путь человека: невалидное не сохраняем, валидное сохраняем как draft."""
    monkeypatch.setattr(main, "_scenario_tool_schemas", lambda: _TOOL_SCHEMAS)
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    task_id = _seed_run(db)
    db.close()

    sid = _propose(client, cookie, task_id)

    bad = client.put(
        f"/scenarios/{sid}/steps",
        cookies={"contextus_session": cookie},
        json={"steps": [{"tool": "web_search", "args_template": {}}]},
    )
    assert bad.status_code == 422, bad.text
    assert bad.json()["detail"]["report"]["empty_args"] == ["web_search"]

    ok = client.put(
        f"/scenarios/{sid}/steps",
        cookies={"contextus_session": cookie},
        json={"steps": _GOOD_STEPS},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "validated"
    assert ok.json()["scenario"]["needs_args"] is False

    db = TestingSessionLocal()
    row = db.query(models.ScenarioDefinition).filter(models.ScenarioDefinition.id == sid).first()
    stored = json.loads(row.steps)
    status = row.status
    db.close()

    assert stored[0]["args_template"]["query"] == "{{query}}", "аргументы обязаны дойти до БД"
    assert status == "draft", "заполнение аргументов само по себе не активирует сценарий"


def test_activation_locks_steps_with_executor_hash(client, monkeypatch):
    """approved_hash обязан совпадать с проверкой LinearScenarioExecutor (сырой sha256 steps)."""
    monkeypatch.setattr(main, "_scenario_tool_schemas", lambda: _TOOL_SCHEMAS)
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    task_id = _seed_run(db)
    db.close()

    sid = _propose(client, cookie, task_id)
    resp = client.post(
        f"/scenarios/{sid}/activate",
        cookies={"contextus_session": cookie},
        json={"confirm": True, "steps": _GOOD_STEPS},
    )
    assert resp.status_code == 200, resp.text

    db = TestingSessionLocal()
    row = db.query(models.ScenarioDefinition).filter(models.ScenarioDefinition.id == sid).first()
    expected = hashlib.sha256(row.steps.encode("utf-8")).hexdigest()
    approved_hash, approved_by, approved_at, steps = (
        row.approved_hash, row.approved_by, row.approved_at, row.steps,
    )
    db.close()

    assert approved_hash == expected, (
        "approved_hash не совпал с hash в LinearScenarioExecutor — исполнение заблокируется ложно"
    )
    assert approved_by and approved_at is not None
    assert json.loads(steps)[0]["args_template"]["query"] == "{{query}}"


def test_proposal_reason_is_recorded():
    """Почему предложен сценарий — отдельное поле, а не текст в description."""
    db = TestingSessionLocal()
    task_id = _seed_run(db)
    scenario, _ = scenarios_service.propose_from_run(db, task_id)
    card = scenarios_service.scenario_card(scenario)
    db.close()

    assert card["proposal_reason"], "причина предложения обязана сохраняться"
    assert task_id in card["proposal_reason"]
    assert card["source_task_id"] == task_id
    assert card["steps_detail"][0]["tool"] == "web_search"


def test_api_refuses_bad_run_with_reason(client):
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    task_id = _seed_run(db, task_status="failed", journal_status="failed")
    db.close()

    resp = client.post(f"/tasks/{task_id}/scenario", cookies={"contextus_session": cookie})
    assert resp.status_code == 400
    assert "удачному прогону" in resp.json()["detail"]
