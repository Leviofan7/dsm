"""
mcp_manager.py — MCP Client Orchestrator

Центральный менеджер, который при старте бэкенда поднимает наши MCP-серверы
как stdio-подпроцессы, агрегирует все их инструменты в единый реестр
и предоставляет метод call_tool() для вызова любого инструмента из любой точки кода.
"""

import asyncio
import sys
import os
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
    # Gate 2.0: только для кодера в песочнице, не для общего агента
    "request_command_execution",
    "request_plan_review",
    "request_diff_apply",
})
# ── Пути к нашим MCP-серверам ──────────────────────────────────────
_BACKEND_DIR = Path(__file__).parent.parent.resolve()
_MCP_SERVERS_DIR = _BACKEND_DIR / "mcp_servers"
_PYTHON = sys.executable


# ── Описания серверов ──────────────────────────────────────────────
MCP_SERVER_SPECS = [
    {
        "name": "workspace",
        "script": str(_MCP_SERVERS_DIR / "workspace.py"),
        "description": "Безопасная работа с файлами и терминалом",
    },
    {
        "name": "contextus-rag",
        "script": str(_MCP_SERVERS_DIR / "contextus_rag.py"),
        "description": "RAG-поиск по ChromaDB",
    },
    {
        "name": "web-stealth",
        "script": str(_MCP_SERVERS_DIR / "web_stealth.py"),
        "description": "Stealth-браузер через Playwright",
    },
    {
        "name": "coder",
        "script": str(_MCP_SERVERS_DIR / "coder.py"),
        "description": "Claude Code CLI autonomous agent wrapper",
    },
    {
        "name": "coder-gate",
        "script": str(_MCP_SERVERS_DIR / "coder_gate_tools.py"),
        "description": "Apprentice-Gate 2.0: инструменты запроса разрешений для автономного Кодера",
    },
    {
        "name": "scheduler",
        "script": str(_MCP_SERVERS_DIR / "scheduler_mcp.py"),
        "description": "Планировщик крон-задач (APScheduler)",
    },
    {
        "name": "analyst",
        "script": str(_MCP_SERVERS_DIR / "analyst_mcp.py"),
        "description": "Телеметрия и аудит эффективности агента",
    },
    {
        "name": "fs-tools",
        "script": str(_MCP_SERVERS_DIR / "fs_tools.py"),
        "description": "Инструменты для работы с локальной файловой системой",
    },
]


class MCPManager:
    """
    Unified Tool Registry — единый реестр инструментов от всех MCP-серверов.

    Жизненный цикл:
        manager = MCPManager()
        await manager.start()       # поднимает серверы, агрегирует tools
        ...
        result = await manager.call_tool("read_file", {"relative_path": "README.md"})
        ...
        await manager.shutdown()    # корректно закрывает все подпроцессы
    """

    def __init__(self):
        self._exit_stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}        # name → ClientSession
        self._tool_registry: dict[str, dict] = {}            # tool_name → {server, schema}
        self._started = False

    # ── Старт ──────────────────────────────────────────────────────

    async def start(self):
        """Поднимает все MCP-серверы и агрегирует инструменты."""
        if self._started:
            return

        logger.info("╔══════════════════════════════════════════╗")
        logger.info("║   MCP Client Orchestrator — ЗАПУСК      ║")
        logger.info("╚══════════════════════════════════════════╝")

        await self._exit_stack.__aenter__()

        for spec in MCP_SERVER_SPECS:
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

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        """
        Вызывает инструмент по имени из единого реестра.
        Возвращает текстовый результат.
        """
        if tool_name not in self._tool_registry:
            return f"Error: Tool '{tool_name}' not found in registry. Available: {list(self._tool_registry.keys())}"

        entry = self._tool_registry[tool_name]
        server_name = entry["server"]
        session = self._sessions.get(server_name)

        if not session:
            return f"Error: Server '{server_name}' session is not active."

        try:
            result = await session.call_tool(tool_name, arguments or {})
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

    def get_tools_for_llm(self) -> list[dict]:
        """
        Возвращает описания инструментов в формате, пригодном для
        OpenAI-style tool calling (type: function).
        """
        tools = []
        for name, entry in self._tool_registry.items():
            if name in PRIVILEGED_TOOLS:
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

    def get_tools_for_anthropic(self) -> list[dict]:
        """
        Возвращает описания инструментов в формате Anthropic Messages API.
        """
        tools = []
        for name, entry in self._tool_registry.items():
            if name in PRIVILEGED_TOOLS:
                continue
            tools.append({
                "name": name,
                "description": entry["description"],
                "input_schema": entry.get("input_schema", {}),
            })
        return tools

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
