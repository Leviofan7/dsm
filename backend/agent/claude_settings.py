"""
claude_settings.py — Утилита для программного переключения модели в Claude Code.

Обновляет ~/.claude/settings.json для смены провайдера / модели
в зависимости от сложности задачи, определённой Doorman-ом.

ВАЖНО: Claude Code не читает произвольное поле "provider" в settings.json.
Провайдер определяется через блок "env" с переменными
ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL.
Поэтому каждый пресет теперь описывает именно эти переменные.
"""

import json
import os
import logging
from pathlib import Path

logger = logging.getLogger("contextus.claude_settings")

_CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Базовые URL известных провайдеров.
#
# ФАКТ-ЧЕК (проверено по документации на 06.07.2026):
#   - DeepSeek отдаёт нативный Anthropic-совместимый эндпоинт /anthropic.
#     Прокси НЕ нужен, подключается прямым base_url.
#   - Ollama (>= 0.14.0, с 16.01.2026) отдаёт нативный Anthropic-совместимый
#     /v1/messages прямо на своём обычном порту 11434. Прокси НЕ нужен.
#     ВАЖНО: base_url без суффикса /v1 — Anthropic SDK сам добавляет /v1/messages.
#   - Gemini отдаёт ТОЛЬКО OpenAI-совместимый эндпоинт (/v1beta/openai).
#     Anthropic-формат не понимает вообще, поэтому это единственный
#     пресет, которому реально нужен LiteLLM (или аналог) как переводчик.
_MODEL_PRESETS = {
    "anthropic": {
        "model": "claude-sonnet-5",
        "base_url": None,  # None -> используем дефолтный api.anthropic.com, ключ не трогаем
        "auth_env": "ANTHROPIC_API_KEY",
        "small_fast_model": "claude-haiku-4-5-20251001",
    },
    "deepseek": {
        # deepseek-chat / deepseek-reasoner депрекейтятся 24.07.2026 —
        # актуальные имена деталей deepseek-v4-pro (тяжёлый) / deepseek-v4-flash (быстрый).
        "model": "deepseek-v4-pro[1m]",
        "base_url": "https://api.deepseek.com/anthropic",  # нативный, без прокси
        "auth_env": "DEEPSEEK_API_KEY",
        "small_fast_model": "deepseek-v4-flash",
    },
    "gemini": {
        "model": "gemini-2.5-flash",
        # У Gemini НЕТ анthropic-совместимого эндпоинта — только OpenAI (/v1beta/openai).
        # Поэтому единственный рабочий путь — через LiteLLM на localhost:4000,
        # который сам сконфигурирован слать запросы в Gemini в OpenAI-формате.
        "base_url": "http://localhost:4000",
        "auth_env": "GEMINI_API_KEY",
        "small_fast_model": "gemini-2.5-flash",
    },
    "ollama": {
        "model": "qwen2.5-coder:32b",  # подтверждено `ollama list` на вашей машине
        "base_url": "http://localhost:11434",  # нативный Anthropic-эндпоинт, без /v1, без прокси
        "auth_env": None,  # не нужен реальный ключ, но переменная должна быть непустой
        "small_fast_model": "qwen2.5-coder:32b",
    },
}


def read_claude_settings() -> dict:
    """Читает текущий settings.json. Если файла нет — возвращает пустой dict."""
    if not _CLAUDE_SETTINGS_PATH.exists():
        logger.info(f"  📄 {_CLAUDE_SETTINGS_PATH} не найден. Будет создан.")
        return {}
    try:
        with open(_CLAUDE_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"  ⚠️ Ошибка чтения {_CLAUDE_SETTINGS_PATH}: {e}")
        return {}


def write_claude_settings(settings: dict):
    """Записывает settings.json, создавая директорию если нужно."""
    _CLAUDE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CLAUDE_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
    logger.info(f"  ✅ Записано: {_CLAUDE_SETTINGS_PATH}")


