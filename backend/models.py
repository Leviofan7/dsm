import uuid
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, Table, Boolean, Float
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

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(255), nullable=False, default="Новая беседа")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
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

class PendingPrivilegedAction(Base):
    __tablename__ = "pending_privileged_actions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    action_type = Column(String(50), nullable=False)   # 'prompt_update' | 'coder_task'
    target = Column(String(255), nullable=False)        # target_persona или target_file
    instruction = Column(Text, nullable=False)     # new_prompt или coder instruction
    reasoning = Column(Text, nullable=False)       # диагноз от Ревизора
    session_id = Column(String(36), nullable=True)  # из какой сессии
    status = Column(String(50), default="awaiting_approval")
    # awaiting_approval -> approved -> coder_running -> diff_ready
    # -> diff_approved -> applied | rejected
    diff_content = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

from sqlalchemy.dialects.postgresql import JSON

class ScenarioDefinition(Base):
    __tablename__ = "scenario_definitions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String)
    description = Column(Text)
    steps = Column(Text)  # using Text for JSON compatibility across sqlite/pg
    scenario_hash = Column(String, index=True, nullable=True)  # sha256 of steps
    approved_hash = Column(String, nullable=True)  # Signed approved hash
    proposed_by_session_id = Column(String, nullable=True)
    status = Column(String, default="draft")  # draft -> active | rejected
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
    # Привязка к задаче кодера (AgentTask или PendingPrivilegedAction)
    coder_task_id = Column(String(36), nullable=False, index=True)
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

