"""
model_catalog.py — обнаружение локальных моделей Ollama и хранение их в БД.

Зачем: список моделей в выпадающих списках UI должен совпадать с реальным `ollama list`,
а не только с хардкодом `config/models.yaml`. Плюс метаданные (tools / vision / context)
берутся из самой Ollama, а не угадываются руками.

Поток:
  1. `fetch_ollama_models()`      → GET  /api/tags   (что установлено и когда менялось)
  2. `fetch_model_capabilities()` → POST /api/show   (capabilities, context_length, template)
  3. `sync_catalog()`             → upsert в таблицу `model_catalog`
  4. `load_catalog_entries()`     → записи для ModelRegistry (модели попадают в реестр)

`models.yaml` остаётся источником курируемых записей: при совпадении `model_id` побеждает YAML,
и каталог для такой модели не создаётся (см. `skip_model_ids`).
"""

import asyncio
import json
import logging
import os
from datetime import datetime

import httpx

from database import SessionLocal, engine
from models import ModelCatalog

logger = logging.getLogger("contextus.model_catalog")

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_CONTEXT_WINDOW = 4096
_MAX_PARALLEL_SHOW = 4

_CAP_TOOLS = "tools"
_CAP_VISION = "vision"


def ollama_url() -> str:
    """Базовый URL Ollama. В Docker задаётся через OLLAMA_URL=http://host.docker.internal:11434."""
    return os.getenv("OLLAMA_URL", DEFAULT_OLLAMA_URL)


def ensure_table() -> None:
    """
    Создаёт таблицу model_catalog, если её нет.

    В проекте нет наполненных alembic-миграций (versions/ пуст), поэтому схема здесь
    создаётся идемпотентно при старте — как это уже сделано в тестах через create_all.
    """
    try:
        ModelCatalog.__table__.create(bind=engine, checkfirst=True)
    except Exception as e:  # параллельный старт воркера/бэкенда не должен ронять приложение
        logger.warning(f"⚠️ Не удалось создать таблицу model_catalog: {e}")


# ═══════════════════════════════════════════════════════════════════
#  Ollama API
# ═══════════════════════════════════════════════════════════════════

async def fetch_ollama_models(base_url: str | None = None, timeout: float = 10.0) -> list[dict] | None:
    """
    GET /api/tags — список установленных моделей.

    Возвращает None, если Ollama недоступна. Это важно отличать от пустого списка:
    «Ollama молчит» не значит «пользователь удалил все модели», и в этом случае
    флаги `installed` в БД трогать нельзя.
    """
    url = base_url or ollama_url()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}/api/tags")
            if resp.status_code != 200:
                logger.warning(f"🟡 Ollama /api/tags ответила {resp.status_code}")
                return None
            return resp.json().get("models", []) or []
    except Exception as e:
        logger.warning(f"🔴 Ollama недоступна ({url}): {e}")
        return None


async def fetch_model_capabilities(
    model_id: str,
    base_url: str | None = None,
    timeout: float = 10.0,
) -> dict | None:
    """POST /api/show — capabilities, model_info (context_length), template, parameters."""
    url = base_url or ollama_url()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{url}/api/show", json={"model": model_id})
            if resp.status_code != 200:
                logger.debug(f"  /api/show {model_id} → {resp.status_code}")
                return None
            return resp.json()
    except Exception as e:
        logger.debug(f"  /api/show {model_id} не удался: {e}")
        return None


def parse_capabilities(show: dict | None) -> tuple[bool, bool, int | None]:
    """
    (supports_tools, supports_vision, context_window) из ответа /api/show.

    Актуальные версии Ollama отдают поле `capabilities`; для старых есть эвристики:
    поддержка tools — через `{{ if .Tools }}` в шаблоне, vision — по clip/vision-ключам
    в `model_info`.
    """
    if not show:
        return False, False, None

    capabilities = show.get("capabilities") or []
    model_info = show.get("model_info") or {}

    supports_tools = _CAP_TOOLS in capabilities
    if not supports_tools:
        # Фолбек для Ollama без поля capabilities: шаблон рендерит блок .Tools
        template = show.get("template") or ""
        supports_tools = ".Tools" in template or ".tools" in template

    supports_vision = _CAP_VISION in capabilities or _has_vision_keys(model_info)

    return supports_tools, supports_vision, _extract_context_window(show, model_info)


def _has_vision_keys(model_info: dict) -> bool:
    """Признак vision-модели для старых версий Ollama: clip/vision-ключи в model_info."""
    for key, value in model_info.items():
        low = key.lower()
        if "vision" not in low and "clip" not in low:
            continue
        if isinstance(value, bool) and not value:
            continue  # clip.has_vision_encoder: false — не признак
        return True
    return False


