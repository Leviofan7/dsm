import asyncio
import json
import uuid
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Request, BackgroundTasks, Depends, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session


from database import get_db, SessionLocal
from models import Source, Chunk, Conversation, Message, conversation_sources
from models import ActionRequest
from services.indexer import index_github_repo, _process_files_and_index, reindex_source, auto_reindex_if_needed
from services.vector_store import delete_source_collection
import httpx

from agent.llm_manager import LLMManager
from agent.claude_settings import auto_switch_by_complexity
from agent.session_state import session_manager, SessionState

from arq import create_pool
from arq.connections import RedisSettings
from models import AgentTask, AgentTaskEvent
import redis.asyncio as aioredis

# ── Логирование ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ── Глобальный LLMManager ─────────────────────────────────────────
llm_manager = LLMManager()

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle: при старте — инициализируем LLMManager, при остановке — shutdown."""
    app.state.redis = await create_pool(RedisSettings.from_dsn(redis_url))
    app.state.redis_pubsub = aioredis.from_url(redis_url)
    
    await llm_manager.initialize()
    # Запускаем фоновую очистку сессий и поллер статусов HITL
    asyncio.create_task(hitl_monitor_loop())
    yield
    await llm_manager.shutdown()
    app.state.redis.close()
    await app.state.redis.wait_closed()
    await app.state.redis_pubsub.close()


app = FastAPI(lifespan=lifespan)

# ── Роутеры ───────────────────────────────────────────────────────
from api.action_requests import router as action_requests_router
app.include_router(action_requests_router)

# Telegram Security
ALLOWED_TELEGRAM_USER_ID = os.getenv("ALLOWED_TELEGRAM_USER_ID", "")

# Этот секрет должен совпадать с WORKER_SECRET в файле app/api/chat/route.ts
WORKER_SECRET = "default_secret"

async def safe_agent_stream(query: str, accounts: list, history: list, allow_browser: bool, debug_mode: bool = False, source_ids: list[str] = None, attached_folders: list[str] = None):
    """
    Обертка над run_agent_loop для перехвата любых необработанных исключений генератора
    и отправки их клиенту в формате SSE.
    """
    try:
        async for event in llm_manager.execute_stream(
            query=query, 
            accounts=accounts, 
            history=history, 
            allow_browser=allow_browser, 
            debug_mode=debug_mode, 
            source_ids=source_ids,
            attached_folders=attached_folders
        ):
            yield event
    except Exception as e:
        error_event = json.dumps({"type": "error", "message": f"Внутренняя ошибка стриминга: {str(e)}"})
        yield f"data: {error_event}\n\n"

@app.post("/agent/run")
async def run_agent(request: Request, authorization: str = Header(None), db: Session = Depends(get_db)):
    # 1. Проверка Bearer токена
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized: Missing Bearer token")
    
    token = authorization.split(" ")[1]
    if token != WORKER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid token")

    # 2. Получение данных
    body = await request.json()
    query = body.get("query", "Пустой запрос")
    accounts = body.get("accounts", [])
    history = body.get("history", [])
    allow_browser = body.get("allow_browser", True)
    debug_mode = body.get("debug_mode", False)
    source_ids = body.get("source_ids", [])
    chat_id = body.get("chat_id", 0) # Optionally passed from frontend if needed
    target_agent = body.get("target_agent", "auto")
    mode = body.get("mode", "auto")
    
    attached_folders = []
    if source_ids:
        sources = db.query(Source).filter(Source.id.in_(source_ids), Source.type == "local").all()
        attached_folders = [s.detail for s in sources if s.detail]

    # 3. Создаем задачу в базе данных
    task = AgentTask(
        owner_id=str(chat_id),
        status="pending",
        task_type="agent_run",
        query=query,
        conversation_id=str(chat_id) if chat_id else None
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    print(f"[*] Поставлена задача в очередь: {task.id} | Query: {query}")

    # 4. Отправляем в ARQ
    await request.app.state.redis.enqueue_job(
        "run_agent_task",
        task.id,
        query,
        chat_id,
        history,
        accounts,
        source_ids,
        attached_folders,
        target_agent,
        mode,
        _job_id=task.id
    )

    return {"task_id": task.id, "status": task.status}

@app.post("/agent/task/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request, authorization: str = Header(None)):
    db = next(get_db())
    try:
        task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
            
        task.status = "cancelled"
        db.commit()
        
        # Запрашиваем отмену у ARQ
        # arq.abort_job internally sets a redis key "arq:abort:{job_id}"
        from arq.jobs import Job
        job = Job(job_id=task_id, redis=request.app.state.redis)
        await job.abort()
        
        # Отправляем уведомление
        pubsub = request.app.state.redis_pubsub
        await pubsub.publish(f"task:{task_id}", 'data: {"type": "status", "status": "cancelled"}\n\n')
        
        return {"status": "cancelled"}
    finally:
        db.close()

async def event_stream_generator(task_id: str, request: Request, last_seq: int = -1):
    """Читает историю из БД, затем слушает Redis Pub/Sub."""
    db = next(get_db())
    try:
        # Выдаем старые события (replay)
        events = db.query(AgentTaskEvent).filter(
            AgentTaskEvent.task_id == task_id,
            AgentTaskEvent.sequence_number > last_seq
        ).order_by(AgentTaskEvent.sequence_number).all()
        
        for ev in events:
            last_seq = ev.sequence_number
            # payload уже содержит "data: {...}\n\n"
            yield ev.payload
            
        task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
        if task and task.status in ["completed", "failed", "cancelled"]:
            # Если задача уже завершена, мы выдали все из БД и можем закрывать стрим
            yield f'data: {{"type": "status", "status": "{task.status}"}}\n\n'
            return
            
    finally:
        db.close()

    # Подписываемся на новые события через Redis Pub/Sub
    pubsub = request.app.state.redis_pubsub.pubsub()
    channel_name = f"task:{task_id}"
    await pubsub.subscribe(channel_name)
    import time
    last_ping_time = time.time()
    try:
        while True:
            # Если клиент отключился
            if await request.is_disconnected():
                break
                
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message:
                last_ping_time = time.time()
                data = message["data"].decode("utf-8")
                # data уже содержит "data: {...}\n\n"
                yield data
                
                # Если пришло финальное сообщение
                try:
                    # Убираем префикс "data: " для парсинга
                    clean_data = data
                    if clean_data.startswith("data: "):
                        clean_data = clean_data[6:].strip()
                    parsed = json.loads(clean_data)
                    if parsed.get("type") == "status" and parsed.get("status") in ["completed", "failed", "cancelled"]:
                        break
                except json.JSONDecodeError:
                    pass
            else:
                # Keep-alive ping
                if time.time() - last_ping_time > 15:
                    yield ": ping\n\n"
                    last_ping_time = time.time()
    finally:
        await pubsub.unsubscribe(channel_name)

@app.get("/agent/task/{task_id}/stream")
async def task_stream(task_id: str, request: Request, last_seq: int = -1, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ")[1]
    if token != WORKER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    return StreamingResponse(
        event_stream_generator(task_id, request, last_seq),
        media_type="text/event-stream"
    )

# --- TELEGRAM INTEGRATION ---

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
telegram_histories: dict[int, list] = {}

async def send_telegram_message(chat_id: int, text: str):
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN is not set, cannot send message")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Telegram API max message length is 4096 characters.
    max_len = 4000
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]
    
    async with httpx.AsyncClient() as client:
        for chunk in chunks:
            try:
                resp = await client.post(url, json={"chat_id": chat_id, "text": chunk})
                if resp.status_code != 200:
                    logging.error(f"Telegram API Error: {resp.status_code} - {resp.text}")
            except Exception as e:
                logging.error(f"Failed to send message to Telegram: {e}")

async def send_telegram_photo(chat_id: int, caption: str, photo_b64: str):
    """Отправляет скриншот в Telegram (полезно для HITL)."""
    if not TELEGRAM_BOT_TOKEN:
        return
    import base64
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        photo_bytes = base64.b64decode(photo_b64)
    except Exception:
        # Если не можем декодировать — шлем как текст
        await send_telegram_message(chat_id, f"⚠️ Не удалось загрузить скриншот.\n\n{caption}")
        return

    async with httpx.AsyncClient() as client:
        try:
            files = {"photo": ("screenshot.png", photo_bytes, "image/png")}
            data = {"chat_id": chat_id, "caption": caption}
            await client.post(url, data=data, files=files)
        except Exception as e:
            logging.error(f"Failed to send photo to Telegram: {e}")

async def hitl_monitor_loop():
    """Фоновый поллер для уведомления пользователей о замороженных сессиях."""
    notified_sessions = set()
    while True:
        try:
            session_manager.cleanup_stale(max_age_seconds=1800) # 30 min
            for chat_id, session in list(session_manager._sessions.items()):
                if session.state == SessionState.WAITING_FOR_HUMAN and session.session_id not in notified_sessions:
                    notified_sessions.add(session.session_id)
                    msg = (
                        f"⚠️ **Сессия заморожена (Требуется вмешательство)**\n\n"
                        f"**Причина:** {session.hitl_reason}\n\n"
                        f"Напишите ответ или команду для обхода (например, 'Продолжай', 'Кликни 12', 'Я решил капчу')."
                    )
                    if session.screenshot_b64:
                        await send_telegram_photo(chat_id, msg, session.screenshot_b64)
                    else:
                        await send_telegram_message(chat_id, msg)
        except Exception as e:
            logging.error(f"HITL Monitor Error: {e}")
        await asyncio.sleep(5)

def load_role_instruction(role_name: str) -> str:
    if not role_name or role_name == "default":
        return ""
    try:
        roles_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roles")
        file_path = os.path.join(roles_dir, f"{role_name}.yaml")
        if os.path.exists(file_path):
            import yaml
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data.get("system_instruction", "")
    except Exception as e:
        logging.error(f"Error loading role {role_name}: {e}")
    return ""

async def process_telegram_message(chat_id: int, text: str):
    """Фоновая задача обработки сообщений из Telegram с полным pipeline."""
    tg_logger = logging.getLogger("contextus.telegram")
    tg_logger.info(f"📨 Получено сообщение от {chat_id}: {text[:80]}")

    # Проверяем, есть ли сессия в ожидании ответа человека (HITL)
    session = session_manager.get_waiting_session(chat_id)
    if session:
        tg_logger.info(f"👤 Ответ человека получен: {text}. Разблокировка сессии...")
        session.resume_from_human(text)
        await send_telegram_message(chat_id, "✅ Ответ принят, агент продолжает работу.")
        return

    # Запрещаем генерацию новых сессий, если предыдущая еще RUNNING
    active_session = session_manager.get_session(chat_id)
    if active_session and active_session.state == SessionState.RUNNING:
        await send_telegram_message(chat_id, "⏳ Пожалуйста, подождите. Я еще выполняю вашу предыдущую задачу...")
        return

    # 1. Doorman: классификация
    intent = await llm_manager.route_intent(text)
    task_type = intent.get("task_type", "general")
    complexity = intent.get("complexity", "low")
    role_name = intent.get("role", "default")
    tg_logger.info(f"🚪 Doorman: type={task_type}, complexity={complexity}, role={role_name}")

    # 2. Авто-переключение Claude Code (если complexity == high)
    auto_switch_by_complexity(complexity, task_type)

    # Загружаем инструкции роли
    role_instruction = load_role_instruction(role_name)
    
    # Базовый промпт
    system_prompt = "Ты — ИИ-агент Contextus. Отвечай на русском языке."
    if role_instruction:
        system_prompt += f"\n\nТвоя роль и инструкции:\n{role_instruction}"
    elif task_type == "browser_automation":
        system_prompt += (
            "\n\nТы работаешь через официальный MCP-сервер web-stealth. У тебя есть нативные инструменты: goto_url, get_dom_map и take_screenshot. "
            "Если после вызова `goto_url` сайт возвращает пустую страницу или защиту Cloudflare/капчу, запрещено использовать терминал (curl, wget или поиск скриптов). "
            "Вместо этого ты обязан использовать инструмент `take_screenshot`, чтобы проанализировать глазами визуальное состояние страницы, или делать `scroll`, чтобы стриггерить загрузку данных."
        )

    # 3. Полный инференс через LLMManager.execute() с tool calling
    history = telegram_histories.get(chat_id, [])
    
    answer = await llm_manager.execute(
        query=text,
        task_type=task_type,
        system_prompt=system_prompt,
        history=history,
        chat_id=chat_id,
    )
    tg_logger.info(f"✅ Ответ ({len(answer)} символов): {answer[:120]}…")
    
    # Сохраняем в историю (ограничиваем 10 сообщениями)
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": answer})
    telegram_histories[chat_id] = history[-10:]
    
    # Отправить answer обратно в Telegram через Bot API
    await send_telegram_message(chat_id, answer)


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()

    if "callback_query" in body:
        callback_query = body["callback_query"]
        chat_id = callback_query["message"]["chat"]["id"]
        data = callback_query.get("data", "")
        
        if ALLOWED_TELEGRAM_USER_ID and str(chat_id) != ALLOWED_TELEGRAM_USER_ID:
            raise HTTPException(status_code=403, detail="Forbidden")

        if data.startswith("ar_approve_"):
            req_id = data.replace("ar_approve_", "")
            try:
                from api.action_requests import approve
                from database import SessionLocal
                db = SessionLocal()
                try:
                    approve(req_id, db=db)
                    async def send_response():
                        import httpx
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        async with httpx.AsyncClient() as client:
                            await client.post(url, json={"chat_id": chat_id, "text": "✅ Разрешено!"})
                    background_tasks.add_task(send_response)
                finally:
                    db.close()
            except Exception as e:
                logging.error(f"Telegram ActionRequest approve error: {e}")

        elif data.startswith("ar_reject_"):
            req_id = data.replace("ar_reject_", "")
            try:
                from api.action_requests import reject, RejectBody
                from database import SessionLocal
                db = SessionLocal()
                try:
                    reject(req_id, body=RejectBody(reason="Отклонено через Telegram"), db=db)
                    async def send_response():
                        import httpx
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        async with httpx.AsyncClient() as client:
                            await client.post(url, json={"chat_id": chat_id, "text": "❌ Отклонено."})
                    background_tasks.add_task(send_response)
                finally:
                    db.close()
            except Exception as e:
                logging.error(f"Telegram ActionRequest reject error: {e}")
        
        # Обязательно отвечать на callback, чтобы кнопка перестала "крутиться"
        async def answer_callback():
            import httpx
            cb_id = callback_query["id"]
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
            async with httpx.AsyncClient() as client:
                await client.post(url, json={"callback_query_id": cb_id})
        background_tasks.add_task(answer_callback)
        return {"status": "ok"}

    message = body.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "")

    # Жесткая проверка: отсекаем чужие запросы
    if ALLOWED_TELEGRAM_USER_ID and str(chat_id) != ALLOWED_TELEGRAM_USER_ID:
        raise HTTPException(status_code=403, detail="Forbidden: You are not the owner.")

    if text:
        # Маршрутизируем задачу через BackgroundTasks, не вешая сервер
        background_tasks.add_task(process_telegram_message, chat_id, text)

    return {"status": "ok"}

# --- SOURCES API ---

@app.get("/sources")
def get_sources(db: Session = Depends(get_db)):
    sources = db.query(Source).order_by(Source.created_at.desc()).all()
    # Serialize for frontend
    result = []
    for s in sources:
        size_mb = f"{(s.size_bytes or 0) / 1024 / 1024:.1f} MB"
        result.append({
            "id": s.id,
            "name": s.name,
            "type": s.type,
            "detail": s.detail,
            "files": s.files_count or 0,
            "size": size_mb,
            "status": s.status,
            "updatedAt": s.updated_at.isoformat(),
            "error_message": s.error_message
        })
    return result

@app.post("/sources/github")
async def add_github_source(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    body = await request.json()
    repo = body.get("repo")
    branch = body.get("branch", "main")
    
    token = body.get("token")
    
    if not repo:
        raise HTTPException(status_code=400, detail="Repo is required")
        
    # Обработка случая, когда пользователь ввел полную ссылку
    if repo.startswith("http"):
        clean_repo = repo.replace("https://", "").replace("http://", "")
        if "@" in clean_repo:
            clean_repo = clean_repo.split("@")[-1]
            
        if token:
            repo_url = f"https://{token}@{clean_repo}"
        else:
            repo_url = f"https://{clean_repo}"
            
        if not repo_url.endswith(".git"):
            repo_url += ".git"
        # Extract owner/repo for the name
        name = clean_repo.replace("github.com/", "").replace(".git", "")
    else:
        if token:
            repo_url = f"https://{token}@github.com/{repo}.git"
        else:
            repo_url = f"https://github.com/{repo}.git"
        name = repo
    
    new_source = Source(
        name=name,
        type="github",
        detail=f"branch: {branch}",
        status="queued"
    )
    db.add(new_source)
    db.commit()
    db.refresh(new_source)
    
    background_tasks.add_task(index_github_repo, new_source.id, repo_url)
    return {"id": new_source.id, "status": "queued"}

@app.get("/sources/preview")
async def preview_local_source(path: str = Query(...)):
    from services.folder_service import get_folder_preview
    result = get_folder_preview(path)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.post("/sources/local")
async def add_local_source(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    body = await request.json()
    path = body.get("path")
    
    if not path or not os.path.isdir(path):
        raise HTTPException(status_code=400, detail="Valid path is required")
        
    name = os.path.basename(path.rstrip("/")) or path
    
    new_source = Source(
        name=name,
        type="local",
        detail=path,
        status="ready"
    )
    db.add(new_source)
    db.commit()
    db.refresh(new_source)
    
    return {"id": new_source.id, "status": "ready"}

@app.delete("/sources/{source_id}")
def delete_source(source_id: str, db: Session = Depends(get_db)):
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
        
    # Удалить из БД (Alembic cascade delete orphan удалит и чанки из базы)
    db.delete(source)
    db.commit()
    
    # Удалить из ChromaDB
    delete_source_collection(source_id)
    
    return {"success": True}

@app.post("/sources/{source_id}/reindex")
async def reindex_source_endpoint(source_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Запускает переиндексацию источника в фоне. Для local — инкрементально (только изменённые файлы)."""
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if source.status == "indexing":
        raise HTTPException(status_code=409, detail="Source is already being indexed")
    
    source.status = "queued"
    db.commit()
    background_tasks.add_task(reindex_source, source_id)
    return {"status": "queued", "source_id": source_id}

