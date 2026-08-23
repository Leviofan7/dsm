import os
import asyncio
import logging
from arq.connections import RedisSettings
from arq.worker import Worker
from sqlalchemy.orm import Session

from database import SessionLocal
from agent.llm_manager import LLMManager
from models import AgentTask, AgentTaskEvent, ActionRequest

# ── Логирование ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("contextus.worker")

# Глобальный LLMManager для воркера
worker_llm_manager = LLMManager()

async def startup(ctx):
    """Инициализация ресурсов при старте воркера."""
    logger.info("Starting up ARQ worker...")
    await worker_llm_manager.initialize()
    ctx['llm_manager'] = worker_llm_manager

async def shutdown(ctx):
    """Очистка ресурсов при остановке воркера."""
    logger.info("Shutting down ARQ worker...")
    if 'llm_manager' in ctx:
        await ctx['llm_manager'].shutdown()

async def run_agent_task(ctx, task_id: str, query: str, chat_id: int, history: list, accounts: list, source_ids: list, attached_folders: list, target_agent: str = "auto", mode: str = "auto"):
    """Главная задача ARQ: запуск агента и обработка его цикла."""
    logger.info(f"Начало выполнения задачи {task_id} для chat_id={chat_id}")
    llm_manager: LLMManager = ctx['llm_manager']
    
    # 1. Загружаем или создаем сессию для AgentTask
    db: Session = SessionLocal()
    try:
        task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
        if not task:
            logger.error(f"Задача {task_id} не найдена в БД!")
            return
        
        # Блокировка (FOR UPDATE SKIP LOCKED) будет реализована позже при подхвате "упавших" задач.
        # Для свежих задач просто переводим статус в running
        task.status = "running"
        db.commit()
    except Exception as e:
        logger.error(f"Ошибка при работе с БД для задачи {task_id}: {e}")
        db.close()
        return
    finally:
        db.close()

    try:
        # 2. Выполняем стрим. Воркер перехватывает yield и пишет в БД/Redis.
        import redis.asyncio as aioredis
        import json
        
        redis_pubsub = aioredis.from_url(redis_url)
        seq = 0
        
        async for event in llm_manager.execute_stream(
            task_id=task_id,
            query=query,
            accounts=accounts,
            history=history,
            allow_browser=True,
            debug_mode=False,
            source_ids=source_ids,
            attached_folders=attached_folders,
            target_agent=target_agent,
            mode=mode
        ):
            db = SessionLocal()
            try:
                task_event = AgentTaskEvent(
                    task_id=task_id,
                    sequence_number=seq,
                    event_type="stream",
                    payload=event
                )
                db.add(task_event)
                db.commit()
            finally:
                db.close()
            
            # Отправка в Redis Pub/Sub для трансляции в реальном времени
            await redis_pubsub.publish(f"task:{task_id}", event)
            seq += 1
            
        # 3. Успешное завершение
        db = SessionLocal()
        task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
        if task and task.status == "running":
            task.status = "completed"
            
            # 3.1. Сохраняем событие финального ответа
            final_payload = json.dumps({
                "type": "final_answer",
                "task_id": task_id,
                "status": "completed"
            }, ensure_ascii=False)
            final_sse = f"data: {final_payload}\n\n"
            
            final_answer_event = AgentTaskEvent(
                task_id=task_id,
                sequence_number=seq,
                event_type="final_answer",
                payload=final_sse
            )
            db.add(final_answer_event)
            db.commit()
            await redis_pubsub.publish(f"task:{task_id}", final_sse)
            
            seq += 1
            
            # 3.2. Уведомляем клиентов о завершении
            completion_event = 'data: {"type": "status", "status": "completed"}\n\n'
            task_event = AgentTaskEvent(
                task_id=task_id, 
                sequence_number=seq, 
                event_type="status", 
                payload=completion_event
            )
            db.add(task_event)
            db.commit()
            await redis_pubsub.publish(f"task:{task_id}", completion_event)
            
        db.close()
        await redis_pubsub.close()
        logger.info(f"Задача {task_id} успешно завершена")
        
    except asyncio.CancelledError:
        logger.warning(f"Задача {task_id} была отменена (CancelledError)")
        db = SessionLocal()
        task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
        if task:
            task.status = "cancelled"
            db.commit()
        db.close()
        raise
    except Exception as e:
        logger.error(f"Внутренняя ошибка воркера для задачи {task_id}: {str(e)}")
        db = SessionLocal()
        task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
        if task:
            task.status = "failed"
            db.commit()
        db.close()

