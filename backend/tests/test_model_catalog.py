"""
Тесты каталога моделей: обнаружение локальных моделей Ollama (`/api/tags`),
парсинг метаданных из `/api/show` и слияние каталога с реестром `models.yaml`.

Запуск: cd backend && python -m pytest tests/test_model_catalog.py -q
"""
import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# Те же env-переменные, что и в остальных тестах: main.py падает без webhook-секрета.
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test_webhook_secret_12345"
os.environ["WEB_AUTH_SECRET"] = "test_web_auth_secret_67890"

from agent import llm_manager, model_catalog
from agent.llm_manager import ModelEntry, ModelRegistry
from agent.model_catalog import (
    load_catalog_entries,
    parse_capabilities,
    refresh_catalog,
    sync_catalog,
)
from models import ModelCatalog


@pytest.fixture
def session():
    """Изолированная in-memory БД на каждый тест (реальный contextus.db не трогаем)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ModelCatalog.__table__.create(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()


# ── /api/show → метаданные ──────────────────────────────────────────

def test_parse_capabilities_from_capabilities_field():
    show = {
        "capabilities": ["completion", "tools", "vision"],
        "model_info": {"llama.context_length": 131072},
    }
    assert parse_capabilities(show) == (True, True, 131072)


def test_parse_capabilities_fallbacks_for_old_ollama():
    """Старые версии не отдают capabilities: tools — по шаблону, vision — по clip-ключам."""
    show = {
        "template": "{{ if .Tools }}You have tools{{ end }}",
        "model_info": {"clip.has_vision_encoder": True},
        "parameters": "num_ctx 8192\nstop <|endoftext|>",
    }
    assert parse_capabilities(show) == (True, True, 8192)


def test_parse_capabilities_without_metadata():
    assert parse_capabilities(None) == (False, False, None)
    assert parse_capabilities({"capabilities": ["completion"]}) == (False, False, None)
    # clip.has_vision_encoder: false — это НЕ признак vision-модели
    assert parse_capabilities(
        {"model_info": {"clip.has_vision_encoder": False}}
    ) == (False, False, None)


def test_parse_capabilities_takes_largest_context_window():
    show = {"model_info": {"general.context_length": 8192, "llama.context_length": 32768}}
    assert parse_capabilities(show)[2] == 32768


# ── Синхронизация каталога ──────────────────────────────────────────

def test_sync_catalog_discovers_models_with_show_metadata(session):
    installed = [
        {"name": "qwen3:8b", "size": 5_000_000_000, "digest": "sha-qwen"},
        {"name": "llava:latest", "size": 4_000_000_000, "digest": "sha-llava"},
    ]
    capabilities = {
        "qwen3:8b": {"capabilities": ["tools"], "model_info": {"qwen3.context_length": 40960}},
    }

    stats = sync_catalog(installed, capabilities=capabilities, session=session)
    assert stats["discovered"] == 2 and stats["missing"] == 0

    entries = load_catalog_entries(session)
    assert set(entries) == {"qwen3:8b", "llava:latest"}

    qwen = entries["qwen3:8b"]
    assert qwen["provider"] == "ollama"
    assert qwen["model_id"] == "qwen3:8b"
    assert qwen["supports_tools"] is True
    assert qwen["context_window"] == 40960
    assert qwen["installed"] is True
    assert qwen["tags"] == ["local", "discovered", "tools"]

    # Без /api/show модель всё равно попадает в список — просто с метаданными по умолчанию
    llava = entries["llava:latest"]
    assert llava["installed"] is True
    assert llava["supports_tools"] is False
    assert llava["context_window"] == model_catalog.DEFAULT_CONTEXT_WINDOW


def test_sync_catalog_repeated_run_is_idempotent(session):
    """Повторный опрос без изменений не должен ни дублировать, ни перезаписывать строку."""
    payload = [{"name": "m:a", "size": 10, "digest": "d1"}]
    sync_catalog(payload, session=session)
    seen_at = session.get(ModelCatalog, "m:a").last_seen_at

    stats = sync_catalog(payload, session=session)

    assert stats["discovered"] == 0 and stats["updated"] == 0
    assert session.query(ModelCatalog).count() == 1
    assert session.get(ModelCatalog, "m:a").last_seen_at == seen_at


def test_sync_catalog_updates_row_when_digest_changes(session):
    """Перекачали модель (новый digest) → метаданные перечитываются."""
    sync_catalog([{"name": "m:a", "size": 10, "digest": "d1"}], session=session)
    stats = sync_catalog([{"name": "m:a", "size": 20, "digest": "d2"}], session=session)

    assert stats["updated"] == 1
    row = session.get(ModelCatalog, "m:a")
    assert row.digest == "d2" and row.size_bytes == 20


def test_sync_catalog_hides_embedding_only_models(session):
    """nomic-embed-text умеет только embedding — в выпадающем списке моделей ему не место."""
    installed = [
        {"name": "nomic-embed-text:latest", "digest": "sha-embed"},
        {"name": "qwen3:8b", "digest": "sha-chat"},
    ]
    capabilities = {
        "nomic-embed-text:latest": {"capabilities": ["embedding"]},
        "qwen3:8b": {"capabilities": ["completion", "tools"]},
    }

    stats = sync_catalog(installed, capabilities=capabilities, session=session)
    assert stats["non_chat"] == 1

    # В реестр (и в UI) попадает только чатовая модель
    assert set(load_catalog_entries(session)) == {"qwen3:8b"}

    # Запись embedding-модели сохранена, но скрыта — повторно её не опрашиваем
    embed = session.get(ModelCatalog, "nomic-embed-text:latest")
    assert embed is not None and embed.enabled is False and embed.installed is True
    assert embed.tags.endswith("embedding")


def test_sync_catalog_marks_missing_but_keeps_row(session):
    """Модель удалили из Ollama: запись остаётся, иначе конфиги агентов ломались бы."""
    sync_catalog([{"name": "qwen3:8b", "digest": "sha-qwen"}], session=session)
    stats = sync_catalog([], session=session)

    assert stats["missing"] == 1
    entries = load_catalog_entries(session)
    assert entries["qwen3:8b"]["installed"] is False


def test_sync_catalog_skips_models_curated_in_yaml(session):
    """model_id, описанный в models.yaml, в каталог не дублируется."""
    stats = sync_catalog(
        [{"name": "qwen2.5-coder:14b", "digest": "sha-x"}],
        session=session,
        skip_model_ids={"qwen2.5-coder:14b"},
    )

    assert stats["skipped"] == 1
    assert load_catalog_entries(session) == {}


def test_sync_catalog_prunes_rows_now_curated_in_yaml(session):
    """Модель добавили в models.yaml → её запись из каталога убираем, иначе будет дубль."""
    sync_catalog([{"name": "gemma4:26b", "digest": "sha-g4"}], session=session)
    assert "gemma4:26b" in load_catalog_entries(session)

    stats = sync_catalog(
        [{"name": "gemma4:26b", "digest": "sha-g4"}],
        session=session,
        skip_model_ids={"gemma4:26b"},
    )

    assert stats["pruned"] == 1
    assert load_catalog_entries(session) == {}
    assert session.query(ModelCatalog).count() == 0


def test_capabilities_raw_stays_valid_json_for_big_show(session):
    """Регресс: обрезка сырого ответа /api/show ломала JSON в БД на 20 000 символах."""
    installed = [{"name": "big:latest", "digest": "sha-big"}]
    show = {
        "capabilities": ["completion", "tools"],
        "model_info": {"big.context_length": 8192},
        "template": "x" * 60_000,  # шаблон реально весит десятки килобайт
    }

    sync_catalog(installed, capabilities={"big:latest": show}, session=session)
    row = session.get(ModelCatalog, "big:latest")

    parsed = json.loads(row.capabilities_raw)  # не должно бросать
    assert parsed["capabilities"] == ["completion", "tools"]
    assert parsed["context_length"] == 8192


@pytest.mark.asyncio
async def test_refresh_catalog_keeps_flags_when_ollama_unreachable(session, monkeypatch):
    """Ollama недоступна → installed в БД не сбрасываем (это не «модели удалили»)."""
    sync_catalog([{"name": "qwen3:8b", "digest": "sha-qwen"}], session=session)

    async def no_connection(*args, **kwargs):
        return None

    monkeypatch.setattr(model_catalog, "fetch_ollama_models", no_connection)

    result = await refresh_catalog(session=session)
    assert result["ok"] is False
    assert result["reason"] == "ollama_unreachable"
    assert load_catalog_entries(session)["qwen3:8b"]["installed"] is True


@pytest.mark.asyncio
async def test_refresh_catalog_upserts_discovered_models(session, monkeypatch):
    async def fake_tags(*args, **kwargs):
        return [{"name": "gemma3:4b", "size": 3_000_000_000, "digest": "sha-gemma"}]

    async def fake_show(model_id, *args, **kwargs):
        return {"capabilities": ["completion", "vision"], "model_info": {"gemma3.context_length": 32768}}

    monkeypatch.setattr(model_catalog, "fetch_ollama_models", fake_tags)
    monkeypatch.setattr(model_catalog, "fetch_model_capabilities", fake_show)

    result = await refresh_catalog(session=session)
    assert result["ok"] is True and result["discovered"] == 1

    entry = load_catalog_entries(session)["gemma3:4b"]
    assert entry["supports_vision"] is True and entry["supports_tools"] is False
    assert entry["context_window"] == 32768


# ── Реестр: YAML приоритетнее каталога ──────────────────────────────

def _local_entry(name: str, model_id: str) -> ModelEntry:
    return ModelEntry(name, {"provider": "ollama", "model_id": model_id}, {"type": "ollama"})


def test_merge_catalog_skips_model_ids_already_in_yaml(monkeypatch):
    """alias supervisor-14b → qwen2.5-coder:14b: каталог не создаёт дубль с другим именем."""
    registry = ModelRegistry()
    registry._raw_config = {"providers": {"ollama": {"type": "ollama"}}}
    registry.models = {"supervisor-14b": _local_entry("supervisor-14b", "qwen2.5-coder:14b")}

    monkeypatch.setattr(llm_manager, "ensure_catalog_table", lambda: None)
    monkeypatch.setattr(llm_manager, "load_catalog_entries", lambda: {
        "qwen2.5-coder:14b": {
            "provider": "ollama", "model_id": "qwen2.5-coder:14b", "context_window": 32768,
            "supports_tools": True, "supports_vision": False, "tags": ["local"], "installed": True,
        },
        "llama3.1:8b": {
            "provider": "ollama", "model_id": "llama3.1:8b", "context_window": 131072,
            "supports_tools": True, "supports_vision": False, "tags": ["local"], "installed": True,
        },
    })

    assert registry.merge_catalog() == 1
    assert "qwen2.5-coder:14b" not in registry.models

    discovered = registry.models["llama3.1:8b"]
    assert discovered.source == "ollama"
    assert discovered.provider_type == "ollama"
    assert discovered.installed is True
    assert discovered.context_window == 131072
    assert discovered.supports_tools is True


def test_discovered_model_is_usable_in_agent_config(monkeypatch):
    """Модель из каталога резолвится оркестратором так же, как курируемая из YAML."""
    registry = ModelRegistry()
    registry._raw_config = {"providers": {"ollama": {"type": "ollama"}}}
    registry.models = {}
    registry.routing = {"coder": []}

    monkeypatch.setattr(llm_manager, "ensure_catalog_table", lambda: None)
    monkeypatch.setattr(llm_manager, "load_catalog_entries", lambda: {
        "llama3.1:8b": {
            "provider": "ollama", "model_id": "llama3.1:8b", "context_window": 131072,
            "supports_tools": True, "supports_vision": False, "tags": ["local"], "installed": True,
        },
    })
    registry.merge_catalog()

    model = registry.models["llama3.1:8b"]
    model.available = True  # так его выставит discover_availability

    resolved = registry.resolve_model("coder", model_overrides=["llama3.1:8b"])
    assert resolved is not None and resolved.name == "llama3.1:8b"
