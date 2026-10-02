"""
MCP-сервер `artifacts`: агент сам складывает результат прогона в панель.

Главное здесь — fail-closed по владельцу: без служебного id прогона (`_meta`) публикация
отклоняется, потому что артефакт без задачи нельзя ни показать в панели, ни прибрать
вместе с ней. И защита от чтения чужих файлов: `path` приходит от модели, поэтому файл
вне PROJECT_ROOT — отказ, а не «прочитаем и выложим».

Контекст MCP подделывается: инструменты читают из него ровно одно поле —
`request_context.meta`, и именно его заполняет оркестратор.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from database import Base
import models
from mcp_servers import artifacts as artifacts_mcp
from services import artifacts as artifacts_service
from services.mcp_meta import CALLER_TASK_META_KEY
from services.role_permissions import CALLER_ROLE_META_KEY

engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


class _Ctx:
    """Минимальный MCP-контекст: инструментам нужен только `request_context.meta`."""

    def __init__(self, meta: dict | None = None):
        self.request_context = SimpleNamespace(meta=meta or {})


@pytest.fixture
def env(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=engine)
    # MCP-сервер держит ссылку на SessionLocal с момента импорта — подменяем её саму
    monkeypatch.setattr(artifacts_mcp, "SessionLocal", TestingSessionLocal)
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("PROJECT_ROOT", str(project))

    db = TestingSessionLocal()
    task = models.AgentTask(owner_id="1", status="running", query="публикация результата")
    db.add(task)
    db.commit()
    db.refresh(task)
    task_id = task.id
    db.close()

    yield SimpleNamespace(task_id=task_id, project=project, tmp=tmp_path)

    Base.metadata.drop_all(bind=engine)


def _ctx(task_id: str | None = None, role: str = "web_researcher") -> _Ctx:
    meta = {CALLER_ROLE_META_KEY: role}
    if task_id:
        meta[CALLER_TASK_META_KEY] = task_id
    return _Ctx(meta)


def _rows(task_id: str):
    db = TestingSessionLocal()
    try:
        return db.query(models.Artifact).filter(models.Artifact.task_id == task_id).all()
    finally:
        db.close()


def test_without_task_meta_refuses(env):
    """Прогона нет (Telegram-путь) — публиковать некуда, и «как система» не разрешаем."""
    out = json.loads(artifacts_mcp.publish_artifact(_ctx(), "report", "Отчёт", content="текст"))
    assert "error" in out
    assert _rows(env.task_id) == []


def test_publishes_text_owned_by_run(env):
    out = json.loads(
        artifacts_mcp.publish_artifact(_ctx(env.task_id), "report", "Отчёт", content="# Итог\nвсё ок")
    )
    assert out.get("published") is True, out
    rows = _rows(env.task_id)
    assert len(rows) == 1
    assert rows[0].kind == "report" and rows[0].storage == "db"
    assert rows[0].origin == "agent:web_researcher"
    assert rows[0].sha256 and rows[0].size_bytes == len("# Итог\nвсё ок".encode())


def test_rejects_unknown_kind(env):
    out = json.loads(artifacts_mcp.publish_artifact(_ctx(env.task_id), "видео", "Клип", content="x"))
    assert "error" in out and "kind" in out["error"]
    assert _rows(env.task_id) == []


def test_rejects_file_outside_project(env):
    """Файл рядом с проектом (как /etc/passwd) публиковать нельзя: path приходит от модели."""
    secret = env.tmp / "secret.txt"
    secret.write_text("пароль", encoding="utf-8")

    out = json.loads(
        artifacts_mcp.publish_artifact(_ctx(env.task_id), "file", "Секрет", path=str(secret))
    )
    assert "error" in out and "PROJECT_ROOT" in out["error"]
    assert _rows(env.task_id) == []


def test_publishes_project_file(env):
    report = env.project / "данные.csv"
    report.write_text("a,b\n1,2\n", encoding="utf-8")

    out = json.loads(
        artifacts_mcp.publish_artifact(_ctx(env.task_id), "file", "Данные", path=str(report))
    )
    assert out.get("published") is True, out
    row = _rows(env.task_id)[0]
    assert row.storage == "disk"
    # Файл реально читается той же проверкой, что стоит на отдаче содержимого
    assert artifacts_service.safe_disk_path(row).read_text(encoding="utf-8") == "a,b\n1,2\n"


def test_same_content_does_not_duplicate(env):
    """Цикл браузера не должен набивать панель одинаковыми карточками."""
    first = json.loads(
        artifacts_mcp.publish_artifact(_ctx(env.task_id), "report", "Отчёт", content="один и тот же текст")
    )
    second = json.loads(
        artifacts_mcp.publish_artifact(_ctx(env.task_id), "report", "Отчёт", content="один и тот же текст")
    )
    assert first["artifact_id"] == second["artifact_id"]
    assert len(_rows(env.task_id)) == 1


def test_list_run_artifacts(env):
    artifacts_mcp.publish_artifact(_ctx(env.task_id), "report", "Отчёт", content="текст")

    out = json.loads(artifacts_mcp.list_run_artifacts(_ctx(env.task_id)))
    assert out["task_id"] == env.task_id
    assert [a["title"] for a in out["artifacts"]] == ["Отчёт"]
    assert out["journal_status"] is None

    # Без прогона — тоже отказ, а не «показать всё подряд»
    assert "error" in json.loads(artifacts_mcp.list_run_artifacts(_ctx()))
