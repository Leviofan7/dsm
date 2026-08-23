import pytest
import sys
import yaml
from pathlib import Path

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from agent.llm_manager import LLMManager
from agent.mcp_manager import PRIVILEGED_TOOLS

@pytest.mark.asyncio
async def test_llm_manager_filters_privileged_tools(tmp_path, monkeypatch):
    # Mock _BACKEND_DIR to tmp_path
    monkeypatch.setattr("agent.llm_manager._BACKEND_DIR", tmp_path)
    
    # Create a malicious role YAML
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    malicious_yaml = roles_dir / "malicious.yaml"
    malicious_yaml.write_text(yaml.dump({
        "name": "Malicious",
        "tools": ["read_file", "run_terminal_command", "write_file", "run_claude_coder"]
    }))
    
    # Mock ModelRegistry and MCPManager
    class MockModel:
        provider_type = "anthropic"
        supports_tools = True
        name = "mock_model"
        
    class MockRegistry:
        def resolve_model(self, role):
            return MockModel()
            
    class MockMCP:
        is_started = True
        def get_tools_for_anthropic(self):
            # Pretend MCPManager has all tools, EVEN privileged ones (to test LLMManager's second line of defense)
            return [
                {"name": "read_file"},
                {"name": "run_terminal_command"},
                {"name": "write_file"},
                {"name": "run_claude_coder"}
            ]
            
    manager = LLMManager()
    manager.registry = MockRegistry()
    manager.mcp = MockMCP()
    
    # Run the executor loop for 1 subtask
    class MockSubtask:
        id = "1"
        target_role = "malicious"
        topic = "Test"
        prompt_instruction = "Test"
        
    class MockSession:
        def query(self, *args, **kwargs):
            class MockQuery:
                def filter(self, *args, **kwargs):
                    class MockFilter:
                        def order_by(self, *args, **kwargs):
                            class MockOrderBy:
                                def all(self):
                                    return [MockSubtask()]
                            return MockOrderBy()
                        def first(self):
                            return None
                    return MockFilter()
            return MockQuery()
        def close(self):
            pass
            
    monkeypatch.setattr("agent.llm_manager.SessionLocal", MockSession)
    
    # Override _chat to intercept tools
    intercepted_tools = []
    async def mock_chat(model, messages, tools=None):
        nonlocal intercepted_tools
        intercepted_tools = tools
        return None
        
    manager._chat = mock_chat
    manager._extract_text = lambda m, r: "Stub"
    manager._detect_stub = lambda x: True
    
    # We need an async generator consumer
    async def consume():
        async for _ in manager.execute_stream("query", [], "test_task"):
            pass
            
    try:
        await consume()
    except Exception as e:
        pass # Ignore errors after _chat is called
        
    # Check that tools passed to _chat DO NOT contain PRIVILEGED_TOOLS
    assert intercepted_tools is not None
    tool_names = [t["name"] for t in intercepted_tools]
    
    assert "read_file" in tool_names, "Safe tool should be kept"
    assert "run_terminal_command" not in tool_names, "Privileged tool should be filtered"
    assert "write_file" not in tool_names, "Privileged tool should be filtered"
    assert "run_claude_coder" not in tool_names, "Privileged tool should be filtered"
