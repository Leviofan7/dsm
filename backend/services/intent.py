"""
intent.py — intent.md как артефакт задачи (ТЗ 4.1).

Единый источник правды между ролями: frontmatter + секции (markdown и YAML-блоки).
Утилиты — точный инструмент для программных мутаций, а не «текстовый редактор markdown».

Ключевые инварианты:
  * атомарная запись: intent.md.tmp → os.replace() (POSIX-rename атомарен);
  * optimistic concurrency вместо файлового лока: сверка mtime + frontmatter.updated,
    при конфликте — retry с перечитыванием (50ms → 200ms → 1s), затем явная ошибка;
  * append_* идемпотентны по содержимому (повторный retry не создаёт дубль);
  * только ПЕРВЫЙ YAML-блок в секции используется (на остальные — warning);
    невалидный YAML — явная ошибка (fail-loud), без «угадывания»;
  * permissions_overrides и plan присутствуют всегда, даже пустыми;
  * статичные секции (Цель / DoD / Ограничения) меняются ОДНОЙ update_section —
    никаких пер-секционных утилит.
"""

# ВАЖНО: нужен для функции list() из ТЗ (см. конец модуля) — аннотации не исполняются.
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger("contextus.intent")

# ── Расположение ──────────────────────────────────────────────────
# intents/ — в корне workspace (PROJECT_ROOT), БЕЗ точки в начале:
# должна попадать в поиск и быть видимой инструментам.
_ENV_INTENTS_DIR = os.getenv("INTENTS_DIR")
INTENTS_DIR = Path(_ENV_INTENTS_DIR).resolve() if _ENV_INTENTS_DIR else (
    Path(os.getenv("PROJECT_ROOT", "/app/project_workspace")).resolve() / "intents"
)

ACTIVE_DIRNAME = "active"
ARCHIVE_DIRNAME = "archive"
INTENT_FILENAME = "intent.md"
INDEX_FILENAME = "INDEX.md"

VALID_STATUSES = ("active", "blocked", "done", "archived")

INTENT_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*$")

# ── Секции ────────────────────────────────────────────────────────
SECTION_GOAL = "Цель"
SECTION_DOD = "Критерии успеха (DoD)"
SECTION_CONSTRAINTS = "Ограничения"
SECTION_OVERRIDES = "Permissions overrides"
SECTION_PLAN = "План выполнения"
SECTION_JOURNAL = "Журнал"
SECTION_VISIONS = "Видения ролей"
SECTION_FINAL_REPORT = "Финальный отчёт"

SECTION_ORDER = (
    SECTION_GOAL,
    SECTION_DOD,
    SECTION_CONSTRAINTS,
    SECTION_OVERRIDES,
    SECTION_PLAN,
    SECTION_JOURNAL,
    SECTION_VISIONS,
    SECTION_FINAL_REPORT,
)

#: синонимы для update_section (регистр/пробелы нормализуются)
_SECTION_ALIASES = {
    "цель": SECTION_GOAL,
    "цели": SECTION_GOAL,
    "goal": SECTION_GOAL,
    "dod": SECTION_DOD,
    "критерии успеха": SECTION_DOD,
    "ограничения": SECTION_CONSTRAINTS,
    "constraints": SECTION_CONSTRAINTS,
    "permissions overrides": SECTION_OVERRIDES,
    "overrides": SECTION_OVERRIDES,
    "план": SECTION_PLAN,
    "план выполнения": SECTION_PLAN,
    "журнал": SECTION_JOURNAL,
    "journal": SECTION_JOURNAL,
    "видения ролей": SECTION_VISIONS,
    "видения": SECTION_VISIONS,
    "финальный отчёт": SECTION_FINAL_REPORT,
    "финальный отчет": SECTION_FINAL_REPORT,
    "final report": SECTION_FINAL_REPORT,
}

VISIONS_PLACEHOLDER = "<!-- подсекции добавляются по мере появления ролей -->"
FINAL_REPORT_PLACEHOLDER = "<!-- заполняется при закрытии -->"

# ── Retry ─────────────────────────────────────────────────────────
#: задержки между попытками при конфликте записи (ТЗ: 50ms → 200ms → 1s)
RETRY_DELAYS = (0.05, 0.2, 1.0)

#: пауза перед финальной сверкой. Закрывает окно «я проверил изменение — и сразу
#: после этого меня перезаписал другой процесс». Это НЕ файловый лок: нет общего
#: состояния, нет риска отравления при краше. Стоит ~10ms на запись.
SETTLE_DELAY = 0.01

