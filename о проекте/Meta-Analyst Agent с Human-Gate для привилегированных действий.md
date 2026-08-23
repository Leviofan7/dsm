# Meta-Analyst Agent с Human-Gate для привилегированных действий

## Описание

Реализуем систему, где Ревизор (Meta-Analyst) может анализировать логи сессий и **предлагать** улучшения промптов и кода, но **не исполнять их напрямую**. Все привилегированные действия (`prompt_update`, `coder_task`) проходят через **симметричный двухшаговый flow** с diff-просмотром перед любым применением.

Дополнительно: `run_terminal_command` и `run_claude_coder` явно изолированы из пула инструментов Planner'а — это Tier-3 инструменты, доступные только через отдельный approve-поток.

---

## User Review Required

> [!WARNING]
> **Симметричный двухшаговый flow для обоих типов:**
> - `coder_task`: approve ТЗ → coder в sandbox → diff → **Apply** (применяет patch в `PROJECT_ROOT`)
> - `prompt_update`: approve → генерируется unified diff (старый vs новый prompt) → **Apply** (обновляет YAML)
>
> Оба типа требуют два отдельных клика. Изменение промпта не менее опасно, чем изменение кода — промпт-инъекция от Ревизора теперь тоже защищена двумя шагами.

> [!IMPORTANT]
> **`run_terminal_command` = Tier-3.** Workspace MCP (`workspace.py`) регистрирует `run_terminal_command`, который сейчас попадает в общий пул инструментов Planner'а. Нужно вынести его в список `PRIVILEGED_TOOLS` в `mcp_manager.py`, чтобы он не выдавался LLM в `get_tools_for_llm/anthropic()`. Это критично — иначе gate для `run_claude_coder` бессмысленен.

> [!NOTE]
> `run_claude_coder` остаётся в `coder.py` как инструмент MCP-сервера, но **исключается из пула инструментов** для всех агентов через тот же механизм `PRIVILEGED_TOOLS`. Единственный путь к нему — через `propose_coder_task` → approve → изолированный запуск из backend-эндпоинта.

---

## Open Questions (закрытые по итогам обсуждения)

> [!NOTE]
> **Алembic vs `create_all`:** Alembic-директория пустая. Таблицы создаются через `Base.metadata.create_all()` при старте. Создаём первую Alembic-миграцию только для `pending_privileged_actions`. Применяется вручную или при старте контейнера.

---

## Proposed Changes

### Часть 1: Модель данных

---

#### [MODIFY] [models.py](file:///home/ai-line/Projects/data-sources-management/backend/models.py)

Добавить в конец файла новую модель `PendingPrivilegedAction`. `ExecutionTrace` уже содержит `session_id` (строка 130) — расширять её не нужно, одна запись = один tool-call шаг, это подходит для read_session_trace.

```python
class PendingPrivilegedAction(Base):
    __tablename__ = "pending_privileged_actions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    action_type = Column(String)   # 'prompt_update' | 'coder_task'
    target = Column(String)        # target_persona или target_file
    instruction = Column(Text)     # new_prompt или coder instruction
    reasoning = Column(Text)       # диагноз от Ревизора
    session_id = Column(String, nullable=True)  # из какой сессии
    status = Column(String, default="awaiting_approval")
    # awaiting_approval → approved → coder_running → diff_ready
    # → diff_approved → applied | rejected
    diff_content = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

---

#### [NEW] Alembic migration для `pending_privileged_actions`

Файл: `backend/alembic/versions/001_add_pending_privileged_actions.py`

Создаём первую миграцию. Применяется через `alembic upgrade head` (запуск при старте контейнера или вручную).

---

### Часть 2: Конфигурация ролей и инструменты Ревизора

---

#### [NEW] [meta_analyst.yaml](file:///home/ai-line/Projects/data-sources-management/backend/roles/meta_analyst.yaml)

```yaml
name: Meta-Analyst
description: >
  Senior AI Architect. Анализирует логи выполнения агента.
  Предлагает улучшения через propose_prompt_update и propose_coder_task.
  НЕ исполняет изменения напрямую.
model: claude-sonnet
tools:
  - read_session_trace
  - propose_prompt_update
  - propose_coder_task
