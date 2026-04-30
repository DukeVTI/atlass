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

from tools.registry import registry
from tools.web_search import WebSearchTool
from tools.calendar import CalendarReadTool, CalendarCreateTool
from tools.gmail import GmailReadTool, GmailDraftTool, GmailSendTool
from tools.paystack import PaystackBalanceTool, PaystackCustomerTool, PaystackTransactionsTool, PaystackTransferTool
from tools.local_file import LocalFileTool
from tools.whatsapp import WhatsAppReadTool, WhatsAppSendTool, WhatsAppContactSearchTool

# Register all tools
registry.register(WebSearchTool())
registry.register(CalendarReadTool())
registry.register(CalendarCreateTool())
registry.register(GmailReadTool())
registry.register(GmailDraftTool())
registry.register(GmailSendTool())
registry.register(PaystackBalanceTool())
registry.register(PaystackCustomerTool())
registry.register(PaystackTransactionsTool())
registry.register(PaystackTransferTool())
registry.register(LocalFileTool())
registry.register(WhatsAppReadTool())
registry.register(WhatsAppSendTool())
registry.register(WhatsAppContactSearchTool())

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
        butler.set_schemas(registry.get_schemas())
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


@app.post("/chat", tags=["chat"])
async def chat(request: ChatRequest):
    """
    Process a message from the Telegram bot and stream Atlas's response.

    Maintains per-user conversation history for contextual replies.
    Streams SSE events: status updates and the final message.
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

    async def event_generator():
        try:
            async for event_chunk in butler.run_stream(
                messages=history,
                user_id=user_id,
                session_id=f"user_{user_id}"
            ):
                yield event_chunk
                
                # Intercept the final message to store it in history
                if event_chunk.startswith("data: "):
                    event_str = event_chunk[6:].strip()
                    import json
                    try:
                        event = json.loads(event_str)
                        if event.get("type") == "message":
                            history.append({"role": "assistant", "content": event["content"]})
                            logger.info(
                                "Response for user %d: %r",
                                user_id,
                                event["content"][:120] + ("…" if len(event["content"]) > 120 else ""),
                            )
                    except json.JSONDecodeError:
                        pass
        except Exception as exc:
            logger.error("Unexpected error in butler loop: %s", exc, exc_info=True)
            history.pop()  # Don't store the failed user message
            import json
            yield f"data: {json.dumps({'type': 'message', 'content': 'An internal error occurred in the orchestrator.'})}\n\n"

    from fastapi.responses import StreamingResponse
    return StreamingResponse(event_generator(), media_type="text/event-stream")


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


# ─── Internal Alert Endpoints ─────────────────────────────────────────────────


@app.get("/alerts/emails", tags=["alerts"])
async def get_alert_emails() -> dict:
    """
    Internal endpoint for the bot's email alert loop.
    Returns recent unread emails as structured data — NO LLM involved.
    The bot's urgency scoring algorithm does the classification.
    """
    import asyncio
    from tools.google_auth import get_google_credentials
    from googleapiclient.discovery import build

    def _sync_fetch():
        creds = get_google_credentials()
        if not creds:
            return []
        service = build("gmail", "v1", credentials=creds)
        results = service.users().messages().list(
            userId="me",
            q="is:unread newer_than:1h",
            maxResults=20,
        ).execute()
        messages = results.get("messages", [])
        emails = []
        for msg in messages:
            full = service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            headers = full.get("payload", {}).get("headers", [])
            sender = next((h["value"] for h in headers if h["name"] == "From"), "")
            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
            emails.append({
                "id": msg["id"],
                "sender": sender,
                "subject": subject,
                "snippet": full.get("snippet", ""),
                "date_ms": int(full.get("internalDate", 0)),
            })
        return emails

    try:
        emails = await asyncio.get_event_loop().run_in_executor(None, _sync_fetch)
        return {"emails": emails}
    except Exception as e:
        logger.error("Failed to fetch emails for alert endpoint: %s", e)
        return {"emails": []}


@app.get("/alerts/calendar", tags=["alerts"])
async def get_alert_calendar() -> dict:
    """
    Internal endpoint for the bot's meeting reminder loop.
    Returns today's upcoming events with start times — NO LLM involved.
    """
    import asyncio
    from datetime import datetime, timezone, timedelta
    from tools.google_auth import get_google_credentials
    from googleapiclient.discovery import build

    def _sync_fetch():
        creds = get_google_credentials()
        if not creds:
            return []
        service = build("calendar", "v3", credentials=creds)
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=2)
        result = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=10,
        ).execute()
        events = []
        for evt in result.get("items", []):
            start = evt.get("start", {})
            events.append({
                "id": evt.get("id"),
                "summary": evt.get("summary", "Untitled"),
                "start": start.get("dateTime") or start.get("date"),
                "location": evt.get("location", ""),
            })
        return events

    try:
        events = await asyncio.get_event_loop().run_in_executor(None, _sync_fetch)
        return {"events": events}
    except Exception as e:
        logger.error("Failed to fetch calendar for alert endpoint: %s", e)
        return {"events": []}
