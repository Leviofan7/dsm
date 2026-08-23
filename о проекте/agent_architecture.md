# Архитектура веб-агента: сводный документ

> Версия: финальная. Охватывает все решения принятые в ходе проектирования.

---

## Обзор цикла

```
Observe → Think → Act → Verify → [следующий шаг или retry]
```

При смене хэша страницы или URL — история сбрасывается.  
Максимум шагов: **12**. Максимум неудачных verify подряд: **3**.

---

## Модуль 1: `browser.py`

### Сессия

Использовать `storage_state` вместо `add_cookies`.  
Сохраняет куки + localStorage + IndexedDB — критично для SPA с JWT-токенами.

```python
# Сохранить
await context.storage_state(path="agent/session.json")

# Загрузить при старте
context = await browser.new_context(storage_state="agent/session.json")
```

### Клики — два метода

| Метод | Когда использовать |
|---|---|
| `click(selector)` | CSS-селекторы, автоматически пробивает открытый Shadow DOM |
| `click_xy(x, y)` | Координаты от Vision-модели — основной метод |
| `click_shadow(host, inner)` | Только для closed Shadow DOM (через `page.evaluate`) |

**XPath не использовать** — не пробивает Shadow DOM.

### Надёжный ввод текста (`type_text`)

Цепочка вместо слепого `page.fill()`:

1. Вычислить центр элемента
2. `page.mouse.click(x, y)` — физический клик для фокуса
3. Микрозадержка 100–200 мс
4. `page.keyboard.type(text, delay=random.randint(20, 50))` — имитация человека
5. Диспатч событий для React:

```python
await page.evaluate("""
    el => {
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }
""", element)
```

> Duck.ai работает на Next.js — нужны оба события (`input` и `change`).

### Логика выбора поля для ввода (приоритет по убыванию)

1. `document.activeElement` — если уже в фокусе после клика
2. Visible + in viewport + семантика (`placeholder` или `aria-label` содержит: search, поиск, ответить, reply, message)
3. Самое крупное видимое поле на экране
4. Если не найдено → вернуть `False`, оркестратор получает ошибку

**Никогда:** не брать "последнее поле на странице" — источник бага с вводом не туда.

### Скриншот

```python
await page.screenshot(path=path, full_page=False, type="png")
```

`full_page=False` — только viewport. Vision видит то, что видит пользователь.

### Восстановление после глитча Duck.ai

Приоритетная цепочка (не сразу `reload`):

```
1. Найти чат в левом сайдбаре → кликнуть (переподключает WebSocket)
2. Если сайдбар пустой → ждать 3 сек → новый Observe
3. Только если 2 попытки провалились → page.reload() как крайняя мера
```

---

## Модуль 2: `vision.py`

### Контракт `analyze(screenshot, goal) → dict`

```json
{"action": "click",  "x": 412, "y": 307}
{"action": "type",   "text": "запрос пользователя"}
{"action": "wait"}
{"action": "done",   "result": "текст финального ответа"}
{"action": "error",  "reason": "описание почему застряли"}
```

**Важно:** Vision возвращает координаты (x, y), не CSS-селекторы. Модель видит пиксели.

### Системный промпт — приоритеты

```
ПРИОРИТЕТЫ (строго по порядку):
1. Если есть модальное окно, попап, кнопка "Продолжить" / "Accept" / "Agree" — кликни ПЕРВОЙ.
2. Если страница загружается (спиннер, skeleton) — верни {"action": "wait"}.
3. Если на экране ошибка сервиса ("Упс...") — найди чат в левом сайдбаре и кликни по нему.
4. Если экран чистый — найди поле ввода и введи запрос.
5. Если ответ получен и отображается полностью — верни {"action": "done", "result": "..."}.

[ИСТОРИЯ — только справочно, не копируй прошлые ответы]:
{history_text}

[ТЕКУЩЕЕ СОСТОЯНИЕ — это главное, доверяй только скриншоту]:
<screenshot>

Страница на скриншоте является ЕДИНСТВЕННЫМ источником истины.
Если на скриншоте нет ошибки — ошибки нет.
```

### Контракт `verify(screenshot, last_action, goal) → dict`

```json
{"success": true}
{"success": false, "reason": "модалка не закрылась"}
```

Промпт короткий и бинарный — только да/нет. Не давать модели думать о следующем шаге.

### Когда вызывать `verify`

| Действие | Нужен verify |
|---|---|
| `click` | ✅ да |
| `type` | ✅ да |
| `wait` | ❌ нет |
| `done` | ❌ нет |
| `goto` (Playwright сам кидает исключение) | ❌ нет |

---

## Модуль 3: `orchestrator.py`

### Полный цикл