system_instruction: >
  Ты — Senior AI Architect. Твоя задача — анализировать логи выполнения (execution trace)
  агента. Ищи: циклические вызовы инструментов (одно и то же имя 3+ раза подряд),
  неэффективное планирование, галлюцинации (final_status=stub_response),
  лишние шаги и ошибки. Предлагай архитектурные улучшения через доступные инструменты.
  Ты НЕ можешь исполнять изменения напрямую — только формулировать и регистрировать
  предложения для проверки человеком. run_claude_coder и run_terminal_command
  тебе недоступны.
```

---

#### [MODIFY] [analyst_mcp.py](file:///home/ai-line/Projects/data-sources-management/backend/mcp_servers/analyst_mcp.py)

Полная замена содержимого. Убираем `optimize_tool_behavior` (прямая запись в лог без approval). Добавляем три инструмента:

1. **`read_session_trace(session_id)`** — читает все `ExecutionTrace` по `session_id`, возвращает JSON с полным контекстом (actions_log, tools_called_names, final_status, ошибки). Read-only.

2. **`propose_prompt_update(target_persona, new_prompt, reasoning, session_id?)`** — создаёт `PendingPrivilegedAction(action_type="prompt_update")`. Не трогает файлы ролей.

3. **`propose_coder_task(target_file, instruction, reasoning, session_id?)`** — создаёт `PendingPrivilegedAction(action_type="coder_task")`. Не вызывает `coder.py`.

**Явно не включать:** `run_claude_coder`, `run_terminal_command`, `optimize_tool_behavior`.

---

#### [MODIFY] [mcp_manager.py](file:///home/ai-line/Projects/data-sources-management/backend/agent/mcp_manager.py)

Добавить константу `PRIVILEGED_TOOLS` и фильтровать их из `get_tools_for_llm()` и `get_tools_for_anthropic()`:

```python
# Tier-3 инструменты — НЕ выдаются LLM автоматически
PRIVILEGED_TOOLS = frozenset({
    "run_claude_coder",
    "run_terminal_command",
})
```

В методах `get_tools_for_llm()` и `get_tools_for_anthropic()` добавить фильтр:
```python
for name, entry in self._tool_registry.items():
    if name in PRIVILEGED_TOOLS:
        continue  # пропускаем привилегированные
    tools.append(...)
```

Добавить `call_privileged_tool(tool_name, arguments)` — метод **только для backend-эндпоинтов**. Изоляция обеспечивается архитектурно: этот метод не регистрируется как MCP-инструмент и не доступен ни одному агенту через tool-calling. Единственный вызывающий — функция `_run_coder_in_sandbox()` в `main.py`, которая сама вызывается только из `/privileged-actions/{id}/approve`. Никакой MCP-инструмент не получает ссылку на `mcp_manager` и не может вызвать `call_privileged_tool` изнутри своего handler'а — сервера работают в отдельных subprocess'ах и общаются только через stdio-протокол.

---

#### [MODIFY] [mcp_manager.py](file:///home/ai-line/Projects/data-sources-management/backend/agent/mcp_manager.py) — регистрация `analyst` MCP-сервера

`analyst` сервер уже есть в `MCP_SERVER_SPECS`. После изменения `analyst_mcp.py` новые инструменты (`read_session_trace`, `propose_prompt_update`, `propose_coder_task`) автоматически попадут в реестр. Они **не являются** привилегированными инструментами (только регистрируют, не исполняют), поэтому в `PRIVILEGED_TOOLS` не попадают.

---

### Часть 3: Meta-Analyst как отдельный метод LLMManager

---

#### [MODIFY] [llm_manager.py](file:///home/ai-line/Projects/data-sources-management/backend/agent/llm_manager.py)

Добавить метод `run_meta_analyst(session_id: str) -> str`:

```python
async def run_meta_analyst(self, session_id: str) -> str:
    """
    Запускает Ревизора по конкретной сессии.
    Модель: claude-sonnet (или лучшая доступная из models.yaml).
    Инструменты: только analyst MCP (read_session_trace, propose_prompt_update, propose_coder_task).
    """
