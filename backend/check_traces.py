import sqlite3
import json

conn = sqlite3.connect('backend/contextus.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT created_at, task_type_classified, task_type_final, model_used, tools_called, tools_called_names, actions_log, final_status FROM execution_traces ORDER BY created_at DESC LIMIT 5")
rows = cur.fetchall()

print("Recent Execution Traces:")
for row in rows:
    print(dict(row))

conn.close()
