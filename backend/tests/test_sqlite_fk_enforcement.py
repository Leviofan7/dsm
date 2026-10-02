"""
FK-принуждение SQLite: контракт схемы должен исполняться, а не декларироваться.

Контекст: `PRAGMA foreign_keys` в SQLite по умолчанию OFF, поэтому все объявленные
`ondelete="CASCADE"/"SET NULL"` молча не работали — DELETE /conversations/{id} оставлял
висячие ссылки в agent_tasks (68 накопилось в рабочей БД). Теперь прагма включается
на каждом соединении: в проде — `database.enable_sqlite_fk(engine)`, в тестах —
класс-левел listener в conftest (иначе тестовые in-memory движки проверяли бы
поведение БЕЗ принуждения, то есть не проверяли бы главный контракт).

Тесты ниже стерегут сам механизм: без него регресс вернётся тихо — ссылки снова
начнут копиться, а весь остальной прогон этого не заметит.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from database import Base, enable_sqlite_fk
import models  # noqa: F401 — регистрирует таблицы в Base.metadata

engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
# conftest уже включает FK для всех sqlite-движков; вызов ниже — страховка на случай
# изолированного запуска файла без conftest.
enable_sqlite_fk(engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _fresh_schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def test_pragma_is_on():
    db = TestingSessionLocal()
    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1, "без прагмы контракт схемы мертв"
    db.close()


def test_message_with_unknown_conversation_rejected():
    """Ссылка на несуществующую беседу должна падать, а не копиться как висяк."""
    db = TestingSessionLocal()
    db.add(models.Message(conversation_id="no-such-conv", role="user", content="x"))
    with pytest.raises(IntegrityError) as err:
        db.commit()
    assert "FOREIGN KEY" in str(err.value).upper()
    db.rollback()
    db.close()


def test_delete_conversation_sets_task_null_at_db_level():
    """DELETE беседы на уровне SQL => agent_tasks.conversation_id обнуляется САМА (SET NULL)."""
    db = TestingSessionLocal()
    conv = models.Conversation(title="fk")
    db.add(conv)
    db.flush()
    task = models.AgentTask(owner_id="1", status="pending", query="q", conversation_id=conv.id)
    db.add(task)
    db.commit()
    conv_id, task_id = conv.id, task.id
    db.close()

    db = TestingSessionLocal()
    db.execute(text("DELETE FROM conversations WHERE id = :c"), {"c": conv_id})  # без ORM-каскадов
    db.commit()
    left = db.execute(
        text("SELECT conversation_id FROM agent_tasks WHERE id = :t"), {"t": task_id}
    ).scalar()
    db.close()
    assert left is None, "ondelete=SET NULL должен обнулять ссылку на уровне БД"


def test_delete_task_cascades_events():
    """DELETE задачи на уровне SQL => события уходят вместе с ней (CASCADE)."""
    db = TestingSessionLocal()
    task = models.AgentTask(owner_id="1", status="pending", query="q")
    db.add(task)
    db.flush()
    db.add(
        models.AgentTaskEvent(task_id=task.id, sequence_number=0, event_type="stream", payload="x")
    )
    db.commit()
    task_id = task.id
    db.close()

    db = TestingSessionLocal()
    db.execute(text("DELETE FROM agent_tasks WHERE id = :t"), {"t": task_id})
    db.commit()
    left = db.execute(
        text("SELECT COUNT(*) FROM agent_task_events WHERE task_id = :t"), {"t": task_id}
    ).scalar()
    db.close()
    assert left == 0, "ondelete=CASCADE должен удалять события вместе с задачей"