```python
async def run_agent_loop(query, max_steps=12):
    history = []          # текстовая история последних 7 шагов
    prev_url = ""
    prev_hash = ""
    loop_count = 0
    captcha_attempts = 0

    for step in range(1, max_steps + 1):

        # Шаг 1: Observe
        html, screenshot = await browser.get_page_content()
        current_url = await browser.get_current_url()
        current_hash = get_page_hash(screenshot)

        # Хэш не меняется 3 шага подряд → StateStuckException
        if current_hash == prev_hash:
            stuck_count += 1
            if stuck_count >= 3:
                raise StateStuckException("Страница не меняется 3 шага подряд")
        else:
            stuck_count = 0

        # URL изменился → сбросить счётчик зацикливания
        if current_url != prev_url:
            loop_count = 0

        # Хэш изменился → очистить историю (старый контекст устарел)
        if current_hash != prev_hash and prev_hash != "":
            history.clear()

        prev_url = current_url
        prev_hash = current_hash

        # Шаг 2: Think
        history_text = format_history(history[-7:])
        action = await vision.analyze(screenshot, query, history_text)

        # Шаг 3: Act
        if action["action"] == "wait":
            await asyncio.sleep(1.5)
            continue  # не считать как шаг

        elif action["action"] == "done":
            yield {"type": "done", "result": action["result"]}
            return

        elif action["action"] == "error":
            yield {"type": "error", "reason": action["reason"]}
            return

        elif action["action"] == "captcha":
            # см. раздел HITL ниже
            ...

        else:
            await execute_action(action, browser)

            # Шаг 4: Verify (только для click и type)
            await asyncio.sleep(0.8)
            verify_screenshot = await browser.take_screenshot()
            result = await vision.verify(verify_screenshot, action, query)

            history.append(f"Шаг {step}: {action} -> {'ok' if result['success'] else 'fail: ' + result.get('reason','')}")

            if not result["success"]:
                loop_count += 1
                yield {"type": "step", "step": step, "status": "retry", "reason": result["reason"]}
            else:
                loop_count = 0
                yield {"type": "step", "step": step, "status": "ok", "action": action}
```

### Хэширование состояний

```python
# pip install imagehash pillow
import imagehash
from PIL import Image

def get_page_hash(screenshot_path: str) -> str:
    return imagehash.phash(Image.open(screenshot_path))

# Сравнение — не строгое равенство, устойчиво к микроанимациям
def hashes_equal(h1, h2) -> bool:
    return (h1 - h2) <= 5  # расстояние Хэмминга
```

### Детектор зацикливания

Зависание = **и действие, и URL не изменились**.  
Смена URL = прогресс → счётчик сбрасывается.  
Смена хэша страницы → история очищается.

### HITL — обработка капчи (двухэтапная)

```
captcha_attempts = 0  # переменная состояния сессии

Если обнаружена капча:

  Если captcha_attempts == 0:
    → Лог: "[🤖 Агент] Обнаружена защита. Пробую решить локальной Vision-моделью..."
    → Передать в Vision изолированный промпт: найти координаты чекбокса
      "Verify you are human" / "Я человек"
    → Кликнуть, подождать 2 секунды, сделать ре-диагностику
    → captcha_attempts += 1

  Если captcha_attempts > 0 (модель уже пробовала):
    → Лог: "[⚠️ Агент] Локальная модель не справилась с капчей. Жду человека."
    → SSE на фронтенд: {"type": "hitl", "reason": "captcha", "timeout": 30}
    → asyncio.sleep(30)
    → captcha_attempts = 0
    → history.clear()  # сбросить отравленный контекст
```

### SSE-события (полный список)

```json
{"type": "step",   "step": 3, "status": "ok",    "action": {...}}
{"type": "step",   "step": 3, "status": "retry",  "reason": "модалка не закрылась"}
{"type": "hitl",   "reason": "captcha",           "timeout": 30}
{"type": "error",  "stage": "vision",             "reason": "..."}
{"type": "done",   "result": "текст ответа"}
```

---

## Отчёт по завершению сессии

```markdown
### 📋 Отчет о выполнении задачи
* **Цель:** {query}
* **Статус:** Успешно / Ошибка сервиса / Блокировка / Капча (HITL)
* **Количество шагов:** {step}/12
* **Выполненные действия:**
  - Шаг 1: {действие} → {результат verify}
  - Шаг 2: {действие} → {результат verify}
  - Шаг N: {финальный результат или описание затыка}
```

---

## Важные ограничения Vision-модели (Gemma 4 / малые модели)

**Text Anchoring Bias** — если в истории написано "сервис недоступен", модель может
скопировать этот ответ даже когда скриншот чистый. Решение: очищать историю при смене хэша.

**Координаты, не селекторы** — модель возвращает `{"x": 412, "y": 307}`, не CSS.

**Контекстное окно** — Ollama по умолчанию 4K. Обязательно создать Modelfile:
```
FROM gemma4:4b
PARAMETER num_ctx 8192
PARAMETER temperature 0.2
```

**История** — передавать только последние 7 шагов текстом. Скриншоты прошлых шагов
не передавать — они съедают контекст.

---

## Порядок реализации

1. `browser.py` — надёжный `type_text` (фундамент)
2. `vision.py` — обновить промпт + добавить `verify_action`
3. `orchestrator.py` — хэширование, очистка истории при смене состояния
4. `orchestrator.py` — HITL капча двухэтапная
5. `orchestrator.py` — восстановление через сайдбар Duck.ai вместо reload
