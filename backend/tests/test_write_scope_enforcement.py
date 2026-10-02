"""
Фаза A.1 — привязка write_scope к фактической выдаче write-тулов.

Критерии приёмки:
  1 effective_write_tools("coder") = 4 write-тула
  2 effective_write_tools("tester") = set()
  3 allowed_privileged_tools("coder") не изменился
  4 positive: write_file в GATE_TOOLS_BY_ROLE у роли с write_scope=none → пусто + warning
  5 positive: write_scope: none у кодера → ноль write-тулов
  6 validate_write_scope_consistency() на текущем конфиге = []
  7 runtime deny: причина + audit в лог и в активный intent
  8 новые тесты + существующие зелёные
"""

import logging
import sys
from pathlib import Path

import pytest
import yaml

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from agent import llm_manager, mcp_manager
from agent.mcp_manager import GATE_TOOLS_BY_ROLE, allowed_privileged_tools
from services import intent as intent_service
from services import role_permissions as rp

EXPECTED_WRITE_TOOLS = {
    "write_file",
    "run_terminal_command",
    "run_claude_coder",
    "request_command_execution",
    "request_diff_apply",
}

#: tools-белый список кодера: write-тулы те же, кроме run_claude_coder,
#: которого у кодера в whitelist нет (он есть в классе write-тулов = 5-й элемент).
EXPECTED_CODER_GATE_TOOLS = frozenset({
    "request_plan_review",
    "request_command_execution",
    "request_diff_apply",
    "write_file",
    "run_terminal_command",
})


# ── 1–2. effective_write_tools ────────────────────────────────────

def test_effective_write_tools_for_project_role():
    assert rp.effective_write_tools("coder") == EXPECTED_WRITE_TOOLS


def test_effective_write_tools_for_none_role():
    for role in ("tester", "doorman", "code_architect", "supervisor_14b", "meta_analyst"):
        assert rp.effective_write_tools(role) == set(), role


def test_effective_write_tools_matches_write_scope_for_every_role():
    """Инвариант: write-тулы есть ровно у ролей с write_scope=project."""
    for role in rp.list_all_roles():
        ws = rp.get_role_permissions(role)["write_scope"]
        expected = EXPECTED_WRITE_TOOLS if ws == "project" else set()
        assert rp.effective_write_tools(role) == expected, f"[{role}] write_scope={ws}"


def test_workspace_scope_is_fail_closed_with_warning(temp_roles, caplog):
    _write_role(temp_roles, "ws_role", (
        "name: ws\nplanner: false\nwrite_scope: workspace:tests/\n"
        "intent_access: write_own\ncan_create_intent: false\ntools: []\n"
    ))
    with caplog.at_level(logging.WARNING):
        assert rp.effective_write_tools("ws_role") == set()
    assert any("enforcement workspace-записи ещё не реализован" in rec.message for rec in caplog.records)


# ── 3. Пересечение whitelist × write_scope ────────────────────────

def test_allowed_privileged_tools_unchanged_for_coder():
    allowed = allowed_privileged_tools("coder")
    assert allowed == frozenset(GATE_TOOLS_BY_ROLE["coder"])
    assert allowed == EXPECTED_CODER_GATE_TOOLS
    assert EXPECTED_CODER_GATE_TOOLS <= EXPECTED_WRITE_TOOLS | {"request_plan_review"}
    assert "request_plan_review" in allowed          # не write-тул, остаётся


def test_supervisor_keeps_only_non_write_gate_tool():
    """write_scope=none → write-тул request_diff_apply не выдаётся, request_plan_review остаётся."""
    allowed = allowed_privileged_tools("supervisor_14b")
    assert "request_plan_review" in allowed
    assert "request_diff_apply" not in allowed
    assert allowed & EXPECTED_WRITE_TOOLS == set()


def test_unknown_role_gets_nothing():
    assert allowed_privileged_tools("ghost_role") == frozenset()
    assert allowed_privileged_tools(None) == frozenset()


# ── 4. Positive: дыра закрыта (whitelist расширили, права не дали) ─

def test_whitelist_expansion_does_not_grant_write_tools(monkeypatch, caplog):
    """
    Fail-open дыра: кто-то дописал write_file в whitelist роли с write_scope=none.
    Ожидаем пусто + warning, а не выдачу инструмента.
    """
    monkeypatch.setitem(GATE_TOOLS_BY_ROLE, "tester", frozenset({"write_file"}))

    with caplog.at_level(logging.WARNING):
        allowed = allowed_privileged_tools("tester")

    assert allowed == frozenset()
    assert any("write-тулы вырезаны по write_scope" in rec.message for rec in caplog.records)


# ── 5. Positive: write_scope: none у кодера обнуляет права ────────

