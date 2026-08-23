import os
import sys
from pathlib import Path

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP

# Create FastMCP server instance (stdio protocol)
mcp = FastMCP("fs-tools", instructions="Инструменты для анализа и поиска по локальной файловой системе")


def resolve_path(filepath: str) -> str:
    """Нормализует путь."""
    return str(Path(filepath).resolve())


@mcp.tool()
async def outline_file(filepath: str) -> str:
    """Анализирует архитектуру файла (классы, функции, импорты) без загрузки всего текста. Возвращает пронумерованные строки с определениями."""
    try:
        path = resolve_path(filepath)
        if not os.path.isfile(path):
            return f"Error: File not found {filepath}"

        outline = []
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for i, line in enumerate(lines, 1):
                sline = line.strip()
                if sline.startswith((
                    "import ", "from ", "def ", "class ", "async def ",
                    "export ", "const ", "let ", "var ", "function ",
                    "type ", "interface ", "@",
                )):
                    outline.append(f"{i}: {sline}")
        return "\n".join(outline) if outline else "No definitions found."
    except Exception as e:
        return f"Error outlining file: {e}"


@mcp.tool()
async def search_files(directory: str, query: str) -> str:
    """Ищет строку (case-insensitive) по всем текстовым файлам директории рекурсивно. Возвращает совпадения в формате path:line: content."""
    try:
        results = []
        query_lower = query.lower()
        base = Path(resolve_path(directory))

        if not base.is_dir():
            return f"Error: Directory not found {directory}"

        # Расширения текстовых файлов
        TEXT_EXTS = {
            ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
            ".md", ".txt", ".html", ".css", ".toml", ".cfg", ".ini", ".sh",
            ".sql", ".env", ".dockerfile",
        }
        SKIP_DIRS = {".git", "node_modules", "__pycache__", ".next", "venv", ".venv", "chroma_data"}

        for root, dirs, files in os.walk(str(base)):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                fpath = Path(root) / fname
                if fpath.suffix.lower() not in TEXT_EXTS:
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            if query_lower in line.lower():
                                rel_path = fpath.relative_to(base)
                                results.append(f"{rel_path}:{i}: {line.strip()}")
                                if len(results) >= 50:
                                    results.append("... [РЕЗУЛЬТАТ ОБРЕЗАН: 50 совпадений]")
                                    return "\n".join(results)
                except Exception:
                    pass

        return "\n".join(results) if results else "No matches found."
    except Exception as e:
        return f"Error searching files: {e}"


if __name__ == "__main__":
    mcp.run()
