import os
import json
from pathlib import Path

# Бинарные / тяжёлые расширения, которые НЕ показываем
SKIP_EXTENSIONS = frozenset({
    '.exe', '.dll', '.so', '.dylib', '.o', '.a', '.lib', '.obj', '.pyc', '.pyo', '.class', '.wasm',
    '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar', '.jar', '.war', '.egg', '.whl',
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg', '.webp', '.tiff', '.psd',
    '.mp3', '.mp4', '.wav', '.avi', '.mkv', '.mov', '.flac', '.ogg', '.webm',
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
    '.db', '.sqlite', '.sqlite3', '.bin', '.dat', '.pkl', '.npy', '.npz', '.h5', '.hdf5',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.map', '.lock',
    '.min.js', '.min.css', '.chunk.js',
})

SKIP_DIRS = frozenset({
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', '.tox', '.mypy_cache',
    '.pytest_cache', '.eggs', 'dist', 'build', '.next', '.nuxt', '.svelte-kit',
    'coverage', '.idea', '.vscode', '.DS_Store', 'vendor', 'target',
})

TOKEN_CHAR_RATIO = 4
MAX_DIRECT_TOKENS = 15000

def get_folder_preview(path: str, max_depth: int = 2) -> dict:
    """Быстрый сканер структуры папки для превью в UI."""
    if not os.path.isdir(path):
        return {"error": "Invalid directory path"}
        
    root_path = Path(path)
    
    def _scan(current_path: Path, current_depth: int):
        if current_depth > max_depth:
            return None
            
        result = []
        try:
            items = sorted(current_path.iterdir(), key=lambda x: (x.is_file(), x.name))
        except PermissionError:
            return []
            
        for item in items:
            if item.name.startswith('.') and item.name != '.env.example':
                continue
                
            if item.is_dir():
                if item.name in SKIP_DIRS:
                    continue
                children = _scan(item, current_depth + 1)
                if children is not None:
                    result.append({"name": item.name, "type": "folder", "children": children})
                else:
                    result.append({"name": item.name, "type": "folder", "children": [{"name": "...", "type": "file"}]})
            else:
                if item.suffix.lower() in SKIP_EXTENSIONS:
                    continue
                result.append({"name": item.name, "type": "file"})
        return result

    try:
        children = _scan(root_path, 1)
        tokens = get_token_budget(path)
        mode = "direct" if tokens < MAX_DIRECT_TOKENS else "workspace"
        return {
            "name": root_path.name or str(root_path),
            "type": "folder",
            "children": children or [],
            "tokens": tokens,
            "mode": mode
        }
    except Exception as e:
        return {"error": str(e)}

def _get_files_sorted_by_mtime(path: str) -> list[Path]:
    """Возвращает все текстовые файлы в папке, отсортированные по mtime (сначала свежие)."""
    root_path = Path(path)
    files = []
    
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]
        for file in filenames:
            if file.startswith('.'):
                continue
            ext = os.path.splitext(file)[1].lower()
            if ext in SKIP_EXTENSIONS:
                continue
            
            full_path = Path(dirpath) / file
            try:
                if full_path.is_symlink():
                    continue
                mtime = full_path.stat().st_mtime
                files.append((full_path, mtime))
            except OSError:
                pass

    files.sort(key=lambda x: x[1], reverse=True)
    return [f[0] for f in files]

def get_token_budget(path: str) -> int:
    """Оценивает бюджет токенов во всех текстовых файлах папки."""
    total_chars = 0
    files = _get_files_sorted_by_mtime(path)
    for f in files:
        try:
            total_chars += f.stat().st_size
        except OSError:
            pass
    return total_chars // TOKEN_CHAR_RATIO

def build_context(path: str) -> dict:
    """
    Адаптивная генерация контекста папки.
    Возвращает словарь:
    {
        "mode": "direct" | "workspace",
        "content": str
    }
    """
    if not os.path.isdir(path):
        return {"mode": "error", "content": "Директория не найдена"}

    tokens = get_token_budget(path)
    files = _get_files_sorted_by_mtime(path)
    
    if tokens < MAX_DIRECT_TOKENS:
        # Mode 1: Direct Snapshot
        content = [f"=== ПАПКА: {path} ==="]
        for f in files:
            try:
                rel_path = f.relative_to(path)
                with open(f, 'r', encoding='utf-8') as f_in:
                    text = f_in.read()
                content.append(f"\n[Файл: {rel_path}]\n```\n{text}\n```")
            except Exception:
                continue
        content.append(f"\n=== КОНЕЦ ПАПКИ ===")
        return {"mode": "direct", "content": "\n".join(content)}
    else:
        # Mode 2: Agentic Workspace (Manifest)
        content = [
            f"=== ПАПКА: {path} ===",
            "Это большая директория (Режим Workspace). Ниже приведена её структура и список недавно изменённых файлов.",
            "Для чтения содержимого файлов используй инструмент read_file, для поиска — search_files, для анализа архитектуры — outline_file.",
            "\n[Топ 50 недавно изменённых файлов:]"
        ]
        for f in files[:50]:
            try:
                rel_path = f.relative_to(path)
                content.append(f"- {rel_path}")
            except Exception:
                continue
        content.append("\n=== КОНЕЦ ПАПКИ ===")
        return {"mode": "workspace", "content": "\n".join(content)}