def _extract_context_window(show: dict, model_info: dict) -> int | None:
    """
    Контекст модели: самый большой `*.context_length` из model_info,
    иначе num_ctx из parameters (строка вида "num_ctx 8192").
    """
    windows = [
        int(value)
        for key, value in model_info.items()
        if key.endswith("context_length") and isinstance(value, (int, float)) and value > 0
    ]
    if windows:
        return max(windows)

    parameters = show.get("parameters") or ""
    for line in parameters.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "num_ctx" and parts[1].isdigit():
            return int(parts[1])
    return None


def is_chat_model(show: dict | None) -> bool:
    """
    Может ли модель работать в чате. Ollama отдаёт `capabilities` с "embedding"
    для чисто векторных моделей (nomic-embed-text и т.п.) — им не место в списке
    моделей агента.

    Неизвестные наборы (например только ["tools"]) считаем чатовыми: лучше показать
    лишнюю модель, чем потерять рабочую из-за особенностей версии Ollama.
    """
    if not show:
        return True
    capabilities = show.get("capabilities") or []
    if not capabilities:
        return True
    if "completion" in capabilities:
        return True
    return "embedding" not in capabilities


def _summarize_show(show: dict, context_window: int | None) -> str:
    """
    Компактный JSON из /api/show для отладки.

    Целиком ответ хранить нельзя: он содержит шаблон и `model_info` на десятки килобайт,
    а обрезка ломает JSON. Храним только то, что реально используется.
    """
    details = show.get("details") or {}
    return json.dumps(
        {
            "capabilities": show.get("capabilities"),
            "context_length": context_window,
            "family": details.get("family"),
            "parameter_size": details.get("parameter_size"),
            "quantization_level": details.get("quantization_level"),
        },
        ensure_ascii=False,
    )


# ═══════════════════════════════════════════════════════════════════
#  БД
# ═══════════════════════════════════════════════════════════════════

def load_catalog_entries(session=None) -> dict[str, dict]:
    """
    Записи каталога в форме, которую понимает `ModelEntry`, — включая не установленные.

    Включённые в каталог, но отсутствующие в Ollama модели остаются в реестре:
    тогда сохранённый конфиг агента не ломается при недоступной Ollama, а UI показывает
    модель серой с пометкой «не найдена».
    """
    own_session = session is None
    session = session or SessionLocal()
    try:
        rows = session.query(ModelCatalog).filter(ModelCatalog.enabled.is_(True)).all()
        return {
            row.id: {
                "provider": row.provider_name,
                "model_id": row.model_id,
                "context_window": row.context_window or DEFAULT_CONTEXT_WINDOW,
                "supports_tools": bool(row.supports_tools),
                "supports_vision": bool(row.supports_vision),
                "tags": [t for t in (row.tags or "").split(",") if t],
                "installed": bool(row.installed),
                "size_bytes": row.size_bytes or 0,
            }
            for row in rows
        }
    except Exception as e:
        logger.warning(f"⚠️ Каталог моделей недоступен: {e}")
        return {}
    finally:
        if own_session:
            session.close()


