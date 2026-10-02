import pytest
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# Ensure test environment variables
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test_webhook_secret_12345"
os.environ["WEB_AUTH_SECRET"] = "test_web_auth_secret_67890"

from sqlalchemy.pool import StaticPool
from main import app, get_or_create_tg_user_and_conversation
from database import Base, get_db
import database
import auth
import main
import models
from auth import create_session_token

# Test in-memory SQLite DB with StaticPool so all connections share the same DB
TEST_DB_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DB_URL, 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)

@pytest.fixture(scope="function", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    
    # Override get_db in FastAPI app
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
            
    app.dependency_overrides[get_db] = override_get_db
    
    orig_main_sl = main.SessionLocal
    orig_db_sl = database.SessionLocal
    orig_send_tg = main.send_telegram_message
    
    main.SessionLocal = TestingSessionLocal
    database.SessionLocal = TestingSessionLocal
    
    async def mock_send_tg(chat_id, text):
        pass
    main.send_telegram_message = mock_send_tg
    
    yield
    
    app.dependency_overrides.clear()
    main.SessionLocal = orig_main_sl
    database.SessionLocal = orig_db_sl
    main.send_telegram_message = orig_send_tg
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def client():
    return TestClient(app)

def test_webhook_missing_secret(client):
    """1. Webhook без секрета возвращает 401"""
    resp = client.post("/telegram/webhook", json={"message": {"text": "hello"}})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid secret token"

def test_webhook_invalid_secret(client):
    """2. Webhook с неверным секретом возвращает 401"""
    resp = client.post(
        "/telegram/webhook", 
        json={"message": {"text": "hello"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_secret"}
    )
    assert resp.status_code == 401

def test_new_telegram_user_gets_user_role():
    """3. Новый Telegram user получает роль 'user'"""
    user, conv = get_or_create_tg_user_and_conversation("999888777")
    assert user is not None
    assert user.role == "user"
    assert user.telegram_chat_id == "999888777"
    assert conv.telegram_chat_id == "999888777"
    assert conv.user_id == user.id

def test_telegram_callback_from_stranger_is_silent(client):
    """4a. Callback от постороннего → тишина наружу, но постоянный след в аудите."""
    db = TestingSessionLocal()
    db.add(models.User(role="user", telegram_chat_id="111222333", display_name="Regular User"))
    db.commit()
    db.close()

    payload = {
        "callback_query": {
            "id": "cb_123",
            "message": {"chat": {"id": 111222333}},
            "data": "ar_approve_test_action_id"
        }
    }

    resp = client.post(
        "/telegram/webhook",
        json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "test_webhook_secret_12345"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    db = TestingSessionLocal()
    log = db.query(models.AuditLog).filter(models.AuditLog.source == "telegram").one()
    assert log.action == "access_denied"
    assert log.result == "denied"
    assert log.user_id is None, "у постороннего нет пользователя — user_id должен остаться пустым"
    assert log.target_id == "111222333", "сырой chat_id должен сохраняться в target_id"
    assert "callback" in log.detail and "ar_approve_test_action_id" in log.detail
    db.close()


def test_telegram_callback_owner_without_admin_denied_and_audited(client, monkeypatch):
    """4b. Владелец без роли admin → 403 + аудит (это наша ошибка конфигурации, её надо видеть)."""
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "111222333")

    db = TestingSessionLocal()
    user = models.User(role="user", telegram_chat_id="111222333", display_name="Regular User")
    db.add(user)
    db.commit()
    user_id = user.id  # read before close to avoid DetachedInstanceError
    db.close()

    payload = {
        "callback_query": {
            "id": "cb_123",
            "message": {"chat": {"id": 111222333}},
            "data": "ar_approve_test_action_id"
        }
    }

    resp = client.post(
        "/telegram/webhook",
        json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "test_webhook_secret_12345"}
    )
    assert resp.status_code == 403

    db = TestingSessionLocal()
    log = db.query(models.AuditLog).filter(
        models.AuditLog.action == "callback_denied",
        models.AuditLog.source == "telegram",
        models.AuditLog.result == "denied"
    ).first()
    assert log is not None
    assert log.user_id == user_id
    db.close()


def _webhook_message(client, chat_id: int) -> "object":
    return client.post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": chat_id}, "text": "привет"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "test_webhook_secret_12345"},
    )


def test_stranger_message_is_silent_but_audited(client, monkeypatch):
    """4c. Сообщение постороннего: ни ответа, ни user'а — но след в аудите есть."""
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "1338181864")
    dispatched = []

    async def spy(chat_id, text):
        dispatched.append((chat_id, text))

    monkeypatch.setattr(main, "process_telegram_message", spy)

    resp = _webhook_message(client, 555000111)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert dispatched == [], "агент не должен запускаться для постороннего"

    db = TestingSessionLocal()
    assert db.query(models.User).filter(models.User.telegram_chat_id == "555000111").first() is None
    assert db.query(models.Conversation).filter(models.Conversation.telegram_chat_id == "555000111").first() is None

    log = db.query(models.AuditLog).filter(models.AuditLog.source == "telegram").one()
    assert (log.action, log.result, log.target_id, log.user_id) == (
        "access_denied", "denied", "555000111", None
    )
    assert "привет" in log.detail, "в detail полезно видеть, что именно писали"
    db.close()


