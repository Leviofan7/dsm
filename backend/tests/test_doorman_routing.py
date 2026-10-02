"""
Маршрутизатор не должен отвечать по памяти и не должен быть слабой моделью.

Инцидент 26.09: «Новости по прилетам НПЗ в России и оценка ущерба» не попал ни в
keyword-детектор (там не было слов «найди/поищи»), ни в категорию research — запрос ушёл
в general, и на нём отвечала локальная deepseek-r1:8b, выдумавшая «сжиженные газовые
конструкции» и «удары по ОДКБ».

Стражи:
  1) новостные запросы уходят в research быстрым детектором, БЕЗ обращения к LLM;
  2) прочие запросы остаются на усмотрение LLM (детектор не разрастается);
  3) маршрутизатор берёт модель тем же резолвером, что остальные роли;
  4) первой моделью маршрутизатора стоит облачная (не слабая локальная);
  5) промпт классификатора запрещает отвечать и требует research для новостей.
"""

import asyncio
import inspect
import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from agent import llm_manager  # noqa: E402

NEWS_QUERIES = [
    "Новости по прилетам НПЗ в России и оценка ущерба",
    "удары по нпз сегодня украина нанесла дронами",
    "что случилось в Москве ночью",
    "сводка за неделю",
    "последние новости Украины",
]

NON_NEWS_QUERIES = [
    "напиши функцию на Python для сортировки",
    "привет",
    "как дела?",
    "почини ошибку в этом коде",
]


def _bare_manager():
    """Без __init__: быстрый детектор обязан сработать ДО обращения к реестру."""
    return llm_manager.LLMManager.__new__(llm_manager.LLMManager)


def test_news_queries_go_to_research_without_llm():
    for query in NEWS_QUERIES:
        result = asyncio.run(_bare_manager().route_intent(query))
        assert result["task_type"] == "research", (
            f"новостной запрос не ушёл в research: {query} → {result}"
        )


def test_non_news_queries_are_left_to_llm():
    """
    У «голого» менеджера нет реестра: если детектор зацепит такой запрос, тест упадёт
    AttributeError'ом — это и есть проверка, что лишнее не перехватываем.
    """
    for query in NON_NEWS_QUERIES:
        with pytest.raises(AttributeError):
            asyncio.run(_bare_manager().route_intent(query))


def test_router_uses_agent_config_resolver():
    src = inspect.getsource(llm_manager.LLMManager.route_intent)
    assert "resolve_agent_decision(" in src, (
        "route_intent снова обходит резолвер — маршрутизатор вернётся на слабую локальную модель"
    )


def test_router_prefers_cloud_model_first():
    cfg = json.loads((BACKEND_DIR / "config" / "agent_configs.json").read_text(encoding="utf-8"))
    models = cfg["doorman"]["light"]["models"]
    assert models[0] == "deepseek-chat", (
        f"первой моделью маршрутизатора должна быть облачная, не слабая локальная: {models}"
    )


def test_classifier_prompt_forbids_answering_and_forces_research():
    src = inspect.getsource(llm_manager.LLMManager.route_intent)
    assert "Ты НЕ отвечаешь на вопрос" in src
    assert "Новости, свежие события" in src
    assert "выбирай 'research'" in src
