import os
import asyncio
import logging
from datetime import datetime, timedelta
from arq.connections import RedisSettings
from arq.worker import Worker
from sqlalchemy.orm import Session

from database import SessionLocal
from agent.llm_manager import LLMManager
from models import AgentTask, AgentTaskEvent, ActionRequest, ApprenticeStep

# ── Логирование ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("contextus.worker")

# Воркер тоже зовёт Telegram (эскалация от 14B-надсмотрщика), а httpx печатает URL целиком —
# вместе с токеном бота в пути. Держим только предупреждения и ошибки.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

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


def expire_pending_apprentice_steps(db: Session, session_ids: list[str | None]) -> int:
    """
    Гасит висящие подтверждения (`human_decision IS NULL`) для указанных сессий.

    Нужно потому, что ожидание решения живёт ВНУТРИ прогона: если воркер перезапустили
    или ARQ сделал ретрай, ожидающего уже нет, а строка в БД остаётся и продолжает
    показываться пользователю как «ожидает решения». Возвращает число погашенных.
    """
    ids = [s for s in session_ids if s]
    if not ids:
        return 0
    rows = db.query(ApprenticeStep).filter(
        ApprenticeStep.session_id.in_(ids),
        ApprenticeStep.human_decision == None,  # noqa: E711 — SQL IS NULL
    ).all()
    now = datetime.utcnow()
    for row in rows:
        row.human_decision = "expired"
        row.decided_at = now
    return len(rows)

async def run_agent_task(ctx, task_id: str, query: str, chat_id: int, history: list, accounts: list, source_ids: list, attached_folders: list, target_agent: str = "auto", mode: str = "auto", complexity: str = "auto"):
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

    # 1.1. Гасим осиротевшие apprentice-шаги прошлой попытки.
    # ARQ ретраит задачу после рестарта воркера (`try=2`): агент стартует заново,
    # а карточки подтверждения от убитой попытки остаются с human_decision = NULL
    # навсегда и продолжают висеть в UI (на разборе инцидента их было 4, два — с июля).
    try:
        db = SessionLocal()
        try:
            expired = expire_pending_apprentice_steps(db, [str(task_id), str(chat_id) if chat_id else None])
            if expired:
                db.commit()
                logger.info(f"Погашено осиротевших apprentice-шагов прошлой попытки: {expired}")
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — уборка не имеет права сорвать прогон
        logger.warning(f"Не удалось погасить осиротевшие apprentice-шаги: {e}")

    try:
        # 2. Выполняем стрим. Воркер перехватывает yield и пишет в БД/Redis.
        import redis.asyncio as aioredis
        import json

        from services import artifacts as artifacts_service
        
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
            mode=mode,
            complexity=complexity
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

            # Журнал прогона — то, «что система знает о себе»: агрегат для самоанализа
            # (паттерны неудач/удач) и для панели артефактов. Метрики и ошибки берёт
            # finalize_journal из execution_traces, а не выдумываются здесь.
            artifacts_service.finalize_journal(db, task_id, status="success")
            
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
            # Ожидающего больше нет — карточка подтверждения не должна пережить прогон.
            try:
                expire_pending_apprentice_steps(db, [task_id, task.conversation_id])
                db.commit()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Не удалось погасить apprentice-шаги при отмене {task_id}: {e}")
            # Отмена — тоже исход: без строки в журнале прогон «исчезает» из истории
            artifacts_service.finalize_journal(
                db, task_id, status="cancelled", summary="прогон отменён (cancel/перезапуск сервиса)"
            )
        db.close()
        raise
    except Exception as e:
        logger.error(f"Внутренняя ошибка воркера для задачи {task_id}: {str(e)}")
        failed_task_type = None
        db = SessionLocal()
        task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
        if task:
            # task_type читаем ДО commit: после него атрибуты истекают (expire_on_commit),
            # а объект после db.close() уже detached — доступ к полю дал бы DetachedInstanceError.
            failed_task_type = task.task_type
            task.status = "failed"
            db.commit()
            # Ошибка обязана остаться в истории: раньше она жила только в логе контейнера,
            # который стирается при пересоздании (инцидент 26.09 — «запрос испарился»).
            artifacts_service.finalize_journal(
                db,
                task_id,
                status="failed",
                errors=[f"{type(e).__name__}: {e}"],
            )
        db.close()

        # LLM-ревизия упавшего прогона — сохраняется в журнал (`self_review.llm_review`).
        # Раньше аналитик вызывался только из Telegram-пути и результат выбрасывался.
        # Трейсы web-прогона пишутся с `session_id == task_id` (см. execute_stream),
        # поэтому ревизор читает этот прогон по task_id. Кулдаун и фон — как в execute().
        try:
            tt = failed_task_type or "unknown"
            if llm_manager._should_trigger_analyst(tt):
                llm_manager._mark_analyst_run(tt)
                await llm_manager._analyst_and_store(session_id=task_id, task_id=task_id)
        except Exception as analyst_err:  # noqa: BLE001 — фон не имеет права уронить воркер
            logger.error(f"Meta-Analyst (worker) не отработал для {task_id}: {analyst_err}")

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

        # Просроченные подтверждения гасим независимо от статуса задачи: ожидание живёт
        # внутри прогона, и если прогон умер (рестарт/ретрай), ждать уже некому. Срок
        # ожидания внутри execute_apprentice_step — 15 мин, поэтому 30 мин здесь заведомо
        # означает «осиротевший шаг», а не «человек думает».
        stale_steps = db.query(ApprenticeStep).filter(
            ApprenticeStep.human_decision == None,  # noqa: E711 — SQL IS NULL
            ApprenticeStep.created_at < timeout_threshold,
        ).all()
        now = datetime.utcnow()
        for step in stale_steps:
            step.human_decision = "expired"
            step.decided_at = now
        if stale_steps:
            logger.info(f"Погашено просроченных apprentice-шагов: {len(stale_steps)}")

        db.commit()
    except Exception as e:
        logger.error(f"Ошибка в cron-задаче cancel_stuck_hitl_tasks: {e}")
    finally:
        db.close()


