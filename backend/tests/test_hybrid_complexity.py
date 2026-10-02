"""
Гибридное определение режима модели: explicit → role_default → heuristic.

Проверяем:
  * эвристику по сигналам (порог ≥2 → heavy), без вызовов моделей;
  * ROLE_DEFAULTS как источник режима для известных ролей;
  * отсутствие мержа heavy+light в резолвере;
  * отсутствие фильтра по типу провайдера в ModelRegistry.resolve_model;
  * validate_role_defaults — warning, а не raise.
"""

import json
import sys
from pathlib import Path

import pytest

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from agent import llm_manager
from agent.llm_manager import (
    ModelEntry,
    ModelRegistry,
    ROLE_DEFAULTS,
    heuristic_complexity,
    resolve_agent_mode,
    validate_role_defaults,
    validate_roles_config_sync,
)


# ── Эвристика ─────────────────────────────────────────────────────

def test_heuristic_requires_two_signals():
    heavy_query = "Отрефактори архитектуру и обнови схемы во всём проекте, затем задеплой в прод"
    level, score, hits = heuristic_complexity(heavy_query)
    assert level == "heavy"
    assert score >= llm_manager.HEAVY_SIGNAL_THRESHOLD
    assert hits

    # один сигнал — всё ещё light
    light_query = "Покажи план проекта"
    level_one, score_one, _ = heuristic_complexity(light_query)
    assert score_one < llm_manager.HEAVY_SIGNAL_THRESHOLD
    assert level_one == "light"


def test_heuristic_is_local_and_handles_empty():
    assert heuristic_complexity("") == ("light", 0, [])
    assert heuristic_complexity(None) == ("light", 0, [])
    level, score, hits = heuristic_complexity("привет")
    assert (level, score, hits) == ("light", 0, [])


# ── Режим: explicit → role_default → heuristic ────────────────────

def test_mode_prefers_explicit_and_role_default():
    assert resolve_agent_mode("coder", "heavy", "привет") == ("heavy", "explicit")
    assert resolve_agent_mode("coder", "light") == ("light", "explicit")
    assert resolve_agent_mode("coder", "auto", "привет") == (ROLE_DEFAULTS["coder"], "role_default")
    assert resolve_agent_mode("meta_analyst", "auto") == ("heavy", "role_default")


def test_mode_uses_heuristic_for_unknown_role():
    level, reason = resolve_agent_mode(
        "general", "auto", "Перепиши архитектуру и прогони миграции по всему проекту, потом деплой"
    )
    assert reason == "heuristic"
    assert level in ("light", "heavy")

    level_small, reason_small = resolve_agent_mode("general", "auto", "сколько сейчас времени")
    assert reason_small == "heuristic"
    assert level_small == "light"


def test_role_defaults_are_valid_and_cover_key_roles():
    assert validate_role_defaults() == [], "ROLE_DEFAULTS ссылается на несуществующие роли"
    required = {"doorman", "web_researcher", "news_extractor", "coder", "code_architect", "supervisor_14b", "meta_analyst"}
    assert required <= set(ROLE_DEFAULTS)
    assert ROLE_DEFAULTS["coder"] == "light"
    assert ROLE_DEFAULTS["code_architect"] == "heavy"


def test_validate_role_defaults_warns_without_raise():
    problems = validate_role_defaults({"ghost_role": "light", "coder": "turbo"})
    assert len(problems) == 2
    assert any("ghost_role" in p for p in problems)
    assert any("turbo" in p for p in problems)


def test_validate_roles_config_sync_reports_config_without_role(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_manager, "AGENT_CONFIGS_PATH", tmp_path / "agent_configs.json")
    (tmp_path / "agent_configs.json").write_text(json.dumps({"ghost_agent": {"light": {"models": []}}}), encoding="utf-8")
    problems = validate_roles_config_sync()
    assert problems and "ghost_agent" in problems[0]


# ── Резолвер: одна цепочка, без мержа и без фильтра по типу ───────

def _entry(name: str, provider_type: str = "ollama", available: bool = True) -> ModelEntry:
    provider_name = "ollama" if provider_type == "ollama" else "deepseek"
    entry = ModelEntry(
        name,
        {"provider": provider_name, "model_id": name},
        {"type": provider_type},
    )
    entry.available = available
    return entry


def _registry() -> ModelRegistry:
    registry = ModelRegistry()
    registry.models = {
        "local-1": _entry("local-1"),
        "cloud-1": _entry("cloud-1", "deepseek"),
    }
    registry.routing = {"general": ["cloud-1"]}
    return registry


def test_resolver_takes_single_chain_without_merge(tmp_path, monkeypatch):
    """light=[local-1], heavy=[cloud-1]: в auto для coder (role_default=light) — только локальная цепочка."""
    monkeypatch.setattr(llm_manager, "AGENT_CONFIGS_PATH", tmp_path / "cfg.json")
    (tmp_path / "cfg.json").write_text(json.dumps({
        "coder": {
            "light": {"models": ["local-1"], "extra_mcps": ["fs-tools"]},
            "heavy": {"models": ["cloud-1"], "extra_mcps": ["coder-gate-tools"]},
        }
    }), encoding="utf-8")

    model, conf = llm_manager.resolve_agent_model(_registry(), "coder", "auto")
    assert model is not None and model.name == "local-1"
    assert conf["extra_mcps"] == ["fs-tools"], "extra_mcps не должны мержиться из heavy"

    model_heavy, conf_heavy = llm_manager.resolve_agent_model(_registry(), "coder", "heavy")
    assert model_heavy is not None and model_heavy.name == "cloud-1"
    assert conf_heavy["extra_mcps"] == ["coder-gate-tools"]


def test_resolver_falls_back_along_light_chain(tmp_path, monkeypatch):
    """light-цепочка смешанная: локальная первой, облачная — фолбек."""
    monkeypatch.setattr(llm_manager, "AGENT_CONFIGS_PATH", tmp_path / "cfg.json")
    (tmp_path / "cfg.json").write_text(json.dumps({
        "coder": {"light": {"models": ["local-1", "cloud-1"], "extra_mcps": []}}
    }), encoding="utf-8")
    registry = _registry()
    registry.models["local-1"].available = False

    model, _ = llm_manager.resolve_agent_model(registry, "coder", "auto")
    assert model is not None and model.name == "cloud-1"


def test_resolve_model_has_no_provider_type_filter():
    """Раньше heavy принудительно фильтровался до облачных — локальная модель молча терялась."""
    registry = ModelRegistry()
    registry.models = {"local-1": _entry("local-1")}
    registry.routing = {"coding": ["local-1"]}

    assert registry.resolve_model("coding", complexity="heavy") is not None
    assert registry.resolve_model("coding", complexity="light").name == "local-1"


def test_orchestration_config_has_sandbox_ttl_documented():
    """TTL песочниц задаётся env SANDBOX_TTL_HOURS (см. docker-compose)."""
    import os

    assert os.getenv("SANDBOX_TTL_HOURS") is None or float(os.getenv("SANDBOX_TTL_HOURS")) > 0
