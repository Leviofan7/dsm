"""
Здоровье провайдеров: классификация сбоя, кулдаун и НАСТОЯЩИЙ fallback.

Стражи против тихой деградации (инцидент 26.09: «второй запрос из TG молча не ответил»):
  1. причина сбоя видна и различима (ключ / баланс-лимит / сеть), а не «ошибка»;
  2. сбой не хоронит модель навсегда — кулдаун по классу, потом возврат в строй;
  3. «нет доступной модели» обязано объяснять, ЧТО случилось;
  4. fallback идёт по цепочке АГЕНТА, а не по legacy-`routing`: иначе он декоративен
     (старый код требовал, чтобы упавшая модель была в routing, и молча возвращал None).
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from agent import llm_manager  # noqa: E402
from services import provider_health  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_health():
    """Синглтон живёт на процесс — чистим до и после, иначе тесты влияют друг на друга."""
    provider_health.health.clear()
    yield
    provider_health.health.clear()


def _entry(name: str, *, available: bool = True, api_key: str = "key", provider_type: str = "openai",
           installed: bool = False):
    return SimpleNamespace(
        name=name, available=available, api_key=api_key,
        provider_type=provider_type, installed=installed, provider_name="test",
    )


# ── 1. Классификация ───────────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    (401, provider_health.KIND_AUTH),
    (403, provider_health.KIND_AUTH),
    (402, provider_health.KIND_QUOTA),
    (429, provider_health.KIND_QUOTA),
    (408, provider_health.KIND_UNAVAILABLE),
    (500, provider_health.KIND_UNAVAILABLE),
    (503, provider_health.KIND_UNAVAILABLE),
    (400, provider_health.KIND_UNKNOWN),
])
def test_http_status_is_classified(status, expected):
    assert provider_health.classify_http_status(status) == expected


def test_exception_classification_distinguishes_network_from_status():
    request = httpx.Request("POST", "https://api.example.com/chat")
    response = httpx.Response(402, request=request, text="Insufficient Balance")

    kind, detail = provider_health.classify_exception(
        httpx.HTTPStatusError("boom", request=request, response=response)
    )
    assert kind == provider_health.KIND_QUOTA
    assert "402" in detail and "Insufficient Balance" in detail

    kind, _ = provider_health.classify_exception(httpx.ConnectTimeout("t"))
    assert kind == provider_health.KIND_UNAVAILABLE


# ── 2. Кулдаун вместо вечного бана ─────────────────────────────────

def test_failure_sets_cooldown_and_marks_model_unavailable():
    model = _entry("deepseek-chat")
    state = provider_health.health.note_failure(model, provider_health.KIND_QUOTA, "HTTP 402: нет баланса")

    assert model.available is False
    assert state["kind"] == provider_health.KIND_QUOTA
    assert state["retry_in_s"] > 0
    assert "баланс" in state["reason"]


def test_recover_expired_returns_model_to_service(monkeypatch):
    model = _entry("deepseek-chat")
    registry = SimpleNamespace(models={"deepseek-chat": model})
    provider_health.health.note_failure(model, provider_health.KIND_QUOTA, "HTTP 402")

    # кулдаун ещё идёт — не возвращаем
    assert provider_health.health.recover_expired(registry) == []
    assert model.available is False

    # «прошло время»: нулевой кулдаун = истёк сразу
    monkeypatch.setitem(provider_health.COOLDOWN_SECONDS, provider_health.KIND_QUOTA, 0)
    provider_health.health.note_failure(model, provider_health.KIND_QUOTA, "HTTP 402")
    assert provider_health.health.recover_expired(registry) == ["deepseek-chat"]
    assert model.available is True


def test_model_without_key_is_not_revived(monkeypatch):
    """Ключа нет — кулдаун не поможет: не делаем вид, что модель восстановилась."""
    model = _entry("gemini-flash", api_key="")
    registry = SimpleNamespace(models={"gemini-flash": model})
    monkeypatch.setitem(provider_health.COOLDOWN_SECONDS, provider_health.KIND_UNAVAILABLE, 0)
    provider_health.health.note_failure(model, provider_health.KIND_UNAVAILABLE, "timeout")

    assert provider_health.health.recover_expired(registry) == []
    assert model.available is False


def test_success_clears_cooldown():
    model = _entry("deepseek-chat")
    provider_health.health.note_failure(model, provider_health.KIND_QUOTA, "HTTP 402")
    provider_health.health.note_success(model)

    assert model.available is True
    assert provider_health.health.state("deepseek-chat") is None


# ── 3. Причина в тексте отказа ─────────────────────────────────────

def test_unavailable_message_explains_reason():
    manager = llm_manager.LLMManager.__new__(llm_manager.LLMManager)
    model = _entry("deepseek-chat")
    provider_health.health.note_failure(model, provider_health.KIND_QUOTA, "HTTP 402: Insufficient Balance")

    msg = manager._unavailable_message("Ошибка: все модели недоступны.")
    assert "deepseek-chat" in msg
    assert "баланс" in msg, "без причины сообщение бесполезно для человека"

    provider_health.health.clear()
    assert manager._unavailable_message("Ошибка: все модели недоступны.") == "Ошибка: все модели недоступны."


# ── 4. Fallback по цепочке агента ──────────────────────────────────

def test_fallback_uses_agent_config_chain(monkeypatch):
    """
    Ключевой страж: упавшая модель пришла из настроек агента и её НЕТ в routing.
    Старый код в этом случае возвращал None (fallback был декоративным).
    """
    manager = llm_manager.LLMManager.__new__(llm_manager.LLMManager)
    primary = _entry("cloud-primary", available=False)
    local = _entry("local-fallback", available=True)
    manager.registry = SimpleNamespace(models={"cloud-primary": primary, "local-fallback": local}, routing={})
    monkeypatch.setattr(
        llm_manager, "load_agent_configs",
        lambda: {"coder": {"light": {"models": ["cloud-primary", "local-fallback"]}}},
    )

    assert manager._get_fallback(primary, "coder") is local


def test_fallback_falls_back_to_routing_chain_when_agent_config_empty(monkeypatch):
    manager = llm_manager.LLMManager.__new__(llm_manager.LLMManager)
    primary = _entry("cloud-primary", available=False)
    routed = _entry("routed-model", available=True)
    manager.registry = SimpleNamespace(
        models={"cloud-primary": primary, "routed-model": routed},
        routing={"coding": ["cloud-primary", "routed-model"]},
    )
    monkeypatch.setattr(llm_manager, "load_agent_configs", lambda: {})

    assert manager._get_fallback(primary, "coding") is routed


def test_fallback_skips_unavailable_candidates(monkeypatch):
    manager = llm_manager.LLMManager.__new__(llm_manager.LLMManager)
    primary = _entry("a", available=False)
    dead = _entry("b", available=False)
    alive = _entry("c", available=True)
    manager.registry = SimpleNamespace(models={"a": primary, "b": dead, "c": alive}, routing={})
    monkeypatch.setattr(llm_manager, "load_agent_configs", lambda: {"coder": {"light": {"models": ["a", "b", "c"]}}})

    assert manager._get_fallback(primary, "coder") is alive


# ── 5. Снимок для API/UI ───────────────────────────────────────────

def test_snapshot_reports_degradation():
    ok = _entry("local-model", provider_type="ollama", api_key="", installed=True)
    bad = _entry("deepseek-chat")
    registry = SimpleNamespace(models={"local-model": ok, "deepseek-chat": bad})
    provider_health.health.note_failure(bad, provider_health.KIND_QUOTA, "HTTP 402")

    snap = provider_health.health.snapshot(registry)

    assert "deepseek-chat" in snap["degraded"]
    degraded = next(m for m in snap["models"] if m["name"] == "deepseek-chat")
    assert degraded["degraded"] is True and degraded["failure"]["kind"] == provider_health.KIND_QUOTA
    healthy = next(m for m in snap["models"] if m["name"] == "local-model")
    assert healthy["degraded"] is False and healthy["failure"] is None