@app.get("/sources/{source_id}/status")
def get_source_status(source_id: str, db: Session = Depends(get_db)):
    """Лёгкий эндпоинт для поллинга статуса источника без загрузки всего списка."""
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return {
        "id": source.id,
        "status": source.status,
        "files": source.files_count or 0,
        "indexed_at": source.indexed_at.isoformat() if source.indexed_at else None,
        "error_message": source.error_message,
    }

# --- FILE TREE ---

def build_file_tree(flat_paths: list[str]) -> list[dict]:
    """
    Конвертирует список путей типа ['src/main.py', 'src/utils/h.py', 'README.md']
    в древовидную структуру JSON для UI сайдбара.
    """
    root = []
    for path in flat_paths:
        parts = path.split('/')
        current_level = root
        for i, part in enumerate(parts):
            is_file = (i == len(parts) - 1)
            existing_node = next((node for node in current_level if node["name"] == part), None)
            if not existing_node:
                new_node = {
                    "name": part,
                    "path": "/".join(parts[:i+1]),
                    "type": "file" if is_file else "directory",
                }
                if not is_file:
                    new_node["children"] = []
                current_level.append(new_node)
                current_level = new_node.get("children", [])
            else:
                current_level = existing_node.get("children", [])
    return root

@app.get("/sources/{source_id}/tree")
def get_source_file_tree(source_id: str, db: Session = Depends(get_db)):
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    paths = db.query(Chunk.file_path).filter(Chunk.source_id == source_id).distinct().all()
    flat_paths = sorted([p[0] for p in paths])
    return build_file_tree(flat_paths)

