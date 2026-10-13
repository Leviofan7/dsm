"""
Здоровье провайдеров моделей: классификация сбоев, кулдаун, видимый статус.

Повод (инцидент 26.09 «второй запрос из TG не получил ответа»): `entry.available`
означало РОВНО «задан API-ключ». Ни отозванный ключ, ни кончившийся баланс, ни упавший
провайдер в этом флаге не отражались, а любой сбой вызова навсегда помечал модель
недоступной (`model.available = False`) — без причины, без срока и без возможности вернуться.
Итог: тихая деградация и «все модели недоступны» без объяснения, что именно случилось.

Что здесь:
  * классификация сбоя (auth / quota / unavailable / unknown) — по коду ответа, а не «ошибка»;
  * КУЛДАУН вместо вечного бана: лимит/сеть — временные, ключ — надолго, но с проверкой;
  * снимок состояния для API/UI: кто доступен, кто в кулдауне и почему;
  * живая проба (Ollama / баланс DeepSeek) — то, что нельзя узнать по флагу ключа.

ЧЕСТНОЕ ОГРАНИЧЕНИЕ: состояние кулдауна живёт В ПАМЯТИ ПРОЦЕССА. FastAPI-процесс и
воркер не делят его между собой, а рестарт контейнера обнуляет. Это осознанно: разделяемое
состояние требует Redis/БД и отдельного решения, а польза кулдауна достигается и так —
в рамках процесса, который делает вызовы.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger("contextus.provider_health")

#: Классы сбоя. Разные классы — разное лечение, поэтому и разный кулдаун.
KIND_AUTH = "auth"                 # 401/403 — ключ неверный/отозван
KIND_QUOTA = "quota"               # 402/429 — нет баланса или превышен лимит
KIND_UNAVAILABLE = "unavailable"   # 5xx / таймаут / сеть — провайдер или канал лежит
KIND_UNKNOWN = "unknown"

#: Сколько держать модель «на паузе» после сбоя (секунды).
#: Смысл: не долбить мёртвый провайдер на каждом шаге, но и не хоронить его навсегда.
COOLDOWN_SECONDS: dict[str, int] = {
    KIND_AUTH: 1800,        # ключ сам не починится — проверяем редко
    KIND_QUOTA: 600,        # баланс могли пополнить — пробуем через 10 минут
    KIND_UNAVAILABLE: 120,  # сеть/5xx — короткая пауза
    KIND_UNKNOWN: 300,
}

#: Человекочитаемая причина + что делать. Идёт в сообщение пользователю и в self_review.
KIND_LABEL: dict[str, str] = {
    KIND_AUTH: "ключ недействителен (проверьте/перевыпустите API-ключ)",
    KIND_QUOTA: "исчерпан баланс или лимит провайдера",
    KIND_UNAVAILABLE: "провайдер недоступен (сеть или 5xx)",
    KIND_UNKNOWN: "неизвестная ошибка провайдера",
}


def classify_http_status(status: int) -> str:
    """Класс сбоя по HTTP-коду. 5xx и 408 — «недоступен», не «сломался навсегда»."""
    if status in (401, 403):
        return KIND_AUTH
    if status in (402, 429):
        return KIND_QUOTA
    if status == 408 or 500 <= status <= 599:
        return KIND_UNAVAILABLE
    return KIND_UNKNOWN


def classify_exception(exc: BaseException) -> tuple[str, str]:
    """(класс, деталь) для исключения вызова модели."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = (exc.response.text or "").strip().replace("\n", " ")[:200]
        return classify_http_status(status), f"HTTP {status}: {body}"
    if isinstance(exc, httpx.TimeoutException):
        return KIND_UNAVAILABLE, f"таймаут: {type(exc).__name__}"
    if isinstance(exc, httpx.TransportError):
        return KIND_UNAVAILABLE, f"сеть: {type(exc).__name__}"
    return KIND_UNKNOWN, f"{type(exc).__name__}: {str(exc)[:200]}"


@dataclass
class _Failure:
    kind: str
    detail: str
    at: float
    until: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "at": self.at,
            "until": self.until,
            "retry_in_s": max(0, int(self.until - time.time())),
            "reason": KIND_LABEL.get(self.kind, KIND_LABEL[KIND_UNKNOWN]),
        }


