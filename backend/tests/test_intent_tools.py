"""
Фаза B — MCP-сервер `intent-tools`: 10 инструментов, роль через _meta, права по intent_access.

Критерии приёмки:
  1 сервер `intent-tools` регистрирует ровно 10 инструментов
  2 служебный канал роли не виден модели: в схемах нет `_role`/`ctx`/`meta`
  3 роль инъектится кодом (_meta), а не приходит от LLM: подделка в аргументах не работает
  4 нет роли → отказ (fail-closed), неизвестная роль → отказ на запись
  5 intent_create доступен только ролям с can_create_intent=true (сейчас doorman)
  6 write_own: чтение/журнал/видение/свой шаг — можно; чужой шаг и структурные операции — нельзя
  7 владельца шага нельзя переназначить через intent_update_step
  8 каждый отказ: warning в лог + строка DENY в журнал intent
  9 оркестратор передаёт роль только для intent_-инструментов
 10 mcp_manager.call_tool прокидывает meta в MCP-сессию
 11 tester получает intent-тулы по правам роли, структурные — нет
"""

import json
import sys
import inspect
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from agent import llm_manager, mcp_manager
from agent.mcp_manager import MCPManager, PRIVILEGED_TOOLS, GATE_TOOLS_BY_ROLE
from mcp_servers import intent_tools
from services import intent as intent_service
from services import role_permissions as rp

KEY = rp.CALLER_ROLE_META_KEY
INTENT_ID = "2026-09-25-add-jwt-auth"
OTHER_ID = "2026-09-25-second-intent"

TOOL_NAMES = (
    "intent_create", "intent_read", "intent_list",
    "intent_append_journal", "intent_append_vision", "intent_update_step",
    "intent_update_section", "intent_update_status", "intent_update_overrides",
    "intent_archive",
)

PLAN_YAML = (
    "```yaml\n"
    "plan:\n"
    "  - id: s1\n"
    "    role: tester\n"
    "    status: pending\n"
    "  - id: s2\n"
    "    role: coder\n"
    "    status: pending\n"
    "```"
)


# ── Фикстуры и хелперы ────────────────────────────────────────────

@pytest.fixture
def intents(tmp_path, monkeypatch):
    """Изолированный intents/: модуль читает INTENTS_DIR при каждом вызове."""
    monkeypatch.setattr(intent_service, "INTENTS_DIR", tmp_path / "intents")
    return intent_service


class _PydanticLikeMeta:
    """Имитация pydantic-модели Meta с extra='allow' (реальный MCP кладёт meta так)."""

    def __init__(self, data: dict):
        self.model_extra = dict(data)


def _ctx_with(meta):
    """Контекст FastMCP: нужен только request_context.meta."""
    return SimpleNamespace(request_context=SimpleNamespace(meta=meta))


def ctx(role: str):
    return _ctx_with({KEY: role})


def ctx_attr(role: str):
    return _ctx_with(_PydanticLikeMeta({KEY: role}))


def ctx_raw(meta):
    return _ctx_with(meta)


def denied(answer: str) -> dict:
    payload = json.loads(answer)
    assert payload.get("denied") is True, payload
    return payload


def ok(answer: str) -> dict:
    payload = json.loads(answer)
    assert "denied" not in payload or payload.get("denied") is False, payload
    return payload


def call(name: str, as_role=None, _ctx=None, **kwargs):
    """Вызов инструмента как функции (FastMCP отдаёт исходную функцию)."""
    fn = getattr(intent_tools, name)
    return fn(ctx=ctx(as_role) if _ctx is None else _ctx, **kwargs)


@pytest.fixture
def created(intents):
    """intent, созданный через сам MCP-инструмент от имени doorman."""
    assert ok(call("intent_create", "doorman", intent_id=INTENT_ID, title="JWT"))["created"]
    return INTENT_ID


# ── 1–2. Регистрация и схемы ──────────────────────────────────────

def test_server_registers_exactly_ten_tools():
    import asyncio

    tools = asyncio.run(intent_tools.mcp.list_tools())
    assert sorted(t.name for t in tools) == sorted(TOOL_NAMES)


def test_tool_schemas_hide_service_channel():
    """
    Служебных полей в схеме нет: модель их не видит и не может сгенерировать.
    Это и есть смысл _meta вместо скрытого аргумента.
    """
    import asyncio

    tools = asyncio.run(intent_tools.mcp.list_tools())
    for tool in tools:
        props = (tool.inputSchema or {}).get("properties", {})
        for hidden in ("_role", "ctx", "context", "meta", "_meta"):
            assert hidden not in props, f"[{tool.name}] в схеме протёк {hidden}"
        assert not [p for p in props if p.startswith("_")], f"[{tool.name}] приватный параметр в схеме"


