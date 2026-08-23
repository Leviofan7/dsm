"""
llm_manager.py — LLMManager (Model Registry + Intent Router + Tool Executor)

Центральный компонент Contextus 2.0:
  1. При инициализации загружает models.yaml (Model Registry).
  2. Опрашивает Ollama /api/tags для обнаружения локальных моделей.
  3. Проверяет наличие API-ключей в окружении для облачных провайдеров.
  4. Doorman-роутер классифицирует запрос и выбирает модель по цепочке.
  5. Автоматический fallback — если модель недоступна или зависла.
  6. Tool execution loop — при наличии MCP-инструментов обрабатывает tool_calls.
"""

import json
import os
import asyncio
import yaml
import httpx
import logging
from pathlib import Path
from typing import Any
import uuid

import time
from database import SessionLocal
from models import AgentTask, AgentSubtask, ExecutionTrace


from .mcp_manager import MCPManager
from .session_state import session_manager, SessionState
from services.dom_utils import calculate_dom_hash

logger = logging.getLogger("contextus.llm_manager")

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "models.yaml"
_BACKEND_DIR = Path(__file__).parent.parent.resolve()


# ═══════════════════════════════════════════════════════════════════
#  Model Registry
# ═══════════════════════════════════════════════════════════════════

class ModelEntry:
    """Один элемент реестра моделей."""
    __slots__ = (
        "name", "provider_name", "provider_type", "model_id",
        "context_window", "supports_tools", "supports_vision",
        "tags", "base_url", "api_key", "available",
    )

    def __init__(self, name: str, cfg: dict, provider_cfg: dict):
        self.name = name
        self.provider_name = cfg["provider"]
        self.provider_type = provider_cfg["type"]  # ollama | openai | anthropic
        self.model_id = cfg["model_id"]
        self.context_window = cfg.get("context_window", 4096)
        self.supports_tools = cfg.get("supports_tools", False)
        self.supports_vision = cfg.get("supports_vision", False)
        self.tags = cfg.get("tags", [])

        # Resolve base_url
        env_key = provider_cfg.get("base_url_env")
        defaults = {
            "ollama": "http://localhost:11434",
            "deepseek": "https://api.deepseek.com",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
        }
        self.base_url = os.getenv(env_key, defaults.get(self.provider_name, "")) if env_key else defaults.get(self.provider_name, "")

        # Resolve api_key
        key_env = provider_cfg.get("api_key_env")
        self.api_key = os.getenv(key_env, "") if key_env else ""

        # Availability flag — will be set during discovery
        self.available = False

    def __repr__(self):
        status = "✅" if self.available else "❌"
        return f"{status} {self.name} ({self.provider_type}/{self.model_id})"


class ModelRegistry:
    """Загружает models.yaml и управляет доступностью моделей."""

    def __init__(self):
        self.models: dict[str, ModelEntry] = {}
        self.routing: dict[str, list[str]] = {}
        self._raw_config: dict = {}

    def load(self):
        """Загрузка конфигурации из YAML."""
        if not _CONFIG_PATH.exists():
            logger.error(f"❌ Config not found: {_CONFIG_PATH}")
            return

        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            self._raw_config = yaml.safe_load(f)

        providers = self._raw_config.get("providers", {})
        models_cfg = self._raw_config.get("models", {})
        routing_cfg = self._raw_config.get("routing", {})

        # Build model entries
        for model_name, model_cfg in models_cfg.items():
            provider_name = model_cfg.get("provider", "")
            provider_cfg = providers.get(provider_name, {})
            entry = ModelEntry(model_name, model_cfg, provider_cfg)
            self.models[model_name] = entry

        # Build routing chains
        for task_name, task_cfg in routing_cfg.items():
            self.routing[task_name] = task_cfg.get("chain", [])

        logger.info(f"  📋 Загружено моделей: {len(self.models)}, маршрутов: {len(self.routing)}")

    async def discover_availability(self):
        """
        Проверяет доступность каждой модели:
        - Ollama: запрос к /api/tags
        - Cloud: проверка наличия API-ключа
        """
        logger.info("╔══════════════════════════════════════════╗")
        logger.info("║   Model Registry — ОБНАРУЖЕНИЕ          ║")
        logger.info("╚══════════════════════════════════════════╝")

        # 1. Получаем список моделей от Ollama
        ollama_models: set[str] = set()
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{ollama_url}/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    for m in data.get("models", []):
                        ollama_models.add(m.get("name", ""))
                    logger.info(f"  🟢 Ollama доступна. Моделей в Ollama: {len(ollama_models)}")
                else:
                    logger.warning(f"  🟡 Ollama ответила {resp.status_code}")
        except Exception as e:
            logger.warning(f"  🔴 Ollama недоступна: {e}")

        # 2. Проставляем доступность каждой модели
        for name, entry in self.models.items():
            if entry.provider_type == "ollama":
                # Проверяем точное совпадение model_id в списке Ollama
                entry.available = entry.model_id in ollama_models
                status = "✅ найдена" if entry.available else "❌ не найдена в Ollama"
                logger.info(f"  [{name}] {entry.model_id} → {status}")
            else:
                # Облачные: доступны, если есть API-ключ
                entry.available = bool(entry.api_key)
                status = "✅ ключ есть" if entry.available else "❌ API-ключ не задан"
                logger.info(f"  [{name}] {entry.provider_type}/{entry.model_id} → {status}")

        logger.info("  ─────────────────────────────────────────")

    def resolve_model(self, task: str) -> ModelEntry | None:
        """
        Выбирает первую доступную модель из цепочки для задачи.
        Это и есть автоматический fallback.
        """
        chain = self.routing.get(task, self.routing.get("general", []))
        for model_name in chain:
            entry = self.models.get(model_name)
            if entry and entry.available:
                return entry
        logger.error(f"  ❌ Ни одна модель не доступна для задачи '{task}'")
        return None


# ═══════════════════════════════════════════════════════════════════
#  LLMManager
# ═══════════════════════════════════════════════════════════════════

import re

