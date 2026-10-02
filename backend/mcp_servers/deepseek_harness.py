"""MCP bridge for running one DeepSeek Harness ACP session."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


mcp = FastMCP(
    "deepseek-harness",
    instructions="Run a task through DeepSeek Harness with its native tools.",
)

HARNESS_ROOT = Path(os.getenv("DSH_ROOT", "/home/ai-line/Projects/deepseek-harness"))
HARNESS_TIMEOUT_SECONDS = float(os.getenv("DSH_MCP_TIMEOUT_SECONDS", "900"))


async def _send(process: asyncio.subprocess.Process, message: dict[str, Any]) -> None:
    process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
    await process.stdin.drain()


async def _request(
    process: asyncio.subprocess.Process,
    request_id: int,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    await _send(process, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    while True:
        line = await asyncio.wait_for(process.stdout.readline(), HARNESS_TIMEOUT_SECONDS)
        if not line:
            stderr = await process.stderr.read()
            raise RuntimeError(
                f"Harness ACP exited (code={process.returncode}, signal={process.returncode if process.returncode and process.returncode < 0 else None}): "
                f"{stderr.decode(errors='replace').strip()}"
            )
        message = json.loads(line)
        if message.get("method") == "session/request_permission":
            options = message.get("params", {}).get("options", [])
            option = next((item for item in options if item.get("optionId") == "allow-once"), None)
            if option is None:
                option = next((item for item in options if item.get("kind", "").startswith("allow")), None)
            if option is None:
                raise RuntimeError("Harness requested permission but offered no allow option")
            await _send(process, {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"outcome": {"outcome": "selected", "optionId": option["optionId"]}},
            })
            continue
        if message.get("id") == request_id:
            if "error" in message:
                raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))
            return message.get("result", {})


async def _run_harness(prompt: str, cwd: str) -> str:
    workspace = Path(cwd).expanduser().resolve()
    if not workspace.is_absolute() or not workspace.is_dir():
        raise ValueError(f"cwd must be an existing absolute directory: {cwd}")

    child_env = os.environ.copy()
    child_env["DSH_HOME"] = os.getenv("DSH_HOME", "/home/ai-line/.dsh")
    process = await asyncio.create_subprocess_exec(
        "node",
        "--import",
        "tsx/esm",
        "apps/cli/src/bin.ts",
        "--profile",
        "acp",
        cwd=str(HARNESS_ROOT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env,
    )
    try:
        await _request(process, 1, "initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": "dsm", "version": "1.0.0"},
        })
        await _send(process, {"jsonrpc": "2.0", "method": "initialized", "params": {}})
        session = await _request(process, 2, "session/new", {"cwd": str(workspace), "mcpServers": []})
        session_id = session["sessionId"]
        await _send(process, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt}]},
        })

        output: list[str] = []
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), HARNESS_TIMEOUT_SECONDS)
            if not line:
                stderr = await process.stderr.read()
                raise RuntimeError(
                    f"Harness ACP exited (code={process.returncode}, signal={process.returncode if process.returncode and process.returncode < 0 else None}): "
                    f"{stderr.decode(errors='replace').strip()}"
                )
            message = json.loads(line)
            if message.get("method") == "session/request_permission":
                options = message.get("params", {}).get("options", [])
                option = next((item for item in options if item.get("optionId") == "allow-once"), None)
                if option is None:
                    option = next((item for item in options if item.get("kind", "").startswith("allow")), None)
                if option is None:
                    raise RuntimeError("Harness requested permission but offered no allow option")
                await _send(process, {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"outcome": {"outcome": "selected", "optionId": option["optionId"]}},
                })
                continue
            params = message.get("params", {})
            update = params.get("update", {})
            if update.get("sessionUpdate") in {"agent_message_chunk", "agent_thought_chunk"}:
                content = update.get("content", {})
                if content.get("type") == "text":
                    output.append(content.get("text", ""))
            if message.get("id") == 3:
                if "error" in message:
                    raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))
                break
        return "".join(output).strip() or "Harness завершил задачу без текстового ответа."
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()


@mcp.tool()
async def deepseek_harness(prompt: str, cwd: str = "/app/project_workspace") -> str:
    """Run a task through DeepSeek Harness in the selected workspace."""
    try:
        return await _run_harness(prompt, cwd)
    except asyncio.TimeoutError:
        return f"Harness timeout after {HARNESS_TIMEOUT_SECONDS:g} seconds."
    except Exception as error:
        return f"Harness error: {error}"


if __name__ == "__main__":
    mcp.run()