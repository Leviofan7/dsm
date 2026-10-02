"""
intent.md как артефакт задачи (ТЗ 4.1) — unit, integration и concurrency.

Покрыты все 10 критериев приёмки:
  1 create → папка + шаблон + строка в INDEX.md
  2 read → frontmatter + секции + распарсенные YAML-блоки
  3 update_step меняет только свой шаг
  4 append_journal идемпотентен
  5 update_overrides только сужает права
  6 update_section не трогает соседние секции
  7 archive переносит папку и обновляет INDEX.md
  8 параллельная запись из двух процессов: retry, ничего не потеряно
  9 невалидный YAML → явная ошибка
 10 интеграционный цикл create → update_step → append_journal → archive
"""

import multiprocessing
import sys
from pathlib import Path

import pytest
import yaml

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from services import intent

INTENT_ID = "2026-09-25-add-jwt-auth"
PLAN_YAML = (
    "```yaml\n"
    "plan:\n"
    "  - id: s1\n"
    "    step: описать схему\n"
    "    role: code_architect\n"
    "    status: pending\n"
    "  - id: s2\n"
    "    step: реализовать\n"
    "    role: coder\n"
    "    status: pending\n"
    "```"
)


@pytest.fixture
def intents(tmp_path, monkeypatch):
    """Изолированный intents/ в tmp: модуль читает INTENTS_DIR при каждом вызове."""
    monkeypatch.setattr(intent, "INTENTS_DIR", tmp_path / "intents")
    return intent


@pytest.fixture
def created(intents):
    intents.create(INTENT_ID, "Добавить JWT-авторизацию в API", "doorman")
    return INTENT_ID


# ── 1. create ─────────────────────────────────────────────────────

def test_create_makes_folder_template_and_index(intents):
    path = intents.create(INTENT_ID, "Добавить JWT", "doorman")

    assert path == intents.active_root() / INTENT_ID / "intent.md"
    assert path.is_file()
    assert path.parent.parent == intents.active_root()

    text = path.read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---")[1])
    assert frontmatter["id"] == INTENT_ID
    assert frontmatter["title"] == "Добавить JWT"
    assert frontmatter["status"] == "active"
    assert frontmatter["created_by"] == "doorman"
    assert frontmatter["created"] and frontmatter["updated"]

    for title in intent.SECTION_ORDER:
        assert f"## {title}" in text, f"нет секции {title}"

    # permissions_overrides и plan присутствуют всегда, даже пустыми
    assert "permissions_overrides: []" in text
    assert "plan: []" in text

    index = (intents.intents_root() / "INDEX.md").read_text(encoding="utf-8")
    assert INTENT_ID in index and "| active |" in index and f"active/{INTENT_ID}/" in index


def test_create_rejects_duplicates_and_bad_ids(intents):
    intents.create(INTENT_ID, "t", "doorman")
    with pytest.raises(intent.IntentValidationError):
        intents.create(INTENT_ID, "t", "doorman")

    for bad in ["add-jwt", "2026-09-25-AddJWT", "../escape", "", "2026-09-25-"]:
        with pytest.raises(intent.IntentValidationError):
            intents.create(bad, "t", "doorman")


# ── 2. read ───────────────────────────────────────────────────────

def test_read_parses_frontmatter_sections_and_yaml(intents, created):
    intents.update_section(created, "Цель", "Добавить JWT в API")
    intents.update_section(created, "План выполнения", PLAN_YAML)
    intents.update_overrides(created, "coder", "write_any")

    data = intents.read(created)

    assert data["frontmatter"]["id"] == INTENT_ID
    assert data["goal"] == "Добавить JWT в API"
    assert [s["id"] for s in data["plan"]] == ["s1", "s2"]
    assert data["plan"][0]["role"] == "code_architect"
    assert data["permissions_overrides"] == [{"role": "coder", "restrict": ["write_any"]}]
    assert data["journal"] and "intent создан" in data["journal"][0]
    assert set(intent.SECTION_ORDER) <= set(data["sections"])


def test_read_missing_intent_raises(intents):
    with pytest.raises(intent.IntentNotFoundError):
        intents.read("2026-09-25-nope")


# ── 3. update_step ────────────────────────────────────────────────

