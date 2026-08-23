import asyncio
import sys
import os
import json

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP
from agent.browser import BrowserSession

# Create FastMCP server instance
mcp = FastMCP("web-stealth", instructions="Browser automation server with stealth capabilities and human-like interaction")

# Global browser session state
browser = BrowserSession(headless=False)


@mcp.tool()
async def goto_url(url: str) -> str:
    """Navigate to a given URL. Must be called before interacting with elements. Returns the loaded page text automatically."""
    if not browser.playwright:
        await browser.start(target_url=url)
    await browser.goto(url)
    
    # 1. Проверка блокировки ДО прогрева (спалили железо/IP)
    blocked_early = await browser.detect_access_denied()
    if blocked_early:
        import json
        return json.dumps({
            "__hitl_request__": True,
            "reason": f"Страница {url} заблокирована ДО прогрева (Cloudflare/капча/403). Открой браузер, реши капчу и напиши 'готово'.",
            "current_url": url,
            "screenshot": ""
        })

    # 2. Ритуал приземления (поведенческий прогрев)
    STEALTH_WARMUP_ENABLED = os.environ.get("STEALTH_WARMUP_ENABLED", "True").lower() in ["true", "1", "yes"]
    if STEALTH_WARMUP_ENABLED:
        import random
        print("[*] Запуск ритуала приземления (поведенческий прогрев)...")
        await asyncio.sleep(random.uniform(0.5, 1.2))
        await browser.simulate_mouse_wandering()
        await browser.simulate_exploratory_scroll()
        await browser.hover_neutral_element()
        await browser.simulate_text_selection()
        print("[*] Прогрев завершен. Страница готова к анализу ИИ.")
        
        # 3. Проверка блокировки ПОСЛЕ прогрева (агрессивный антифрод)
        blocked_late = await browser.detect_access_denied()
        if blocked_late:
            import json
            return json.dumps({
                "__hitl_request__": True,
                "reason": f"Страница {url} заблокирована ПОСЛЕ прогрева (агрессивный антифрод). Открой браузер, реши капчу и напиши 'готово'.",
                "current_url": url,
                "screenshot": ""
            })

    # Сразу собираем текст, чтобы локальная модель (Gemma/Qwen) не галлюцинировала
    page_text = ""
    if browser.page:
        page_text = await browser.page.evaluate('''(maxChars) => {
            const removeSelectors = ['script', 'style', 'nav', 'header', 'footer', 'iframe', 'noscript', 'svg'];
            const clone = document.body.cloneNode(true);
            removeSelectors.forEach(sel => {
                clone.querySelectorAll(sel).forEach(el => el.remove());
            });
            return (clone.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim().substring(0, maxChars);
        }''', 3000)
        
    return f"Navigated to {url}. Страница загружена.\n\nСОДЕРЖИМОЕ СТРАНИЦЫ:\n{page_text}\n\nЕсли нужно кликать - вызови get_dom_map."


@mcp.tool()
async def get_dom_map() -> str:
    """Gets the interactive elements map of the current page. Returns a JSON list of elements with id, tag, text, role, type, href, and bounding box (bbox: x, y, width, height) for stealth interaction. ALWAYS call this before clicking or typing."""
    if not browser.page:
        return json.dumps({"error": "Browser not started or page not loaded."})
    elements = await browser.get_interactive_elements_map()
    return json.dumps(elements, ensure_ascii=False)


@mcp.tool()
async def take_screenshot() -> str:
    """Takes a screenshot of the current viewport. Returns a base64 encoded string. Use for visual analysis when DOM map is insufficient or to verify page state."""
    if not browser.page:
        return "Error: Browser not started or page not loaded."
    return await browser.take_screenshot()


