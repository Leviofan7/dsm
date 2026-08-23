import os
import uuid
import asyncio
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger

_env_root = os.getenv("PROJECT_ROOT")
PROJECT_ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).parent.parent.parent.resolve()

mcp = FastMCP("scheduler_mcp")

_scheduler: AsyncIOScheduler | None = None

def _get_scheduler() -> AsyncIOScheduler:
    """Lazily create and start the scheduler with persistent SQLAlchemy jobstore."""
    global _scheduler
    if _scheduler is None:
        db_path = os.environ.get("DATABASE_URL", "sqlite:///contextus.db")
        jobstores = {
            'default': SQLAlchemyJobStore(url=db_path)
        }
        _scheduler = AsyncIOScheduler(jobstores=jobstores)
        _scheduler.start()
    return _scheduler

async def _run_command(command: str):
    """Execution wrapper for cron jobs."""
    try:
        # We split the command to run with create_subprocess_exec (not shell=True)
        import shlex
        args = shlex.split(command)
        if not args:
            return
            
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT)
        )
        await process.communicate()
    except Exception:
        pass # Normally we'd log this

@mcp.tool()
def list_tasks() -> str:
    """Показывает все активные крон-задачи."""
    sched = _get_scheduler()
    jobs = sched.get_jobs()
    if not jobs:
        return "Нет активных крон-задач."
    res = []
    for job in jobs:
        res.append(f"- ID: {job.id} | Name: {job.name} | Next Run: {job.next_run_time}")
    return "\n".join(res)

@mcp.tool()
def add_cron_task(cron_expression: str, topic: str, command: str) -> str:
    """Добавляет новую крон-задачу с расписанием."""
    # 1. Validate the command for security
    import shlex
    try:
        args = shlex.split(command)
    except ValueError as e:
        raise RuntimeError(f"Invalid command syntax: {e}")
        
    if not args:
        raise RuntimeError("Command cannot be empty")
        
    binary = args[0]
    allowed_binaries = ["python", "python3", "node", "bash", "sh"]
    if binary not in allowed_binaries:
        raise RuntimeError(f"Security error: Binary '{binary}' is not allowed. Only {allowed_binaries} are allowed.")
        
    # Check that any paths in args are within PROJECT_ROOT
    for arg in args[1:]:
        if arg.startswith("/") or arg.startswith("./") or ".py" in arg or ".sh" in arg or ".js" in arg:
            # Simple check: try to resolve path
            clean_arg = arg.lstrip("/")
            full_path = (PROJECT_ROOT / clean_arg).resolve()
            try:
                full_path.relative_to(PROJECT_ROOT)
            except ValueError:
                raise RuntimeError(f"Security error: Argument '{arg}' resolves outside of project root.")
    
    # 2. Add job to scheduler
    task_id = str(uuid.uuid4())
    sched = _get_scheduler()
    try:
        trigger = CronTrigger.from_crontab(cron_expression)
        sched.add_job(
            func=_run_command,
            trigger=trigger,
            kwargs={'command': command},
            id=task_id,
            name=topic,
            replace_existing=True
        )
    except Exception as e:
        raise RuntimeError(f"Не удалось добавить крон-задачу: {e}")

    return f"Добавлена задача: {topic} с расписанием {cron_expression} (ID: {task_id})"

@mcp.tool()
def toggle_task(task_id: str, enabled: bool) -> str:
    """Включает или отключает крон-задачу."""
    sched = _get_scheduler()
    job = sched.get_job(task_id)
    if not job:
        raise RuntimeError(f"Задача с ID {task_id} не найдена.")
        
    if enabled:
        sched.resume_job(task_id)
        return f"Задача {task_id} возобновлена."
    else:
        sched.pause_job(task_id)
        return f"Задача {task_id} приостановлена."

if __name__ == "__main__":
    mcp.run()