```

- Роль берётся из `backend/roles/meta_analyst.yaml`
- Инструменты фильтруются: только те, что зарегистрированы от сервера `analyst`
- Запускается с теми же `_chat()` + tool-calling циклом, но с ограниченным пулом инструментов
- Результат записывается в `improvements_log.md` (информационно) и возвращается как строка

**Автотриггер с rate-limit:** В конце `execute()` если `final_status in ("timeout", "failure")` — запускать `asyncio.create_task(self.run_meta_analyst(session_id))` **только если** прошло более `N` минут с последнего запуска Ревизора для аналогичного `task_type`:

```python
# В LLMManager:
_last_analyst_run: dict[str, float] = {}  # task_type → timestamp
ANALYST_COOLDOWN_MINUTES = 30

def _should_trigger_analyst(self, task_type: str) -> bool:
    last = self._last_analyst_run.get(task_type, 0)
    return (time.time() - last) > ANALYST_COOLDOWN_MINUTES * 60

def _mark_analyst_run(self, task_type: str):
    self._last_analyst_run[task_type] = time.time()
```

Это предотвращает лавинный запуск Ревизора при системной ошибке (например, Ollama недоступна → все сессии `failure`).

> [!NOTE]
> **Известный техдолг #1 (rate-limit):** Ключ кулдауна — `task_type`, а не `(task_type, error_signature)`. Два разных сбоя типа `coding` в одном 30-минутном окне попадут под одно окно, и второй Ревизор не запустится. Для старта приемлемо. Если окажется, что Ревизор пропускает новые паттерны ошибок — уточнить ключ до `(task_type, hash(errors[:64]))`. В коде будет размещён `# TODO(rate-limit): refine key to (task_type, error_sig) if false-negatives observed in production` прямо рядом с `_last_analyst_run`.

---

### Часть 4: Backend-эндпоинты approve-потока

---

#### [MODIFY] [main.py](file:///home/ai-line/Projects/data-sources-management/backend/main.py)

Добавить в конец файла новый блок эндпоинтов:

```
# --- PRIVILEGED ACTIONS API ---

GET  /privileged-actions                      # список с фильтром ?status=
POST /privileged-actions/{id}/approve         # approve: оба типа → diff_ready
POST /privileged-actions/{id}/reject          # → rejected
GET  /privileged-actions/{id}/diff            # вернуть diff_content
POST /privileged-actions/{id}/apply-diff      # финальное применение (оба типа)
POST /agent/analyze-session/{session_id}      # ручной триггер Ревизора (read-only)
```

**Auth:** Все эндпоинты `/privileged-actions/*` защищены тем же `Bearer WORKER_SECRET`, что используется в `/agent/run`. До внедрения полноценной auth это достаточный барьер.

**Логика `approve` для `coder_task`:**
1. `status → coder_running`
2. В фоне (`BackgroundTasks`):
   a. Копировать `PROJECT_ROOT` → `/tmp/coder_sandbox_<id>/` через `shutil.copytree`
   b. Вызвать `run_claude_coder` через `mcp.call_privileged_tool()` с `target_dir=/tmp/coder_sandbox_<id>/`
   c. `git diff --no-index PROJECT_ROOT /tmp/coder_sandbox_<id>/` → сохранить в `diff_content`
   d. `status → diff_ready`
   e. Очистить `/tmp/coder_sandbox_<id>/` **сразу после** генерации diff (не хранить копию)

**Логика `approve` для `prompt_update` (новый двухшаговый flow):**
1. Прочитать текущий `system_instruction` из `backend/roles/<target_persona>.yaml`
2. Сгенерировать unified diff (old prompt vs new prompt) как текстовый pseudo-diff:
   ```
   --- a/roles/<persona>.yaml
   +++ b/roles/<persona>.yaml
   @@ system_instruction @@
   - <старая строка>
   + <новая строка>
   ```
3. Сохранить в `diff_content`, `status → diff_ready`  
   ⚠️ YAML-файл ещё **не изменён** на этом этапе

**Логика `apply-diff`:**
- Для `coder_task`: проверить `status == "diff_ready"`, сделать снапшот (`git stash` или резервную копию изменяемых файлов), затем `git apply <diff>` в `PROJECT_ROOT`. При ошибке применения — автоматически откатить (`git apply -R`), `status → apply_failed`, вернуть ошибку в UI.
- Для `prompt_update`: записать новый `system_instruction` в YAML, `status → applied`.

