"""
4.3 — резолвер режима (auto/light/heavy) подключён к реальному исполнению ролей.

Проблема, которую закрывает файл: резолвер был готов, но пути исполнения обходили его
через `registry.resolve_model(role)`. Из-за этого для ролей, назначенных планировщиком
(подзадачи), и для Telegram-пути:
  * настройки роли из config/agent_configs.json не применялись вовсе;
  * ROLE_DEFAULTS/heuristic не влияли на выбор режима;
  * роль оставалась без своих `extra_mcps` (например web_researcher — без web-stealth).

Критерии:
  1 resolve_agent_decision отдаёт решение целиком: model, conf, mode, reason
  2 resolve_agent_model остаётся 2-элементной обёрткой (совместимость)
  3 _resolve_role_target: режим по роли + серверы роли (extra_mcps выбранного режима)
  4 серверы не мержатся из чужого режима и дедуплицируются
  5 каждый путь исполнения зовёт резолвер, а не registry.resolve_model(role)
  6 пустая complexity от швейцара = auto, а не молчаливый heavy
  7 нет модели по настройкам роли → откат на модель родителя (не падение)
  8 тесты-стражи от повторного обхода резолвера
"""

import inspect
import json
import re
import sys
from pathlib import Path

import pytest

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from agent import llm_manager
from agent.llm_manager import (
    ModelEntry,
    ModelRegistry,
    resolve_agent_decision,
    resolve_agent_model,
)

AGENT_ID = "web_researcher"


# ── Хелперы (в стиле test_hybrid_complexity) ──────────────────────

def _entry(name: str, provider_type: str = "ollama", available: bool = True, tools: bool = True) -> ModelEntry:
    provider_name = "ollama" if provider_type == "ollama" else "deepseek"
    entry = ModelEntry(name, {"provider": provider_name, "model_id": name}, {"type": provider_type})
    entry.available = available
    entry.supports_tools = tools
    return entry


def _registry() -> ModelRegistry:
    registry = ModelRegistry()
    registry.models = {
        "local-1": _entry("local-1"),
        "cloud-1": _entry("cloud-1", "deepseek"),
    }
    registry.routing = {"general": ["cloud-1"]}
    return registry


