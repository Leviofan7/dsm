"""
Гигиена stdout у MCP-серверов: stdout — протокольный канал JSON-RPC, печатать туда нельзя.

Инцидент 26.09 (виден в логах как простыни «Failed to parse JSONRPC message»): web-stealth
печатал отладочные строки браузера (`[🌐 Browser] ...`) и многострочные тексты исключений
Playwright в stdout. MCP-клиент пытался парсить каждую строку как JSON-RPC и на каждую
сыпал ValidationError с трейсбеком. Опаснее шума то, что печать может вклиниться в середину
настоящего сообщения и сломать протокол по-настоящему.

Второй стерегомый регресс: из `agent/browser.py` однажды пропал `import httpx` — путь
запуска реального Chrome через Launcher Daemon молча ломался (NameError маскировался под
«демон недоступен»), и агент каждый раз падал в headless-фолбэк.

Файлы в списке — те, что реально исполняются ВНУТРИ MCP stdio-подпроцессов
(web_stealth.py и импортируемый им agent/browser.py). Если сервер начнёт импортировать
новый проектный модуль — добавь его сюда.
"""

import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

MCP_SUBPROCESS_FILES = [
    BACKEND_DIR / "mcp_servers" / "web_stealth.py",
    BACKEND_DIR / "agent" / "browser.py",
]


def test_no_bare_print_into_stdout():
    """
    Ищем только КОД: `print(` в начале строки, вне docstring и комментариев.

    Сканировать «любое print( в строке» нельзя — это ловит саму документацию
    (первая версия стража падала на docstring browser.py, который объясняет правило
    и упоминает print()).
    """
    offenders = []
    for path in MCP_SUBPROCESS_FILES:
        in_docstring = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            triples = stripped.count('"""') + stripped.count("'''")
            if in_docstring:
                if triples:
                    in_docstring = False
                continue
            if stripped.startswith(('"""', "'''")):
                if triples % 2 == 1:
                    in_docstring = True
                continue
            if stripped.startswith("#"):
                continue
            if re.match(r"\s*print\(", line) and "file=" not in line:
                offenders.append(f"{path.name}:{lineno}: {stripped[:90]}")

    assert not offenders, (
        "Печать в stdout внутри MCP-подпроцесса ломает JSON-RPC канал — "
        "нужен file=sys.stderr:\n  " + "\n  ".join(offenders)
    )


def test_browser_module_has_httpx_for_launcher():
    """Launcher-путь зовёт httpx.AsyncClient — импорт обязан присутствовать (был регресс)."""
    import httpx

    import agent.browser as browser_module

    assert browser_module.httpx is httpx, (
        "из agent/browser.py пропал `import httpx` — запуск реального Chrome через "
        "Launcher Daemon снова будет падать NameError'ом и маскироваться под «демон недоступен»"
    )
