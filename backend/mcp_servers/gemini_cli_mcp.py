import os
import sys
import subprocess
import logging
import asyncio
from typing import Any

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server.stdio import stdio_server

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gemini-cli-mcp")

server = Server("gemini-cli")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="gemini_ask",
            description="Send a prompt to Gemini CLI and get the response.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The prompt to send to Gemini"
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional context to include in the prompt"
                    }
                },
                "required": ["prompt"],
            },
        ),
        types.Tool(
            name="gemini_code",
            description="Run Gemini CLI in code mode for a specific task.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The coding task to perform"
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Optional working directory for the task"
                    }
                },
                "required": ["task"],
            },
        ),
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if not arguments:
        raise ValueError("Missing arguments")

    if name == "gemini_ask":
        prompt = arguments.get("prompt", "")
        context = arguments.get("context", "")
        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        
        # User tip: Gemini CLI при передаче аргументов без флага -p переходит в интерактивный TTY-режим и зависает. 
        # В неинтерактивном вызове обязателен флаг -p и жесткий timeout.
        try:
            result = subprocess.run(
                ["gemini", "-p", full_prompt],
                capture_output=True,
                text=True,
                timeout=60
            )
            output = result.stdout if result.returncode == 0 else result.stderr
            return [types.TextContent(type="text", text=output or "Empty response")]
        except subprocess.TimeoutExpired:
            return [types.TextContent(type="text", text="Error: Gemini CLI timed out after 60s")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error executing Gemini CLI: {e}")]

    elif name == "gemini_code":
        task = arguments.get("task", "")
        working_dir = arguments.get("working_dir")
        
        # Validate working directory
        if working_dir and not os.path.isdir(working_dir):
            return [types.TextContent(type="text", text=f"Error: working_dir '{working_dir}' does not exist or is not a directory")]
            
        try:
            # Need -p to prevent TTY hang in non-interactive execution
            cmd = ["gemini", "-p", task]
            
            result = subprocess.run(
                cmd,
                cwd=working_dir if working_dir else None,
                capture_output=True,
                text=True,
                timeout=120
            )
            output = result.stdout if result.returncode == 0 else result.stderr
            return [types.TextContent(type="text", text=output or "Task completed, empty response")]
        except subprocess.TimeoutExpired:
            return [types.TextContent(type="text", text="Error: Gemini CLI code task timed out after 120s")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error executing Gemini CLI: {e}")]
    else:
        raise ValueError(f"Unknown tool: {name}")

async def main():
    logger.info("Starting Gemini CLI MCP server")
    
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="gemini-cli",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