def test_roles_only_change_arguments_not_schema():
    """Права ролей не видны модели: схема одного инструмента одинакова для всех."""
    import asyncio

    tools = {t.name: t.inputSchema for t in asyncio.run(intent_tools.mcp.list_tools())}
    assert sorted(tools["intent_update_step"]["properties"]) == ["fields", "intent_id", "step_id"]
    assert sorted(tools["intent_create"]["properties"]) == ["intent_id", "title"]


# ── 3. Роль приходит служебным каналом ────────────────────────────

def test_role_is_read_from_meta(intents, created):
    assert ok(call("intent_read", "tester", intent_id=created))["frontmatter"]["id"] == created
    assert ok(call("intent_list", "tester"))["intents"][0]["id"] == created


def test_role_from_pydantic_like_meta(intents, created):
    """Реальный MCP отдаёт meta моделью, а не dict — читается и такой вариант."""
    assert ok(call("intent_read", _ctx=ctx_attr("tester"), intent_id=created))["frontmatter"]["id"] == created


@pytest.mark.parametrize("bad_ctx", [
    ctx_raw({}),
    ctx_raw(None),
    _ctx_with({KEY: "   "}),
    _ctx_with({"role": "doorman"}),        # чужой ключ
    SimpleNamespace(request_context=SimpleNamespace()),  # нет meta вообще
    SimpleNamespace(),                      # нет request_context
])
def test_missing_role_is_denied_fail_closed(intents, bad_ctx):
    for name, kwargs in (("intent_read", {"intent_id": INTENT_ID}),
                         ("intent_list", {}),
                         ("intent_create", {"intent_id": INTENT_ID, "title": "x"})):
        assert denied(call(name, _ctx=bad_ctx, **kwargs))["role"] == ""


def test_tools_have_no_caller_role_parameter():
    """
    Роль не может прийти аргументом: её просто нет в сигнатурах.
    Единственный параметр `role` — цель у intent_update_overrides, не вызывающий.
    """
    for name in TOOL_NAMES:
        params = set(inspect.signature(getattr(intent_tools, name)).parameters)
        assert "_role" not in params and "created_by" not in params, name
        assert ("role" in params) == (name == "intent_update_overrides"), name


def test_meta_decides_role_not_arguments(intents, created):
    """Решение о роли берётся только из _meta: can_create_intent смотрит на meta, не на аргументы."""
    # supervisor_14b не умеет создавать — отказ, хотя LLM мог «попросить» doorman
    assert denied(call("intent_create", "supervisor_14b", intent_id=OTHER_ID, title="Подделка"))["role"] == "supervisor_14b"
    # doorman — создаёт, и создатель берётся из meta, а не из аргументов
    payload = ok(call("intent_create", "doorman", intent_id=OTHER_ID, title="Подделка"))
    assert payload["created_by"] == "doorman"
    assert ok(call("intent_read", "doorman", intent_id=OTHER_ID))["frontmatter"]["created_by"] == "doorman"


def test_unknown_role_is_fail_closed(intents, created):
    """Неизвестная роль: чтение по fail-closed write_own, структурная запись и чужой шаг — отказ."""
    assert ok(call("intent_read", "ghost", intent_id=created))["frontmatter"]["id"] == created
    assert denied(call("intent_update_status", "ghost", intent_id=created, new_status="done"))["role"] == "ghost"

    intent_service.update_section(created, intent_service.SECTION_PLAN, PLAN_YAML)
    payload = denied(call("intent_update_step", "ghost", intent_id=created, step_id="s1", fields={"status": "done"}))
    assert "принадлежит роли tester" in payload["error"]


# ── 4. Право создавать intent ─────────────────────────────────────

def test_create_only_for_roles_with_can_create_intent(intents):
    allowed = {r for r in rp.list_all_roles() if rp.get_role_permissions(r)["can_create_intent"]}
    assert allowed == {"doorman"}, f"ТЗ ожидает единственного создателя, получено {allowed}"

    for role in sorted(rp.list_all_roles()):
        answer = call("intent_create", role, intent_id=INTENT_ID, title="x")
        if role in allowed:
            assert ok(answer)["created"] is True, role
        else:
            assert denied(answer)["role"] == role, role


def test_duplicate_create_is_rejected(intents, created):
    payload = ok(call("intent_create", "doorman", intent_id=created, title="dup"))
    assert "error" in payload and "существует" in payload["error"].lower()


# ── 5. Матрица write_own ──────────────────────────────────────────