_FRONTMATTER_FENCE = re.compile(r"^---\s*$")
_SECTION_HEADER = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_SUBSECTION_HEADER = re.compile(r"^###\s+(?P<title>.+?)\s*$")
#: открывающий ```yaml и закрывающий ``` — разные фенсы (иначе границы блока не найти)
_YAML_FENCE_OPEN = re.compile(r"^\s*```ya?ml\s*$", re.IGNORECASE)
_YAML_FENCE_CLOSE = re.compile(r"^\s*```\s*$")


# ── Ошибки (fail-loud) ────────────────────────────────────────────

class IntentError(Exception):
    """Базовая ошибка работы с intent.md."""


class IntentNotFoundError(IntentError):
    """intent с таким id не найден."""


class IntentParseError(IntentError):
    """Файл intent.md повреждён (невалидный YAML/frontmatter)."""


class IntentValidationError(IntentError):
    """Невалидные данные запроса (id, статус, поле шага, неизвестная секция)."""


class IntentConflictError(IntentError):
    """Конкурентная запись: не удалось применить изменение за отведённые попытки."""


class IntentPermissionError(IntentError):
    """Попытка расширить права через permissions_overrides."""


# ── Время ─────────────────────────────────────────────────────────

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _today() -> str:
    return date.today().isoformat()


# ── Модель документа ──────────────────────────────────────────────

@dataclass
class Section:
    title: str
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip("\n")


@dataclass
class IntentDoc:
    frontmatter: dict
    preamble: list[str]
    sections: list[Section]

    def section(self, title: str) -> Section | None:
        for sec in self.sections:
            if sec.title == title:
                return sec
        return None


# ── Пути и bootstrap ──────────────────────────────────────────────

def intents_root() -> Path:
    """Корень intents/ (читается из модульной переменной — удобно для тестов)."""
    return Path(INTENTS_DIR)


def active_root() -> Path:
    return intents_root() / ACTIVE_DIRNAME


def archive_root() -> Path:
    return intents_root() / ARCHIVE_DIRNAME


def index_path() -> Path:
    return intents_root() / INDEX_FILENAME


def intent_dir(intent_id: str, *, archived: bool | None = None) -> Path:
    """Папка intent'а. archived=None — ищем в active, затем в archive."""
    _validate_intent_id(intent_id)
    if archived is True:
        return archive_root() / intent_id
    if archived is False:
        return active_root() / intent_id
    active = active_root() / intent_id
    if active.is_dir():
        return active
    return archive_root() / intent_id


def intent_file(intent_id: str) -> Path:
    return intent_dir(intent_id) / INTENT_FILENAME


def ensure_layout() -> None:
    """Создаёт intents/{active,archive} и INDEX.md при первом обращении."""
    active_root().mkdir(parents=True, exist_ok=True)
    archive_root().mkdir(parents=True, exist_ok=True)
    if not index_path().exists():
        _atomic_write(index_path(), _index_header())


def _validate_intent_id(intent_id: str) -> None:
    if not intent_id or not INTENT_ID_RE.match(str(intent_id)):
        raise IntentValidationError(
            f"Некорректный intent_id: {intent_id!r}. Ожидается YYYY-MM-DD-<слаг>, "
            "слаг из латиницы/цифр/дефисов (например 2026-09-25-add-jwt-auth)."
        )


# ── Низкоуровневые helpers ────────────────────────────────────────

def _atomic_write(path: Path, text: str) -> None:
    """
    intent.md.tmp → os.replace(): POSIX-rename атомарен, прямых записей нет.
    Имя tmp уникально на процесс/вызов: общий «intent.md.tmp» ломался при параллельной
    записи двух процессов (один уже переименовал — у второго os.replace падал).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _render_frontmatter(frontmatter: dict) -> str:
    body = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{body}---\n"


def _render(doc: IntentDoc) -> str:
    parts: list[str] = [_render_frontmatter(doc.frontmatter)]
    if doc.preamble:
        parts.append("\n".join(doc.preamble).strip("\n") + "\n")
    for sec in doc.sections:
        body = "\n".join(sec.lines).strip("\n")
        parts.append(f"## {sec.title}\n")
        if body:
            parts.append(body + "\n")
    return "\n".join(parts).rstrip("\n") + "\n"


def _parse(text: str, source: str = INTENT_FILENAME) -> IntentDoc:
    """Парсит intent.md. Невалидный frontmatter/YAML — явная ошибка."""
    lines = text.splitlines()
    if not lines or not _FRONTMATTER_FENCE.match(lines[0]):
        raise IntentParseError(f"{source}: отсутствует frontmatter (ожидалась строка '---' первой)")

    end = None
    for i in range(1, len(lines)):
        if _FRONTMATTER_FENCE.match(lines[i]):
            end = i
            break
    if end is None:
        raise IntentParseError(f"{source}: frontmatter не закрыт строкой '---'")

    raw_frontmatter = "\n".join(lines[1:end])
    try:
        frontmatter = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as e:
        raise IntentParseError(f"{source}: невалидный YAML во frontmatter: {e}") from e
    if not isinstance(frontmatter, dict):
        raise IntentParseError(f"{source}: frontmatter должен быть словарём, получено {type(frontmatter).__name__}")

    preamble: list[str] = []
    sections: list[Section] = []
    current: Section | None = None
    for line in lines[end + 1:]:
        header = _SECTION_HEADER.match(line)
        if header:
            current = Section(title=header.group("title").strip())
            sections.append(current)
            continue
        if current is None:
            preamble.append(line)
        else:
            current.lines.append(line)

    return IntentDoc(frontmatter=frontmatter, preamble=preamble, sections=sections)


def _load(intent_id: str) -> tuple[Path, IntentDoc]:
    path = intent_file(intent_id)
    if not path.is_file():
        raise IntentNotFoundError(f"intent '{intent_id}' не найден: {path} не существует")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise IntentError(f"Не удалось прочитать {path}: {e}") from e
    return path, _parse(text, source=str(path))


def _frontmatter_updated(path: Path) -> str | None:
    """Читает только frontmatter.updated — альтернативный сигнал конфликта (mtime может врать)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(4096)
        doc = _parse(head if head.endswith("\n") else head + "\n", source=str(path))
        return doc.frontmatter.get("updated")
    except IntentParseError:
        return None


