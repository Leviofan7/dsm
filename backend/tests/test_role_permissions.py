"""
Фаза A — permission-envelope ролей: явные поля, канонический порядок, fail-closed.

Покрывает критерии приёмки:
  1 все роли имеют три поля в каноническом порядке после planner
  2 tester.yaml создан с полным промптом и write_scope: none
  3 ROLE_DEFAULTS содержит tester → light
  4 get_role_permissions() возвращает корректные значения
  5 fail-closed: битый YAML / невалидное значение / отсутствие поля → безопасный дефолт + warning
  6 validate_role_permissions() возвращает пустой список для реальных ролей
  7 тест-страж (один тест, не параметризованный)
"""

import logging
import sys
from pathlib import Path

import pytest
import yaml

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from agent.llm_manager import ROLE_DEFAULTS
from services import role_permissions as rp

#: значения, зафиксированные ТЗ Фазы A
EXPECTED = {
    "coder":          {"write_scope": "project", "intent_access": "write_own", "can_create_intent": False},
    "doorman":        {"write_scope": "none",    "intent_access": "write_own", "can_create_intent": True},
    "code_architect": {"write_scope": "none",    "intent_access": "write_own", "can_create_intent": False},
    "supervisor_14b": {"write_scope": "none",    "intent_access": "write_own", "can_create_intent": False},
    "meta_analyst":   {"write_scope": "none",    "intent_access": "write_own", "can_create_intent": False},
    "web_researcher": {"write_scope": "none",    "intent_access": "write_own", "can_create_intent": False},
    "news_extractor": {"write_scope": "none",    "intent_access": "write_own", "can_create_intent": False},
    "tester":         {"write_scope": "none",    "intent_access": "write_own", "can_create_intent": False},
}


@pytest.fixture
def temp_roles(tmp_path, monkeypatch):
    """Изолированный каталог ролей: модуль читает ROLES_DIR при каждом вызове."""
    monkeypatch.setattr(rp, "ROLES_DIR", tmp_path / "roles")
    rp.roles_dir().mkdir(parents=True, exist_ok=True)
    return rp.roles_dir()


def _write_role(root: Path, role: str, body: str) -> None:
    (root / f"{role}.yaml").write_text(body, encoding="utf-8")


# ── 7. Тест-страж (намеренно один и не параметризованный) ─────────

def test_all_roles_have_valid_permissions():
    """
    У каждой роли есть все три поля с валидными значениями.

    Именно этот тест удерживает вариант A («всё явно») от расползания: без него
    явность неизбежно деградирует в «явность у большинства».
    """
    roles = rp.list_all_roles()
    assert roles, f"в {rp.roles_dir()} не найдено ни одной роли"

    for role in roles:
        data = rp.load_role_yaml(role)
        assert isinstance(data, dict) and data, f"[{role}] YAML роли не читается"

        for field in rp.PERMISSION_FIELDS:
            assert field in data, f"[{role}] отсутствует обязательное поле '{field}'"

        assert rp.is_valid_write_scope(data["write_scope"]), f"[{role}] write_scope={data['write_scope']!r}"
        assert rp.is_valid_intent_access(data["intent_access"]), f"[{role}] intent_access={data['intent_access']!r}"
        assert isinstance(data["can_create_intent"], bool), f"[{role}] can_create_intent не bool"

        assert rp.permissions_are_canonical(role), f"[{role}] поля не в каноническом порядке после planner"


# ── 1. Наличие, значения и порядок ────────────────────────────────

def test_permissions_match_spec_table():
    """Критерий 4: get_role_permissions() совпадает с таблицей ТЗ."""
    for role, expected in EXPECTED.items():
        assert rp.get_role_permissions(role) == expected, f"[{role}] не совпало с ТЗ"


def test_write_any_is_not_used_anywhere():
    """`write_any` не вводим: реального сценария перепланирования нет."""
    assert rp.is_valid_intent_access("write_any")  # значение валидно как enum
    for role in rp.list_all_roles():
        assert rp.get_role_permissions(role)["intent_access"] == "write_own", f"[{role}] неожиданный write_any"


def test_only_coder_has_project_scope():
    """`project` — специфика кодера; остальные не пишут в проект."""
    scopes = {role: rp.get_role_permissions(role)["write_scope"] for role in rp.list_all_roles()}
    assert scopes["coder"] == "project"
    assert all(scope == "none" for role, scope in scopes.items() if role != "coder"), scopes


def test_only_doorman_can_create_intent():
    creators = [role for role in rp.list_all_roles() if rp.get_role_permissions(role)["can_create_intent"]]
    assert creators == ["doorman"], creators


def test_permissions_are_after_planner_in_raw_yaml():
    """Порядок в файле, а не только в распарсенном словаре."""
    for role in rp.list_all_roles():
        text = rp.role_path(role).read_text(encoding="utf-8")
        lines = [line.split(":")[0] for line in text.splitlines() if line and not line.startswith((" ", "-", "#"))]
        planner_at = lines.index("planner")
        assert lines[planner_at + 1:planner_at + 4] == list(rp.PERMISSION_FIELDS), f"[{role}] порядок в файле: {lines}"


# ── 2. tester ─────────────────────────────────────────────────────

def test_tester_role_exists_with_full_prompt():
    data = rp.load_role_yaml("tester")
    assert data, "tester.yaml отсутствует или не читается"
    assert data["write_scope"] == "none"
    assert data["planner"] is False
    prompt = data["system_instruction"]
    assert len(prompt) > 300, "промпт tester выглядит заглушкой"
    # Фаза B: промпт переписан на работу через intent.md — маркеры проверяют смысл, а не формулировки
    for marker in ("тест", "intent_append_vision", "intent_append_journal", "intent_update_step", "write_own"):
        assert marker.lower() in prompt.lower(), f"в промпте tester нет смыслового блока '{marker}'"
    assert data["tools"], "у tester должен быть набор инструментов"


