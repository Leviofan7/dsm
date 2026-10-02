"""
mcp_manager.py — MCP Client Orchestrator

Центральный менеджер, который при старте бэкенда поднимает наши MCP-серверы
как stdio-подпроцессы, агрегирует все их инструменты в единый реестр
и предоставляет метод call_tool() для вызова любого инструмента из любой точки кода.
"""

import asyncio
import sys
import os
import shutil
import logging
from pathlib import Path
from typing import Any
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

logger = logging.getLogger("contextus.mcp_manager")

# Tier-3 инструменты — НЕ выдаются LLM автоматически
PRIVILEGED_TOOLS = frozenset({
    "run_claude_coder",
    "run_terminal_command",
    "write_file",
    # Gate 2.0: инструменты запроса разрешения у Надсмотрщика/человека
    "request_command_execution",
    "request_plan_review",
    "request_diff_apply",
})

# Whitelist Gate 2.0: какие привилегированные инструменты разрешено ВИДЕТЬ конкретной роли.
# Раньше фильтр был «вырезать всем»: кодер оставался без единого инструмента действия,
# а весь request_*-контур (Apprentice-Gate) был недостижим. Право теперь выдаётся по роли.
GATE_TOOLS_BY_ROLE: dict[str, frozenset[str]] = {
    "coder": frozenset({
        "request_plan_review",
        "request_command_execution",
        "request_diff_apply",
        # Запись и команды разрешены ТОЛЬКО внутри git-worktree задачи:
        # workspace.write_file / run_terminal_command требуют sandbox_id,
        # иначе вызов отклоняется (require_sandbox_for_writes в config/orchestration.json).
        "write_file",
        "run_terminal_command",
    }),
    "supervisor_14b": frozenset({
        # только не-write gate-тул: write_scope у надсмотрщика = none, а
        # request_diff_apply относится к write-классу и был бы вырезан на A.1
        "request_plan_review",
    }),
}


def allowed_privileged_tools(role: str | None) -> frozenset[str]:
    """
    Привилегированные инструменты, разрешённые роли.

    GATE_TOOLS_BY_ROLE — это «что роль просит», а write_scope — «что ей разрешено»:
    write-тулы вырезаются, если роль их не имеет (Фаза A.1). Так роль с
    write_scope=none получает пусто, даже если её случайно дописать в whitelist —
    иначе декларация в roles/*.yaml врёт про фактические права.
    """
    role = (role or "").strip()
    declared = GATE_TOOLS_BY_ROLE.get(role, frozenset())
    if not declared:
        return frozenset()

    from services.role_permissions import WRITE_TOOLS, effective_write_tools

    permitted = effective_write_tools(role)
    blocked = {t for t in declared if t in WRITE_TOOLS and t not in permitted}
    if blocked:
        logger.warning(
            f"  ⚠️ [{role}] write-тулы вырезаны по write_scope: {sorted(blocked)} "
            f"(в GATE_TOOLS_BY_ROLE есть, но роль их не имеет)"
        )
    return frozenset(declared - blocked)


# ── Пути к нашим MCP-серверам ──────────────────────────────────────
_BACKEND_DIR = Path(__file__).parent.parent.resolve()
_MCP_SERVERS_DIR = _BACKEND_DIR / "mcp_servers"
_PYTHON = sys.executable


# ── Окружение MCP-подпроцессов ─────────────────────────────────────
# MCP SDK при env=None подставляет get_default_environment(), где есть ТОЛЬКО HOME и PATH.
# Из-за этого подпроцессы не видели PROJECT_ROOT, OLLAMA_URL, CODER_SANDBOX_DIR и *_API_KEY:
# workspace.py терял корень проекта (песочница превращалась во весь контейнер), а cli_engine-серверы
# не находили ни одной доступной модели. env — единый источник, без ручных перечислений ключей.
def _server_env() -> dict[str, str]:
    """Полное окружение backend-процесса для запуска MCP-серверов."""
    return {k: str(v) for k, v in os.environ.items()}