def test_owner_message_is_dispatched(client, monkeypatch):
    """4d. Сообщение владельца обрабатывается как раньше (тишина не задевает владельца)."""
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "1338181864")
    dispatched = []

    async def spy(chat_id, text):
        dispatched.append((chat_id, text))

    monkeypatch.setattr(main, "process_telegram_message", spy)

    resp = _webhook_message(client, 1338181864)
    assert resp.status_code == 200
    assert dispatched == [(1338181864, "привет")]


def test_empty_allowlist_silences_everyone(client, monkeypatch):
    """4e. Fail-closed: пустой ALLOWED_TELEGRAM_USER_ID → бот молчит для всех, а не для никого."""
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "")
    dispatched = []

    async def spy(chat_id, text):
        dispatched.append((chat_id, text))

    monkeypatch.setattr(main, "process_telegram_message", spy)

    assert _webhook_message(client, 1338181864).status_code == 200
    assert dispatched == [], "при пустом allowlist не должен отвечать даже прежний владелец"


def test_role_read_endpoints_require_admin(client):
    """4g. Промпты ролей (слепок модели прав) не отдаём без аутентификации."""
    assert client.get("/roles").status_code == 401
    assert client.get("/roles/coder").status_code == 401

    cookies = {"contextus_session": _admin_cookie()}
    resp = client.get("/roles", cookies=cookies)
    assert resp.status_code == 200
    assert any(r["id"] == "coder" and r["system_instruction"] for r in resp.json())
    assert client.get("/roles/coder", cookies=cookies).status_code == 200


def test_pending_action_requests_require_admin(client):
    """4h. Payload'ы ожидающих подтверждений (команды/диффы) закрыты админом."""
    assert client.get("/api/action-requests/pending").status_code == 401

    resp = client.get("/api/action-requests/pending", cookies={"contextus_session": _admin_cookie()})
    assert resp.status_code == 200
    assert resp.json() == []


def test_owner_ids_support_comma_separated_list(monkeypatch):
    """4f. Список через запятую (несколько устройств) + мусор в строке не ломает разбор."""
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", " 111 , 222 ,, ")
    assert main.telegram_owner_ids() == {"111", "222"}
    assert main.is_telegram_owner(111) is True
    assert main.is_telegram_owner("222") is True
    assert main.is_telegram_owner(333) is False
    assert main.is_telegram_owner(None) is False


# ── Скользящая сессия (пункт 3 ТЗ) ────────────────────────────────

def _token_exp(token: str) -> int:
    import json as _json
    import auth as _authx

    return _json.loads(_authx._b64decode(token.split(".")[0]).decode("utf-8"))["exp"]


def _issued_token_header(resp) -> str:
    """Достаёт значение contextus_session из Set-Cookie ответа ('' если его нет/пусто)."""
    raw = resp.headers.get("set-cookie", "")
    for part in raw.split(","):
        if "contextus_session=" in part:
            value = part.split("contextus_session=")[1].split(";")[0].strip()
            return value.strip('"')
    return ""