def switch_claude_model(preset_name: str) -> dict:
    """
    Переключает модель Claude Code на указанный пресет, корректно
    выставляя env-блок, который Claude Code реально читает.

    Args:
        preset_name: 'anthropic' | 'deepseek' | 'gemini' | 'ollama'

    Returns:
        Обновлённый settings dict, либо {} при ошибке/недостатке ключа.
    """
    if preset_name not in _MODEL_PRESETS:
        available = list(_MODEL_PRESETS.keys())
        logger.error(f"  ❌ Неизвестный пресет: '{preset_name}'. Доступные: {available}")
        return {}

    preset = _MODEL_PRESETS[preset_name]

    # Проверяем наличие ключа, если он требуется
    auth_token = None
    if preset["auth_env"]:
        auth_token = os.getenv(preset["auth_env"], "")
        if not auth_token:
            logger.warning(
                f"  ⚠️ Пресет '{preset_name}' требует переменную {preset['auth_env']}, "
                f"но она не установлена. Переключение отменено."
            )
            return {}
    else:
        # Ollama и подобные — реальный ключ не нужен, но переменная должна быть непустой строкой
        auth_token = "local-no-key-required"

    settings = read_claude_settings()
    settings.setdefault("env", {})

    if preset_name == "anthropic":
        # Возврат к дефолтному Anthropic API: убираем override base_url,
        # чтобы Claude Code сам сходил на api.anthropic.com.
        settings["env"].pop("ANTHROPIC_BASE_URL", None)
        settings["env"]["ANTHROPIC_API_KEY"] = auth_token
        settings["env"].pop("ANTHROPIC_AUTH_TOKEN", None)
    else:
        settings["env"]["ANTHROPIC_BASE_URL"] = preset["base_url"]
        settings["env"]["ANTHROPIC_AUTH_TOKEN"] = auth_token
        # Пустой ANTHROPIC_API_KEY, чтобы не было конфликта авторизации
        settings["env"]["ANTHROPIC_API_KEY"] = ""

    settings["env"]["ANTHROPIC_MODEL"] = preset["model"]
    settings["env"]["ANTHROPIC_SMALL_FAST_MODEL"] = preset["small_fast_model"]

    # Дополнительно перекрываем алиасы opus/sonnet/haiku. Это защищает от ситуации,
    # когда Claude Code (или сам пользователь через /model opus) запросит модель
    # по алиасу — без этого запрос уйдёт на дефолтный Anthropic, а не на текущий провайдер.
    settings["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] = preset["model"]
    settings["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] = preset["model"]
    settings["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = preset["small_fast_model"]

    # Отключаем attribution header — критично для локальных моделей (KV-cache),
    # но не мешает и облачным.
    settings["env"]["CLAUDE_CODE_ATTRIBUTION_HEADER"] = "0"

    # Верхнеуровневый "model" — это дефолтный алиас модели для / model picker,
    # оставляем как подсказку, но реальный роутинг определяет ANTHROPIC_MODEL выше.
    settings["model"] = preset["model"]

    write_claude_settings(settings)
    logger.info(f"  🔄 Claude Code переключён на: {preset_name} ({preset['model']})")
    return settings


def auto_switch_by_complexity(complexity: str, task_type: str = "general") -> dict | None:
    """
    Автоматически выбирает модель для Claude Code по сложности задачи.

    - low complexity      → Ollama (локально, бесплатно и быстро для простых правок)
    - high + code         → DeepSeek (дешёвый мощный кодер) → fallback Anthropic
    - high + planning      → Anthropic (лучше держит контекст и рассуждения) → fallback DeepSeek
    - high + прочее        → Gemini как дешёвый универсальный вариант
    - если для high ничего не сработало → fallback на Ollama, чтобы задача
      не осталась вовсе без модели (с предупреждением о рисках качества)
    """
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    if complexity == "low":
        result = switch_claude_model("ollama")
        if result:
            return result
        # Если локальный сервер недоступен/не настроен — не блокируем работу,
        # остаёмся на текущих настройках молча.
        logger.warning("  ⚠️ Ollama недоступна для low-complexity задачи, оставляем текущую модель")
        return None

    if complexity != "high":
        return None

    if task_type in ("code", "coding"):
        if deepseek_key:
            result = switch_claude_model("deepseek")
            if result:
                return result
        if anthropic_key:
            return switch_claude_model("anthropic")
    elif task_type in ("planning", "research"):
        if anthropic_key:
            result = switch_claude_model("anthropic")
            if result:
                return result
        if deepseek_key:
            return switch_claude_model("deepseek")
    else:
        if gemini_key:
            result = switch_claude_model("gemini")
            if result:
                return result

    # Ничего из облака не доступно — лучше локальная модель, чем никакой
    logger.warning(
        "  ⚠️ Нет доступных API-ключей для high-complexity задачи, "
        "пробуем fallback на Ollama (качество может быть ниже)"
    )
    return switch_claude_model("ollama")
