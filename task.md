# Tasks: Рефакторинг «Папка как живое окружение»

## Этап 1: Quick Fix
- [x] Удалить `auto_reindex_if_needed` из `GET /conversations/{id}/messages`
- [x] Удалить `auto_reindex_if_needed` из `PUT /conversations/{id}`
- [x] `POST /sources/local` → мгновенный ответ `status: "ready"`, без индексации
- [x] Добавить `GET /sources/preview?path=...` (быстрый скан структуры)
- [x] Обновить `status-badge.tsx` — добавить статус `ready`
- [x] Обновить `data.ts` — добавить `ready` в SyncStatus
- [x] Обновить `sources-dashboard.tsx` — убрать Reindex для local, добавить режим-бейдж
- [x] Обновить `local-folder-modal.tsx` — превью файлов после ввода пути

## Этап 2: Двухуровневый движок (folder_service.py + llm_manager.py)
- [x] Создать `backend/services/folder_service.py` (get_manifest, get_token_budget, build_context)
- [x] Обновить `llm_manager.py` — заменить RAG на folder_service для local-источников

## Этап 3: Инструменты агента (FS Tools MCP)
- [x] Создать `backend/mcp_servers/fs_tools.py` (read_file, outline_file, list_files, search_files, write_file с diff)
- [x] Зарегистрировать fs_tools в mcp_manager

## Этап 4: Оптимизация
- [x] Anthropic cache_control на блок с контекстом папки
- [x] Индикаторы режима в UI (⚡ Direct / 🛠️ Workspace)
