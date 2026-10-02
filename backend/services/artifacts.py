"""
Артефакты прогонов: публикация, чтение, план, журнал.

Владелец артефакта — ЗАДАЧА (`agent_tasks`), а не беседа (решение 02.10.2026): история работы
должна переживать удаление беседы, а результат — быть привязан к прогону, который его произвёл.

Здесь одна точка правды для API и будущего MCP-инструмента:
  * publish / save_file — публикация артефакта (текст, файл, ссылка);
  * list_for_task — карточка прогона: план из agent_subtasks + артефакты + журнал;
  * safe_disk_path — единственный разрешённый способ достать файл с диска (защита от traversal);
  * upsert_journal — исход прогона для самоанализа.

План НЕ дублируется в артефактах: он живёт в `agent_subtasks` и отдаётся как есть, поэтому
в панели его шаги обновляются сами по мере выполнения.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import AgentSubtask, AgentTask, Artifact, ExecutionTrace, RunJournal

#: Закрытый список типов: UI рассчитывает на превью для каждого из них.
KINDS = ("plan", "file", "image", "text", "report", "link", "error_dump")

#: Имена MCP-инструментов этого сервиса — единый источник для оркестратора и сервера.
ARTIFACT_TOOLS = ("publish_artifact", "list_run_artifacts")

#: Потолок размера файла, который агент может опубликовать (защита от «приложил образ диска»).
MAX_AGENT_FILE_BYTES = 20 * 1024 * 1024


def artifacts_root() -> Path:
    """
    Корень файловых артефактов: по умолчанию `<PROJECT_ROOT>/artifacts`.

    PROJECT_ROOT — тот же bind-mount, что и репозиторий, значит файлы переживают пересоздание
    контейнера; каталог закрыт в .gitignore.
    """
    root = os.getenv("ARTIFACTS_DIR") or os.path.join(os.getenv("PROJECT_ROOT", "."), "artifacts")
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_name(name: str) -> str:
    """Имя файла без путей и опасных символов: артефакт приходит из внешнего мира."""
    base = Path(str(name or "artifact")).name
    cleaned = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE).strip("._")
    return (cleaned or "artifact")[:120]


def publish(
    db: Session,
    *,
    task_id: str,
    kind: str,
    title: str,
    content: str | None = None,
    path: str | None = None,
    url: str | None = None,
    mime: str | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    origin: str = "agent",
    conversation_id: str | None = None,
    meta: dict[str, Any] | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
    dedup: bool = True,
) -> Artifact:
    """Единая точка публикации: сама понимает, где лежит содержимое (db/disk/url)."""
    if kind not in KINDS:
        raise ValueError(f"неизвестный kind={kind!r}, допустимые: {KINDS}")

    if url:
        storage = "url"
    elif path:
        storage = "disk"
    else:
        storage = "db"

    task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
    if task is None:
        # Артефакт без владельца — мусор: его нельзя будет ни показать, ни удалить вместе с задачей.
        raise ValueError(f"задача {task_id} не найдена — артефакт без владельца не создаём")
    if conversation_id is None:
        conversation_id = task.conversation_id

    if content is not None:
        encoded = content.encode("utf-8")
        size_bytes = size_bytes if size_bytes is not None else len(encoded)
        sha256 = sha256 or _sha256_bytes(encoded)

    if dedup and sha256:
        # Тот же результат в той же задаче — та же карточка. Без этого цикл браузера
        # набивал панель десятками одинаковых скриншотов/отчётов.
        existing = (
            db.query(Artifact)
            .filter(
                Artifact.task_id == task_id,
                Artifact.sha256 == sha256,
                Artifact.kind == kind,
                Artifact.deleted_at.is_(None),
            )
            .first()
        )
        if existing is not None:
            return existing

    row = Artifact(
        id=str(uuid.uuid4()),
        task_id=task_id,
        conversation_id=conversation_id,
        kind=kind,
        title=(title or "Артефакт")[:255],
        mime=mime,
        size_bytes=size_bytes,
        storage=storage,
        content=content,
        path=path,
        url=url,
        sha256=sha256,
        origin=origin,
        meta=json.dumps(meta, ensure_ascii=False) if meta else None,
        ref_type=ref_type,
        ref_id=ref_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def save_file(
    db: Session,
    *,
    task_id: str,
    filename: str,
    data: bytes,
    kind: str = "file",
    title: str | None = None,
    mime: str | None = None,
    origin: str = "agent",
    conversation_id: str | None = None,
    meta: dict[str, Any] | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
) -> Artifact:
    """Кладёт файл на диск (sha-адресация = дедуп внутри задачи) и создаёт карточку."""
    digest = _sha256_bytes(data)
    task_dir = artifacts_root() / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    target = task_dir / f"{digest[:8]}_{_safe_name(filename)}"
    if not target.exists():
        target.write_bytes(data)

    return publish(
        db,
        task_id=task_id,
        kind=kind,
        title=title or _safe_name(filename),
        path=str(target.relative_to(artifacts_root())),
        mime=mime,
        size_bytes=len(data),
        sha256=digest,
        origin=origin,
        conversation_id=conversation_id,
        meta=meta,
        ref_type=ref_type,
        ref_id=ref_id,
    )


def safe_disk_path(artifact: Artifact) -> Path:
    """
    Путь к файлу артефакта, ПРОВЕРЕННЫЙ на выход за корень.

    `path` в БД — недоверенный ввод (артефакт мог прийти извне: скачивание, агент, MCP).
    Поэтому резолвим и требуем, чтобы результат остался внутри корня артефактов, был обычным
    файлом и не симлинком.
    """
    root = artifacts_root().resolve()
    candidate = (root / str(artifact.path or "")).resolve()
    if not str(candidate).startswith(str(root) + os.sep):
        raise ValueError("path вне корня артефактов")
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("path не является обычным файлом")
    return candidate


def plan_steps(db: Session, task_id: str) -> list[dict[str, Any]]:
    """План прогона = шаги планировщика (живые, из agent_subtasks; в артефактах не дублируем)."""
    steps = (
        db.query(AgentSubtask)
        .filter(AgentSubtask.task_id == task_id)
        .order_by(AgentSubtask.execution_order)
        .all()
    )
    return [
        {
            "order": s.execution_order,
            "topic": s.topic,
            "role": s.target_role,
            "status": s.status,
            "result_preview": (s.result_output or "")[:400],
        }
        for s in steps
    ]


def render_plan(steps: list[dict[str, Any]]) -> str:
    """Тот же план markdown-чеклистом — для «Скачать» и копирования в отчёт."""
    marks = {"completed": "x", "failed": "!", "running": "~", "pending": " "}
    lines = ["# План выполнения", ""]
    for step in steps:
        role = f" ({step['role']})" if step.get("role") else ""
        mark = marks.get(step["status"], " ")
        lines.append(f"- [{mark}] {step['order']}. {step['topic']}{role} — {step['status']}")
        if step.get("result_preview"):
            lines.append(f"      {step['result_preview'][:200]}")
    return "\n".join(lines)


def artifact_card(a: Artifact, *, inline_limit: int = 20000) -> dict[str, Any]:
    """Карточка для UI: инлайн-текст отдаём сразу (это и есть превью), файл — по ссылке."""
    card = {
        "id": a.id,
        "task_id": a.task_id,
        "kind": a.kind,
        "title": a.title,
        "mime": a.mime,
        "size_bytes": a.size_bytes,
        "storage": a.storage,
        "origin": a.origin,
        "url": a.url,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "content_url": f"/artifacts/{a.id}/content",
        "meta": json.loads(a.meta) if a.meta else None,
    }
    if a.storage == "db" and a.content:
        card["content"] = a.content[:inline_limit]
        card["truncated"] = len(a.content) > inline_limit
    return card


def journal_card(j: RunJournal) -> dict[str, Any]:
    """Карточка журнала для API/панели (публичная: нужна и эндпоинту /tasks/{id}/journal)."""
    def load(raw: str | None):
        return json.loads(raw) if raw else None

    return {
        "status": j.status,
        "summary": j.summary,
        "errors": load(j.errors),
        "achievements": load(j.achievements),
        "metrics": load(j.metrics),
        "self_review": j.self_review,
        "scenario_proposed_id": j.scenario_proposed_id,
        "tags": j.tags,
        "updated_at": j.updated_at.isoformat() if j.updated_at else None,
    }


def list_for_task(db: Session, task_id: str) -> dict[str, Any] | None:
    """Карточка прогона для правой панели: задача, план (живой), артефакты, журнал."""
    task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
    if task is None:
        return None

    steps = plan_steps(db, task_id)
    artifacts = (
        db.query(Artifact)
        .filter(Artifact.task_id == task_id, Artifact.deleted_at.is_(None))
        .order_by(Artifact.created_at)
        .all()
    )
    journal = db.query(RunJournal).filter(RunJournal.task_id == task_id).first()

    return {
        "task": {
            "id": task.id,
            "query": task.query,
            "status": task.status,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "conversation_id": task.conversation_id,
        },
        "plan": {"steps": steps, "markdown": render_plan(steps)} if steps else None,
        "artifacts": [artifact_card(a) for a in artifacts],
        "journal": journal_card(journal) if journal else None,
    }


def upsert_journal(
    db: Session,
    task_id: str,
    *,
    status: str,
    summary: str | None = None,
    errors: list | None = None,
    achievements: list | None = None,
    metrics: dict | None = None,
    self_review: str | None = None,
    tags: str | None = None,
) -> RunJournal:
    """Исход прогона: одна строка на задачу; повторные вызовы обновляют её."""
    row = db.query(RunJournal).filter(RunJournal.task_id == task_id).first()
    if row is None:
        row = RunJournal(task_id=task_id)
        db.add(row)

    row.status = status
    if summary is not None:
        row.summary = summary
    if errors is not None:
        row.errors = json.dumps(errors, ensure_ascii=False)
    if achievements is not None:
        row.achievements = json.dumps(achievements, ensure_ascii=False)
    if metrics is not None:
        row.metrics = json.dumps(metrics, ensure_ascii=False)
    if self_review is not None:
        row.self_review = self_review
    if tags is not None:
        row.tags = tags

    db.commit()
    db.refresh(row)
    return row


def runs_for_conversation(db: Session, conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """История прогонов беседы (свежие сверху) — то, что панель показывает списком."""
    tasks = (
        db.query(AgentTask)
        .filter(AgentTask.conversation_id == conversation_id)
        .order_by(AgentTask.created_at.desc())
        .limit(limit)
        .all()
    )
    if not tasks:
        return []

    ids = [t.id for t in tasks]
    artifact_counts = dict(
        db.query(Artifact.task_id, func.count(Artifact.id))
        .filter(Artifact.task_id.in_(ids), Artifact.deleted_at.is_(None))
        .group_by(Artifact.task_id)
        .all()
    )
    plan_counts = dict(
        db.query(AgentSubtask.task_id, func.count(AgentSubtask.id))
        .filter(AgentSubtask.task_id.in_(ids))
        .group_by(AgentSubtask.task_id)
        .all()
    )
    journals = {
        j.task_id: j for j in db.query(RunJournal).filter(RunJournal.task_id.in_(ids)).all()
    }

    runs = []
    for t in tasks:
        journal = journals.get(t.id)
        runs.append(
            {
                "task_id": t.id,
                "query": (t.query or "")[:200],
                "status": t.status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "artifacts_count": artifact_counts.get(t.id, 0),
                "plan_steps": plan_counts.get(t.id, 0),
                "journal": {"status": journal.status, "summary": journal.summary} if journal else None,
            }
        )
    return runs


# ── Фаза 3: что агент может опубликовать сам ───────────────────────

def project_root() -> Path:
    """Корень проекта — тот же PROJECT_ROOT, что у файловых MCP-инструментов."""
    return Path(os.getenv("PROJECT_ROOT") or ".").resolve()


def read_agent_file(raw_path: str) -> bytes:
    """
    Читает файл, который агент решил опубликовать, — с проверками.

    Почему не просто open(): путь приходит аргументом MCP-инструмента, то есть от модели.
    Без проверок модель могла бы выложить в панель содержимое `/etc/passwd`, ключи из
    окружения или симлинк куда угодно — а артефакт скачивается руками администратора.
    Поэтому разрешены только обычные файлы внутри PROJECT_ROOT (или внутри корня артефактов),
    не симлинки, с потолком размера.
    """
    if not raw_path or not str(raw_path).strip():
        raise ValueError("path не задан")

    candidate = Path(str(raw_path).strip())
    if not candidate.is_absolute():
        candidate = project_root() / candidate
    candidate = candidate.resolve()

    allowed_roots = [project_root(), artifacts_root().resolve()]
    if not any(str(candidate).startswith(str(root) + os.sep) for root in allowed_roots):
        raise ValueError("path вне PROJECT_ROOT — публиковать можно только файлы проекта")
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("path не является обычным файлом")

    size = candidate.stat().st_size
    if size > MAX_AGENT_FILE_BYTES:
        raise ValueError(f"файл слишком большой: {size} байт (потолок {MAX_AGENT_FILE_BYTES})")
    return candidate.read_bytes()


# ── Журнал: метрики из трейсов и паттерны ────────────────────────

def collect_metrics(db: Session, task_id: str) -> dict[str, Any]:
    """
    Метрики прогона из `execution_traces` — данные уже собраны, дублировать их не нужно.

    До этого журнал знал только про «события»: модели, длительность, тул-коллы и ошибки
    лежали в таблице, которую никто не читает глазами («система знает, но не помнит»).
    """
    traces = db.query(ExecutionTrace).filter(ExecutionTrace.task_id == task_id).all()
    if not traces:
        return {}

    models = sorted(
        {(t.model_selected or t.model_used) for t in traces if (t.model_selected or t.model_used)}
    )
    tool_names: list[str] = []
    for t in traces:
        try:
            names = json.loads(t.tools_called_names) if t.tools_called_names else []
        except Exception:
            names = []
        if isinstance(names, list):
            tool_names.extend(str(n) for n in names)

    return {
        "traces": len(traces),
        "duration_ms": sum(int(t.duration_ms or 0) for t in traces),
        "tokens_in": sum(int(t.tokens_in or 0) for t in traces),
        "tokens_out": sum(int(t.tokens_out or 0) for t in traces),
        "models": models,
        "tool_calls": sum(int(t.tools_called or 0) for t in traces),
        "tools": sorted(set(tool_names)),
        "statuses": sorted({t.final_status for t in traces if t.final_status}),
    }


def trace_errors(db: Session, task_id: str) -> list[str]:
    """Ошибки из трейсов (обрезанные): журнал — сводка, а не свалка логов."""
    out: list[str] = []
    for t in db.query(ExecutionTrace).filter(ExecutionTrace.task_id == task_id).all():
        text = str(t.errors or "").strip()
        if text and text not in out:
            out.append(text[:400])
    return out


def _achievements_from_run(db: Session, task_id: str) -> list[str]:
    """Достижения по факту: сколько шагов плана закрыто и что именно осталось в результате."""
    steps = plan_steps(db, task_id)
    done = [s for s in steps if s["status"] == "completed"]
    kinds = dict(
        db.query(Artifact.kind, func.count(Artifact.id))
        .filter(Artifact.task_id == task_id, Artifact.deleted_at.is_(None))
        .group_by(Artifact.kind)
        .all()
    )
    out: list[str] = []
    if steps:
        out.append(f"план: завершено {len(done)}/{len(steps)} шагов")
    if kinds:
        out.append("результаты: " + ", ".join(f"{k}×{v}" for k, v in sorted(kinds.items())))
    return out


def finalize_journal(
    db: Session,
    task_id: str,
    *,
    status: str,
    summary: str | None = None,
    errors: list | None = None,
    achievements: list | None = None,
    self_review: str | None = None,
    tags: str | None = None,
) -> RunJournal:
    """
    Итог прогона одной записью: то, что знает воркер + метрики и ошибки из трейсов.

    Вызывается из всех трёх исходов (успех/отмена/падение): раньше журнал писался
    «на глазок», и ошибка прогона оставалась только в логе контейнера.
    """
    metrics = collect_metrics(db, task_id)
    merged = list(errors or [])
    for err in trace_errors(db, task_id):
        if err not in merged:
            merged.append(err)
    if achievements is None:
        achievements = _achievements_from_run(db, task_id)
    if summary is None and achievements:
        # Сводка по фактам, а не «всё хорошо»: план и то, что осталось в результате.
        summary = "; ".join(achievements)

    return upsert_journal(
        db,
        task_id,
        status=status,
        summary=summary,
        errors=merged or None,
        achievements=achievements or None,
        metrics=metrics or None,
        self_review=self_review,
        tags=tags,
    )


_NORM_DIGITS = re.compile(r"\d+")
_NORM_PATHS = re.compile(r"[^\s\"']*[/\\][^\s\"']*")


def _error_signature(text: str) -> str:
    """Грубая нормализация ошибки для группировки: числа и пути → плейсхолдеры."""
    return _NORM_PATHS.sub("<path>", _NORM_DIGITS.sub("<n>", text.strip().lower()))[:160]


def journal_patterns(db: Session, limit: int = 100) -> dict[str, Any]:
    """
    Сводка по последним прогонам — самоанализ минимальными средствами.

    Журнал ценен не поодиночке, а паттернами («падает на капче», «модель X не тянет»).
    Здесь только агрегаты по уже записанным журналам: без LLM и без новых таблиц —
    ровно то, что можно утверждать по имеющимся данным.
    """
    rows = db.query(RunJournal).order_by(RunJournal.created_at.desc()).limit(limit).all()
    by_status: dict[str, int] = {}
    durations: list[int] = []
    signatures: dict[str, dict[str, Any]] = {}

    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        try:
            metrics = json.loads(row.metrics) if row.metrics else {}
        except Exception:
            metrics = {}
        if isinstance(metrics, dict):
            try:
                durations.append(int(metrics.get("duration_ms") or 0))
            except Exception:
                pass
        try:
            errs = json.loads(row.errors) if row.errors else []
        except Exception:
            errs = []
        for err in errs if isinstance(errs, list) else []:
            key = _error_signature(str(err))
            entry = signatures.setdefault(key, {"count": 0, "sample": str(err)[:200]})
            entry["count"] += 1

    total = len(rows)
    success = by_status.get("success", 0)
    return {
        "window": total,
        "by_status": by_status,
        "success_rate": round(success / total, 3) if total else None,
        "avg_duration_ms": int(sum(durations) / len(durations)) if durations else None,
        "top_errors": sorted(signatures.values(), key=lambda e: e["count"], reverse=True)[:5],
    }
