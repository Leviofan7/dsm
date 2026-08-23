"""
coder.py — MCP-инструмент run_claude_coder: выполняет задачи кодинга напрямую
через LLM (без вызова внешнего бинарника claude CLI), редактируя файлы в песочнице.
"""

import asyncio
import json
import logging
import sys
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# Добавляем путь к backend для импорта
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

logger = logging.getLogger("contextus.coder")

mcp = FastMCP("coder", instructions="Official Claude Code CLI agent wrapper replacement via direct LLM calls")

_DEFAULT_TIMEOUT_SEC = 600

# Глобальный менеджер
_llm_manager = None

async def get_llm_manager():
    global _llm_manager
    if _llm_manager is None:
        from agent.llm_manager import LLMManager
        _llm_manager = LLMManager()
        await _llm_manager.initialize()
    return _llm_manager

def list_project_files(base_dir: Path) -> list[str]:
    ignored_dirs = {".git", "__pycache__", "venv", "node_modules", ".next", "dist", "build", "chroma_data", ".claude"}
    ignored_exts = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".db", ".sqlite", ".sqlite3", ".bin", ".tar", ".gz", ".zip"}
    files = []
    for root, dirs, filenames in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
        for f in filenames:
            ext = Path(f).suffix.lower()
            if ext not in ignored_exts:
                rel_path = os.path.relpath(os.path.join(root, f), base_dir)
                files.append(rel_path)
    return files

def clean_llm_code(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        first_line_end = text.find("\n")
        if first_line_end != -1:
            text = text[first_line_end+1:]
        else:
            text = text.removeprefix("```")
    if text.endswith("```"):
        text = text.removesuffix("```")
    return text.strip()

@mcp.tool()
async def run_claude_coder(
    target_dir: str = Field(..., description="Абсолютный путь к директории с кодом"),
    prompt: str = Field(None, description="Задача для кодера"),
    instruction: str = Field(None, description="Задача для кодера (alias)"),
    timeout: int = Field(_DEFAULT_TIMEOUT_SEC, description="Таймаут в секундах")
) -> str:
    """
    Выполняет кодинг напрямую через LLM в target_dir.
    """
    actual_prompt = prompt or instruction
    if not actual_prompt:
        return json.dumps({
            "success": False, "result": "", "cost_usd": None, "session_id": None,
            "error": "Не передан prompt или instruction",
        })

    target = Path(target_dir)
    if not target.is_dir():
        return json.dumps({
            "success": False, "result": "", "cost_usd": None, "session_id": None,
            "error": f"target_dir не существует или не директория: {target_dir}",
        })

    logger.info(f"🤖 Запуск прямого кодера в {target_dir}. Задача: {actual_prompt[:100]}...")

    try:
        manager = await get_llm_manager()
        model = manager.registry.resolve_model("coding")
        if not model:
            raise Exception("Ни одна модель не доступна для задачи 'coding'")

        # Шаг 1: Получаем список файлов и просим выбрать нужные
        files_list = list_project_files(target)
        files_str = "\n".join(f"- {f}" for f in files_list)
        
        identify_system = (
            "Ты — ассистент-координатор разработки. Тебе дана задача по программированию и список файлов проекта.\n"
            "Определи, какие файлы из предоставленного списка необходимо изменить, дополнить или создать новые для решения задачи.\n"
            "Верни строго JSON-массив строк (путей к файлам относительно корня проекта). Не пиши никаких пояснений или markdown-тегов.\n"
            "Пример вывода: [\"path/to/file1.py\", \"path/to/file2.js\"]"
        )
        
        identify_user = (
            f"Список файлов проекта:\n{files_str}\n\n"
            f"Задача: {actual_prompt}"
        )
        
        messages = [
            {"role": "system", "content": identify_system},
            {"role": "user", "content": identify_user}
        ]
        
        plan_resp = await manager._chat(model, messages)
        plan_text = manager._extract_text(model, plan_resp) if plan_resp else "[]"
        
        files_to_edit = []
        try:
            cleaned_json = plan_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            files_to_edit = json.loads(cleaned_json)
        except Exception:
            # Fallback: поиск упомянутых файлов по именам
            for f in files_list:
                base_name = Path(f).name
                if base_name in actual_prompt:
                    files_to_edit.append(f)

        if not files_to_edit:
            return json.dumps({
                "success": False, "result": "", "cost_usd": None, "session_id": None,
                "error": f"Не удалось определить файлы для редактирования. Ответ модели: {plan_text[:200]}",
            })

        logger.info(f"📂 Файлы для изменения: {files_to_edit}")
        modified_files = []

        # Шаг 2: Для каждого файла запрашиваем обновленное содержимое
        for rel_path in files_to_edit:
            file_path = target / rel_path
            
            # Читаем старое содержимое
            old_content = ""
            if file_path.is_file():
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        old_content = f.read()
                except Exception as e:
                    logger.error(f"Не удалось прочитать файл {rel_path}: {e}")
                    
            edit_system = (
                "Ты — Senior Software Engineer. Тебе дана задача по программированию и содержимое файла, который нужно изменить.\n"
                f"Задача: {actual_prompt}\n"
                f"Файл: {rel_path}\n\n"
                "Вот текущее содержимое файла (если файл новый, то пусто):\n"
                "=== НАЧАЛО ФАЙЛА ===\n"
                f"{old_content}\n"
                "=== КОНЕЦ ФАЙЛА ===\n\n"
                "Внеси изменения в этот файл для решения задачи.\n"
                "Верни ПОЛНОЕ новое содержимое файла.\n"
                "КРИТИЧЕСКИЕ ПРАВИЛА:\n"
                "1. Твой ответ должен содержать ТОЛЬКО обновленный код файла, от первой до последней строчки.\n"
                "2. Не используй markdown-блоки с подсветкой синтаксиса (такие как ```python или ```js). Начни писать код сразу.\n"
                "3. Не пиши никаких объяснений, комментариев вне кода или вводных слов."
            )
            
            edit_messages = [
                {"role": "system", "content": edit_system},
                {"role": "user", "content": f"Сгенерируй новое содержимое для файла {rel_path}."}
            ]
            
            edit_resp = await manager._chat(model, edit_messages)
            edit_text = manager._extract_text(model, edit_resp) if edit_resp else ""
            new_content = clean_llm_code(edit_text)
            
            if not new_content or new_content.strip() == "":
                raise Exception(f"Модель вернула пустой результат для файла {rel_path}")

            # Записываем изменения
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
                
            modified_files.append(rel_path)
            logger.info(f"✅ Успешно изменен/создан файл: {rel_path}")

        return json.dumps({
            "success": True,
            "result": f"Успешно применены изменения к файлам: {', '.join(modified_files)}",
            "cost_usd": 0.0,
            "session_id": "direct_mcp_session",
            "error": None,
        })

    except Exception as e:
        logger.error(f"❌ Ошибка прямого кодера: {e}")
        return json.dumps({
            "success": False, "result": "", "cost_usd": None, "session_id": None,
            "error": str(e),
        })

if __name__ == "__main__":
    mcp.run()
