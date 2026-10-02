"""
Артефакты и журнал прогонов: привязка к задаче, план из subtasks, отдача содержимого.

Ключевой страж — path traversal: `path` в БД недоверенный (артефакт может создать скачивание,
агент или внешний MCP), поэтому выход за корень артефактов обязан отвергаться, а не отдавать
файл. Плюс проверяем, что мягкое удаление скрывает карточку, но не стирает историю.
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
from services import artifacts as artifacts_service

engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


@pytest.fixture
def client(tmp_path, monkeypatch):
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

    # Корень артефактов — во временном каталоге: тест не трогает рабочий artifacts/
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    yield TestClient(main.app)

    main.app.dependency_overrides.clear()
    main.SessionLocal, database.SessionLocal = orig_main_sl, orig_db_sl
    Base.metadata.drop_all(bind=engine)


def _admin_cookie(db) -> str:
    user = models.User(role="admin", display_name="Artifacts Admin")
    db.add(user)
    db.commit()
    db.refresh(user)
    return create_session_token(user.id)


def _seed_task(db, *, with_plan: bool = True):
    conv = models.Conversation(title="Артефакты")
    db.add(conv)
    db.flush()
    task = models.AgentTask(
        owner_id="1", status="completed", query="сделай отчёт", conversation_id=conv.id
    )
    db.add(task)
    db.flush()
    if with_plan:
        db.add(
            models.AgentSubtask(
                task_id=task.id, topic="Собрать данные", prompt_instruction="…",
                target_role="web_researcher", execution_order=1, status="completed",
                result_output="нашли 3 источника",
            )
        )
        db.add(
            models.AgentSubtask(
                task_id=task.id, topic="Оформить отчёт", prompt_instruction="…",
                target_role="coder", execution_order=2, status="running",
            )
        )
    db.commit()
    return conv.id, task.id


def test_run_card_has_live_plan_and_artifacts(client):
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    conv_id, task_id = _seed_task(db)
    artifacts_service.publish(db, task_id=task_id, kind="report", title="Отчёт", content="# Итог\nвсё ок")
    artifacts_service.save_file(db, task_id=task_id, filename="данные.csv", data=b"a,b\n1,2\n")
    db.close()

    resp = client.get(f"/tasks/{task_id}/artifacts", cookies={"contextus_session": cookie})
    assert resp.status_code == 200
    card = resp.json()

    steps = card["plan"]["steps"]
    assert [s["order"] for s in steps] == [1, 2], "план должен идти в порядке выполнения"
    assert [s["status"] for s in steps] == ["completed", "running"], "статусы шагов — живые"
    assert "- [x] 1." in card["plan"]["markdown"] and "- [~] 2." in card["plan"]["markdown"]

    kinds = sorted(a["kind"] for a in card["artifacts"])
    assert kinds == ["file", "report"]
    assert card["journal"] is None, "журнала ещё нет — карточка это допускает"


def test_content_endpoint_serves_db_disk_and_url(client):
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    _, task_id = _seed_task(db, with_plan=False)
    text = artifacts_service.publish(db, task_id=task_id, kind="text", title="Заметка", content="привет")
    file_row = artifacts_service.save_file(db, task_id=task_id, filename="отчёт.md", data=b"# ok\n")
    url_row = artifacts_service.publish(db, task_id=task_id, kind="link", title="Источник", url="https://example.com/x")
    db.close()

    db_body = client.get(f"/artifacts/{text.id}/content", cookies={"contextus_session": cookie})
    assert db_body.status_code == 200 and db_body.text == "привет"

    disk_body = client.get(f"/artifacts/{file_row.id}/content", cookies={"contextus_session": cookie})
    assert disk_body.status_code == 200 and disk_body.content == b"# ok\n"
    assert "attachment" in disk_body.headers.get("content-disposition", "")

    redirect = client.get(
        f"/artifacts/{url_row.id}/content", cookies={"contextus_session": cookie}, follow_redirects=False
    )
    assert redirect.status_code == 302 and redirect.headers["location"] == "https://example.com/x"


def test_path_traversal_is_rejected(client):
    """Артефакт с path за пределами корня не должен отдавать файл (и уж тем более /etc/passwd)."""
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    _, task_id = _seed_task(db, with_plan=False)
    evil = artifacts_service.publish(
        db, task_id=task_id, kind="file", title="вредный", path="../../../../etc/passwd"
    )
    db.close()

    resp = client.get(f"/artifacts/{evil.id}/content", cookies={"contextus_session": cookie})
    assert resp.status_code == 400, resp.text
    assert "root:" not in resp.text, "файл вне корня не должен утекать"


def test_soft_delete_hides_but_keeps_history(client):
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    conv_id, task_id = _seed_task(db, with_plan=False)
    row = artifacts_service.publish(db, task_id=task_id, kind="text", title="Черновик", content="x")
    db.close()

    assert client.delete(f"/artifacts/{row.id}", cookies={"contextus_session": cookie}).status_code == 200

    card = client.get(f"/tasks/{task_id}/artifacts", cookies={"contextus_session": cookie}).json()
    assert card["artifacts"] == [], "удалённый артефакт исчезает из карточки"
    assert client.get(f"/artifacts/{row.id}/content", cookies={"contextus_session": cookie}).status_code == 404

    db = TestingSessionLocal()
    kept = db.query(models.Artifact).filter(models.Artifact.id == row.id).first()
    assert kept is not None and kept.deleted_at is not None, "история не стирается — только помечается"
    db.close()


def test_runs_for_conversation_lists_history(client):
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    conv_id, first_task = _seed_task(db)
    second = models.AgentTask(owner_id="1", status="failed", query="упавший прогон", conversation_id=conv_id)
    db.add(second)
    db.commit()
    artifacts_service.upsert_journal(db, first_task, status="success", summary="отчёт готов")
    artifacts_service.upsert_journal(db, second.id, status="failed", errors=["RuntimeError: тест"])
    db.close()

    runs = client.get(f"/conversations/{conv_id}/runs", cookies={"contextus_session": cookie}).json()
    assert len(runs) == 2
    newest, oldest = runs[0], runs[1]
    assert newest["task_id"] == second.id, "свежие прогоны сверху"
    assert newest["journal"]["status"] == "failed"
    assert oldest["plan_steps"] == 2 and oldest["journal"]["summary"] == "отчёт готов"


def test_publish_without_task_is_rejected(client):
    db = TestingSessionLocal()
    cookie = _admin_cookie(db)
    db.close()

    resp = client.post(
        "/artifacts",
        json={"task_id": "no-such-task", "kind": "text", "title": "x", "content": "y"},
        cookies={"contextus_session": cookie},
    )
    assert resp.status_code == 400, "артефакт без владельца создавать нельзя"
