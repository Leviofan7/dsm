import os
import asyncio
import uuid
import tempfile
import shutil
import logging
from datetime import datetime
from database import SessionLocal
from models import Source, Chunk
from services.embedder import embed_texts
from services.vector_store import add_chunks, delete_source_collection, delete_chunks

logger = logging.getLogger(__name__)

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    """Very simple character-based chunking."""
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += chunk_size - overlap
    return chunks

# Бинарные / тяжёлые расширения, которые НЕ индексируем
SKIP_EXTENSIONS = frozenset({
    # Компилированные / объектные
    '.exe', '.dll', '.so', '.dylib', '.o', '.a', '.lib', '.obj', '.pyc', '.pyo', '.class', '.wasm',
    # Архивы
    '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar', '.jar', '.war', '.egg', '.whl',
    # Изображения
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg', '.webp', '.tiff', '.psd',
    # Аудио / видео
    '.mp3', '.mp4', '.wav', '.avi', '.mkv', '.mov', '.flac', '.ogg', '.webm',
    # Шрифты
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
    # Данные / БД
    '.db', '.sqlite', '.sqlite3', '.bin', '.dat', '.pkl', '.npy', '.npz', '.h5', '.hdf5',
    # Документы (бинарные)
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    # Карты / lock-файлы
    '.map', '.lock',
    # Прочее
    '.min.js', '.min.css', '.chunk.js',
})

# Директории, которые пропускаем
SKIP_DIRS = frozenset({
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', '.tox', '.mypy_cache',
    '.pytest_cache', '.eggs', 'dist', 'build', '.next', '.nuxt', '.svelte-kit',
    'coverage', '.idea', '.vscode', '.DS_Store', 'vendor', 'target',
})

# Максимальный размер файла для индексации (2 MB)
MAX_FILE_SIZE = 2 * 1024 * 1024

async def _process_files_and_index(source_id: str, db, directory_to_scan: str):
    """Общий метод для чтения файлов, чанкинга и векторизации"""
    files_to_read = []
    # Collect files
    for root, dirs, files in os.walk(directory_to_scan):
        # Прямо в os.walk: убираем системные директории, чтобы не заходить в них
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for file in files:
            # Пропускаем скрытые файлы
            if file.startswith('.'):
                continue
            # Пропускаем бинарные расширения
            _, ext = os.path.splitext(file)
            if ext.lower() in SKIP_EXTENSIONS:
                continue
            full_path = os.path.join(root, file)
            # Пропускаем слишком большие файлы
            try:
                if os.path.getsize(full_path) > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            files_to_read.append(full_path)
    
    total_files = len(files_to_read)
    total_size = 0
    all_db_chunks = []
    all_vector_chunks = []
    all_texts_for_embed = []
    
    logger.info(f"[Source {source_id}] Найдено {total_files} файлов для индексации.")
    
    for file_path in files_to_read:
        try:
            total_size += os.path.getsize(file_path)
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            relative_path = os.path.relpath(file_path, directory_to_scan)
            text_chunks = chunk_text(content)
            
            for i, text_chunk in enumerate(text_chunks):
                chunk_id = str(uuid.uuid4())
                
                # Подготовка для БД
                db_chunk = Chunk(
                    id=chunk_id,
                    source_id=source_id,
                    file_path=relative_path,
                    chunk_index=i,
                    content_preview=text_chunk[:500],
                    token_count=len(text_chunk) // 4  # rough estimate
                )
                all_db_chunks.append(db_chunk)
                
                # Подготовка для ChromaDB
                all_vector_chunks.append({
                    "id": chunk_id,
                    "text": text_chunk,
                    "metadata": {"file_path": relative_path, "chunk_index": i}
                })
                all_texts_for_embed.append(text_chunk)
                
        except Exception as e:
            logger.warning(f"Failed to read {file_path}: {e}")
            continue

    # Обновляем БД: количество файлов и размер
    source = db.query(Source).filter(Source.id == source_id).first()
    if source:
        source.files_count = total_files
        source.size_bytes = total_size
        db.commit()

    if not all_texts_for_embed:
        logger.info(f"[Source {source_id}] Нет текстовых файлов для индексации.")
        return

    # Векторизация (может быть долгой)
    # Разбиваем на батчи по 100 чанков чтобы не перегрузить Ollama
    BATCH_SIZE = 100
    for i in range(0, len(all_texts_for_embed), BATCH_SIZE):
        batch_texts = all_texts_for_embed[i:i+BATCH_SIZE]
        batch_vector_chunks = all_vector_chunks[i:i+BATCH_SIZE]
        
        logger.info(f"[Source {source_id}] Эмбеддинг батча {i//BATCH_SIZE + 1}...")
        embeddings = await embed_texts(batch_texts)
        
        logger.info(f"[Source {source_id}] Сохранение в ChromaDB...")
        add_chunks(source_id, batch_vector_chunks, embeddings)
        
    # Сохраняем метаданные в SQLite
    db.bulk_save_objects(all_db_chunks)
    db.commit()


