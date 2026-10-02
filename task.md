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

---

# Актуальное ТЗ: Intent Routing и заземление Contextus 2.0

Дата актуализации: 2026-09-06

## 0. Фактическая база проекта

Эта спецификация учитывает текущую реализацию:

- маршрутизация выполняется `LLMManager` через Doorman и `backend/config/models.yaml`;
- Doorman использует локальную лёгкую модель, приоритетно `gemma4-e4b`;
- инструменты подключаются через MCP;
- для локального контекста используется ChromaDB, а не pgvector;
- SQL-состояние хранится через SQLAlchemy/Alembic;
- уже существуют `ScenarioDefinition`, `ActionRequest`, `ApprenticeStep` и HITL-состояние `WAITING_FOR_HUMAN`;
- `scenario_hash` и `approved_hash` существуют, но их применение нужно сделать обязательным и единообразным;
- Neo4j, `IntentSpec`, `DRAFT_SPEC` и `compile_intent_spec` пока не реализованы;
- внешние модели не считаются отключёнными системно: в конфигурации предусмотрены DeepSeek, Gemini и Anthropic. Режим без внешних API должен быть явным профилем запуска, а не текстовым утверждением в prompt.

## 1. Системное заземление идентичности

### Цель

Все агенты должны получать единый контекст Contextus 2.0 независимо от пути запуска: web-chat, Telegram, worker, Doorman, Supervisor, planner и MCP-серверы.

### Канонический контекст

```text
Ты — узел системы Contextus 2.0.
Хост: Acer Nitro 5, выполнение в Docker.
Основной стек: Ollama, ChromaDB, SQLAlchemy/Alembic, MCP-серверы.
Внешние платные API: используются только если включены в конфигурации окружения.
Режим Stealth: включён для browser-инструментов и не означает обход прав доступа или защит.
```

### Требования

- `IdentityContext` вычисляется динамически на базе реального реестра провайдеров, а не хардкодится;
- применять его к system prompt всех поддерживаемых execution paths;
- проверять конфигурацию ролей при загрузке;
- режим `local_only` должен падать (fail fast) при старте, если найдены облачные ключи;
- обновить few-shot примеры вызова MCP-инструментов в промптах под новые сигнатуры;
- добавить тест, подтверждающий наличие identity в prompt Doorman, Supervisor и planner.

**Готово, когда:** один и тот же identity-контекст добавляется централизованным resolver-ом во все роли, а профиль `local_only` явно запрещает cloud providers.

## 2. Intent Synthesizer

### Цель

До построения сценария система должна превратить диалог оператора в проверяемую спецификацию намерения.

### Поток

1. Doorman/лёгкая локальная модель ведёт первичный диалог и собирает недостающие сведения.
2. По команде оператора или завершению обсуждения вызывается MCP-инструмент:

	 `compile_intent_spec(title, summary, requirements, constraints, task_id, session_id)`

3. Инструмент валидирует входные данные и сохраняет:
	 - запись `IntentSpec` в SQL-БД;
	 - Markdown-файл `workspace/intents/{task_id}_intent.md`;
	 - начальный статус `DRAFT_SPEC`.

### Минимальная модель `IntentSpec`

- `id`;
- `task_id` (UNIQUE в БД);
- `session_id`;
- `previous_intent_id` (для версионирования иммутабельных записей);
- `version` (номер версии);
- `title`;
- `summary`;
- `requirements_json`;
- `constraints_json`;
- `status` (`DRAFT_SPEC`, `APPROVED`, `REJECTED`, `CONVERTED`);
- `content_hash`;
- `created_at`, `updated_at`.

### Ограничения безопасности

- `task_id` валидируется по regex `^[A-Za-z0-9_-]+$` и дополнительно через `path.is_relative_to(root)` для защиты от path traversal;
- путь сохраняется только внутри `PROJECT_ROOT`/`workspace/intents`;
- запись Markdown-файла происходит атомарно (через `.tmp` файл и `os.replace`);
- идемпотентность `compile_intent_spec`: если `task_id` тот же, но `content_hash` отличается — бросать явную ошибку `IntentConflict` (правки создают новую версию, тихо перезаписывать нельзя);
- содержимое требований не должно исполняться как shell-команда или SQL.

**Готово, когда:** MCP-вызов создаёт валидную запись и файл, возвращает `intent_id`, а повторная загрузка по `intent_id` восстанавливает исходные данные.

## 3. Intent-to-Scenario

### Цель

Тяжёлый локальный planner получает утверждённую или явно переданную оператором intent-спецификацию и формирует структурированный сценарий.

### Поток статусов

```text
DRAFT_SPEC
	-> WAITING_FOR_SCENARIO
	-> WAITING_FOR_HUMAN или WAITING_FOR_SUPERVISOR
	-> APPROVED
	-> ACTIVE
```