def sync_catalog(
    installed: list[dict],
    capabilities: dict[str, dict] | None = None,
    session=None,
    skip_model_ids: set[str] | None = None,
) -> dict:
    """
    Upsert обнаруженных моделей и пометка `installed=False` для пропавших.

    installed — сырой ответ /api/tags; capabilities — {model_id: ответ /api/show}.
    skip_model_ids — model_id, уже описанные в models.yaml (курируемые записи не дублируем).
    """
    capabilities = capabilities or {}
    skip_model_ids = skip_model_ids or set()

    own_session = session is None
    session = session or SessionLocal()
    stats = {"discovered": 0, "updated": 0, "missing": 0, "skipped": 0, "non_chat": 0, "pruned": 0}

    try:
        rows = {row.id: row for row in session.query(ModelCatalog).all()}
        seen: set[str] = set()
        now = datetime.utcnow()

        for item in installed:
            model_id = (item.get("name") or item.get("model") or "").strip()
            if not model_id:
                continue
            if model_id in skip_model_ids:
                stats["skipped"] += 1
                continue

            seen.add(model_id)
            row = rows.get(model_id)
            show = capabilities.get(model_id)
            is_new = row is None
            new_digest = item.get("digest") or None
            digest_changed = bool(new_digest) and row is not None and row.digest != new_digest

            # Ничего не изменилось — строку не трогаем. Иначе два процесса (backend и worker)
            # писали бы в SQLite каждые MODEL_REFRESH_INTERVAL секунд.
            if row is not None and not digest_changed and row.installed and not show:
                continue

            if is_new:
                row = ModelCatalog(id=model_id, model_id=model_id)
                session.add(row)
                rows[model_id] = row
                # Метаданные неизвестны → разумные значения по умолчанию (4096 у Ollama)
                row.context_window = DEFAULT_CONTEXT_WINDOW

            row.model_id = model_id
            row.provider_name = "ollama"
            row.provider_type = "ollama"
            row.installed = True
            row.last_seen_at = now
            row.size_bytes = int(item.get("size") or 0)
            row.digest = new_digest or row.digest

            if show:
                tools, vision, window = parse_capabilities(show)
                chat_model = is_chat_model(show)
                row.supports_tools = tools
                row.supports_vision = vision
                if window:
                    row.context_window = window
                row.capabilities_raw = _summarize_show(show, window)
                # Векторные модели (nomic-embed-text и пр.) не умеют чат с tool calling —
                # держим их в каталоге, но скрываем из выпадающих списков.
                row.enabled = chat_model
                tags = ["local", "discovered"]
                if tools:
                    tags.append("tools")
                if vision:
                    tags.append("vision")
                if not chat_model:
                    tags.append("embedding")
                row.tags = ",".join(tags)
                if not chat_model:
                    stats["non_chat"] += 1

            if is_new:
                stats["discovered"] += 1
            else:
                stats["updated"] += 1

        # Модели, которых больше нет в `ollama list`. Записи не удаляем — на них могут
        # ссылаться конфиги агентов; UI покажет их как недоступные.
        # А записи, которые теперь описаны в models.yaml, наоборот удаляем: YAML — источник
        # правды, дубль в каталоге дал бы вторую строку с тем же model_id.
        for model_id, row in rows.items():
            if row.model_id in skip_model_ids:
                session.delete(row)
                stats["pruned"] += 1
            elif model_id not in seen and row.installed:
                row.installed = False
                stats["missing"] += 1

        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Ошибка синхронизации каталога моделей: {e}")
        raise
    finally:
        if own_session:
            session.close()

    return stats


# ═══════════════════════════════════════════════════════════════════
#  Оркестрация
# ═══════════════════════════════════════════════════════════════════

async def _collect_capabilities(model_ids: list[str], base_url: str) -> dict[str, dict]:
    """Параллельно (но не более _MAX_PARALLEL_SHOW за раз) опрашивает /api/show."""
    semaphore = asyncio.Semaphore(_MAX_PARALLEL_SHOW)
    results: dict[str, dict] = {}

    async def worker(model_id: str) -> None:
        async with semaphore:
            show = await fetch_model_capabilities(model_id, base_url=base_url)
            if show:
                results[model_id] = show

    await asyncio.gather(*(worker(model_id) for model_id in model_ids))
    return results


async def refresh_catalog(
    base_url: str | None = None,
    *,
    deep: bool = False,
    session=None,
    skip_model_ids: set[str] | None = None,
) -> dict:
    """
    Полный цикл: /api/tags → (при необходимости) /api/show → запись в БД.

    deep=True заставляет переопросить /api/show для всех моделей (когда обновили
    саму Ollama или метаданные кажутся устаревшими).

    Возвращает {"ok": bool, "installed": set[str], ...stats}. При недоступной Ollama
    ok=False и флаги `installed` в БД не меняются.
    """
    url = base_url or ollama_url()
    skip_model_ids = skip_model_ids or set()

    installed = await fetch_ollama_models(base_url=url)
    if installed is None:
        return {"ok": False, "reason": "ollama_unreachable", "installed": set()}

    installed_ids = [
        (item.get("name") or item.get("model") or "").strip()
        for item in installed
        if (item.get("name") or item.get("model"))
    ]

    own_session = session is None
    session = session or SessionLocal()
    try:
        known = {row.id: row for row in session.query(ModelCatalog).all()}
        need_show: list[str] = []
        for item in installed:
            model_id = (item.get("name") or item.get("model") or "").strip()
            if not model_id or model_id in skip_model_ids:
                continue
            row = known.get(model_id)
            if deep or row is None or row.capabilities_raw is None or row.digest != item.get("digest"):
                need_show.append(model_id)

        capabilities = await _collect_capabilities(need_show, url) if need_show else {}
        stats = sync_catalog(
            installed,
            capabilities=capabilities,
            session=session,
            skip_model_ids=skip_model_ids,
        )
    finally:
        if own_session:
            session.close()

    logger.info(
        f"  🧩 Каталог моделей: установлено {len(installed_ids)}, "
        f"новых {stats['discovered']}, обновлено {stats['updated']}, "
        f"пропало {stats['missing']}, из YAML {stats['skipped']}, "
        f"убрано дублей YAML {stats['pruned']}, "
        f"не чатовых (embedding) {stats['non_chat']}"
    )
    return {"ok": True, "installed": set(installed_ids), **stats}