@dataclass
class ProviderHealth:
    """Реестр сбоев и кулдаунов. Работает с любым объектом модели с `.name` и `.available`."""

    _failures: dict[str, _Failure] = field(default_factory=dict)

    # ── запись событий ────────────────────────────────────────────

    def note_failure(self, model: Any, kind: str, detail: str) -> dict[str, Any]:
        """Помечает модель недоступной на кулдаун по классу сбоя."""
        now = time.time()
        cooldown = COOLDOWN_SECONDS.get(kind, COOLDOWN_SECONDS[KIND_UNKNOWN])
        failure = _Failure(kind=kind, detail=detail, at=now, until=now + cooldown)
        self._failures[getattr(model, "name", str(model))] = failure
        model.available = False
        logger.warning(
            f"  🩺 [{model.name}] сбой провайдера ({kind}): {detail} — "
            f"пауза {cooldown} c ({KIND_LABEL.get(kind, kind)})"
        )
        return failure.as_dict()

    def note_success(self, model: Any) -> None:
        """Успешный вызов снимает кулдаун: модель снова в строю."""
        name = getattr(model, "name", str(model))
        if name in self._failures:
            logger.info(f"  🩺 [{name}] восстановлен — кулдаун снят после успешного вызова")
            self._failures.pop(name, None)
        if not getattr(model, "available", False):
            model.available = True

    # ── восстановление ────────────────────────────────────────────

    def _statically_available(self, model: Any) -> bool:
        """Доступна ли модель по «статическим» правилам (ключ / установлена в Ollama)."""
        if getattr(model, "provider_type", "") == "ollama":
            return bool(getattr(model, "installed", False))
        return bool(getattr(model, "api_key", ""))

    def recover_expired(self, registry: Any) -> list[str]:
        """Возвращает в строй модели, у которых кулдаун истёк. Возвращает список имён."""
        now = time.time()
        recovered: list[str] = []
        for name, failure in list(self._failures.items()):
            if failure.until > now:
                continue
            model = registry.models.get(name)
            self._failures.pop(name, None)
            if model is None:
                continue
            if self._statically_available(model) and not model.available:
                model.available = True
                recovered.append(name)
                logger.info(f"  🩺 [{name}] кулдаун истёк ({failure.kind}) — модель снова доступна")
        return recovered

    # ── чтение состояния ──────────────────────────────────────────

    def state(self, model_name: str) -> dict[str, Any] | None:
        failure = self._failures.get(model_name)
        return failure.as_dict() if failure else None

    def active(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        return {
            name: failure.as_dict()
            for name, failure in self._failures.items()
            if failure.until > now
        }

    def failure_summary(self, limit: int = 3) -> str:
        """Короткая причина для сообщения пользователю: без неё «все модели недоступны» — загадка."""
        active = self.active()
        if not active:
            return ""
        parts = []
        for name, state in list(active.items())[:limit]:
            parts.append(f"{name}: {state['reason']}")
        return "; ".join(parts)

    def snapshot(self, registry: Any) -> dict[str, Any]:
        """Состояние по провайдерам и моделям — для API/UI."""
        active = self.active()
        providers: dict[str, dict[str, Any]] = {}
        models: list[dict[str, Any]] = []
        for name, entry in sorted(registry.models.items()):
            provider = getattr(entry, "provider_name", "?")
            bucket = providers.setdefault(provider, {"provider": provider, "models": [], "available": False})
            bucket["models"].append(name)
            bucket["available"] = bucket["available"] or bool(entry.available)
            state = active.get(name)
            models.append({
                "name": name,
                "provider": provider,
                "provider_type": getattr(entry, "provider_type", None),
                "model_id": getattr(entry, "model_id", None),
                "available": bool(entry.available),
                "installed": bool(getattr(entry, "installed", False)),
                "key_present": bool(getattr(entry, "api_key", "")),
                "degraded": state is not None,
                "failure": state,
            })
        for bucket in providers.values():
            bucket["degraded"] = [
                m for m in bucket["models"] if m in active
            ]
        return {"providers": list(providers.values()), "models": models, "degraded": active}

    def clear(self) -> None:
        self._failures.clear()


#: Единственный экземпляр на процесс (см. ограничение в докстроке модуля).
health = ProviderHealth()


# ── Живая проба: то, чего не видно по флагу ключа ──────────────────

async def live_probe() -> dict[str, Any]:
    """
    Живая проверка провайдеров. Проверяем ровно то, что МОЖЕМ проверить, и честно
    помечаем остальное как «только наличие ключа» — а не выдаём «ok» за проверку.

    * ollama  — GET /api/tags (локальный демон);
    * deepseek — GET /user/balance (единственный из наших провайдеров с публичным балансом);
    * прочие   — наличие ключа (у gemini/anthropic баланса в публичном API нет).
    """
    result: dict[str, Any] = {}

    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
        result["ollama"] = {
            "checked": "live",
            "ok": resp.status_code == 200,
            "detail": f"HTTP {resp.status_code}",
        }
    except Exception as e:  # noqa: BLE001 — диагностика важнее строгости типов
        result["ollama"] = {"checked": "live", "ok": False, "detail": f"{type(e).__name__}: {e}"}

    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        result["deepseek"] = {"checked": "key", "ok": False, "detail": "ключ не задан"}
    else:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    "https://api.deepseek.com/user/balance",
                    headers={"Authorization": f"Bearer {key}"},
                )
            if resp.status_code == 200:
                data = resp.json()
                info = (data.get("balance_infos") or [{}])[0]
                result["deepseek"] = {
                    "checked": "live",
                    "ok": bool(data.get("is_available")),
                    "detail": f"баланс {info.get('total_balance')} {info.get('currency')}",
                }
            else:
                result["deepseek"] = {
                    "checked": "live",
                    "ok": False,
                    "detail": f"HTTP {resp.status_code}: {resp.text[:120]}",
                }
        except Exception as e:  # noqa: BLE001
            result["deepseek"] = {"checked": "live", "ok": False, "detail": f"{type(e).__name__}: {e}"}

    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        provider = name.replace("_API_KEY", "").lower()
        present = bool(os.getenv(name, ""))
        result[provider] = {
            "checked": "key",
            "ok": present,
            "detail": "ключ задан (баланс через публичный API недоступен)" if present else "ключ не задан",
        }

    return result
