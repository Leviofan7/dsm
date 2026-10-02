"""
role_permissions.py — permission-envelope роли (Фаза A).

Поля живут в `roles/<role>.yaml` сразу после `planner`, в каноническом порядке:

    write_scope: none | project | workspace:<path>
    intent_access: write_own | write_any
    can_create_intent: <bool>

Почему явно у всех ролей: UI-панель не отдаёт эти поля (GET /roles/{id} возвращает
только name/description/planner/tools/system_instruction), поэтому файл на диске —
единственное место, где permission-envelope роли виден целиком. Отсюда же требование
канонического порядка: файлы должны быть diff-абельны друг с другом.

Философия — fail-closed: файл источник истины, но отсутствие/битое значение не роняет
систему (роли hot-reload) и не расширяет права: включается безопасный дефолт + warning.
`any` (пиши куда угодно) сознательно НЕ вводится.
"""

import logging
import os
import re
import yaml
from pathlib import Path

logger = logging.getLogger("contextus.role_permissions")

# ── Расположение ──────────────────────────────────────────────────
_ENV_ROLES_DIR = os.getenv("ROLES_DIR")
ROLES_DIR = Path(_ENV_ROLES_DIR).resolve() if _ENV_ROLES_DIR else (
    Path(__file__).parent.parent.resolve() / "roles"
)

ROLE_NAME_RE = re.compile(r"^[a-z0-9_][a-z0-9_\-]*$")

# ── Значения ──────────────────────────────────────────────────────
WRITE_SCOPE_NONE = "none"
WRITE_SCOPE_PROJECT = "project"
WORKSPACE_PREFIX = "workspace:"

VALID_INTENT_ACCESS = ("write_own", "write_any")

#: канонический порядок и место вставки в YAML роли
PERMISSION_FIELDS = ("write_scope", "intent_access", "can_create_intent")
INSERT_AFTER_FIELD = "planner"

# ── Классы write-тулов (Фаза A.1) ────────────────────────────────
# Прямая запись меняет ФС сразу (run_claude_coder тоже правит файлы в target_dir);
# «запрос на запись» инициирует цепочку одобрения → sandbox. Оба класса — write-тулы:
# роль с write_scope=none не получает ни одного из них.
DIRECT_WRITE_TOOLS = frozenset({"write_file", "run_terminal_command", "run_claude_coder"})
GATED_WRITE_TOOLS = frozenset({"request_command_execution", "request_diff_apply"})
WRITE_TOOLS = DIRECT_WRITE_TOOLS | GATED_WRITE_TOOLS

# ── Классы intent-тулов (Фаза B) ─────────────────────────────────
#: только чтение — разрешено любому intent_access
INTENT_READ_TOOLS = frozenset({"intent_read", "intent_list"})
#: «своя» запись: инструмент сам дописывает что-то от имени роли (роль знает сервер)
INTENT_OWN_TOOLS = frozenset({"intent_append_journal", "intent_append_vision", "intent_update_step"})
#: структурная запись — только intent_access: write_any
INTENT_STRUCTURAL_TOOLS = frozenset({
    "intent_update_status", "intent_update_overrides", "intent_update_section", "intent_archive",
})
#: создание intent — отдельное право can_create_intent
INTENT_CREATE_TOOL = "intent_create"

#: Ключ в _meta запроса MCP, которым оркестратор передаёт роль вызывающего.
#: Именно _meta (а не аргумент инструмента): FastMCP запрещает параметры с '_',
#: а главное — служебный канал не попадает в схему инструмента, поэтому модель
#: его структурно не видит и не может ни подделать, ни перезаписать.
CALLER_ROLE_META_KEY = "contextus/role"

INTENT_TOOLS = INTENT_READ_TOOLS | INTENT_OWN_TOOLS | INTENT_STRUCTURAL_TOOLS | {INTENT_CREATE_TOOL}

#: fail-closed дефолты (используются только как fallback, не как источник истины)
DEFAULT_PERMISSIONS: dict = {
    "write_scope": WRITE_SCOPE_NONE,
    "intent_access": "write_own",
    "can_create_intent": False,
}


# ── Доступ к файлам ролей ─────────────────────────────────────────

def roles_dir() -> Path:
    """Каталог ролей (читается из модульной переменной — удобно для тестов)."""
    return Path(ROLES_DIR)


def role_path(role: str) -> Path:
    return roles_dir() / f"{role}.yaml"


