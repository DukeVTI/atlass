"""
history_store.py — Persistent conversation history for Atlas Orchestrator.

Replaces the in-process _history dict with:
  - Postgres: sliding window of last MAX_TURNS messages per user
  - ChromaDB: semantic summary of messages beyond the window, injected as context

Summary generation uses Claude Haiku via the existing ClaudeClient.
"""

import logging
import os
from typing import Optional

import asyncpg
import httpx

logger = logging.getLogger("atlas.history_store")

MAX_TURNS = 20          # message pairs kept in Postgres window
SUMMARY_THRESHOLD = 30  # trigger summarisation when total turns exceed this
CHROMA_URL = os.getenv("CHROMA_URL", "http://chromadb:8000")
POSTGRES_DSN = os.getenv("POSTGRES_DSN")  # e.g. postgresql://atlas:pass@postgres:5432/atlas


# ─── Postgres Pool (module-level, initialised on first use) ──────────────────

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not POSTGRES_DSN:
            raise ValueError("POSTGRES_DSN environment variable is not set")
        dsn = POSTGRES_DSN.replace("+asyncpg", "")
        _pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
    return _pool


# ─── Public API ───────────────────────────────────────────────────────────────


async def load_history(user_id: int) -> list[dict]:
    """
    Load the last MAX_TURNS messages for user_id from Postgres.
    Returns list of {role, content} dicts ordered oldest→newest.
    """
    pool = await _get_pool()
    rows = await pool.fetch(
        """
        SELECT role, content FROM (
            SELECT role, content, created_at
            FROM conversation_turns
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
        ) sub
        ORDER BY created_at ASC
        """,
        user_id, MAX_TURNS
    )
    return [{"role": r["role"], "content": r["content"]} for r in rows]


async def append_turn(user_id: int, role: str, content: str) -> None:
    """
    Persist a single turn to Postgres.
    After insert, checks if summarisation should be triggered.
    """
    pool = await _get_pool()
    await pool.execute(
        "INSERT INTO conversation_turns (user_id, role, content) VALUES ($1, $2, $3)",
        user_id, role, content
    )
    await _maybe_summarise(user_id, pool)


async def load_summary(user_id: int) -> Optional[str]:
    """
    Retrieve the latest semantic summary for user_id from ChromaDB.
    Returns None if no summary exists yet.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{CHROMA_URL}/api/v1/collections/conversation_summaries/query",
                json={
                    "query_texts": [f"user_{user_id}_summary"],
                    "n_results": 1,
                    "where": {"user_id": str(user_id)},
                }
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            docs = data.get("documents", [[]])[0]
            return docs[0] if docs else None
    except Exception as exc:
        logger.warning("Could not fetch summary from ChromaDB: %s", exc)
        return None


# ─── Internal: Summarisation Logic ───────────────────────────────────────────


async def _maybe_summarise(user_id: int, pool: asyncpg.Pool) -> None:
    """
    If total turn count exceeds SUMMARY_THRESHOLD, summarise all turns
    beyond the MAX_TURNS window and store the summary in ChromaDB,
    then delete those old rows from Postgres.
    """
    total = await pool.fetchval(
        "SELECT COUNT(*) FROM conversation_turns WHERE user_id = $1",
        user_id
    )
    if total <= SUMMARY_THRESHOLD:
        return

    overflow_count = total - MAX_TURNS
    old_rows = await pool.fetch(
        """
        SELECT id, role, content FROM conversation_turns
        WHERE user_id = $1
        ORDER BY created_at ASC
        LIMIT $2
        """,
        user_id, overflow_count
    )

    if not old_rows:
        return

    # Build a transcript of the old messages
    transcript = "\n".join(
        f"{r['role'].upper()}: {r['content']}" for r in old_rows
    )

    summary = await _summarise_via_claude(transcript)
    if not summary:
        logger.warning("Summarisation returned empty for user %d — skipping.", user_id)
        return

    # Store summary in ChromaDB (upsert pattern — delete old, insert new)
    await _upsert_summary_in_chroma(user_id, summary)

    # Delete the summarised rows from Postgres
    old_ids = [r["id"] for r in old_rows]
    await pool.execute(
        "DELETE FROM conversation_turns WHERE id = ANY($1::bigint[])",
        old_ids
    )
    logger.info(
        "Summarised %d old turns for user %d and pruned from Postgres.",
        len(old_ids), user_id
    )


async def _summarise_via_claude(transcript: str) -> Optional[str]:
    """
    Call Claude Haiku directly (not via ButlerLoop) to produce a summary.
    Kept intentionally simple — no tool use, no history.
    """
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=512,
            system=(
                "You are a memory compression system for a personal AI butler called Atlas. "
                "Your job is to compress conversation history into a dense, factual summary "
                "that preserves: user preferences revealed, tasks completed, decisions made, "
                "and any personal context shared. Be concise. Plain prose. No bullet points."
            ),
            messages=[{
                "role": "user",
                "content": f"Compress the following conversation into a summary:\n\n{transcript}"
            }]
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.error("Claude summarisation failed: %s", exc)
        return None


async def _upsert_summary_in_chroma(user_id: int, summary: str) -> None:
    """
    Store or replace the user's summary document in the
    'conversation_summaries' ChromaDB collection.
    """
    doc_id = f"summary_user_{user_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Ensure collection exists
            await client.post(
                f"{CHROMA_URL}/api/v1/collections",
                json={"name": "conversation_summaries", "get_or_create": True}
            )
            # Delete existing summary for this user if present
            await client.post(
                f"{CHROMA_URL}/api/v1/collections/conversation_summaries/delete",
                json={"ids": [doc_id]}
            )
            # Insert fresh summary
            await client.post(
                f"{CHROMA_URL}/api/v1/collections/conversation_summaries/add",
                json={
                    "ids": [doc_id],
                    "documents": [summary],
                    "metadatas": [{"user_id": str(user_id)}]
                }
            )
    except Exception as exc:
        logger.error("Failed to upsert summary in ChromaDB: %s", exc)