@mcp.tool()
async def click_element(element_id: int) -> str:
    """Clicks an element by its data-agent-id obtained from get_dom_map. Uses stealth Bezier-curve mouse movement to a randomized point within the element — mimics human behavior."""
    if not browser.page:
        return "Error: Browser not started or page not loaded."
    try:
        await browser.click(element_id)
        return f"Successfully clicked element {element_id} (stealth mode)"
    except Exception as e:
        return f"Error clicking element {element_id}: {e}. Попробуй вызвать get_dom_map заново для обновления карты элементов."


@mcp.tool()
async def type_text(element_id: int, text: str, press_enter: bool = False) -> str:
    """Types text into an element by its data-agent-id with human-like per-character timing. Uses stealth click to focus the element first."""
    if not browser.page:
        return "Error: Browser not started or page not loaded."
    try:
        await browser.type_text(element_id, text, press_enter)
        return f"Successfully typed text into element {element_id} (stealth mode)"
    except Exception as e:
        return f"Error typing into element {element_id}: {e}. Попробуй вызвать get_dom_map заново."


@mcp.tool()
async def scroll(direction: str = "down") -> str:
    """Scrolls the page up or down using physical mouse wheel simulation."""
    if not browser.page:
        return "Error: Browser not started or page not loaded."
    await browser.scroll(direction)
    return f"Successfully scrolled {direction}. Вызови get_dom_map или take_screenshot чтобы увидеть новые элементы."


@mcp.tool()
async def detect_block() -> str:
    """Checks if the current page is blocked by Cloudflare, captcha, or access denied. Returns detection result."""
    if not browser.page:
        return "Error: Browser not started or page not loaded."
    blocked = await browser.detect_access_denied()
    if blocked:
        return "[CLOUDFLARE_DETECTED] Страница заблокирована (Cloudflare/капча/403). Вызови request_human_help для помощи человека."
    return "OK: Страница доступна, блокировка не обнаружена."


@mcp.tool()
async def wait_for_human_captcha(reason: str = "Капча") -> str:
    """
    Останавливает агента, оставляет окно браузера открытым
    и ждет, пока человек решит капчу в интерфейсе.
    """
    import sys
    print(f"\n[HITL ВНИМАНИЕ]: Агент заблокирован. Причина: {reason}", file=sys.stderr)
    print("-> Перейди в открывшееся окно браузера и реши капчу руками.", file=sys.stderr)
    
    # Блокирующее ожидание ввода (через /dev/tty, чтобы не сломать MCP sys.stdin)
    try:
        with open("/dev/tty", "r") as tty:
            print("--> После того как капча будет успешно пройдена, нажми ENTER здесь для продолжения...", file=sys.stderr)
            tty.readline()
    except Exception:
        # Если /dev/tty недоступен (например, в Docker без TTY), используем механизм Telegram-паузы
        print("[HITL] Терминал недоступен. Отправлен запрос в Telegram...", file=sys.stderr)
        import json
        return json.dumps({"__hitl_request__": True, "reason": f"{reason}. Открой браузер и реши капчу, затем напиши 'готово' здесь."})

    return "Капча пройдена человеком. Сессия активна, можно продолжать запросы."


@mcp.tool()
async def request_human_help(reason: str) -> str:
    """
    Request human intervention when the agent is stuck.
    Call this when:
    - Cloudflare/captcha is detected
    - An element cannot be clicked or found
    - The page requires manual authentication
    
    The system will pause execution, send a screenshot to the human via Telegram, 
    and wait for their response before continuing.
    Returns the human's response when they reply.
    """
    # Этот инструмент возвращает специальный маркер, который Supervisor в llm_manager
    # перехватывает и инициирует HITL-процедуру
    screenshot = ""
    if browser.page:
        screenshot = await browser.take_screenshot()
    
    return json.dumps({
        "__hitl_request__": True,
        "reason": reason,
        "screenshot": screenshot,
        "current_url": browser.page.url if browser.page else "unknown",
    })