def test_write_own_allows_read_journal_vision(intents, created):
    assert ok(call("intent_read", "tester", intent_id=created))["frontmatter"]["id"] == created
    assert ok(call("intent_append_journal", "tester", intent_id=created, text="сборка зелёная"))["role"] == "tester"
    assert ok(call("intent_append_vision", "tester", intent_id=created, text="нужен линтер"))["role"] == "tester"

    doc = ok(call("intent_read", "tester", intent_id=created))
    assert any("сборка зелёная" in str(line) for line in doc["journal"])
    assert "нужен линтер" in doc["visions"]["tester"]


def test_write_own_allows_only_own_step(intents, created):
    intent_service.update_section(created, intent_service.SECTION_PLAN, PLAN_YAML)
    doc = ok(call("intent_read", "tester", intent_id=created))
    assert [s["id"] for s in doc["plan"]] == ["s1", "s2"]

    assert ok(call("intent_update_step", "tester", intent_id=created, step_id="s1", fields={"status": "done"}))["updated"]
    payload = denied(call("intent_update_step", "tester", intent_id=created, step_id="s2", fields={"status": "done"}))
    assert "принадлежит роли coder" in payload["error"]


def test_step_owner_cannot_be_reassigned(intents, created):
    """Переназначение владельца шага — структурная операция, а не запись шага."""
    intent_service.update_section(created, intent_service.SECTION_PLAN, PLAN_YAML)
    payload = denied(call("intent_update_step", "tester", intent_id=created, step_id="s1", fields={"role": "coder"}))
    assert "попытка сменить владельца шага" in payload["error"]

    doc = ok(call("intent_read", "doorman", intent_id=created))
    assert [s["role"] for s in doc["plan"]] == ["tester", "coder"]


def test_structural_tools_denied_for_write_own(intents, created):
    structural = (
        ("intent_update_section", {"section_name": intent_service.SECTION_GOAL, "new_text": "x"}),
        ("intent_update_status", {"new_status": "done"}),
        ("intent_update_overrides", {"role": "coder", "restrict": "write"}),
    )
    for role in sorted(rp.list_all_roles()):
        if rp.get_role_permissions(role)["intent_access"] != "write_own":
            continue
        for name, kwargs in structural:
            payload = denied(call(name, as_role=role, intent_id=created, **kwargs))
            assert "intent_access=write_own" in payload["error"], (role, name)
        assert denied(call("intent_archive", role, intent_id=created))["role"] == role


def test_nobody_has_write_any_so_plan_is_service_owned(intents, created):
    """
    Документируем фактическое состояние: `write_any` пока ни у кого нет, поэтому
    структуру плана и статусы пишет сервис/оператор, а не роль через MCP.
    Тест упадёт, когда появится первая роль с write_any — тогда его надо осознанно обновить.
    """
    write_any = [r for r in rp.list_all_roles() if rp.get_role_permissions(r)["intent_access"] == "write_any"]
    assert write_any == [], f"появилась роль с write_any: {write_any} — обнови ожидания по плану"


# ── 6. Аудит отказов ──────────────────────────────────────────────

def test_deny_is_recorded_in_journal_and_log(intents, created, caplog):
    with caplog.at_level(logging.WARNING, logger="contextus.intent_tools"):
        denied(call("intent_update_status", "coder", intent_id=created, new_status="done"))

    assert any("DENY" in r.message for r in caplog.records), [r.message for r in caplog.records]
    doc = ok(call("intent_read", "doorman", intent_id=created))
    assert any("DENY role=coder" in str(line) for line in doc["journal"])


def test_create_deny_has_no_journal_target(intents, caplog):
    """У отказа на создание нет intent'а — писать в журнал некуда, и это не ошибка."""
    with caplog.at_level(logging.WARNING, logger="contextus.intent_tools"):
        assert denied(call("intent_create", "coder", intent_id=INTENT_ID, title="x"))["role"] == "coder"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# ── 7. Сторона оркестратора ───────────────────────────────────────

def test_role_meta_is_set_only_for_intent_tools():
    assert llm_manager.LLMManager._role_meta(None, "intent_read", "tester") == {KEY: "tester"}
    assert llm_manager.LLMManager._role_meta(None, "intent_create", " doorman ") == {KEY: "doorman"}
    for tool in ("write_file", "run_terminal_command", "request_diff_apply", "search_knowledge_base"):
        assert llm_manager.LLMManager._role_meta(None, tool, "coder") is None


def test_call_tool_passes_meta_to_session():
    class _Block:
        text = "ok"

    class _Result:
        content = [_Block()]

    class _FakeSession:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, arguments=None, meta=None):
            self.calls.append((name, arguments, meta))
            return _Result()

    import asyncio

    manager = MCPManager()
    fake = _FakeSession()
    manager._tool_registry = {"intent_read": {"server": "intent-tools", "description": "", "input_schema": {}}}
    manager._sessions = {"intent-tools": fake}

    assert asyncio.run(manager.call_tool("intent_read", {"intent_id": INTENT_ID}, meta={KEY: "tester"})) == "ok"
    assert asyncio.run(manager.call_tool("intent_read", {"intent_id": INTENT_ID})) == "ok"
    assert fake.calls[0] == ("intent_read", {"intent_id": INTENT_ID}, {KEY: "tester"})
    assert fake.calls[1][2] is None


