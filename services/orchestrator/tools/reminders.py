"""
Reminder tools for Atlas.

Storage: the `reminders` Postgres table (created by the api service).
Firing: the bot polls /alerts/reminders on the orchestrator every minute;
        due rows are returned, advanced (for recurring) or marked fired
        (for one-shots), and the bot sends them to Telegram.

Recurrence vocabulary:
    None / "once"  → one-shot, status flips to 'fired' after firing
    "daily"        → due_at += 1 day
    "weekdays"     → due_at += 1 day, skipping Sat/Sun
    "weekly"       → due_at += 7 days
    "monthly"      → due_at += 1 calendar month (best-effort, clamps to month end)
    "yearly"       → due_at += 1 calendar year
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

import asyncpg

from tools.base import Tool

logger = logging.getLogger("atlas.tools.reminders")

POSTGRES_DSN = os.getenv("POSTGRES_DSN")
WAT = timezone(timedelta(hours=1))

VALID_REPEATS = {"once", "daily", "weekdays", "weekly", "monthly", "yearly"}


_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not POSTGRES_DSN:
            raise RuntimeError("POSTGRES_DSN is not set")
        dsn = POSTGRES_DSN.replace("+asyncpg", "")
        _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    return _pool


def _parse_due(due_at_iso: str) -> datetime:
    """Parse an ISO timestamp. Naive strings are treated as WAT."""
    s = due_at_iso.strip()
    # tolerate trailing Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WAT)
    return dt.astimezone(timezone.utc)


def advance(repeat: str | None, current_due: datetime) -> datetime | None:
    """Compute the next firing time for a recurring reminder, or None if one-shot."""
    if not repeat or repeat == "once":
        return None
    if repeat == "daily":
        return current_due + timedelta(days=1)
    if repeat == "weekdays":
        nxt = current_due + timedelta(days=1)
        while nxt.weekday() >= 5:  # 5 = Sat, 6 = Sun
            nxt += timedelta(days=1)
        return nxt
    if repeat == "weekly":
        return current_due + timedelta(days=7)
    if repeat == "monthly":
        # add 1 calendar month, clamp day to last-of-month if needed
        year = current_due.year + (1 if current_due.month == 12 else 0)
        month = 1 if current_due.month == 12 else current_due.month + 1
        # find last day of the target month
        if month == 12:
            last_day = 31
        else:
            last_day = (current_due.replace(year=year, month=month + 1, day=1) - timedelta(days=1)).day
        day = min(current_due.day, last_day)
        return current_due.replace(year=year, month=month, day=day)
    if repeat == "yearly":
        try:
            return current_due.replace(year=current_due.year + 1)
        except ValueError:
            # Feb 29 → Feb 28
            return current_due.replace(year=current_due.year + 1, day=28)
    return None


class SetReminderTool(Tool):
    name = "set_reminder"
    description = (
        "Schedule a reminder. Atlas will message Duke on Telegram when it's due. "
        "Supports one-shot or recurring reminders."
    )
    is_destructive = False

    schema = {
        "name": "set_reminder",
        "description": (
            "Schedule a reminder for Duke. The orchestrator will fire a Telegram "
            "message at due_at_iso. For recurring reminders set `repeat`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "body": {
                    "type": "string",
                    "description": "What the reminder is about, written for Duke to read directly (e.g. 'Call Tobi about the contract').",
                },
                "due_at_iso": {
                    "type": "string",
                    "description": (
                        "When to fire. ISO-8601. If no timezone is given, WAT (Africa/Lagos, UTC+1) is assumed. "
                        "Example: '2026-05-20T15:00:00' fires at 3pm WAT tomorrow."
                    ),
                },
                "repeat": {
                    "type": "string",
                    "enum": ["once", "daily", "weekdays", "weekly", "monthly", "yearly"],
                    "description": "Recurrence. Omit or use 'once' for a one-shot.",
                },
            },
            "required": ["body", "due_at_iso"],
        },
    }

    async def run(self, body: str, due_at_iso: str, repeat: str | None = None, **kwargs) -> str:
        if repeat and repeat not in VALID_REPEATS:
            return f"Error: repeat must be one of {sorted(VALID_REPEATS)}."
        if repeat == "once":
            repeat = None

        try:
            due_utc = _parse_due(due_at_iso)
        except Exception as exc:
            return f"Error: could not parse due_at_iso ({exc})."

        # Resolve user_id from registry kwargs (orchestrator passes it through)
        user_id_raw = kwargs.get("user_id")
        try:
            user_id = int(user_id_raw) if user_id_raw is not None else 0
        except (TypeError, ValueError):
            user_id = 0

        pool = await _get_pool()
        row = await pool.fetchrow(
            """
            INSERT INTO reminders (user_id, body, due_at, repeat)
            VALUES ($1, $2, $3, $4)
            RETURNING id, due_at
            """,
            user_id, body.strip(), due_utc, repeat,
        )
        due_local = row["due_at"].astimezone(WAT)
        when = due_local.strftime("%A, %B %d at %I:%M %p WAT")
        suffix = f" (repeating {repeat})" if repeat else ""
        return f"Reminder #{row['id']} set for {when}{suffix}: {body.strip()}"


class ListRemindersTool(Tool):
    name = "list_reminders"
    description = "List Duke's upcoming pending reminders."
    is_destructive = False

    schema = {
        "name": "list_reminders",
        "description": "List pending reminders for Duke, soonest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max rows. Default 20."}
            },
        },
    }

    async def run(self, limit: int = 20, **kwargs) -> str:
        limit = max(1, min(int(limit or 20), 100))
        pool = await _get_pool()
        rows = await pool.fetch(
            """
            SELECT id, body, due_at, repeat
            FROM reminders
            WHERE status = 'pending'
            ORDER BY due_at ASC
            LIMIT $1
            """,
            limit,
        )
        if not rows:
            return "No pending reminders."
        lines = ["Pending reminders:"]
        for r in rows:
            when = r["due_at"].astimezone(WAT).strftime("%a %b %d, %I:%M %p WAT")
            tag = f" [{r['repeat']}]" if r["repeat"] else ""
            lines.append(f"#{r['id']} — {when}{tag} — {r['body']}")
        return "\n".join(lines)


class CancelReminderTool(Tool):
    name = "cancel_reminder"
    description = "Cancel a pending reminder by its numeric id."
    is_destructive = False  # reversible in practice (just sets status); not user-visible side effect

    schema = {
        "name": "cancel_reminder",
        "description": "Cancel a reminder by id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "integer", "description": "The numeric id from list_reminders or the set confirmation."}
            },
            "required": ["reminder_id"],
        },
    }

    async def run(self, reminder_id: int, **kwargs) -> str:
        pool = await _get_pool()
        row = await pool.fetchrow(
            "UPDATE reminders SET status='cancelled' WHERE id=$1 AND status='pending' RETURNING id, body",
            int(reminder_id),
        )
        if not row:
            return f"No pending reminder with id {reminder_id}."
        return f"Cancelled reminder #{row['id']}: {row['body']}"


class SnoozeReminderTool(Tool):
    name = "snooze_reminder"
    description = "Push a pending reminder to a new time."
    is_destructive = False

    schema = {
        "name": "snooze_reminder",
        "description": "Move a reminder's due time. Use after a reminder fires when Duke asks to be reminded again later.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "integer"},
                "new_due_at_iso": {"type": "string", "description": "ISO time. WAT is assumed if no tz."},
            },
            "required": ["reminder_id", "new_due_at_iso"],
        },
    }

    async def run(self, reminder_id: int, new_due_at_iso: str, **kwargs) -> str:
        try:
            new_due_utc = _parse_due(new_due_at_iso)
        except Exception as exc:
            return f"Error: could not parse new_due_at_iso ({exc})."
        pool = await _get_pool()
        row = await pool.fetchrow(
            """
            UPDATE reminders
            SET due_at=$2, status='pending', fired_at=NULL
            WHERE id=$1
            RETURNING id, body, due_at
            """,
            int(reminder_id), new_due_utc,
        )
        if not row:
            return f"No reminder with id {reminder_id}."
        when = row["due_at"].astimezone(WAT).strftime("%a %b %d, %I:%M %p WAT")
        return f"Snoozed reminder #{row['id']} to {when}: {row['body']}"


# ─── Internal: drained by the bot's polling loop via /alerts/reminders ───────

async def fetch_and_advance_due() -> list[dict]:
    """
    Atomically claim all reminders due now: for one-shots, flip to 'fired';
    for recurring, advance due_at to the next slot. Returns the list of
    reminders to deliver (each: id, user_id, body, repeat).
    """
    pool = await _get_pool()
    now_utc = datetime.now(timezone.utc)
    delivered: list[dict] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, user_id, body, due_at, repeat
                FROM reminders
                WHERE status = 'pending' AND due_at <= $1
                ORDER BY due_at ASC
                FOR UPDATE SKIP LOCKED
                """,
                now_utc,
            )
            for r in rows:
                rid = r["id"]
                nxt = advance(r["repeat"], r["due_at"])
                if nxt is None:
                    await conn.execute(
                        "UPDATE reminders SET status='fired', fired_at=$1 WHERE id=$2",
                        now_utc, rid,
                    )
                else:
                    # walk forward in case we're catching up from a long outage
                    while nxt <= now_utc:
                        nxt = advance(r["repeat"], nxt)
                        if nxt is None:
                            break
                    if nxt is None:
                        await conn.execute(
                            "UPDATE reminders SET status='fired', fired_at=$1 WHERE id=$2",
                            now_utc, rid,
                        )
                    else:
                        await conn.execute(
                            "UPDATE reminders SET due_at=$1, fired_at=$2 WHERE id=$3",
                            nxt, now_utc, rid,
                        )
                delivered.append({
                    "id": rid,
                    "user_id": r["user_id"],
                    "body": r["body"],
                    "repeat": r["repeat"],
                })
    return delivered
