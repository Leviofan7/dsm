import uuid
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, Table, Boolean, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class Source(Base):
    __tablename__ = "sources"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, index=True)
    type = Column(String) # github, local, file
    detail = Column(String)
    files_count = Column(Integer, default=0)
    size_bytes = Column(Integer, default=0)
    status = Column(String, default="queued") # queued, indexing, indexed, error
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    indexed_at = Column(DateTime, nullable=True)

    chunks = relationship("Chunk", back_populates="source", cascade="all, delete-orphan")

class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    source_id = Column(String, ForeignKey("sources.id"))
    file_path = Column(String, index=True)
    chunk_index = Column(Integer)
    content_preview = Column(String(500))
    token_count = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    source = relationship("Source", back_populates="chunks")

# Таблица связей Many-to-Many между Беседами и Источниками данных
conversation_sources = Table(
    "conversation_sources",
    Base.metadata,
    Column("conversation_id", String(36), ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True),
    Column("source_id", String(36), ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True)
)

class User(Base):
    """Пользователь системы. Роль — на уровне человека, не беседы."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(String(20), default="user", nullable=False)  # "user" | "admin"
    telegram_chat_id = Column(String(50), unique=True, index=True, nullable=True)
    display_name = Column(String(255), nullable=True)
    linking_token = Column(String(64), nullable=True)       # одноразовый deep-link токен привязки
    linking_token_expires = Column(DateTime, nullable=True)  # TTL токена (10 мин)
    login_token = Column(String(64), nullable=True)         # одноразовый токен входа через Telegram
    login_token_expires = Column(DateTime, nullable=True)    # TTL токена входа (10 мин)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversations = relationship("Conversation", back_populates="user")

class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("telegram_chat_id", name="uq_conversation_telegram_chat_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(255), nullable=False, default="Новая беседа")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # nullable на переходный период
    telegram_chat_id = Column(String(50), index=True, nullable=True)  # для UI-иконки, НЕ источник роли
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    user = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    sources = relationship("Source", secondary=conversation_sources, backref="conversations")

class Message(Base):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(50), nullable=False)  # user / assistant
    content = Column(Text, nullable=False)
    steps = Column(Text, nullable=True) # JSON encoded steps
    timestamp = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")

class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    owner_id = Column(String(255), index=True) # Telegram chat_id or user ID
    title = Column(String(255), nullable=False, default="Task")
    cron_expression = Column(String(100), nullable=True)
    training_mode = Column(Boolean, nullable=False, default=False)
    status = Column(String(50), default="pending", index=True) # pending, running, paused, completed, failed, cancelled
    task_type = Column(String(50))
    query = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    events = relationship("AgentTaskEvent", back_populates="task", cascade="all, delete-orphan", order_by="AgentTaskEvent.sequence_number")
    subtasks = relationship("AgentSubtask", back_populates="task", cascade="all, delete-orphan", order_by="AgentSubtask.execution_order")

class AgentTaskEvent(Base):
    __tablename__ = "agent_task_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence_number = Column(Integer, nullable=False)
    event_type = Column(String(50), nullable=False) # e.g. "step", "result", "error", "hitl_request"
    payload = Column(Text, nullable=False) # JSON encoded payload
    created_at = Column(DateTime, default=datetime.utcnow)

    task = relationship("AgentTask", back_populates="events")

class ResourceDomCache(Base):
    __tablename__ = "resource_dom_cache"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    resource_domain = Column(String(255), index=True, nullable=False)
    dom_structure_hash = Column(String(255), index=True, nullable=False)
    payload = Column(Text, nullable=False)
    cached_at = Column(DateTime, default=datetime.utcnow)

class AgentSubtask(Base):
    __tablename__ = "agent_subtasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    depends_on_id = Column(String(36), ForeignKey("agent_subtasks.id", ondelete="SET NULL"), nullable=True)
    target_role = Column(String(50), nullable=True)
    topic = Column(String(255), nullable=False)
    prompt_instruction = Column(Text, nullable=False)
    execution_order = Column(Integer, nullable=False)
    status = Column(String(50), default="pending", index=True) # pending, running, completed, failed
    result_output = Column(Text, nullable=True)
    dom_cache_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    task = relationship("AgentTask", back_populates="subtasks")

class ExecutionTrace(Base):
    __tablename__ = "execution_traces"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String(36), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    session_id = Column(String(36), index=True, nullable=False)
    
    # Doorman / Classification
    task_type_classified = Column(String(50), index=True, nullable=True)
    task_type_final = Column(String(50), nullable=True)
    
    # Model info
    model_used = Column(String(100), nullable=False)
    model_selected = Column(String(100), index=True, nullable=True)
    
    # execution stats
    planner_enabled = Column(Boolean, nullable=True, default=False)
    tools_available = Column(Integer, nullable=True, default=0)
    tools_called = Column(Integer, nullable=True, default=0)
    tools_called_names = Column(Text, nullable=True) # JSON
    stage_durations = Column(Text, nullable=True) # JSON
    final_status = Column(String(50), index=True, nullable=True) # success, failure, stub_response, etc.

    # Verification
    tool_verified = Column(Boolean, nullable=True)
    tool_verification_details = Column(Text, nullable=True)

    duration_ms = Column(Integer, nullable=False)
    ttft_ms = Column(Integer, nullable=True)
    tps = Column(Float, nullable=True)
    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)
    actions_log = Column(Text, nullable=False) # Storing JSONB as Text string for generic compatibility
    errors = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class HumanCorrection(Base):
    __tablename__ = "human_corrections"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    task_type = Column(String(50), nullable=True)
    correction_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class WebSession(Base):
    """
    Реестр веб-сессий — служебный, а НЕ источник правды о доступе.

    Сессия сама по себе stateless (HMAC-токен в cookie), но из токена невозможно узнать,
    кто залогинен и когда истечёт: значит предупредить «сессия истекает через сутки»
    неоткуда. Одна строка на пользователя — минимально достаточное состояние для
    этой узкой задачи (уведомление), без дублирования процесса аутентификации.
    """
    __tablename__ = "web_sessions"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    expires_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    notified_at = Column(DateTime, nullable=True)  # когда предупредили о скором истечении


class IntentSpec(Base):
    __tablename__ = "intent_specs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String(128), index=True, unique=True, nullable=False)
    session_id = Column(String(36), nullable=True)
    previous_intent_id = Column(String(36), nullable=True)
    version = Column(Integer, default=1, nullable=False)
    title = Column(String(255), nullable=True)
    summary = Column(Text, nullable=True)
    requirements_json = Column(Text, nullable=True)
    constraints_json = Column(Text, nullable=True)
    status = Column(String(50), default="DRAFT_SPEC")
    content_hash = Column(String(96), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type = Column(String(100), nullable=False)
    payload = Column(Text, nullable=False)
    status = Column(String(50), default="PENDING")
    retry_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class ScenarioDefinition(Base):
    __tablename__ = "scenario_definitions"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255))
    description = Column(Text)
    steps = Column(Text)  # using Text for JSON compatibility across sqlite/pg
    
    intent_id = Column(String(36), ForeignKey("intent_specs.id"), nullable=True)
    source_task_id = Column(String(128), nullable=True)
    proposed_by_session_id = Column(String(128), nullable=True)
    mode = Column(String(16), nullable=True)
    
    scenario_hash = Column(String(96), index=True, nullable=True)  # sha256 of steps
    approved_hash = Column(String(96), nullable=True)  # Signed approved hash
    approved_by = Column(String(128), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    previous_scenario_id = Column(String(36), nullable=True)
    
    status = Column(String(50), default="draft")  # draft -> active | rejected
    created_at = Column(DateTime, default=datetime.utcnow)

class ApprenticeStep(Base):
    __tablename__ = "apprentice_steps"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String)
    proposed_tool = Column(String, nullable=True)   # None для финального ответа
    proposed_args = Column(Text, nullable=True)     # JSON string
    proposed_reasoning = Column(Text)
    proposed_response_text = Column(Text, nullable=True)  # если это не tool call, а финальный ответ
    human_decision = Column(String, nullable=True)   # 'accepted' | 'corrected' | 'rejected'
    corrected_args = Column(Text, nullable=True)      # если human_decision='corrected'
    corrected_reasoning = Column(Text, nullable=True) # опционально — почему поправили
    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime, nullable=True)


class ActionRequest(Base):
    """
    Apprentice-Gate 2.0: Реестр запросов на действия от автономного Кодера.

    Жизненный цикл статусов:
      pending_supervisor  → 14B Надсмотрщик анализирует запрос
      pending_friend_call → Человек-оператор должен принять решение
      approved            → Действие одобрено (Надсмотрщиком или человеком)
      rejected            → Действие отклонено с комментарием
    """
    __tablename__ = "action_requests"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Ссылка на породившую запись: AgentTask у gate-запросов (request_* кодера).
    # У предложений meta-analyst (prompt_update/coder_task) и у эскалации apprentice
    # задачи кодера НЕТ — сессия давно завершена, поэтому NULL, а не фиктивная ссылка.
    coder_task_id = Column(String(36), nullable=True, index=True)
    # Тип запрашиваемого действия
    action_type = Column(String(50), nullable=False)
    # RUN_COMMAND  — запрос на выполнение bash-команды в песочнице
    # REVIEW_PLAN  — запрос на утверждение спецификации/плана
    # APPLY_DIFF   — запрос на применение изменений в репозиторий
    payload = Column(Text, nullable=False)           # JSON: команда, план или diff
    status = Column(String(50), default="pending_supervisor", index=True)
    supervisor_notes = Column(Text, nullable=True)   # Вердикт и логи 14B Надсмотрщика
    human_comment = Column(Text, nullable=True)      # Комментарий при отклонении человеком
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ModelCatalog(Base):
    """
    Каталог локальных моделей, обнаруженных в Ollama (GET /api/tags).

    models.yaml остаётся источником курируемых записей (облако, алиасы, ручные метаданные).
    Здесь хранится всё, что реально установлено в Ollama, — чтобы выпадающие списки в UI
    совпадали с `ollama list`, а не только с хардкодом из YAML.

    Записи не удаляются при пропаже модели из Ollama, а помечаются `installed=False`:
    иначе сохранённые конфиги агентов (agent_configs.json) начали бы ссылаться на
    несуществующую модель только потому, что Ollama временно недоступна.
    """
    __tablename__ = "model_catalog"

    id = Column(String, primary_key=True)              # ключ реестра = tag модели в Ollama
    provider_name = Column(String, nullable=False, default="ollama")
    provider_type = Column(String, nullable=False, default="ollama")
    model_id = Column(String, nullable=False, index=True)
    context_window = Column(Integer, default=4096)
    supports_tools = Column(Boolean, default=False)
    supports_vision = Column(Boolean, default=False)
    tags = Column(String, default="")                  # CSV: local, discovered, ...
    installed = Column(Boolean, default=True, index=True)
    enabled = Column(Boolean, default=True)            # можно скрыть модель из UI, не удаляя
    size_bytes = Column(Integer, default=0)
    digest = Column(String, nullable=True)
    capabilities_raw = Column(Text, nullable=True)     # JSON из /api/show (для отладки)
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(Base):
    """Аудит-лог привилегированных действий."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    source = Column(String(20), nullable=False)     # "telegram" | "web_ui"
    action = Column(String(100), nullable=False)     # "approve_action", "reject_action", "edit_role", ...
    target_id = Column(String(128), nullable=True)   # ID объекта, над которым действие
    detail = Column(Text, nullable=True)             # JSON: diff, args, reason
    result = Column(String(20), nullable=False)      # "success" | "denied"
    created_at = Column(DateTime, default=datetime.utcnow)


