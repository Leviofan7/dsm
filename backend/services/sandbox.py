"""
sandbox.py — git-worktree песочница для привилегированных действий агентов.

Модель работы (ТЗ 4.5/4.6):
  * версионируется рабочий проект — `PROJECT_ROOT` (в docker-compose: /app/project_workspace);
  * под задачу кодера создаётся отдельный git-worktree в `CODER_SANDBOX_DIR`;
  * кодер пишет ТОЛЬКО внутрь песочницы (workspace.write_file(sandbox_id=...)),
    поэтому рабочий проект не меняется до одобрения человеком;
  * изменения собираются в unified diff и применяются к рабочему проекту
    отдельным шагом «Apply» (`git apply`), коммит делает человек;
  * опционально (auto_checkpoint=true в config/orchestration.json) перед созданием
    песочницы делается `git stash` — точка отката. По умолчанию выключено.

Прямые вызовы git идут через `git -C`, никаких копирований дерева.
"""

import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("contextus.sandbox")

_BACKEND_DIR = Path(__file__).parent.parent.resolve()
ORCHESTRATION_CONFIG_PATH = _BACKEND_DIR / "config" / "orchestration.json"

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", "/app/project_workspace")).resolve()
SANDBOX_ROOT = Path(os.getenv("CODER_SANDBOX_DIR", "/tmp/coder_sandbox")).resolve()

#: суффикс sidecar-маркера (лежит РЯДОМ с worktree, а не внутри него:
#: иначе маркер попадал в git diff песочницы и уезжал в патч проекта)
MARKER_SUFFIX = ".sandbox.json"

#: служебные пути, которые не должны попадать в патч песочницы
_SANDBOX_EXCLUDES = (
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    ".pytest_cache/**",
    "**/.pytest_cache/**",
)

_GIT_TIMEOUT = 120


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Конфиг оркестрации ────────────────────────────────────────────

_DEFAULT_ORCHESTRATION = {
    "auto_checkpoint": False,
    "require_sandbox_for_writes": True,
}


def load_orchestration_config() -> dict:
    """Читает config/orchestration.json (перечитывается на каждый вызов)."""
    data = dict(_DEFAULT_ORCHESTRATION)
    if ORCHESTRATION_CONFIG_PATH.exists():
        try:
            with open(ORCHESTRATION_CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception as e:
            logger.error(f"Ошибка чтения orchestration.json: {e}")
    return data


def auto_checkpoint_enabled() -> bool:
    return bool(load_orchestration_config().get("auto_checkpoint", False))


def sandbox_required_for_writes() -> bool:
    return bool(load_orchestration_config().get("require_sandbox_for_writes", True))


# ── git ───────────────────────────────────────────────────────────

def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    ensure_safe_directory(cwd)
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )


def ensure_safe_directory(path: Path | str) -> None:
    """
    git в контейнере работает от root, а репозиторий принадлежит uid хоста →
    «detected dubious ownership». Прописываем исключение один раз (идемпотентно).
    """
    try:
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", str(path)],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:  # git может отсутствовать — это не повод падать
        logger.warning(f"Не удалось добавить safe.directory {path}: {e}")


def is_git_repo(path: Path | str) -> bool:
    if not Path(path).is_dir():
        return False
    try:
        res = _git(["rev-parse", "--is-inside-work-tree"], Path(path))
        return res.returncode == 0 and res.stdout.strip() == "true"
    except Exception:
        return False


def project_repo_ready() -> tuple[bool, str]:
    """Готов ли рабочий проект к worktree-операциям."""
    if not PROJECT_ROOT.is_dir():
        return False, f"PROJECT_ROOT не найден: {PROJECT_ROOT}"
    if not is_git_repo(PROJECT_ROOT):
        return False, f"PROJECT_ROOT не является git-репозиторием: {PROJECT_ROOT}"
    res = _git(["rev-parse", "HEAD"], PROJECT_ROOT)
    if res.returncode != 0:
        return False, f"В репозитории {PROJECT_ROOT} нет коммитов: {res.stderr.strip()}"
    return True, ""


def project_wip_status(limit: int = 10) -> tuple[int, list[str]]:
    """
    Незакоммиченные изменения рабочего проекта (ТЗ: WIP-warning в baseline).
    Baseline песочницы — HEAD, поэтому эти правки в worktree не попадут.
    Автоматический stash НЕ делается (см. auto_checkpoint).
    """
    ready, _ = project_repo_ready()
    if not ready:
        return 0, []
    res = _git(["status", "--porcelain"], PROJECT_ROOT)
    if res.returncode != 0:
        return 0, []
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    return len(lines), lines[:limit]