class LLMManager:
    """
    LLMManager (Intent Router + Tool Executor) — «Швейцар» системы.

    При инициализации:
      - Загружает Model Registry из models.yaml
      - Опрашивает Ollama /api/tags
      - Проверяет API-ключи из .env
      - Запускает MCP Manager

    Методы:
      - route_intent(query) → классификация задачи
      - execute(query, task, tools) → полный цикл инференса с tool calling
    """

    def __init__(self):
        self.registry = ModelRegistry()
        self.mcp = MCPManager()
        self._initialized = False
        self._last_analyst_run: dict[str, float] = {}  # task_type -> timestamp
        # TODO(rate-limit): refine key to (task_type, error_sig) if false-negatives observed in production

    async def initialize(self):
        """Полная инициализация: загрузка конфига, обнаружение, запуск MCP."""
        if self._initialized:
            return

        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("  Contextus 2.0 — LLMManager INIT")
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # 1. Загрузка реестра
        self.registry.load()

        # 2. Обнаружение доступных моделей
        await self.registry.discover_availability()

        # 3. Запуск MCP-серверов
        await self.mcp.start()

        self._initialized = True
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("  ✅ LLMManager полностью инициализирован")
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    async def shutdown(self):
        """Graceful shutdown."""
        await self.mcp.shutdown()
        self._initialized = False

    # ── Doorman: классификация ─────────────────────────────────────

    async def route_intent(self, query: str, context: str = "") -> dict[str, Any]:
        """
        Doorman — быстрая классификация запроса.
        Выбирает модель из цепочки 'doorman' и отправляет запрос.
        """
        # ── Быстрый keyword-детектор (до LLM) ─────────────────────
        # Для очевидных случаев не тратим время на инференс Doorman.
        q_lower = query.lower()
        
        _RESEARCH_KEYWORDS = [
            "найди", "погугли", "загугли", "из инета", "в интернете",
            "из интернета", "в инете", "поищи", "ищи", "нагугли",
            "прогугли", "search", "google", "найти инфу", "дай инфу",
            "узнай", "выясни", "разузнай", "покажи инфу",
        ]
        _BROWSER_KEYWORDS = [
            "зайди на сайт", "открой сайт", "перейди на", "открой страницу",
            "зайди на ", "goto ", "navigate to",
        ]
        
        if any(kw in q_lower for kw in _RESEARCH_KEYWORDS):
            logger.info(f"  🚪 Doorman [KEYWORD]: research (быстрый детект)")
            return {"task_type": "research", "role": "default", "complexity": "low"}
        
        if any(kw in q_lower for kw in _BROWSER_KEYWORDS):
            logger.info(f"  🚪 Doorman [KEYWORD]: browser_automation (быстрый детект)")
            return {"task_type": "browser_automation", "role": "default", "complexity": "low"}

        # ── LLM Doorman (для неочевидных случаев) ─────────────────
        model = self.registry.resolve_model("doorman")
        if not model:
            return {"task_type": "general", "role": "default", "complexity": "low", "error": "no doorman model"}

        prompt = (
            "Ты — системный маршрутизатор (Doorman).\n"
            "Классифицируй входящий запрос и верни СТРОГИЙ JSON:\n"
            "{\n"
            '  "task_type": "coding|research|general|browser_automation|rag|planning",\n'
            '  "role": "news_extractor|code_architect|default",\n'
            '  "complexity": "low|high"\n'
            "}\n\n"
            "ВАЖНО: Если пользователь просит найти, узнать или поискать информацию — task_type ОБЯЗАТЕЛЬНО 'research'.\n"
            "Если пользователь просит зайти на конкретный сайт — task_type 'browser_automation'.\n"
            "Если задача связана с написанием кода, скриптов, SQL, настройкой серверов, фоновыми задачами, cron или системным администрированием — task_type ОБЯЗАТЕЛЬНО 'coding'.\n\n"
            f"Контекст: {context}\n"
            f"Запрос: {query}"
        )

        try:
            raw = await self._raw_generate(model, prompt, json_mode=True)
            result = json.loads(raw)
            # Валидация полей
            result.setdefault("task_type", "general")
            result.setdefault("role", "default")
            result.setdefault("complexity", "low")
            logger.info(f"  🚪 Doorman [{model.name}]: {result}")
            return result
        except Exception as e:
            logger.error(f"  ❌ Doorman error ({model.name}): {e}")

            # Fallback: пробуем следующую модель в цепочке
            chain = self.registry.routing.get("doorman", [])
            for fallback_name in chain:
                if fallback_name == model.name:
                    continue
                fb = self.registry.models.get(fallback_name)
                if fb and fb.available:
                    logger.info(f"  🔄 Doorman fallback → {fb.name}")
                    try:
                        raw = await self._raw_generate(fb, prompt, json_mode=True)
                        result = json.loads(raw)
                        result.setdefault("task_type", "general")
                        result.setdefault("role", "default")
                        result.setdefault("complexity", "low")
                        return result
                    except Exception:
                        continue

            return {"task_type": "general", "role": "default", "complexity": "low", "error": str(e)}


    async def execute_apprentice_step(self, session_id: str, proposed_tool: str | None, proposed_args: dict | None, proposed_reasoning: str, proposed_response_text: str | None) -> dict:
        """
        Создает ApprenticeStep и ждет решения человека.
        Возвращает {'decision': '...', 'corrected_args': ...}
        """
        from database import SessionLocal
        from models import ApprenticeStep
        import json
        import asyncio
        
        db = SessionLocal()
        try:
            step = ApprenticeStep(
                session_id=session_id,
                proposed_tool=proposed_tool,
                proposed_args=json.dumps(proposed_args) if proposed_args else None,
                proposed_reasoning=proposed_reasoning,
                proposed_response_text=proposed_response_text
            )
            db.add(step)
            db.commit()
            db.refresh(step)
            step_id = step.id
        finally:
            db.close()
            
        # Ожидание решения
        while True:
            await asyncio.sleep(1.0)
            db = SessionLocal()
            try:
                current_step = db.query(ApprenticeStep).filter(ApprenticeStep.id == step_id).first()
                if current_step and current_step.human_decision:
                    return {
                        "decision": current_step.human_decision,
                        "corrected_args": json.loads(current_step.corrected_args) if current_step.corrected_args else None
                    }
            finally:
                db.close()

    # ── Execute Stream: полный цикл с выдачей SSE-событий ──────────


    async def execute_stream(
        self,
        query: str,
        accounts: list | None = None,
        history: list | None = None,
        allow_browser: bool = True,
        debug_mode: bool = False,
        source_ids: list | None = None,
        max_tool_rounds: int = 15,
        attached_folders: list[str] | None = None,
        task_id: str | None = None,
        target_agent: str | None = None,
        mode: str | None = None,
    ):
        accounts = accounts or []
        history = history or []
        source_ids = source_ids or []
        attached_folders = attached_folders or []

        if target_agent and target_agent.startswith("scenario_"):
            from agent.scenario_executor import LinearScenarioExecutor
            executor = LinearScenarioExecutor(self.mcp, self)
            async for event in executor.execute_scenario(task_id, target_agent, query, mode):
                yield event
            return
            
        if target_agent and target_agent != "auto":
            task_type = target_agent
            yield 'data: ' + json.dumps({"type": "step", "step": "doorman_skipped", "message": f"Ручной выбор агента: {target_agent}. Doorman пропущен."}) + '\n\n'
        else:
            yield 'data: ' + json.dumps({"type": "step", "step": "init", "message": "Оценка задачи и маршрутизация (Doorman)..."}) + '\n\n'
            intent = await self.route_intent(query)
            task_type = intent.get("task_type", "general")
            yield 'data: ' + json.dumps({"type": "step", "step": "doorman_ok", "message": f"Задача классифицирована: {task_type}. Подготовка контекста..."}) + '\n\n'


        model = self.registry.resolve_model(task_type)
        if not model:
            yield 'data: ' + json.dumps({"type": "error", "message": f"Ошибка: нет доступной модели для задачи '{task_type}'."}) + '\n\n'
            return

        yield 'data: ' + json.dumps({"type": "step", "step": "model_selected", "message": f"Выбрана модель: {model.name} ({model.provider_type})"}) + '\n\n'

        if history:
            query = await self._reformulate_query(model, history, query)

        # Context Injection
        system_context_blocks = []
        github_source_ids = []
        
        if source_ids:
            from database import SessionLocal
            from models import Source
            db = SessionLocal()
            try:
                sources = db.query(Source).filter(Source.id.in_(source_ids)).all()
                for s in sources:
                    if s.type == "local":
                        from services.folder_service import build_context
                        ctx_res = build_context(s.detail)
                        if ctx_res.get("mode") != "error":
                            system_context_blocks.append(ctx_res["content"])
                    else:
                        github_source_ids.append(s.id)
            finally:
                db.close()

        if github_source_ids:
            try:
                rag_result = await self.mcp.call_tool("search_knowledge_base", {"query": query, "source_ids": github_source_ids, "top_k": 7})
                if rag_result and "Данные не найдены" not in str(rag_result) and '{"results": []}' not in str(rag_result):
                    system_context_blocks.append(
                        f"=== КОНТЕКСТ ИЗ GITHUB ===\n{rag_result}\n=== КОНЕЦ КОНТЕКСТА GITHUB ===\n"
                    )
            except Exception as e:
                pass

        role_cfg = None
        role_path = _BACKEND_DIR / "roles" / f"{task_type}.yaml"
        if role_path.exists():
            import yaml
            try:
                with open(role_path, "r", encoding="utf-8") as f:
                    role_cfg = yaml.safe_load(f)
            except Exception as e:
                logger.error(f"Ошибка загрузки роли {task_type}: {e}")

        has_tools = model.supports_tools and self.mcp.is_started
        tools = None
        allowed_tool_names = None

        if has_tools:
            all_tools = self.mcp.get_tools_for_anthropic() if model.provider_type == "anthropic" else self.mcp.get_tools_for_llm()
            if role_cfg and "tools" in role_cfg:
                allowed_tool_names = set(role_cfg["tools"])
                tools = [t for t in all_tools if (t["name"] if model.provider_type == "anthropic" else t["function"]["name"]) in allowed_tool_names]
            else:
                tools = all_tools

        if role_cfg and "system_instruction" in role_cfg:
            system_prompt = role_cfg["system_instruction"]
        else:
            system_prompt = "Ты — автономный ИИ-агент Contextus. Отвечай на языке пользователя.\n"
            if has_tools:
                tool_names_list = [t["name"] if model.provider_type == "anthropic" else t["function"]["name"] for t in (tools or []) if (t["name"] if model.provider_type == "anthropic" else t["function"]["name"]) != "get_raw_html"]
                system_prompt += f"ДОСТУПНЫЕ ИНСТРУМЕНТЫ: {', '.join(tool_names_list)}\n"
        messages = []
        
        if model.provider_type == "anthropic" and system_context_blocks:
            messages.append({
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": system_prompt
                    },
                    {
                        "type": "text",
                        "text": "\n\n" + "\n\n".join(system_context_blocks),
                        "cache_control": {"type": "ephemeral"}
                    }
                ]
            })
        else:
            if system_context_blocks:
                system_prompt += "\n\n" + "\n\n".join(system_context_blocks)
            messages.append({"role": "system", "content": system_prompt})
        for msg in history:
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        messages.append({"role": "user", "content": query})

        # Allow roles to bypass planner if they explicitly set planner: false
        use_planner = True
        if role_cfg and role_cfg.get("planner") is False:
            use_planner = False

        if has_tools and task_id and use_planner:
            # Planner Phase
            yield 'data: ' + json.dumps({"type": "step", "step": "planning", "message": "Планирование графа задач (Planner)..."}) + '\n\n'
            
            # Получаем список доступных ролей для Оркестратора
            available_roles = []
            roles_dir = _BACKEND_DIR / "roles"
            if roles_dir.exists():
                for role_file in roles_dir.glob("*.yaml"):
                    role_name = role_file.stem
                    try:
                        with open(role_file, "r", encoding="utf-8") as f:
                            r_cfg = yaml.safe_load(f)
                            r_desc = r_cfg.get("description", "")
                            available_roles.append(f"- {role_name}: {r_desc}")
                    except:
                        pass
            roles_text = "\n".join(available_roles) if available_roles else "- general: Базовый агент"

            plan_messages = messages.copy()
            plan_messages.append({
                "role": "user",
                "content": (
                    "Сформируй план выполнения задачи в виде JSON-массива подзадач (графа выполнения).\n"
                    "Для каждой подзадачи выбери наиболее подходящую роль (target_role) из списка доступных.\n\n"
                    f"ДОСТУПНЫЕ РОЛИ:\n{roles_text}\n\n"
                    "Каждый элемент массива должен содержать:\n"
                    " - 'topic': краткое название шага (строка)\n"
                    " - 'prompt_instruction': подробная инструкция для выбранной роли, включающая нужный контекст\n"
                    " - 'target_role': ID выбранной роли (из списка выше)\n\n"
                    "Верни ТОЛЬКО валидный JSON-массив, без markdown блоков."
                )
            })
            
            t0 = time.perf_counter()
            plan_response = await self._chat(model, plan_messages, tools=None)
            t1 = time.perf_counter()
            
            plan_text = self._extract_text(model, plan_response) if plan_response else "[]"
            plan_text = plan_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            
            try:
                steps = json.loads(plan_text)
                if not isinstance(steps, list): steps = [{"topic": "main task", "prompt_instruction": query}]
            except:
                steps = [{"topic": "main task", "prompt_instruction": query}]
            
            # Save to DB
            db = SessionLocal()
            try:
                order = 0
                for step in steps:
                    st = AgentSubtask(
                        task_id=task_id,
                        topic=step.get("topic", f"Step {order}"),
                        prompt_instruction=step.get("prompt_instruction", ""),
                        target_role=step.get("target_role", "general"),
                        execution_order=order,
                        status="pending"
                    )
                    db.add(st)
                    order += 1
                db.commit()
            except Exception as e:
                logger.error(f"Error saving subtasks: {e}")
            finally:
                db.close()
                
            yield 'data: ' + json.dumps({"type": "step", "step": "plan_ready", "message": "План составлен! Начинаю выполнение (Executor)..."}) + '\n\n'

            # Executor Phase
            db = SessionLocal()
            subtasks = db.query(AgentSubtask).filter(AgentSubtask.task_id == task_id).order_by(AgentSubtask.execution_order).all()
            db.close()
            
            all_results = []
            PRIVILEGED_TOOLS = {"run_terminal_command", "write_file", "run_claude_coder"}
            
            for st in subtasks:
                target_role = getattr(st, "target_role", "general") or "general"
                
                # Загрузка роли
                role_cfg = None
                role_path = _BACKEND_DIR / "roles" / f"{target_role}.yaml"
                if not role_path.exists():
                    logger.warning(f"Роль {target_role} не найдена, откат на general")
                    target_role = "general"
                else:
                    try:
                        import yaml
                        with open(role_path, "r", encoding="utf-8") as f:
                            role_cfg = yaml.safe_load(f)
                    except Exception as e:
                        logger.error(f"Ошибка загрузки роли {target_role}: {e}")
                        target_role = "general"

                # Выбор модели
                st_model = self.registry.resolve_model(target_role)
                if not st_model:
                    st_model = model

                st_has_tools = st_model.supports_tools and self.mcp.is_started
                st_tools = None
                
                if st_has_tools:
                    all_tools = self.mcp.get_tools_for_anthropic() if st_model.provider_type == "anthropic" else self.mcp.get_tools_for_llm()
                    if role_cfg and "tools" in role_cfg:
                        allowed_tool_names = set(role_cfg["tools"])
                        # Принудительная двойная фильтрация PRIVILEGED_TOOLS
                        allowed_tool_names = allowed_tool_names - PRIVILEGED_TOOLS
                        st_tools = [t for t in all_tools if (t["name"] if st_model.provider_type == "anthropic" else t["function"]["name"]) in allowed_tool_names]
                    else:
                        # Если инструменты не заданы явно (например, general), всё равно фильтруем опасные
                        st_tools = [t for t in all_tools if (t["name"] if st_model.provider_type == "anthropic" else t["function"]["name"]) not in PRIVILEGED_TOOLS]

                st_sys_prompt = role_cfg.get("system_instruction", "Ты — автономный ИИ-агент Contextus. Отвечай на языке пользователя.\n") if role_cfg else "Ты — автономный ИИ-агент Contextus. Отвечай на языке пользователя.\n"
                
                if st_has_tools:
                    tool_names_list = [t["name"] if st_model.provider_type == "anthropic" else t["function"]["name"] for t in (st_tools or []) if (t["name"] if st_model.provider_type == "anthropic" else t["function"]["name"]) != "get_raw_html"]
                    st_sys_prompt += f"\nДОСТУПНЫЕ ИНСТРУМЕНТЫ: {', '.join(tool_names_list)}\n"

                # Инжект контекста от предыдущих шагов
                if all_results:
                    st_sys_prompt += "\n\n=== РЕЗУЛЬТАТЫ ПРЕДЫДУЩИХ ШАГОВ ===\n" + "\n\n".join(all_results) + "\n===================================\n"

                exec_messages = [{"role": "system", "content": st_sys_prompt}]
                # История чата (исключаем системный промпт Planner'а)
                for msg in history:
                    exec_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
                
                exec_messages.append({"role": "user", "content": f"Твоя задача (роль {target_role}): {st.topic}\nИнструкция: {st.prompt_instruction}"})

                yield 'data: ' + json.dumps({"type": "step", "step": f"subtask_{st.id}", "message": f"Выполняю шаг: {st.topic} (Агент: {target_role})"}) + '\n\n'
                
                t_start = time.perf_counter()
                st_result = "Успешно"
                tool_calls = None
                is_failed = False
                stub_response = False
                st_resp = None

                # ── Tool round loop: модель работает пока не вернёт ответ без tool_calls ──
                for _st_round in range(max_tool_rounds):
                    st_resp = await self._chat(st_model, exec_messages, tools=st_tools)
                    t_end = time.perf_counter()

                    if not st_resp:
                        is_failed = True
                        st_result = "Ошибка: Агент не смог сгенерировать ответ"
                        break

                    tool_calls = self._extract_tool_calls(st_model, st_resp)

                    if not tool_calls:
                        # Финальный текстовый ответ subtask'а
                        st_result = self._extract_text(st_model, st_resp)
                        if not st_result or st_result.strip() == "":
                            is_failed = True
                            st_result = "Ошибка: Агент вернул пустой ответ"
                        if self._detect_stub(st_result):
                            is_failed = True
                            stub_response = True
                        break

                    # Есть tool_calls — обрабатываем
                    exec_messages.append(self._build_assistant_msg(st_model, st_resp))
                    corrected_mark = False

                    for tc in tool_calls:
                        tool_name = tc["name"]
                        tool_args = tc["arguments"]

                        # ── Runtime allowlist: блокируем привилегированные инструменты
                        if tool_name in PRIVILEGED_TOOLS:
                            blocked_msg = f"ЗАБЛОКИРОВАНО: инструмент {tool_name} запрещён для роли {target_role}"
                            logger.warning(f"⛔ Заблокирована попытка вызова {tool_name} ролью {target_role}, session={task_id}")
                            st_result += f"\nTool {tool_name} BLOCKED: {blocked_msg}"
                            yield 'data: ' + json.dumps({"type": "step", "step": "blocked", "message": f"Заблокировано: {tool_name} — привилегированный инструмент"}) + '\n\n'
                            exec_messages.append(self._build_tool_result_msg(st_model, tc.get("id", tool_name), tool_name, blocked_msg))
                            continue

                        yield 'data: ' + json.dumps({"type": "step", "step": f"tool_{tool_name}", "message": f"Вызов: {tool_name}(...)"}) + '\n\n'

                        if mode == "apprentice":
                            yield 'data: ' + json.dumps({"type": "apprentice_request", "tool": tool_name, "args": tool_args}) + '\n\n'

                            db_session = SessionLocal()
                            apprentice_session_id = task_id
                            try:
                                task_obj = db_session.query(AgentTask).filter(AgentTask.id == task_id).first()
                                if task_obj and task_obj.conversation_id:
                                    apprentice_session_id = task_obj.conversation_id
                            finally:
                                db_session.close()

                            decision_res = await self.execute_apprentice_step(apprentice_session_id, tool_name, tool_args, f"Хочу вызвать {tool_name}", None)
                            decision = decision_res["decision"]

                            if decision == "rejected":
                                sys_msg = {"role": "user", "content": f"[System: Вызов инструмента {tool_name} был отклонен оператором. Попробуй альтернативный путь решения задачи.]"}
                                exec_messages.append(sys_msg)
                                yield 'data: ' + json.dumps({"type": "step", "step": "rejected", "message": f"Вызов {tool_name} отклонен. Модель ищет обходной путь."}) + '\n\n'
                                continue
                            elif decision == "corrected":
                                if decision_res.get("corrected_args"):
                                    tool_args = decision_res["corrected_args"]
                                    corrected_mark = True

                        try:
                            res = await self.mcp.call_tool(tool_name, tool_args)
                            if mode == "apprentice" and corrected_mark:
                                corrected_note = f"[System: Оператор скорректировал твои аргументы для инструмента {tool_name}. Ниже представлен результат выполнения с учетом правок.]\n"
                                res = corrected_note + res

                            exec_messages.append(self._build_tool_result_msg(st_model, tc.get("id", tool_name), tool_name, res))
                            res_preview = res[:200].replace('\n', ' ') + ('...' if len(res) > 200 else '')
                            yield 'data: ' + json.dumps({"type": "step", "step": f"tool_result_{tool_name}", "message": f"Результат {tool_name}: {res_preview}"}) + '\n\n'
                            st_result += f"\nTool {tool_name} Result: {res[:500]}"
                        except Exception as e:
                            err_str = f"Ошибка: {e}"
                            exec_messages.append(self._build_tool_result_msg(st_model, tc.get("id", tool_name), tool_name, err_str))
                            st_result += f"\nTool {tool_name} Error: {e}"
                            is_failed = True
                    # Продолжаем loop — модель увидит результаты инструментов и напишет финальный ответ
                else:
                    # Превышен max_tool_rounds — принудительное завершение
                    is_failed = True
                    st_result = st_result or "Ошибка: превышен лимит раундов инструментов"

                
                all_results.append(f"Шаг '{st.topic}' (Роль {target_role}):\n{st_result}")
                
                db_session = SessionLocal()
                try:
                    db_st = db_session.query(AgentSubtask).filter(AgentSubtask.id == st.id).first()
                    if db_st:
                        db_st.status = "failed" if is_failed else "completed"
                        db_st.result_output = st_result
                        
                    trace = ExecutionTrace(
                        session_id=task_id,
                        task_id=task_id,
                        task_type_classified=task_type,
                        task_type_final=target_role,
                        model_used=st_model.name,
                        model_selected=st_model.name,
                        planner_enabled=True,
                        tools_available=len(st_tools) if st_tools else 0,
                        tools_called=len(tool_calls) if tool_calls else 0,
                        tools_called_names=json.dumps([tc["name"] for tc in tool_calls] if tool_calls else [], ensure_ascii=False),
                        stage_durations=json.dumps({"executor": int((t_end - t_start) * 1000)}),
                        duration_ms=int((t_end - t_start) * 1000),
                        actions_log=json.dumps([tc["name"] for tc in tool_calls] if tool_calls else [], ensure_ascii=False),
                        final_status="stub_response" if stub_response else ("failed" if is_failed else "success"),
                        tool_verified=None,
                        tool_verification_details=None
                    )
                    db_session.add(trace)
                    db_session.commit()
                except Exception as e:
                    logger.error(f"Error saving subtask status: {e}")
                finally:
                    db_session.close()

                if is_failed:
                    yield 'data: ' + json.dumps({"type": "error", "message": f"Шаг '{st.topic}' ({target_role}) завершился с ошибкой. Выполнение графа прервано."}) + '\n\n'
                    return

            # Aggregator Phase
            yield 'data: ' + json.dumps({"type": "step", "step": "aggregating", "message": "Сборка финального ответа (Aggregator)..."}) + '\n\n'
            agg_messages = messages.copy()
            agg_messages.append({
                "role": "user",
                "content": "Вот результаты выполнения всех шагов:\n" + "\n".join(all_results) + "\nСформируй финальный ответ."
            })
            
            final_resp = await self._chat(model, agg_messages, tools=None)
            final_text = self._extract_text(model, final_resp) if final_resp else ""

            # Fallback: если агрегатор вернул пустую строку — показываем сырые результаты шагов
            if not final_text or not final_text.strip():
                final_text = "\n\n".join(all_results) if all_results else "Задача выполнена."
            
            yield 'data: ' + json.dumps({"type": "step", "step": "success", "message": "Задача завершена!"}) + '\n\n'
            for i in range(0, len(final_text), 5):
                chunk = final_text[i:i+5]
                yield 'data: ' + json.dumps({"type": "result", "content": chunk}) + '\n\n'
                await asyncio.sleep(0.03)
            return


        # Fallback to standard execution if no tools/not planning
        yield 'data: ' + json.dumps({"type": "step", "step": "success", "message": "Переход к стандартному циклу..."}) + '\n\n'
        
        final_text = "ОК"
        error_hashes = []
        for round_num in range(max_tool_rounds):
            yield 'data: ' + json.dumps({"type": "step", "step": "generating", "message": f"Ожидание ответа от {model.name} (генерация может занять до минуты)..."}) + '\n\n'
            resp = None
            ollama_streamed_content = ""  # Track content streamed live for Ollama
            if model.provider_type == "ollama":
                try:
                    async for chunk_type, chunk_data in self._chat_ollama_stream(model, messages, tools):
                        if chunk_type == "thought_chunk":
                            ollama_streamed_content += chunk_data
                            yield 'data: ' + json.dumps({"type": "thought_chunk", "content": chunk_data}) + '\n\n'
                        elif chunk_type == "final":
                            resp = chunk_data
                except Exception as e:
                    logger.error(f"Ollama stream error: {e}")
                    resp = None
            else:
                resp = await self._chat(model, messages, tools=tools)
                
            if not resp:
                final_text = "Ошибка: модель не ответила"
                break
                
            tool_calls = self._extract_tool_calls(model, resp)
            
            agent_thought = self._extract_text(model, resp)
            if agent_thought and agent_thought.strip() and model.provider_type != "ollama":
                yield 'data: ' + json.dumps({"type": "step", "step": "thought", "message": f"🤔 {agent_thought.strip()}"}) + '\n\n'

            if not tool_calls:
                if model.provider_type == "ollama" and ollama_streamed_content:
                    # Already streamed as thought_chunks — re-emit as result so it shows in the message bubble
                    final_text = ollama_streamed_content
                else:
                    final_text = agent_thought or "ОК"

                # Apprentice Mode: запрашиваем одобрение даже для прямых текстовых ответов
                if mode == "apprentice" and task_id and final_text:
                    yield 'data: ' + json.dumps({"type": "apprentice_request", "tool": None, "args": None, "response_preview": final_text[:300]}) + '\n\n'
                    db_session = SessionLocal()
                    apprentice_session_id = task_id
                    try:
                        task_obj = db_session.query(AgentTask).filter(AgentTask.id == task_id).first()
                        if task_obj and task_obj.conversation_id:
                            apprentice_session_id = task_obj.conversation_id
                    finally:
                        db_session.close()
                    decision_res = await self.execute_apprentice_step(
                        apprentice_session_id, None, None,
                        "Агент хочет отправить финальный ответ", final_text
                    )
                    if decision_res["decision"] == "rejected":
                        final_text = "[Ответ отклонён оператором. Агент завершил работу.]"
                    elif decision_res["decision"] == "corrected" and decision_res.get("corrected_args"):
                        # corrected_args здесь нет, но на будущее — можно передать corrected text
                        pass

                if model.provider_type == "ollama" and ollama_streamed_content:
                    yield 'data: ' + json.dumps({"type": "result", "content": final_text}) + '\n\n'
                break

                
            messages.append(self._build_assistant_msg(model, resp))
            
            for tc in tool_calls:
                tool_name = tc.get("name")
                if not tool_name:
                    continue
                tool_args = tc.get("arguments", {})
                t_id = tc.get("id", tool_name)
                
                if tool_name in PRIVILEGED_TOOLS:
                    yield 'data: ' + json.dumps({"type": "step", "step": "blocked", "message": f"Заблокировано: {tool_name} — привилегированный инструмент"}) + '\n\n'
                    messages.append(self._build_tool_result_msg(model, t_id, tool_name, "ОШИБКА: Этот инструмент запрещен для использования в текущем режиме."))
                    continue
                    
                yield 'data: ' + json.dumps({"type": "step", "step": f"tool_{tool_name}", "message": f"Вызов: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})"}) + '\n\n'
                
                corrected_mark = False
                if mode == "apprentice" and task_id:
                    # Emit a dedicated event so the frontend renders an approval card
                    yield 'data: ' + json.dumps({"type": "apprentice_request", "tool": tool_name, "args": tool_args}) + '\n\n'
                    
                    db_session = SessionLocal()
                    apprentice_session_id = task_id
                    try:
                        task_obj = db_session.query(AgentTask).filter(AgentTask.id == task_id).first()
                        if task_obj and task_obj.conversation_id:
                            apprentice_session_id = task_obj.conversation_id
                    finally:
                        db_session.close()
                    
                    decision_res = await self.execute_apprentice_step(apprentice_session_id, tool_name, tool_args, f"Хочу вызвать {tool_name}", None)
                    decision = decision_res["decision"]
                    
                    if decision == "rejected":
                        sys_msg = {"role": "user", "content": f"[System: Вызов инструмента {tool_name} был отклонен оператором. Попробуй альтернативный путь решения задачи.]"}
                        messages.append(sys_msg)
                        yield 'data: ' + json.dumps({"type": "step", "step": "rejected", "message": f"Вызов {tool_name} отклонен. Модель ищет обходной путь."}) + '\n\n'
                        continue
                    elif decision == "corrected":
                        if decision_res.get("corrected_args"):
                            tool_args = decision_res["corrected_args"]
                            corrected_mark = True

                try:
                    res = await self.mcp.call_tool(tool_name, tool_args)
                    if mode == "apprentice" and corrected_mark:
                        res = f"[System: Оператор скорректировал твои аргументы для инструмента {tool_name}. Ниже представлен результат выполнения с учетом правок.]\n" + res
                    
                    error_hashes.clear() # Reset on success
                    messages.append(self._build_tool_result_msg(model, t_id, tool_name, res))
                    
                    # Truncate response for UI rendering
                    res_preview = res[:200].replace('\n', ' ') + ('...' if len(res) > 200 else '')
                    yield 'data: ' + json.dumps({"type": "step", "step": f"tool_result_{tool_name}", "message": f"Результат {tool_name}: {res_preview}"}) + '\n\n'
                except Exception as e:
                    import hashlib
                    err_str = f"Ошибка: {e}"
                    err_hash = hashlib.sha256(err_str.encode("utf-8")).hexdigest()
                    error_hashes.append(err_hash)
                    
                    if len(error_hashes) >= 3 and all(h == error_hashes[0] for h in error_hashes[-3:]):
                        yield 'data: ' + json.dumps({"type": "error", "message": "Обнаружено зацикливание ошибки! Принудительная пауза."}) + '\n\n'
                        messages.append(self._build_tool_result_msg(model, t_id, tool_name, err_str + "\n[System: ОБНАРУЖЕНО ЗАЦИКЛИВАНИЕ! Прервите текущую линию действий.]"))
                    else:
                        messages.append(self._build_tool_result_msg(model, t_id, tool_name, err_str))

        for i in range(0, len(final_text), 5):
            chunk = final_text[i:i+5]
            yield 'data: ' + json.dumps({"type": "result", "content": chunk}) + '\n\n'
            await asyncio.sleep(0.03)

    async def execute(
        self,
        query: str,
        task_type: str = "general",
        system_prompt: str = "",
        history: list[dict] | None = None,
        max_tool_rounds: int = 15,
        chat_id: int | None = None,
    ) -> str:
        """
        Полный цикл инференса с поддержкой HITL (Human-in-the-Loop) и Supervisor:
        1. Выбирает модель из routing[task_type].
        2. Если модель поддерживает tools — прикрепляет MCP-инструменты.
        3. Обрабатывает tool_calls в цикле.
        4. Если обнаружено зацикливание или вызов request_human_help, переводит сессию в ожидание.
        """
        model = self.registry.resolve_model(task_type)
        if not model:
            return f"Ошибка: нет доступной модели для задачи '{task_type}'."

        t_start = time.perf_counter()
        tools_called = 0
        tools_called_names = []
        
        # State for verification
        tool_verified = None
        tool_verification_details = None

        logger.info(f"  🎯 Execute [{task_type}] → модель: {model.name}")

        # Инициализация сессии (если есть chat_id, создаем/используем SessionManager)
        session = None
        if chat_id:
            session = session_manager.create_session(chat_id, str(uuid.uuid4()))
            session.model_name = model.name
            session.task_type = task_type

        # Собираем инструменты (только если модель поддерживает tool calling)
        tools = None
        has_tools = model.supports_tools and self.mcp.is_started
        if has_tools:
            if model.provider_type == "anthropic":
                tools = self.mcp.get_tools_for_anthropic()
            else:
                tools = self.mcp.get_tools_for_llm()
            logger.info(f"  🔧 Прикреплено инструментов: {len(tools)}")
            if session:
                session.tools = tools

        # Добавляем инструкции по инструментам и Supervisor
        tool_prompt = ""
        if has_tools:
            tool_prompt = (
                "\n\n[CRITICAL INSTRUCTION] Тебе доступны внешние инструменты (tools) для выполнения ЛЮБЫХ системных, поисковых или исследовательских задач. "
                "ОБЯЗАТЕЛЬНО используй инструменты для: получения информации из интернета, проверки фоновых задач (cron, systemd), выполнения скриптов, и работы с файловой системой. "
                "НИКОГДА не отказывайся от выполнения задачи со словами 'я не имею прямого доступа', 'я не могу показать', 'я языковая модель'. "
                "Если пользователь просит посмотреть задачи или добавить крон, ИСПОЛЬЗУЙ ИНСТРУМЕНТЫ. Твой ответ должен быть результатом работы инструментов."
            )
        else:
            tool_prompt = (
                "\n\n[CRITICAL INSTRUCTION] У тебя НЕТ доступа к инструментам. "
                "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО описывать действия, которые ты якобы выполняешь. "
                "Честно скажи, что у тебя нет доступа."
            )

        supervisor_prompt = (
            " \n\n[SUPERVISOR INSTRUCTION] Если ты видишь на странице чекбокс Cloudflare, "
            "капчу Google (reCAPTCHA) или любое другое окно блокировки, которое ты не можешь пройти кликом, "
            "не пытайся циклиться. Немедленно вызывай инструмент `wait_for_human_captcha`. "
            "После его завершения продолжай выполнение сценария с той же открытой страницы."
        )
        system_prompt = (system_prompt or "") + tool_prompt + supervisor_prompt
        
        # Собираем messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for msg in (history or []):
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        messages.append({"role": "user", "content": query})
        
        if session:
            session.messages = messages


        # ── Tool execution loop ────────────────────────────────────
        for round_num in range(max_tool_rounds):
            
            # --- Блок HITL паузы ---
            if session and session.state == SessionState.WAITING_FOR_HUMAN:
                logger.info(f"  ⏳ Ожидание человека (сессия {session.session_id})...")
                await session.pending_event.wait()
                logger.info(f"  ▶️ Продолжение сессии после ответа человека: {session.human_response}")
                # Добавляем ответ человека как tool_result или user message (для контекста)
                messages.append({"role": "user", "content": f"[ОТВЕТ ОТ ЧЕЛОВЕКА]: {session.human_response}"})
            # -------------------------
            
            response = await self._chat(model, messages, tools=tools)

            if response is None:
                # Модель зависла — пробуем fallback
                fallback = self._get_fallback(model, task_type)
                if fallback:
                    logger.warning(f"  🔄 Fallback: {model.name} → {fallback.name}")
                    model = fallback
                    # Пересобираем tools для нового провайдера
                    if model.supports_tools and self.mcp.is_started:
                        if model.provider_type == "anthropic":
                            tools = self.mcp.get_tools_for_anthropic()
                        else:
                            tools = self.mcp.get_tools_for_llm()
                    else:
                        tools = None
                    response = await self._chat(model, messages, tools=tools)
                    if response is None:
                        if session:
                            session.complete()
                            session_manager.remove_session(chat_id)
                        self._record_trace(session, chat_id, task_type, task_type, model, tools, tools_called, tools_called_names, t_start, "failure")
                        return "Ошибка: все модели недоступны."

                else:
                    if session:
                        session.complete()
                        session_manager.remove_session(chat_id)
                    self._record_trace(session, chat_id, task_type, task_type, model, tools, tools_called, tools_called_names, t_start, "failure", tool_verified, tool_verification_details)
                    return "Ошибка: модель не ответила и нет fallback."

            # Проверяем: есть ли tool_calls?
            tool_calls = self._extract_tool_calls(model, response)
            if not tool_calls:
                # ── КОНТРОЛЁР: Принудительный вызов инструмента ─────────
                # Если задача research/browser_automation, модель имеет инструменты,
                # но НЕ вызвала ни одного на первом раунде — это галлюцинация.
                # Контролёр перехватывает и принудительно запускает web_search.
                _TOOL_REQUIRED_TASKS = {"research", "browser_automation"}
                if round_num == 0 and task_type in _TOOL_REQUIRED_TASKS and has_tools:
                    logger.warning(f"  🎛️ КОНТРОЛЁР: Модель не вызвала инструменты для задачи '{task_type}'. Принудительный web_search.")
                    
                    # Выполняем web_search с запросом пользователя
                    try:
                        search_result = await self.mcp.call_tool("web_search", {"query": query})
                    except Exception as e:
                        logger.error(f"  ❌ КОНТРОЛЁР: web_search failed: {e}")
                        search_result = f"Ошибка поиска: {e}"
                    
                    # Ограничиваем размер результата
                    if len(search_result) > 6000:
                        search_result = search_result[:6000] + "\n...[РЕЗУЛЬТАТ ОБРЕЗАН]"
                    
                    # Подставляем результат как контекст и просим модель ответить
                    messages.append(self._build_assistant_msg(model, response))
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[КОНТРОЛЁР] Я выполнил поиск в интернете за тебя. Вот результаты:\n\n"
                            f"{search_result}\n\n"
                            f"Теперь используя ЭТИ РЕАЛЬНЫЕ данные, дай подробный и точный ответ пользователю на его вопрос: «{query}».\n"
                            f"НЕ придумывай информацию — используй ТОЛЬКО то, что было найдено в поиске."
                        ),
                    })
                    # Повторяем инференс без инструментов (чтобы модель просто ответила)
                    response = await self._chat(model, messages, tools=None)
                    if response:
                        final_answer = self._extract_text(model, response)
                    else:
                        final_answer = f"Вот что я нашёл в интернете:\n\n{search_result}"
                    
                    if session:
                        session.complete()
                        session_manager.remove_session(chat_id)
                    self._record_trace(session, chat_id, task_type, task_type, model, tools, tools_called, tools_called_names, t_start, "success", tool_verified, tool_verification_details)
                    return final_answer
                # ── Конец Контролёра ───────────────────────────────────

                # Обычный случай: нет инструментов → финальный ответ
                final_answer = self._extract_text(model, response)
                if session:
                    session.complete()
                    session_manager.remove_session(chat_id)
                final_status = "stub_response" if self._detect_stub(final_answer) else "success"
                self._record_trace(session, chat_id, task_type, task_type, model, tools, tools_called, tools_called_names, t_start, final_status, tool_verified, tool_verification_details)
                return final_answer

            # Выполняем каждый tool_call через MCP
            logger.info(f"  🔧 Round {round_num + 1}: {len(tool_calls)} tool call(s)")

            # Добавляем ответ ассистента в историю
            messages.append(self._build_assistant_msg(model, response))

            for tc in tool_calls:
                tools_called += 1
                tool_name = tc["name"]
                tools_called_names.append(tool_name)
                tool_args = tc["arguments"]
                tool_id = tc.get("id", tool_name)
                logger.info(f"    → {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:120]})")
                
                # --- Supervisor: Детекция зацикливания ---
                if session:
                    session.record_tool_call(tool_name)
                    if session.detect_tool_loop(window=3):
                        logger.warning(f"  ⚠️ Supervisor: Обнаружено зацикливание ({tool_name} x3). Переход в HITL.")
                        # Инициируем паузу (не прерывая цикл, просто ставим флаг для след. итерации)
                        try:
                            # Пытаемся получить скриншот из mcp, если доступно
                            screenshot = await self.mcp.call_tool("take_screenshot", {})
                        except:
                            screenshot = ""
                        session.pause_for_human(f"Обнаружено зацикливание инструмента {tool_name}", screenshot)
                        # Запишем ошибку как результат инструмента
                        messages.append(self._build_tool_result_msg(model, tool_id, tool_name, "⚠️ [SUPERVISOR] Обнаружено зацикливание. Действие заблокировано. Ожидается помощь человека."))
                        continue
                # ----------------------------------------

                result_text = await self.mcp.call_tool(tool_name, tool_args)
                
                # --- HITL Request (если инструмент сам попросил) ---
                if "__hitl_request__" in result_text and session:
                    try:
                        hitl_data = json.loads(result_text)
                        session.pause_for_human(hitl_data.get("reason", "Запрос помощи"), hitl_data.get("screenshot", ""))
                        # Ответ будет возвращен после wait()
                        messages.append(self._build_tool_result_msg(model, tool_id, tool_name, "Ожидается помощь человека..."))
                        continue
                    except json.JSONDecodeError:
                        pass
                # ---------------------------------------------------

                # Добавляем результат в историю
                messages.append(self._build_tool_result_msg(model, tool_id, tool_name, result_text))
                
                # --- Verification ---
                is_verified, v_details = await self._verify_tool_call(tool_name, tool_args, result_text)
                if is_verified is not None:
                    # If we already have a False verification, don't overwrite it with True from another tool
                    if tool_verified is None or not is_verified:
                        tool_verified = is_verified
                        tool_verification_details = v_details

        # Если вышли за пределы max_tool_rounds
        messages.append({
            "role": "user",
            "content": (
                "[СИСТЕМНАЯ КОМАНДА] Бюджет инструментов ПОЛНОСТЬЮ ИСЧЕРПАН. "
                "Ты БОЛЬШЕ НЕ МОЖЕШЬ вызывать инструменты. "
                "Сформулируй ФИНАЛЬНЫЙ ОТВЕТ пользователю на основе ВСЕХ данных, "
                "которые ты уже собрал. Дай максимально подробный и полезный ответ.\n\n"
                "ВАЖНО: Поскольку лимит раундов исчерпан, ты НЕ успел записать результаты в какие-либо файлы или выполнить финальные команды на диске. "
                "В своём ответе ты обязан честно вывести собранные данные прямо в текст чата и прямо указать, что сохранить их в файлы на диске ты не успел. "
                "КАТЕГОРИЧЕСКИ запрещено писать 'файл создан' или 'отчет сохранен', так как этого не произошло."
            ),
        })
        response = await self._chat(model, messages, tools=None)

        if session:
            session.complete()
            session_manager.remove_session(chat_id)
        final_answer = self._extract_text(model, response) if response else "Превышен лимит раундов tool calling."
        final_status = "stub_response" if self._detect_stub(final_answer) else ("timeout" if not response else "success")
        self._record_trace(session, chat_id, task_type, task_type, model, tools, tools_called, tools_called_names, t_start, final_status, tool_verified, tool_verification_details)
        
        if final_status in ("timeout", "failure") or tools_called >= max_tool_rounds:
            if self._should_trigger_analyst(task_type):
                self._mark_analyst_run(task_type)
                if session:
                    asyncio.create_task(self.run_meta_analyst(session.session_id))
        
        return final_answer

    # ── Meta-Analyst ───────────────────────────────────────────────

    def _should_trigger_analyst(self, task_type: str) -> bool:
        import time
        ANALYST_COOLDOWN_MINUTES = 30
        last = self._last_analyst_run.get(task_type, 0)
        return (time.time() - last) > ANALYST_COOLDOWN_MINUTES * 60

    def _mark_analyst_run(self, task_type: str):
        import time
        self._last_analyst_run[task_type] = time.time()

    async def run_meta_analyst(self, session_id: str) -> str:
        """
        Запускает Ревизора по конкретной сессии.
        """
        import yaml
        import time
        from pathlib import Path
        
        _BACKEND_DIR = Path(__file__).resolve().parent.parent
        
        logger.info(f"  🔍 Запуск Meta-Analyst для сессии {session_id}...")
        
        try:
            role_path = _BACKEND_DIR / "roles" / "meta_analyst.yaml"
            with open(role_path, "r", encoding="utf-8") as f:
                role_cfg = yaml.safe_load(f)
        except Exception as e:
            logger.error(f"  ❌ Ошибка загрузки meta_analyst.yaml: {e}")
            return f"Ошибка: {e}"

        # Используем "карусель" роутинга для meta_analyst, игнорируя жестко заданную модель в yaml
        model = self.registry.resolve_model("meta_analyst")
        if not model:
            logger.error("  ❌ Нет доступных моделей для Meta-Analyst.")
            return "Модель недоступна."
            
        logger.info(f"  🤖 Meta-Analyst использует модель: {model.name}")

        # Разрешены только инструменты аналитика
        allowed_tool_names = set(role_cfg.get("tools", []))
        all_tools = self.mcp.get_tools_for_anthropic() if model.provider_type == "anthropic" else self.mcp.get_tools_for_llm()
        tools = [t for t in all_tools if (t.get("name") if "name" in t else t.get("function", {}).get("name")) in allowed_tool_names]

        messages = [
            {"role": "system", "content": role_cfg.get("system_instruction", "")},
            {"role": "user", "content": f"session_id={session_id}. Выполни шаги 1-4 из алгоритма. Начни с вызова get_global_analytics()."}
        ]

        try:
            # ── ReAct Loop: крутимся, пока модель вызывает инструменты ──
            MAX_ITERATIONS = 6
            for iteration in range(MAX_ITERATIONS):
                logger.info(f"  🔄 Meta-Analyst итерация {iteration + 1}/{MAX_ITERATIONS}")
                response = await self._chat(model, messages, tools=tools)
                if not response:
                    return "Не получен ответ от модели."

                tool_calls = self._extract_tool_calls(model, response)

                # Если модель не вызвала инструменты — это финальный текстовый ответ
                if not tool_calls:
                    final_text = self._extract_text(model, response)
                    if not final_text and iteration > 0:
                        # Модель дала пустой ответ после инструментов — принудительно запрашиваем текст
                        logger.warning(f"  ⚠️ Meta-Analyst: пустой финальный ответ на итерации {iteration + 1}, запрашиваем принудительно")
                        messages.append(self._build_assistant_msg(model, response))
                        messages.append({"role": "user", "content": "На основе данных, которые ты уже получил от инструментов, напиши подробный текстовый отчёт. Не вызывай инструменты — только текст."})
                        forced_response = await self._chat(model, messages, tools=None)
                        final_text = self._extract_text(model, forced_response) if forced_response else ""
                    if final_text:
                        log_path = _BACKEND_DIR / "improvements_log.md"
                        with open(log_path, "a", encoding="utf-8") as f:
                            ts = time.strftime('%Y-%m-%d %H:%M:%S')
                            f.write(f"\n## Meta-Analyst Analysis ({ts})\nSession: {session_id}\n{final_text}\n")
                        return final_text
                    return "Аналитик не сформировал отчет."

                # Модель хочет вызвать инструменты — выполняем все
                messages.append(self._build_assistant_msg(model, response))
                for tc in tool_calls:
                    t_name = tc["name"]
                    t_args = tc["arguments"]
                    t_id = tc.get("id", t_name)
                    logger.info(f"  🔧 Meta-Analyst вызывает {t_name}({t_args})")
                    result = await self.mcp.call_tool(t_name, t_args)
                    messages.append(self._build_tool_result_msg(model, t_id, t_name, result))

            # Если исчерпали итерации — принудительный финальный ответ
            logger.warning("  ⚠️ Meta-Analyst: лимит итераций исчерпан, запрашиваем финальный ответ")
            messages.append({"role": "user", "content": "Лимит вызовов инструментов исчерпан. Сформируй итоговый отчет на основе уже собранных данных."})
            final_response = await self._chat(model, messages, tools=None)
            final_text = self._extract_text(model, final_response) or "Аналитик не сформировал отчет."
            log_path = _BACKEND_DIR / "improvements_log.md"
            with open(log_path, "a", encoding="utf-8") as f:
                ts = time.strftime('%Y-%m-%d %H:%M:%S')
                f.write(f"\n## Meta-Analyst Analysis ({ts})\nSession: {session_id}\n{final_text}\n")
            return final_text
        except Exception as e:
            logger.error(f"  ❌ Ошибка выполнения Meta-Analyst: {e}")
            return str(e)

    # ── Внутренние методы ──────────────────────────────────────────

    def _detect_stub(self, text: str) -> bool:
        if not text:
            return False
        patterns = [
            r"как языковая модель",
            r"не имею доступа",
            r"не могу выполнить",
            r"я языковая модель",
            r"я искусственный интеллект",
            r"у меня нет доступа",
            r"я не могу напрямую",
            r"не могу напрямую взаимодействовать",
        ]
        text_lower = text.lower()
        return any(re.search(p, text_lower) for p in patterns)

    async def _verify_tool_call(self, tool_name: str, tool_args: dict, result_text: str) -> tuple[bool | None, str | None]:
        """Проверяет side-effects выполненного инструмента."""
        if tool_name == "write_file":
            relative_path = tool_args.get("relative_path")
            if not relative_path:
                return None, None
            clean_path = str(relative_path).lstrip("/")
            _env_root = os.getenv("PROJECT_ROOT")
            project_root = Path(_env_root).resolve() if _env_root else Path(__file__).parent.parent.parent.resolve()
            full_path = (project_root / clean_path).resolve()
            if full_path.exists():
                return True, f"File {relative_path} verified exists."
            else:
                return False, f"File {relative_path} does not exist after write_file."
        elif tool_name == "add_cron_task":
            # If the text indicates an error, then it wasn't a fake success
            if "error" in result_text.lower() or "не удалось" in result_text.lower():
                return None, None
                
            try:
                import sys
                sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
                from mcp_servers.scheduler_mcp import _get_scheduler
                sched = _get_scheduler()
                jobs = sched.get_jobs()
                topic = tool_args.get("topic", "")
                for job in jobs:
                    if job.name == topic or (job.kwargs and job.kwargs.get("command") == tool_args.get("command")):
                        return True, f"Cron task {topic} verified in scheduler."
                return False, f"Cron task {topic} not found in scheduler despite success string."
            except Exception as e:
                return False, f"Verification failed: {e}"
        return None, None

    def _record_trace(self, session, chat_id, task_type_classified, task_type_final, model, tools, tools_called, tools_called_names, t_start, final_status, tool_verified=None, tool_verification_details=None):
        db = SessionLocal()
        try:
            duration = int((time.perf_counter() - t_start) * 1000)
            trace = ExecutionTrace(
                session_id=session.session_id if session else (str(chat_id) if chat_id else str(uuid.uuid4())),
                task_type_classified=task_type_classified,
                task_type_final=task_type_final,
                model_used=model.name if model else "none",
                model_selected=model.name if model else "none",
                planner_enabled=False,
                tools_available=len(tools) if tools else 0,
                tools_called=tools_called,
                tools_called_names=json.dumps(tools_called_names, ensure_ascii=False),
                stage_durations=json.dumps({"executor": duration}),
                duration_ms=duration,
                actions_log=json.dumps(tools_called_names, ensure_ascii=False),
                final_status=final_status,
                tool_verified=tool_verified,
                tool_verification_details=tool_verification_details
            )
            db.add(trace)
            db.commit()
        except Exception as e:
            logger.error(f"Failed to record trace: {e}")
        finally:
            db.close()

    async def _raw_generate(self, model: ModelEntry, prompt: str, json_mode: bool = False) -> str:
        """Простой generate (не chat) — для Doorman и простых задач."""
        if model.provider_type == "ollama":
            payload: dict[str, Any] = {
                "model": model.model_id,
                "prompt": prompt,
                "stream": False,
                "options": {"num_ctx": min(model.context_window, 8192), "temperature": 0.1},
            }
            if json_mode:
                payload["format"] = "json"

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{model.base_url}/api/generate", json=payload)
                resp.raise_for_status()
                return resp.json().get("response", "")

        elif model.provider_type == "openai":
            payload = {
                "model": model.model_id,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "stream": False,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}

            headers = {"Authorization": f"Bearer {model.api_key}", "Content-Type": "application/json"}
            base = model.base_url.rstrip("/")
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]

        elif model.provider_type == "anthropic":
            payload = {
                "model": model.model_id,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            }
            headers = {
                "x-api-key": model.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=900.0) as client:
                resp = await client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

        return ""

    async def _chat(
        self,
        model: ModelEntry,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> dict | None:
        """
        Chat completion с поддержкой tools.
        Возвращает сырой ответ модели (dict) или None при ошибке.
        """
        try:
            if model.provider_type == "ollama":
                return await self._chat_ollama(model, messages, tools)
            elif model.provider_type == "openai":
                return await self._chat_openai(model, messages, tools)
            elif model.provider_type == "anthropic":
                return await self._chat_anthropic(model, messages, tools)
        except Exception as e:
            logger.error(f"  ❌ _chat({model.name}): {repr(e)}")
            # Помечаем модель как недоступную для будущих вызовов
            model.available = False
            return None

    async def _chat_ollama(self, model: ModelEntry, messages: list[dict], tools: list[dict] | None, stream_callback=None) -> dict:
        payload: dict[str, Any] = {
            "model": model.model_id,
            "messages": messages,
            "stream": stream_callback is not None,
            "options": {
                "num_ctx": min(model.context_window, 32768),
                "repeat_penalty": 1.15
            },
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=900.0) as client:
            if not stream_callback:
                resp = await client.post(f"{model.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                return resp.json()
            else:
                final_response = None
                content = ""
                tool_calls = []
                async with client.stream("POST", f"{model.base_url}/api/chat", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except:
                            continue
                        msg = data.get("message", {})
                        
                        if "content" in msg and msg["content"]:
                            chunk = msg["content"]
                            content += chunk
                            await stream_callback(chunk)
                            
                        if "tool_calls" in msg and msg["tool_calls"]:
                            tool_calls = msg["tool_calls"]
                            
                        if data.get("done"):
                            final_response = data
                            if "message" not in final_response:
                                final_response["message"] = {}
                            final_response["message"]["content"] = content
                            if tool_calls:
                                final_response["message"]["tool_calls"] = tool_calls
                            break
                            
                return final_response or {}

    async def _chat_ollama_stream(self, model: ModelEntry, messages: list[dict], tools: list[dict] | None):
        payload: dict[str, Any] = {
            "model": model.model_id,
            "messages": messages,
            "stream": True,
            "options": {
                "num_ctx": min(model.context_window, 32768),
                "repeat_penalty": 1.15
            },
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=900.0) as client:
            final_response = None
            content = ""
            tool_calls_raw: list[dict] = []
            async with client.stream("POST", f"{model.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    msg = data.get("message", {})

                    # Ollama streams incremental content chunks — yield each one
                    chunk = msg.get("content", "")
                    if chunk:
                        content += chunk
                        yield "thought_chunk", chunk

                    # Tool calls come only on done=True or last chunk
                    if msg.get("tool_calls"):
                        tool_calls_raw = msg["tool_calls"]

                    if data.get("done"):
                        # Build a synthetic response object the rest of the code expects
                        final_response = {
                            "message": {
                                "role": "assistant",
                                "content": content,
                            },
                            "done": True,
                        }
                        if tool_calls_raw:
                            final_response["message"]["tool_calls"] = tool_calls_raw
                        break

            yield "final", final_response or {}

    async def _chat_openai(self, model: ModelEntry, messages: list[dict], tools: list[dict] | None) -> dict:
        payload: dict[str, Any] = {
            "model": model.model_id,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        headers = {"Authorization": f"Bearer {model.api_key}", "Content-Type": "application/json"}
        base = model.base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=900.0) as client:
            resp = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def _chat_anthropic(self, model: ModelEntry, messages: list[dict], tools: list[dict] | None) -> dict:
        # Anthropic не принимает system в messages — вычленяем
        system_text = ""
        filtered = []
        for m in messages:
            if m["role"] == "system":
                system_text += m["content"] + "\n"
            else:
                filtered.append(m)

        payload: dict[str, Any] = {
            "model": model.model_id,
            "max_tokens": 8192,
            "messages": filtered,
        }
        if system_text:
            payload["system"] = system_text.strip()
        if tools:
            payload["tools"] = tools

        headers = {
            "x-api-key": model.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=900.0) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    # ── Извлечение данных из ответов разных провайдеров ─────────────

    def _extract_text(self, model: ModelEntry, response: dict) -> str:
        if model.provider_type == "ollama":
            return response.get("message", {}).get("content", "")
        elif model.provider_type == "openai":
            return response.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        elif model.provider_type == "anthropic":
            return "".join(
                b.get("text", "")
                for b in response.get("content", [])
                if b.get("type") == "text"
            )
        return ""

    def _extract_tool_calls(self, model: ModelEntry, response: dict) -> list[dict]:
        """Извлекает tool_calls из ответа модели (unified format)."""
        calls = []
        if model.provider_type == "ollama":
            msg = response.get("message", {})
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                calls.append({
                    "id": fn.get("name", ""),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", {}),
                })
        elif model.provider_type == "openai":
            msg = response.get("choices", [{}])[0].get("message", {})
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                calls.append({
                    "id": tc.get("id", fn.get("name", "")),
                    "name": fn.get("name", ""),
                    "arguments": args,
                })
        elif model.provider_type == "anthropic":
            for block in response.get("content", []):
                if block.get("type") == "tool_use":
                    calls.append({
                        "id": block.get("id", block.get("name", "")),
                        "name": block.get("name", ""),
                        "arguments": block.get("input", {}),
                    })
        return calls

    def _build_assistant_msg(self, model: ModelEntry, response: dict) -> dict:
        """Строит assistant message для добавления в историю."""
        if model.provider_type == "anthropic":
            return {"role": "assistant", "content": response.get("content", [])}
        elif model.provider_type == "openai":
            return response.get("choices", [{}])[0].get("message", {})
        elif model.provider_type == "ollama":
            return response.get("message", {"role": "assistant", "content": ""})
        return {"role": "assistant", "content": ""}

    def _build_tool_result_msg(self, model: ModelEntry, tool_id: str, tool_name: str, result: str) -> dict:
        """Строит tool result message для добавления в историю."""
        if model.provider_type == "anthropic":
            return {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result,
                }],
            }
        elif model.provider_type == "openai":
            return {
                "role": "tool",
                "tool_call_id": tool_id,
                "content": result,
            }
        elif model.provider_type == "ollama":
            return {
                "role": "tool",
                "content": result,
            }
        return {"role": "tool", "content": result}

    def _get_fallback(self, failed_model: ModelEntry, task_type: str) -> ModelEntry | None:
        """Ищет следующую доступную модель в цепочке после failed_model."""
        chain = self.registry.routing.get(task_type, [])
        found_failed = False
        for name in chain:
            if name == failed_model.name:
                found_failed = True
                continue
            if found_failed:
                entry = self.registry.models.get(name)
                if entry and entry.available:
                    return entry
        return None

    async def _reformulate_query(self, model, history: list, query: str) -> str:
        """
        Переписывает запрос пользователя на основе истории диалога, делая его
        самостоятельным поисковым/RAG-запросом. Предотвращает получение 'винегрета' в результатах.
        """
        if not history:
            return query
            
        # Использовать быструю модель (doorman) для переписывания, а не тяжелую целевую модель
        rewrite_model = self.registry.resolve_model("doorman") or model

            
        system_prompt = (
            "Ты — вспомогательный модуль оптимизации поисковых запросов. Твоя задача — переписать последний вопрос пользователя, "
            "сделав его самостоятельным поисковым запросом (без ссылок на местоимения вроде 'это', 'то', 'как там', 'дай сводку'), "
            "основываясь на контексте предыдущего диалога.\n\n"
            "Примеры:\n"
            "История:\n"
            "User: Расскажи про биткоин\n"
            "Assistant: Биткоин — это криптовалюта...\n"
            "User: дай курс за неделю\n"
            "Твой ответ: Курс биткоина за неделю\n\n"
            "История:\n"
            "User: Какие удары получила россия от украины\n"
            "Assistant: Анализ ударов по РФ...\n"
            "User: дай сводку за неделю\n"
            "Твой ответ: Сводка ударов Украины по России за неделю\n\n"
            "Правила:\n"
            "1. Возвращай ТОЛЬКО переписанный поисковый запрос.\n"
            "2. Никаких лишних слов, вежливых фраз, вступлений, кавычек или объяснений.\n"
            "3. Если последний запрос уже самостоятельный и не требует уточнения, верни его без изменений."
        )
        
        # Формируем сообщения для чата (берём последние 4 сообщения для контекста)
        messages = [{"role": "system", "content": system_prompt}]
        for msg in history[-4:]:
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        messages.append({"role": "user", "content": f"Перепиши этот запрос: {query}"})
        
        try:
            response = await self._chat(rewrite_model, messages, tools=None)
            if response:
                reformulated = self._extract_text(rewrite_model, response).strip()
                # Очищаем от кавычек
                reformulated = reformulated.replace('"', '').replace("'", "").strip()
                # Если модель сглючила и выдала огромный текст (например маркдаун), 
                # считаем что реформация не удалась
                if reformulated and len(reformulated) < 200:
                    logger.info(f"[🔍 Rewrite] '{query}' -> '{reformulated}'")
                    return reformulated
                else:
                    logger.warning(f"[⚠️ Rewrite] Результат слишком длинный, откат к оригиналу. (len: {len(reformulated)})")
        except Exception as e:
            logger.error(f"[❌ Rewrite] Ошибка реформации запроса: {e}")
            
        return query