@pytest.fixture
def configs(tmp_path, monkeypatch):
    """Свои agent_configs.json: роль с разными цепочками в light/heavy."""
    monkeypatch.setattr(llm_manager, "AGENT_CONFIGS_PATH", tmp_path / "cfg.json")
    payload = {
        AGENT_ID: {
            "light": {"models": ["local-1"], "extra_mcps": ["web-stealth"]},
            "heavy": {"models": ["cloud-1"], "extra_mcps": ["gemini-cli-mcp", "web-stealth"]},
        }
    }
    (tmp_path / "cfg.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


@pytest.fixture
def manager(monkeypatch):
    """LLMManager без сети и MCP: только реестр и менеджер серверов-заглушка."""
    mgr = llm_manager.LLMManager.__new__(llm_manager.LLMManager)
    mgr.registry = _registry()
    mgr.mcp = type("FakeMCP", (), {"is_started": False})()
    return mgr


# ── 1–2. Контракт резолвера ───────────────────────────────────────

def test_decision_returns_mode_and_reason(configs):
    model, conf, mode, reason = resolve_agent_decision(_registry(), AGENT_ID, "auto")
    assert model is not None and model.name == "local-1"
    assert mode == "light" and reason == "role_default"
    assert conf["extra_mcps"] == ["web-stealth"]


def test_decision_explicit_and_heuristic_paths(configs):
    _model, _conf, mode, reason = resolve_agent_decision(_registry(), AGENT_ID, "heavy")
    assert (mode, reason) == ("heavy", "explicit")

    unknown = {"unknown_role": {"light": {"models": ["local-1"]}}}
    (_m, _c, mode, reason) = resolve_agent_decision(_registry(), "unknown_role", "auto", query="который час")
    assert mode == "light" and reason == "heuristic"

    (_m, _c, mode, reason) = resolve_agent_decision(
        _registry(), "no_such_role", "auto",
        query="спроектируй архитектуру и проанализируй несколько сценариев рефакторинга ядра",
    )
    assert mode == "heavy" and reason == "heuristic"


def test_resolve_agent_model_is_two_element_wrapper(configs):
    result = resolve_agent_model(_registry(), AGENT_ID, "auto")
    assert isinstance(result, tuple) and len(result) == 2
    model, conf = result
    assert model.name == "local-1" and conf["extra_mcps"] == ["web-stealth"]


# ── 3–4. Роль получает свои серверы и режим ───────────────────────

def test_role_target_uses_role_mode_and_own_mcps(manager, configs, monkeypatch):
    monkeypatch.setattr(manager, "_resolve_allowed_servers", lambda servers, model=None: list(servers or []))

    model, conf, servers, mode, reason = manager._resolve_role_target(
        AGENT_ID, "найди цену на сайте", base_servers=["workspace", "fs-tools"]
    )
    assert model.name == "local-1", "режим роли (light) должен применяться, а не routing.general"
    assert mode == "light" and reason == "role_default"
    assert servers == ["workspace", "fs-tools", "web-stealth"], "ножи роли (extra_mcps) должны добавиться"


def test_role_target_does_not_merge_other_mode_mcps(manager, configs, monkeypatch):
    monkeypatch.setattr(manager, "_resolve_allowed_servers", lambda servers, model=None: list(servers or []))

    _model, _conf, servers, mode, _reason = manager._resolve_role_target(AGENT_ID, "задача")
    assert mode == "light"
    assert "gemini-cli-mcp" not in servers, "extra_mcps heavy не должны попадать в light-режим"


def test_role_target_dedups_servers(manager, configs, monkeypatch):
    monkeypatch.setattr(manager, "_resolve_allowed_servers", lambda servers, model=None: list(servers or []))

    _m, _c, servers, _mode, _reason = manager._resolve_role_target(
        AGENT_ID, "задача", base_servers=["web-stealth", "web-stealth", "workspace"]
    )
    assert servers == ["web-stealth", "workspace"]


def test_role_target_none_base_servers_means_all(manager, configs, monkeypatch):
    """execute() (Telegram) сохраняет прежнюю политику «все серверы»."""
    seen = {}

    def fake_resolve(servers, model=None):
        seen["servers"] = servers
        return ["all"]

    monkeypatch.setattr(manager, "_resolve_allowed_servers", fake_resolve)
    _m, _c, servers, _mode, _reason = manager._resolve_role_target(AGENT_ID, "задача")
    assert seen["servers"] is None, "None → legacy-путь «все серверы»"
    assert servers == ["all"]


# ── 5–8. Стражи от повторного обхода резолвера ────────────────────

def test_no_execution_path_bypasses_resolver():
    """
    Страж: ни один путь исполнения роли не должен звать registry.resolve_model(<роль>) напрямую.
    Именно этот вызов (в цикле подзадач) отключал настройки роли и ROLE_DEFAULTS.
    """
    source = Path(llm_manager.__file__).read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in source.splitlines()
        if re.search(r"self\.registry\.resolve_model\((target_role|task_type)\)", line)
    ]
    assert offenders == [], f"обход резолвера вернулся: {offenders}"


def test_role_paths_call_role_target_helper():
    """Оба пути (подзадачи и Telegram) обязаны идти через _resolve_role_target."""
    stream_src = inspect.getsource(llm_manager.LLMManager.execute_stream)
    execute_src = inspect.getsource(llm_manager.LLMManager.execute)

    assert "_resolve_role_target(" in stream_src, "цикл подзадач больше не использует резолвер"
    assert "resolve_agent_decision(" in execute_src, "execute() больше не использует резолвер"


def test_doorman_delegation_defaults_to_auto():
    """Пустая complexity от швейцара — это auto, а не молчаливый heavy."""
    source = inspect.getsource(llm_manager.LLMManager.execute_stream)
    assert 'delegation_data.get("complexity") or "auto"' in source
    assert 'delegation_data.get("complexity", "heavy")' not in source


def test_step_mode_is_reported_to_ui():
    """Режим шага должен быть виден в событии стрима (иначе нечем проверять живьём)."""
    source = inspect.getsource(llm_manager.LLMManager.execute_stream)
    assert "(Агент: {target_role}, режим: {st_mode})" in source


def test_missing_role_model_falls_back_to_parent(manager, monkeypatch):
    """Нет модели по настройкам роли → берём модель родителя, а не падаем."""
    monkeypatch.setattr(manager, "_resolve_allowed_servers", lambda servers, model=None: list(servers or []))
    manager.registry.models = {"cloud-1": _entry("cloud-1", "deepseek")}
    manager.registry.routing = {}

    model, _conf, servers, mode, reason = manager._resolve_role_target(
        "unknown_role", "задача", base_servers=["workspace"]
    )
    assert model is None, "резолвер честно вернул None — решение об откате принимает вызывающий"
    assert servers == ["workspace"]
