"""
Данные для браузерной проверки: прогон с планом/журналом/самоанализом + предложенный сценарий.

Запуск:
  python /app/scratch/seed_ui_scenario_probe.py          # посеять
  python /app/scratch/seed_ui_scenario_probe.py --clean   # убрать за собой

Пишет в РЕАЛЬНУЮ БД намеренно: проверяем панель артефактов живьём через :3000.
Маркер — «UI-проба» в названии беседы и owner_id='probe-ui'.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import models
from services import artifacts as artifacts_service

OWNER = "probe-ui"
CONV_TITLE = "UI-проба: сценарий и самоанализ"


def cleanup() -> None:
    db = database.SessionLocal()
    try:
        convs = db.query(models.Conversation).filter(models.Conversation.title == CONV_TITLE).all()
        conv_ids = [c.id for c in convs]
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
            db.query(models.Artifact).filter(
                models.Artifact.task_id.in_(task_ids)
            ).delete(synchronize_session=False)
            db.query(models.ExecutionTrace).filter(
                models.ExecutionTrace.task_id.in_(task_ids)
            ).delete(synchronize_session=False)
            for t in db.query(models.AgentTask).filter(models.AgentTask.id.in_(task_ids)).all():
                db.delete(t)
        if conv_ids:
            db.query(models.Message).filter(
                models.Message.conversation_id.in_(conv_ids)
            ).delete(synchronize_session=False)
            db.query(models.Conversation).filter(
                models.Conversation.id.in_(conv_ids)
            ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def seed() -> None:
    db = database.SessionLocal()
    try:
        conv = models.Conversation(title=CONV_TITLE)
        db.add(conv)
        db.flush()

        task = models.AgentTask(
            owner_id=OWNER, status="completed", task_type="research",
            query="Собери новости по прилётам НПЗ и оформи отчёт", conversation_id=conv.id,
        )
        db.add(task)
        db.flush()

        # План (живёт в agent_subtasks)
        db.add(models.AgentSubtask(
            task_id=task.id, topic="Собрать источники", prompt_instruction="…",
            target_role="web_researcher", execution_order=0, status="completed",
            result_output="нашёл 3 источника",
        ))
        db.add(models.AgentSubtask(
            task_id=task.id, topic="Оформить отчёт", prompt_instruction="…",
            target_role="coder", execution_order=1, status="completed",
        ))

        # Фактические вызовы инструментов — из них строится предложение сценария
        seq = 0
        for tool in ("web_search", "goto_url"):
            for step_name in (f"tool_{tool}", f"tool_result_{tool}"):
                db.add(models.AgentTaskEvent(
                    task_id=task.id, sequence_number=seq, event_type="stream",
                    payload="data: " + json.dumps({"type": "step", "step": step_name}),
                ))
                seq += 1
        db.add(models.ExecutionTrace(
            session_id=task.id, task_id=task.id,
            task_type_classified="research", task_type_final="research",
            model_used="deepseek-chat", model_selected="deepseek-chat", planner_enabled=True,
            tools_available=3, tools_called=2,
            tools_called_names=json.dumps(["web_search", "goto_url"]),
            actions_log=json.dumps(["web_search", "goto_url"]),
            stage_durations=json.dumps({"executor": 4200}), duration_ms=4200,
            final_status="success",
        ))
        db.commit()

        artifacts_service.publish(
            db, task_id=task.id, kind="report", title="Отчёт по прогону",
            content="# Итог проверки\n\nПанель показывает план, артефакты, журнал и самоанализ.",
        )
        artifacts_service.finalize_journal(db, task.id, status="success")
        print(f"@@ беседа для UI: {conv.id}")
        print(f"@@ задача: {task.id}")
    finally:
        db.close()


if __name__ == "__main__":
    if "--clean" in sys.argv:
        cleanup()
        print("@@ убрано")
    else:
        cleanup()
        seed()
