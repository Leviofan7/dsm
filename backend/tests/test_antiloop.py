"""
Anti-loop по прогрессу (ТЗ 4.4).

read_file возвращает {"error": "File not found: ..."} обычным успешным результатом,
поэтому счётчик по исключениям такие циклы не видел и агент молотил все 15 раундов.
Здесь проверяем, что цикл останавливается на 3-м раунде:
  - при повторе одного и того же вызова (tool + args);
  - при повторе одной и той же ошибки в результатах (даже если пути разные).
"""

import json
import sys
from pathlib import Path

import pytest

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from agent.llm_manager import LLMManager


class MockModel:
    provider_type = "openai"
    supports_tools = True
    name = "mock_model"


class MockRegistry:
    models = {}
    routing = {}

    def resolve_model(self, role, complexity="auto", model_override=None, model_overrides=None):
        return MockModel()


class FakeMCP:
    """MCP, который всегда отвечает ошибкой «файл не найден» (как настоящий read_file)."""

    is_started = True

    def __init__(self):
        self.calls = []

    def get_tools_for_llm(self, allowed_servers=None, allow_privileged=None):
        return [{
            "type": "function",
            "function": {"name": "read_file", "description": "read", "parameters": {}},
        }]

    def get_tools_for_anthropic(self, allowed_servers=None, allow_privileged=None):
        return [{"name": "read_file", "description": "read", "input_schema": {}}]

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        # каждый раз путь разный — как в реальном цикле блуждания по namespace
        return json.dumps({
            "error": f"File not found: {arguments.get('relative_path', '?')} "
                     f"(project root: /app/project_workspace)"
        })


def _fake_openai_response(tool_name: str, args: dict) -> dict:
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(args)},
                }],
            }
        }]
    }


async def _run_manager(mock_chat) -> None:
    manager = LLMManager()
    manager.registry = MockRegistry()
    manager.mcp = FakeMCP()
    manager._chat = mock_chat
    async for _ in manager.execute_stream("прочитай файл", [], [], target_agent="probe"):
        pass
    return manager


@pytest.mark.asyncio
async def test_antiloop_stops_on_repeated_error_bodies():
    """Разные пути, одна и та же ошибка → стоп на 3-м вызове, а не на 15-м."""
    paths = iter([
        "project_workspace/backend/main.py",
        "backend/main.py",
        "main.py",
        "backend/models.py",
    ])
    chat_calls = []

    async def mock_chat(model, messages, tools=None):
        chat_calls.append(1)
        path = next(paths, "backend/models.py")
        return _fake_openai_response("read_file", {"relative_path": path})

    manager = await _run_manager(mock_chat)

    assert len(manager.mcp.calls) == 3, f"Ожидалось 3 вызова, было {len(manager.mcp.calls)}"
    assert len(chat_calls) == 3, "Цикл должен прерваться на 3-м раунде"


@pytest.mark.asyncio
async def test_antiloop_stops_on_repeated_identical_call():
    """Один и тот же вызов с теми же аргументами → стоп на 3-м вызове."""
    async def mock_chat(model, messages, tools=None):
        return _fake_openai_response("read_file", {"relative_path": "backend/main.py"})

    manager = await _run_manager(mock_chat)

    assert len(manager.mcp.calls) == 3, f"Ожидалось 3 вызова, было {len(manager.mcp.calls)}"


@pytest.mark.asyncio
async def test_antiloop_does_not_fire_on_successful_results():
    """Успешные, но разные результаты не должны попадать под anti-loop."""
    calls = []

    class SuccessMCP(FakeMCP):
        async def call_tool(self, tool_name, arguments, meta=None):
            calls.append(arguments)
            return json.dumps({"content": f"line {len(calls)}"})

    async def mock_chat(model, messages, tools=None):
        return _fake_openai_response("read_file", {"relative_path": f"file_{len(calls)}.py"})

    manager = LLMManager()
    manager.registry = MockRegistry()
    manager.mcp = SuccessMCP()
    manager._chat = mock_chat
    async for _ in manager.execute_stream("читай", [], [], target_agent="probe"):
        pass

    # 15 раундов стандартного цикла — anti-loop не должен вмешиваться
    assert len(calls) == 15, f"Ложное срабатывание anti-loop, вызовов: {len(calls)}"