def baseline_warning() -> str | None:
    """
    Предупреждение о «грязном» рабочем дереве на момент создания песочницы.
    Возвращается как есть — в поле `warning` ответа request_diff_apply.
    """
    count, files = project_wip_status(limit=5)
    if not count:
        return None
    preview = ", ".join(f.split(maxsplit=1)[-1] for f in files)
    more = "" if count <= len(files) else f" и ещё {count - len(files)}"
    return (
        f"В рабочем проекте {count} незакоммиченных изменений ({preview}{more}). "
        "Песочница создаётся от HEAD, поэтому эти правки в неё не попадут — "
        "учитывай это при Apply (патч может конфликтовать). Авто-stash не делается."
    )


def list_sandboxes() -> list[dict]:
    """
    Зарегистрированные песочницы на диске (для cleanup-воркера, ТЗ 4.7).
    Возвращает список {task_id, path, age_hours}.
    """
    result: list[dict] = []
    if not SANDBOX_ROOT.is_dir():
        return result
    now = time.time()
    for entry in sorted(SANDBOX_ROOT.iterdir()):
        if not is_registered_sandbox(entry):
            continue
        try:
            task_id = entry.name
            try:
                marker = json.loads(sandbox_marker_path(entry).read_text(encoding="utf-8"))
                task_id = marker.get("coder_task_id") or task_id
            except Exception:
                pass
            result.append({
                "task_id": task_id,
                "path": entry,
                "age_hours": (now - entry.stat().st_mtime) / 3600.0,
            })
        except Exception:
            continue
    return result


# ── Песочница ─────────────────────────────────────────────────────

def sandbox_key(coder_task_id: str) -> str:
    """Безопасный ключ песочницы из id задачи (никаких слэшей в пути)."""
    raw = str(coder_task_id or "").strip()
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in raw)
    return safe or "default"


def sandbox_dir(coder_task_id: str) -> Path:
    return SANDBOX_ROOT / sandbox_key(coder_task_id)


def sandbox_marker_path(path: Path) -> Path:
    """Sidecar-маркер рядом с worktree. Вне его — значит не попадает в diff песочницы."""
    return path.parent / f"{path.name}{MARKER_SUFFIX}"


def is_registered_sandbox(path: Path) -> bool:
    """Доверенная песочница = существует и содержит sidecar-маркер, поставленный backend'ом."""
    try:
        if not path.is_dir() or not sandbox_marker_path(path).is_file():
            return False
        path.resolve().relative_to(SANDBOX_ROOT)
        return True
    except Exception:
        return False


def registered_sandbox_dir(coder_task_id: str) -> Path | None:
    path = sandbox_dir(coder_task_id)
    return path if is_registered_sandbox(path) else None


def create_sandbox(coder_task_id: str, base_ref: str = "HEAD") -> tuple[Path | None, str]:
    """
    Создаёт git-worktree под задачу. Возвращает (путь, ошибка).
    Повторный вызов для той же задачи переиспользует существующую песочницу.
    """
    ready, err = project_repo_ready()
    if not ready:
        return None, err

    path = sandbox_dir(coder_task_id)
    if is_registered_sandbox(path):
        return path, ""

    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)

    res = _git(["worktree", "add", "--detach", str(path), base_ref], PROJECT_ROOT)
    if res.returncode != 0:
        return None, f"git worktree add failed: {res.stderr.strip() or res.stdout.strip()}"

    marker = {
        "coder_task_id": str(coder_task_id),
        "sandbox_key": sandbox_key(coder_task_id),
        "project_root": str(PROJECT_ROOT),
        "base_ref": base_ref,
        "created_at": _utc_iso(),
        "wip_files_at_creation": project_wip_status(limit=100)[0],
    }
    try:
        sandbox_marker_path(path).write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Не удалось записать маркер песочницы: {e}")
    logger.info(f"🧪 Sandbox создана: {path} (base={base_ref}, task={coder_task_id})")
    return path, ""


def remove_sandbox(coder_task_id: str) -> None:
    """Удаляет worktree, sidecar-маркер и чистит служебные записи git."""
    path = sandbox_dir(coder_task_id)
    try:
        if path.exists():
            _git(["worktree", "remove", "--force", str(path)], PROJECT_ROOT)
            if path.exists():  # git мог не справиться — убираем руками
                shutil.rmtree(path, ignore_errors=True)
        sandbox_marker_path(path).unlink(missing_ok=True)
        _git(["worktree", "prune"], PROJECT_ROOT)
        logger.info(f"🧹 Sandbox удалена: {path}")
    except Exception as e:
        logger.error(f"Ошибка удаления песочницы {path}: {e}")