# --- CONVERSATIONS API ---

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

async def _auto_rename_conversation(conv_id: str, first_message: str):
    """Фоновая задача: переименовать беседу через Ollama на основе первого сообщения."""
    prompt = (
        'Ты — эксперт по анализу текста. Твоя задача — прочитать первое сообщение '
        'пользователя в чате и сгенерировать короткое, емкое название для этой беседы '
        '(от 2 до 4 слов) на языке пользователя.\n'
        'ЗАПРЕЩЕНО использовать кавычки, знаки препинания или писать вводные фразы. '
        'Верни ТОЛЬКО само название.\n\n'
        f'Сообщение пользователя: "{first_message}"\nНазвание чата:'
    )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/generate", json={
                "model": "gemma4:e4b",
                "prompt": prompt,
                "stream": False,
            })
            if resp.status_code == 200:
                title = resp.json().get("response", "").strip()
                if title and len(title) < 100:
                    local_db = next(get_db())
                    try:
                        conv = local_db.query(Conversation).filter(Conversation.id == conv_id).first()
                        if conv:
                            conv.title = title
                            local_db.commit()
                    finally:
                        local_db.close()
    except Exception as e:
        print(f"[auto-rename] Ошибка: {e}")


@app.get("/conversations")
def get_conversations(db: Session = Depends(get_db)):
    convs = db.query(Conversation).order_by(Conversation.created_at.desc()).all()
    result = []
    for c in convs:
        result.append({
            "id": c.id,
            "title": c.title,
            "createdAt": c.created_at.isoformat(),
            "sourceIds": [s.id for s in c.sources],
        })
    return result


