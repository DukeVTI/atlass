"""
Atlas Bot — Message Handlers
------------------------------
All Telegram interaction logic for Layer 2.

At this layer Atlas echoes messages back to confirm the read/write pipeline
is healthy end-to-end. In Layer 3 the echo will be replaced by a call to
the orchestrator service (Claude Haiku 3 + tool loop).

Edge cases handled:
- Telegram's 4096-character message limit
- All non-text media types (photo, doc, voice, video, sticker, audio, location, contact)
- Flood control (RetryAfter) — logged and silently absorbed
- Network timeouts (TimedOut) — logged and silently absorbed
- All other exceptions — logged, user notified politely
- Updates with no message object (edited messages, channel posts, etc.)
"""

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TimedOut
from telegram.ext import ContextTypes

from auth import require_auth

logger = logging.getLogger("atlas.bot.handlers")

# Telegram hard limit for outgoing messages
TELEGRAM_MAX_LENGTH = 4096


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _truncate(text: str, limit: int = TELEGRAM_MAX_LENGTH - 20) -> str:
    """Truncate text to fit within Telegram's character limit."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _classify_media(message) -> str:  # type: ignore[no-untyped-def]
    """Return a human-readable label for the media type in a message."""
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
        emoji = message.sticker.emoji or ""
        return f"sticker {emoji}".strip()
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


# ─── Command Handlers ─────────────────────────────────────────────────────────


@require_auth
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — send a butler-style greeting."""
    user = update.effective_user
    logger.info("User %d (%s) triggered /start.", user.id, user.first_name)
    await update.message.reply_text(
        f"Good day, {user.first_name}.\n\n"
        "I am Atlas — your personal AI butler, running on private infrastructure.\n\n"
        "I am currently in Layer 2 (echo mode) while my intelligence is being "
        "assembled. Everything you send me will be echoed back as confirmation "
        "that the pipeline is healthy.\n\n"
        "Type /status to see my current state, or /help for available commands."
    )


@require_auth
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help — list available commands."""
    logger.info("User %d triggered /help.", update.effective_user.id)
    await update.message.reply_text(
        "Available commands:\n\n"
        "/start — Wake me up\n"
        "/status — Check my operational status\n"
        "/help — Show this message\n\n"
        "Send any text and I will echo it back.\n"
        "Multi-modal intelligence arrives in Layer 3."
    )


@require_auth
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status — report current layer and service state."""
    logger.info("User %d triggered /status.", update.effective_user.id)
    text = (
        "🟢 *Atlas is online*\n\n"
        "▸ Layer: 2 — Telegram Gateway\n"
        "▸ Security gate: Active\n"
        "▸ LLM: Not yet connected \\(Layer 3\\)\n"
        "▸ Mode: Echo"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


# ─── Message Handlers ─────────────────────────────────────────────────────────


@require_auth
async def echo_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle incoming text messages — echo them back.

    This is a temporary placeholder. In Layer 3 this function will forward
    the message to the orchestrator service and stream the AI response back.
    """
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    text = update.message.text

    logger.info(
        "Text from user %d (%s): %r",
        user.id,
        user.first_name,
        text[:100] + ("…" if len(text) > 100 else ""),
    )

    reply = _truncate(f"[Echo] {text}")
    await update.message.reply_text(reply)


@require_auth
async def handle_unsupported_media(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle all non-text, non-command messages gracefully.

    Identifies the media type and informs the user that it will be
    supported in a later layer (multi-modal processing).
    """
    if not update.message:
        return

    user = update.effective_user
    media_type = _classify_media(update.message)

    logger.info(
        "User %d sent unsupported media type: %s.", user.id, media_type
    )

    await update.message.reply_text(
        f"I've received your {media_type}, sir.\n\n"
        "Full multi-modal processing — images, documents, and voice — "
        "will be available in a later layer."
    )


@require_auth
async def handle_edited_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle edited messages.

    Users sometimes edit messages after sending them. For now we acknowledge
    the edit without reprocessing it, maintaining conversational awareness.
    """
    if not update.edited_message:
        return

    logger.info(
        "User %d edited a message: %r",
        update.effective_user.id,
        (update.edited_message.text or "")[:80],
    )
    # Don't reply — just silently note it. Avoids confusing echo spam.


# ─── Global Error Handler ─────────────────────────────────────────────────────


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Global catch-all error handler.

    Handles Telegram-specific exceptions gracefully without crashing the bot.
    For unknown errors: logs the full traceback and notifies the user if possible.
    """
    error = context.error

    # Flood control — Telegram asked us to back off
    if isinstance(error, RetryAfter):
        logger.warning(
            "Telegram flood control hit. RetryAfter: %.1f seconds.", error.retry_after
        )
        return

    # Network timeout — transient, the library will retry
    if isinstance(error, TimedOut):
        logger.warning("Telegram request timed out — library will retry automatically.")
        return

    # Bot was blocked or kicked by the user
    if isinstance(error, Forbidden):
        logger.warning("Bot was blocked or kicked by a user.")
        return

    # Unknown error — log the full traceback
    logger.error(
        "Unhandled exception in update handler.",
        exc_info=context.error,
    )

    # Try to notify the user if we have a message context
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "I've run into an unexpected issue, sir. "
                "The incident has been logged. Please try again shortly."
            )
        except Exception:
            # Don't let the error handler itself crash the bot
            pass