**Снапшот перед apply (защита от поломки):**
```python
# Перед git apply:
backup_path = f"/tmp/pre_apply_backup_{action_id}/"
shutil.copytree(PROJECT_ROOT, backup_path, dirs_exist_ok=True)
try:
    subprocess.run(["git", "apply", patch_file], check=True, cwd=PROJECT_ROOT)
except subprocess.CalledProcessError:
    shutil.copytree(backup_path, PROJECT_ROOT, dirs_exist_ok=True)
    action.status = "apply_failed"
    # вернуть 422 с описанием ошибки
finally:
    shutil.rmtree(backup_path, ignore_errors=True)
```
Контейнер **не перезапускается автоматически** — это ответственность оператора. В UI будет показано предупреждение: _"Изменение применено. Перезапустите сервис для вступления в силу."_

---

### Часть 5: Frontend — раздел "Предложения по улучшению"

---

#### [NEW] [app/improvements/page.tsx](file:///home/ai-line/Projects/data-sources-management/app/improvements/page.tsx)

Новая страница в Next.js. Дизайн в стиле существующего analytics dashboard (тёмная тема, карточки, таблицы).

**Компоненты:**

1. **Таблица pending-действий** (`PendingActionsTable`):
   - Колонки: `action_type` (badge), `target`, `reasoning` (truncated), `status` (colored badge), `created_at`, кнопки
   - Кнопки: **Approve** (зелёная) / **Reject** (красная)
   - Фильтр по статусу

2. **Diff-viewer** (модальный диалог или inline-раскрытие):
   - Появляется когда `status == "diff_ready"`
   - `<pre>` с построчной подсветкой: строки с `+` — зелёные, с `-` — красные
   - Кнопки: **Apply Diff** (финальное применение) / **Discard** (reject)
   - Явный визуальный разделитель и предупреждение: _"Это изменение будет применено к рабочей копии кода"_

3. **Кнопка 🔍 "Проанализировать сессию"** в чате:
   - В `app/chat/` добавить кнопку, вызывающую `POST /agent/analyze-session/{session_id}`
   - Результат (текстовый отчёт) выводится в чат как отдельное сообщение от `assistant`

**Двухшаговый flow (обязательно):**
- Step 1: Approve ТЗ → backend запускает coder в sandbox, генерирует diff
- Step 2: Пользователь видит diff → нажимает **Apply** → patch применяется к коду

---

### Часть 6: models.yaml

---

#### [MODIFY] [models.yaml](file:///home/ai-line/Projects/data-sources-management/backend/config/models.yaml)

Добавить routing-цепочку для `meta_analyst`:

```yaml
meta_analyst:
  description: "Ревизор — анализ логов и предложения по улучшению"
  chain:
    - claude-sonnet      # Приоритет: самая мощная
    - deepseek-chat      # Фолбек 1
    - gemma4-26b         # Фолбек 2: локальная тяжёлая
```

---

## Verification Plan

### Automated

```bash
# 1. Убедиться что привилегированные инструменты не в пуле LLM:
docker exec contextus_backend python -c "
from agent.mcp_manager import MCPManager, PRIVILEGED_TOOLS
import asyncio
async def check():
    m = MCPManager()
    await m.start()
    tools = m.get_tools_for_anthropic()
    names = [t['name'] for t in tools]
    assert 'run_claude_coder' not in names, 'FAIL: run_claude_coder в пуле!'
    assert 'run_terminal_command' not in names, 'FAIL: run_terminal_command в пуле!'
    print('OK: Tier-3 инструменты изолированы')
asyncio.run(check())
"

# 2. Создать тестовое PendingPrivilegedAction и проверить approve-поток:
curl -X POST http://localhost:8000/privileged-actions \
  -H 'Content-Type: application/json' \
  -d '{"action_type":"coder_task","target":"backend/test.py","instruction":"Добавь комментарий","reasoning":"Тест"}'
# Затем approve → проверить что diff создан в /tmp/coder_sandbox_* а не в main
# Затем apply-diff → проверить что изменение применено

# 3. Alembic migration:
docker exec contextus_backend alembic upgrade head
```

### Manual Verification

- Открыть `/improvements` в браузере, убедиться что таблица пустая (нет записей)
- Нажать 🔍 "Проанализировать сессию" на завершённой сессии, увидеть текстовый отчёт в чате
- Создать `coder_task` через UI (или API), нажать Approve — убедиться что статус сменился на `diff_ready`
- Просмотреть diff в UI, нажать Apply — убедиться что статус `applied` и файл изменился
- Проверить что `improvements_log.md` создаётся только как информационный файл