@mcp.tool()
async def web_search(query: str) -> str:
    """Search the web using Google. Returns top search results with titles, URLs, and snippets.
    This is the PRIMARY tool for answering questions that require up-to-date or factual information.
    ALWAYS use this tool when the user asks to 'find', 'search', 'google', or 'look up' something."""
    if not browser.playwright:
        await browser.start()
    
    import urllib.parse
    search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&hl=ru"
    
    await browser.goto(search_url)
    
    # Проверяем блокировку
    blocked = await browser.detect_access_denied()
    if blocked:
        # Фолбек: пробуем DuckDuckGo
        ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        await browser.goto(ddg_url)
        
        blocked_ddg = await browser.detect_access_denied()
        if blocked_ddg:
            import json
            return json.dumps({
                "__hitl_request__": True,
                "reason": f"Поиск заблокирован (Google и DuckDuckGo). Открой браузер, реши капчу и напиши 'готово'.",
                "current_url": browser.page.url,
                "screenshot": ""
            })
    
    # Извлекаем результаты поиска
    results = await browser.page.evaluate('''() => {
        let results = [];
        
        // Google results
        document.querySelectorAll('div.g, div[data-hveid]').forEach(el => {
            const titleEl = el.querySelector('h3');
            const linkEl = el.querySelector('a[href]');
            const snippetEl = el.querySelector('[data-sncf], .VwiC3b, [style*="-webkit-line-clamp"]');
            if (titleEl && linkEl) {
                results.push({
                    title: titleEl.innerText.trim(),
                    url: linkEl.href,
                    snippet: snippetEl ? snippetEl.innerText.trim().substring(0, 300) : ''
                });
            }
        });
        
        // DuckDuckGo fallback results
        if (results.length === 0) {
            document.querySelectorAll('.result, .web-result').forEach(el => {
                const titleEl = el.querySelector('.result__title a, .result__a');
                const snippetEl = el.querySelector('.result__snippet');
                if (titleEl) {
                    results.push({
                        title: titleEl.innerText.trim(),
                        url: titleEl.href || '',
                        snippet: snippetEl ? snippetEl.innerText.trim().substring(0, 300) : ''
                    });
                }
            });
        }
        
        return results.slice(0, 8);
    }''')
    
    if not results:
        # Последний фолбек: вернуть сырой текст страницы
        text = await browser.page.evaluate("() => (document.body?.innerText || '').slice(0, 4000)")
        return f"Поиск по запросу: {query}\n\nРезультаты (сырой текст):\n{text}"
    
    # Форматируем результаты в читаемый текст
    output_lines = [f"🔍 Результаты поиска: «{query}»\n"]
    for i, r in enumerate(results, 1):
        output_lines.append(f"{i}. **{r['title']}**")
        output_lines.append(f"   URL: {r['url']}")
        if r['snippet']:
            output_lines.append(f"   {r['snippet']}")
        output_lines.append("")
    
    return "\n".join(output_lines)


@mcp.tool()
async def get_page_text(max_chars: int = 5000) -> str:
    """Extracts readable text content from the current page. Use after goto_url to read article content, search results, etc. Returns clean text without HTML."""
    if not browser.page:
        return "Error: Browser not started or page not loaded."
    
    text = await browser.page.evaluate('''(maxChars) => {
        // Удаляем ненужные элементы
        const removeSelectors = ['script', 'style', 'nav', 'header', 'footer', 'iframe', 'noscript', 'svg'];
        const clone = document.body.cloneNode(true);
        removeSelectors.forEach(sel => {
            clone.querySelectorAll(sel).forEach(el => el.remove());
        });
        return (clone.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim().substring(0, maxChars);
    }''', max_chars)
    
    current_url = browser.page.url
    return f"URL: {current_url}\n\nСодержимое страницы:\n{text}"

@mcp.tool()
async def get_raw_html() -> str:
    """Returns the raw HTML of the current page for Supervisor DOM hashing. Hidden from normal LLM usage by convention."""
    if not browser.page:
        return ""
    return await browser.page.content()

if __name__ == "__main__":
    # Start the MCP stdio server
    mcp.run()
