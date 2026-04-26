"""
Atlas Bot — Message Handlers (Layer 3)
----------------------------------------
Replaces the echo handler with a real call to the orchestrator service.
All other handlers (commands, media, error) remain unchanged from Layer 2.

Edge cases handled:
- Orchestrator not reachable — user gets a butler-style error message
- Orchestrator returns 5xx — user notified gracefully
- Network timeout — user notified, conversation not stored
- Telegram's 4096-character message length limit
- All non-text media types (photo, doc, voice, video, sticker, etc.)
- Edited messages — silently logged, not reprocessed
- Flood control (RetryAfter) and network timeouts
"""

import logging
import os

import httpx
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import Forbidden, RetryAfter, TimedOut
from telegram.ext import ContextTypes

from auth import require_auth

logger = logging.getLogger("atlas.bot.handlers")

TELEGRAM_MAX_LENGTH = 4096
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8001")


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _truncate(text: str, limit: int = TELEGRAM_MAX_LENGTH - 10) -> str:
    """Truncate to Telegram's character limit."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _classify_media(message) -> str:  # type: ignore[no-untyped-def]
    """Identify the media type of a non-text message."""
    if message.photo:
        return "photo"
    if message.document:
        return f"document ({message.document.file_name or 'unnamed'})"
    if message.voice:
        return "voice note"
    if message.video:
        return "video"
    if message.video_note:
        return "video note"
    if message.sticker:
        return f"sticker {message.sticker.emoji or ''}".strip()
    if message.audio:
        return "audio file"
    if message.animation:
        return "GIF"
    if message.location:
        return "location"
    if message.venue:
        return "venue"
    if message.contact:
        return "contact"
    if message.poll:
        return "poll"
    if message.dice:
        return "dice roll"
    return "message"


import json

async def _call_orchestrator(user_id: int, username: str, message: str):
    """
    POST the message to the orchestrator and yield SSE events.
    Yields dicts with 'type' (status|message|error) and 'content'.
    """
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{ORCHESTRATOR_URL}/chat",
                json={
                    "message": message,
                    "user_id": user_id,
                    "username": username,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:].strip())
                        except json.JSONDecodeError:
                            pass

    except httpx.TimeoutException:
        logger.warning("Orchestrator timed out for user %d.", user_id)
        yield {
            "type": "error",
            "content": "My apologies, sir — I took too long to gather my thoughts. Please try again."
        }
    except httpx.HTTPStatusError as exc:
        logger.error("Orchestrator HTTP error: %s", exc)
        yield {
            "type": "error",
            "content": "I encountered an internal error while processing that, sir."
        }
    except Exception as exc:
        logger.error("Unexpected error for user %d: %s", user_id, exc)
        yield {
            "type": "error",
            "content": "Something unexpected happened, sir. Please try again shortly."
        }


# ─── Command Handlers ─────────────────────────────────────────────────────────


@require_auth
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — send a butler greeting."""
    user = update.effective_user
    logger.info("User %d triggered /start.", user.id)
    await update.message.reply_text(
        f"Good day, {user.first_name}.\n\n"
        "I am Atlas — your personal AI butler, running on private infrastructure.\n\n"
        "I am now fully operational. Ask me anything."
    )


@require_auth
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help."""
    logger.info("User %d triggered /help.", update.effective_user.id)
    await update.message.reply_text(
        "Available commands:\n\n"
        "/start — Wake me up\n"
        "/status — Check my operational status\n"
        "/clear — Clear our conversation history\n"
        "/help — Show this message\n\n"
        "Or simply send me any message and I will respond."
    )


@require_auth
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status — report current service state."""
    logger.info("User %d triggered /status.", update.effective_user.id)
    text = (
        "🟢 *Atlas is online*\n\n"
        "▸ Layer: 3 — LLM Connected\n"
        "▸ Model: Claude Haiku 3\n"
        "▸ Security gate: Active\n"
        "▸ Memory: Working \\(in\\-process\\)\n"
        "▸ Tools: Not yet connected \\(Layer 4\\)"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


@require_auth
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clear — wipe conversation history for this user."""
    user = update.effective_user
    logger.info("User %d requested history clear.", user.id)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                f"{ORCHESTRATOR_URL}/chat/{user.id}/history"
            )
            resp.raise_for_status()
        await update.message.reply_text(
            "Done, sir. Our conversation history has been wiped. "
            "I am starting fresh."
        )
    except Exception as exc:
        logger.error("Failed to clear history for user %d: %s", user.id, exc)
        await update.message.reply_text(
            "I was unable to clear the history at this time, sir. Please try again."
        )


# ─── Message Handlers ─────────────────────────────────────────────────────────


@require_auth
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle incoming text messages.
    Forwards to the orchestrator and sends back Atlas's response.
    Shows a "typing…" action while waiting.
    """
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    message = update.message.text

    logger.info(
        "Text from user %d (%s): %r",
        user.id,
        user.first_name,
        message[:100] + ("…" if len(message) > 100 else ""),
    )

    # Show typing indicator immediately
    await update.message.chat.send_action(ChatAction.TYPING)
    bot_msg = await update.message.reply_text("⏳ _Thinking..._", parse_mode=ParseMode.MARKDOWN)

    try:
        async for event in _call_orchestrator(
            user_id=user.id,
            username=user.username or user.first_name,
            message=message,
        ):
            if event.get("type") == "status":
                await bot_msg.edit_text(f"⏳ {event['content']}")
                await update.message.chat.send_action(ChatAction.TYPING)
            else:
                await bot_msg.edit_text(_truncate(event["content"]))
    except Exception as e:
        logger.error("Failed to process stream: %s", e)
        await bot_msg.edit_text("Something unexpected happened, sir. Please try again.")


@require_auth
async def handle_unsupported_media(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle all non-text messages gracefully.
    Informs the user that multi-modal processing comes in a later layer.
    """
    if not update.message:
        return

    user = update.effective_user
    media_type = _classify_media(update.message)

    logger.info("User %d sent unsupported media: %s.", user.id, media_type)
    await update.message.reply_text(
        f"I've received your {media_type}, sir.\n\n"
        "Full multi-modal processing — images, documents, and voice — "
        "will be available in a later layer."
    )


@require_auth
async def handle_edited_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle edited messages — silently log, do not reprocess."""
    if not update.edited_message:
        return
    logger.info(
        "User %d edited a message: %r",
        update.effective_user.id,
        (update.edited_message.text or "")[:80],
    )


# ─── Global Error Handler ─────────────────────────────────────────────────────


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Catch-all error handler for the Telegram bot.
    Handles platform errors gracefully without crashing.
    """
    error = context.error

    if isinstance(error, RetryAfter):
        logger.warning("Telegram flood control: retry after %.1fs.", error.retry_after)
        return

    if isinstance(error, TimedOut):
        logger.warning("Telegram request timed out — library will retry.")
        return

    if isinstance(error, Forbidden):
        logger.warning("Bot was blocked or kicked by a user.")
        return

    logger.error("Unhandled exception in Telegram handler.", exc_info=error)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "I've run into an unexpected issue, sir. "
                "The incident has been logged. Please try again shortly."
            )
        except Exception:
            pass
