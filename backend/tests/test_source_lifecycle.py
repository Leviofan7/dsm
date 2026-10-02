"""
Удаление источника: чанки уходят вместе с ним — проверено ПРИ включённом FK-принуждении.

Почему отдельный тест: chunks.source_id — FK с NO ACTION, а история знает 150 чанков-сирот
(следы до-фиксовых удалений, вычищены 26.09). После включения PRAGMA foreign_keys=ON любой
путь удаления источника, не чистящий чанки раньше родителя, обязан падать ГРОМКО
(IntegrityError), а не тихо копить мусор. Этот тест фиксирует, что рабочий путь чистый:
DELETE через тот же эндпоинт, что использует UI, при живых чанках — 200 и ноль чанков.

Если кто-то перепишет эндпоинт на bulk `db.query(Source).filter(...).delete()` (в обход
ORM-каскада) — с включённой прагмой этот тест упадёт IntegrityError'ом, и это ровно то
поведение, которое мы хотим: fail loud вместо silent poisoning.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
def client(monkeypatch):
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

    # ChromaDB в тесте не наш: векторную часть глушим, проверяем именно SQL-периметр.
    monkeypatch.setattr(main, "delete_source_collection", lambda sid: None)

    yield TestClient(main.app)

    main.app.dependency_overrides.clear()
    main.SessionLocal, database.SessionLocal = orig_main_sl, orig_db_sl
    Base.metadata.drop_all(bind=engine)


def _admin_cookie(db) -> str:
    user = models.User(role="admin", display_name="Src Admin")
    db.add(user)
    db.commit()
    db.refresh(user)
    return create_session_token(user.id)


def test_delete_source_with_chunks_succeeds(client):
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    src = models.Source(name="live-delete-check", type="local", detail="/tmp/x", status="ready")
    db.add(src)
    db.flush()
    for i in range(3):
        db.add(
            models.Chunk(
                source_id=src.id, file_path=f"f{i}.py", chunk_index=i, content_preview="x", token_count=5
            )
        )
    db.commit()
    sid = src.id
    db.close()

    resp = client.delete(f"/sources/{sid}", cookies={"contextus_session": cookie})
    assert resp.status_code == 200
    assert resp.json() == {"success": True}

    db = TestingSessionLocal()
    assert db.query(models.Source).filter_by(id=sid).first() is None
    assert db.query(models.Chunk).filter_by(source_id=sid).count() == 0, (
        "чанки должны уйти вместе с источником (иначе при FK=ON следующий DELETE упадёт IntegrityError)"
    )
    db.close()


def test_delete_source_without_chunks_and_second_delete_404(client):
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    src = models.Source(name="empty", type="local", detail="/tmp/y", status="ready")
    db.add(src)
    db.commit()
    sid = src.id
    db.close()

    assert client.delete(f"/sources/{sid}", cookies={"contextus_session": cookie}).status_code == 200
    assert client.delete(f"/sources/{sid}", cookies={"contextus_session": cookie}).status_code == 404