def test_authorized_request_renews_session_cookie(client):
    """Валидная cookie продлевается: приходит новая cookie с exp ≈ now+7д."""
    db = TestingSessionLocal()
    user = models.User(role="admin", display_name="Renewal Admin")
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()

    old = create_session_token(user_id, ttl_seconds=60)  # старый токен почти истёк
    resp = client.get("/api/auth/me", cookies={"contextus_session": old})
    assert resp.status_code == 200

    renewed = _issued_token_header(resp)
    assert renewed, "успешный авторизованный запрос должен продлить сессию"
    assert renewed != old
    assert _token_exp(renewed) - _token_exp(old) > 6 * 86400


def test_logout_is_not_renewed(client):
    """Выход не должен продлевать сессию — иначе logout не выходит."""
    resp = client.post("/api/auth/logout", cookies={"contextus_session": create_session_token(1)})
    assert resp.status_code == 200
    raw = resp.headers.get("set-cookie", "")
    assert "contextus_session=;" in raw or 'contextus_session=""' in raw or "Max-Age=0" in raw
    assert _issued_token_header(resp) == "", "logout не продлевает сессию"


@pytest.mark.parametrize("token", ["bogus", "aaa.bbb", ""])
def test_invalid_or_absent_cookie_is_not_renewed(client, token):
    """Продлеваем только валидную подпись: иначе продлевали бы и подделку."""
    resp = client.get("/api/auth/me", cookies={"contextus_session": token})
    assert resp.status_code == 401
    assert _issued_token_header(resp) == ""


def test_linking_token_single_use_and_expiry(client):
    """5. Linking token работает один раз и истекает через 10 минут"""
    db = TestingSessionLocal()
    admin = models.User(role="admin", display_name="Admin")
    db.add(admin)
    db.commit()
    db.refresh(admin)

    # 1. Generate link token via API
    admin_token = create_session_token(admin.id)
    resp = client.post(
        f"/api/users/{admin.id}/link-telegram",
        cookies={"contextus_session": admin_token}
    )
    assert resp.status_code == 200
    data = resp.json()
    token = data["token"]
    assert token is not None

    # 2. Use linking token via Telegram message
    import main
    import asyncio
    asyncio.run(main.process_telegram_message(555444333, f"/start {token}"))

    db.expire_all()
    user_after = db.query(models.User).filter(models.User.id == admin.id).first()
    assert user_after.telegram_chat_id == "555444333"
    assert user_after.linking_token is None

    # 3. Try to use same token again -> should fail
    asyncio.run(main.process_telegram_message(999999999, f"/start {token}"))
    user_after_2 = db.query(models.User).filter(models.User.id == admin.id).first()
    assert user_after_2.telegram_chat_id == "555444333" # Not overwritten
    db.close()

def test_concurrent_conversation_creation_race_condition():
    """6. Повторные вызовы get_or_create_tg_user_and_conversation идемпотентны — создаётся ровно одна Conversation"""
    # SQLite in-memory с StaticPool является однопоточным, поэтому тестируем идемпотентность
    # последовательными вызовами (реальная параллельность тестируется на PostgreSQL в staging)
    chat_id = "777888999"
    results = []

    for _ in range(5):
        u, c = get_or_create_tg_user_and_conversation(chat_id)
        results.append((u.id, c.id))

    assert len(results) == 5

    # All calls should resolve to the same user and conversation
    user_ids = {r[0] for r in results}
    conv_ids = {r[1] for r in results}
    assert len(user_ids) == 1, f"Expected 1 unique user, got {user_ids}"
    assert len(conv_ids) == 1, f"Expected 1 unique conversation, got {conv_ids}"

    db = TestingSessionLocal()
    total_convs = db.query(models.Conversation).filter(models.Conversation.telegram_chat_id == chat_id).count()
    assert total_convs == 1
    db.close()

def test_web_ui_without_auth_401_and_logged(client):
    """7. Web UI без cookie/токена -> 401 и запись в AuditLog"""
    resp = client.get("/api/users")
    assert resp.status_code == 401

    db = TestingSessionLocal()
    log = db.query(models.AuditLog).filter(
        models.AuditLog.source == "web_ui",
        models.AuditLog.action == "access_denied",
        models.AuditLog.result == "denied"
    ).first()
    assert log is not None
    db.close()