def _settled(intent_id: str, verify) -> bool:
    """
    Финальная сверка через короткую паузу: если другой процесс записал сразу после
    нашей проверки, изменение уже могло исчезнуть — тогда retry применит его заново.
    """
    time.sleep(SETTLE_DELAY)
    try:
        _, latest = _load(intent_id)
    except IntentError:
        return False
    return bool(verify(latest))


def _plan_steps(doc: IntentDoc) -> list:
    """Шаги плана из YAML-блока ([] если блок пуст)."""
    data = _read_yaml_block(doc.section(SECTION_PLAN))
    if isinstance(data, dict):
        steps = data.get("plan") or []
    elif isinstance(data, list):
        steps = data
    else:
        steps = []
    return steps if isinstance(steps, list) else []


def _section_text(doc: IntentDoc, title: str) -> str:
    sec = doc.section(title)
    return sec.text if sec else ""


def _mutate(intent_id: str, mutator, *, what: str = "изменение") -> IntentDoc:
    """
    Читаем → модифицируем → сверяем mtime/updated → пишем атомарно → проверяем изменение.

    mutator(doc) возвращает:
      * False — изменение не требуется (идемпотентный no-op);
      * callable(verify_doc) -> bool — предикат «моё изменение есть в этом документе».

    Постпроверка верификатором закрывает гонку check-then-write: если два процесса
    прошли предпроверку и записали одновременно, проигравший видит, что его изменения
    нет в файле, перечитывает и применяет его заново — обе записи сохраняются.
    """
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(attempts):
        path, doc = _load(intent_id)
        if doc.frontmatter.get("status") == "archived":
            raise IntentValidationError(
                f"intent '{intent_id}' в архиве — только чтение ({what} запрещено)"
            )

        before_mtime = path.stat().st_mtime_ns
        before_updated = doc.frontmatter.get("updated")

        result = mutator(doc, attempt=attempt)
        if result is False:
            return doc
        verify = result if callable(result) else (lambda _doc: True)

        after_mtime = path.stat().st_mtime_ns
        stale = after_mtime != before_mtime or _frontmatter_updated(path) != before_updated

        if not stale:
            doc.frontmatter["updated"] = _utc_iso()
            _atomic_write(path, _render(doc))
            try:
                _, current = _load(intent_id)
            except IntentError:
                current = None
            if current is not None and verify(current) and _settled(intent_id, verify):
                return current
            logger.warning(f"⚠️ intent '{intent_id}': наше изменение перезаписано параллельно")

        if attempt < len(RETRY_DELAYS):
            delay = RETRY_DELAYS[attempt]
            logger.warning(
                f"⚠️ intent '{intent_id}': конкурентная запись, retry {attempt + 1}/{len(RETRY_DELAYS)} через {delay}s"
            )
            time.sleep(delay)
            continue

        raise IntentConflictError(
            f"intent '{intent_id}': не удалось применить {what} — файл меняется параллельно "
            f"({attempts} попыток). Повторите операцию."
        )

    raise IntentConflictError(f"intent '{intent_id}': конфликт записи ({what})")


# ── YAML-блоки в секциях ──────────────────────────────────────────