# ── Наполнение и diff ─────────────────────────────────────────────

def _write_files_into(sandbox: Path, files: dict) -> tuple[list[str], str]:
    written = []
    for rel_path, content in files.items():
        if not isinstance(rel_path, str) or not rel_path.strip():
            continue
        clean = rel_path.strip().lstrip("/")
        target = (sandbox / clean).resolve()
        try:
            target.relative_to(sandbox.resolve())
        except ValueError:
            return written, f"Путь вне песочницы: {rel_path}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(clean)
    return written, ""


def materialize_files(coder_task_id: str, files_diff: str) -> tuple[list[str], str]:
    """
    Переносит предложенные кодером изменения в песочницу.
    `files_diff` — JSON {"путь": "содержимое"} либо unified diff.
    Возвращает (список файлов, ошибка).
    """
    sandbox, err = create_sandbox(coder_task_id)
    if not sandbox:
        return [], err

    text = (files_diff or "").strip()
    if not text:
        return [], "Пустой files_diff"

    # 1) unified diff
    if text.startswith("diff --git") or text.startswith("--- "):
        # trailing newline обязателен: .strip() выше его срезает, а без него
        # git apply считает последнюю строку патча неполной («corrupt patch»)
        patch_text = text if text.endswith("\n") else text + "\n"
        res = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=str(sandbox), input=patch_text, capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
        if res.returncode != 0:
            return [], f"git apply в песочнице не удался: {res.stderr.strip()}"
        changed = _git(["diff", "--name-only"], sandbox)
        return [p for p in changed.stdout.splitlines() if p.strip()], ""

    # 2) JSON {файл: содержимое}
    try:
        files = json.loads(text)
    except json.JSONDecodeError as e:
        return [], f"files_diff не является ни JSON, ни unified diff: {e}"
    if not isinstance(files, dict) or not files:
        return [], "files_diff должен быть непустым объектом {путь: содержимое}"

    return _write_files_into(sandbox, files)


def sandbox_diff(coder_task_id: str) -> tuple[str, str]:
    """Unified diff изменений песочницы относительно HEAD."""
    sandbox = registered_sandbox_dir(coder_task_id)
    if not sandbox:
        return "", f"Песочница для задачи {coder_task_id} не найдена"

    _git(["add", "-A", "--", ".", *[f":(exclude){pattern}" for pattern in _SANDBOX_EXCLUDES]], sandbox)
    res = _git(["diff", "--cached", "--no-color", "--no-ext-diff"], sandbox)
    if res.returncode != 0:
        return "", f"git diff failed: {res.stderr.strip()}"
    return res.stdout, ""


# ── Checkpoint (git stash) ────────────────────────────────────────

def create_stash_checkpoint(coder_task_id: str) -> tuple[str | None, str]:
    """
    Точка отката в рабочем проекте. Делается только если auto_checkpoint=true.
    Возвращает (метка stash, ошибка).
    """
    if not auto_checkpoint_enabled():
        return None, ""
    ready, err = project_repo_ready()
    if not ready:
        return None, err

    status = _git(["status", "--porcelain"], PROJECT_ROOT)
    if not status.stdout.strip():
        return None, ""  # нечего сохранять — дерево и так чистое

    label = f"contextus: checkpoint before sandbox {coder_task_id}"
    res = _git(["stash", "push", "--include-untracked", "-m", label], PROJECT_ROOT)
    if res.returncode != 0:
        return None, f"git stash failed: {res.stderr.strip()}"
    logger.info(f"💾 Checkpoint: {label}")
    return label, ""


# ── Применение к рабочему проекту ─────────────────────────────────

def apply_patch_to_project(patch: str) -> tuple[bool, str]:
    """
    Применяет unified diff к рабочему проекту (шаг «Apply», без коммита).
    Сначала `--check` (всё или ничего), затем реальное применение.
    """
    ready, err = project_repo_ready()
    if not ready:
        return False, err
    if not (patch or "").strip():
        return False, "Пустой патч"

    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn", "-"],
        cwd=str(PROJECT_ROOT), input=patch, capture_output=True, text=True, timeout=_GIT_TIMEOUT,
    )
    if check.returncode != 0:
        return False, f"Патч не применяется (git apply --check): {check.stderr.strip()}"

    apply_res = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=str(PROJECT_ROOT), input=patch, capture_output=True, text=True, timeout=_GIT_TIMEOUT,
    )
    if apply_res.returncode != 0:
        return False, f"git apply failed: {apply_res.stderr.strip()}"

    logger.info(f"✅ Патч применён к {PROJECT_ROOT} (коммит делает человек)")
    return True, ""
