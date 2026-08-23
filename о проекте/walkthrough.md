# Отчет о возможностях: Автономный Веб-Агент («Звонок Другу»)

Мы создали гибридного ИИ-агента, интегрированного в Next.js фронтенд и FastAPI бекенд, способного самостоятельно решать задачи с использованием эмулируемого браузера Playwright и локальной Vision-модели Ollama (`gemma4:e4b`).

Ниже представлен подробный разбор текущих возможностей, архитектуры и настроек.

---

## 🗺️ Общая архитектура решения

Агент работает по гибридной схеме: если задача простая (вопрос по коду), он отвечает напрямую. Если требуется доступ к внешнему сайту или автоматизация — запускается цикл `Observe-Think-Act` в браузере.

```mermaid
graph TD
    User([Пользователь]) -->|Запрос в чат| NextJS[Next.js API Route /api/chat]
    NextJS -->|Проверка режима| Mode{Режим?}
    
    Mode -->|Автономный выключен| OllamaDirect[Прямой Ollama Chat API]
    Mode -->|⚡ Автономный активен| FastAPI[FastAPI Backend /agent/run]
    
    FastAPI --> Orchestrator[orchestrator.py: run_agent_loop]
    Orchestrator --> Classifier[Classifier: Нужен ли браузер?]
    
    Classifier -->|Нет| DirectAns[Прямой ответ модели]
    Classifier -->|Да| BrowserStart[Запуск Playwright Browser Session]
    
    subgraph Цикл Observe-Think-Act (макс. 12 шагов)
        BrowserStart --> Screenshot[1. Observe: Скриншот видимой зоны]
        Screenshot --> Vision[2. Think: Vision-модель + credentials]
        Vision --> Act{3. Act: Действие?}
        
        Act -->|click x, y| ExecuteClick[Клик по координатам] --> Screenshot
        Act -->|type text| ExecuteType[Ввод текста] --> Screenshot
        Act -->|error| StopError[Остановка с ошибкой]
        Act -->|done| StopDone[Успешное завершение]
    end
    
    ExecuteClick -.-> UIStream[SSE: Живой поток мыслей и шагов в чат]
    ExecuteType -.-> UIStream
    StopError -.-> UIStream
    StopDone -.-> UIStream
```

---

## 🛠️ Детальное описание модулей и возможностей

### 1. 🌐 Управление сессией браузера (`browser.py`)
* **Слепой режим (Headless):** Запускает скрытый Chromium для тихой работы на сервере.
* **Сохранение сессии (`session.json`):** При логине на любой сайт автоматически экспортирует `storage_state` (куки, LocalStorage, IndexedDB). При следующем запуске сессия восстанавливается — агенту не нужно логиниться заново.
* **Снимки видимой зоны (Viewport screenshots):** Делает скриншоты фиксированного размера, адаптированные под разрешение контекста Vision-модели.
* **Абсолютные клики по координатам (`click_xy`):** Кликает в точные точки `x, y`, присланные моделью, имитируя реальные действия пользователя.
* **Shadow DOM Bypass (`click_shadow`):** Специальный JS-коннектор для взаимодействия со скрытыми элементами внутри закрытых Shadow Roots.

### 2. 🧠 Классификация задач и Vision-модуль (`vision.py`)
* **Автоматический роутинг:** Анализирует запрос пользователя. Например:
  * *«Что такое React?»* → Модель отвечает мгновенно без браузера.
  * *«Зайди на google.com и найди погоду»* → Инициирует запуск браузера на указанном URL.
* **Контекст учетных данных (Credentials Injection):** Если на фронтенде заданы аккаунты, их логины и пароли передаются в промпт Vision-модели. При обнаружении формы авторизации модель сама берет нужные данные из контекста и выполняет `type` и `click`.
* **JSON-контракт:** Модель настроена на строгий формат ответов:
  ```json
  {"action": "click", "x": 450, "y": 200}
  // или
  {"action": "type", "text": "my-login"}
  ```

### 3. ⚡ Сохранение и передача настроек аккаунтов (`agent-toggle.tsx`)
* **Управление из чата:** Нажатие на иконку ⚡/⚙️ открывает диалоговое окно «Звонок другу».
* **Поля аккаунта:** Пользователь может задать название, целевой URL, логин и пароль.
* **Локальная безопасность:** Данные сохраняются исключительно в `localStorage` вашего браузера и отправляются на локальный сервер только при включенном автономном режиме.

### 4. 💬 Интерактивный лог мыслей (`agent-thinking-bubble.tsx`)
Вместо скрытых логов в консоли, все шаги агента теперь рендерятся прямо внутри чата:
* **Интерактивный спойлер:** Можно кликнуть по шапке, чтобы развернуть или скрыть лог.
* **Живой поток (Streaming):** Шаги появляются в реальном времени с пульсирующим курсором на текущей задаче.
* **Цветовая индикация состояния:**
  * 🟡 **Желтый/Фиолетовый** — агент работает.
  * 🟢 **Зеленый** — задача успешно решена.
  * 🔴 **Красный** — произошла ошибка.
* **Кастомные иконки:** Каждый шаг логически визуализирован (эмуляция браузера 🌐, анализ экрана 📷, генерация ответа ⚡).

---

## 📝 Сводная таблица компонентов

| Файл | Назначение | Текущий статус |
|---|---|---|
| [`browser.py`](file:///home/ai-line/Projects/data-sources-management/backend/agent/browser.py) | Playwright, снимки экрана, клики, shadow DOM, сессии. | **Готов / Протестирован** |
| [`vision.py`](file:///home/ai-line/Projects/data-sources-management/backend/agent/vision.py) | API Ollama `/api/generate`, классификатор задач, парсер JSON. | **Готов / Протестирован** |
| [`orchestrator.py`](file:///home/ai-line/Projects/data-sources-management/backend/agent/orchestrator.py) | Главный цикл `max_steps=12`, генерация SSE, инъекция credentials. | **Готов / Протестирован** |
| [`main.py`](file:///home/ai-line/Projects/data-sources-management/backend/main.py) | FastAPI сервер, проверка Bearer токенов, стриминг SSE. | **Готов / Протестирован** |
| [`route.ts`](file:///home/ai-line/Projects/data-sources-management/app/api/chat/route.ts) | Роутинг запросов Next.js, проксирование на FastAPI. | **Готов / Протестирован** |
| [`chat-interface.tsx`](file:///home/ai-line/Projects/data-sources-management/components/chat/chat-interface.tsx) | Подключение SSE-потока, рендер интерфейса, синхронизация localStorage. | **Готов / Протестирован** |
| [`agent-toggle.tsx`](file:///home/ai-line/Projects/data-sources-management/components/chat/agent-toggle.tsx) | Настройки аккаунтов, форм ввода логинов/паролей. | **Готов / Протестирован** |
| [`agent-thinking-bubble.tsx`](file:///home/ai-line/Projects/data-sources-management/components/chat/agent-thinking-bubble.tsx) | Живой раскрываемый лог шагов агента внутри чата. | **Готов / Протестирован** |

---

> [!TIP]
> **Как проверить автозаполнение паролей:**
> 1. Откройте чат, нажмите на фиолетовую молнию ⚡.
> 2. Заполните аккаунт: URL `https://duck.ai/chat`, логин `test-user`, любой пароль.
> 3. Нажмите «Сохранить и закрыть».
> 4. Введите в чат: *«Зайди на duck.ai/chat и авторизуйся»*.
> 5. Откройте разворачивающийся лог в чате: вы увидите, как агент запустит браузер, передаст credentials в Vision модель и начнет процесс заполнения формы.
