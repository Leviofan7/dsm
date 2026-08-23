import os
import sys
import json
import asyncio
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# Restrict operations to the project directory for safety
# Determine the root of the project (assuming backend/mcp_servers is inside the project)
_env_root = os.getenv("PROJECT_ROOT")
PROJECT_ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).parent.parent.parent.resolve()

mcp = FastMCP("workspace", instructions="Local workspace operations like file editing and terminal commands")

def get_safe_path(relative_path: str) -> Path:
    """Resolves the path strictly inside PROJECT_ROOT. Prevents directory traversal."""
    # Убираем ведущий слэш, чтобы путь вроде "/file.txt" не считался абсолютным корнем системы,
    # а воспринимался как корень проекта.
    clean_path = str(relative_path).lstrip("/")
    path = (PROJECT_ROOT / clean_path).resolve()
    
    # Бронебойная защита: проверяем, что итоговый путь находится ВНУТРИ PROJECT_ROOT
    try:
        path.relative_to(PROJECT_ROOT)
    except ValueError:
        raise ValueError(f"Security error: Access denied. Path '{relative_path}' is outside the project root ({PROJECT_ROOT}).")
        
    return path

@mcp.tool()
async def read_file(relative_path: str) -> str:
    """Reads a file from the workspace. Path must be relative to project root."""
    try:
        path = get_safe_path(relative_path)
        if not path.is_file():
            return json.dumps({"error": f"File not found: {relative_path}"})
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return json.dumps({"content": content})
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
async def write_file(relative_path: str, content: str) -> str:
    """Writes content to a file in the workspace. Will overwrite existing files."""
    try:
        path = get_safe_path(relative_path)
        # Create parent directories if they don't exist
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return json.dumps({"success": True, "message": f"Successfully wrote to {relative_path}"})
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
async def run_terminal_command(command: str) -> str:
    """Runs a shell command in the project root directory and returns the output."""
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        result = {
            "exit_code": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace")
        }
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})

if __name__ == "__main__":
    mcp.run()
