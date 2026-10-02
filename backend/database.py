from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# We'll use SQLite for now, keeping PostgreSQL in mind for the future.
# Alembic will help us migrate later.
#
# URL переопределяется через env DATABASE_URL — это нужно тестам: они выставляют
# свой путь в conftest.py ДО импорта приложения, и тогда даже прямые ссылки вида
# `from database import SessionLocal` в других модулях не смогут дотянуться до
# рабочей contextus.db (раньше так в аудит просачивались данные pytest).
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./contextus.db")


def enable_sqlite_fk(engine) -> None:
    """
    Включает принуждение внешних ключей для SQLite.

    По умолчанию SQLite НЕ принуждает FK (PRAGMA foreign_keys=OFF), поэтому объявленные
    в моделях ondelete="CASCADE"/"SET NULL" молча не работают — контракт схемы существует
    только на бумаге, и копятся висячие ссылки (в рабочей БД так накопилось 68 у
    agent_tasks.conversation_id после удаления бесед).

    Прагма действует per-connection, поэтому включаем её на каждом новом соединении.
    У PostgreSQL FK-принуждение включено всегда, поэтому там ничего не делаем.
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
enable_sqlite_fk(engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency for FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
