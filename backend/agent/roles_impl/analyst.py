import logging
import yaml
from pathlib import Path

logger = logging.getLogger("contextus.analyst_agent")

class AnalystAgent:
    """
    Выполняет анализ завершенных сессий (Meta-Analyst).
    Заменяет функцию run_meta_analyst.
    """
    def __init__(self, llm_manager):
        self.llm = llm_manager
        
    async def run(self, session_id: str) -> str:
        _BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
        logger.info(f"  🔍 Запуск Meta-Analyst для сессии {session_id}...")
        
        try:
            role_path = _BACKEND_DIR / "roles" / "meta_analyst.yaml"
            with open(role_path, "r", encoding="utf-8") as f:
                role_cfg = yaml.safe_load(f)
        except Exception as e:
            logger.error(f"  ❌ Ошибка загрузки meta_analyst.yaml: {e}")
            return f"Ошибка: {e}"

        model = self.llm.registry.resolve_model("meta_analyst")
        if not model:
            logger.error("  ❌ Нет доступных моделей для Meta-Analyst.")
            return "Модель недоступна."
            
        logger.info(f"  🤖 Meta-Analyst использует модель: {model.name}")

        allowed_tool_names = set(role_cfg.get("tools", []))
        all_tools = self.llm.mcp.get_tools_for_anthropic(None) if model.provider_type == "anthropic" else self.llm.mcp.get_tools_for_llm(None)
        tools = [t for t in all_tools if (t.get("name") if "name" in t else t.get("function", {}).get("name")) in allowed_tool_names]

        messages = [
            {"role": "system", "content": role_cfg.get("system_instruction", "")},
            {"role": "user", "content": f"session_id={session_id}. Выполни шаги 1-4 из алгоритма. Начни с вызова get_global_analytics()."}
        ]

        try:
            MAX_ITERATIONS = 6
            for iteration in range(MAX_ITERATIONS):
                logger.info(f"  🔄 Meta-Analyst итерация {iteration + 1}/{MAX_ITERATIONS}")
                response = await self.llm._chat(model, messages, tools=tools)
                if not response:
                    return "Не получен ответ от модели."

                tool_calls = self.llm._extract_tool_calls(model, response)

                if not tool_calls:
                    return self.llm._extract_text(model, response)

                messages.append(self.llm._build_assistant_msg(model, response))
                
                for tc in tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc.get("arguments", {})
                    try:
                        result = await self.llm.mcp.call_tool(tool_name, tool_args)
                        messages.append(self.llm._build_tool_result_msg(model, tc.get("id", tool_name), tool_name, str(result)))
                    except Exception as e:
                        messages.append(self.llm._build_tool_result_msg(model, tc.get("id", tool_name), tool_name, f"Error: {e}"))
            
            return "Аналитик превысил лимит итераций."
        except Exception as e:
            logger.error(f"  ❌ Ошибка Meta-Analyst: {e}")
            return f"Ошибка выполнения: {e}"