def _yaml_block_bounds(section: Section) -> tuple[int, int] | None:
    """Границы ПЕРВОГО ```yaml-блока в секции: (индекс открывающего фенса, индекс закрывающего)."""
    opens = [i for i, line in enumerate(section.lines) if _YAML_FENCE_OPEN.match(line)]
    if not opens:
        return None
    if len(opens) > 1:
        logger.warning(
            f"Секция '{section.title}': найдено больше одного YAML-блока — используется первый"
        )
    start = opens[0]
    for j in range(start + 1, len(section.lines)):
        if _YAML_FENCE_CLOSE.match(section.lines[j]):
            return (start, j)
    raise IntentParseError(f"Секция '{section.title}': YAML-блок не закрыт (нет ```)")


def _read_yaml_block(section: Section | None) -> object | None:
    """Данные первого YAML-блока секции. Невалидный YAML → IntentParseError (fail-loud)."""
    if section is None:
        return None
    bounds = _yaml_block_bounds(section)
    if not bounds:
        return None
    start, end = bounds
    raw = "\n".join(section.lines[start + 1:end]).strip()
    if not raw:
        return None
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise IntentParseError(f"Секция '{section.title}': невалидный YAML-блок: {e}") from e


def _write_yaml_block(section: Section, data: object) -> None:
    """Заменяет первый YAML-блок или создаёт его в конце секции."""
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip("\n")
    block = ["```yaml", *body.splitlines(), "```"]
    bounds = _yaml_block_bounds(section)
    if bounds:
        start, end = bounds
        section.lines = section.lines[:start] + block + section.lines[end + 1:]
    else:
        lines = [line for line in section.lines if line.strip()]
        if lines:
            lines.append("")
        section.lines = lines + block


def _ensure_section(doc: IntentDoc, title: str) -> Section:
    """Возвращает секцию, создавая её (в правильном порядке) при отсутствии."""
    sec = doc.section(title)
    if sec is not None:
        return sec

    sec = Section(title=title)
    if title in SECTION_ORDER:
        target = SECTION_ORDER.index(title)
        for i, existing in enumerate(doc.sections):
            if existing.title in SECTION_ORDER and SECTION_ORDER.index(existing.title) > target:
                doc.sections.insert(i, sec)
                return sec
    doc.sections.append(sec)
    return sec


def _resolve_section_title(doc: IntentDoc, section_name: str) -> str:
    key = " ".join(str(section_name or "").split()).casefold()
    if not key:
        raise IntentValidationError("Пустое имя секции")
    if key in _SECTION_ALIASES:
        return _SECTION_ALIASES[key]
    for sec in doc.sections:
        if sec.title.casefold() == key:
            return sec.title
    known = ", ".join(s.title for s in doc.sections) or "нет секций"
    raise IntentValidationError(f"Секция '{section_name}' не найдена. Есть: {known}")


# ── Шаблон ────────────────────────────────────────────────────────

def _template(intent_id: str, title: str, created_by: str) -> IntentDoc:
    now = _utc_iso()
    return IntentDoc(
        frontmatter={
            "id": intent_id,
            "title": title,
            "status": "active",
            "created": now,
            "updated": now,
            "created_by": created_by,
        },
        preamble=[f"# Intent: {title}"],
        sections=[
            Section(SECTION_GOAL, [""]),
            Section(SECTION_DOD, ["- [ ] <проверяемый критерий>"]),
            Section(SECTION_CONSTRAINTS, ["- Технические: <...>", "- Ресурсные: <...>"]),
            Section(SECTION_OVERRIDES, []),
            Section(SECTION_PLAN, []),
            Section(SECTION_JOURNAL, [f"- {now} {created_by}: intent создан"]),
            Section(SECTION_VISIONS, [VISIONS_PLACEHOLDER]),
            Section(SECTION_FINAL_REPORT, [FINAL_REPORT_PLACEHOLDER]),
        ],
    )


def _fill_yaml_blocks(doc: IntentDoc) -> None:
    """permissions_overrides и plan присутствуют всегда, даже пустыми."""
    overrides = _ensure_section(doc, SECTION_OVERRIDES)
    if _read_yaml_block(overrides) is None:
        _write_yaml_block(overrides, {"permissions_overrides": []})
    plan = _ensure_section(doc, SECTION_PLAN)
    if _read_yaml_block(plan) is None:
        _write_yaml_block(plan, {"plan": []})


# ── Публичное API ─────────────────────────────────────────────────

def create(intent_id: str, title: str, created_by: str) -> Path:
    """Создаёт intents/active/<id>/intent.md по шаблону и строку в INDEX.md."""
    ensure_layout()
    _validate_intent_id(intent_id)
    if not str(title or "").strip():
        raise IntentValidationError("title обязателен")
    if not str(created_by or "").strip():
        raise IntentValidationError("created_by обязателен")

    target = active_root() / intent_id
    if target.exists() or (archive_root() / intent_id).exists():
        raise IntentValidationError(f"intent '{intent_id}' уже существует ({target})")

    doc = _template(intent_id, str(title).strip(), str(created_by).strip())
    _fill_yaml_blocks(doc)
    path = target / INTENT_FILENAME
    _atomic_write(path, _render(doc))
    _index_upsert(intent_id, title=doc.frontmatter["title"], status="active", closed="", rel_path=f"{ACTIVE_DIRNAME}/{intent_id}/")
    return path


