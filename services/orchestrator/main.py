"""
Atlas Orchestrator — Layer 3 Main
-----------------------------------
FastAPI service: the brain of Atlas.

Receives messages from the Telegram bot, runs them through the butler loop
with Claude Haiku 3, maintains per-user conversation history (working memory),
and returns Atlas's response.

Working memory: in-process dict keyed by user_id. Stateless across restarts.
Replaced with proper persistent memory (ChromaDB + Postgres) in Layer 7.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from butler_loop import ButlerLoop
from claude_client import ClaudeClient, ClaudeError

load_dotenv()

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("atlas.orchestrator")

# ─── Working Memory ───────────────────────────────────────────────────────────
# Per-user conversation history. In-process, cleared on restart.
# Max messages kept per user to stay within Claude's context window.

_history: dict[int, list[dict]] = {}
MAX_HISTORY = 20  # ~10 conversation turns (user + assistant pairs)

# ─── Global Service Instances ─────────────────────────────────────────────────

claude_client: ClaudeClient | None = None
butler: ButlerLoop | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global claude_client, butler
    logger.info("Atlas Orchestrator starting up (Layer 3)…")

    try:
        claude_client = ClaudeClient()
        butler = ButlerLoop(claude=claude_client)
        logger.info(
            "Butler loop ready. Model: %s | Max tokens: %d | Temp: %.1f",
            claude_client.model,
            claude_client.max_tokens,
            claude_client.temperature,
        )
    except ValueError as exc:
        logger.critical("Orchestrator startup failed: %s", exc)
        sys.exit(1)

    yield

    logger.info("Atlas Orchestrator shutting down.")


# ─── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Atlas Orchestrator",
    description="Brain of the Atlas AI butler — LLM routing, butler loop, tool execution.",
    version="3.0.0",
    lifespan=lifespan,
)


# ─── Models ───────────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    user_id: int
    username: Optional[str] = None


class ChatResponse(BaseModel):
    response: str


class HistoryClearResponse(BaseModel):
    status: str
    user_id: int
    messages_cleared: int


# ─── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Liveness probe."""
    return {
        "status": "ok",
        "service": "atlas-orchestrator",
        "layer": 3,
        "model": claude_client.model if claude_client else "not initialized",
    }


@app.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Process a message from the Telegram bot and return Atlas's response.

    Maintains per-user conversation history for contextual replies.
    Runs the full butler loop: LLM → tool (if needed) → LLM → response.
    """
    if not butler or not claude_client:
        raise HTTPException(status_code=503, detail="Orchestrator not ready.")

    user_id = request.user_id
    message = request.message.strip()

    logger.info(
        "Incoming message — user: %s (%d) | text: %r",
        request.username or "unknown",
        user_id,
        message[:120] + ("…" if len(message) > 120 else ""),
    )

    # Retrieve or create conversation history for this user
    history = _history.setdefault(user_id, [])

    # Append the new user message
    history.append({"role": "user", "content": message})

    # Trim history to keep within context window limits
    if len(history) > MAX_HISTORY:
        _history[user_id] = history[-MAX_HISTORY:]
        history = _history[user_id]

    try:
        response_text = await butler.run(messages=history, user_id=user_id)
    except ClaudeError as exc:
        # User-friendly Claude error — ClaudeError already has a butler-style message
        logger.error("ClaudeError for user %d: %s", user_id, exc)
        history.pop()  # Don't store the failed exchange
        return ChatResponse(response=str(exc))
    except Exception as exc:
        logger.error(
            "Unexpected error in butler loop for user %d: %s",
            user_id,
            exc,
            exc_info=True,
        )
        history.pop()  # Don't store the failed exchange
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred in the orchestrator.",
        )

    # Store Atlas's response in history
    history.append({"role": "assistant", "content": response_text})

    logger.info(
        "Response for user %d: %r",
        user_id,
        response_text[:120] + ("…" if len(response_text) > 120 else ""),
    )

    return ChatResponse(response=response_text)


@app.delete("/chat/{user_id}/history", response_model=HistoryClearResponse, tags=["chat"])
async def clear_history(user_id: int) -> HistoryClearResponse:
    """
    Clear the conversation history for a specific user.
    Useful for testing and for giving Atlas a fresh context.
    """
    history = _history.pop(user_id, [])
    count = len(history)
    logger.info("Cleared %d history messages for user %d.", count, user_id)
    return HistoryClearResponse(
        status="cleared", user_id=user_id, messages_cleared=count
    )
