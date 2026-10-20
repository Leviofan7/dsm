"""
Identity-контекст и поведение швейцара: знает, где он, кто он и что может.

Повод (запрос 15.10 «скачай видео… ищи выход, выполняй задачу»): швейцар ответил
«я не могу физически скачать файл… у меня нет доступа к файловой системе» и НЕ делегировал.
Причины, каждая из которых здесь закреплена стражем:
  1. в промпте не было карты системы/агентов (identity) — модель не знала, что умеет система;
  2. не было запрета отказов в веб-пути (в Telegram-пути он был) — асимметрия путей;
  3. отказ не детектировался и уходил как «success», поэтому self_review показывал «норму»;
  4. `delegate_task` был mock-схемой без имён агентов — модель не знала, кому передавать.
"""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from agent import llm_manager  # noqa: E402
from agent.roles_impl.base_agent import BaseAgent  # noqa: E402
from services import identity  # noqa: E402
from services import review as review_service  # noqa: E402

STUB_TEXT = "Я не могу физически скачать файл — у меня нет доступа к файловой системе."


class FakeLLM:
    """Минимальный двойник LLMManager: только то, что нужно ReAct-циклу."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    async def _chat(self, model, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        return self.responses.pop(0)

    def _extract_tool_calls(self, model, response):
        return response.get("tool_calls", [])

    def _extract_text(self, model, response):
        return response.get("text", "")

    def _build_assistant_msg(self, model, response):
        return {"role": "assistant", "content": response.get("text", "")}

    def _detect_stub(self, text):
        # Настоящая реализация: «не имею доступа»/«у меня нет доступа» и т.п.
        return llm_manager.LLMManager._detect_stub(self, text)


def _events(raw_events: list[str]) -> list[dict]:
    out = []
    for e in raw_events:
        try:
            out.append(json.loads(e.replace("data: ", "").strip()))
        except Exception:
            continue
    return out


async def _run(agent: BaseAgent, query="скачай видео", history=None) -> tuple[list[dict], str]:
    raw = [event async for event in agent.run_stream(query, history or [])]
    return _events(raw), agent.final_status


@pytest.fixture
def model():
    return SimpleNamespace(name="fake-model", provider_type="openai")


# ── identity ───────────────────────────────────────────────────────

def test_identity_states_capabilities_and_boundaries():
    block = identity.identity_block()

    assert "Contextus" in block, "агент обязан знать, в какой системе работает"
    assert "Браузер" in block and "Артефакты" in block, "карта возможностей обязательна"
    # Честная граница: файл нельзя «положить на устройство», его скачивают из артефактов.
    assert "устройстве пользователя" in block
    assert "артефакт" in block.lower()
    assert "delegate_task" not in block, "ростер добавляется только делегирующим ролям"


def test_identity_includes_roster_only_for_delegating_roles():
    with_agents = identity.identity_block(include_agents=True)
    assert "КОМУ МОЖНО ПЕРЕДАТЬ ЗАДАЧУ" in with_agents
    assert "`coder`" in with_agents and "`web_researcher`" in with_agents


def test_roster_excludes_service_roles_and_doorman():
    ids = identity.delegatable_ids()

    assert "coder" in ids and "web_researcher" in ids
    assert "doorman" not in ids, "швейцар не может делегировать сам себе — это цикл"
    assert "meta_analyst" not in ids and "supervisor_14b" not in ids, "служебные роли не получают задачи"
    # Ростер строится из реальных файлов ролей, а не из копии в коде
    assert set(ids) <= {p.stem for p in identity.ROLES_DIR.glob("*.yaml")}


# ── delegate_task: реальный ростер вместо mock-схемы ───────────────

def test_delegate_tool_lists_real_agents_openai_shape():
    tools = llm_manager.build_delegate_tool("openai")
    fn = tools[0]["function"]

    assert fn["name"] == "delegate_task"
    assert "coder" in fn["description"], "модель должна видеть, кому передавать"
    props = fn["parameters"]["properties"]
    assert set(props["target_agent"]["enum"]) == set(identity.delegatable_ids())
    assert props["complexity"]["enum"] == ["light", "heavy"]


def test_delegate_tool_anthropic_shape():
    tools = llm_manager.build_delegate_tool("anthropic")
    assert tools[0]["name"] == "delegate_task"
    assert "input_schema" in tools[0]
    assert tools[0]["input_schema"]["required"] == [
        "target_agent", "complexity", "structured_prompt",
    ]


# ── BaseAgent: промпт ─────────────────────────────────────────────

def test_system_prompt_has_identity_role_and_anti_refusal(model):
    role = {"system_instruction": "Ты — системный роутер (Doorman)."}
    agent = BaseAgent(FakeLLM([]), role, model, llm_manager.build_delegate_tool("openai"))

    prompt = agent.build_system_prompt()

    assert "ЧТО ЭТО ЗА СИСТЕМА" in prompt, "identity обязана быть в промпте"
    assert "Ты — системный роутер" in prompt, "инструкция роли не потеряна"
    assert "ЗАПРЕТ ОТКАЗОВ" in prompt and "delegate_task" in prompt


def test_system_prompt_without_tools_does_not_lie_about_capabilities(model):
    agent = BaseAgent(FakeLLM([]), {"system_instruction": "роль"}, model, [])
    prompt = agent.build_system_prompt()

    assert "ГРАНИЦА ВОЗМОЖНОСТЕЙ" in prompt
    assert "ЗАПРЕТ ОТКАЗОВ" not in prompt, "нельзя обещать инструменты тому, у кого их нет"


# ── BaseAgent: отказ -> корректирующий раунд -> делегирование ─────

def test_refusal_triggers_retry_and_delegation(model):
    fake = FakeLLM([
        {"text": STUB_TEXT},  # отказ
        {"tool_calls": [{
            "name": "delegate_task",
            "arguments": {
                "target_agent": "web_researcher",
                "complexity": "light",
                "structured_prompt": "найди способ получить видео и сохрани в артефакты",
            },
        }]},
    ])
    agent = BaseAgent(fake, {"system_instruction": "роутер"}, model,
                      llm_manager.build_delegate_tool("openai"))

    events, status = asyncio.run(_run(agent))

    steps = [e.get("step") for e in events if e.get("type") == "step"]
    assert "stub_retry" in steps, "первый отказ обязан вызвать корректирующий раунд"
    delegation = next((e for e in events if e.get("type") == "delegation"), None)
    assert delegation is not None, "после корректировки обязано случиться делегирование"
    assert delegation["agent"] == "web_researcher"
    assert status == "delegated"

    # Корректирующее сообщение действительно ушло модели
    second_call = fake.calls[1]
    assert any("СИСТЕМНАЯ КОМАНДА" in str(m.get("content")) for m in second_call)


def test_persistent_refusal_is_marked_as_stub_not_success(model):
    """Отказ без возможности делегировать не должен выглядеть как успешный прогон."""
    fake = FakeLLM([{"text": STUB_TEXT}])
    agent = BaseAgent(fake, {"system_instruction": "роль"}, model, [])

    events, status = asyncio.run(_run(agent))

    steps = [e.get("step") for e in events if e.get("type") == "step"]
    assert "stub_response" in steps
    assert status == "stub_response", "иначе журнал запишет success и self_review не увидит проблему"


def test_second_refusal_after_retry_is_still_stub(model):
    fake = FakeLLM([
        {"text": STUB_TEXT},
        {"text": "Я языковая модель, у меня нет доступа к интернету."},  # снова отказ
    ])
    agent = BaseAgent(fake, {"system_instruction": "роутер"}, model,
                      llm_manager.build_delegate_tool("openai"))

    _, status = asyncio.run(_run(agent))

    assert status == "stub_response"
    assert len(fake.calls) == 2, "ровно один корректирующий раунд, без зацикливания"


# ── Явная просьба действовать ─────────────────────────────────────

@pytest.mark.parametrize("text", [
    "выполняй задачу",
    "не спрашивай, делай",
    "ищи сам и разберись",
    "просто сделай",
    # Прямой императив — это задача, а не small talk (реальный кейс 21:25)
    "скачай видео",
    "найди курс доллара",
    "собери отчёт по продажам",
])
def test_explicit_action_is_detected(text):
    from agent.roles_impl.base_agent import explicit_action_requested
    assert explicit_action_requested(text) is True


@pytest.mark.parametrize("text", [
    "привет",
    "как дела?",
    "что ты умеешь?",
    "как скачать видео?",          # вопрос, а не команда
    "можно ли найти курс доллара?",
])
def test_vague_requests_are_not_forced(text):
    from agent.roles_impl.base_agent import explicit_action_requested
    assert explicit_action_requested(text) is False


def test_direct_task_command_without_delegation_triggers_retry(model):
    """
    Реальный случай 21:25: «скачай видео» → швейцар ответил сам («я не смогу скачать…»),
    без делегирования. Это не «заглушка» по словарю, но задача не передана — значит
    обязаны потребовать delegate_task, а не записывать success.
    """
    fake = FakeLLM([
        {"text": "Я не смогу скачать видео с YouTube — у системы нет инструмента для медиафайлов."},
        {"tool_calls": [{
            "name": "delegate_task",
            "arguments": {
                "target_agent": "web_researcher",
                "complexity": "light",
                "structured_prompt": "оцени возможность скачивания и предложи рабочий путь",
            },
        }]},
    ])
    agent = BaseAgent(fake, {"system_instruction": "роутер"}, model,
                      llm_manager.build_delegate_tool("openai"))

    events, status = asyncio.run(_run(agent, query="скачай видео"))

    steps = [e.get("step") for e in events if e.get("type") == "step"]
    assert "force_action_retry" in steps
    assert status == "delegated"


def test_explicit_action_forces_delegation(model):
    """«выполняй» + вопросы вместо действия = ещё один корректирующий раунд."""
    fake = FakeLLM([
        {"text": "Уточню один момент, и сразу передам в работу — без него задачу невозможно выполнить."},
        {"tool_calls": [{
            "name": "delegate_task",
            "arguments": {
                "target_agent": "web_researcher",
                "complexity": "light",
                "structured_prompt": "проверь доступные способы и сохрани результат в артефакты",
            },
        }]},
    ])
    agent = BaseAgent(fake, {"system_instruction": "роутер"}, model,
                      llm_manager.build_delegate_tool("openai"))

    events, status = asyncio.run(
        _run(agent, query="не спрашивай, выполняй задачу")
    )

    steps = [e.get("step") for e in events if e.get("type") == "step"]
    assert "force_action_retry" in steps
    assert status == "delegated"
    assert any(
        "Уточняющие вопросы" in str(m.get("content"))
        for m in fake.calls[1]
    )


def test_explicit_action_without_delegation_is_not_success(model):
    fake = FakeLLM([
        {"text": "Уточню один момент."},
        {"text": "Всё равно уточню: какой именно файл нужен?"},
    ])
    agent = BaseAgent(fake, {"system_instruction": "роутер"}, model,
                      llm_manager.build_delegate_tool("openai"))

    events, status = asyncio.run(_run(agent, query="выполняй задачу"))

    steps = [e.get("step") for e in events if e.get("type") == "step"]
    assert "no_delegation" in steps
    assert status == "no_delegation", "нельзя записывать «успех», если задачу просили выполнять"


# ── review: отказ внутри «completed» виден ───────────────────────

def test_stub_trace_is_not_reported_as_no_failure():
    """
    Реальный случай 15.10: задача completed, трейс doorman_chat, в ответе «я не могу».
    classификация обязана увидеть проблему, а не «отклонений нет».
    """
    mode = review_service.classify_failure(
        "completed", {"statuses": ["stub_response"], "traces": 1, "tool_calls": 0}, []
    )
    assert mode == "stub_response"

    healthy = review_service.classify_failure(
        "completed", {"statuses": ["delegated"], "traces": 1, "tool_calls": 0}, []
    )
    assert healthy == "none"


def test_no_delegation_trace_is_visible_as_declined_tools():
    mode = review_service.classify_failure(
        "completed", {"statuses": ["no_delegation"], "traces": 1, "tool_calls": 0}, []
    )
    assert mode == "model_declined_tools"
