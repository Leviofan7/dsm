import json
import logging
from typing import AsyncGenerator

logger = logging.getLogger("contextus.base_agent")

class BaseAgent:
    """
    Базовый класс для всех интерактивных агентов (Doorman, Planner, Worker).
    Инкапсулирует ReAct-цикл (Think -> Act -> Observe).
    """
    def __init__(self, llm_manager, role_cfg: dict, model_entry, tools: list):
        self.llm = llm_manager
        self.role_cfg = role_cfg
        self.model = model_entry
        self.tools = tools

    async def run_stream(self, query: str, history: list) -> AsyncGenerator[str, None]:
        """
        Основной цикл агента. Возвращает SSE-события.
        """
        system_prompt = self.role_cfg.get("system_instruction", "Ты — ИИ-агент.")
        messages = [{"role": "system", "content": system_prompt}]
        
        for msg in history:
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        
        if query:
            messages.append({"role": "user", "content": query})

        max_rounds = 10
        for _round in range(max_rounds):
            yield f'data: {json.dumps({"type": "step", "step": "think", "message": f"Думает ({self.model.name})..."})}\n\n'
            
            response = await self.llm._chat(self.model, messages, tools=self.tools)
            if not response:
                yield f'data: {json.dumps({"type": "error", "message": "Модель не вернула ответ"})}\n\n'
                return

            tool_calls = self.llm._extract_tool_calls(self.model, response)

            if not tool_calls:
                # Финальный текстовый ответ
                text = self.llm._extract_text(self.model, response)
                logger.info(f"BaseAgent _extract_text result: {repr(text)}")
                yield f'data: {json.dumps({"type": "result", "content": text})}\n\n'
                return

            # Обработка тул-коллов
            messages.append(self.llm._build_assistant_msg(self.model, response))
            
            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = tc.get("arguments", {})
                
                yield f'data: {json.dumps({"type": "step", "step": f"tool_{tool_name}", "message": f"Вызов: {tool_name}(...)"})}\n\n'
                
                # Специальный перехват для delegate_task
                if tool_name == "delegate_task":
                    yield f'data: {json.dumps({"type": "delegation", "agent": tool_args.get("target_agent"), "complexity": tool_args.get("complexity"), "prompt": tool_args.get("structured_prompt")})}\n\n'
                    return # Прерываем цикл Doorman'а, управление возвращается в llm_manager

                try:
                    result = await self.llm.mcp.call_tool(tool_name, tool_args)
                    messages.append(self.llm._build_tool_result_msg(self.model, tc.get("id", tool_name), tool_name, str(result)))
                except Exception as e:
                    messages.append(self.llm._build_tool_result_msg(self.model, tc.get("id", tool_name), tool_name, f"Error: {e}"))
