"""
Самоанализ прогона: интерпретация фактов и что с ней происходит дальше.

Стражи против тихих потерь:
  1. класс проблемы определяется ПО ДАННЫМ (provider/stub/timeout/verification/…), а не «на глаз»;
  2. подсказки конкретны и проверяемы (в них есть путь к инструменту диагностики/действие);
  3. `self_review` заполняется при финализации журнала — раньше поле было декорацией;
  4. LLM-ревизия (meta_analyst) СОХРАНЯЕТСЯ и переживает повторную финализацию:
     раньше её результат выбрасывался (create_task без записи) — работа уходила в никуда;
  5. `journal_patterns` агрегирует классы проблем и подсказки по серии прогонов (мета-анализ).
"""

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from database import Base
import models
from services import artifacts as artifacts_service
from services import review as review_service

engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


def _task(db, *, status="completed", task_type="research"):
    task = models.AgentTask(owner_id="1", status=status, task_type=task_type, query="собери новости")
    db.add(task)
    db.commit()
    db.refresh(task)
    return task.id


def _trace(db, task_id, *, status="success", tools=("web_search",), verified=None,
           details=None, duration_ms=1000):
    db.add(models.ExecutionTrace(
        session_id=task_id, task_id=task_id,
        task_type_classified="research", task_type_final="research",
        model_used="deepseek-chat", model_selected="deepseek-chat", planner_enabled=False,
        tools_available=len(tools), tools_called=len(tools),
        tools_called_names=json.dumps(list(tools)), actions_log=json.dumps(list(tools)),
        stage_durations=json.dumps({"executor": duration_ms}), duration_ms=duration_ms,
        final_status=status, tool_verified=verified, tool_verification_details=details,
    ))
    db.commit()


def _review(db, task_id, **kwargs):
    journal = artifacts_service.finalize_journal(db, task_id, **kwargs)
    return artifacts_service.journal_card(journal)["self_review"]


def test_success_review_has_tools_models_and_scenario_hint(db):
    task_id = _task(db)
    _trace(db, task_id, tools=("web_search", "goto_url"))

    review = _review(db, task_id, status="success")

    assert review["failure_mode"] == "none"
    assert review["used_tools"] == ["goto_url", "web_search"]
    assert review["models"] == ["deepseek-chat"]
    assert review["signals"]["tool_calls"] == 2
    assert review["outcome"]["status"] == "success"
    assert any("сценарий" in h for h in review["improvement_hints"])


def test_provider_failure_gets_actionable_hint(db):
    task_id = _task(db, status="failed")

    review = _review(db, task_id, status="failed", errors=["Ошибка: все модели недоступны."])

    assert review["failure_mode"] == "provider_unavailable"
    assert any("check_providers.py" in h for h in review["improvement_hints"]), (
        "подсказка обязана вести к конкретному действию, а не «проверьте провайдеров»"
    )


def test_verification_failure_is_detected_from_traces(db):
    task_id = _task(db)
    _trace(db, task_id, verified=False, details="File out.txt does not exist after write_file.")

    review = _review(db, task_id, status="failed", errors=["Прогон упал"])

    assert review["failure_mode"] == "verification_failed"
    assert review["signals"]["verification_failures"] == 1
    assert "verification_details" in review["signals"]


def test_run_without_traces_is_its_own_mode(db):
    """Прогон, умерший до первого шага (импорт/бюджет/краш), нельзя мешать с «модель не смогла»."""
    task_id = _task(db, status="failed")

    review = _review(db, task_id, status="failed", errors=["ImportError: boom"])

    assert review["failure_mode"] == "no_traces"
    assert review["signals"]["traces"] == 0


def test_stub_response_is_classified_before_other_signals(db):
    task_id = _task(db)
    _trace(db, task_id, status="stub_response")

    review = _review(db, task_id, status="failed", errors=["модель не смогла выполнить"])

    assert review["failure_mode"] == "stub_response"


def test_silent_miss_no_tool_calls(db):
    """Трейсы есть, но инструменты не вызывались — «тихий промах» (модель ответила по памяти)."""
    task_id = _task(db)
    _trace(db, task_id, tools=())

    review = _review(db, task_id, status="failed", errors=["ответ без инструментов"])

    assert review["failure_mode"] == "model_declined_tools"


def test_cancelled_is_its_own_mode(db):
    task_id = _task(db, status="cancelled")

    review = _review(db, task_id, status="cancelled")

    assert review["failure_mode"] == "cancelled"


def test_llm_review_is_preserved_across_refinalize(db):
    """LLM-ревизия дорогая и невоспроизводимая: повторная финализация её не теряет."""
    task_id = _task(db)
    _trace(db, task_id, tools=("web_search",))
    artifacts_service.finalize_journal(db, task_id, status="success")
    review_service.attach_llm_review(db, task_id, "Ревизор: модель выбрала слабую цепочку")

    card = artifacts_service.journal_card(
        artifacts_service.finalize_journal(db, task_id, status="success")
    )
    review = card["self_review"]

    assert review["llm_review"]["text"].startswith("Ревизор")
    assert review["failure_mode"] == "none", "детерминированные блоки пересобрались, ревизия осталась"


def test_attach_llm_review_creates_journal_when_missing(db):
    task_id = _task(db, status="failed")

    review_service.attach_llm_review(db, task_id, "текст ревизии")

    row = db.query(models.RunJournal).filter(models.RunJournal.task_id == task_id).first()
    assert row is not None, "ревизию надо куда-то положить, даже если журнал ещё не создан"
    parsed = review_service.parse_self_review(row.self_review)
    assert parsed["llm_review"]["text"] == "текст ревизии"


def test_patterns_aggregate_failure_modes_and_hints(db):
    ok = _task(db)
    _trace(db, ok, tools=("web_search",))
    artifacts_service.finalize_journal(db, ok, status="success")

    for err in ("Ошибка: все модели недоступны.", "Ошибка: модель не ответила и нет fallback."):
        bad = _task(db, status="failed")
        artifacts_service.finalize_journal(db, bad, status="failed", errors=[err])

    patterns = artifacts_service.journal_patterns(db, limit=50)

    assert patterns["window"] == 3
    assert patterns["by_failure_mode"]["provider_unavailable"] == 2
    assert patterns["by_failure_mode"]["none"] == 1
    assert patterns["top_hints"], "подсказки обязаны агрегироваться по серии прогонов"
    assert patterns["top_hints"][0]["count"] >= 1
