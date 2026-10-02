import logging
from mcp.server import Server
import mcp.types as types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("doorman-tools-mcp")

server = Server("doorman-tools")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="delegate_task",
            description="Delegate the collected requirements to a specialized agent (like coder or web_researcher).",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_agent": {
                        "type": "string",
                        "description": "The agent role to delegate to (e.g. 'coder', 'web_researcher', 'meta_analyst')",
                        "enum": ["coder", "web_researcher", "meta_analyst"]
                    },
                    "complexity": {
                        "type": "string",
                        "description": "The complexity of the task",
                        "enum": ["light", "heavy"]
                    },
                    "structured_prompt": {
                        "type": "string",
                        "description": "The detailed technical specification and context for the agent to execute."
                    }
                },
                "required": ["target_agent", "complexity", "structured_prompt"]
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    if name == "delegate_task":
        # The actual interception logic will happen in BaseAgent / LLMManager
        # This tool just echoes back to the orchestrator to trigger the context switch.
        return [types.TextContent(
            type="text",
            text=f"DELEGATION_TRIGGERED: {arguments.get('target_agent')}"
        )]
    raise ValueError(f"Unknown tool: {name}")

async def run():
    from mcp.server.stdio import stdio_server
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(run())
