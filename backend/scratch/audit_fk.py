"""
Аудит FK-контракта SQLite: что реально лежит в DDL и где висят нарушенные ссылки.

Запуск: docker compose exec -T fastapi_backend python -B /app/scratch/audit_fk.py
Только чтение — ничего не меняет.

Нужен перед включением PRAGMA foreign_keys=ON: если в данных уже есть ссылки на
несуществующие строки, принуждение начнёт ронять обращения к этим записям, а SET NULL
при удалении родителя — работать не там, где не хватает констрейнта в самом DDL
(таблицы, созданные сырым CREATE TABLE, прагма не защитит).
"""

import os
import sys

# Скрипт лежит в backend/scratch → корень бэкенда на уровень выше; нужен для `import database`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from database import Base, SessionLocal
import models  # noqa: F401 — регистрирует модели в Base.metadata

db = SessionLocal()
tables = {
    r[0]
    for r in db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    ).fetchall()
}
print(f"@@ таблиц в БД: {len(tables)}")

missing_models = [t.name for t in Base.metadata.sorted_tables if t.name not in tables]
print(f"@@ модельных таблиц нет в БД: {missing_models}")

print("@@ --- FK-контракт из DDL (что БД реально знает) ---")
ddl_fk_total = 0
for t in sorted(tables):
    rows = db.execute(text(f"PRAGMA foreign_key_list('{t}')")).fetchall()
    for fk in rows:  # (id, seq, table, from, to, on_update, on_delete, match)
        ddl_fk_total += 1
        print(f"@@   {t}.{fk[3]} -> {fk[2]}.{fk[4]} | on_delete={fk[6]} | on_update={fk[5]}")
print(f"@@ всего FK в DDL: {ddl_fk_total}")

print("@@ --- orphan-скан по метаданным моделей ---")
problems = []
for table in Base.metadata.sorted_tables:
    if table.name not in tables:
        continue
    for fk in table.foreign_keys:
        col, ref = fk.parent, fk.column
        if ref.table.name not in tables:
            problems.append((f"{table.name}.{col.name}", f"-> {ref.table.name}.{ref.name}", "PARENT TABLE MISSING"))
            continue
        q = text(
            f"SELECT COUNT(*) FROM {table.name} "
            f"WHERE {col.name} IS NOT NULL "
            f"AND {col.name} NOT IN (SELECT {ref.name} FROM {ref.table.name})"
        )
        n = db.execute(q).scalar()
        if n:
            problems.append((f"{table.name}.{col.name}", f"-> {ref.table.name}.{ref.name}", n))
if problems:
    for p in problems:
        print("@@  ", p)
else:
    print("@@   нарушений нет")
print(f"@@ всего нарушений: {len(problems)}")

print("@@ --- PRAGMA foreign_key_check (все констрейнты из DDL) ---")
violations = db.execute(text("PRAGMA foreign_key_check")).fetchall()
for v in violations[:50]:
    print("@@  ", v)
print(f"@@ нарушений foreign_key_check: {len(violations)}")

print("@@ --- typeof-скан: мусор в Integer-колонках (динамическая типизация SQLite) ---")
# SQLite не проверяет типы: в Integer-колонку можно записать строку ("probe-admin").
# foreign_key_check это ловит только для FK-колонок; прочие Integer-id и счётчики —
# вне его радара, поэтому проверяем typeof по всем Integer-колонкам всех моделей.
from sqlalchemy import Integer as _SqlInteger

type_problems = []
for table in Base.metadata.sorted_tables:
    if table.name not in tables:
        continue
    for col in table.columns:
        if not isinstance(col.type, _SqlInteger):
            continue
        q = text(
            f"SELECT COUNT(*) FROM {table.name} "
            f"WHERE typeof({col.name}) NOT IN ('integer', 'null')"
        )
        n = db.execute(q).scalar()
        if n:
            type_problems.append((f"{table.name}.{col.name}", n))
for p in type_problems:
    print("@@  ", p)
print(f"@@ типа-нарушений: {len(type_problems)}")

print("@@ PRAGMA foreign_keys сейчас:", db.execute(text("PRAGMA foreign_keys")).scalar())
db.close()
