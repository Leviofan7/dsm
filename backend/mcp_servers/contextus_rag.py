import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP
from services.vector_store import search as vector_search
from services.embedder import embed_texts
from database import SessionLocal
from models import Source
from services.indexer import index_github_repo, _process_files_and_index

mcp = FastMCP("contextus-rag", instructions="RAG and Vector Search server for Contextus")

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
                    proj_root = source.detail
            
            rel_path = file_path
            if proj_root and file_path.startswith(proj_root):
                rel_path = file_path[len(proj_root):].lstrip("/")
                    
            block = f"📄 Файл: {rel_path}\n"
            if proj_root:
                block += f"📂 Корень проекта: {proj_root}\n"
            block += f"📝 Текст:\n{r.get('text', '').strip()}"
            context_blocks.append(block)
    finally:
        local_db.close()
        
    return "\n\n---\n\n".join(context_blocks)

@mcp.tool()
async def index_local_source(path: str, name: str = "") -> str:
    """Indexes a local directory into the knowledge base. Returns the Source ID."""
    if not os.path.isdir(path):
        return json.dumps({"error": f"{path} is not a valid directory."})
        
    db = SessionLocal()
    try:
        source_name = name or os.path.basename(path.rstrip("/")) or path
        new_source = Source(
            name=source_name,
            type="local",
            detail=path,
            status="indexing"
        )
        db.add(new_source)
        db.commit()
        db.refresh(new_source)
        
        await _process_files_and_index(new_source.id, db, path)
        
        new_source.status = "indexed"
        db.commit()
        return json.dumps({"success": True, "source_id": new_source.id})
    except Exception as e:
        return json.dumps({"error": f"Error indexing local source: {str(e)}"})
    finally:
        db.close()

if __name__ == "__main__":
    mcp.run()
