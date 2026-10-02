import sys
import os
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP
from services.vector_store import search as vector_search
from services.embedder import embed_texts
from database import SessionLocal
from models import Source
from services.indexer import index_github_repo, _process_files_and_index

mcp = FastMCP("contextus-rag", instructions="RAG and Vector Search server for Contextus")

# Рабочая папка агента: именно относительно неё работает read_file.
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", "/app/project_workspace")).resolve()


def _same_tree(a: Path, b: Path) -> bool:
    """Ссылаются ли два пути на одну и ту же директорию (напр. разные точки монтирования)."""
    try:
        sa, sb = os.stat(a), os.stat(b)
        return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)
    except OSError:
        return False


def _agent_path(file_path: str, proj_root: str) -> str | None:
    """
    Путь в namespace инструментов агента (относительно PROJECT_ROOT).

    ТЗ 4.10: раньше в ответе светился относительный `detail` вида “project_workspace/backend”,
    и модель пыталась читать файлы по несуществующим путям — двойной префикс + другой
    namespace. Теперь переводим абсолютный путь в тот вид, который принимает read_file.
    """
    try:
        candidate = Path(file_path)
        if not candidate.is_absolute() and proj_root:
            candidate = Path(proj_root) / file_path
        resolved = candidate.expanduser().resolve()
        try:
            return str(resolved.relative_to(PROJECT_ROOT))
        except ValueError:
            pass
    except OSError:
        pass

    # Источник проиндексирован по другому пути к ТОМУ ЖЕ дереву (например, host-путь):
    # metadata.file_path уже относителен корня источника, поэтому годится как есть.
    if proj_root and not Path(file_path).is_absolute() and _same_tree(Path(proj_root), PROJECT_ROOT):
        return file_path.lstrip("/")
    return None

@mcp.tool()
async def search_knowledge_base(query: str, source_ids: list[str], top_k: int = 5) -> str:
    """Searches the knowledge base across the specified source IDs for the given query."""
    query_embeds = await embed_texts([query])
    if not query_embeds:
        return json.dumps({"error": "Failed to embed query."})
    
    q_emb = query_embeds[0]
    rag_results = vector_search(source_ids, q_emb, top_k=top_k)
    
    if not rag_results:
        return "Данные не найдены."
        
    context_blocks = []
    local_db = SessionLocal()
    try:
        for r in rag_results:
            file_path = r.get("metadata", {}).get("file_path", "unknown_file")
            source_id = r.get("source_id")
            proj_root = ""
            if source_id:
                source = local_db.query(Source).filter(Source.id == source_id).first()
                if source and source.type == "local":
                    proj_root = source.detail or ""

            rel_path = _agent_path(file_path, proj_root)

            if rel_path:
                block = f"📄 Файл: {rel_path}\n"
                block += f"📂 Корень проекта: {PROJECT_ROOT} (читай через read_file(\"{rel_path}\"))\n"
            else:
                # Источник вне рабочей папки агента — read_file его не прочитает
                shown = file_path if os.path.isabs(file_path) else os.path.join(proj_root, file_path)
                block = f"📄 Файл: {shown}\n"
                block += "⚠️ Вне рабочей папки агента — read_file этот файл не читает\n"
            block += f"📝 Текст:\n{r.get('text', '').strip()}"
            context_blocks.append(block)
    finally:
        local_db.close()
        
    return "\n\n---\n\n".join(context_blocks)

@mcp.tool()
async def index_local_source(path: str, name: str = "") -> str:
    """
    Indexes a local directory into the knowledge base. Returns the Source ID.

    ТЗ 4.10: путь ВСЕГДА нормализуется до абсолютного. Относительный путь трактуется
    как путь внутри рабочей папки агента (PROJECT_ROOT) — иначе в базу уезжал
    container-relative префикс вида "project_workspace/backend", который потом не читался.
    Повторная индексация той же директории не создаёт дубль — переиспользуется источник.
    """
    raw = (path or "").strip()
    if not raw:
        return json.dumps({"error": "Пустой path"})

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        in_workspace = (PROJECT_ROOT / raw.lstrip("/")).resolve()
        if in_workspace.is_dir():
            candidate = in_workspace
        else:
            return json.dumps({
                "error": (
                    f"'{path}' — не абсолютный путь и его нет в рабочей папке {PROJECT_ROOT}. "
                    "Передай абсолютный путь (например /app/project_workspace/backend)."
                )
            })

    resolved_dir = candidate.resolve()
    if not resolved_dir.is_dir():
        return json.dumps({"error": f"{resolved_dir} is not a valid directory."})

    detail = str(resolved_dir)

    db = SessionLocal()
    try:
        existing = db.query(Source).filter(
            Source.type == "local",
            Source.detail == detail,
            Source.status == "indexed",
        ).first()
        if existing:
            return json.dumps({
                "success": True,
                "source_id": existing.id,
                "already_indexed": True,
                "detail": detail,
                "message": f"Директория {detail} уже проиндексирована (источник переиспользован).",
            })

        source_name = name or os.path.basename(detail.rstrip("/")) or detail
        new_source = Source(
            name=source_name,
            type="local",
            detail=detail,
            status="indexing"
        )
        db.add(new_source)
        db.commit()
        db.refresh(new_source)

        await _process_files_and_index(new_source.id, db, detail)

        new_source.status = "indexed"
        db.commit()
        return json.dumps({"success": True, "source_id": new_source.id, "detail": detail})
    except Exception as e:
        return json.dumps({"error": f"Error indexing local source: {str(e)}"})
    finally:
        db.close()

if __name__ == "__main__":
    mcp.run()
