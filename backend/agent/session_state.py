"""
session_state.py — Session State Machine для Multi-Agent Controller.

Управляет жизненным циклом сессий агента:
  RUNNING → агент работает
  WAITING_FOR_HUMAN → ожидание вмешательства человека (HITL)
  COMPLETED → задача завершена

Каждая сессия изолирована по chat_id (Telegram) и хранит:
  - Полный массив messages для продолжения инференса
  - Текущую модель и инструменты
  - Скриншот для отправки человеку
  - asyncio.Event для блокировки/разблокировки цикла
"""

import asyncio
import logging
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("contextus.session")


class SessionState(Enum):
    RUNNING = "running"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETED = "completed"


@dataclass
class AgentSession:
    """Контекст одной активной сессии агента."""
    session_id: str
    chat_id: int
    state: SessionState = SessionState.RUNNING

    # Контекст инференса (сохраняется между паузами HITL)
    messages: list[dict] = field(default_factory=list)
    model_name: str = ""
    task_type: str = "general"
    tools: list[dict] | None = None

    # HITL данные
    screenshot_b64: str = ""
    hitl_reason: str = ""
    human_response: str = ""

    # Supervisor: история вызовов инструментов для детекции зацикливания
    tool_call_history: list[str] = field(default_factory=list)

    # asyncio.Event для блокировки цикла при ожидании человека
    pending_event: asyncio.Event = field(default_factory=asyncio.Event)

    # Время создания и последнего обновления
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def pause_for_human(self, reason: str, screenshot: str = ""):
        """Переводит сессию в ожидание вмешательства человека."""
        self.state = SessionState.WAITING_FOR_HUMAN
        self.hitl_reason = reason
        self.screenshot_b64 = screenshot
        self.human_response = ""
        self.pending_event.clear()  # Блокируем ожидание
        self.updated_at = time.time()
        logger.info(f"  ⏸️  Session {self.session_id[:8]} → WAITING_FOR_HUMAN: {reason}")

    def resume_from_human(self, response: str):
        """Возобновляет сессию после ответа человека."""
        self.state = SessionState.RUNNING
        self.human_response = response
        self.pending_event.set()  # Разблокируем ожидание
        self.updated_at = time.time()
        logger.info(f"  ▶️  Session {self.session_id[:8]} → RUNNING (human responded)")

    def complete(self):
        """Завершает сессию."""
        self.state = SessionState.COMPLETED
        self.updated_at = time.time()

    def record_tool_call(self, tool_name: str):
        """Записывает вызов инструмента в историю для Supervisor."""
        self.tool_call_history.append(tool_name)
        self.updated_at = time.time()

    def detect_tool_loop(self, window: int = 3) -> bool:
        """
        Supervisor: Детектирует зацикливание инструментов.
        Если последние `window` вызовов — одинаковые, возвращает True.
        """
        if len(self.tool_call_history) < window:
            return False
        last_calls = self.tool_call_history[-window:]
        return len(set(last_calls)) == 1


class SessionManager:
    """
    Синглтон-менеджер активных сессий.
    Ключ — chat_id (один пользователь = одна активная сессия).
    """

    def __init__(self):
        self._sessions: dict[int, AgentSession] = {}

    def create_session(self, chat_id: int, session_id: str) -> AgentSession:
        """Создает новую сессию, заменяя старую если была."""
        session = AgentSession(session_id=session_id, chat_id=chat_id)
        self._sessions[chat_id] = session
        logger.info(f"  📋 Создана сессия {session_id[:8]} для chat_id={chat_id}")
        return session

    def get_session(self, chat_id: int) -> AgentSession | None:
        """Возвращает активную сессию для chat_id."""
        return self._sessions.get(chat_id)

    def get_waiting_session(self, chat_id: int) -> AgentSession | None:
        """Возвращает сессию в состоянии WAITING_FOR_HUMAN для chat_id."""
        session = self._sessions.get(chat_id)
        if session and session.state == SessionState.WAITING_FOR_HUMAN:
            return session
        return None

    def remove_session(self, chat_id: int):
        """Удаляет завершенную сессию."""
        if chat_id in self._sessions:
            del self._sessions[chat_id]

    def cleanup_stale(self, max_age_seconds: int = 600):
        """Удаляет сессии старше max_age_seconds."""
        now = time.time()
        stale = [
            cid for cid, s in self._sessions.items()
            if (now - s.updated_at) > max_age_seconds
        ]
        for cid in stale:
            logger.info(f"  🗑️  Очистка устаревшей сессии для chat_id={cid}")
            del self._sessions[cid]


# Глобальный менеджер сессий
session_manager = SessionManager()
