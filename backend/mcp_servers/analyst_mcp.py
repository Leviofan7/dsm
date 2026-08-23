import os
import json
from mcp.server.fastmcp import FastMCP
import sys

# Ensure backend directory is in path to import database and models
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import SessionLocal
from models import ExecutionTrace, PendingPrivilegedAction

mcp = FastMCP("analyst_mcp")

@mcp.tool()
def read_session_trace(session_id: str) -> str:
    """Достаёт полный след выполнения сессии для анализа."""
    db = SessionLocal()
    try:
        traces = db.query(ExecutionTrace).filter(ExecutionTrace.session_id == session_id).order_by(ExecutionTrace.created_at).all()
        if not traces:
            return "Аудит для данной сессии не найден."
        
        result = []
        for t in traces:
            result.append({
                "task_id": t.task_id,
                "model": t.model_used,
                "duration_ms": t.duration_ms,
                "tools_called": t.tools_called,
                "tools_called_names": json.loads(t.tools_called_names) if t.tools_called_names else [],
                "actions_log": json.loads(t.actions_log) if t.actions_log else [],
                "final_status": t.final_status,
                "tool_verified": t.tool_verified,
                "tool_verification_details": t.tool_verification_details,
                "errors": t.errors
            })
                
        return json.dumps({"session_id": session_id, "traces": result}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Ошибка при запросе аудита: {e}"
    finally:
        db.close()

@mcp.tool()
def get_global_analytics() -> str:
    """Возвращает агрегированную статистику по всем сессиям (процент успеха, тихие промахи, исправления человеком).
    Используй это для выявления хронических проблем агентов."""
    db = SessionLocal()
    from sqlalchemy import text
    try:
        silent_miss_sql = """
            SELECT task_type_classified as task_type, COUNT(*) as total,
                   SUM(CASE WHEN tools_available > 0 AND tools_called = 0 THEN 1 ELSE 0 END) as silent_miss
            FROM execution_traces
            WHERE task_type_classified IS NOT NULL
            GROUP BY task_type_classified
        """
        silent_miss_res = db.execute(text(silent_miss_sql)).mappings().all()

        model_outcome_sql = """
            SELECT model_selected as model, task_type_final as task_type,
                   COUNT(*) as total,
                   SUM(CASE WHEN final_status = 'success' THEN 1 ELSE 0 END) as success_count,
                   SUM(CASE WHEN final_status = 'stub_response' THEN 1 ELSE 0 END) as stub_count
            FROM execution_traces
            WHERE model_selected IS NOT NULL AND final_status IS NOT NULL
            GROUP BY model_selected, task_type_final
        """
        model_outcome_res = db.execute(text(model_outcome_sql)).mappings().all()

        report = {
            "silent_misses": [dict(r) for r in silent_miss_res],
            "model_outcomes": [dict(r) for r in model_outcome_res]
        }
        return json.dumps(report, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Ошибка получения аналитики: {e}"
    finally:
        db.close()


@mcp.tool()
def propose_prompt_update(target_persona: str, new_prompt: str, reasoning: str, session_id: str = "") -> str:
    """Регистрирует предложение по изменению системного промпта персоны.
    НЕ применяет изменение — только создаёт запись на approval."""
    db = SessionLocal()
    try:
        action = PendingPrivilegedAction(
            action_type="prompt_update",
            target=target_persona,
            instruction=new_prompt,
            reasoning=reasoning,
            session_id=session_id or None,
            status="awaiting_approval",
        )
        db.add(action)
        db.commit()
        return f"Предложение по промпту для '{target_persona}' зарегистрировано (id={action.id}), ожидает подтверждения человека."
    finally:
        db.close()

@mcp.tool()
def propose_coder_task(target_file: str, instruction: str, reasoning: str, session_id: str = "") -> str:
    """Регистрирует ТЗ для Claude Code. НЕ вызывает coder напрямую —
    создаёт запись, которая появится в UI для approve человеком."""
    db = SessionLocal()
    try:
        action = PendingPrivilegedAction(
            action_type="coder_task",
            target=target_file,
            instruction=instruction,
            reasoning=reasoning,
            session_id=session_id or None,
            status="awaiting_approval",
        )
        db.add(action)
        db.commit()
        return f"ТЗ для правки '{target_file}' зарегистрировано (id={action.id}), ожидает подтверждения человека."
    finally:
        db.close()

# ─── Apprentice-Gate 2.0: Инструменты для Надсмотрщика (Supervisor 14B) ────────
@mcp.tool()
async def approve_action(request_id: str) -> str:
    """Одобрить ActionRequest автоматически. Кодер немедленно разблокируется."""
    from api.action_requests import mcp_approve_action
    return await mcp_approve_action(request_id)

@mcp.tool()
async def escalate_to_friend_call(request_id: str, summary: str) -> str:
    """Эскалировать ActionRequest к человеку (отправить в Telegram/UI)."""
    from api.action_requests import mcp_escalate_to_friend_call
    return await mcp_escalate_to_friend_call(request_id, summary)

@mcp.tool()
async def reject_action(request_id: str, feedback: str) -> str:
    """Отклонить ActionRequest. Кодер разблокируется и получит этот feedback."""
    from api.action_requests import mcp_reject_action
    return await mcp_reject_action(request_id, feedback)

if __name__ == "__main__":
    mcp.run()