async def index_github_repo(source_id: str, repo_url: str):
    """Клонирует репозиторий через subprocess (быстро, надежно) и индексирует"""
    db = SessionLocal()
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        db.close()
        return

    temp_dir = os.path.join(tempfile.gettempdir(), f"repo_clone_{uuid.uuid4().hex}")
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        source.status = "indexing"
        db.commit()
        
        logger.info(f"[Source {source_id}] Клонирование репозитория: {repo_url}")
        
        # Надежное клонирование как просил пользователь
        process = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", repo_url, temp_dir, 
            stdout=asyncio.subprocess.PIPE, 
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            error_message = stderr.decode('utf-8', errors='ignore')
            logger.error(f"[Source {source_id}] Git clone failed: {error_message}")
            source.status = "error"
            source.error_message = error_message
            db.commit()
            return
            
        logger.info(f"[Source {source_id}] Успешно склонировано. Начинаем чтение файлов...")
        await _process_files_and_index(source_id, db, temp_dir)
        
        source.status = "indexed"
        source.indexed_at = datetime.utcnow()
        db.commit()
        logger.info(f"[Source {source_id}] Индексация завершена успешно.")
        
    except Exception as e:
        logger.error(f"[Source {source_id}] Критическая ошибка индексации: {e}", exc_info=True)
        source.status = "error"
        source.error_message = str(e)
        db.commit()
    finally:
        db.close()
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"[Source {source_id}] Временная директория {temp_dir} удалена.")


