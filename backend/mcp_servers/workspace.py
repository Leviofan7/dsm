import os
import sys
import json
import asyncio
from pathlib import Path

# backend в sys.path — нужен импорт services.sandbox (реестр песочниц)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP
from services.sandbox import (
    registered_sandbox_dir,
    sandbox_required_for_writes,
)

# Операции ограничены корнем проекта. PROJECT_ROOT задаётся окружением (docker-compose).
# Fallback на Path(__file__).parent.parent.parent УДАЛЁН осознанно: раньше при незаданном
# PROJECT_ROOT он давал "/" — все пути уезжали в корень контейнера (read_file → File not found),
# а проверка path.relative_to(PROJECT_ROOT) становилась истинной всегда, т.е. песочница исчезала.
_env_root = os.getenv("PROJECT_ROOT")
if not _env_root:
    raise RuntimeError(
        "PROJECT_ROOT не задан. MCP-сервер 'workspace' не угадывает корень проекта: "
        "без него пути резолвятся от корня файловой системы. "
        "Задайте PROJECT_ROOT (для docker-compose: /app/project_workspace)."
    )

PROJECT_ROOT = Path(_env_root).resolve()
if not PROJECT_ROOT.is_dir():
    raise RuntimeError(f"PROJECT_ROOT указывает на несуществующую директорию: {PROJECT_ROOT}")

mcp = FastMCP("workspace", instructions=f"Local workspace operations like file editing and terminal commands (root: {PROJECT_ROOT})")

_NO_SANDBOX_ERROR = (
    "Песочница не указана. Запись и команды выполняются только внутри git-worktree задачи: "
    "передай sandbox_id (он совпадает с coder_task_id и выдаётся после одобрения request_diff_apply), "
    "либо создай песочницу через gate-инструменты."
)

def _root_for(sandbox_id: str | None) -> Path:
    """Корень для операции: зарегистрированная песочница задачи или сам PROJECT_ROOT."""
    if not sandbox_id:
        return PROJECT_ROOT
    sandbox = registered_sandbox_dir(str(sandbox_id))
    if not sandbox:
        raise ValueError(
            f"Sandbox '{sandbox_id}' не найдена или не зарегистрирована. "
            "Сначала получи одобрение request_diff_apply — backend создаст git-worktree."
        )
    return sandbox.resolve()

def get_safe_path(relative_path: str, sandbox_id: str | None = None) -> Path:
    """Resolves the path strictly inside the chosen root. Prevents directory traversal."""
    root = _root_for(sandbox_id)
    # Убираем ведущий слэш, чтобы путь вроде "/file.txt" не считался абсолютным корнем системы,
    # а воспринимался как корень проекта.
    clean_path = str(relative_path).lstrip("/")
    path = (root / clean_path).resolve()
    
    # Бронебойная защита: проверяем, что итоговый путь находится ВНУТРИ выбранного корня
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError(f"Security error: Access denied. Path '{relative_path}' is outside {root}.")
        
    return path

@mcp.tool()
async def read_file(relative_path: str, sandbox_id: str = "") -> str:
    """Reads a file from the workspace. Path is relative to project root (or to the sandbox if sandbox_id is given)."""
    try:
        path = get_safe_path(relative_path, sandbox_id or None)
        if not path.is_file():
            return json.dumps({"error": f"File not found: {relative_path} (root: {_root_for(sandbox_id or None)})"})
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return json.dumps({"content": content})
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
async def list_dir(relative_path: str = ".", sandbox_id: str = "") -> str:
    """Lists a directory in the workspace (files and subfolders). Path is relative to project root (or sandbox)."""
    try:
        path = get_safe_path(relative_path, sandbox_id or None)
        if not path.is_dir():
            return json.dumps({"error": f"Directory not found: {relative_path} (root: {_root_for(sandbox_id or None)})"})

        entries = []
        for child in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if child.name.startswith("."):
                continue
            suffix = "/" if child.is_dir() else ""
            entries.append(f"{child.name}{suffix}")
        return json.dumps({"path": relative_path, "entries": entries})
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
async def write_file(relative_path: str, content: str, sandbox_id: str = "") -> str:
    """
    Writes content to a file. По умолчанию пишет ТОЛЬКО в песочницу задачи:
    без sandbox_id вызов отклоняется (require_sandbox_for_writes в config/orchestration.json).
    """
    try:
        if not sandbox_id and sandbox_required_for_writes():
            return json.dumps({"error": _NO_SANDBOX_ERROR})
        path = get_safe_path(relative_path, sandbox_id or None)
        # Create parent directories if they don't exist
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return json.dumps({
            "success": True,
            "message": f"Successfully wrote to {relative_path}",
            "sandbox_id": sandbox_id or None,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
async def run_terminal_command(command: str, sandbox_id: str = "") -> str:
    """
    Runs a shell command. По умолчанию команды выполняются ТОЛЬКО внутри песочницы задачи:
    без sandbox_id вызов отклоняется (см. config/orchestration.json).
    """
    try:
        if not sandbox_id and sandbox_required_for_writes():
            return json.dumps({"error": _NO_SANDBOX_ERROR})
        cwd = _root_for(sandbox_id or None)
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        result = {
            "exit_code": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "cwd": str(cwd),
        }
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})

if __name__ == "__main__":
    mcp.run()