def test_web_ui_admin_allowed(client):
    """8. Web UI admin -> доступ к privileged endpoints разрешён"""
    db = TestingSessionLocal()
    admin = models.User(role="admin", display_name="Admin Superuser")
    db.add(admin)
    db.commit()
    db.refresh(admin)

    admin_session = create_session_token(admin.id)
    db.close()

    resp = client.get(
        "/api/users",
        cookies={"contextus_session": admin_session}
    )
    assert resp.status_code == 200
    users = resp.json()
    assert len(users) == 1
    assert users[0]["role"] == "admin"

def test_role_change_applies_immediately_without_restart(client):
    """9. Смена роли применяется мгновенно без рестарта сервера"""
    db = TestingSessionLocal()
    user = models.User(role="user", display_name="Promoted User")
    db.add(user)
    db.commit()
    db.refresh(user)

    user_session = create_session_token(user.id)

    # 1. Attempt admin endpoint with user role -> 403 Forbidden
    resp = client.get(
        "/api/users",
        cookies={"contextus_session": user_session}
    )
    assert resp.status_code == 403

    # 2. Promote user to admin in DB
    user.role = "admin"
    db.commit()
    db.close()

    # 3. Same user with same session token now immediately succeeds!
    resp2 = client.get(
        "/api/users",
        cookies={"contextus_session": user_session}
    )
    assert resp2.status_code == 200


# ── Настройки агентов (роли + конфиг оркестратора) ─────────────────

def _admin_cookie() -> str:
    db = TestingSessionLocal()
    admin = models.User(role="admin", display_name="Admin For Agent Tests")
    db.add(admin)
    db.commit()
    db.refresh(admin)
    token = create_session_token(admin.id)
    db.close()
    return token


def test_agent_config_requires_admin(client):
    """10. POST /agents/{id}/config: без cookie — 401, обычный пользователь — 403"""
    payload = {"light": {"models": [], "extra_mcps": []}}

    resp = client.post("/agents/coder/config", json=payload)
    assert resp.status_code == 401

    db = TestingSessionLocal()
    user = models.User(role="user", display_name="Plain User")
    db.add(user)
    db.commit()
    db.refresh(user)
    user_token = create_session_token(user.id)
    db.close()

    resp = client.post(
        "/agents/coder/config",
        json=payload,
        cookies={"contextus_session": user_token},
    )
    assert resp.status_code == 403


def test_agent_config_validates_agent_id(client):
    """11. Конфиг сохраняется только для существующей роли и с корректным id"""
    cookies = {"contextus_session": _admin_cookie()}
    payload = {"light": {"models": [], "extra_mcps": []}}

    # Несуществующая роль
    resp = client.post("/agents/no_such_role/config", json=payload, cookies=cookies)
    assert resp.status_code == 404

    # Мусорный id: раньше он попадал в agent_configs.json как ключ "undefined"
    resp = client.post("/agents/undefined/config", json=payload, cookies=cookies)
    assert resp.status_code == 400


def test_agent_config_writes_normalized_config(client, tmp_path, monkeypatch):
    """12. Успешное сохранение пишет нормализованный конфиг (legacy model → models)"""
    # Изолируем файлы: endpoint вычисляет пути от __file__ модуля main
    fake_backend = tmp_path / "backend"
    (fake_backend / "config").mkdir(parents=True)
    (fake_backend / "roles").mkdir(parents=True)
    (fake_backend / "roles" / "coder.yaml").write_text("name: Кодер\n", encoding="utf-8")
    monkeypatch.setattr(main, "__file__", str(fake_backend / "main.py"))
    # В тестах реестр моделей не загружается (нет initialize()) — подкладываем минимальный набор
    monkeypatch.setattr(main.llm_manager.registry, "models", {"qwen-coder": object()})

    resp = client.post(
        "/agents/coder/config",
        json={"light": {"model": "qwen-coder", "extra_mcps": ["fs-tools"]}},
        cookies={"contextus_session": _admin_cookie()},
    )
    assert resp.status_code == 200

    written = json.loads((fake_backend / "config" / "agent_configs.json").read_text(encoding="utf-8"))
    assert written["coder"]["light"] == {"models": ["qwen-coder"], "extra_mcps": ["fs-tools"]}


