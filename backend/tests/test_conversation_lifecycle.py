"""
Жизненный цикл беседы: удаление не оставляет «висячих» ссылок и закрыто от анонимов.

Регрессия №1: DELETE /conversations/{id} удалял беседу, а agent_tasks.conversation_id
продолжал ссылаться на несуществующую запись. В схеме объявлен ondelete="SET NULL",
но SQLite не принуждает внешние ключи (PRAGMA foreign_keys=OFF), поэтому БД сама ничего
не обнуляет. В рабочей БД так накопилось 68 ссылок на удалённые беседы.
Теперь эндпоинт снимает ссылки сам; задача при этом выживает — это история и сырьё аналитики.

Регрессия №2: восемь эндпоинтов бесед были открыты вообще без аутентификации
(чтение/правка/удаление сообщений). Закрыты Depends(require_admin) — как остальной периметр.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from auth import create_session_token
import database
from database import Base, get_db
import main
import models

engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = override_get_db
    orig_main_sl, orig_db_sl = main.SessionLocal, database.SessionLocal
    main.SessionLocal = TestingSessionLocal
    database.SessionLocal = TestingSessionLocal

    yield TestClient(main.app)

    main.app.dependency_overrides.clear()
    main.SessionLocal, database.SessionLocal = orig_main_sl, orig_db_sl
    Base.metadata.drop_all(bind=engine)


def _admin_cookie(db) -> str:
    user = models.User(role="admin", display_name="Conv Admin")
    db.add(user)
    db.commit()
    db.refresh(user)
    return create_session_token(user.id)


def test_delete_conversation_detaches_tasks_and_removes_children(client):
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)

    conv = models.Conversation(title="Проба удаления")
    db.add(conv)
    db.flush()
    db.add(models.Message(conversation_id=conv.id, role="user", content="привет"))
    src = models.Source(name="s", type="local", detail="/tmp")
    db.add(src)
    db.flush()
    conv.sources.append(src)
    task = models.AgentTask(owner_id="1", status="completed", query="вопрос", conversation_id=conv.id)
    db.add(task)
    db.commit()
    conv_id, task_id = conv.id, task.id
    db.close()

    resp = client.delete(f"/conversations/{conv_id}", cookies={"contextus_session": cookie})
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "detached_tasks": 1}

    db = TestingSessionLocal()
    assert db.query(models.Conversation).filter_by(id=conv_id).first() is None
    assert db.query(models.Message).filter_by(conversation_id=conv_id).count() == 0
    assoc = db.execute(
        text("SELECT COUNT(*) FROM conversation_sources WHERE conversation_id = :c"), {"c": conv_id}
    ).scalar()
    assert assoc == 0, "связи беседы с источниками должны уйти вместе с беседой"

    task_after = db.query(models.AgentTask).filter_by(id=task_id).first()
    assert task_after is not None, "задачу удалять нельзя — это история"
    assert task_after.conversation_id is None, "ссылка на удалённую беседу должна быть снята"
    db.close()


def test_delete_conversation_without_tasks_reports_zero(client):
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    conv = models.Conversation(title="Пустая")
    db.add(conv)
    db.commit()
    conv_id = conv.id
    db.close()

    resp = client.delete(f"/conversations/{conv_id}", cookies={"contextus_session": cookie})
    assert resp.status_code == 200
    assert resp.json()["detached_tasks"] == 0


def test_conversation_endpoints_require_auth(client):
    """Без cookie — 401 на всех восьми эндпоинтах бесед (раньше были открыты вовсе)."""
    for method, path in [
        ("get", "/conversations"),
        ("post", "/conversations"),
        ("put", "/conversations/no-such"),
        ("delete", "/conversations/no-such"),
        ("get", "/conversations/no-such/messages"),
        ("post", "/conversations/no-such/messages"),
        ("put", "/conversations/no-such/messages/no-such"),
        ("delete", "/conversations/no-such/messages/no-such"),
    ]:
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, f"{method.upper()} {path} → {resp.status_code}, ожидали 401"

    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    db.close()
    ok = client.get("/conversations", cookies={"contextus_session": cookie})
    assert ok.status_code == 200, "с админ-cookie доступ обязан работать"