def test_update_step_touches_only_its_step(intents, created):
    intents.update_section(created, "План", PLAN_YAML)
    intents.update_step(created, "s1", status="done", report="схема готова")

    plan = intents.read(created)["plan"]
    assert plan[0] == {
        "id": "s1", "step": "описать схему", "role": "code_architect",
        "status": "done", "report": "схема готова",
    }
    assert plan[1] == {"id": "s2", "step": "реализовать", "role": "coder", "status": "pending"}


def test_update_step_validates_input(intents, created):
    intents.update_section(created, "План", PLAN_YAML)
    with pytest.raises(intent.IntentValidationError):
        intents.update_step(created, "ghost", status="done")
    with pytest.raises(intent.IntentValidationError):
        intents.update_step(created, "s1", id="s9")
    with pytest.raises(intent.IntentValidationError):
        intents.update_step(created, "s1")


# ── 4. append_journal (идемпотентность) ───────────────────────────

def test_append_journal_is_idempotent(intents, created):
    intents.append_journal(created, "coder", "начал реализацию")
    first = intents.read(created)["journal"]

    intents.append_journal(created, "coder", "начал реализацию")   # тот же текст → пропуск
    assert intents.read(created)["journal"] == first

    intents.append_journal(created, "coder", "другой текст")
    assert len(intents.read(created)["journal"]) == len(first) + 1

    # тот же текст, но другая роль — это новая запись
    intents.append_journal(created, "tester", "начал реализацию")
    assert len(intents.read(created)["journal"]) == len(first) + 2


def test_append_vision_per_role(intents, created):
    intents.append_vision(created, "coder", "нужен refresh-токен")
    intents.append_vision(created, "coder", "нужен refresh-токен")  # дубль → пропуск
    intents.append_vision(created, "tester", "нужны e2e-тесты")

    visions = intents.read(created)["visions"]
    assert "refresh-токен" in visions["coder"]
    assert "e2e-тесты" in visions["tester"]
    body = intents.read(created)["sections"]["Видения ролей"]
    assert body.count("нужен refresh-токен") == 1
    assert "### coder" in body and "### tester" in body


def test_append_validates_arguments(intents, created):
    for role, text in (("", "x"), ("coder", ""), ("coder", "   ")):
        with pytest.raises(intent.IntentValidationError):
            intents.append_journal(created, role, text)
        with pytest.raises(intent.IntentValidationError):
            intents.append_vision(created, role, text)


# ── 5. update_overrides — только сужение ──────────────────────────

def test_update_overrides_narrows_only(intents, created):
    intents.update_overrides(created, "coder", "write_any")
    assert intents.read(created)["permissions_overrides"] == [
        {"role": "coder", "restrict": ["write_any"]}
    ]

    # добавление второго ограничения — сужение, разрешено
    intents.update_overrides(created, "coder", "run_terminal_command")
    assert [{"role": "coder", "restrict": ["run_terminal_command", "write_any"]}] == intents.read(created)["permissions_overrides"]

    # повтор того же — no-op
    intents.update_overrides(created, "coder", "write_any")
    assert len(intents.read(created)["permissions_overrides"]) == 1


def test_update_overrides_blocks_expansion(intents, created):
    intents.update_overrides(created, "coder", "write_any")

    with pytest.raises(intent.IntentPermissionError):
        intents.update_overrides(created, "coder", "")          # снять ограничения
    with pytest.raises(intent.IntentPermissionError):
        intents.update_overrides(created, "coder", "*")         # «все права»
    with pytest.raises(intent.IntentValidationError):
        intents.update_overrides(created, "", "write_any")      # без роли

    assert intents.read(created)["permissions_overrides"] == [
        {"role": "coder", "restrict": ["write_any"]}
    ]


# ── 6. update_section ─────────────────────────────────────────────

def test_update_section_replaces_only_target(intents, created):
    intents.update_section(created, "Цель", "Первая цель")
    intents.update_section(created, "Ограничения", "- Технические: Python 3.12")
    before_neighbour = intents.read(created)["constraints"]

    intents.update_section(created, "doD", "- [x] готово")
    data = intents.read(created)

    assert data["dod"] == "- [x] готово"
    assert data["constraints"] == before_neighbour == "- Технические: Python 3.12"
    assert data["goal"] == "Первая цель"