async def reindex_source(source_id: str):
    """
    Полная или инкрементальная переиндексация существующего источника.

    Алгоритм для local:
      1. Сканируем папку для поиска текущих файлов.
      2. Сравниваем с существующими чанками в SQLite.
      3. Определяем новые, удаленные и измененные файлы.
      4. Удаляем старые чанки только для измененных/удаленных файлов из SQLite и ChromaDB.
      5. Индексируем и добавляем новые/измененные файлы.
    
    Алгоритм для github:
      1. Стираем старые чанки.
      2. Клонируем заново.
      3. Индексируем.
    """
    db = SessionLocal()
    try:
        source = db.query(Source).filter(Source.id == source_id).first()
        if not source:
            logger.error(f"[Reindex] Source {source_id} not found")
            return

        logger.info(f"[Reindex] Начало переиндексации: {source.name} (type={source.type})")
        source.status = "indexing"
        source.error_message = None
        db.commit()

        if source.type == "local":
            directory = source.detail
            if not directory or not os.path.isdir(directory):
                raise ValueError(f"Локальная папка недоступна: {directory}")

            prev_indexed_at = source.indexed_at  # может быть None при первом запуске

            if prev_indexed_at is not None:
                # ── Инкрементальный режим ───────────────────────
                # 1. Сканируем активные файлы в директории
                active_files = {}
                for root, dirs, files in os.walk(directory):
                    dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
                    for file in files:
                        if file.startswith('.'):
                            continue
                        _, ext = os.path.splitext(file)
                        if ext.lower() in SKIP_EXTENSIONS:
                            continue
                        full_path = os.path.join(root, file)
                        try:
                            if os.path.getsize(full_path) > MAX_FILE_SIZE:
                                continue
                            rel_path = os.path.relpath(full_path, directory)
                            active_files[rel_path] = full_path
                        except OSError:
                            continue

                # 2. Получаем существующие чанки из БД
                existing_chunks = db.query(Chunk).filter(Chunk.source_id == source_id).all()
                db_files = {}
                for chunk in existing_chunks:
                    db_files.setdefault(chunk.file_path, []).append(chunk)

                # 3. Находим удалённые, изменённые и новые файлы
                deleted_files = []
                modified_files = []
                new_files = []

                for rel_path, chunks in db_files.items():
                    if rel_path not in active_files:
                        deleted_files.append(rel_path)
                    else:
                        full_path = active_files[rel_path]
                        try:
                            mtime = datetime.utcfromtimestamp(os.path.getmtime(full_path))
                            if mtime > prev_indexed_at:
                                modified_files.append(rel_path)
                        except OSError:
                            deleted_files.append(rel_path)

                for rel_path in active_files:
                    if rel_path not in db_files:
                        new_files.append(rel_path)

                # 4. Удаляем старые чанки из SQLite и ChromaDB
                paths_to_remove = deleted_files + modified_files
                chunk_ids_to_delete = []
                for rel_path in paths_to_remove:
                    chunks = db_files.get(rel_path, [])
                    chunk_ids_to_delete.extend([c.id for c in chunks])

                if chunk_ids_to_delete:
                    logger.info(f"[Reindex] Удаление {len(chunk_ids_to_delete)} старых чанков для {len(paths_to_remove)} файлов")
                    delete_chunks(source_id, chunk_ids_to_delete)
                    db.query(Chunk).filter(Chunk.id.in_(chunk_ids_to_delete)).delete(synchronize_session=False)
                    db.commit()

                # 5. Индексируем изменённые и новые файлы
                files_to_index = [active_files[r] for r in (modified_files + new_files)]
                logger.info(f"[Reindex] Локальный синк: {len(new_files)} новых, {len(modified_files)} изменённых, {len(deleted_files)} удалённых")
                
                if files_to_index:
                    await _process_changed_files(source_id, db, directory, files_to_index)
                
                # 6. Обновляем метаданные
                total_files = len(active_files)
                total_size = 0
                for full_path in active_files.values():
                    try:
                        total_size += os.path.getsize(full_path)
                    except OSError:
                        pass
                source.files_count = total_files
                source.size_bytes = total_size
                db.commit()
            else:
                # Полная переиндексация (первый запуск)
                db.query(Chunk).filter(Chunk.source_id == source_id).delete(synchronize_session=False)
                db.commit()
                delete_source_collection(source_id)
                await _process_files_and_index(source_id, db, directory)

        elif source.type == "github":
            # GitHub: всегда полная очистка
            db.query(Chunk).filter(Chunk.source_id == source_id).delete(synchronize_session=False)
            db.commit()
            delete_source_collection(source_id)

            repo_url = source.detail or ""
            if not repo_url.startswith("http"):
                raise ValueError(f"Не удалось определить repo URL: {repo_url}")

            temp_dir = os.path.join(tempfile.gettempdir(), f"repo_reindex_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)
            try:
                process = await asyncio.create_subprocess_exec(
                    "git", "clone", "--depth", "1", repo_url, temp_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await process.communicate()
                if process.returncode != 0:
                    raise RuntimeError(stderr.decode("utf-8", errors="ignore"))
                await _process_files_and_index(source_id, db, temp_dir)
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            raise ValueError(f"Неизвестный тип источника: {source.type}")

        source.status = "indexed"
        source.indexed_at = datetime.utcnow()
        db.commit()
        logger.info(f"[Reindex] Переиндексация завершена: {source.name}")

    except Exception as e:
        logger.error(f"[Reindex] Ошибка: {e}", exc_info=True)
        try:
            source = db.query(Source).filter(Source.id == source_id).first()
            if source:
                source.status = "error"
                source.error_message = str(e)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


async def _process_changed_files(source_id: str, db, base_dir: str, file_paths: list[str]):
    """
    Как _process_files_and_index, но обрабатывает только конкретный список файлов.
    Эмбеддинги уже очищены, поэтому просто добавляем.
    """
    all_db_chunks = []
    all_vector_chunks = []
    all_texts_for_embed = []

    for file_path in file_paths:
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            relative_path = os.path.relpath(file_path, base_dir)
            text_chunks = chunk_text(content)
            for i, text_chunk in enumerate(text_chunks):
                chunk_id = str(uuid.uuid4())
                all_db_chunks.append(Chunk(
                    id=chunk_id,
                    source_id=source_id,
                    file_path=relative_path,
                    chunk_index=i,
                    content_preview=text_chunk[:500],
                    token_count=len(text_chunk) // 4,
                ))
                all_vector_chunks.append({
                    "id": chunk_id,
                    "text": text_chunk,
                    "metadata": {"file_path": relative_path, "chunk_index": i},
                })
                all_texts_for_embed.append(text_chunk)
        except Exception as e:
            logger.warning(f"[_process_changed_files] Пропущен {file_path}: {e}")
            continue

    if not all_texts_for_embed:
        return

    BATCH_SIZE = 100
    for i in range(0, len(all_texts_for_embed), BATCH_SIZE):
        batch_texts = all_texts_for_embed[i:i + BATCH_SIZE]
        batch_chunks = all_vector_chunks[i:i + BATCH_SIZE]
        embeddings = await embed_texts(batch_texts)
        add_chunks(source_id, batch_chunks, embeddings)

    db.bulk_save_objects(all_db_chunks)
    db.commit()


async def auto_reindex_if_needed(source_ids: list[str], db, background_tasks):
    """
    Проверяет, были ли изменены файлы в локальных источниках (local),
    и запускает инкрементальную переиндексацию в фоне.
    """
    if not source_ids:
        return

    sources = db.query(Source).filter(Source.id.in_(source_ids), Source.type == "local").all()
    for source in sources:
        # Пропускаем, если уже индексируется или в очереди
        if source.status in ["indexing", "queued"]:
            continue

        directory = source.detail
        if not directory or not os.path.isdir(directory):
            continue

        prev_indexed_at = source.indexed_at
        trigger = False

        if prev_indexed_at is None:
            trigger = True
        else:
            # 1. Сканируем папку и ищем новые/измененные файлы
            active_files = set()
            for root, dirs, files in os.walk(directory):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
                for file in files:
                    if file.startswith('.'):
                        continue
                    _, ext = os.path.splitext(file)
                    if ext.lower() in SKIP_EXTENSIONS:
                        continue
                    full_path = os.path.join(root, file)
                    try:
                        if os.path.getsize(full_path) > MAX_FILE_SIZE:
                            continue
                        rel_path = os.path.relpath(full_path, directory)
                        active_files.add(rel_path)

                        mtime = datetime.utcfromtimestamp(os.path.getmtime(full_path))
                        if mtime > prev_indexed_at:
                            trigger = True
                            break
                    except OSError:
                        continue
                if trigger:
                    break

            # 2. Если изменения не найдены через mtime, проверим, были ли файлы удалены
            if not trigger:
                db_files = db.query(Chunk.file_path).filter(Chunk.source_id == source.id).distinct().all()
                db_files_set = {f[0] for f in db_files}
                if db_files_set - active_files:
                    trigger = True

        if trigger:
            logger.info(f"[Auto-Sync] Обнаружены изменения в local источнике '{source.name}' ({source.id}). Запуск синка в фоне...")
            source.status = "queued"
            db.commit()
            background_tasks.add_task(reindex_source, source.id)