def read(intent_id: str) -> dict:
    """Распарсенная структура intent'а: frontmatter + все секции (markdown и YAML)."""
    path, doc = _load(intent_id)
    sections = {sec.title: sec.text for sec in doc.sections}

    overrides_data = _read_yaml_block(doc.section(SECTION_OVERRIDES)) or {}
    if isinstance(overrides_data, dict):
        overrides = overrides_data.get("permissions_overrides") or []
    else:
        overrides = overrides_data or []

    plan_data = _read_yaml_block(doc.section(SECTION_PLAN)) or {}
    if isinstance(plan_data, dict):
        plan = plan_data.get("plan") or []
    else:
        plan = plan_data or []

    journal_sec = doc.section(SECTION_JOURNAL)
    journal = []
    if journal_sec:
        journal = [line.strip()[2:].strip() for line in journal_sec.lines
                   if line.strip().startswith("- ") and line.strip()]

    visions: dict[str, str] = {}
    visions_sec = doc.section(SECTION_VISIONS)
    if visions_sec:
        current_role = None
        buffer: list[str] = []
        for line in visions_sec.lines:
            sub = _SUBSECTION_HEADER.match(line)
            if sub:
                if current_role:
                    visions[current_role] = "\n".join(buffer).strip()
                current_role = sub.group("title").strip()
                buffer = []
                continue
            if current_role and line.strip() and not line.strip().startswith("<!--"):
                buffer.append(line.rstrip())
        if current_role:
            visions[current_role] = "\n".join(buffer).strip()

    return {
        "path": str(path),
        "frontmatter": dict(doc.frontmatter),
        "sections": sections,
        "goal": sections.get(SECTION_GOAL, ""),
        "dod": sections.get(SECTION_DOD, ""),
        "constraints": sections.get(SECTION_CONSTRAINTS, ""),
        "permissions_overrides": overrides,
        "plan": plan,
        "journal": journal,
        "visions": visions,
        "final_report": sections.get(SECTION_FINAL_REPORT, ""),
    }


def update_status(intent_id: str, new_status: str) -> None:
    """
    Переход жизненного цикла во frontmatter: active | blocked | done | archived.
    Отдельно от update_section: фиксированный enum + точка для хуков (архивация/уведомления).
    """
    status = str(new_status or "").strip().lower()
    if status not in VALID_STATUSES:
        raise IntentValidationError(
            f"Недопустимый статус '{new_status}'. Разрешены: {', '.join(VALID_STATUSES)}"
        )
    if status == "archived":
        raise IntentValidationError("Статус 'archived' ставится только через archive() — он переносит папку")

    def mutator(doc: IntentDoc, attempt: int = 0):
        if doc.frontmatter.get("status") == status:
            return False
        doc.frontmatter["status"] = status
        return lambda d: d.frontmatter.get("status") == status

    _mutate(intent_id, mutator, what="смена статуса")

    title = _load(intent_id)[1].frontmatter.get("title", "")
    closed = _today() if status == "done" else _existing_closed(intent_id)
    _index_upsert(
        intent_id, title=title, status=status, closed=closed,
        rel_path=f"{ACTIVE_DIRNAME}/{intent_id}/",
    )


def update_step(intent_id: str, step_id: str, **fields) -> None:
    """
    Обновляет поля одного шага плана (YAML-блок секции «План выполнения»).
    Другие шаги не трогаются. Неизвестный шаг — ошибка.
    """
    if not str(step_id or "").strip():
        raise IntentValidationError("step_id обязателен")
    if not fields:
        raise IntentValidationError("Не передано ни одного поля для обновления")
    if "id" in fields:
        raise IntentValidationError("Поле 'id' менять нельзя — это ключ шага")

    def mutator(doc: IntentDoc, attempt: int = 0):
        section = _ensure_section(doc, SECTION_PLAN)
        data = _read_yaml_block(section)
        block = dict(data) if isinstance(data, dict) else {}
        raw_steps = block.get("plan")
        if raw_steps is None:
            steps: list = []
        elif isinstance(raw_steps, list):
            steps = raw_steps
        else:
            raise IntentParseError(f"{SECTION_PLAN}: ожидается список шагов (plan: [...]), получено {type(raw_steps).__name__}")

        found = False
        for step in steps:
            if isinstance(step, dict) and str(step.get("id")) == str(step_id):
                step.update(fields)
                found = True
                break
        if not found:
            known = ", ".join(str(s.get("id")) for s in steps if isinstance(s, dict)) or "план пуст"
            raise IntentValidationError(
                f"Шаг '{step_id}' не найден в плане ({known}). Структуру плана пишет планировщик "
                "через update_section('План выполнения', ...)."
            )

        # Явное присваивание: block.get('plan') or [] отвязывал бы список при пустом значении
        block["plan"] = steps
        _write_yaml_block(section, block)
        expected_fields = dict(fields)

        def verify(d: IntentDoc) -> bool:
            for item in _plan_steps(d):
                if isinstance(item, dict) and str(item.get("id")) == str(step_id):
                    return all(str(item.get(k)) == str(v) for k, v in expected_fields.items())
            return False

        return verify

    _mutate(intent_id, mutator, what=f"обновление шага {step_id}")