def test_update_section_unknown_name_raises(intents, created):
    with pytest.raises(intent.IntentValidationError):
        intents.update_section(created, "Чего нет", "x")


# ── 7. archive ────────────────────────────────────────────────────

def test_archive_moves_folder_and_updates_index(intents, created):
    intents.append_journal(created, "coder", "готово")
    intents.archive(created)

    assert not (intents.active_root() / INTENT_ID).exists()
    archived_dir = intents.archive_root() / INTENT_ID
    assert (archived_dir / "intent.md").is_file()

    data = intents.read(created)
    assert data["frontmatter"]["status"] == "archived"
    assert "готово" in " ".join(data["journal"])

    index = (intents.intents_root() / "INDEX.md").read_text(encoding="utf-8")
    assert f"archive/{INTENT_ID}/" in index
    assert "| archived |" in index

    assert [i["id"] for i in intents.list()] == []            # активных нет
    assert [i["id"] for i in intents.list("archived")] == [INTENT_ID]
    assert [i["id"] for i in intents.list_intents()] == []

    with pytest.raises(intent.IntentValidationError):
        intents.archive(created)                              # повторно нельзя
    with pytest.raises(intent.IntentValidationError):
        intents.update_status(created, "active")              # архив — только чтение


def test_update_status_lifecycle_and_index(intents, created):
    intents.update_status(created, "blocked")
    assert intents.read(created)["frontmatter"]["status"] == "blocked"
    assert [i["id"] for i in intents.list("blocked")] == [INTENT_ID]

    intents.update_status(created, "done")
    assert [i["id"] for i in intents.list("done")] == [INTENT_ID]
    index = (intents.intents_root() / "INDEX.md").read_text(encoding="utf-8")
    assert "| done |" in index

    with pytest.raises(intent.IntentValidationError):
        intents.update_status(created, "turbo")
    with pytest.raises(intent.IntentValidationError):
        intents.update_status(created, "archived")            # только через archive()


# ── 8. Конкурентная запись ────────────────────────────────────────

def _writer(intents_dir: str, intent_id: str, role: str, count: int) -> None:
    """Отдельный процесс: пишет в журнал то же, что делает агент."""
    from pathlib import Path as _Path
    from services import intent as _intent

    _intent.INTENTS_DIR = _Path(intents_dir)
    for i in range(count):
        _intent.append_journal(intent_id, role, f"запись {role}-{i}")


def _count_exact(journal: list, role: str, text: str) -> int:
    """Сколько раз ровно эта запись есть в журнале (без подстрочных совпадений)."""
    suffix = f"{role}: {text}"
    return sum(1 for line in journal if line.endswith(suffix))


