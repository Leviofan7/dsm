"""Переименование intent: 2026-09-26-... → 2026-09-25-... (опечатка в дате ТЗ).

id по формату кодирует дату создания, поэтому исправляем и папку, и frontmatter, и INDEX.md.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, "/app")

OLD = "2026-09-26-web-stealth-false-block-detection"
NEW = "2026-09-25-web-stealth-false-block-detection"


def main() -> None:
    from services import intent as intent_service

    root = intent_service.intents_root()
    old_dir = root / "active" / OLD
    new_dir = root / "active" / NEW

    print("@@ существует старая папка:", old_dir.is_dir(), "| новая:", new_dir.exists())
    if not old_dir.is_dir():
        print("@@ нечего переименовывать")
        return

    old_dir.rename(new_dir)

    # frontmatter: id
    doc = new_dir / "intent.md"
    text = doc.read_text(encoding="utf-8")
    text = re.sub(rf"^id:\s*{re.escape(OLD)}\s*$", f"id: {NEW}", text, count=1, flags=re.M)
    doc.write_text(text, encoding="utf-8")

    # INDEX.md: старые id и путь → новые
    index = root / "INDEX.md"
    if index.exists():
        idx = index.read_text(encoding="utf-8")
        idx = idx.replace(OLD, NEW).replace(f"active/{OLD}/", f"active/{NEW}/")
        index.write_text(idx, encoding="utf-8")

    parsed = intent_service.read(NEW)
    print(f"@@ read({NEW}): id={parsed['frontmatter']['id']} status={parsed['frontmatter']['status']}")
    print("@@ старый id читается:", end=" ")
    try:
        intent_service.read(OLD)
        print("ДА (плохо — остался дубль)")
    except Exception as e:
        print(f"нет ({type(e).__name__})")

    print("@@ список активных:", [i["id"] for i in intent_service.list_intents()])
    line = [ln for ln in index.read_text(encoding="utf-8").splitlines() if NEW in ln]
    print("@@ INDEX строка:", line[0][:120] if line else "НЕТ")
    print("@@ файлы:", sorted(p.name for p in new_dir.iterdir()))


main()
