import asyncio
import json
import os
import base64
import math
import random
from pathlib import Path
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth

# Rotating User-Agent pool — common desktop browsers
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# Keywords that indicate access denied / bot detection pages
_BLOCK_KEYWORDS = [
    "access denied", "403 forbidden", "запрещено", "доступ запрещён",
    "доступ запрещен", "blocked", "cloudflare", "captcha", "hcaptcha",
    "recaptcha", "just a moment", "verify you are human",
    "checking your browser", "attention required", "ray id",
    "if this persists, please email us", "anonymized error code",
    "context of your search",
]

class BrowserSession:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.session_file = Path(__file__).parent / "session.json"

    async def start(self, target_url: str = None):
        self.playwright = await async_playwright().start()
        
        # Определяем хост для CDP (если в Докере — подключаемся к хосту)
        in_docker = os.path.exists("/.dockerenv") or os.getenv("PROJECT_ROOT") is not None
        cdp_host = "host.docker.internal" if in_docker else "localhost"
        cdp_port = 9223 if in_docker else 9222
        cdp_url = f"http://{cdp_host}:{cdp_port}"
        
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
            "--no-sandbox",
        ]

        connected_via_cdp = False

        # ── Попытка 1: подключиться к уже запущенному Chrome по CDP ──
        try:
            print(f"[🌐 Browser] Подключаюсь к Chrome по CDP ({cdp_url})...")
            self.browser = await self.playwright.chromium.connect_over_cdp(cdp_url)
            connected_via_cdp = True
            print(f"[✅ Browser] Подключён к Chrome по CDP — GUI-режим активен!")
        except Exception as e:
            print(f"[⚠️ Browser] CDP на {cdp_url} недоступен: {e}")

        # ── Попытка 2: запуск Chrome на хосте через Launcher Daemon ──
        if not connected_via_cdp:
            launcher_port = 9224
            launcher_host = cdp_host  # host.docker.internal в Docker, localhost локально
            launcher_url = f"http://{launcher_host}:{launcher_port}/launch"
            
            try:
                print(f"[🚀 Browser] Отправляю запрос на запуск Chrome → {launcher_url}")
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(launcher_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        print(f"[🚀 Browser] Launcher ответил: {data.get('status', 'ok')}")
                        
                        # Даём Chrome секунду на стабилизацию после запуска
                        await asyncio.sleep(1.0)
                        
                        # Пробуем подключиться по CDP
                        try:
                            self.browser = await self.playwright.chromium.connect_over_cdp(cdp_url)
                            connected_via_cdp = True
                            print(f"[✅ Browser] Chrome запущен демоном и подключён по CDP — GUI-режим!")
                        except Exception as e_cdp:
                            print(f"[⚠️ Browser] Chrome запущен, но CDP недоступен: {e_cdp}")
                    else:
                        print(f"[⚠️ Browser] Launcher вернул {resp.status_code}: {resp.text}")
            except Exception as e_launcher:
                print(f"[⚠️ Browser] Launcher Daemon недоступен ({launcher_url}): {e_launcher}")

        # ── Попытка 3: локальный headless Chromium (гарантированный fallback) ──
        if not connected_via_cdp:
            print(f"[⚠️ Browser] CDP недоступен. Запускаю встроенный headless Chromium...")
            try:
                self.browser = await self.playwright.chromium.launch(headless=True, args=launch_args)
                print(f"[✅ Browser] Headless Chromium запущен (без GUI-окна)")
            except Exception as e_launch:
                print(f"[❌ Browser] Не удалось запустить Chromium: {e_launch}")
                raise e_launch
            
        # Берем дефолтный контекст
        if self.browser.contexts:
            self.context = self.browser.contexts[0]
        else:
            self.context = await self.browser.new_context()
                
        # Поиск подходящей вкладки
        if target_url:
            import urllib.parse
            domain = urllib.parse.urlparse(target_url).netloc
            found = await self.connect_to_tab(domain)
            if found:
                print(f"[✅ Browser] Найдена открытая вкладка с {domain}")
            else:
                print(f"[✅ Browser] Открыта новая вкладка для {domain}")
        else:
            if not self.page:
                if self.context.pages and self.context.pages[0].url == "about:blank":
                    self.page = self.context.pages[0]
                else:
                    self.page = await self.context.new_page()
                
        # Очистка пустых вкладок (about:blank), чтобы не мусорить
        for p in self.context.pages:
            if p != self.page and p.url == "about:blank":
                try:
                    await p.close()
                except Exception:
                    pass
                
        # Дополнительно накатываем stealth, чтобы подчистить мелкие JS-переменные
        await Stealth().apply_stealth_async(self.page)
        print("[✅ Browser] Успешно подключено. Браузер готов к работе.")

    async def close(self):
        # ВАЖНО: При работе по CDP метод self.browser.close() закрывать НЕ НАДО, 
        # иначе мы убьем сам системный процесс Chrome. Просто отключаемся.
        if self.playwright:
            await self.playwright.stop()
            print("[🌐 Browser] Сессия отладки Playwright завершена.")

    async def connect_to_tab(self, target_domain: str) -> bool:
        """Ищет открытую вкладку по домену и делает ее активной, исключая дубликаты"""
        if not self.context:
            raise Exception("Браузерный контекст не инициализирован")
            
        for page in self.context.pages:
            if target_domain in page.url:
                self.page = page
                await self.page.bring_to_front()
                await asyncio.sleep(0.5)
                return True
        
        self.page = await self.context.new_page()
        return False

    async def save_session(self):
        pass

    async def clear_session(self):
        pass

    async def goto(self, url: str):
        if self.page:
            # 1. Ждем базовой загрузки HTML
            await self.page.goto(url, wait_until="domcontentloaded")
            
            try:
                # 2. Ждем, пока прекратится активный сетевой трафик (подгрузка JS/шрифтов React'ом)
                await self.page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass  # Игнорируем тайм-аут, если сайт держит долгие SSE-соединения
                
            # 3. КРИТИЧНО: Даем 1.5 секунды на завершение CSS-анимаций и стабилизацию Virtual DOM
            await asyncio.sleep(1.5)

    async def click(self, element_id: int):
        """Click using data-agent-id generated by the DOM map — stealth version with Bezier mouse movement."""
        if self.page:
            locator = self.page.locator(f"[data-agent-id='{element_id}']")
            await locator.scroll_into_view_if_needed()
            await asyncio.sleep(random.uniform(0.1, 0.3))

            # Получаем bounding box элемента для стелс-клика по координатам
            bbox = await locator.bounding_box()
            if bbox:
                await self._stealth_click_bbox(bbox)
            else:
                # Фолбек: обычный клик если bbox недоступен
                await locator.click()

            try:
                await self.page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

    async def click_xy(self, x: int, y: int):
        """Click by absolute coordinates. Used primarily by Vision models."""
        if self.page:
            await self.page.mouse.click(x, y)
            await self.page.wait_for_load_state("networkidle")

    async def click_shadow(self, host_selector: str, inner_selector: str):
        """Click inside a closed Shadow DOM."""
        if self.page:
            await self.page.evaluate(f'''() => {{
                const host = document.querySelector("{host_selector}");
                if (host && host.shadowRoot) {{
                    const el = host.shadowRoot.querySelector("{inner_selector}");
                    if (el) el.click();
                }}
            }}''')
            await self.page.wait_for_load_state("networkidle")
            
    async def type_text(self, element_id: int, text: str, press_enter: bool = False):
        """Type text into a specific element — stealth version with human-like timing."""
        if self.page:
            locator = self.page.locator(f"[data-agent-id='{element_id}']")
            await locator.scroll_into_view_if_needed()

            # Стелс: кликаем по элементу через Bezier перед вводом
            bbox = await locator.bounding_box()
            if bbox:
                await self._stealth_click_bbox(bbox)
                await asyncio.sleep(random.uniform(0.2, 0.5))
            else:
                await locator.focus()
            
            # Посимвольный ввод с human-like задержками (0.05 — 0.15с)
            for i, char in enumerate(text):
                await self.page.keyboard.type(char)
                # Базовая задержка между символами
                delay = random.uniform(0.05, 0.15)
                # Дополнительная пауза после пробелов/знаков препинания (имитация размышления)
                if char in ' .,!?;:':
                    delay += random.uniform(0.05, 0.25)
                # Каждые 5-15 символов — микропауза (человек смотрит на экран)
                if i > 0 and i % random.randint(5, 15) == 0:
                    delay += random.uniform(0.2, 0.6)
                await asyncio.sleep(delay)
                
            # React/Next.js state sync fallback: dispatch input and change events
            await self.page.evaluate(f'''() => {{
                const el = document.querySelector("[data-agent-id='{element_id}']");
                if (el) {{
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }}''')
                
            if press_enter:
                await asyncio.sleep(random.uniform(0.3, 1.2))
                await self.page.keyboard.press("Enter")
                await asyncio.sleep(2)
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass

    async def press_key(self, key: str):
        """Press a specific keyboard key"""
        if self.page:
            await self.page.keyboard.press(key)
            await asyncio.sleep(0.5)

    async def scroll(self, direction: str = "down"):
        """
        Физический скролл колесиком мыши по центру вьюпорта (Self-Healing).
        Решает проблему SPA-интерфейсов, где window.scrollBy заблокирован фреймворком.
        """
        if self.page:
            # Получаем реальный размер текущего окна (дефолт, если упало)
            viewport = self.page.viewport_size or {"width": 1280, "height": 800}
            center_x = viewport["width"] / 2
            center_y = viewport["height"] / 2
            
            # Шаг прокрутки — уверенные 60% от высоты экрана
            scroll_step = int(viewport["height"] * 0.6)
            delta_y = scroll_step if direction == "down" else -scroll_step
            
            try:
                # 1. Перемещаем виртуальный курсор мыши в центр экрана чата
                await self.page.mouse.move(center_x, center_y)
                
                # 2. Крутим колесико мыши по оси Y
                await self.page.mouse.wheel(delta_x=0, delta_y=delta_y)
                
                # 3. КРИТИЧНО: Даем время на плавную анимацию прокрутки и подгрузку контента (Lazy Load)
                await asyncio.sleep(1.2)
                print(f"[⚙️ Browser] Выполнен физический скролл {direction} на {scroll_step}px")
            except Exception as e:
                print(f"[⚠️ Browser] Ошибка физического скролла: {e}. Пробую плавный фолбек...")
                # Резервный вариант, если мышь заблокирована
                delta = "window.innerHeight * 0.6" if direction == "down" else "-window.innerHeight * 0.6"
                await self.page.evaluate(f"window.scrollBy({{ top: {delta}, behavior: 'smooth' }})")
                await asyncio.sleep(1.0)

    async def scroll_to_element(self, selector: str):
        """Smoothly scroll a specific element into the center of the viewport."""
        if self.page:
            await self.page.evaluate(f'''() => {{
                const el = document.querySelector("{selector}");
                if (el) el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
            }}''')
            await asyncio.sleep(1.0)  # Let scroll animation finish

    async def get_interactive_elements_map(self) -> list:
        """Сканирует DOM и возвращает карту интерактивных элементов с Bounding Boxes для стелс-взаимодействия."""
        if not self.page:
            return []
            
        elements_map = await self.page.evaluate('''() => {
            // ПЕРВЫМ ДЕЛОМ: Стираем ВСЕ старые data-agent-id
            document.querySelectorAll('[data-agent-id]').forEach(el => {
                el.removeAttribute('data-agent-id');
            });

            // Собираем интерактивные элементы и значимые текстовые блоки
            const selectors = 'input, textarea, button, select, [role="button"], [role="link"], [role="tab"], [role="menuitem"], a, [onclick], [tabindex], h1, h2, h3, h4, h5, h6, p, span';
            const interactives = document.querySelectorAll(selectors);
            let map_list = [];
            let currentId = 1;
            
            interactives.forEach(el => {
                // Жесткая фильтрация неинформативных тегов
                const tag = el.tagName.toLowerCase();
                if (['script', 'style', 'svg', 'path', 'noscript', 'meta', 'link'].includes(tag)) {
                    return;
                }

                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                
                // Элемент должен быть видимым на экране прямо сейчас
                const isVisible = rect.width > 0 && rect.height > 0 && 
                                  style.display !== 'none' && 
                                  style.visibility !== 'hidden' && 
                                  style.opacity !== '0' &&
                                  rect.top >= -10 && rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) + 10;
                                  
                if (isVisible) {
                    let textContent = el.innerText || el.placeholder || el.value || el.getAttribute('aria-label') || '';
                    textContent = textContent.replace(/\\n/g, ' ').substring(0, 100).trim();
                    const role = el.getAttribute('role') || '';
                    const inputType = el.getAttribute('type') || '';
                    const href = tag === 'a' ? (el.getAttribute('href') || '').substring(0, 100) : '';
                    
                    const isInteractive = ['input', 'textarea', 'button', 'select', 'a'].includes(tag) || role !== '' || el.hasAttribute('onclick') || el.hasAttribute('tabindex');
                    
                    // Сохраняем элемент, если он интерактивный ИЛИ если это значимый текст (непустой)
                    if (isInteractive || (textContent.length > 3)) {
                        map_list.push({
                            id: currentId,
                            tag: tag,
                            type: inputType,
                            role: role,
                            text: textContent,
                            href: href,
                            bbox: {
                                x: Math.round(rect.x),
                                y: Math.round(rect.y),
                                width: Math.round(rect.width),
                                height: Math.round(rect.height)
                            }
                        });
                        el.setAttribute('data-agent-id', currentId);
                        currentId++;
                    }
                }
            });
            return map_list;
        }''')
        return elements_map

    # ── Stealth Mouse Movement (Bezier Curves) ────────────────────

    def _bezier_point(self, t: float, p0: tuple, p1: tuple, p2: tuple, p3: tuple) -> tuple:
        """Вычисляет точку на кубической кривой Безье для заданного t (0..1)."""
        u = 1 - t
        return (
            u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1],
        )

    def _generate_bezier_path(self, start: tuple, end: tuple, steps: int = 15) -> list[tuple]:
        """
        Генерирует путь движения мыши по кривой Безье между двумя точками.
        Контрольные точки расставляются случайно для имитации естественного движения руки.
        """
        # Случайные контрольные точки — отклонение от прямой линии
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        
        # Контрольные точки смещены перпендикулярно основному вектору
        offset1 = random.uniform(-abs(dy) * 0.3, abs(dy) * 0.3)
        offset2 = random.uniform(-abs(dx) * 0.3, abs(dx) * 0.3)
        
        cp1 = (
            start[0] + dx * random.uniform(0.2, 0.4) + offset1,
            start[1] + dy * random.uniform(0.2, 0.4) + offset2,
        )
        cp2 = (
            start[0] + dx * random.uniform(0.6, 0.8) - offset1,
            start[1] + dy * random.uniform(0.6, 0.8) - offset2,
        )
        
        path = []
        for i in range(steps + 1):
            t = i / steps
            # Небольшое нелинейное ускорение (ease-in-out)
            t = t * t * (3 - 2 * t)
            point = self._bezier_point(t, start, cp1, cp2, end)
            path.append(point)
        return path

    async def _stealth_move_mouse(self, target_x: float, target_y: float):
        """Плавно перемещает мышь к цели по кривой Безье."""
        if not self.page:
            return
        
        # Текущая позиция мыши (если неизвестна — берем случайную точку на экране)
        viewport = self.page.viewport_size or {"width": 1280, "height": 800}
        current_x = random.uniform(viewport["width"] * 0.1, viewport["width"] * 0.9)
        current_y = random.uniform(viewport["height"] * 0.1, viewport["height"] * 0.9)
        
        # Генерируем путь Безье
        steps = random.randint(10, 20)
        path = self._generate_bezier_path(
            (current_x, current_y),
            (target_x, target_y),
            steps=steps,
        )
        
        # Двигаем мышь по точкам
        for point in path:
            await self.page.mouse.move(point[0], point[1])
            await asyncio.sleep(random.uniform(0.005, 0.025))

    async def _stealth_click_bbox(self, bbox: dict):
        """
        Стелс-клик по элементу через его bounding box:
        1. Вычисляет случайную точку внутри элемента (20-85% от краев)
        2. Перемещает мышь по кривой Безье
        3. Делает микропаузу перед кликом
        4. Кликает
        """
        # Случайная точка внутри элемента (смещение от центра 20-85%)
        offset_x = random.uniform(0.2, 0.85) * bbox["width"]
        offset_y = random.uniform(0.2, 0.85) * bbox["height"]
        target_x = bbox["x"] + offset_x
        target_y = bbox["y"] + offset_y
        
        # Плавное движение мыши
        await self._stealth_move_mouse(target_x, target_y)
        
        # Микропауза перед кликом (человек «наводится»)
        await asyncio.sleep(random.uniform(0.05, 0.15))
        
        # Клик
        await self.page.mouse.click(target_x, target_y)

    async def take_screenshot(self) -> str:
        """Takes a viewport screenshot and returns as base64 string."""
        if self.page:
            screenshot_bytes = await self.page.screenshot(full_page=False)
            return base64.b64encode(screenshot_bytes).decode("utf-8")
        return ""

    # ── Stealth Warmup (Ritual of Landing) ────────────────────────
    
    async def simulate_mouse_wandering(self):
        if not self.page: return
        viewport = self.page.viewport_size or {"width": 1280, "height": 800}
        moves = random.randint(1, 2)
        for _ in range(moves):
            x = random.uniform(viewport["width"] * 0.1, viewport["width"] * 0.9)
            y = random.uniform(viewport["height"] * 0.1, viewport["height"] * 0.5)
            await self._stealth_move_mouse(x, y)
            await asyncio.sleep(random.uniform(0.2, 0.5))

    async def simulate_exploratory_scroll(self):
        if not self.page: return
        scroll_down = random.randint(150, 400)
        await self.page.mouse.wheel(delta_x=0, delta_y=scroll_down)
        await asyncio.sleep(random.uniform(0.4, 0.9))
        if random.random() > 0.5:
            scroll_up = -random.randint(50, 100)
            await self.page.mouse.wheel(delta_x=0, delta_y=scroll_up)
        else:
            scroll_more = random.randint(50, 100)
            await self.page.mouse.wheel(delta_x=0, delta_y=scroll_more)
        await asyncio.sleep(random.uniform(0.2, 0.5))

    async def hover_neutral_element(self):
        if not self.page: return
        try:
            element = await self.page.evaluate_handle('''() => {
                const elements = Array.from(document.querySelectorAll('h1, h2, h3, .logo, a'));
                return elements.find(el => {
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0 && rect.top > 0 && rect.top < window.innerHeight;
                });
            }''')
            if element:
                bbox = await element.bounding_box()
                if bbox:
                    target_x = bbox["x"] + bbox["width"] / 2
                    target_y = bbox["y"] + bbox["height"] / 2
                    await self._stealth_move_mouse(target_x, target_y)
                    await asyncio.sleep(random.uniform(0.3, 0.8))
        except Exception as e:
            print(f"[⚠️ Browser] hover_neutral_element error: {e}")

    async def simulate_text_selection(self):
        if not self.page: return
        try:
            element = await self.page.evaluate_handle('''() => {
                const paragraphs = Array.from(document.querySelectorAll('p, span, div'));
                return paragraphs.find(el => {
                    const rect = el.getBoundingClientRect();
                    return rect.width > 150 && rect.height > 20 && rect.top > 0 && rect.top < window.innerHeight && el.innerText.length > 20;
                });
            }''')
            if element:
                bbox = await element.bounding_box()
                if bbox:
                    start_x = bbox["x"] + random.uniform(5, 15)
                    start_y = bbox["y"] + bbox["height"] / 2
                    await self._stealth_move_mouse(start_x, start_y)
                    await self.page.mouse.down()
                    
                    end_x = start_x + random.uniform(80, 120)
                    end_y = start_y + random.uniform(-5, 5)
                    await self._stealth_move_mouse(end_x, end_y)
                    await self.page.mouse.up()
                    
                    await asyncio.sleep(random.uniform(0.5, 1.2))
                    await self.page.evaluate("window.getSelection().removeAllRanges()")
        except Exception as e:
            print(f"[⚠️ Browser] simulate_text_selection error: {e}")

    # ── Resilience methods ────────────────────────────────────────

    async def detect_access_denied(self) -> bool:
        """
        Checks the current page for signs of 403 / Cloudflare / captcha blocks.
        Returns True if the page appears to be blocked.
        """
        if not self.page:
            return False
        try:
            title = (await self.page.title()).lower()
            # Check a small snippet of visible body text (first 2000 chars)
            body_text = await self.page.evaluate(
                "() => (document.body?.innerText || '').slice(0, 2000).toLowerCase()"
            )
            combined = title + " " + body_text
            return any(kw in combined for kw in _BLOCK_KEYWORDS)
        except Exception:
            return False

    async def dismiss_modal_if_present(self) -> bool:
        """Ищет типичные кнопки подтверждения и кликает если есть."""
        if not self.page:
            return False
            
        selectors = [
            "button:has-text('Продолжить')",
            "button:has-text('Continue')",
            "button:has-text('Accept')",
            "button:has-text('Agree')",
            "button:has-text('OK')",
            "button:has-text('Принять')",
            "[aria-label='Close']",
            "[aria-label='Закрыть']"
        ]
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.is_visible(timeout=500):
                    await el.click()
                    await self.page.wait_for_load_state("networkidle", timeout=2000)
                    return True
            except Exception:
                pass
        return False

    async def refresh_cookies(self):
        """Clears cookies and reloads the page to try to get a fresh session."""
        if not self.context or not self.page:
            return
        url = self.page.url
        await self.context.clear_cookies()
        await self.page.reload(wait_until="networkidle")

    async def rotate_user_agent(self):
        """
        Creates a brand-new browser context with a different User-Agent.
        Navigates back to the same URL.
        """
        if not self.browser or not self.page:
            return
        url = self.page.url
        # Close old context
        if self.context:
            await self.context.close()
        # Open new context with a different UA
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=random.choice(_USER_AGENTS),
            locale="ru-RU",
            timezone_id="Europe/Kiev"
        )
        self.page = await self.context.new_page()
        await Stealth().apply_stealth_async(self.page)
        await self.page.goto(url, wait_until="networkidle")


class FileTools:
    @staticmethod
    def read_full_file(project_root: str, relative_path: str) -> str:
        """Безопасное чтение файла целиком для контекста LLM"""
        from pathlib import Path
        # Защита от выхода за пределы папки проекта (Path Traversal)
        safe_path = Path(project_root).resolve() / relative_path
        if not safe_path.resolve().is_relative_to(Path(project_root).resolve()):
            raise Exception("Попытка несанкционированного доступа к системным файлам!")
            
        if not safe_path.exists():
            return f"Ошибка: Файл {relative_path} не найден."
            
        # Проверяем размер, чтобы не повесить контекст
        if safe_path.stat().st_size > 200 * 1024: # 200 KB максимум
            return "Ошибка: Файл слишком большой для чтения целиком. Используйте RAG-поиск."
            
        with open(safe_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            
        return f"--- СОДЕРЖИМОЕ ФАЙЛА {relative_path} ---\n{content}"