def append_journal(intent_id: str, role: str, text: str) -> None:
    """Добавляет строку в журнал. Идемпотентно: та же роль + тот же текст подряд → пропуск."""
    role = str(role or "").strip()
    text = " ".join(str(text or "").split())
    if not role or not text:
        raise IntentValidationError("append_journal требует непустые role и text")

    def mutator(doc: IntentDoc, attempt: int = 0):
        section = _ensure_section(doc, SECTION_JOURNAL)
        entries = [line.strip() for line in section.lines if line.strip().startswith("- ")]
        if entries:
            last = entries[-1][2:].strip()
            if _journal_entry_matches(last, role, text):
                return False  # идемпотентность по ТЗ: та же роль+текст последней строкой
        if attempt > 0 and any(_journal_entry_matches(e[2:].strip(), role, text) for e in entries):
            # Retry после проигранной гонки: наша запись уже могла уехать выше по журналу
            # (другой процесс дописал свою) — повторно не добавляем, иначе получим дубль.
            return False
        section.lines = [line for line in section.lines if line.strip()]
        section.lines.append(f"- {_utc_iso()} {role}: {text}")
        expected_suffix = f"{role}: {text}"

        def verify(d: IntentDoc) -> bool:
            sec = d.section(SECTION_JOURNAL)
            return bool(sec and any(line.strip().endswith(expected_suffix) for line in sec.lines))

        return verify

    _mutate(intent_id, mutator, what="запись в журнал")


def append_vision(intent_id: str, role: str, text: str) -> None:
    """Добавляет строку в подсекцию роли (секция «Видения ролей»). Идемпотентно."""
    role = str(role or "").strip()
    text = " ".join(str(text or "").split())
    if not role or not text:
        raise IntentValidationError("append_vision требует непустые role и text")

    def mutator(doc: IntentDoc, attempt: int = 0):
        section = _ensure_section(doc, SECTION_VISIONS)
        lines = [line for line in section.lines if line.strip() != VISIONS_PLACEHOLDER]

        def verify(d: IntentDoc) -> bool:
            sec = d.section(SECTION_VISIONS)
            if not sec:
                return False
            in_role = False
            for line in sec.lines:
                sub = _SUBSECTION_HEADER.match(line)
                if sub:
                    in_role = sub.group("title").strip() == role
                    continue
                if in_role and line.strip().startswith("- ") and line.strip().endswith(text):
                    return True
            return False

        start = None
        for i, line in enumerate(lines):
            sub = _SUBSECTION_HEADER.match(line)
            if sub and sub.group("title").strip() == role:
                start = i
                break

        if start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"### {role}")
            lines.append(f"- {_utc_iso()} {text}")
            section.lines = lines
            return verify

        end = len(lines)
        for j in range(start + 1, len(lines)):
            if _SUBSECTION_HEADER.match(lines[j]):
                end = j
                break
        entries = [line.strip() for line in lines[start + 1:end] if line.strip().startswith("- ")]
        if entries and entries[-1][2:].strip().endswith(text):
            return False
        if attempt > 0 and any(e[2:].strip().endswith(text) for e in entries):
            return False  # retry: запись уже есть выше по подсекции

        insert_at = end
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, f"- {_utc_iso()} {text}")
        section.lines = lines
        return verify

    _mutate(intent_id, mutator, what="запись видения роли")


