"""
Изоляция тестов от рабочей БД — на уровне импорта, а не договорённости.

Повод: в аудите рабочей БД нашлась запись `callback_denied` с target
`ar_approve_test_action_id` — след pytest. Причина не в конкретном тесте, а в том,
что часть модулей делает `from database import SessionLocal` НА УРОВНЕ МОДУЛЯ
(например `api/action_requests.py`). Подмена `main.SessionLocal` /
`database.SessionLocal` такие ссылки не перекрывает, поэтому написание в рабочую
contextus.db остаётся возможным для любого теста, который дёрнет такую функцию.

Решение: среда выставляется ДО импорта приложения (conftest грузится первым),
`database.py` уважает DATABASE_URL, поэтому ВСЕ модули, включая прямые ссылки,
берут тестовую БД. Тестовая БД — файл в /tmp: pytest в контейнере, рабочая БД
там же (`/app/contextus.db`), то есть одного env-переопределения достаточно.
"""

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

#: Путь тестовой БД. Отдельный файл — чтобы рабочая contextus.db была недостижима.
TEST_DB_PATH = Path(tempfile.gettempdir()) / "contextus_pytest.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

# Секреты фиксируем здесь, ДО первого импорта main: раньше их выставлял
# test_auth_and_security.py, и любой файл, импортировавший main раньше него (например
# test_action_request_queue.py — он идёт первым по алфавиту), защёлкивал РЕАЛЬНЫЙ секрет
# из окружения. Тогда вебхук-тесты получали 401, и падение зависело от порядка файлов.
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test_webhook_secret_12345"
os.environ["WEB_AUTH_SECRET"] = "test_web_auth_secret_67890"

# FK-принуждение для ВСЕХ sqlite-соединений в тестах. Продовый engine включает прагму
# через database.enable_sqlite_fk, но тестовые файлы создают собственные in-memory движки
# (и без этого прогон не проверял бы главный контракт схемы — ondelete CASCADE/SET NULL).
# Именно из-за выключенной прагмы в проде копились висячие ссылки: хотели проверять
# поведение с принуждением, а тесты проверяли поведение без него.
import sqlite3

from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "connect")
def _fk_on_for_sqlite(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def pytest_configure(config):
    """Страховка + чистая тестовая БД на каждый запуск."""
    from database import SQLALCHEMY_DATABASE_URL, Base, engine

    assert "contextus_pytest.db" in SQLALCHEMY_DATABASE_URL, (
        f"тесты подключены к неожиданной БД: {SQLALCHEMY_DATABASE_URL}. "
        "Похоже, это рабочая contextus.db — проверьте DATABASE_URL в conftest.py"
    )

    # Чистим таблицы, чтобы прогоны не влияли друг на друга. ВАЖНО: модели должны быть
    # импортированы ДО drop_all — иначе метаданные пусты, drop ничего не удаляет, а create_all
    # потом пропустит существующие таблицы со СТАРОЙ схемой (так уже воспроизвелось:
    # в temp-БД остался NOT NULL там, где модель уже разрешает NULL).
    import models  # noqa: F401 — регистрирует таблицы в Base.metadata

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