from arq import cron
from datetime import datetime, timedelta

async def cancel_stuck_hitl_tasks(ctx):
    """Cron задача для отмены зависших HITL задач."""
    db = SessionLocal()
    try:
        timeout_threshold = datetime.utcnow() - timedelta(minutes=30)
        stuck_tasks = db.query(AgentTask).filter(
            AgentTask.status == "paused", 
            AgentTask.updated_at < timeout_threshold
        ).all()
        
        for task in stuck_tasks:
            task.status = "cancelled"
            logger.info(f"Задача {task.id} отменена по TTL (HITL timeout)")
        
        db.commit()
    except Exception as e:
        logger.error(f"Ошибка в cron-задаче cancel_stuck_hitl_tasks: {e}")
    finally:
        db.close()

# ARQ конфигурация
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")


async def run_supervisor_review(ctx, action_request_id: str):
    """
    Apprentice-Gate 2.0: ARQ-задача для Надсмотрщика (14B).
    Запускается автоматически при создании нового ActionRequest.
    Загружает модель supervision и просит её принять решение: approve/escalate/reject.
    """
    import json
    llm_manager: LLMManager = ctx['llm_manager']
    logger.info(f"Supervisor review: {action_request_id}")

    db = SessionLocal()
    try:
        req = db.query(ActionRequest).filter(ActionRequest.id == action_request_id).first()
        if not req:
            logger.warning(f"ActionRequest {action_request_id} не найден")
            return
        if req.status != "pending_supervisor":
            logger.info(f"ActionRequest {action_request_id} уже обработан: {req.status}")
            return
        payload = json.loads(req.payload) if req.payload else {}
        action_type = req.action_type
    finally:
        db.close()

    model = llm_manager.registry.resolve_model("supervision")
    if not model:
        logger.error("Нет доступной модели для supervision, эскалируем")
        from api.action_requests import mcp_escalate_to_friend_call
        await mcp_escalate_to_friend_call(action_request_id, "Нет модели для автоматического ревью")
        return

    # Читаем промпт из файла роли, чтобы логика была в одном месте
    import yaml
    from pathlib import Path
    role_path = Path(__file__).parent / "roles" / "supervisor_14b.yaml"
    try:
        with open(role_path, "r", encoding="utf-8") as f:
            role_data = yaml.safe_load(f)
            system_instruction = role_data.get("system_instruction", "")
    except Exception as e:
        logger.error(f"Не удалось загрузить роль supervisor_14b: {e}")
        system_instruction = ""

    system_prompt = (
        f"{system_instruction}\n\n"
        "ВАЖНО: Верни ТОЛЬКО JSON: {decision: 'approve'|'escalate'|'reject', reason: '...'}\n"
        "Никакого текста кроме JSON быть не должно."
    )
    user_msg = f"action_type: {action_type}\npayload: {json.dumps(payload, ensure_ascii=False)}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    try:
        resp = await llm_manager._chat(model, messages)
        text = llm_manager._extract_text(model, resp) if resp else "{}"
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(text)
        decision = parsed.get("decision", "escalate")
        reason = parsed.get("reason", "")
    except Exception as e:
        logger.error(f"Supervisor ошибка: {e}, эскалируем")
        decision = "escalate"
        reason = f"Ошибка Supervisor: {str(e)}"

    logger.info(f"Supervisor решение: {decision} | {reason[:80]}")

    from api.action_requests import mcp_approve_action, mcp_escalate_to_friend_call, mcp_reject_action
    if decision == "approve":
        await mcp_approve_action(action_request_id)
    elif decision == "reject":
        await mcp_reject_action(action_request_id, reason)
    else:
        await mcp_escalate_to_friend_call(action_request_id, reason)


class WorkerSettings:
    functions = [run_agent_task, run_supervisor_review]
    cron_jobs = [cron(cancel_stuck_hitl_tasks, minute=set(range(0, 60, 5)))]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(redis_url)
    max_jobs = 10
    job_timeout = 3600 # 1 час таймаут на 1 задачу (агенты работают долго)

if __name__ == "__main__":
    import arq
    
    settings = WorkerSettings()
    
    # Создаём Worker и запускаем его
    async def worker():
        worker = arq.Worker(**settings.__dict__)
        await worker
    
    import asyncio
    asyncio.run(worker())
