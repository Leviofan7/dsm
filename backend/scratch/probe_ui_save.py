"""Живая проверка: сохранение роли из UI не теряет permission-поля.

Исход А = поля лежат прямо в roles/*.yaml и переживают Save.
Исход Б = UI пересобирает файл и поля теряются (тогда нужен sidecar).
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, "/app")

ROLES = ("doorman", "coder", "tester", "meta_analyst", "web_researcher")
FIELDS = ("write_scope", "intent_access", "can_create_intent")
UI_KEYS = ("name", "description", "planner", "tools", "system_instruction")
BACKUP = Path("/tmp/roles_backup")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    from database import SessionLocal
    from models import User
    from auth import create_session_token

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.role == "admin").first()
        print("@@ ADMIN:", admin.id if admin else "нет администратора в БД")
        if not admin:
            return
        token = create_session_token(admin.id)
    finally:
        db.close()

    BACKUP.mkdir(parents=True, exist_ok=True)
    for role in ROLES:
        shutil.copy2(f"/app/roles/{role}.yaml", BACKUP / f"{role}.yaml")

    for role in ROLES:
        path = Path(f"/app/roles/{role}.yaml")
        before_sha = sha(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        payload = {k: data[k] for k in UI_KEYS if k in data}

        resp = httpx.post(
            f"http://localhost:8000/roles/{role}",
            json=payload,
            cookies={"contextus_session": token},
            timeout=30,
        )
        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        keys = list(saved.keys())
        after = [k for k in FIELDS if k in saved]
        print(
            f"@@ {role:15} http={resp.status_code} sha_equal={sha(path) == before_sha} "
            f"поля={after} values={[saved.get(f) for f in FIELDS]} порядок_после_planner="
            f"{keys[keys.index('planner') + 1:keys.index('planner') + 4] if 'planner' in keys else '?'}"
        )
        if sha(path) != before_sha:
            print(f"   ⚠️ байты изменились: копия в {BACKUP / (role + '.yaml')}")

    print("@@ BACKUP:", json.dumps(sorted(p.name for p in BACKUP.iterdir()), ensure_ascii=False))

    # ── Самый опасный случай: поле ИЗМЕНИЛИ через панель ────────────
    role = "doorman"
    path = Path(f"/app/roles/{role}.yaml")
    original_sha = sha(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload = {k: data[k] for k in UI_KEYS if k in data}
    payload["description"] = "ИЗМЕНЕНО ПРОБОЙ (проверка сохранения permission-полей)"

    resp = httpx.post(
        f"http://localhost:8000/roles/{role}",
        json=payload,
        cookies={"contextus_session": token},
        timeout=30,
    )
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    print(
        f"@@ CHANGE {role} http={resp.status_code} описание_изменилось="
        f"{saved['description'] != data['description']} поля={[saved.get(f) for f in FIELDS]}"
    )

    shutil.copy2(BACKUP / f"{role}.yaml", path)
    print(f"@@ RESTORED {role} sha_восстановлен={sha(path) == original_sha}")


main()
