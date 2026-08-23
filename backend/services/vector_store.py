import chromadb
from chromadb.config import Settings
import logging
import os

logger = logging.getLogger(__name__)

# Храним данные ChromaDB локально в папке chroma_data
CHROMA_DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "chroma_data")

# Initialize ChromaDB client
client = chromadb.PersistentClient(path=CHROMA_DATA_PATH, settings=Settings(anonymized_telemetry=False))

def get_or_create_collection(source_id: str):
    """
    Создает или возвращает коллекцию для конкретного источника.
    Имя коллекции = source_id (заменяем дефисы на подчеркивания, т.к. Chroma может не любить дефисы).
    """
    collection_name = source_id.replace("-", "_")
    return client.get_or_create_collection(name=collection_name)

def add_chunks(source_id: str, chunks: list[dict], embeddings: list[list[float]]):
    """
    Добавляет чанки и их эмбеддинги в коллекцию источника.
    chunks: [{"id": "chunk_id", "text": "...", "metadata": {"file_path": "..."}}, ...]
    """
    collection = get_or_create_collection(source_id)
    
    ids = [chunk["id"] for chunk in chunks]
    documents = [chunk["text"] for chunk in chunks]
    metadatas = [chunk["metadata"] for chunk in chunks]
    
    # ChromaDB поддерживает батчевое добавление
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas
    )

def search(source_ids: list[str], query_embedding: list[float], top_k: int = 5):
    """
    Ищет релевантные чанки сразу по нескольким источникам.
    """
    results = []
    for source_id in source_ids:
        try:
            collection = client.get_collection(name=source_id.replace("-", "_"))
            res = collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k
            )
            # Извлекаем результаты
            for i in range(len(res['ids'][0])):
                results.append({
                    "id": res['ids'][0][i],
                    "text": res['documents'][0][i],
                    "metadata": res['metadatas'][0][i],
                    "distance": res['distances'][0][i],
                    "source_id": source_id
                })
        except Exception:
            # Коллекция не найдена
            continue
            
    # Сортируем по distance (чем меньше, тем ближе)
    results.sort(key=lambda x: x["distance"])
    return results[:top_k]

def delete_source_collection(source_id: str):
    """Удаляет коллекцию источника"""
    collection_name = source_id.replace("-", "_")
    try:
        client.delete_collection(name=collection_name)
    except Exception:
        # Collection already absent or doesn't exist — nothing to do
        pass

def delete_chunks(source_id: str, chunk_ids: list[str]):
    """Удаляет конкретные чанки по списку их ID из коллекции источника"""
    if not chunk_ids:
        return
    try:
        collection = get_or_create_collection(source_id)
        collection.delete(ids=chunk_ids)
    except Exception as e:
        logger.error(f"Failed to delete chunks from ChromaDB for source {source_id}: {e}")

