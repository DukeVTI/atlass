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
import random
from datetime import datetime, timezone, timedelta

import httpx
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ChatAction, ParseMode
from telegram.error import Forbidden, RetryAfter, TimedOut
from telegram.ext import ContextTypes

from auth import require_auth, ALLOWED_IDS
from transcribe import transcribe_audio

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


async def _process_stream(stream, bot_msg, chat):
    """
    Consumes the SSE generator.
    Parses [CONFIRM:ID] tags into inline keyboards.
    """
    reply_markup = None
    try:
        async for event in stream:
            if event.get("type") == "status":
                await bot_msg.edit_text(f"⏳ {event['content']}")
                await chat.send_action(ChatAction.TYPING)
            else:
                content = event["content"]
                
                # Check for confirmation tag from security.py
                if "[CONFIRM:" in content:
                    match = re.search(r"\[CONFIRM:([^\]]+)\]", content)
                    if match:
                        conf_id = match.group(1)
                        content = content.replace(match.group(0), "").strip()
                        keyboard = [
                            [
                                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{conf_id}"),
                                InlineKeyboardButton("❌ Decline", callback_data=f"reject_{conf_id}")
                            ]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                
                await bot_msg.edit_text(_truncate(content), reply_markup=reply_markup)
    except Exception as e:
        logger.error("Failed to process stream loop: %s", e)
        await bot_msg.edit_text("Something unexpected happened, sir. Please try again.")

# ─── Command Handlers ─────────────────────────────────────────────────────────


@require_auth
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — send a butler greeting."""
    user = update.effective_user
    logger.info("User %d triggered /start.", user.id)
    
    keyboard = [
        [KeyboardButton("📧 Read Emails"), KeyboardButton("💬 WhatsApp Inbox")],
        [KeyboardButton("🧠 My Status"), KeyboardButton("🧹 Clear History")],
        [KeyboardButton("❓ Help")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        f"Good day, {user.first_name}.\n\n"
        "I am Atlas — your personal AI butler, running on private infrastructure.\n\n"
        "I am now fully operational. Ask me anything.",
        reply_markup=reply_markup
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

    # Map menu buttons to system prompts
    button_mapping = {
        "📧 Read Emails": "Check my recent unread emails and summarise the important ones.",
        "💬 WhatsApp Inbox": "Check my WhatsApp inbox for recent messages.",
        "🧠 My Status": "Show me your current operational status and any pending tasks.",
        "🧹 Clear History": "/clear", # Will trigger the clear flow
        "❓ Help": "/help"
    }
    
    if message in button_mapping:
        mapped = button_mapping[message]
        if mapped.startswith("/"):
            # Re-route to command handler logic
            if mapped == "/clear": return await clear_command(update, context)
            if mapped == "/help": return await help_command(update, context)
        message = mapped

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
        stream = _call_orchestrator(
            user_id=user.id,
            username=user.username or user.first_name,
            message=message,
        )
        await _process_stream(stream, bot_msg, update.message.chat)
    except Exception as e:
        logger.error("Failed to process text stream: %s", e)
        await bot_msg.edit_text("Something unexpected happened, sir. Please try again.")

@require_auth
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle inline keyboard button clicks for security gate actions.
    """
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = update.effective_user
    
    if data.startswith("approve_"):
        conf_id = data.split("_")[1]
        action_msg = f"Approve action {conf_id}"
    elif data.startswith("reject_"):
        conf_id = data.split("_")[1]
        action_msg = f"Reject action {conf_id}"
    else:
        return
        
    # Remove the inline keyboard to prevent double-clicks
    await query.edit_message_reply_markup(reply_markup=None)
    
    # Process it as if the user typed the command
    await update.effective_chat.send_action(ChatAction.TYPING)
    bot_msg = await update.effective_chat.send_message("⏳ _Processing..._", parse_mode=ParseMode.MARKDOWN)
    
    try:
        stream = _call_orchestrator(
            user_id=user.id,
            username=user.username or user.first_name,
            message=action_msg,
        )
        await _process_stream(stream, bot_msg, update.effective_chat)
    except Exception as e:
        logger.error("Failed to process callback stream: %s", e)
        await bot_msg.edit_text("Something unexpected happened, sir. Please try again.")


@require_auth
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle incoming Telegram voice notes.
    Downloads the OGG file, transcribes it via Groq/faster-whisper,
    then forwards the transcript to the orchestrator exactly like a text message.
    """
    if not update.message or not update.message.voice:
        return

    user = update.effective_user
    logger.info("Voice note from user %d — duration: %ds.", user.id, update.message.voice.duration)

    await update.message.chat.send_action(ChatAction.TYPING)
    bot_msg = await update.message.reply_text("🎙️ _Transcribing..._", parse_mode=ParseMode.MARKDOWN)

    try:
        # Download OGG bytes from Telegram
        voice_file = await update.message.voice.get_file()
        ogg_bytes = await voice_file.download_as_bytearray()

        # Transcribe via Groq → faster-whisper fallback
        transcript = await transcribe_audio(bytes(ogg_bytes))

        if not transcript:
            await bot_msg.edit_text("I couldn't make out what you said, sir. Please try again.")
            return

        logger.info("Transcript for user %d: %r", user.id, transcript[:100])

        # Update message to show the transcript, then process
        await bot_msg.edit_text(f"🎙️ _{transcript}_", parse_mode=ParseMode.MARKDOWN)

        # Route to orchestrator — prefix so Claude knows it came from audio
        prefixed = f"[Voice Note]: {transcript}"
        stream = _call_orchestrator(
            user_id=user.id,
            username=user.username or user.first_name,
            message=prefixed,
        )
        await _process_stream(stream, bot_msg, update.message.chat)

    except Exception as e:
        logger.error("Voice note handling failed for user %d: %s", user.id, e)
        await bot_msg.edit_text(
            "I had trouble processing your voice note, sir. "
            "Please try again or type your message."
        )


@require_auth
async def handle_unsupported_media(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle all non-text, non-voice messages gracefully.
    Informs the user that multi-modal processing comes in a later layer.
    """
    if not update.message:
        return

    user = update.effective_user
    media_type = _classify_media(update.message)

    logger.info("User %d sent unsupported media: %s.", user.id, media_type)
    await update.message.reply_text(
        f"I've received your {media_type}, sir.\n\n"
        "Full multi-modal processing — images, documents, and video — "
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


# ─── Morning Briefing Scheduler ───────────────────────────────────────────────

BRIEFING_PROMPT = (
    "[SYSTEM: MORNING BRIEFING MODE]\n"
    "Good morning. Please deliver the morning briefing. "
    "Use your tools to: "
    "(1) fetch today's Google Calendar agenda, "
    "(2) check the Gmail inbox for important unread emails, "
    "(3) fetch the Paystack balance and any overnight transactions. "
    "Synthesise everything into a clean, structured morning briefing for Duke. "
    "Be concise, professional, and butler-like. Lead with the most urgent items."
)


async def _send_briefing_now(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Actually sends the morning briefing to all authorised users."""
    logger.info("Morning briefing firing now.")
    
    for user_id in ALLOWED_IDS:
        try:
            bot_msg = await context.bot.send_message(
                chat_id=user_id,
                text="🌅 _Preparing your morning briefing..._",
                parse_mode=ParseMode.MARKDOWN,
            )

            final_text = "Morning briefing unavailable — please try again shortly."
            async for event in _call_orchestrator(
                user_id=user_id,
                username="Duke",
                message=BRIEFING_PROMPT,
            ):
                if event.get("type") == "message":
                    final_text = event["content"]

            await bot_msg.edit_text(_truncate(final_text))
            logger.info("Morning briefing delivered to user %d.", user_id)
        except Exception as e:
            logger.error("Failed to deliver morning briefing to user %d: %s", user_id, e)


async def schedule_random_briefing(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Daily trigger job that fires at 6:30am WAT.
    Picks a random offset (0–60 min) and schedules the actual briefing
    as a one-shot job within the 6:30–7:30am window.
    """
    offset_minutes = random.randint(0, 60)
    logger.info(
        "Briefing scheduler triggered. Actual delivery in %d min (%.f:%02.f WAT).",
        offset_minutes,
        6 + (30 + offset_minutes) // 60,
        (30 + offset_minutes) % 60,
    )
    context.job_queue.run_once(
        _send_briefing_now,
        when=timedelta(minutes=offset_minutes),
        name="morning_briefing_delivery",
    )