# ── 3. ROLE_DEFAULTS ─────────────────────────────────────────────

def test_role_defaults_include_tester_as_light():
    assert ROLE_DEFAULTS["tester"] == "light"
    missing = set(rp.list_all_roles()) - set(ROLE_DEFAULTS)
    assert not missing, f"роли без записи в ROLE_DEFAULTS: {sorted(missing)}"


# ── 5. Fail-closed ────────────────────────────────────────────────

def test_missing_fields_fall_back_to_safe_defaults(temp_roles, caplog):
    _write_role(temp_roles, "old_role", "name: old\nplanner: false\ntools: []\n")

    with caplog.at_level(logging.WARNING):
        perms = rp.get_role_permissions("old_role")

    assert perms == {"write_scope": "none", "intent_access": "write_own", "can_create_intent": False}
    assert any("write_scope отсутствует" in rec.message for rec in caplog.records)


def test_invalid_values_fall_back_and_warn(temp_roles, caplog):
    _write_role(temp_roles, "bad_role", (
        "name: bad\nplanner: false\n"
        "write_scope: any\n"            # 'any' сознательно не существует
        "intent_access: write_all\n"
        "can_create_intent: 'yes'\n"
        "tools: []\n"
    ))

    with caplog.at_level(logging.WARNING):
        perms = rp.get_role_permissions("bad_role")

    assert perms == {"write_scope": "none", "intent_access": "write_own", "can_create_intent": False}
    assert any("write_scope='any' невалидно" in rec.message for rec in caplog.records)
    assert any("intent_access='write_all' невалидно" in rec.message for rec in caplog.records)
    assert any("can_create_intent='yes' невалидно" in rec.message for rec in caplog.records)


def test_broken_yaml_and_missing_file_do_not_raise(temp_roles, caplog):
    _write_role(temp_roles, "broken", "name: [не закрыт\nplanner: true\n")

    with caplog.at_level(logging.WARNING):
        assert rp.get_role_permissions("broken") == {"write_scope": "none", "intent_access": "write_own", "can_create_intent": False}
        assert rp.get_role_permissions("ghost") == {"write_scope": "none", "intent_access": "write_own", "can_create_intent": False}
        assert rp.get_role_permissions("../etc/passwd") == {"write_scope": "none", "intent_access": "write_own", "can_create_intent": False}

    assert any("не смог разобрать" in rec.message for rec in caplog.records)
    assert any("не найден" in rec.message for rec in caplog.records)
    assert any("некорректное имя роли" in rec.message for rec in caplog.records)


def test_workspace_scope_is_accepted():
    assert rp.is_valid_write_scope("workspace:tests/")
    assert rp.is_valid_write_scope("workspace:reports/2026")
    assert not rp.is_valid_write_scope("workspace:")
    assert not rp.is_valid_write_scope("workspace")
    assert not rp.is_valid_write_scope("any")
    assert not rp.is_valid_write_scope(None)


# ── 6. Валидатор ──────────────────────────────────────────────────

def test_validate_role_permissions_clean_for_real_roles():
    assert rp.validate_role_permissions() == []


def test_validate_role_permissions_reports_problems(temp_roles):
    _write_role(temp_roles, "good", "name: g\nplanner: false\nwrite_scope: none\nintent_access: write_own\ncan_create_intent: false\n")
    _write_role(temp_roles, "no_fields", "name: n\nplanner: false\n")
    _write_role(temp_roles, "bad", "name: b\nplanner: false\nwrite_scope: any\nintent_access: nope\ncan_create_intent: 1\n")

    problems = rp.validate_role_permissions(["good", "no_fields", "bad"])

    assert not any(problem.startswith("[good]") for problem in problems)
    assert sum(1 for problem in problems if problem.startswith("[no_fields]")) == 3
    assert sum(1 for problem in problems if problem.startswith("[bad]")) == 3


# ── Ретрофит-примитив ─────────────────────────────────────────────

def test_insert_permission_fields_is_idempotent_and_ordered():
    base = {"id": "x", "name": "X", "planner": True, "tools": ["a"], "system_instruction": "p"}
    perms = {"write_scope": "workspace:tests/", "intent_access": "write_own", "can_create_intent": False}

    once = rp.insert_permission_fields(base, perms)
    twice = rp.insert_permission_fields(dict(once), perms)

    assert list(once.keys()) == ["id", "name", "planner", "write_scope", "intent_access", "can_create_intent", "tools", "system_instruction"]
    assert once == twice
    assert once["write_scope"] == "workspace:tests/"


def test_insert_permission_fields_without_planner():
    """Роль без planner: поля всё равно встают перед tools."""
    result = rp.insert_permission_fields({"name": "X", "tools": ["a"]}, {"write_scope": "none"})
    assert list(result.keys()) == ["name", "write_scope", "intent_access", "can_create_intent", "tools"]


def test_write_role_yaml_uses_ui_canonical_form(temp_roles):
    """Writer даёт ту же форму, что UI: yaml.dump(...) — иначе сохранение из панели шумит диффом."""
    data = rp.insert_permission_fields(
        {"name": "Кодер", "description": "тест", "planner": False, "tools": ["read_file"], "system_instruction": "промпт"},
        {"write_scope": "none"},
    )
    path = rp.write_role_yaml("canon", data)

    raw = path.read_text(encoding="utf-8")
    assert raw == yaml.dump(rp.load_role_yaml("canon"), allow_unicode=True, sort_keys=False)