def list_all_roles() -> list[str]:
    """id всех ролей (имена файлов roles/*.yaml)."""
    root = roles_dir()
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.yaml"))


def load_role_yaml(role: str) -> dict:
    """
    YAML роли. Битый/отсутствующий файл — не исключение, а сигнал: пишем warning
    и возвращаем {} (вызывающая сторона получит fail-closed дефолты).
    """
    if not ROLE_NAME_RE.match(str(role or "")):
        logger.warning(f"⛔ role_permissions: некорректное имя роли {role!r} — permission-поля не читаю")
        return {}

    path = role_path(role)
    if not path.is_file():
        logger.warning(f"⚠️ role_permissions: файл роли не найден ({path}) — fail-closed дефолты")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        logger.warning(f"⚠️ role_permissions: не смог разобрать {path} ({e}) — fail-closed дефолты")
        return {}

    if not isinstance(data, dict):
        logger.warning(f"⚠️ role_permissions: {path} — не словарь, fail-closed дефолты")
        return {}
    return data


def write_role_yaml(role: str, data: dict) -> Path:
    """Канонический writer: тот же yaml.dump, которым пишет UI, — чтобы diff'ы не шумели."""
    path = role_path(role)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)
    return path


# ── Валидация значений ────────────────────────────────────────────

def is_valid_write_scope(value) -> bool:
    if value in (WRITE_SCOPE_NONE, WRITE_SCOPE_PROJECT):
        return True
    return isinstance(value, str) and value.startswith(WORKSPACE_PREFIX) and len(value) > len(WORKSPACE_PREFIX)


def is_valid_intent_access(value) -> bool:
    return value in VALID_INTENT_ACCESS


def is_valid_can_create_intent(value) -> bool:
    return isinstance(value, bool)


# ── Публичное API ─────────────────────────────────────────────────

def get_role_permissions(role: str) -> dict:
    """
    Права роли с fail-closed fallback: поле отсутствует или невалидно → безопасный
    дефолт + warning (не raise: роли hot-reload данные, опечатка не должна ронять бэкенд).
    Фактический отказ при попытке привилегированного действия — задача Фазы B (enforcement).
    """
    data = load_role_yaml(role)

    ws = data.get("write_scope")
    if not is_valid_write_scope(ws):
        if ws is not None:
            logger.warning(f"⚠️ role {role}: write_scope={ws!r} невалидно, fallback на {WRITE_SCOPE_NONE!r}")
        else:
            logger.warning(f"⚠️ role {role}: write_scope отсутствует, fallback на {WRITE_SCOPE_NONE!r}")
        ws = DEFAULT_PERMISSIONS["write_scope"]

    ia = data.get("intent_access")
    if not is_valid_intent_access(ia):
        logger.warning(f"⚠️ role {role}: intent_access={ia!r} невалидно, fallback на 'write_own'")
        ia = DEFAULT_PERMISSIONS["intent_access"]

    cci = data.get("can_create_intent")
    if not is_valid_can_create_intent(cci):
        logger.warning(f"⚠️ role {role}: can_create_intent={cci!r} невалидно, fallback на False")
        cci = DEFAULT_PERMISSIONS["can_create_intent"]

    return {"write_scope": ws, "intent_access": ia, "can_create_intent": cci}


def validate_role_permissions(roles: list[str] | None = None) -> list[str]:
    """
    Проверяет наличие и валидность permission-полей у ролей (по умолчанию — у всех).
    Возвращает список проблем и пишет warnings. Не raise (в стиле validate_role_defaults).
    """
    targets = list(roles) if roles is not None else list_all_roles()
    problems: list[str] = []

    if not targets:
        problems.append(f"role_permissions: в {roles_dir()} не найдено ни одной роли")
    for role in targets:
        data = load_role_yaml(role)
        if not data:
            problems.append(f"[{role}] файл роли не читается — permission-поля не проверить")
            continue
        for field in PERMISSION_FIELDS:
            if field not in data:
                problems.append(f"[{role}] отсутствует обязательное поле '{field}' (сработает fail-closed дефолт)")
        if "write_scope" in data and not is_valid_write_scope(data["write_scope"]):
            problems.append(f"[{role}] write_scope={data['write_scope']!r} (ожидается none | project | workspace:<path>)")
        if "intent_access" in data and not is_valid_intent_access(data["intent_access"]):
            problems.append(f"[{role}] intent_access={data['intent_access']!r} (ожидается write_own | write_any)")
        if "can_create_intent" in data and not is_valid_can_create_intent(data["can_create_intent"]):
            problems.append(f"[{role}] can_create_intent={data['can_create_intent']!r} (ожидается bool)")

    for problem in problems:
        logger.warning(f"  ⚠️ {problem}")
    return problems