async def cleanup_stale_sandboxes(ctx):
    """
    ТЗ 4.7: удаляет устаревшие git-worktree песочницы кодера.

    Не трогает песочницы, чьи задачи ещё живы (ActionRequest в ожидании решения Gate),
    и те, что моложе TTL. TTL настраивается через SANDBOX_TTL_HOURS (по умолчанию 24).
    """
    try:
        ttl_hours = float(os.getenv("SANDBOX_TTL_HOURS", "24"))
    except ValueError:
        ttl_hours = 24.0

    from services import sandbox as sandbox_service

    active_task_ids: set[str] = set()
    db = SessionLocal()
    try:
        rows = db.query(ActionRequest).filter(
            ActionRequest.status.in_(("pending_supervisor", "pending_friend_call", "approved"))
        ).all()
        active_task_ids = {r.coder_task_id for r in rows if r.coder_task_id}
    except Exception as e:
        logger.error(f"Sandbox cleanup: не удалось получить активные задачи ({e}) — пропускаю удаление")
        db.close()
        return
    finally:
        db.close()

    removed: list[str] = []
    kept: list[str] = []
    for entry in sandbox_service.list_sandboxes():
        task_id = entry["task_id"]
        age = entry["age_hours"]
        if task_id in active_task_ids:
            kept.append(f"{task_id}(active)")
            continue
        if age < ttl_hours:
            kept.append(f"{task_id}({age:.1f}h)")
            continue
        sandbox_service.remove_sandbox(task_id)
        removed.append(task_id)

    logger.info(
        f"🧹 Sandbox cleanup: удалено {len(removed)} {removed or ''}, "
        f"оставлено {len(kept)} {kept or ''} (TTL={ttl_hours}h)"
    )

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
    cron_jobs = [
        cron(cancel_stuck_hitl_tasks, minute=set(range(0, 60, 5))),
        cron(cleanup_stale_sandboxes, minute=set(range(0, 60, 30))),
    ]
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
