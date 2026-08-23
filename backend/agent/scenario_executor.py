import json
import asyncio
import logging
import time
from database import SessionLocal
from models import ScenarioDefinition
from agent.mcp_manager import MCPManager

logger = logging.getLogger("contextus.scenario_executor")

class LinearScenarioExecutor:
    def __init__(self, mcp_manager: MCPManager, llm_manager):
        self.mcp = mcp_manager
        self.llm_manager = llm_manager

    async def execute_scenario(self, task_id: str, scenario_id: str, query: str, mode: str = "auto"):
        """
        Исполняет сценарий (последовательность шагов) линейно, без вызова LLM.
        Если mode == "apprentice", останавливается перед каждым шагом.
        """
        db = SessionLocal()
        import hashlib
        try:
            scenario = db.query(ScenarioDefinition).filter(
                ScenarioDefinition.id == scenario_id.replace("scenario_", "")
            ).first()
            if not scenario:
                yield 'data: ' + json.dumps({"type": "error", "message": "Сценарий не найден"}) + '\n\n'
                return
            steps = json.loads(scenario.steps)
            approved_hash = scenario.approved_hash
        finally:
            db.close()
            
        current_hash = hashlib.sha256(scenario.steps.encode("utf-8")).hexdigest()
        if approved_hash and current_hash != approved_hash:
            yield 'data: ' + json.dumps({"type": "error", "message": "ОШИБКА ИНТЕГРИТЕТА: Шаги сценария не совпадают с утвержденным хэшем! Выполнение заблокировано."}) + '\n\n'
            return

        yield 'data: ' + json.dumps({"type": "step", "step": "scenario_start", "message": f"Запуск сценария: {scenario.name}"}) + '\n\n'

        for i, step in enumerate(steps):
            tool_name = step.get("tool")
            args_template = step.get("args_template", {})
            description = step.get("description", f"Шаг {i+1}")

            # Простая подстановка переменных (например, {{query}})
            # В реальном приложении здесь можно использовать Jinja2 или langchain prompt template
            tool_args = {}
            for k, v in args_template.items():
                if isinstance(v, str) and "{{query}}" in v:
                    tool_args[k] = v.replace("{{query}}", query)
                else:
                    tool_args[k] = v

            yield 'data: ' + json.dumps({"type": "step", "step": f"scenario_step_{i}", "message": f"Сценарий ({i+1}/{len(steps)}): {description}"}) + '\n\n'
            
            if mode == "apprentice":
                yield 'data: ' + json.dumps({"type": "step", "step": f"apprentice_pause", "message": f"Apprentice: Ожидание решения по шагу {tool_name}"}) + '\n\n'
                
                decision_res = await self.llm_manager.execute_apprentice_step(
                    task_id, 
                    tool_name, 
                    tool_args, 
                    description, 
                    None
                )
                
                decision = decision_res["decision"]
                if decision == "rejected":
                    yield 'data: ' + json.dumps({"type": "step", "step": "rejected", "message": f"Шаг {tool_name} пропущен оператором."}) + '\n\n'
                    continue
                elif decision == "corrected":
                    if decision_res.get("corrected_args"):
                        tool_args = decision_res["corrected_args"]

            yield 'data: ' + json.dumps({"type": "step", "step": f"tool_{tool_name}", "message": f"Вызов: {tool_name}"}) + '\n\n'
            try:
                res = await self.mcp.call_tool(tool_name, tool_args)
                yield 'data: ' + json.dumps({"type": "step", "step": f"tool_result_{tool_name}", "message": f"Успешно: {res[:200]}..."}) + '\n\n'
            except Exception as e:
                logger.error(f"Error in scenario tool {tool_name}: {e}")
                yield 'data: ' + json.dumps({"type": "error", "message": f"Ошибка в шаге {tool_name}: {str(e)}"}) + '\n\n'
                break # Прерываем сценарий при ошибке

        yield 'data: ' + json.dumps({"type": "step", "step": "scenario_complete", "message": "Сценарий успешно завершен."}) + '\n\n'