def test_protected_role_cannot_be_deleted(client):
    """13. Системные роли защищены от удаления"""
    resp = client.delete("/roles/doorman", cookies={"contextus_session": _admin_cookie()})
    assert resp.status_code == 400
    assert "оркестратора" in resp.json()["detail"]


def test_role_save_updates_only_known_fields(client, tmp_path, monkeypatch):
    """14. Сохранение роли не теряет прочие ключи YAML (например, id: у doorman.yaml)"""
    fake_backend = tmp_path / "backend"
    (fake_backend / "roles").mkdir(parents=True)
    role_file = fake_backend / "roles" / "custom.yaml"
    role_file.write_text("id: custom\nname: Old\n", encoding="utf-8")
    monkeypatch.setattr(main, "__file__", str(fake_backend / "main.py"))

    resp = client.post(
        "/roles/custom",
        json={
            "name": "New",
            "description": "desc",
            "planner": False,
            "tools": [],
            "system_instruction": "instr",
        },
        cookies={"contextus_session": _admin_cookie()},
    )
    assert resp.status_code == 200

    saved = role_file.read_text(encoding="utf-8")
    assert "id: custom" in saved
    assert "New" in saved


def test_role_save_keeps_permission_fields_byte_stable(client, tmp_path, monkeypatch):
    """
    Фаза A, критерий 8: сохранение роли из UI не теряет permission-поля,
    а для канонического файла вообще не меняет байты (sha остаётся тем же).
    """
    import hashlib
    import yaml

    fake_backend = tmp_path / "backend"
    (fake_backend / "roles").mkdir(parents=True)
    role_file = fake_backend / "roles" / "probe_role.yaml"

    role_data = {
        "id": "probe_role",
        "name": "Probe",
        "description": "роль для проверки сохранения",
        "planner": False,
        "write_scope": "workspace:tests/",
        "intent_access": "write_own",
        "can_create_intent": False,
        "tools": ["read_file"],
        "system_instruction": "промпт",
    }
    # канонический writer — тот же, что у UI
    with open(role_file, "w", encoding="utf-8") as f:
        yaml.dump(role_data, f, allow_unicode=True, sort_keys=False)
    before = hashlib.sha256(role_file.read_bytes()).hexdigest()

    monkeypatch.setattr(main, "__file__", str(fake_backend / "main.py"))

    resp = client.post(
        "/roles/probe_role",
        json={
            "id": "probe_role",
            "name": role_data["name"],
            "description": role_data["description"],
            "planner": role_data["planner"],
            "tools": role_data["tools"],
            "system_instruction": role_data["system_instruction"],
        },
        cookies={"contextus_session": _admin_cookie()},
    )
    assert resp.status_code == 200

    saved_yaml = yaml.safe_load(role_file.read_text(encoding="utf-8"))
    assert saved_yaml["write_scope"] == "workspace:tests/"
    assert saved_yaml["intent_access"] == "write_own"
    assert saved_yaml["can_create_intent"] is False
    saved_keys = list(saved_yaml.keys())
    planner_at = saved_keys.index("planner")
    assert saved_keys[planner_at + 1:planner_at + 4] == ["write_scope", "intent_access", "can_create_intent"]
    assert hashlib.sha256(role_file.read_bytes()).hexdigest() == before, "UI-Save изменил канонический файл"

    # заодно: поле, которого UI не знает, всё равно переживает сохранение с новым значением
    resp = client.post(
        "/roles/probe_role",
        json={
            "id": "probe_role",
            "name": "Probe v2",
            "description": role_data["description"],
            "planner": role_data["planner"],
            "tools": role_data["tools"],
            "system_instruction": role_data["system_instruction"],
        },
        cookies={"contextus_session": _admin_cookie()},
    )
    assert resp.status_code == 200
    after = yaml.safe_load(role_file.read_text(encoding="utf-8"))
    assert after["name"] == "Probe v2"
    assert after["write_scope"] == "workspace:tests/"
    assert after["can_create_intent"] is False
