"""
Telegram-прогон обязан быть задачей, а не «побочным эффектом».

Инцидент: Telegram шёл мимо задач — результаты нельзя было привязать к прогону
(MCP-публикация артефактов отказывает «без id прогона»), а журнал собрать не из чего.
Путь Telegram целиком выпадал из истории и самоанализа.

Стражи:
  1) сообщение из TG создаёт AgentTask, привязанную к беседе этого чата;
  2) execute() получает id ИМЕННО этой задачи (иначе артефакт публиковать некуда);
  3) успех: задача → completed, журнал → success;
  4) падение: задача → failed и в журнале остаётся запись об ошибке.
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

import database
from database import Base
import main
import models

engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
)


@pytest.fixture
def tg(monkeypatch):
    """Тестовая БД + подмена SessionLocal + перехват отправки в Telegram."""
    Base.metadata.create_all(bind=engine)

    orig_main_sl, orig_db_sl = main.SessionLocal, database.SessionLocal
    main.SessionLocal = TestingSessionLocal
    database.SessionLocal = TestingSessionLocal

    sent: list = []

    async def fake_send(chat_id, text):
        sent.append((chat_id, text))

    monkeypatch.setattr(main, "send_telegram_message", fake_send)
    # Живой auto_switch пишет конфиг Claude — тесту он не нужен.
    monkeypatch.setattr(main, "auto_switch_by_complexity", lambda *a, **k: None)

    async def fake_route_intent(text):
        return {"task_type": "research", "complexity": "low", "role": "default"}

    monkeypatch.setattr(main.llm_manager, "route_intent", fake_route_intent)

    yield sent

    main.SessionLocal, database.SessionLocal = orig_main_sl, orig_db_sl
    Base.metadata.drop_all(bind=engine)


def _install_execute(monkeypatch, *, answer="готово", boom=None):
    """Стаб инференса: запоминает переданный task_id и (опционально) падает."""
    seen: dict = {}

    async def fake_execute(query, task_type="general", system_prompt="",
                           history=None, chat_id=None, task_id=None):
        seen["task_id"] = task_id
        seen["query"] = query
        if boom:
            raise boom
        return answer

    monkeypatch.setattr(main.llm_manager, "execute", fake_execute)
    return seen


def test_telegram_run_creates_task_and_journal(tg, monkeypatch):
    seen = _install_execute(monkeypatch)
    chat_id = 424242
    # Пользователь должен существовать до конвейера: иначе сработает проверка доступа.
    main.get_or_create_tg_user_and_conversation(str(chat_id))

    asyncio.run(main.process_telegram_message(chat_id, "собери новости"))

    db = TestingSessionLocal()
    try:
        tasks = (
            db.query(models.AgentTask)
            .filter(models.AgentTask.owner_id == str(chat_id))
            .all()
        )
        assert len(tasks) == 1, "сообщение из TG обязано создать ровно одну задачу"
        task = tasks[0]
        assert task.status == "completed"
        assert task.conversation_id, "без привязки к беседе UI не покажет прогон"
        assert seen["task_id"] == task.id, "execute() должен получить id именно этой задачи"

        journal = (
            db.query(models.RunJournal).filter(models.RunJournal.task_id == task.id).first()
        )
        assert journal is not None, "успешный прогон обязан оставить журнал"
        assert journal.status == "success"
    finally:
        db.close()

    assert tg, "ответ обязан уйти в Telegram"


def test_telegram_run_failure_is_recorded(tg, monkeypatch):
    _install_execute(monkeypatch, boom=RuntimeError("модель недоступна"))
    chat_id = 555001
    main.get_or_create_tg_user_and_conversation(str(chat_id))

    with pytest.raises(RuntimeError):
        asyncio.run(main.process_telegram_message(chat_id, "запусти что-нибудь"))

    db = TestingSessionLocal()
    try:
        task = (
            db.query(models.AgentTask)
            .filter(models.AgentTask.owner_id == str(chat_id))
            .one()
        )
        assert task.status == "failed"
        journal = (
            db.query(models.RunJournal).filter(models.RunJournal.task_id == task.id).first()
        )
        assert journal is not None and journal.status == "failed"
        assert journal.errors and "RuntimeError" in journal.errors
    finally:
        db.close()
