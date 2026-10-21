"""
Уровни доступа + план перед действием.

Повод. Режим «Ученик» был бинарным: подтверждать КАЖДЫЙ вызов (включая `list_dir`,
который ничего не меняет) или не подтверждать ничего. На прогоне 87304239 это дало
6 карточек на простой осмотр репозитория, а при рестарте воркера — 4 навсегда
висящих шага (`human_decision IS NULL`).

Здесь закреплено:
  * read-only шаги не требуют подтверждения на уровне `ask`;
  * `allow_all`/`autopilot` не спрашивают про инструменты, но план всё равно показывают
    (кроме автопилота);
  * разбор плана из JSON и из списка, не больше трёх шагов;
  * ожидание решения имеет срок, а прошлые висящие шаги гасятся, а не копятся.
"""

import asyncio
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
from services import permissions as perm

engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


@pytest.fixture
def db_session(monkeypatch):
    Base.metadata.create_all(bind=engine)
    import agent.llm_manager as llm

    monkeypatch.setattr(llm, "SessionLocal", TestingSessionLocal)
    yield TestingSessionLocal
    Base.metadata.drop_all(bind=engine)


# ── Уровни: нормализация ──────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("ask", "ask"),
    ("apprentice", "ask"),       # исторический «Ученик» = спрашивать
    ("allow_all", "allow_all"),
    ("auto", "allow_all"),       # исторический «auto» = без вопросов
    ("AUTOPILOT", "autopilot"),
    (None, "allow_all"),
    ("", "allow_all"),
    ("мусор", "allow_all"),      # неизвестное значение не должно ужесточать молча
])
def test_normalize_level(raw, expected):
    assert perm.normalize_level(raw) == expected


def test_read_only_tools_are_known():
    assert perm.is_read_only("list_dir")
    assert perm.is_read_only("read_file")
    assert perm.is_read_only("list_run_artifacts")
    assert not perm.is_read_only("write_file")
    assert not perm.is_read_only("run_terminal_command")
    assert not perm.is_read_only(None)


# ── Уровни: кто кого спрашивает ───────────────────────────────────


def test_ask_does_not_stop_read_only_but_stops_writes():
    # Причина, по которой осмотр репозитория раньше требовал 6 подтверждений.
    assert perm.requires_approval("ask", "list_dir") is False
    assert perm.requires_approval("ask", "read_file") is False
    assert perm.requires_approval("ask", "write_file") is True
    # Финальный ответ показываем перед отправкой.
    assert perm.requires_approval("ask", None) is True


def test_allow_all_and_autopilot_never_ask():
    for level in ("allow_all", "autopilot"):
        assert perm.requires_approval(level, "write_file") is False
        assert perm.requires_approval(level, None) is False


def test_plan_flags():
    assert perm.plan_enabled("ask") is True
    assert perm.plan_enabled("allow_all") is True
    assert perm.plan_enabled("autopilot") is False       # автопилот не останавливается

    assert perm.plan_requires_approval("ask") is True
    assert perm.plan_requires_approval("allow_all") is False


def test_levels_payload_is_ordered_and_complete():
    payload = perm.levels_payload()
    assert [p["id"] for p in payload] == list(perm.LEVELS)
    assert all(p["label"] and p["description"] for p in payload)


# ── Разбор плана ──────────────────────────────────────────────────


def test_parse_plan_from_json():
    import agent.llm_manager as llm

    text = '{"steps": ["осмотреть структуру проекта", "прочитать README", "предложить задачу кодеру"]}'
    assert llm.LLMManager._parse_plan(text) == [
        "осмотреть структуру проекта",
        "прочитать README",
        "предложить задачу кодеру",
    ]


def test_parse_plan_from_bullet_list_and_caps_three():
    import agent.llm_manager as llm

    text = "1. первый шаг\n2) второй шаг\n- третий шаг\n* четвёртый шаг"
    steps = llm.LLMManager._parse_plan(text)
    assert len(steps) == 3
    assert steps[0] == "первый шаг"
    assert steps[-1] == "третий шаг"


def test_parse_plan_handles_garbage():
    import agent.llm_manager as llm

    assert llm.LLMManager._parse_plan("") == []
    assert llm.LLMManager._parse_plan("нет тут плана") == []
    # Модель повторила шаг — дубликат не нужен.
    assert llm.LLMManager._parse_plan('{"steps": ["а", "а", "б"]}') == ["а", "б"]


# ── Ожидание решения: срок и уборка ───────────────────────────────


def test_apprentice_timeout_marks_step_and_returns(db_session, monkeypatch):
    """Бесконечного ожидания больше нет: прогон освобождает слот и завершается честно."""
    import agent.llm_manager as llm

    monkeypatch.setattr(perm, "WAIT_TIMEOUT_SECONDS", 0.0)
    manager = llm.LLMManager.__new__(llm.LLMManager)  # без инициализации MCP/реестра

    result = asyncio.run(
        manager.execute_apprentice_step("sess-timeout", "write_file", {"relative_path": "a.py"}, "проверка", None)
    )
    assert result["decision"] == "timeout"

    db = db_session()
    try:
        row = (
            db.query(models.ApprenticeStep)
            .filter(models.ApprenticeStep.session_id == "sess-timeout")
            .one()
        )
        assert row.human_decision == "timeout"
        assert row.decided_at is not None
    finally:
        db.close()


def test_new_step_expires_orphaned_ones(db_session, monkeypatch):
    """При рестарте воркера старый шаг не должен остаться висеть в UI навсегда."""
    import agent.llm_manager as llm

    db = db_session()
    try:
        db.add(models.ApprenticeStep(
            session_id="sess-orphan",
            proposed_tool="list_dir",
            proposed_args='{"relative_path": "."}',
            proposed_reasoning="старый шаг из убитой попытки",
            human_decision=None,
        ))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(perm, "WAIT_TIMEOUT_SECONDS", 0.0)
    manager = llm.LLMManager.__new__(llm.LLMManager)
    result = asyncio.run(
        manager.execute_apprentice_step("sess-orphan", "read_file", {"relative_path": "README.md"}, "новый шаг", None)
    )
    assert result["decision"] == "timeout"

    db = db_session()
    try:
        rows = (
            db.query(models.ApprenticeStep)
            .filter(models.ApprenticeStep.session_id == "sess-orphan")
            .order_by(models.ApprenticeStep.created_at)
            .all()
        )
        assert len(rows) == 2
        assert rows[0].human_decision == "expired"   # старый — погашен
        assert rows[1].human_decision == "timeout"   # новый — свой исход
    finally:
        db.close()