class Artifact(Base):
    """
    Материальный результат прогона (задачи): план, файл, картинка, текст, отчёт, ссылка.

    Владелец артефакта — ЗАДАЧА (`agent_tasks`), а не беседа: история того, что система делала
    и какие результаты получила, должна переживать удаление беседы (решение 02.10.2026).
    Файлы лежат на диске (`<PROJECT_ROOT>/artifacts/<task_id>/`), в БД — только метаданные
    и sha256 (дедуп).
    """

    __tablename__ = "artifacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    kind = Column(String(20), nullable=False)        # plan | file | image | text | report | link | error_dump
    title = Column(String(255), nullable=False, default="Артефакт")
    mime = Column(String(120), nullable=True)
    size_bytes = Column(Integer, nullable=True)
    storage = Column(String(10), nullable=False, default="db")   # db | disk | url
    content = Column(Text, nullable=True)            # storage=db: инлайн-текст
    path = Column(String(1024), nullable=True)       # storage=disk: путь ОТНОСИТЕЛЬНО корня артефактов
    url = Column(String(2048), nullable=True)        # storage=url: внешняя ссылка
    sha256 = Column(String(64), nullable=True, index=True)
    origin = Column(String(16), nullable=False, default="agent")  # agent | system | user
    meta = Column(Text, nullable=True)               # JSON: произвольные поля (модель, шаг, страница…)
    ref_type = Column(String(32), nullable=True)     # задел на привязки: intent | scenario | subtask …
    ref_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)     # мягкое удаление: история не стирается


class RunJournal(Base):
    """
    Исход прогона в виде, пригодном для самоанализа: что делали, что не вышло, что получилось.

    Это то, «что система знает о себе»: по журналу meta-analyst ищет паттерны неудач и удач,
    а удачный прогон может быть предложен как сценарий (`scenario_proposed_id`).
    """

    __tablename__ = "run_journal"

    task_id = Column(String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"), primary_key=True)
    status = Column(String(20), nullable=False, default="success")   # success | partial | failed | cancelled
    summary = Column(Text, nullable=True)
    errors = Column(Text, nullable=True)             # JSON-список
    achievements = Column(Text, nullable=True)       # JSON-список
    metrics = Column(Text, nullable=True)            # JSON: длительность, шаги, тул-коллы, модели
    self_review = Column(Text, nullable=True)        # оценка от meta_analyst
    scenario_proposed_id = Column(
        String(36), ForeignKey("scenario_definitions.id", ondelete="SET NULL"), nullable=True
    )
    tags = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
