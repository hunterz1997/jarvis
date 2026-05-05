"""
Jarvis Task Scheduler — runs recurring and one-time background tasks using APScheduler.
When a task fires it runs the prompt through the LLM agent,
then pushes the result as a WebSocket notification to all connected clients.

Two task types:
  recurring  — fires every N minutes (interval_minutes required)
  once       — fires exactly once at run_at datetime, then auto-disables
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger

from config import settings

logger = logging.getLogger(__name__)


class JarvisScheduler:
    """APScheduler wrapper that manages recurring and one-time Jarvis tasks."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._db_path = settings.db_path
        self._ws_manager = None  # injected at startup

    def set_ws_manager(self, manager: Any) -> None:
        """Inject the WebSocket connection manager so we can push notifications."""
        self._ws_manager = manager

    async def start(self) -> None:
        """Start the scheduler and re-register all enabled tasks from DB."""
        # Run DB migration to add new columns if they don't exist
        await self._migrate_db()

        self._scheduler.start()
        await self._restore_tasks()

        # Periodic refresh — picks up newly created tasks
        self._scheduler.add_job(
            self._restore_tasks,
            trigger=IntervalTrigger(minutes=1),
            id="__internal_refresh",
            replace_existing=True,
        )
        logger.info("Jarvis scheduler started")

    async def _migrate_db(self) -> None:
        """Add new columns to scheduled_tasks if they don't exist (safe migration)."""
        async with aiosqlite.connect(self._db_path) as db:
            # Check existing columns
            async with db.execute("PRAGMA table_info(scheduled_tasks)") as cur:
                cols = {row[1] for row in await cur.fetchall()}

            if "trigger_type" not in cols:
                await db.execute(
                    "ALTER TABLE scheduled_tasks ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'recurring'"
                )
                logger.info("DB migration: added trigger_type column")

            if "run_at" not in cols:
                await db.execute(
                    "ALTER TABLE scheduled_tasks ADD COLUMN run_at DATETIME"
                )
                logger.info("DB migration: added run_at column")

            # interval_minutes may not be nullable in old schema — allow NULL now
            # SQLite doesn't support ALTER COLUMN, but NULL is allowed by default
            await db.commit()

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        logger.info("Jarvis scheduler stopped")

    # ── DB helpers ─────────────────────────────────────────────────────────

    async def _restore_tasks(self) -> None:
        """Load all enabled scheduled tasks from DB and register them."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM scheduled_tasks WHERE enabled = 1"
            ) as cur:
                rows = await cur.fetchall()

        registered = {
            job.id for job in self._scheduler.get_jobs()
            if not job.id.startswith("__")
        }
        db_ids = set()

        for row in rows:
            job_id = f"task_{row['id']}"
            db_ids.add(job_id)
            if job_id in registered:
                continue  # already scheduled

            trigger_type = row["trigger_type"] or "recurring"

            if trigger_type == "once":
                # One-time task — use DateTrigger
                run_at_str = row["run_at"]
                if not run_at_str:
                    continue
                try:
                    run_at = datetime.fromisoformat(run_at_str)
                except ValueError:
                    logger.warning("Invalid run_at for task %s: %s", row["id"], run_at_str)
                    continue

                if run_at <= datetime.now():
                    # Missed window — if never run, fire immediately; otherwise skip
                    if row["run_count"] == 0:
                        run_at = datetime.now() + timedelta(seconds=3)
                    else:
                        # Already fired or missed — disable
                        async with aiosqlite.connect(self._db_path) as db:
                            await db.execute(
                                "UPDATE scheduled_tasks SET enabled = 0 WHERE id = ?",
                                (row["id"],),
                            )
                            await db.commit()
                        continue

                self._scheduler.add_job(
                    self._run_task,
                    trigger=DateTrigger(run_date=run_at),
                    id=job_id,
                    args=[row["id"]],
                    replace_existing=True,
                )
                logger.info(
                    "One-time task registered: %s at %s",
                    row["name"], run_at.strftime("%Y-%m-%d %H:%M"),
                )

            else:
                # Recurring task — use IntervalTrigger
                interval = row["interval_minutes"]
                if not interval:
                    continue
                self._scheduler.add_job(
                    self._run_task,
                    trigger=IntervalTrigger(minutes=interval),
                    id=job_id,
                    args=[row["id"]],
                    replace_existing=True,
                    next_run_time=datetime.now() + timedelta(seconds=5)
                    if row["run_count"] == 0
                    else None,
                )
                logger.info(
                    "Recurring task registered: %s every %d min",
                    row["name"], interval,
                )

        # Remove APScheduler jobs for tasks that were deleted / disabled
        for job_id in registered - db_ids:
            self._scheduler.remove_job(job_id)
            logger.info("Removed stale job %s", job_id)

    async def _run_task(self, task_id: int) -> None:
        """Execute a scheduled task and push the result as a notification."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
            ) as cur:
                row = await cur.fetchone()
            if not row or not row["enabled"]:
                return

        task_name = row["name"]
        prompt = row["prompt"]
        trigger_type = row["trigger_type"] or "recurring"
        interval_min = row["interval_minutes"] or 0
        logger.info("Running scheduled task: %s (type=%s)", task_name, trigger_type)

        # Execute the prompt through a lightweight agent loop
        result_text = await self._execute_prompt(prompt, task_name)

        # Store as notification and update task metadata
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO notifications (task_id, task_name, content)
                   VALUES (?, ?, ?)""",
                (task_id, task_name, result_text),
            )

            if trigger_type == "once":
                # One-time task — disable after firing
                await db.execute(
                    """UPDATE scheduled_tasks
                       SET last_run = CURRENT_TIMESTAMP,
                           enabled = 0,
                           run_count = run_count + 1
                       WHERE id = ?""",
                    (task_id,),
                )
            else:
                # Recurring — update next_run
                next_run_expr = f"+{interval_min} minutes" if interval_min else "+60 minutes"
                await db.execute(
                    """UPDATE scheduled_tasks
                       SET last_run = CURRENT_TIMESTAMP,
                           next_run = datetime('now', ?),
                           run_count = run_count + 1
                       WHERE id = ?""",
                    (next_run_expr, task_id),
                )

            await db.commit()

        # Push to all connected WebSocket clients
        if self._ws_manager:
            payload = {
                "type": "notification",
                "task_name": task_name,
                "content": result_text,
                "timestamp": datetime.now().strftime("%I:%M %p"),
            }
            for session_id in list(self._ws_manager._connections.keys()):
                await self._ws_manager.send(session_id, payload)

        logger.info("Scheduled task completed: %s", task_name)

    async def _execute_prompt(self, prompt: str, task_name: str) -> str:
        """Run the prompt through the configured LLM backend (backend-agnostic)."""
        try:
            from core.llm import create_backend
            from core.tool_registry import get_tools
            from core.agent import _execute_tool

            backend = create_backend()
            tools = get_tools()
            is_anthropic = settings.llm_backend.lower() == "anthropic"

            system = (
                f"You are Jarvis running a scheduled background task: '{task_name}'.\n"
                "Execute the task efficiently. Use tools as needed.\n"
                "Return a concise, well-formatted markdown summary of the results.\n"
                "Be brief — this will be shown as a notification panel."
            )
            messages = [{"role": "user", "content": prompt}]

            for _ in range(5):  # max iterations
                full_text = ""
                tool_calls = []
                anthropic_content = None
                stop_reason = "end_turn"

                async for event in backend.stream(
                    system=system,
                    messages=messages,
                    tools=tools,
                    max_tokens=2000,
                ):
                    etype = event.get("type")
                    if etype == "text":
                        full_text += event["delta"]
                    elif etype == "tool_calls":
                        tool_calls = event["calls"]
                    elif etype == "_anthropic_content":
                        anthropic_content = event["content"]
                    elif etype == "stop":
                        stop_reason = event["reason"]
                    elif etype == "error":
                        return f"Task error: {event['message']}"

                if stop_reason == "end_turn":
                    return full_text.strip() or "Task completed."

                if stop_reason == "tool_use" and tool_calls:
                    # Append assistant turn
                    if anthropic_content is not None:
                        messages.append({"role": "assistant", "content": anthropic_content})
                    else:
                        import json as _json
                        oai_calls = [
                            {
                                "id": tc["id"], "type": "function",
                                "function": {"name": tc["name"], "arguments": _json.dumps(tc["input"])},
                            }
                            for tc in tool_calls
                        ]
                        msg: dict = {"role": "assistant", "tool_calls": oai_calls}
                        if full_text:
                            msg["content"] = full_text
                        messages.append(msg)

                    # Execute tools and append results
                    if is_anthropic:
                        results_block = []
                        for tc in tool_calls:
                            result = await _execute_tool(tc["name"], tc["input"])
                            results_block.append({
                                "type": "tool_result",
                                "tool_use_id": tc["id"],
                                "content": result,
                            })
                        messages.append({"role": "user", "content": results_block})
                    else:
                        for tc in tool_calls:
                            result = await _execute_tool(tc["name"], tc["input"])
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": result,
                            })
                else:
                    break

            return "Task ran but produced no output."

        except Exception as e:
            logger.error("Scheduled task execution failed: %s", e)
            return f"Task failed: {e}"

    # ── Public management API ──────────────────────────────────────────────

    async def create_task(
        self,
        name: str,
        prompt: str,
        interval_minutes: int | None = None,
        run_at: str | None = None,
    ) -> dict:
        """
        Create a new scheduled task.
        - For recurring tasks: provide interval_minutes
        - For one-time reminders: provide run_at (ISO 8601 datetime string)
        Returns the created task dict.
        """
        trigger_type = "once" if run_at else "recurring"

        if trigger_type == "recurring" and not interval_minutes:
            raise ValueError("interval_minutes is required for recurring tasks")
        if trigger_type == "once" and not run_at:
            raise ValueError("run_at is required for one-time tasks")

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if trigger_type == "once":
                async with db.execute(
                    """INSERT INTO scheduled_tasks
                       (name, prompt, trigger_type, run_at, interval_minutes, next_run)
                       VALUES (?, ?, 'once', ?, 0, ?)""",
                    (name, prompt, run_at, run_at),
                ) as cur:
                    task_id = cur.lastrowid
            else:
                async with db.execute(
                    """INSERT INTO scheduled_tasks
                       (name, prompt, trigger_type, interval_minutes, next_run)
                       VALUES (?, ?, 'recurring', ?, datetime('now', ? || ' minutes'))""",
                    (name, prompt, interval_minutes, str(interval_minutes)),
                ) as cur:
                    task_id = cur.lastrowid
            await db.commit()
            async with db.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
            ) as cur:
                row = await cur.fetchone()
        task = dict(row)

        # Register immediately with APScheduler
        job_id = f"task_{task_id}"
        if trigger_type == "once":
            run_at_dt = datetime.fromisoformat(run_at)
            if run_at_dt > datetime.now():
                self._scheduler.add_job(
                    self._run_task,
                    trigger=DateTrigger(run_date=run_at_dt),
                    id=job_id,
                    args=[task_id],
                    replace_existing=True,
                )
        else:
            self._scheduler.add_job(
                self._run_task,
                trigger=IntervalTrigger(minutes=interval_minutes),
                id=job_id,
                args=[task_id],
                replace_existing=True,
            )

        logger.info(
            "Created %s task: %s (%s)",
            trigger_type, name,
            f"every {interval_minutes} min" if trigger_type == "recurring" else f"at {run_at}",
        )
        return task

    async def list_tasks(self) -> list[dict]:
        """Return all scheduled tasks (recurring + one-time)."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM scheduled_tasks ORDER BY created_at DESC"
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def cancel_task(self, task_id: int) -> bool:
        """Disable a scheduled task."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE scheduled_tasks SET enabled = 0 WHERE id = ?", (task_id,)
            )
            await db.commit()
        job_id = f"task_{task_id}"
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
        logger.info("Cancelled scheduled task %d", task_id)
        return True

    async def get_unread_notifications(self, limit: int = 20) -> list[dict]:
        """Return unread notifications and mark them as read."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM notifications WHERE read = 0
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                placeholders = ",".join("?" * len(ids))
                await db.execute(
                    f"UPDATE notifications SET read = 1 WHERE id IN ({placeholders})",
                    ids,
                )
                await db.commit()
        return [dict(r) for r in rows]


# Module-level singleton
scheduler = JarvisScheduler()