@app.post("/conversations")
async def create_conversation(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    body = await request.json()
    title = body.get("title", "Новая беседа")
    source_ids = body.get("sourceIds", [])

    conv = Conversation(title=title)
    db.add(conv)
    db.flush()

    # Привязать источники
    if source_ids:
        sources = db.query(Source).filter(Source.id.in_(source_ids)).all()
        conv.sources = sources

    db.commit()
    db.refresh(conv)
    return {
        "id": conv.id,
        "title": conv.title,
        "createdAt": conv.created_at.isoformat(),
        "sourceIds": [s.id for s in conv.sources],
    }


@app.put("/conversations/{conv_id}")
async def update_conversation(
    conv_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    body = await request.json()
    if "title" in body:
        conv.title = body["title"]
    if "sourceIds" in body:
        sources = db.query(Source).filter(Source.id.in_(body["sourceIds"])).all()
        conv.sources = sources

    db.commit()
    db.refresh(conv)
    return {
        "id": conv.id,
        "title": conv.title,
        "createdAt": conv.created_at.isoformat(),
        "sourceIds": [s.id for s in conv.sources],
    }


@app.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str, db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conv)
    db.commit()
    return {"success": True}


@app.get("/conversations/{conv_id}/messages")
async def get_messages(
    conv_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    source_ids = [s.id for s in conv.sources]

    msgs = db.query(Message).filter(Message.conversation_id == conv_id).order_by(Message.timestamp.asc()).all()
    return [{
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "steps": json.loads(m.steps) if m.steps else [],
        "timestamp": int(m.timestamp.timestamp() * 1000),
    } for m in msgs]


@app.post("/conversations/{conv_id}/messages")
async def add_message(conv_id: str, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    body = await request.json()
    role = body.get("role", "user")
    content = body.get("content", "")
    steps = body.get("steps")

    msg = Message(
        conversation_id=conv_id,
        role=role,
        content=content,
        steps=json.dumps(steps) if steps else None
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    # Авто-переименование
    if role == "user":
        if conv.title == "Новая беседа":
            background_tasks.add_task(_auto_rename_conversation, conv_id, content)

    return {
        "id": msg.id,
        "role": msg.role,
        "content": msg.content,
        "steps": json.loads(msg.steps) if msg.steps else [],
        "timestamp": int(msg.timestamp.timestamp() * 1000),
    }


@app.put("/conversations/{conv_id}/messages/{msg_id}")
async def update_message(conv_id: str, msg_id: str, request: Request, db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.id == msg_id, Message.conversation_id == conv_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    body = await request.json()
    msg.content = body.get("content", msg.content)
    db.commit()
    db.refresh(msg)
    return {
        "id": msg.id,
        "role": msg.role,
        "content": msg.content,
        "timestamp": int(msg.timestamp.timestamp() * 1000),
    }


@app.delete("/conversations/{conv_id}/messages/{msg_id}")
def delete_message(conv_id: str, msg_id: str, db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.id == msg_id, Message.conversation_id == conv_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    db.delete(msg)
    db.commit()
    return {"ok": True}

# --- ANALYTICS API ---

from fastapi.responses import HTMLResponse
from sqlalchemy import func, text
from datetime import datetime

@app.get("/analytics/metrics")
def get_analytics_metrics(from_date: str = None, to_date: str = None, db: Session = Depends(get_db)):
    query_filter = ""
    params = {}
    if from_date:
        query_filter += " AND created_at >= :from_date"
        params["from_date"] = from_date
    if to_date:
        query_filter += " AND created_at <= :to_date"
        params["to_date"] = to_date

    # 1. Routing accuracy (Doorman) - Silent miss rate
    silent_miss_sql = f"""
        SELECT task_type_classified, COUNT(*) as total,
               SUM(CASE WHEN tools_available > 0 AND tools_called = 0 THEN 1 ELSE 0 END) as silent_miss
        FROM execution_traces
        WHERE task_type_classified IS NOT NULL {query_filter}
        GROUP BY task_type_classified
    """
    silent_miss_res = db.execute(text(silent_miss_sql), params).mappings().all()

    # 2. Per-model outcome tracking
    model_outcome_sql = f"""
        SELECT model_selected, task_type_final,
               COUNT(*) as total,
               SUM(CASE WHEN final_status = 'success' THEN 1 ELSE 0 END) as success_count,
               SUM(CASE WHEN final_status = 'human_corrected' THEN 1 ELSE 0 END) as correction_count,
               SUM(CASE WHEN final_status = 'stub_response' THEN 1 ELSE 0 END) as stub_count
        FROM execution_traces
        WHERE model_selected IS NOT NULL AND final_status IS NOT NULL {query_filter}
        GROUP BY model_selected, task_type_final
    """
    model_outcome_res = db.execute(text(model_outcome_sql), params).mappings().all()

    # 3. Human correction rate per task type
    correction_sql = f"""
        SELECT et.task_type_final, 
               COUNT(DISTINCT et.task_id) as total_tasks,
               COUNT(hc.id) as corrected_tasks
        FROM execution_traces et
        LEFT JOIN human_corrections hc ON hc.task_id = et.task_id
        WHERE et.task_type_final IS NOT NULL {query_filter.replace('created_at', 'et.created_at')}
        GROUP BY et.task_type_final
    """
    correction_res = db.execute(text(correction_sql), params).mappings().all()

    # 4. Latency per stage (using json extraction depends on DB, for SQLite/generic we can extract in Python or use AVG(duration_ms))
    # We will just use duration_ms for total, or parse stage_durations if available.
    # Since SQLite json1 might not be enabled on all environments, we fetch and aggregate in python.
    latency_sql = f"""
        SELECT task_type_final, stage_durations, duration_ms
        FROM execution_traces
        WHERE task_type_final IS NOT NULL {query_filter}
    """
    latency_raw = db.execute(text(latency_sql), params).mappings().all()
    
    latency_agg = {}
    import json
    for row in latency_raw:
        tt = row['task_type_final']
        if tt not in latency_agg:
            latency_agg[tt] = {'count': 0, 'total_ms': 0, 'executor_ms': 0}
        
        latency_agg[tt]['count'] += 1
        latency_agg[tt]['total_ms'] += row['duration_ms'] or 0
        
        sd_str = row['stage_durations']
        if sd_str:
            try:
                sd = json.loads(sd_str)
                latency_agg[tt]['executor_ms'] += sd.get('executor', 0)
            except:
                pass
                
    latency_res = []
    for tt, agg in latency_agg.items():
        if agg['count'] > 0:
            latency_res.append({
                'task_type_final': tt,
                'avg_total_ms': agg['total_ms'] / agg['count'],
                'avg_executor_ms': agg['executor_ms'] / agg['count']
            })

    # 5. Unverified Success
    unverified_sql = f"""
        SELECT task_type_final, COUNT(*) as total,
               SUM(CASE WHEN tool_verified = 0 THEN 1 ELSE 0 END) as unverified_success,
               GROUP_CONCAT(tool_verification_details, '; ') as details
        FROM execution_traces
        WHERE final_status = 'success' AND tool_verified IS NOT NULL {query_filter}
        GROUP BY task_type_final
    """
    unverified_res = db.execute(text(unverified_sql), params).mappings().all()

    return {
        "silent_miss": [dict(r) for r in silent_miss_res],
        "model_outcome": [dict(r) for r in model_outcome_res],
        "human_correction": [dict(r) for r in correction_res],
        "latency": latency_res,
        "unverified_success": [dict(r) for r in unverified_res]
    }

@app.get("/analytics/dashboard", response_class=HTMLResponse)
def get_dashboard():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Agent Analytics Dashboard</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; padding: 20px; background: #f5f7f9; color: #333; }
            h1, h2 { color: #111; }
            .card { background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            th, td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }
            th { background-color: #f8f9fa; }
            .filters { margin-bottom: 20px; display: flex; gap: 10px; align-items: center; }
            input[type="date"] { padding: 5px; border: 1px solid #ccc; border-radius: 4px; }
            button { padding: 6px 12px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
            button:hover { background: #0056b3; }
        </style>
    </head>
    <body>
        <h1>Agent Behavior Analytics</h1>
        <div class="filters">
            <label>From: <input type="date" id="fromDate"></label>
            <label>To: <input type="date" id="toDate"></label>
            <button onclick="loadData()">Apply Filters</button>
        </div>

        <div class="card">
            <h2>1. Routing Accuracy (Silent Misses)</h2>
            <p><small>Tasks classified by Doorman where tools were available but not called (potential hallucinations/stubs).</small></p>
            <table id="silentMissTable">
                <thead><tr><th>Task Type (Classified)</th><th>Total Tasks</th><th>Silent Misses</th><th>Miss Rate</th></tr></thead>
                <tbody></tbody>
            </table>
        </div>

        <div class="card">
            <h2>2. Per-Model Outcome Tracking</h2>
            <table id="modelOutcomeTable">
                <thead><tr><th>Model</th><th>Task Type (Final)</th><th>Total</th><th>Success %</th><th>Correction %</th><th>Stub %</th></tr></thead>
                <tbody></tbody>
            </table>
        </div>

        <div class="card">
            <h2>3. Human Correction Rate</h2>
            <table id="humanCorrectionTable">
                <thead><tr><th>Task Type</th><th>Total Tasks</th><th>Corrected Tasks</th><th>Correction Rate</th></tr></thead>
                <tbody></tbody>
            </table>
        </div>

        <div class="card">
            <h2>4. Latency per Stage</h2>
            <table id="latencyTable">
                <thead><tr><th>Task Type</th><th>Avg Total (ms)</th><th>Avg Executor (ms)</th></tr></thead>
                <tbody></tbody>
            </table>
        </div>

        <div class="card">
            <h2>5. Unverified Successes (Fake Executions)</h2>
            <p><small>Tasks marked as 'success' but the side-effect verification failed.</small></p>
            <table id="unverifiedTable">
                <thead><tr><th>Task Type</th><th>Total Verified attempts</th><th>Unverified (Failed Verification)</th><th>Failure Rate</th><th>Details</th></tr></thead>
                <tbody></tbody>
            </table>
        </div>

        <script>
            async function loadData() {
                const fromDate = document.getElementById('fromDate').value;
                const toDate = document.getElementById('toDate').value;
                let url = '/analytics/metrics';
                const params = new URLSearchParams();
                if (fromDate) params.append('from_date', fromDate + ' 00:00:00');
                if (toDate) params.append('to_date', toDate + ' 23:59:59');
                if (params.toString()) url += '?' + params.toString();

                try {
                    const response = await fetch(url);
                    const data = await response.json();
                    
                    // 1. Silent Miss
                    const smBody = document.querySelector('#silentMissTable tbody');
                    smBody.innerHTML = '';
                    data.silent_miss.forEach(row => {
                        const rate = row.total > 0 ? ((row.silent_miss / row.total) * 100).toFixed(1) : 0;
                        smBody.innerHTML += `<tr><td>${row.task_type_classified}</td><td>${row.total}</td><td>${row.silent_miss}</td><td>${rate}%</td></tr>`;
                    });

                    // 2. Model Outcome
                    const moBody = document.querySelector('#modelOutcomeTable tbody');
                    moBody.innerHTML = '';
                    data.model_outcome.forEach(row => {
                        const successRate = row.total > 0 ? ((row.success_count / row.total) * 100).toFixed(1) : 0;
                        const corrRate = row.total > 0 ? ((row.correction_count / row.total) * 100).toFixed(1) : 0;
                        const stubRate = row.total > 0 ? ((row.stub_count / row.total) * 100).toFixed(1) : 0;
                        moBody.innerHTML += `<tr><td>${row.model_selected}</td><td>${row.task_type_final}</td><td>${row.total}</td><td>${successRate}%</td><td>${corrRate}%</td><td>${stubRate}%</td></tr>`;
                    });

                    // 3. Human Correction
                    const hcBody = document.querySelector('#humanCorrectionTable tbody');
                    hcBody.innerHTML = '';
                    data.human_correction.forEach(row => {
                        const rate = row.total_tasks > 0 ? ((row.corrected_tasks / row.total_tasks) * 100).toFixed(1) : 0;
                        hcBody.innerHTML += `<tr><td>${row.task_type_final}</td><td>${row.total_tasks}</td><td>${row.corrected_tasks}</td><td>${rate}%</td></tr>`;
                    });

                    // 4. Latency
                    const latBody = document.querySelector('#latencyTable tbody');
                    latBody.innerHTML = '';
                    data.latency.forEach(row => {
                        latBody.innerHTML += `<tr><td>${row.task_type_final}</td><td>${Math.round(row.avg_total_ms)}</td><td>${Math.round(row.avg_executor_ms)}</td></tr>`;
                    });

                    // 5. Unverified Success
                    const unvBody = document.querySelector('#unverifiedTable tbody');
                    unvBody.innerHTML = '';
                    data.unverified_success.forEach(row => {
                        const rate = row.total > 0 ? ((row.unverified_success / row.total) * 100).toFixed(1) : 0;
                        unvBody.innerHTML += `<tr><td>${row.task_type_final}</td><td>${row.total}</td><td>${row.unverified_success}</td><td>${rate}%</td><td><small>${row.details || ''}</small></td></tr>`;
                    });

                } catch (e) {
                    console.error('Error fetching analytics:', e);
                }
            }

            // Load on init
            loadData();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# ── PRIVILEGED ACTIONS API ───────────────────────────────────────

import shutil
import subprocess
import difflib
import yaml
from pydantic import BaseModel
from typing import List, Optional
from models import PendingPrivilegedAction, ScenarioDefinition, ApprenticeStep

class PrivilegedActionResponse(BaseModel):
    id: str
    action_type: str
    target: str
    instruction: str
    reasoning: str
    session_id: Optional[str]
    status: str
    diff_content: Optional[str]
    created_at: str
    updated_at: str

@app.get("/privileged-actions")
def get_privileged_actions(status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(PendingPrivilegedAction)
    if status:
        query = query.filter(PendingPrivilegedAction.status == status)
    actions = query.order_by(PendingPrivilegedAction.created_at.desc()).all()
    
    return [
        {
            "id": a.id,
            "action_type": a.action_type,
            "target": a.target,
            "instruction": a.instruction,
            "reasoning": a.reasoning,
            "session_id": a.session_id,
            "status": a.status,
            "diff_content": a.diff_content,
            "created_at": str(a.created_at),
            "updated_at": str(a.updated_at)
        }
        for a in actions
    ]

def _run_coder_in_sandbox(action_id: str, target: str, instruction: str):
    db = SessionLocal()
    sandbox_path = f"/tmp/coder_sandbox_{action_id}"
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Update status to running
    action = db.query(PendingPrivilegedAction).filter(PendingPrivilegedAction.id == action_id).first()
    if not action:
        db.close()
        return
        
    try:
        # Copy project
        if os.path.exists(sandbox_path):
            shutil.rmtree(sandbox_path)
        shutil.copytree(project_root, sandbox_path, dirs_exist_ok=True)
        
        # We need to run call_privileged_tool on mcp
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        args = {
            "target_dir": sandbox_path,
            "instruction": instruction
        }
        
        try:
            loop.run_until_complete(llm_manager.mcp.call_privileged_tool("run_claude_coder", args))
        except Exception as e:
            logger.error(f"Error calling run_claude_coder: {e}")
        finally:
            loop.close()
            
        # git diff
        try:
            res = subprocess.run(["git", "diff", "--no-index", project_root, sandbox_path], capture_output=True, text=True)
            diff_text = res.stdout
            
            # Filter diff to remove absolute paths and just show relative ones
            clean_diff = []
            for line in diff_text.splitlines():
                if line.startswith(f"--- {project_root}"):
                    clean_diff.append(line.replace(f"--- {project_root}", "--- a"))
                elif line.startswith(f"+++ {sandbox_path}"):
                    clean_diff.append(line.replace(f"+++ {sandbox_path}", "+++ b"))
                else:
                    clean_diff.append(line)
            
            diff_text = "\\n".join(clean_diff)
            
        except Exception as e:
            diff_text = f"Error generating diff: {e}"
            
        action.diff_content = diff_text
        action.status = "diff_ready"
        db.commit()
        
    except Exception as e:
        logger.error(f"Error in sandbox: {e}")
        action.status = "apply_failed"
        action.diff_content = f"Error: {e}"
        db.commit()
    finally:
        # Clean up sandbox
        if os.path.exists(sandbox_path):
            shutil.rmtree(sandbox_path, ignore_errors=True)
        db.close()


@app.post("/privileged-actions/{action_id}/approve")
def approve_action(
    action_id: str, 
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if authorization != f"Bearer {WORKER_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    action = db.query(PendingPrivilegedAction).filter(PendingPrivilegedAction.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
        
    if action.status != "awaiting_approval":
        raise HTTPException(status_code=400, detail="Action is not awaiting approval")

    if action.action_type == "coder_task":
        action.status = "coder_running"
        db.commit()
        background_tasks.add_task(_run_coder_in_sandbox, action.id, action.target, action.instruction)
        return {"status": "coder_running"}
        
    elif action.action_type == "prompt_update":
        # Generate pseudo-diff
        role_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roles", f"{action.target}.yaml")
        if not os.path.exists(role_path):
            raise HTTPException(status_code=404, detail="Role file not found")
            
        with open(role_path, "r", encoding="utf-8") as f:
            role_cfg = yaml.safe_load(f)
            
        old_prompt = role_cfg.get("system_instruction", "")
        new_prompt = action.instruction
        
        diff = difflib.unified_diff(
            old_prompt.splitlines(), 
            new_prompt.splitlines(), 
            fromfile=f"a/roles/{action.target}.yaml", 
            tofile=f"b/roles/{action.target}.yaml", 
            lineterm=""
        )
        
        diff_text = "\\n".join(diff)
        action.diff_content = diff_text
        action.status = "diff_ready"
        db.commit()
        
        return {"status": "diff_ready"}

@app.post("/privileged-actions/{action_id}/reject")
def reject_action(
    action_id: str,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if authorization != f"Bearer {WORKER_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    action = db.query(PendingPrivilegedAction).filter(PendingPrivilegedAction.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
        
    action.status = "rejected"
    db.commit()
    return {"status": "rejected"}

@app.get("/privileged-actions/{action_id}/diff")
def get_action_diff(
    action_id: str,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if authorization != f"Bearer {WORKER_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    action = db.query(PendingPrivilegedAction).filter(PendingPrivilegedAction.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
        
    return {"diff_content": action.diff_content}

@app.post("/privileged-actions/{action_id}/apply-diff")
def apply_action_diff(
    action_id: str,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if authorization != f"Bearer {WORKER_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    action = db.query(PendingPrivilegedAction).filter(PendingPrivilegedAction.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
        
    if action.status != "diff_ready":
        raise HTTPException(status_code=400, detail="Action is not ready to apply")

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if action.action_type == "coder_task":
        patch_file = f"/tmp/patch_{action_id}.diff"
        backup_path = f"/tmp/pre_apply_backup_{action_id}"
        
        with open(patch_file, "w", encoding="utf-8") as f:
            f.write(action.diff_content or "")
            
        try:
            shutil.copytree(project_root, backup_path, dirs_exist_ok=True)
            subprocess.run(["git", "apply", patch_file], check=True, cwd=project_root)
            action.status = "applied"
            db.commit()
        except subprocess.CalledProcessError as e:
            shutil.copytree(backup_path, project_root, dirs_exist_ok=True)
            action.status = "apply_failed"
            db.commit()
            raise HTTPException(status_code=422, detail="Git apply failed, rolled back.")
        finally:
            if os.path.exists(backup_path):
                shutil.rmtree(backup_path, ignore_errors=True)
            if os.path.exists(patch_file):
                os.remove(patch_file)
                
    elif action.action_type == "prompt_update":
        role_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roles", f"{action.target}.yaml")
        if not os.path.exists(role_path):
            raise HTTPException(status_code=404, detail="Role file not found")
            
        with open(role_path, "r", encoding="utf-8") as f:
            role_cfg = yaml.safe_load(f)
            
        role_cfg["system_instruction"] = action.instruction
        
        with open(role_path, "w", encoding="utf-8") as f:
            yaml.dump(role_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            
        action.status = "applied"
        db.commit()
        
    return {"status": "applied"}

@app.post("/agent/analyze-session/{session_id}")
async def analyze_session_manual(
    session_id: str,
    authorization: Optional[str] = Header(None)
):
    if authorization != f"Bearer {WORKER_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    report = await llm_manager.run_meta_analyst(session_id)
    return {"report": report}
# ── Agent Selector & Scenarios ─────────────────────────────────────

@app.get("/agents/list")
async def list_agents():
    db = next(get_db())
    try:
        # 1. Загружаем роли из .yaml файлов
        roles_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roles")
        agents = []
        if os.path.exists(roles_dir):
            for filename in os.listdir(roles_dir):
                if filename.endswith(".yaml"):
                    with open(os.path.join(roles_dir, filename), "r", encoding="utf-8") as f:
                        cfg = yaml.safe_load(f)
                        if cfg:
                            agents.append({
                                "id": filename.replace(".yaml", ""),
                                "name": cfg.get("name", filename),
                                "description": cfg.get("description", ""),
                                "type": "role"
                            })
        
        # 2. Загружаем активные сценарии
        scenarios = db.query(ScenarioDefinition).filter(ScenarioDefinition.status == "active").all()
        for s in scenarios:
            agents.append({
                "id": f"scenario_{s.id}",
                "name": s.name,
                "description": s.description,
                "type": "scenario"
            })
            
        return {"agents": agents}
    finally:
        db.close()

@app.post("/scenarios")
async def create_scenario(
    request: Request,
    authorization: Optional[str] = Header(None)
):
    if authorization != f"Bearer {WORKER_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    body = await request.json()
    name = body.get("name")
    description = body.get("description")
    steps = body.get("steps")
    session_id = body.get("session_id")
    
    db = next(get_db())
    try:
        scenario = ScenarioDefinition(
            name=name,
            description=description,
            steps=json.dumps(steps),
            proposed_by_session_id=session_id,
            status="draft"
        )
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
        return {"status": "ok", "id": scenario.id}
    finally:
        db.close()

@app.post("/scenarios/{scenario_id}/activate")
async def activate_scenario(scenario_id: str):
    db = next(get_db())
    try:
        scenario = db.query(ScenarioDefinition).filter(ScenarioDefinition.id == scenario_id).first()
        if not scenario:
            raise HTTPException(status_code=404, detail="Scenario not found")
            
        steps = json.loads(scenario.steps)
        # Проверка на PRIVILEGED_TOOLS
        from agent.mcp_manager import PRIVILEGED_TOOLS
        
        for step in steps:
            tool_name = step.get("tool")
            if tool_name in PRIVILEGED_TOOLS:
                raise HTTPException(
                    status_code=403, 
                    detail=f"Сценарий использует привилегированный инструмент {tool_name}. Активация заблокирована."
                )
                
        scenario.status = "active"
        db.commit()
        return {"status": "activated"}
    finally:
        db.close()

# ── Apprentice Mode ─────────────────────────────────────────────────

@app.get("/apprentice/{session_id}/pending")
async def get_pending_apprentice_step(session_id: str):
    db = next(get_db())
    try:
        step = db.query(ApprenticeStep).filter(
            ApprenticeStep.session_id == session_id,
            ApprenticeStep.human_decision == None
        ).order_by(ApprenticeStep.created_at.desc()).first()
        
        if not step:
            return {"status": "none"}
            
        return {
            "status": "pending",
            "step": {
                "id": step.id,
                "proposed_tool": step.proposed_tool,
                "proposed_args": json.loads(step.proposed_args) if step.proposed_args else None,
                "proposed_reasoning": step.proposed_reasoning,
                "proposed_response_text": step.proposed_response_text
            }
        }
    finally:
        db.close()

@app.post("/apprentice/step/{step_id}/accept")
async def accept_apprentice_step(step_id: str):
    db = next(get_db())
    try:
        step = db.query(ApprenticeStep).filter(ApprenticeStep.id == step_id).first()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
            
        # Блокировка: нельзя принять привилегированный инструмент
        from agent.mcp_manager import PRIVILEGED_TOOLS
        if step.proposed_tool in PRIVILEGED_TOOLS:
            # Создаем PendingPrivilegedAction
            action = PendingPrivilegedAction(
                action_type="coder_task" if step.proposed_tool == "run_claude_coder" else "terminal_command",
                target=step.proposed_tool,
                instruction=step.proposed_args,
                reasoning=step.proposed_reasoning,
                session_id=step.session_id,
                status="awaiting_approval"
            )
            db.add(action)
            db.commit()
            return {"status": "elevation_required", "action_id": action.id}
            
        step.human_decision = "accepted"
        step.decided_at = datetime.utcnow()
        db.commit()
        return {"status": "accepted"}
    finally:
        db.close()

@app.post("/apprentice/step/{step_id}/correct")
async def correct_apprentice_step(step_id: str, request: Request):
    db = next(get_db())
    try:
        body = await request.json()
        corrected_args = body.get("corrected_args")
        corrected_reasoning = body.get("corrected_reasoning", "")
        
        step = db.query(ApprenticeStep).filter(ApprenticeStep.id == step_id).first()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
            
        step.human_decision = "corrected"
        step.corrected_args = json.dumps(corrected_args) if corrected_args else None
        step.corrected_reasoning = corrected_reasoning
        step.decided_at = datetime.utcnow()
        db.commit()
        return {"status": "corrected"}
    finally:
        db.close()

@app.post("/apprentice/step/{step_id}/reject")
async def reject_apprentice_step(step_id: str):
    db = next(get_db())
    try:
        step = db.query(ApprenticeStep).filter(ApprenticeStep.id == step_id).first()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
            
        step.human_decision = "rejected"
        step.decided_at = datetime.utcnow()
        db.commit()
        return {"status": "rejected"}
    finally:
        db.close()
