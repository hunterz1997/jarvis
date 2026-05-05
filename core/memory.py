"""SQLite async memory layer for Jarvis — conversations, preferences, task log."""

import json
import logging
from pathlib import Path
from typing import Any
from datetime import datetime

import aiosqlite

from config import settings

logger = logging.getLogger(__name__)


class Memory:
    """Async SQLite memory store. Handles conversations, preferences, and task log."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or settings.db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Initialize the database and run schema migrations."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        schema = Path(__file__).parent.parent / "memory" / "schemas.sql"
        await self._db.executescript(schema.read_text())
        await self._db.commit()
        logger.info("Memory initialized at %s", self.db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        model_used: str | None = None,
        tool_name: str | None = None,
        tool_input: dict | None = None,
        tool_result: str | None = None,
    ) -> int:
        """Persist a single conversation message and return its row id."""
        async with self._db.execute(
            """INSERT INTO conversations
               (session_id, role, content, model_used, tool_name, tool_input, tool_result)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                role,
                content,
                model_used,
                tool_name,
                json.dumps(tool_input) if tool_input else None,
                tool_result,
            ),
        ) as cur:
            row_id = cur.lastrowid
        await self._db.commit()
        return row_id

    async def get_history(self, session_id: str, limit: int | None = None) -> list[dict]:
        """Return the last N messages for a session, oldest first."""
        limit = limit or settings.max_history_messages
        async with self._db.execute(
            """SELECT role, content, model_used, tool_name, tool_input, tool_result, timestamp
               FROM conversations WHERE session_id = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (session_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]

    async def get_sessions(self, limit: int = 20) -> list[dict]:
        """Return recent sessions with ID, start time, turn count, and first user message."""
        async with self._db.execute(
            """SELECT
                   c.session_id,
                   MIN(c.timestamp) as started,
                   MAX(c.timestamp) as last_active,
                   COUNT(*) as turns,
                   (SELECT content FROM conversations
                    WHERE session_id = c.session_id AND role = 'user'
                    ORDER BY timestamp ASC LIMIT 1) as first_message
               FROM conversations c
               WHERE c.session_id NOT LIKE 'whatsapp%'
               GROUP BY c.session_id
               ORDER BY last_active DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def delete_session(self, session_id: str) -> None:
        """Permanently delete all messages for a session."""
        await self._db.execute(
            "DELETE FROM conversations WHERE session_id = ?", (session_id,)
        )
        await self._db.commit()
        logger.info("Deleted session %s", session_id)

    # ------------------------------------------------------------------
    # API Usage tracking
    # ------------------------------------------------------------------

    async def track_usage(self, model: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        """Record token usage and estimated cost for one API call."""
        import time
        await self._db.execute(
            "INSERT INTO api_usage (timestamp, model, input_tokens, output_tokens, cost_usd) VALUES (?, ?, ?, ?, ?)",
            (time.time(), model, input_tokens, output_tokens, cost_usd),
        )
        await self._db.commit()

    async def get_usage_summary(self) -> dict:
        """Return cumulative token counts and estimated cost across all sessions."""
        async with self._db.execute(
            "SELECT SUM(input_tokens), SUM(output_tokens), SUM(cost_usd) FROM api_usage"
        ) as cur:
            row = await cur.fetchone()
        return {
            "total_input_tokens":  int(row[0] or 0),
            "total_output_tokens": int(row[1] or 0),
            "total_cost_usd":      round(float(row[2] or 0.0), 4),
        }

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    async def get_preference(self, key: str, default: str = "") -> str:
        async with self._db.execute(
            "SELECT value FROM preferences WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return row["value"] if row else default

    async def set_preference(self, key: str, value: str) -> None:
        await self._db.execute(
            """INSERT INTO preferences (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
            (key, value),
        )
        await self._db.commit()

    async def get_all_preferences(self) -> dict[str, str]:
        async with self._db.execute("SELECT key, value FROM preferences") as cur:
            rows = await cur.fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ------------------------------------------------------------------
    # Task log
    # ------------------------------------------------------------------

    async def log_task(
        self,
        session_id: str,
        task_type: str,
        description: str,
        status: str = "completed",
        result_summary: str | None = None,
        tools_used: list[str] | None = None,
        model_used: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        await self._db.execute(
            """INSERT INTO task_log
               (session_id, task_type, description, status, result_summary,
                tools_used, model_used, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                task_type,
                description,
                status,
                result_summary,
                json.dumps(tools_used) if tools_used else None,
                model_used,
                duration_ms,
            ),
        )
        await self._db.commit()

    async def get_recent_tasks(self, limit: int = 10) -> list[dict]:
        async with self._db.execute(
            """SELECT task_type, description, status, result_summary, model_used, timestamp
               FROM task_log ORDER BY timestamp DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]


# Module-level singleton
memory = Memory()