def update_overrides(intent_id: str, role: str, restrict: str) -> None:
    """
    Добавляет СУЖЕНИЕ прав роли в permissions_overrides. Расширение запрещено.

    Семантика монотонная: набор ограничений роли только растёт (объединение).
    Пустой restrict означает «снять ограничения» → IntentPermissionError.
    """
    role = str(role or "").strip()
    tokens = [t for t in re.split(r"[,\s]+", str(restrict or "").strip()) if t]
    if not role:
        raise IntentValidationError("update_overrides требует непустую role")
    if not tokens:
        raise IntentPermissionError(
            f"Нельзя снять ограничения у роли '{role}': permissions_overrides допускает только сужение прав"
        )
    if "*" in tokens or "all" in tokens or "any" in tokens:
        raise IntentPermissionError(
            f"Нельзя ограничить «все права» ('{','.join(tokens)}') — укажи конкретные разрешения"
        )

    def mutator(doc: IntentDoc, attempt: int = 0):
        section = _ensure_section(doc, SECTION_OVERRIDES)
        data = _read_yaml_block(section)
        if isinstance(data, list):
            data = {"permissions_overrides": data}
        block = dict(data) if isinstance(data, dict) else {}
        raw_overrides = block.get("permissions_overrides")
        if raw_overrides is None:
            overrides: list = []
        elif isinstance(raw_overrides, list):
            overrides = raw_overrides
        else:
            raise IntentParseError(f"{SECTION_OVERRIDES}: permissions_overrides должен быть списком")

        def verify(d: IntentDoc) -> bool:
            data_now = _read_yaml_block(d.section(SECTION_OVERRIDES))
            block_now = data_now if isinstance(data_now, dict) else {}
            for item in block_now.get("permissions_overrides") or []:
                if isinstance(item, dict) and str(item.get("role")) == role:
                    return set(tokens) <= {str(x) for x in (item.get("restrict") or [])}
            return False

        entry = None
        for item in overrides:
            if isinstance(item, dict) and str(item.get("role")) == role:
                entry = item
                break

        if entry is None:
            overrides.append({"role": role, "restrict": sorted(tokens)})
        else:
            current = entry.get("restrict") or []
            if not isinstance(current, list):
                raise IntentParseError(f"{SECTION_OVERRIDES}: restrict роли '{role}' должен быть списком")
            merged = sorted({str(x) for x in current} | set(tokens))
            if merged == sorted({str(x) for x in current}):
                return False  # ограничение уже есть — расширения не требуется
            entry["restrict"] = merged

        # Явное присваивание — см. комментарий в update_step
        block["permissions_overrides"] = overrides
        _write_yaml_block(section, block)
        return verify

    _mutate(intent_id, mutator, what=f"сужение прав роли {role}")


def update_section(intent_id: str, section_name: str, new_text: str) -> None:
    """
    Заменяет содержимое секции целиком (статические секции: Цель, DoD, Ограничения,
    а также структура плана). Соседние секции не затрагиваются.
    Пер-секционных утилит (update_goal/update_dod/...) намеренно нет.
    """
    if new_text is None:
        raise IntentValidationError("new_text обязателен (для очистки передай пустую строку)")

    def mutator(doc: IntentDoc, attempt: int = 0):
        title = _resolve_section_title(doc, section_name)
        section = _ensure_section(doc, title)
        body = str(new_text).strip("\n")
        lines = body.splitlines() if body else [""]
        if lines == section.lines:
            return False
        section.lines = lines
        if title == SECTION_OVERRIDES:
            _fill_yaml_blocks(doc)   # секция overrides всегда содержит YAML-блок
        expected = section.text

        def verify(d: IntentDoc) -> bool:
            return _section_text(d, _resolve_section_title(d, section_name)) == expected

        return verify

    _mutate(intent_id, mutator, what=f"замена секции {section_name}")


def archive(intent_id: str) -> None:
    """Переносит папку intent'а (со всем содержимым) в archive/ и обновляет INDEX.md."""
    ensure_layout()
    _validate_intent_id(intent_id)
    src = active_root() / intent_id
    dst = archive_root() / intent_id
    if not src.is_dir():
        if dst.is_dir():
            raise IntentValidationError(f"intent '{intent_id}' уже в архиве")
        raise IntentNotFoundError(f"intent '{intent_id}' не найден: {src} не существует")

    def mutator(doc: IntentDoc, attempt: int = 0):
        if doc.frontmatter.get("status") == "archived":
            return False
        doc.frontmatter["status"] = "archived"
        return lambda d: d.frontmatter.get("status") == "archived"

    _mutate(intent_id, mutator, what="архивация")  # статус ставим ДО перемещения
    shutil.move(str(src), str(dst))
    logger.info(f"📦 intent '{intent_id}' перемещён в {dst}")

    title = ""
    try:
        title = _parse((dst / INTENT_FILENAME).read_text(encoding="utf-8")).frontmatter.get("title", "")
    except Exception:
        pass
    _index_upsert(
        intent_id, title=title, status="archived",
        closed=_existing_closed(intent_id) or _today(),
        rel_path=f"{ARCHIVE_DIRNAME}/{intent_id}/",
    )


