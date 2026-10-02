import json
import logging
import asyncio
from agent.roles_impl.base_agent import BaseAgent

logger = logging.getLogger("contextus.worker_agent")

class WorkerAgent(BaseAgent):
    """
    Класс для тяжелых агентов-кодеров/воркеров, которые работают в цикле (Planner -> Executor).
    Извлечен из старого llm_manager.py (execute_stream).
    """
    
    async def run_stream(self, query: str, history: list[dict]):
        yield f'data: {json.dumps({"type": "step", "step": "think", "message": f"Думает ({self.model.name})..."})}\n\n'
        # Заглушка, чтобы пока не ломать старый код.
        yield f'data: {json.dumps({"type": "result", "content": "Воркер пока не извлечен до конца."})}\n\n'