def test_coder_with_none_scope_loses_all_write_tools(temp_roles, monkeypatch, caplog):
    _write_role(temp_roles, "coder", (
        "name: Кодер\nplanner: false\nwrite_scope: none\n"
        "intent_access: write_own\ncan_create_intent: false\n"
        "tools: [write_file, run_terminal_command]\n"
    ))
    monkeypatch.setattr(rp, "ROLES_DIR", temp_roles)

    with caplog.at_level(logging.WARNING):
        allowed = allowed_privileged_tools("coder")

    assert allowed & EXPECTED_WRITE_TOOLS == set()
    assert "request_plan_review" in allowed          # не write-тул остаётся
    assert rp.effective_write_tools("coder") == set()


# ── 6. Валидатор согласованности ──────────────────────────────────

def test_validate_write_scope_consistency_clean_for_real_config():
    assert rp.validate_write_scope_consistency(GATE_TOOLS_BY_ROLE) == []


def test_validate_write_scope_consistency_reports_fail_open(temp_roles, monkeypatch):
    _write_role(temp_roles, "sneaky", (
        "name: sneaky\nplanner: false\nwrite_scope: none\n"
        "intent_access: write_own\ncan_create_intent: false\n"
        "tools: [write_file, read_file]\n"
    ))
    _write_role(temp_roles, "silent", (
        "name: silent\nplanner: false\nwrite_scope: project\n"
        "intent_access: write_own\ncan_create_intent: false\ntools: []\n"
    ))
    monkeypatch.setattr(rp, "ROLES_DIR", temp_roles)

    problems = rp.validate_write_scope_consistency({"sneaky": {"write_file"}})

    assert any("sneaky" in p and "в tools есть write-тулы" in p for p in problems)
    assert any("sneaky" in p and "GATE_TOOLS_BY_ROLE" in p for p in problems)
    assert any("silent" in p and "инструментов нет" in p for p in problems)


# ── 7. Runtime deny + audit ──────────────────────────────────────

def test_write_denial_reason():
    assert rp.write_denial_reason("tester", "write_file") == "role=tester tool=write_file write_scope=none"
    assert rp.write_denial_reason("coder", "write_file") is None
    assert rp.write_denial_reason("tester", "read_file") is None      # не write-тул
    assert rp.write_denial_reason("supervisor_14b", "request_diff_apply") is not None


def test_deny_is_audited_into_single_active_intent(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(intent_service, "INTENTS_DIR", tmp_path / "intents")
    intent_service.create("2026-09-25-deny-probe", "Проба deny", "doorman")

    manager = llm_manager.LLMManager()
    with caplog.at_level(logging.WARNING):
        reason = manager._write_denial("tester", "write_file")

    assert reason == "role=tester tool=write_file write_scope=none"
    assert any("DENY role=tester tool=write_file" in rec.message for rec in caplog.records)

    journal = intent_service.read("2026-09-25-deny-probe")["journal"]
    assert any("DENY role=tester tool=write_file write_scope=none" in entry for entry in journal)


def test_deny_without_intents_only_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(intent_service, "INTENTS_DIR", tmp_path / "no-intents-yet")
    manager = llm_manager.LLMManager()

    assert manager._write_denial("tester", "run_terminal_command") is not None
    assert not (tmp_path / "no-intents-yet").exists(), "deny не должен создавать каталог intents"


def test_allowed_write_scope_is_not_audited(tmp_path, monkeypatch):
    monkeypatch.setattr(intent_service, "INTENTS_DIR", tmp_path / "intents")
    intent_service.create("2026-09-25-allow-probe", "Проба allow", "doorman")

    manager = llm_manager.LLMManager()
    assert manager._write_denial("coder", "write_file") is None
    journal = intent_service.read("2026-09-25-allow-probe")["journal"]
    assert not any("DENY" in entry for entry in journal)


# ── Вспомогательное ───────────────────────────────────────────────

@pytest.fixture
def temp_roles(tmp_path, monkeypatch):
    root = tmp_path / "roles"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rp, "ROLES_DIR", root)
    return root


def _write_role(root: Path, role: str, body: str) -> None:
    (root / f"{role}.yaml").write_text(body, encoding="utf-8")


def test_roles_yaml_keeps_canonical_permissions_after_edit():
    """Регресс: правка supervisor_14b не сломала канонический вид файла."""
    for role in rp.list_all_roles():
        data = rp.load_role_yaml(role)
        keys = list(data.keys())
        planner_at = keys.index("planner")
        assert keys[planner_at + 1:planner_at + 4] == list(rp.PERMISSION_FIELDS), role
        raw = rp.role_path(role).read_text(encoding="utf-8")
        assert raw == yaml.dump(data, allow_unicode=True, sort_keys=False), f"[{role}] файл не канонический"
