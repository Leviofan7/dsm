"""
Проба: сообщение из Telegram становится ЗАДАЧЕЙ с журналом, а не «побочным эффектом».

Повод: TG-путь (`process_telegram_message`) шёл мимо `agent_tasks` — артефакты из TG
публиковать было некуда (MCP-публикация отказывает «без id прогона»), а журнал собрать
не из чего. Проба проверяет это на РЕАЛЬНОЙ БД, подменяя ТОЛЬКО две вещи:
  * инференс (`llm_manager.route_intent`/`execute`) — в сеть/LLM не ходим, денег не тратим;
  * отправку в Telegram (`send_telegram_message`) — слать владельцу проба не имеет права.

Запуск: docker compose exec -T fastapi_backend python -B /app/scratch/probe_telegram_task.py
Пишет реальные строки и УБИРАЕТ их за собой (как probe_artifacts_mcp).
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import main
import models

#: Явно «пробный» id — не пересекается с реальными telegram_chat_id.
CHAT_ID = "99000123"


async def _fake_route_intent(text):
    return {"task_type": "research", "complexity": "low", "role": "default"}


_seen = {"task_id": None}


async def _fake_execute(query, task_type="general", system_prompt="",
                        history=None, chat_id=None, task_id=None):
    if not task_id:
        raise AssertionError("execute() без task_id — артефакт публиковать некуда")
    _seen["task_id"] = task_id
    return "проба: ответ без выхода в сеть"


_sent = []


async def _fake_send(chat_id, text):
    _sent.append((chat_id, text))  # владельцу НЕ отправляем


def _cleanup():
    """Убирает за собой ВСЁ, что проба могла создать (FK включаем раньше — порядок важен)."""
    db = database.SessionLocal()
    try:
        task_ids = [
            t.id for t in db.query(models.AgentTask).filter(models.AgentTask.owner_id == CHAT_ID)
        ]
        conv_ids = [
            c.id for c in db.query(models.Conversation)
            .filter(models.Conversation.telegram_chat_id == CHAT_ID)
        ]
        if task_ids:
            db.query(models.RunJournal).filter(models.RunJournal.task_id.in_(task_ids)).delete(synchronize_session=False)
            db.query(models.Artifact).filter(models.Artifact.task_id.in_(task_ids)).delete(synchronize_session=False)
            for t in db.query(models.AgentTask).filter(models.AgentTask.id.in_(task_ids)).all():
                db.delete(t)  # ORM-каскад снимет events/subtasks
        if conv_ids:
            db.query(models.Message).filter(models.Message.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
            db.query(models.Conversation).filter(models.Conversation.id.in_(conv_ids)).delete(synchronize_session=False)
        db.query(models.User).filter(models.User.telegram_chat_id == CHAT_ID).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


async def _probe():
    main.llm_manager.route_intent = _fake_route_intent
    main.llm_manager.execute = _fake_execute
    main.send_telegram_message = _fake_send
    main.auto_switch_by_complexity = lambda *a, **k: None

    _cleanup()  # на случай неудачного прошлого прогона
    main.get_or_create_tg_user_and_conversation(CHAT_ID)

    await main.process_telegram_message(int(CHAT_ID), "проба: собери новости")

    db = database.SessionLocal()
    try:
        tasks = db.query(models.AgentTask).filter(models.AgentTask.owner_id == CHAT_ID).all()
        print(f"@@ задач создано: {len(tasks)}")
        assert len(tasks) == 1, "TG-сообщение обязано создать ровно одну задачу"
        task = tasks[0]
        print(f"@@ task.status={task.status} | conversation_id={'есть' if task.conversation_id else 'НЕТ'} "
              f"| task_type={task.task_type}")
        assert task.status == "completed", task.status
        assert task.conversation_id, "без привязки к беседе UI прогон не покажет"
        assert _seen["task_id"] == task.id, "execute() получил не тот task_id"

        journal = db.query(models.RunJournal).filter(models.RunJournal.task_id == task.id).first()
        print(f"@@ журнал: {'есть' if journal else 'НЕТ'} | status={journal.status if journal else None}")
        assert journal is not None and journal.status == "success"
        print(f"@@ ответ перехвачен до отправки в TG: {bool(_sent)}")
        assert _sent, "ответ обязан быть готов к отправке"
    finally:
        db.close()

    _cleanup()
    print("@@ RESULT: telegram_run_creates_task_and_journal = True")


asyncio.run(_probe())