# ── Фаза A.1: write_scope → фактическая выдача write-тулов ────────

def effective_write_tools(role: str) -> set[str]:
    """
    write-тулы, разрешённые роли с учётом write_scope. Fail-closed.

    none          → пусто (роль не пишет вообще);
    project       → write-тулы (но запись всё равно требует sandbox_id — отдельный слой);
    workspace:*   → пусто + warning: значение объявлено, enforcement ещё не реализован.
    """
    ws = get_role_permissions(role)["write_scope"]

    if ws == WRITE_SCOPE_PROJECT:
        return set(WRITE_TOOLS)
    if isinstance(ws, str) and ws.startswith(WORKSPACE_PREFIX):
        logger.warning(
            f"⚠️ role {role}: write_scope={ws!r} объявлен, но enforcement workspace-записи "
            f"ещё не реализован → write-тулы не выдаются"
        )
        return set()
    if ws != WRITE_SCOPE_NONE:
        logger.warning(f"⚠️ role {role}: неизвестный write_scope={ws!r} → write-тулы не выдаются")
    return set()


def write_denial_reason(role: str, tool_name: str) -> str | None:
    """
    Причина запрета write-тула для роли (None — запрета нет).
    Нужна для runtime deny + audit в оркестраторе.
    """
    if tool_name not in WRITE_TOOLS:
        return None
    if tool_name in effective_write_tools(role):
        return None
    ws = get_role_permissions(role)["write_scope"]
    return f"role={role} tool={tool_name} write_scope={ws}"


def validate_write_scope_consistency(privileged_whitelist: dict | None = None) -> list[str]:
    """
    Согласованность декларации (write_scope) и фактической выдачи write-тулов.
    Ловит fail-open: write-тул в tools роли или в privileged-whitelist при write_scope=none.

    privileged_whitelist ({role: [tools]}) можно передать явно (тесты); по умолчанию
    подтягивается GATE_TOOLS_BY_ROLE ленивым импортом — чтобы вызов без аргументов
    не давал ложных срабатываний. Если whitelist неизвестен, проверка «project без
    тулов» не выполняется (это единственная ветка, которой он нужен).
    Warning, не raise. Возвращает список проблем.
    """
    if privileged_whitelist is None:
        try:
            from agent.mcp_manager import GATE_TOOLS_BY_ROLE  # локально: без циклического импорта
            privileged_whitelist = dict(GATE_TOOLS_BY_ROLE)
        except Exception as e:
            logger.warning(f"⚠️ GATE_TOOLS_BY_ROLE недоступен ({e}) — проверю только tools роли")
            privileged_whitelist = {}

    whitelist_known = bool(privileged_whitelist)
    whitelist = {str(role): set(tools or []) for role, tools in privileged_whitelist.items()}
    problems: list[str] = []

    for role in list_all_roles():
        data = load_role_yaml(role)
        ws = get_role_permissions(role)["write_scope"]
        role_write = set(data.get("tools") or []) & set(WRITE_TOOLS)
        gate_write = whitelist.get(role, set()) & set(WRITE_TOOLS)

        if ws == WRITE_SCOPE_NONE:
            if role_write:
                problems.append(f"[{role}] write_scope=none, но в tools есть write-тулы: {sorted(role_write)}")
            if gate_write:
                problems.append(f"[{role}] write_scope=none, но в GATE_TOOLS_BY_ROLE есть write-тулы: {sorted(gate_write)}")
        elif ws == WRITE_SCOPE_PROJECT:
            if whitelist_known and not gate_write:
                problems.append(
                    f"[{role}] write_scope=project, но write-тулов нет в GATE_TOOLS_BY_ROLE: роль объявляет запись, а инструментов нет"
                )
        else:
            problems.append(f"[{role}] write_scope={ws!r} — enforcement не реализован, write-тулы не выдаются (fail-closed)")

    for problem in problems:
        logger.warning(f"  ⚠️ {problem}")
    return problems


# ── Фаза B: политика intent-тулов (единый источник для сервера и клиента) ──