def _discover_server_specs() -> list[dict]:
    specs = []
    if not _MCP_SERVERS_DIR.exists():
        return specs
    for py_file in sorted(_MCP_SERVERS_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        name = py_file.stem.replace("_", "-")
        specs.append({"name": name, "script": str(py_file)})
    return specs


# ── Метаданные серверов — единый источник правды ───────────────────
# engine_type:
#   "tool"       — инструменты вызываются моделью агента (обычный MCP);
#   "cli_engine" — сервер запускает внешний CLI/харнесс и делает независимый инференс.
# required_provider: ключ провайдера из config/models.yaml (gemini | deepseek | anthropic | ollama).
# requires_bin: бинарник, без которого сервер неработоспособен.
DEFAULT_SERVER_META: dict[str, Any] = {
    "engine_type": "tool",
    "required_provider": None,
    "requires_bin": None,
}

SERVER_META: dict[str, dict] = {
    "gemini-cli-mcp": {
        "engine_type": "cli_engine",
        "required_provider": "gemini",
        "requires_bin": "gemini",
    },
    "deepseek-harness": {
        "engine_type": "cli_engine",
        "required_provider": "deepseek",
        "requires_bin": "node",
    },
    "coder": {
        # run_claude_coder резолвит модель по собственной цепочке 'coding'
        "engine_type": "cli_engine",
        "required_provider": None,
        "requires_bin": None,
    },
}


def server_meta(name: str) -> dict:
    """Метаданные MCP-сервера (с дефолтами для неизвестных серверов)."""
    return {**DEFAULT_SERVER_META, **SERVER_META.get(name, {})}


def known_server_names() -> list[str]:
    """Имена всех MCP-серверов, найденных в mcp_servers/ (независимо от факта запуска)."""
    return [spec["name"] for spec in _discover_server_specs()]


def _probe_binary(binary: str | None) -> bool:
    """Проверяет наличие CLI-бинарника в PATH (None → проверять нечего)."""
    if not binary:
        return True
    return shutil.which(binary) is not None


def is_server_available(name: str) -> bool:
    """
    Доступен ли сервер на уровне окружения: нужный бинарник найден в PATH.
    Недоступные CLI-движки просто исключаются из tool-списка, без ошибок.
    """
    return _probe_binary(server_meta(name).get("requires_bin"))


class MCPManager:
    """
    Unified Tool Registry — единый реестр инструментов от всех MCP-серверов.
    """

    def __init__(self):
        self._exit_stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}        # name → ClientSession
        self._tool_registry: dict[str, dict] = {}            # tool_name → {server, schema}
        self._started = False

    async def reload(self):
        """Перезапускает все серверы (полезно при добавлении новых)."""
        logger.info("  🔄 Перезагрузка MCP-серверов...")
        await self.shutdown()
        # Создаем новый exit stack
        self._exit_stack = AsyncExitStack()
        await self.start()

    # ── Старт ──────────────────────────────────────────────────────

    async def start(self):
        """Поднимает все MCP-серверы и агрегирует инструменты."""
        if self._started:
            return

        logger.info("╔══════════════════════════════════════════╗")
        logger.info("║   MCP Client Orchestrator — ЗАПУСК      ║")
        logger.info("╚══════════════════════════════════════════╝")

        await self._exit_stack.__aenter__()
        
        specs = _discover_server_specs()
        if not specs:
            logger.warning("  ⚠️ Не найдено ни одного MCP сервера.")

        for spec in specs:
            name = spec["name"]
            script = spec["script"]

            if not os.path.isfile(script):
                logger.warning(f"  ⚠️  [{name}] Скрипт не найден: {script}. Пропускаю.")
                continue

            try:
                logger.info(f"  🔄 [{name}] Запуск подпроцесса…")
                params = StdioServerParameters(
                    command=_PYTHON,
                    args=[script],
                    cwd=str(_BACKEND_DIR),
                    env=_server_env(),   # без этого SDK отдаёт подпроцессу только HOME+PATH
                )

                # stdio_client — async context manager, возвращает (read, write) streams
                streams = await self._exit_stack.enter_async_context(
                    stdio_client(params)
                )
                read_stream, write_stream = streams

                session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()

                self._sessions[name] = session

                # Забираем список инструментов
                tools_result = await session.list_tools()
                tools = tools_result.tools if hasattr(tools_result, "tools") else []

                for tool in tools:
                    tool_name = tool.name
                    self._tool_registry[tool_name] = {
                        "server": name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                    }

                logger.info(
                    f"  ✅ [{name}] Подключён. "
                    f"Инструментов: {len(tools)} → "
                    f"{[t.name for t in tools]}"
                )

            except Exception as e:
                logger.error(f"  ❌ [{name}] Ошибка запуска: {e}")

        self._started = True
        logger.info(f"  📦 Unified Tool Registry: {len(self._tool_registry)} инструментов всего.")
        logger.info("  ─────────────────────────────────────────")

    # ── Вызов инструмента ──────────────────────────────────────────

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        """
        Вызывает инструмент по имени из единого реестра.
        `meta` — служебный канал MCP (_meta запроса): им оркестратор передаёт роль
        вызывающего. Модель этот канал не видит и не может его заполнить.
        """
        if tool_name not in self._tool_registry:
            return f"Error: Tool '{tool_name}' not found in registry. Available: {list(self._tool_registry.keys())}"

        entry = self._tool_registry[tool_name]
        server_name = entry["server"]
        session = self._sessions.get(server_name)

        if not session:
            return f"Error: Server '{server_name}' session is not active."

        try:
            result = await session.call_tool(tool_name, arguments or {}, meta=meta)
            # MCP call_tool возвращает CallToolResult с content: list[TextContent | ...]
            if hasattr(result, "content") and result.content:
                parts = []
                for block in result.content:
                    if hasattr(block, "text"):
                        parts.append(block.text)
                    else:
                        parts.append(str(block))
                return "\n".join(parts)
            return str(result)
        except Exception as e:
            logger.error(f"  ❌ call_tool({tool_name}): {e}")
            return f"Error calling tool '{tool_name}': {e}"

    async def call_privileged_tool(self, tool_name: str, arguments: dict) -> str:
        """
        Вызов инструмента, который изолирован от LLM. 
        Предназначен ТОЛЬКО для вызова из backend-эндпоинтов напрямую.
        Ни один MCP-инструмент не имеет доступа к этому метонику.
        """
        if tool_name not in PRIVILEGED_TOOLS:
            logger.warning(f"  ⚠️ Попытка вызова непривилегированного инструмента {tool_name} через call_privileged_tool")
        return await self.call_tool(tool_name, arguments)

    # ── Геттеры ────────────────────────────────────────────────────

    def get_tools_for_llm(
        self,
        allowed_servers: list[str] | None = None,
        allow_privileged: frozenset[str] | set[str] | None = None,
    ) -> list[dict]:
        """
        Возвращает описания инструментов в формате, пригодном для
        OpenAI-style tool calling (type: function).

        PRIVILEGED_TOOLS скрыты по умолчанию. `allow_privileged` — явный whitelist
        имён (например `allowed_privileged_tools("coder")`), который разрешает
        конкретной роли увидеть свои gate-инструменты.
        """
        allow = frozenset(allow_privileged or ())
        tools = []
        for name, entry in self._tool_registry.items():
            if name in PRIVILEGED_TOOLS and name not in allow:
                continue
            if allowed_servers is not None and entry.get("server") not in allowed_servers:
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": entry["description"],
                    "parameters": entry.get("input_schema", {}),
                },
            })
        return tools

    def get_tools_for_anthropic(
        self,
        allowed_servers: list[str] | None = None,
        allow_privileged: frozenset[str] | set[str] | None = None,
    ) -> list[dict]:
        """
        Возвращает описания инструментов в формате Anthropic Messages API.

        `allow_privileged` — тот же whitelist, что и в get_tools_for_llm().
        """
        allow = frozenset(allow_privileged or ())
        tools = []
        for name, entry in self._tool_registry.items():
            if name in PRIVILEGED_TOOLS and name not in allow:
                continue
            if allowed_servers is not None and entry.get("server") not in allowed_servers:
                continue
            tools.append({
                "name": name,
                "description": entry["description"],
                "input_schema": entry.get("input_schema", {}),
            })
        return tools

    def get_servers_status(self) -> list[dict]:
        """
        Статус всех обнаруженных MCP-серверов — включая те, что не поднялись
        (раньше список строился только по реестру инструментов, и упавшие
        серверы в UI не были видны вообще).
        """
        tools_by_server: dict[str, list[str]] = {}
        for tool_name, entry in self._tool_registry.items():
            tools_by_server.setdefault(entry["server"], []).append(tool_name)

        status = []
        for spec in _discover_server_specs():
            name = spec["name"]
            meta = server_meta(name)
            status.append({
                "name": name,
                "tools": sorted(tools_by_server.get(name, [])),
                "active": name in self._sessions,
                "available": _probe_binary(meta.get("requires_bin")),
                "engine_type": meta["engine_type"],
                "required_provider": meta.get("required_provider"),
                "requires_bin": meta.get("requires_bin"),
            })
        return status

    @property
    def tool_names(self) -> list[str]:
        return list(self._tool_registry.keys())

    @property
    def is_started(self) -> bool:
        return self._started

    # ── Завершение ─────────────────────────────────────────────────

    async def shutdown(self):
        """Корректно закрывает все MCP-сессии и подпроцессы."""
        if not self._started:
            return
        logger.info("  🛑 MCP Manager: завершение всех серверов…")
        try:
            await self._exit_stack.aclose()
        except Exception as e:
            logger.error(f"  ⚠️ Ошибка при shutdown: {e}")
        self._sessions.clear()
        self._tool_registry.clear()
        self._started = False
        logger.info("  ✅ MCP Manager: все серверы остановлены.")
