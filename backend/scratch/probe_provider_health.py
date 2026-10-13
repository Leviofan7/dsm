"""
Проба: сбой провайдера классифицируется, кулдаун ставится, fallback реально срабатывает.

Живые (не моковые) проверки:
  1. РЕАЛЬНЫЙ отказ: подставляем неверный ключ DeepSeek → настоящий HTTP 401 от
     api.deepseek.com → `_chat` возвращает None, а сервис фиксирует класс `auth`
     (раньше на этом месте было безымянное `available = False` без срока);
  2. кулдаун: у модели стоит срок возврата, а текст отказа ОБЪЯСНЯЕТ причину;
  3. fallback по РЕАЛЬНОМУ config/agent_configs.json: с упавшим облачным кандидатом
     `_get_fallback` возвращает следующую модель цепочки агента (старый код искал
     упавшую модель в legacy-routing и в этом случае молча возвращал None);
  4. HTTP-контракт: `GET /providers/health` и `?probe=true` (живая проба Ollama/DeepSeek).

Запуск: docker compose exec -T fastapi_backend python -B /app/scratch/probe_provider_health.py
БД не пишется (кроме audit/web_sessions от админ-cookie — их проба убирает за собой).
"""

import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import models
from auth import create_session_token

BASE = "http://localhost:8000"
MARK = "Проба провайдеров (probe)"


def _cleanup():
    db = database.SessionLocal()
    try:
        uids = [u.id for u in db.query(models.User).filter(models.User.display_name == MARK).all()]
        if uids:
            db.query(models.AuditLog).filter(models.AuditLog.user_id.in_(uids)).delete(synchronize_session=False)
            db.query(models.WebSession).filter(models.WebSession.user_id.in_(uids)).delete(synchronize_session=False)
            db.query(models.User).filter(models.User.id.in_(uids)).delete(synchronize_session=False)
            db.commit()
    finally:
        db.close()


async def _live_failure_and_fallback():
    from agent.llm_manager import LLMManager, extract_model_candidates, get_agent_mode_config, load_agent_configs
    from services import provider_health

    manager = LLMManager()
    await manager.initialize()
    try:
        provider_health.health.clear()
        entry = manager.registry.models.get("deepseek-chat")
        assert entry is not None, "нет модели deepseek-chat в реестре"

        original_key = entry.api_key
        try:
            entry.api_key = "sk-probe-invalid-key"
            entry.available = True
            result = await manager._chat(
                entry, [{"role": "user", "content": "ping"}], tools=None
            )
            assert result is None, "неверный ключ обязан дать None, а не ответ"
        finally:
            entry.api_key = original_key

        state = provider_health.health.state("deepseek-chat")
        print(f"@@ живой сбой: kind={state['kind']} | {state['detail'][:80]}")
        print(f"@@ кулдаун: retry_in_s={state['retry_in_s']} | reason={state['reason']}")
        assert state["kind"] == provider_health.KIND_AUTH, "401 от DeepSeek обязан быть классом auth"
        assert state["retry_in_s"] > 0, "модель должна уйти в паузу, а не в вечный бан"

        msg = manager._unavailable_message("Ошибка: все модели недоступны.")
        print(f"@@ текст отказа: {msg[:160]}")
        assert "deepseek-chat" in msg and "ключ" in msg, "отказ обязан объяснять причину"

        # Реальный fallback по реальному конфигу агента
        cfg = load_agent_configs()
        chains = {role: extract_model_candidates(get_agent_mode_config(cfg, role, "auto"))
                  for role in ("coder", "web_researcher", "doorman")}
        print("@@ цепочки агентов: " + "; ".join(f"{r}={c}" for r, c in chains.items()))

        role = next((r for r, c in chains.items() if len(c) > 1 and c[0] == "deepseek-chat"), None)
        if role:
            fb = manager._get_fallback(entry, role)
            print(f"@@ fallback для '{role}': {getattr(fb, 'name', None)}")
            assert fb is not None and fb.name != "deepseek-chat", "fallback обязан найти запасную модель"
        else:
            # Если первой в цепочке deepseek не стоит — подменяем упавшую на её первый кандидат
            role = next((r for r, c in chains.items() if len(c) > 1), None)
            assert role, "нет роли с цепочкой из >1 модели для проверки fallback"
            failed = manager.registry.models[chains[role][0]]
            fb = manager._get_fallback(failed, role)
            print(f"@@ fallback для '{role}':{getattr(fb, 'name', None)}")
            assert fb is not None and fb.name != failed.name

        snapshot = provider_health.health.snapshot(manager.registry)
        print(f"@@ snapshot: провайдеров={len(snapshot['providers'])} моделей={len(snapshot['models'])} "
              f"деградаций={list(snapshot['degraded'])}")
        provider_health.health.clear()
        print("@@ RESULT: провайдерские сбои классифицируются и кулдаунятся = True")
    finally:
        await manager.shutdown()


def _http_check():
    db = database.SessionLocal()
    try:
        user = models.User(role="admin", display_name=MARK)
        db.add(user)
        db.commit()
        db.refresh(user)
        uid = user.id
    finally:
        db.close()

    c = httpx.Client(base_url=BASE, cookies={"contextus_session": create_session_token(uid)}, timeout=60)

    r = c.get("/providers/health")
    assert r.status_code == 200, r.text
    body = r.json()
    print(f"@@ GET /providers/health: провайдеров={len(body['providers'])} probe={body['probe']}")
    assert body["probe"] is None
    assert any(p["provider"] == "ollama" for p in body["providers"])

    r = c.get("/providers/health", params={"probe": "true"})
    assert r.status_code == 200, r.text
    probe = r.json()["probe"]
    print(f"@@ ?probe=true: " + "; ".join(f"{k}={v['ok']}/{v['checked']}" for k, v in probe.items()))
    assert "ollama" in probe and "deepseek" in probe

    anon = httpx.get(f"{BASE}/providers/health", timeout=15)
    print(f"@@ аноним: HTTP {anon.status_code}")
    assert anon.status_code == 401

    c.close()


async def main():
    _cleanup()
    await _live_failure_and_fallback()
    _http_check()
    _cleanup()
    print("@@ RESULT: provider_health_live = True")


asyncio.run(main())