def allowed_intent_tools(role: str) -> set[str]:
    """
    intent-тулы, разрешённые роли. Единый источник и для фильтрации выдачи
    (клиент/оркестратор), и для enforcement (MCP-сервер) — чтобы правило не
    дублировалось в двух местах и не разъезжалось.

    write_any → всё; write_own → чтение + «своя» запись (журнал, видение, свой шаг);
    intent_create — отдельное право can_create_intent.
    """
    perms = get_role_permissions(role)
    allowed = set(INTENT_TOOLS) if perms["intent_access"] == "write_any" else set(INTENT_READ_TOOLS | INTENT_OWN_TOOLS)
    if perms["can_create_intent"]:
        allowed.add(INTENT_CREATE_TOOL)
    return allowed


def intent_access_denial(role: str, tool_name: str) -> str | None:
    """Причина запрета intent-тула (None — запрета нет). Правило — одно, см. allowed_intent_tools."""
    if not str(tool_name or "").startswith("intent_"):
        return None
    perms = get_role_permissions(role)
    if tool_name in allowed_intent_tools(role):
        return None
    extra = ""
    if tool_name == INTENT_CREATE_TOOL:
        extra = f" can_create_intent={perms['can_create_intent']}"
    return f"role={role} tool={tool_name} intent_access={perms['intent_access']}{extra}"


def validate_intent_access_consistency() -> list[str]:
    """
    Согласованность декларации (intent_access / can_create_intent) и tools роли.
    Warning, не raise. Возвращает список проблем.
    """
    problems: list[str] = []

    for role in list_all_roles():
        data = load_role_yaml(role)
        perms = get_role_permissions(role)
        declared = set(data.get("tools") or []) & set(INTENT_TOOLS)

        if perms["intent_access"] == "write_own":
            forbidden = declared & (set(INTENT_STRUCTURAL_TOOLS) | {INTENT_CREATE_TOOL})
            if forbidden and not (INTENT_CREATE_TOOL in forbidden and perms["can_create_intent"]):
                problems.append(
                    f"[{role}] intent_access=write_own, но в tools есть структурные intent-тулы: {sorted(forbidden)}"
                )
        elif perms["intent_access"] == "write_any":
            problems.append(f"[{role}] intent_access=write_any — таких ролей пока нет, проверь, что это осознанно")

        if perms["can_create_intent"] and role != "doorman":
            problems.append(f"[{role}] can_create_intent=true, но создавать intent пока разрешено только doorman")

        if INTENT_CREATE_TOOL in declared and not perms["can_create_intent"]:
            problems.append(f"[{role}] в tools есть {INTENT_CREATE_TOOL}, но can_create_intent=false")

    for problem in problems:
        logger.warning(f"  ⚠️ {problem}")
    return problems


def permissions_are_canonical(role: str) -> bool:
    """Поля присутствуют и идут в каноническом порядке сразу после planner."""
    data = load_role_yaml(role)
    keys = list(data.keys())
    present = [k for k in keys if k in PERMISSION_FIELDS]
    if present != list(PERMISSION_FIELDS):
        return False
    first = keys.index(PERMISSION_FIELDS[0])
    expected_prev = INSERT_AFTER_FIELD if INSERT_AFTER_FIELD in keys else None
    if expected_prev is not None:
        return keys[first - 1] == expected_prev
    return True


# ── Ретрофит / создание ролей ─────────────────────────────────────

def insert_permission_fields(data: dict, permissions: dict) -> dict:
    """
    Вставляет три permission-поля в КАНОНИЧЕСКОМ порядке сразу после `planner`.
    Идемпотентно: уже существующие поля сначала удаляются.
    `permissions` может быть неполным — недостающее берётся из fail-closed дефолтов.
    """
    if not isinstance(data, dict):
        raise TypeError("insert_permission_fields ожидает словарь роли")

    values = {**DEFAULT_PERMISSIONS, **{k: permissions[k] for k in PERMISSION_FIELDS if k in (permissions or {})}}
    cleaned = {k: v for k, v in data.items() if k not in PERMISSION_FIELDS}

    result: dict = {}
    inserted = False
    for key, value in cleaned.items():
        result[key] = value
        if key == INSERT_AFTER_FIELD:
            for field in PERMISSION_FIELDS:
                result[field] = values[field]
            inserted = True
    if not inserted:
        if "tools" in result:
            rebuilt: dict = {}
            for key, value in result.items():
                if key == "tools":
                    for field in PERMISSION_FIELDS:
                        rebuilt[field] = values[field]
                rebuilt[key] = value
            result = rebuilt
        else:
            for field in PERMISSION_FIELDS:
                result[field] = values[field]
    return result
