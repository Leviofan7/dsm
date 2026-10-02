"""
Песочница на git-worktree (ТЗ 4.5) и запрет записи вне неё (workspace.write_file).

Проверяем полный цикл без Docker: init репозитория в tmp, worktree, перенос файлов,
diff, применение патча к проекту, удаление worktree.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from services import sandbox


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Временный git-репозиторий как PROJECT_ROOT + отдельный SANDBOX_ROOT."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    _git(["init", "-q"], project_root)
    _git(["config", "user.email", "test@local"], project_root)
    _git(["config", "user.name", "test"], project_root)
    (project_root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], project_root)
    _git(["commit", "-qm", "init"], project_root)

    sandbox_root = tmp_path / "sandboxes"
    monkeypatch.setattr(sandbox, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(sandbox, "SANDBOX_ROOT", sandbox_root)
    return project_root


def test_create_materialize_diff_apply_remove(project):
    path, err = sandbox.create_sandbox("task-1")
    assert path and not err, err
    assert sandbox.is_registered_sandbox(path)

    written, err = sandbox.materialize_files("task-1", json.dumps({"backend/app.py": "x = 1\n"}))
    assert written == ["backend/app.py"], err
    assert (path / "backend/app.py").read_text(encoding="utf-8") == "x = 1\n"

    # работаем в песочнице — проект не меняется
    assert not (project / "backend/app.py").exists()

    diff, err = sandbox.sandbox_diff("task-1")
    assert "backend/app.py" in diff and "+++ b/backend/app.py" in diff, err

    ok, err = sandbox.apply_patch_to_project(diff)
    assert ok, err
    assert (project / "backend/app.py").read_text(encoding="utf-8") == "x = 1\n"

    sandbox.remove_sandbox("task-1")
    assert not path.exists()


def test_materialize_accepts_unified_diff(project):
    patch = (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-hello\n"
        "+hello sandbox\n"
    )
    files, err = sandbox.materialize_files("task-2", patch)
    assert err == "", err
    assert "README.md" in files

    diff, err = sandbox.sandbox_diff("task-2")
    assert "hello sandbox" in diff
    sandbox.remove_sandbox("task-2")


def test_materialize_rejects_path_traversal(project):
    path, err = sandbox.create_sandbox("task-3")
    assert path and not err, err

    written, err = sandbox.materialize_files("task-3", json.dumps({"../escaped.py": "boom"}))
    assert "вне песочницы" in err
    assert not (project.parent / "escaped.py").exists()

    sandbox.remove_sandbox("task-3")


def test_auto_checkpoint_is_off_by_default(monkeypatch):
    """4.6: по умолчанию checkpoint не делается — обычный flow коммитит человек при Apply."""
    monkeypatch.setattr(sandbox, "ORCHESTRATION_CONFIG_PATH", Path("/nonexistent/orchestration.json"))
    assert sandbox.auto_checkpoint_enabled() is False
    assert sandbox.sandbox_required_for_writes() is True


def test_baseline_warning_reports_dirty_tree(project):
    """WIP-warning вместо авто-stash: baseline песочницы — HEAD."""
    assert sandbox.baseline_warning() is None

    (project / "wip.txt").write_text("незакоммиченное", encoding="utf-8")
    warning = sandbox.baseline_warning()
    assert warning is not None
    assert "1 незакоммиченных" in warning
    assert "wip.txt" in warning
    assert sandbox.project_wip_status()[0] == 1


def test_marker_records_baseline_and_list_sandboxes(project):
    path, err = sandbox.create_sandbox("t-marker")
    assert path and not err, err

    marker_path = sandbox.sandbox_marker_path(path)
    assert marker_path.is_file()
    # маркер должен лежать ВНЕ worktree, иначе он уедет в патч
    assert marker_path.parent != path
    assert not (path / sandbox.MARKER_SUFFIX).exists()

    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["coder_task_id"] == "t-marker"
    assert marker["project_root"] == str(project)
    assert "created_at" in marker and "wip_files_at_creation" in marker

    items = sandbox.list_sandboxes()
    assert [i["task_id"] for i in items] == ["t-marker"]
    assert items[0]["age_hours"] < 1

    sandbox.remove_sandbox("t-marker")
    assert sandbox.list_sandboxes() == []
    assert not marker_path.exists()


def test_service_files_do_not_leak_into_patch(project):
    """Регресс: маркер песочницы и __pycache__ не должны попадать в diff и в проект."""
    path, err = sandbox.create_sandbox("t-clean")
    assert path and not err, err

    (path / "__pycache__").mkdir(parents=True, exist_ok=True)
    (path / "__pycache__" / "mod.cpython-312.pyc").write_bytes(b"junk")

    written, err = sandbox.materialize_files("t-clean", json.dumps({"pkg/mod.py": "x = 1\n"}))
    assert written == ["pkg/mod.py"], err

    diff, err = sandbox.sandbox_diff("t-clean")
    assert err == "", err
    assert "pkg/mod.py" in diff
    assert sandbox.MARKER_SUFFIX not in diff, "служебный маркер утёк в патч"
    assert "__pycache__" not in diff, "кеш Python утёк в патч"

    ok, err = sandbox.apply_patch_to_project(diff)
    assert ok, err
    assert (project / "pkg/mod.py").exists()
    assert not (project / sandbox.MARKER_SUFFIX).exists(), "маркер оказался в рабочем проекте"
    assert not (project / "__pycache__").exists()

    sandbox.remove_sandbox("t-clean")


@pytest.mark.asyncio
async def test_workspace_write_requires_sandbox(project, tmp_path, monkeypatch):
    """workspace.write_file без sandbox_id отклоняется, с sandbox_id — пишет в песочницу."""
    from mcp_servers import workspace as ws

    refused = json.loads(await ws.write_file("backend/new.py", "print(1)"))
    assert "error" in refused and "Песочница не указана" in refused["error"]
    assert not (project / "backend/new.py").exists()

    sandbox_dir = tmp_path / "sandboxes" / "task-9"
    sandbox_dir.mkdir(parents=True)
    sandbox.sandbox_marker_path(sandbox_dir).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ws, "registered_sandbox_dir", lambda task_id: sandbox_dir)

    ok = json.loads(await ws.write_file("backend/new.py", "print(1)", sandbox_id="task-9"))
    assert ok.get("success") is True, ok
    assert (sandbox_dir / "backend/new.py").read_text(encoding="utf-8") == "print(1)"
    assert not (project / "backend/new.py").exists()

    denied = json.loads(await ws.run_terminal_command("echo hi"))
    assert "Песочница не указана" in denied["error"]