def test_call_tool_wrapper_adds_meta_only_for_intent_tools():
    """Роль добавляется только intent-тулам; остальные идут без лишнего kwarg (совместимость)."""

    class _FakeMCP:
        def __init__(self):
            self.calls = []

        async def call_tool(self, tool_name, arguments=None, meta=None):
            self.calls.append((tool_name, meta))
            return "ok"

    import asyncio

    manager = llm_manager.LLMManager.__new__(llm_manager.LLMManager)
    manager.mcp = _FakeMCP()

    assert asyncio.run(llm_manager.LLMManager._call_tool(manager, "intent_read", {"intent_id": INTENT_ID}, "tester")) == "ok"
    assert asyncio.run(llm_manager.LLMManager._call_tool(manager, "read_file", {"relative_path": "a.py"}, "coder")) == "ok"
    assert manager.mcp.calls == [("intent_read", {KEY: "tester"}), ("read_file", None)]


def test_intent_tools_are_not_privileged():
    for name in TOOL_NAMES:
        assert name not in PRIVILEGED_TOOLS
        for role, tools in GATE_TOOLS_BY_ROLE.items():
            assert name not in tools, (role, name)


def test_filter_intent_tools_by_role():
    model = SimpleNamespace(provider_type="openai")
    tools = [
        {"type": "function", "function": {"name": "read_file", "parameters": {}}},
        {"type": "function", "function": {"name": "intent_read", "parameters": {}}},
        {"type": "function", "function": {"name": "intent_update_section", "parameters": {}}},
        {"type": "function", "function": {"name": "intent_archive", "parameters": {}}},
    ]
    filtered = llm_manager.LLMManager._filter_intent_tools(None, tools, model, "tester")
    names = [t["function"]["name"] for t in filtered]

    assert "intent_read" in names
    assert "read_file" in names
    assert "intent_update_section" not in names
    assert "intent_archive" not in names


def test_tester_role_prompt_and_tools_match_phase_b():
    data = rp.load_role_yaml("tester")
    assert data["intent_access"] == "write_own"
    assert rp.allowed_intent_tools("tester") == set(rp.INTENT_OWN_TOOLS | rp.INTENT_READ_TOOLS)

    prompt = data["system_instruction"]
    assert "intent_append_vision" in prompt and "intent_append_journal" in prompt
    assert "intent_update_step" in prompt
    assert "write_own" in prompt

    for tool in ("intent_read", "intent_list", "intent_append_vision", "intent_append_journal", "intent_update_step"):
        assert tool in data["tools"], tool


# ── 8. Сквозной сценарий по ролям ─────────────────────────────────

def test_end_to_end_doorman_tester_coder(intents):
    assert ok(call("intent_create", "doorman", intent_id=INTENT_ID, title="JWT"))["created"]
    intent_service.update_section(INTENT_ID, intent_service.SECTION_PLAN, PLAN_YAML)

    # tester: своё видение + журнал + свой шаг
    assert ok(call("intent_append_vision", "tester", intent_id=INTENT_ID, text="проверить 401"))["appended"]
    assert ok(call("intent_append_journal", "tester", intent_id=INTENT_ID, text="тест упал"))["role"] == "tester"
    assert ok(call("intent_update_step", "tester", intent_id=INTENT_ID, step_id="s1", fields={"status": "in_progress"}))["updated"]

    # coder: читает, пишет в свой шаг, но не трогает чужой и не меняет план
    assert ok(call("intent_read", "coder", intent_id=INTENT_ID))["frontmatter"]["title"] == "JWT"
    assert ok(call("intent_update_step", "coder", intent_id=INTENT_ID, step_id="s2", fields={"status": "done"}))["updated"]
    assert denied(call("intent_update_step", "coder", intent_id=INTENT_ID, step_id="s1", fields={"status": "pending"}))["role"] == "coder"
    assert denied(call("intent_update_section", "coder", intent_id=INTENT_ID,
                       section_name=intent_service.SECTION_PLAN, new_text=PLAN_YAML))["role"] == "coder"

    doc = ok(call("intent_read", "doorman", intent_id=INTENT_ID))
    assert {s["id"]: s["status"] for s in doc["plan"]} == {"s1": "in_progress", "s2": "done"}
    assert doc["frontmatter"]["status"] == "active"
