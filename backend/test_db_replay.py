from database import SessionLocal
from models import AgentTask, AgentTaskEvent

db = SessionLocal()
task = db.query(AgentTask).order_by(AgentTask.created_at.desc()).first()
events = db.query(AgentTaskEvent).filter(AgentTaskEvent.task_id == task.id).order_by(AgentTaskEvent.sequence_number).all()
for ev in events:
    print(f"seq={ev.sequence_number} payload={repr(ev.payload)}")