def list_intents(status: str | None = None) -> list[dict]:
    """
    Список intent'ов (по умолчанию — активные). Читает frontmatter папок,
    а не INDEX.md — реестр производный и может отставать.

    Доступно и как `intent.list()` (имя из ТЗ) — через module __getattr__:
    модуль не затеняет builtin list, иначе ломается isinstance(x, list).
    """
    ensure_layout()
    if status and status not in VALID_STATUSES:
        raise IntentValidationError(f"Недопустимый статус '{status}'. Разрешены: {', '.join(VALID_STATUSES)}")

    roots = []
    if status == "archived":
        roots.append((ARCHIVE_DIRNAME, archive_root()))
    else:
        roots.append((ACTIVE_DIRNAME, active_root()))

    result = []
    for dirname, root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            file = child / INTENT_FILENAME
            if not child.is_dir() or not file.is_file():
                continue
            try:
                doc = _parse(file.read_text(encoding="utf-8"), source=str(file))
            except IntentError as e:
                logger.warning(f"⚠️ list(): {e}")
                continue
            item_status = doc.frontmatter.get("status")
            if status and item_status != status:
                continue
            result.append({
                "id": doc.frontmatter.get("id") or child.name,
                "title": doc.frontmatter.get("title", ""),
                "status": item_status,
                "created": doc.frontmatter.get("created"),
                "updated": doc.frontmatter.get("updated"),
                "created_by": doc.frontmatter.get("created_by"),
                "path": f"{dirname}/{child.name}/",
            })
    return result

# ── Внутреннее: журнал и INDEX.md ─────────────────────────────────

def _journal_entry_matches(entry: str, role: str, text: str) -> bool:
    """Сравнение «последняя запись журнала == (role, text)» без учёта timestamp."""
    parts = entry.split(": ", 1)
    if len(parts) != 2:
        return False
    head, body = parts
    head_tokens = head.split()
    if len(head_tokens) < 2 or head_tokens[-1] != role:
        return False
    return " ".join(body.split()) == text


def _index_header() -> str:
    return (
        "# Реестр задач (intents)\n\n"
        "| ID | Title | Status | Created | Closed | Path |\n"
        "|----|-------|--------|---------|--------|------|\n"
    )


def _index_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or cells[0] in ("ID", "") or set(cells[0]) <= {"-", " ", ":"}:
            continue
        rows.append(cells)
    return rows


def _existing_closed(intent_id: str) -> str:
    """Текущее значение Closed из INDEX.md (сохраняем дату закрытия при смене статуса)."""
    path = index_path()
    if not path.is_file():
        return ""
    for cells in _index_rows(path.read_text(encoding="utf-8")):
        if cells[0] == intent_id and len(cells) > 4:
            return "" if cells[4] in ("—", "-", "") else cells[4]
    return ""


def _index_upsert(intent_id: str, *, title: str, status: str, closed: str, rel_path: str) -> None:
    """Строка в INDEX.md: создаём или обновляем. Пишем атомарно (retry на конфликте)."""
    ensure_layout()
    path = index_path()
    created = ""
    for attempt in range(len(RETRY_DELAYS) + 1):
        before_mtime = path.stat().st_mtime_ns if path.is_file() else None
        text = path.read_text(encoding="utf-8") if path.is_file() else _index_header()
        rows = _index_rows(text)

        if not created:
            for cells in rows:
                if cells[0] == intent_id and len(cells) > 3:
                    created = cells[3] if cells[3] not in ("—", "-") else ""
                    break
        if not created:
            try:
                created = str(_parse(intent_file(intent_id).read_text(encoding="utf-8")).frontmatter.get("created", ""))
            except Exception:
                created = ""
        created = (created or _today())[:10]

        row = [
            intent_id, title or "—", status,
            created, closed or "—", rel_path,
        ]
        updated = [cells for cells in rows if cells[0] != intent_id]
        updated.append(row)
        body = "\n".join("| " + " | ".join(cells) + " |" for cells in updated)
        new_text = _index_header() + body + "\n"

        after_mtime = path.stat().st_mtime_ns if path.is_file() else None
        if after_mtime != before_mtime:
            if attempt < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt])
                continue
            raise IntentConflictError(f"INDEX.md: не удалось обновить строку {intent_id} — файл меняется параллельно")

        _atomic_write(path, new_text)
        try:
            survived = path.read_text(encoding="utf-8") == new_text
        except OSError:
            survived = False
        if survived:
            return
        if attempt < len(RETRY_DELAYS):
            time.sleep(RETRY_DELAYS[attempt])
            continue
        raise IntentConflictError(f"INDEX.md: строку {intent_id} перезаписали параллельно")


def export_json(intent_id: str) -> str:
    """Отладочный дамп распарсенного intent'а."""
    return json.dumps(read(intent_id), ensure_ascii=False, indent=2, default=str)


def __getattr__(name: str):
    """
    Публичное имя `list()` из ТЗ без затенения builtin внутри модуля (PEP 562).

    Если бы здесь была обычная `def list(...)`, то `isinstance(steps, list)`
    внутри модуля начал бы получать функцию вместо типа.
    """
    if name == "list":
        return list_intents
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
