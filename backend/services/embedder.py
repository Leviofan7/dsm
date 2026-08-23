import httpx
import logging

import os

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_API_URL = f"{OLLAMA_BASE_URL}/api/embeddings"
EMBEDDING_MODEL = "nomic-embed-text"

async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Получает векторы (embeddings) для списка текстов через Ollama.
    Если текстов много, можно разбивать на батчи (Ollama пока не поддерживает батчи для /api/embeddings на уровне API из коробки в старых версиях, 
    но мы можем вызывать по одному или попробовать /api/embed).
    Для простоты пока делаем последовательно или параллельно (если позволяет сервер).
    Здесь используем последовательно для надежности.
    """
    embeddings = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for text in texts:
            try:
                response = await client.post(OLLAMA_API_URL, json={
                    "model": EMBEDDING_MODEL,
                    "prompt": text
                })
                response.raise_for_status()
                data = response.json()
                embeddings.append(data["embedding"])
            except Exception as e:
                logger.error(f"Failed to embed text: {e}")
                # Fallback zero vector if failed (not ideal, but keeps it running)
                # nomic-embed-text usually has 768 dims.
                embeddings.append([0.0] * 768)
    return embeddings
