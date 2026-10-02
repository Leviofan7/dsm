"""
Тесты конфигурации агентов: приоритетный список моделей (agent_configs.json v2),
парсинг legacy-формата и метаданные MCP-серверов.

Запуск: cd backend && python -m pytest tests/test_agent_config.py -q
"""
import json
import os
import sys
from pathlib import Path

import pytest

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# Те же env-переменные, что и в test_auth_and_security.py (main.py падает без webhook-секрета).
# Присваиваем напрямую: у контейнера секрет приходит из .env, и setdefault его не перезапишет,
# из-за чего проверка webhook-секрета в других тестах начнёт падать.
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test_webhook_secret_12345"
os.environ["WEB_AUTH_SECRET"] = "test_web_auth_secret_67890"

from fastapi import HTTPException

from agent import mcp_manager
from agent import llm_manager
from agent.llm_manager import (
    ModelEntry,
    ModelRegistry,
    extract_model_candidates,
    get_agent_mode_config,
)
import main as backend_main


# ── agent_configs.json ─────────────────────────────────────────────

def test_extract_model_candidates_new_and_legacy():
    """Новый формат models: [...] и legacy model: "..." поддерживаются одновременно."""
    assert extract_model_candidates({"models": ["deepseek-chat", "gemini-flash"]}) == [
        "deepseek-chat",
        "gemini-flash",
    ]
    assert extract_model_candidates({"model": "deepseek-chat"}) == ["deepseek-chat"]
    # Пустой список побеждает legacy-поле: пользователь явно снял все галочки
    assert extract_model_candidates({"models": [], "model": "deepseek-chat"}) == []
    assert extract_model_candidates({"models": ["", "  "]}) == []
    assert extract_model_candidates(None) == []
    assert extract_model_candidates("not-a-dict") == []


def test_get_agent_mode_config_modes_and_auto_merge():
    """light/heavy берутся из своего режима, auto объединяет heavy+light (heavy в приоритете)."""
    configs = {
        "coder": {
            "light": {"models": ["qwen-coder"], "extra_mcps": ["fs-tools"]},
            "heavy": {"models": ["deepseek-chat"], "extra_mcps": ["deepseek-harness"]},
        }
    }

    assert get_agent_mode_config(configs, "coder", "light") == {
        "models": ["qwen-coder"],
        "extra_mcps": ["fs-tools"],
    }

    auto = get_agent_mode_config(configs, "coder", "auto")
    assert auto["models"] == ["deepseek-chat", "qwen-coder"]
    assert auto["extra_mcps"] == ["deepseek-harness", "fs-tools"]

    # Неизвестный агент не должен ронять оркестратор
    assert get_agent_mode_config(configs, "nope", "auto") == {"models": [], "extra_mcps": []}
    assert get_agent_mode_config(configs, "coder", "light") != {}


# ── resolve_model: приоритет доступности ───────────────────────────

def _make_registry() -> ModelRegistry:
    registry = ModelRegistry()

    def entry(name: str, provider_name: str, provider_type: str, available: bool) -> ModelEntry:
        e = ModelEntry(name, {"provider": provider_name, "model_id": name}, {"type": provider_type})
        e.available = available
        return e

    registry.models = {
        "offline-cloud": entry("offline-cloud", "deepseek", "openai", False),
        "online-cloud": entry("online-cloud", "gemini", "openai", True),
        "local-model": entry("local-model", "ollama", "ollama", True),
    }
    registry.routing = {"coder": ["local-model"], "general": ["local-model"]}
    return registry


def test_resolve_model_uses_first_available_candidate():
    registry = _make_registry()
    model = registry.resolve_model("coder", model_overrides=["offline-cloud", "online-cloud"])
    assert model is not None and model.name == "online-cloud"


def test_resolve_model_falls_back_to_chain_when_candidates_unavailable():
    registry = _make_registry()
    # Первый кандидат недоступен, второй вообще не существует → цепочка маршрутизации
    model = registry.resolve_model("coder", model_overrides=["offline-cloud", "ghost-model"])
    assert model is not None and model.name == "local-model"


def test_resolve_model_supports_legacy_single_override():
    registry = _make_registry()
    model = registry.resolve_model("coder", model_override="offline-cloud")
    assert model is not None and model.name == "local-model"


def test_resolve_model_without_candidates_uses_chain():
    registry = _make_registry()
    model = registry.resolve_model("coder", model_overrides=[])
    assert model is not None and model.name == "local-model"


# ── MCP: метаданные и доступность ──────────────────────────────────

def test_server_meta_uses_real_server_names():
    """Регресс: ключ 'gemini-cli' не совпадал с реальным именем 'gemini-cli-mcp'."""
    gemini = mcp_manager.server_meta("gemini-cli-mcp")
    assert gemini["engine_type"] == "cli_engine"
    assert gemini["required_provider"] == "gemini"
    assert gemini["requires_bin"] == "gemini"

    harness = mcp_manager.server_meta("deepseek-harness")
    assert harness["engine_type"] == "cli_engine"
    assert harness["required_provider"] == "deepseek"

    assert mcp_manager.server_meta("fs-tools")["engine_type"] == "tool"
    assert mcp_manager.server_meta("unknown-server") == mcp_manager.DEFAULT_SERVER_META


def test_known_server_names_matches_files_on_disk():
    names = mcp_manager.known_server_names()
    assert "gemini-cli-mcp" in names
    assert "fs-tools" in names
    assert "web-stealth" in names


