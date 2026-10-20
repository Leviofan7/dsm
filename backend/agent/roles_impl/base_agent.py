import json
import logging
import re
from typing import AsyncGenerator

logger = logging.getLogger("contextus.base_agent")

#: Один корректирующий раунд: больше — риск зацикливания на пересказе.
_RETRY_LIMIT = 1

#: Явная просьба действовать. Намеренно УЗКИЙ список: «сделай/делай» слишком часто
#: относятся к размытой постановке, где уточняющий вопрос уместен. А «выполняй»,
#: «не спрашивай», «ищи сам» — это уже команда, и вопросы вместо действия = саботаж.
_EXPLICIT_ACTION_RE = re.compile(
    r"выполняй|не\s+спрашивай|ищи\s+сам|сам\s+ищи|разберись|действуй|запускай|погнали|"
    r"просто\s+сделай|хватит\s+вопросов",
    re.IGNORECASE,
)


def explicit_action_requested(query: str | None) -> bool:
    """Пользователь прямо требует действий, а не уточнений."""
    return bool(_EXPLICIT_ACTION_RE.search(query or ""))


class BaseAgent:
    """
    Базовый класс для всех интерактивных агентов (Doorman, Planner, Worker).
    Инкапсулирует ReAct-цикл (Think -> Act -> Observe).

    Промпт собирается из ТРЁХ частей, а не из одного `system_instruction`:
      1) identity-контекст системы (services/identity.py) — где мы, что умеем, границы;
      2) инструкция роли;
      3) анти-отказ: что делать вместо «я не могу».
    Раньше был только (2). Именно поэтому швейцар на «скачай видео» отвечал «у меня нет
    доступа к файловой системе»: у него в промпте не было ни карты системы, ни агентов,
    ни запрета отказывать (в Telegram-пути такой запрет был, в веб-пути — нет).
    """

    def __init__(self, llm_manager, role_cfg: dict, model_entry, tools: list):
        self.llm = llm_manager
        self.role_cfg = role_cfg
        self.model = model_entry
        self.tools = tools
        #: Итог прогона для телеметрии: success | delegated | stub_response | failure | timeout
        self.final_status = "success"

    # ── сборка промпта ───────────────────────────────────────────

    def _tool_names(self) -> set[str]:
        """Имена инструментов в обоих форматах: Anthropic (`name`) и OpenAI (`function.name`)."""
        names: set[str] = set()
        for tool in self.tools or []:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name") or (tool.get("function") or {}).get("name")
            if name:
                names.add(str(name))
        return names

    def _anti_refusal(self) -> str:
        """
        Запрет отказов, привязанный к реальным возможностям агента.

        Обещать «у тебя есть инструменты» агенту без инструментов — врать; поэтому текст
        зависит от того, что реально передано в `tools`.
        """
        names = self._tool_names()
        if "delegate_task" in names:
            return (
                "=== ЗАПРЕТ ОТКАЗОВ ===\n"
                "Ты не выполняешь задачу сам — ты передаёшь её специализированному агенту. "
                "СТРОГО ЗАПРЕЩЕНО отвечать «я не могу», «у меня нет доступа», "
                "«это технически невозможно», «я языковая модель».\n"
                "Если задача не про приветствие или small talk — вызови `delegate_task` "
                "и передай полное ТЗ. Если не хватает деталей — задай 1–2 вопроса О ЗАДАЧЕ, "
                "но не отказывайся от неё.\n"
                "ВАЖНО: если пользователь ЯВНО просит действовать («выполняй», «делай», "
                "«ищи сам», «разберись», «не спрашивай») — НЕ задавай уточняющих вопросов. "
                "Делегируй с разумными допущениями и перечисли их в `structured_prompt`.\n"
                "======================="
            )
        if names:
            return (
                "=== ЗАПРЕТ ОТКАЗОВ ===\n"
                "У тебя есть инструменты — используй их вместо фраз «я не могу», "
                "«у меня нет доступа», «я языковая модель». Если инструмент недоступен, "
                "скажи об этом прямо и назови, какой именно.\n"
                "======================="
            )
        return (
            "=== ГРАНИЦА ВОЗМОЖНОСТЕЙ ===\n"
            "Инструментов тебе не выдано. Не описывай действия, которые якобы выполняешь; "
            "честно скажи, что для этой задачи нужен агент с инструментами.\n"
            "============================"
        )

    def build_system_prompt(self) -> str:
        from services.identity import identity_block

        role_instruction = self.role_cfg.get("system_instruction", "Ты — ИИ-агент.")
        return "\n\n".join(
            part for part in (
                identity_block(include_agents=("delegate_task" in self._tool_names())),
                role_instruction,
                self._anti_refusal(),
            ) if part
        )

    # ── основной цикл ────────────────────────────────────────────

    async def run_stream(self, query: str, history: list) -> AsyncGenerator[str, None]:
        """
        Основной цикл агента. Возвращает SSE-события.
        """
        messages = [{"role": "system", "content": self.build_system_prompt()}]

        for msg in history:
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

        if query:
            messages.append({"role": "user", "content": query})

        can_delegate = "delegate_task" in self._tool_names()
        force_action = can_delegate and explicit_action_requested(query)
        corrections = 0
        max_rounds = 10

        for _round in range(max_rounds):
            yield f'data: {json.dumps({"type": "step", "step": "think", "message": f"Думает ({self.model.name})..."})}\n\n'

            response = await self.llm._chat(self.model, messages, tools=self.tools)
            if not response:
                self.final_status = "failure"
                yield f'data: {json.dumps({"type": "error", "message": "Модель не вернула ответ"})}\n\n'
                return

            tool_calls = self.llm._extract_tool_calls(self.model, response)

            if not tool_calls:
                # Финальный текстовый ответ
                text = self.llm._extract_text(self.model, response)
                logger.info(f"BaseAgent _extract_text result: {repr(text)}")

                # Отказ модели — это НЕ успех. В веб-пути раньше он уходил как обычный result,
                # журнал писал «success», и self_review показывал «отклонений нет».
                is_stub = bool(text) and self.llm._detect_stub(text)

                # Две причины попросить модель переделать ответ:
                #   1) она отказалась (заглушка);
                #   2) пользователь прямо велел действовать, а она задаёт вопросы.
                # Второй случай — не «мягкое пожелание»: на «выполняй задачу» ответ
                # «уточню один момент» и есть тот саботаж, на который жаловался владелец.
                retry_reason = None
                if corrections < _RETRY_LIMIT:
                    if is_stub and can_delegate:
                        retry_reason = "stub"
                    elif force_action:
                        retry_reason = "action"

                if retry_reason:
                    corrections += 1
                    step = "stub_retry" if retry_reason == "stub" else "force_action_retry"
                    logger.warning(f"  🚫 [{self.model.name}] корректирующий раунд: {retry_reason}")
                    yield f'data: {json.dumps({"type": "step", "step": step, "message": ("Модель отказала — требую делегирование" if retry_reason == "stub" else "Пользователь велел действовать — требую делегирование")})}\n\n'
                    messages.append(self.llm._build_assistant_msg(self.model, response))
                    if retry_reason == "stub":
                        correction = (
                            "[СИСТЕМНАЯ КОМАНДА] Предыдущий ответ — отказ, а это запрещено. "
                            "Ты не выполняешь задачу сам. Вызови инструмент `delegate_task`: "
                            "`target_agent` из списка доступных агентов, `complexity` (light|heavy) "
                            "и `structured_prompt` с полным ТЗ для агента."
                        )
                    else:
                        correction = (
                            "[СИСТЕМНАЯ КОМАНДА] Пользователь ЯВНО просит действовать, а не уточнять. "
                            "Уточняющие вопросы запрещены. Немедленно вызови `delegate_task`: "
                            "`target_agent` из списка, `complexity` (light|heavy) и `structured_prompt` "
                            "с разумными допущениями — перечисли их в ТЗ. Текст в чат не пиши."
                        )
                    messages.append({"role": "user", "content": correction})
                    continue

                if is_stub:
                    self.final_status = "stub_response"
                    yield f'data: {json.dumps({"type": "step", "step": "stub_response", "message": "Модель ответила отказом вместо выполнения задачи"})}\n\n'
                elif force_action:
                    # Просили действовать — модель ограничилась вопросами. Это не «успех».
                    self.final_status = "no_delegation"
                    yield f'data: {json.dumps({"type": "step", "step": "no_delegation", "message": "Пользователь просил действовать, но задача не делегирована"})}\n\n'

                yield f'data: {json.dumps({"type": "result", "content": text})}\n\n'
                return

            # Обработка тул-коллов
            messages.append(self.llm._build_assistant_msg(self.model, response))

            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = tc.get("arguments", {})

                yield f'data: {json.dumps({"type": "step", "step": f"tool_{tool_name}", "message": f"Вызов: {tool_name}(...)"})}\n\n'

                # Специальный перехват для delegate_task
                if tool_name == "delegate_task":
                    yield f'data: {json.dumps({"type": "delegation", "agent": tool_args.get("target_agent"), "complexity": tool_args.get("complexity"), "prompt": tool_args.get("structured_prompt")})}\n\n'
                    self.final_status = "delegated"
                    return  # Прерываем цикл Doorman'а, управление возвращается в llm_manager

                try:
                    result = await self.llm.mcp.call_tool(tool_name, tool_args)
                    messages.append(self.llm._build_tool_result_msg(self.model, tc.get("id", tool_name), tool_name, str(result)))
                except Exception as e:
                    messages.append(self.llm._build_tool_result_msg(self.model, tc.get("id", tool_name), tool_name, f"Error: {e}"))

        # Раунды исчерпаны без финального ответа
        self.final_status = "timeout"
        yield f'data: {json.dumps({"type": "error", "message": "Агент не завершил задачу за отведённые раунды"})}\n\n'
