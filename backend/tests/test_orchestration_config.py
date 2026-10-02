"""
Оркестрация: дедуп agent-конфига и чистота config/agent_configs.json (ТЗ 4.9).
"""

import json
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from agent.llm_manager import get_agent_mode_config, load_agent_configs


def test_auto_mode_dedupes_models_and_mcps():
    """heavy+light часто дублируют модель/сервер — в auto дублей быть не должно."""
    configs = {
        "coder": {
            "heavy": {"models": ["deepseek-chat", "qwen-coder"], "extra_mcps": ["deepseek-harness", "coder-gate-tools"]},
            "light": {"models": ["qwen-coder"], "extra_mcps": ["fs-tools", "deepseek-harness"]},
        }
    }
    merged = get_agent_mode_config(configs, "coder", "auto")

    assert merged["models"] == ["deepseek-chat", "qwen-coder"], merged
    assert merged["extra_mcps"] == ["deepseek-harness", "coder-gate-tools", "fs-tools"], merged
    assert len(merged["extra_mcps"]) == len(set(merged["extra_mcps"]))


def test_light_mode_returns_own_config_only():
    configs = {"coder": {"light": {"models": ["qwen-coder"], "extra_mcps": ["fs-tools"]}, "heavy": {"models": ["deepseek-chat"]}}}
    assert get_agent_mode_config(configs, "coder", "light") == {"models": ["qwen-coder"], "extra_mcps": ["fs-tools"]}
    assert get_agent_mode_config(configs, "coder", "heavy") == {"models": ["deepseek-chat"]}


def test_real_config_has_no_empty_heavy_models():
    """Явные heavy-модели вместо `{models: []}` (пустой список = неявный fallback в routing)."""
    configs = load_agent_configs()
    assert configs, "config/agent_configs.json должен читаться"

    for agent_id, agent_conf in configs.items():
        for mode in ("light", "heavy"):
            mode_conf = agent_conf.get(mode)
            if not isinstance(mode_conf, dict):
                continue
            models = mode_conf.get("models")
            if models is None:
                continue  # legacy-формат {"model": "..."} допустим
            assert models, f"[{agent_id}/{mode}] пустой список моделей — укажи явные модели"

        for mode in ("light", "heavy"):
            extra = (agent_conf.get(mode) or {}).get("extra_mcps")
            if isinstance(extra, list):
                assert len(extra) == len(set(extra)), f"[{agent_id}/{mode}] дубли в extra_mcps: {extra}"


def test_orchestration_config_defaults():
    """4.6: checkpoint только по явному включению, запись только в песочницу."""
    from services.sandbox import load_orchestration_config

    conf = load_orchestration_config()
    assert conf["auto_checkpoint"] is False
    assert conf["require_sandbox_for_writes"] is True