def test_get_servers_status_includes_not_started_servers():
    """Раньше список строился по реестру инструментов, и упавшие серверы не были видны."""
    status = {s["name"]: s for s in mcp_manager.MCPManager().get_servers_status()}

    assert "gemini-cli-mcp" in status
    assert status["gemini-cli-mcp"]["engine_type"] == "cli_engine"
    assert status["gemini-cli-mcp"]["active"] is False  # в тестовом процессе не запущен
    assert status["gemini-cli-mcp"]["tools"] == []


# ── Валидация конфига на бэкенде ───────────────────────────────────

def test_normalize_agent_config_converts_legacy_and_rejects_unknown(monkeypatch):
    monkeypatch.setattr(backend_main.llm_manager.registry, "models", {"deepseek-chat": object()})

    # legacy model → models
    normalized = backend_main._normalize_agent_config(
        "coder", {"light": {"model": "deepseek-chat", "extra_mcps": ["fs-tools"]}}
    )
    assert normalized["light"] == {"models": ["deepseek-chat"], "extra_mcps": ["fs-tools"]}

    # неизвестная модель
    with pytest.raises(HTTPException) as err:
        backend_main._normalize_agent_config("coder", {"light": {"models": ["ghost"], "extra_mcps": []}})
    assert err.value.status_code == 400

    # неизвестный MCP-сервер
    with pytest.raises(HTTPException) as err:
        backend_main._normalize_agent_config("coder", {"heavy": {"models": [], "extra_mcps": ["ghost-mcp"]}})
    assert err.value.status_code == 400

    # пустой/битый payload
    with pytest.raises(HTTPException):
        backend_main._normalize_agent_config("coder", {})
    with pytest.raises(HTTPException):
        backend_main._normalize_agent_config("coder", "not-a-dict")


def test_validate_role_id_rejects_garbage():
    assert backend_main._validate_role_id("my_coder-2") == "my_coder-2"
    for bad in ["undefined", "null", "", "with space", "slash/inside"]:
        with pytest.raises(HTTPException) as err:
            backend_main._validate_role_id(bad)
        assert err.value.status_code == 400


def test_protected_roles_cover_runtime_hardcoded_roles():
    """Роли, зашитые в llm_manager/worker, должны быть защищены от удаления."""
    for role_id in ["doorman", "coder", "web_researcher", "meta_analyst", "supervisor_14b"]:
        assert role_id in backend_main.PROTECTED_ROLES


# ── resolve_agent_model: настройки агента важнее цепочки маршрутизации ──

def _entry(name: str, provider_type: str = "ollama", available: bool = True) -> ModelEntry:
    e = ModelEntry(name, {"provider": "ollama" if provider_type == "ollama" else "deepseek", "model_id": name},
                   {"type": provider_type})
    e.available = available
    return e


def _registry_with_doorman_chain() -> ModelRegistry:
    """Цепочка как в models.yaml: первым идёт gemma4-e4b (именно она отвечала вместо настроенной модели)."""
    registry = ModelRegistry()
    registry.models = {
        "gemma4-e4b": _entry("gemma4-e4b"),
        "deepseek-r1:8b": _entry("deepseek-r1:8b"),
    }
    registry.routing = {"doorman": ["gemma4-e4b"]}
    return registry


def _write_configs(tmp_path, payload) -> Path:
    path = tmp_path / "agent_configs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_resolve_agent_model_prefers_agent_settings(tmp_path, monkeypatch):
    """Регресс: швейцар отвечал gemma4-e4b, так как его настройки не читались вообще."""
    monkeypatch.setattr(llm_manager, "AGENT_CONFIGS_PATH", _write_configs(tmp_path, {
        "doorman": {"light": {"models": ["deepseek-r1:8b"], "extra_mcps": []}, "heavy": {"models": [], "extra_mcps": []}},
    }))
    registry = _registry_with_doorman_chain()

    model, conf = llm_manager.resolve_agent_model(registry, "doorman", "light")
    assert model is not None and model.name == "deepseek-r1:8b"
    assert conf["extra_mcps"] == []

    # auto → режим по ROLE_DEFAULTS["doorman"] = light: настройки по-прежнему важнее routing.doorman
    model_auto, _ = llm_manager.resolve_agent_model(registry, "doorman", "auto")
    assert model_auto is not None and model_auto.name == "deepseek-r1:8b"


def test_resolve_agent_model_falls_back_to_routing_chain(tmp_path, monkeypatch):
    """Без настроек агента работает прежнее поведение — цепочка маршрутизации."""
    monkeypatch.setattr(llm_manager, "AGENT_CONFIGS_PATH", _write_configs(tmp_path, {}))

    model, conf = llm_manager.resolve_agent_model(_registry_with_doorman_chain(), "doorman", "light")
    assert model is not None and model.name == "gemma4-e4b"
    assert conf == {}


def test_resolve_agent_model_skips_unavailable_configured_model(tmp_path, monkeypatch):
    """Настроенная модель есть в реестре, но недоступна (нет в Ollama) → цепочка, без падения."""
    monkeypatch.setattr(llm_manager, "AGENT_CONFIGS_PATH", _write_configs(tmp_path, {
        "doorman": {"light": {"models": ["deepseek-r1:8b"], "extra_mcps": []}},
    }))
    registry = _registry_with_doorman_chain()
    registry.models["deepseek-r1:8b"].available = False

    model, _ = llm_manager.resolve_agent_model(registry, "doorman", "light")
    assert model is not None and model.name == "gemma4-e4b"


def test_load_agent_configs_survives_missing_and_broken_files(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_manager, "AGENT_CONFIGS_PATH", tmp_path / "missing.json")
    assert llm_manager.load_agent_configs() == {}

    broken = tmp_path / "broken.json"
    broken.write_text("{ это не json", encoding="utf-8")
    monkeypatch.setattr(llm_manager, "AGENT_CONFIGS_PATH", broken)
    assert llm_manager.load_agent_configs() == {}