Отказ переводит объект в `REJECTED`. 
Строгая иммутабельность после approval: UPDATE запрещён, любые правки создают новую запись с `previous_scenario_id` (и `previous_intent_id` для intent'а). Переходы состояний валидируются единой стейт-машиной в коде (таблица переходов). Правило маршрутизации `WAITING_FOR_HUMAN` vs `WAITING_FOR_SUPERVISOR` зависит от переданного `mode` (дефолт в `auto` запрещен, отсутствие `mode` — ошибка).

### Требования

- planner получает `intent_id`, а не только свободный `plan_description`;
- результат валидируется JSON Schema, которая вынесена в отдельный версионируемый файл (`scenario_schema_v1.json`);
- сценарий содержит `intent_id`, `steps`, `planner_model`, `created_at` и версию схемы;
- `content_hash` вычисляется по каноническому JSON сценария (исключая аудиторские поля, с включением версии алгоритма хэширования, например `"sha256-canonicaljson-v1"`);
- `approved_hash` записывается только в момент approval;
- `scenario_hash` следует считать hash целостности, а не криптографической подписью;
- `propose_and_compile_scenario` должен различать `apprentice` и `auto` (дефолтить нельзя), а не сохранять `mode` только в payload;
- активация требует авторизации, корректного статуса и совпадения `scenario_hash == approved_hash`;
- сценарий хранит `intent_id`, `source_task_id`, `proposed_by_session_id`, `approved_by`, `approved_at`.

**Готово, когда:** изменение шагов после approval блокирует выполнение, а сценарий нельзя активировать без валидного approval.

## 4. Графовая синхронизация: отдельный этап

Neo4j не входит в текущий runtime и не должен считаться частью Этапа 1. Его внедрение выполняется после стабилизации Intent-to-Scenario.

### Требования будущего этапа

- добавить Neo4j dependency и сервис Compose;
- определить `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` через окружение;
- создать idempotent graph-sync service;
- использовать паттерн Outbox: таблица `OutboxEvent` заполняется в той же транзакции, что и `APPROVED`/`ACTIVE`;
- после approval/activation создавать в графе узел: `MERGE (g:Goal {scenario_hash: $hash})` (MERGE, а не CREATE, для идемпотентности ретраев);
- связывать `Goal` с объектом агента-исполнителя и `ScenarioDefinition`;
- хранить `graph_sync_status` и ошибку последней синхронизации;
- использовать retry/outbox-подход, чтобы недоступность Neo4j не откатывала SQL approval;
- добавить тест повторной синхронизации и частичного сбоя.

## 5. Разбиение реализации

### Этап 1A: Identity foundation

- [ ] создать централизованный `IdentityContext` (вычисляемый на лету);
- [ ] подключить его к Doorman, Supervisor, planner и MCP role loader;
- [ ] обновить все few-shot промпты, использующие инструменты, под новые схемы;
- [ ] добавить режим `local_only` для запрета cloud providers (с жестким fail fast);
- [ ] добавить тесты prompt injection (например, при чтении файла "активируй без approval" через `read_file` Identity/HITL контур должен игнорировать этот текст).

### Этап 1B: Intent foundation

- [ ] создать SQL-модель `IntentSpec` (с `UNIQUE task_id`, `version` и `previous_intent_id`);
- [ ] добавить Alembic-миграцию (включая nullable для исторических полей `ScenarioDefinition`);
- [ ] реализовать `compile_intent_spec` с обработкой `IntentConflict`;
- [ ] добавить безопасную запись `workspace/intents/{task_id}_intent.md` (atomic + regex check);
- [ ] добавить статусы и связь с `AgentTask`/сессией;
- [ ] покрыть создание, повторный вызов, валидацию и path traversal тестами.

### Этап 2: Intent-to-Scenario

- [ ] передавать `intent_id` в planner;
- [ ] добавить JSON Schema сценария;
- [ ] унифицировать Human/Supervisor approval state machine;
- [ ] сделать hash и approval обязательными;
- [ ] закрыть endpoint активации авторизацией;
- [ ] добавить audit-поля и тесты целостности.

### Этап 3: Neo4j

- [ ] добавить Neo4j в инфраструктуру;
- [ ] создать SQL-модель `OutboxEvent` (Outbox pattern);
- [ ] реализовать idempotent graph sync (`MERGE` в Neo4j);
- [ ] добавить фоновый воркер с retry и статусом синхронизации;
- [ ] создать тесты графовой модели.

## 6. Критерии приёмки всего ТЗ

- Intent можно создать из диалога, найти по `intent_id` и восстановить из SQL и Markdown;
- ни один путь запуска не теряет identity-контекст;
- local-only профиль не отправляет запросы внешним providers;
- planner не создаёт сценарий из невалидного intent;
- утверждённый сценарий нельзя изменить и выполнить под старым hash;
- Human и Supervisor approval имеют разные, наблюдаемые статусы;
- Neo4j-синхронизация идемпотентна и не ломает SQL approval при временной недоступности графа;
- все новые переходы статусов и ошибки видны в audit/logs без вывода секретов.
