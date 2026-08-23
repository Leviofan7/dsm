import pytest
import sys
from pathlib import Path

# Добавляем backend в sys.path для импорта
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from agent.mcp_manager import MCPManager, PRIVILEGED_TOOLS

@pytest.fixture
def mock_mcp_manager():
    manager = MCPManager()
    # Mocking the tool registry directly to simulate loaded tools
    manager._tool_registry = {
        "safe_tool_1": {
            "server": "mock_server",
            "description": "Safe tool 1",
            "input_schema": {}
        },
        "safe_tool_2": {
            "server": "mock_server",
            "description": "Safe tool 2",
            "input_schema": {}
        },
        "run_claude_coder": {
            "server": "mock_server",
            "description": "Privileged tool",
            "input_schema": {}
        },
        "run_terminal_command": {
            "server": "mock_server",
            "description": "Privileged tool 2",
            "input_schema": {}
        },
        "write_file": {
            "server": "mock_server",
            "description": "Privileged tool 3",
            "input_schema": {}
        }
    }
    return manager

def test_privileged_tools_filtered_llm(mock_mcp_manager):
    """
    Test that get_tools_for_llm unconditionally filters out PRIVILEGED_TOOLS
    """
    tools = mock_mcp_manager.get_tools_for_llm()
    tool_names = [t["function"]["name"] for t in tools]
    
    assert "safe_tool_1" in tool_names
    assert "safe_tool_2" in tool_names
    
    for priv_tool in PRIVILEGED_TOOLS:
        assert priv_tool not in tool_names, f"Security leak: {priv_tool} exposed to LLM!"

def test_privileged_tools_filtered_anthropic(mock_mcp_manager):
    """
    Test that get_tools_for_anthropic unconditionally filters out PRIVILEGED_TOOLS
    """
    tools = mock_mcp_manager.get_tools_for_anthropic()
    tool_names = [t["name"] for t in tools]
    
    assert "safe_tool_1" in tool_names
    assert "safe_tool_2" in tool_names
    
    for priv_tool in PRIVILEGED_TOOLS:
        assert priv_tool not in tool_names, f"Security leak: {priv_tool} exposed to Anthropic LLM!"
