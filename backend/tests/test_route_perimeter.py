"""
Тест-страж периметра: ни один роут не должен отвечать анониму.

Повод: класс дыр находился СЛУЧАЙНО дважды — сначала `/roles`/`/privileged-actions`,
потом весь кластер `/conversations**` (+ `/sources**`, `/analytics**` и прочие,
закрытые в этом же проходе). Оба раза это были открытые данные, найденные побочным
эффектом другой задачи. Страховка от третьего случайного обнаружения — этот тест:
он перебирает ВСЕ роуты приложения и требует, чтобы каждый отвечал 401/403 без сессии,
кроме явного allowlist публичных (вход в систему/вебхук).

Новый роут без защиты валит тест сразу, а не «когда-нибудь найдут при расследовании
чего-то несвязанного». Роуты с машинным периметром (WORKER_SECRET: /agent/run,
/agent/task/*/stream, /agent/task/*/cancel) тоже проходят проверку — они обязаны
отвергать анонимный запрос.
"""

import re
import sys
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

import main  # noqa: E402

#: Публичные по замыслу. Добавление сюда — осознанное решение, требующее причины:
#:   /telegram/webhook          — защищён секретом X-Telegram-Bot-Api-Secret-Token (fail-closed молчание);
#:   /api/auth/login-with-token — сам выдаёт сессию, проверяет одноразовый токен из Telegram;
#:   /api/auth/me               — нужен странице входа, отдаёт состояние «кто я»/null;
#:   /api/auth/logout           — идемпотентен и без сессии, только чистит cookie.
PUBLIC_ENDPOINTS = {
    ("POST", "/telegram/webhook"),
    ("POST", "/api/auth/login-with-token"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/logout"),
}


def _concrete(path: str) -> str:
    """Подставляет кодовые значения вместо path-параметров: /a/{x}/b -> /a/x/b."""
    return re.sub(r"\{[^}]+\}", "x", path)


def test_every_route_is_closed_to_anonymous():
    client = TestClient(main.app)
    checked = []

    for route in main.app.routes:
        if not isinstance(route, APIRoute):
            continue  # docs/openapi/static — не часть API-периметра
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            if (method, route.path) in PUBLIC_ENDPOINTS:
                continue
            kwargs = {} if method == "GET" else {"json": {}}
            with client.stream(method, _concrete(route.path), **kwargs) as resp:
                assert resp.status_code in (401, 403), (
                    f"{method} {route.path} отвечает анониму {resp.status_code} — "
                    "роут не защищён сессией (require_admin/require_user) "
                    "или машинным WORKER_SECRET. Если это осознанно публичный роут — "
                    "добавь его в PUBLIC_ENDPOINTS с причиной."
                )
                checked.append(f"{method} {route.path}")

    assert len(checked) > 30, (
        f"Проверено всего {len(checked)} роутов — похоже, перебор сломался "
        "(routes не собираются?), тест превратился в фикцию"
    )