def test_concurrent_appends_from_two_processes(intents, created):
    """Retry с перечитыванием: обе записи сохранены, ни одна не потеряна."""
    ctx = multiprocessing.get_context("fork")
    intents_dir = str(intents.intents_root())

    procs = [
        ctx.Process(target=_writer, args=(intents_dir, INTENT_ID, role, 3))
        for role in ("coder", "tester")
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0, f"процесс-писатель упал с кодом {p.exitcode}"

    journal = intents.read(INTENT_ID)["journal"]
    for role in ("coder", "tester"):
        for i in range(3):
            text = f"запись {role}-{i}"
            assert _count_exact(journal, role, text) == 1, f"запись {text} потеряна или задублирована"


def test_concurrent_appends_three_processes(intents, created):
    """Три роли пишут одновременно — потери и дубли недопустимы."""
    ctx = multiprocessing.get_context("fork")
    intents_dir = str(intents.intents_root())
    roles = ("coder", "tester", "reviewer")

    procs = [
        ctx.Process(target=_writer, args=(intents_dir, INTENT_ID, role, 4))
        for role in roles
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=90)
        assert p.exitcode == 0, f"процесс-писатель упал с кодом {p.exitcode}"

    journal = intents.read(INTENT_ID)["journal"]
    for role in roles:
        for i in range(4):
            text = f"запись {role}-{i}"
            assert _count_exact(journal, role, text) == 1, f"запись {text} потеряна или задублирована"


def test_conflict_exhaustion_is_loud(intents, created, monkeypatch):
    """Если изменение не выживает (гонка не разрешилась) — явная ошибка, а не тихая потеря."""
    real_write = intent._atomic_write

    def clobbering_write(path, text):
        real_write(path, text)
        # «другой процесс» сразу перезаписывает файл своей версией, вытирая наше изменение
        real_write(path, text.replace("не доехало", "чужая запись"))

    monkeypatch.setattr(intent, "_atomic_write", clobbering_write)
    monkeypatch.setattr(intent, "RETRY_DELAYS", (0.0, 0.0, 0.0))

    with pytest.raises(intent.IntentConflictError):
        intents.append_journal(created, "coder", "не доехало")

    # файл остался валидным: неудачная запись ничего не сломала
    assert intents.read(created)["frontmatter"]["status"] == "active"


# ── 9. Fail-loud на битом YAML ────────────────────────────────────

def test_invalid_frontmatter_yaml_raises(intents, created):
    path = intents.intent_file(created)
    path.write_text("---\nid: [unclosed\n---\n\n# Intent\n", encoding="utf-8")

    with pytest.raises(intent.IntentParseError):
        intents.read(created)
    with pytest.raises(intent.IntentParseError):
        intents.append_journal(created, "coder", "x")   # не тихий fallback


def test_invalid_section_yaml_raises(intents, created):
    intents.update_section(
        created, "План",
        "```yaml\nplan: [ {id: s1, status: pending\n```",
    )
    with pytest.raises(intent.IntentParseError):
        intents.read(created)


def test_second_yaml_block_warns_and_first_wins(intents, created, caplog):
    intents.update_section(
        created, "План",
        "```yaml\nplan:\n- id: first\n  status: pending\n```\n\n"
        "```yaml\nplan:\n- id: second\n  status: pending\n```",
    )
    with caplog.at_level("WARNING"):
        data = intents.read(created)

    assert [s["id"] for s in data["plan"]] == ["first"]
    assert any("больше одного YAML-блока" in rec.message for rec in caplog.records)


def test_missing_section_block_is_created_for_overrides(intents, created):
    """Секция есть, а YAML-блока нет → создаём, не падаем."""
    intents.update_section(created, "Permissions overrides", "текст без блока")
    intents.update_overrides(created, "tester", "write_any")

    data = intents.read(created)
    assert data["permissions_overrides"] == [{"role": "tester", "restrict": ["write_any"]}]


# ── 10. Интеграционный цикл ───────────────────────────────────────

def test_full_lifecycle_on_real_file(intents):
    iid = "2026-09-25-add-jwt-auth"
    path = intents.create(iid, "Добавить JWT-авторизацию в API", "doorman")
    assert path.is_file()

    intents.update_section(iid, "Цель", "Добавить JWT-авторизацию в API")
    intents.update_section(iid, "DoD", "- [ ] выдача токена\n- [ ] проверка токена")
    intents.update_section(iid, "План", PLAN_YAML)
    intents.update_step(iid, "s1", status="done", report="ADRs готовы")
    intents.append_journal(iid, "code_architect", "схема описана")
    intents.append_vision(iid, "code_architect", "нужен эндпоинт /refresh")
    intents.update_overrides(iid, "coder", "write_any")
    intents.archive(iid)

    data = intents.read(iid)
    assert data["frontmatter"]["status"] == "archived"
    assert data["frontmatter"]["updated"] >= data["frontmatter"]["created"]
    assert "выдача токена" in data["dod"]
    assert any(s["status"] == "done" for s in data["plan"])
    assert "схема описана" in " ".join(data["journal"])
    assert "refresh" in data["visions"]["code_architect"]
    assert data["permissions_overrides"] == [{"role": "coder", "restrict": ["write_any"]}]

    index = (intents.intents_root() / "INDEX.md").read_text(encoding="utf-8")
    rows = [line for line in index.splitlines() if line.startswith(f"| {iid} ")]
    assert len(rows) == 1, f"в INDEX.md должна быть ровно одна строка на intent, есть {len(rows)}"
    assert index.count("| ID |") == 1
