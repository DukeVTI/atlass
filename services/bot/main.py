"""
Atlas Bot Service — Layer 2 Entry Point
-----------------------------------------
Telegram gateway using python-telegram-bot v20 (fully async, long-polling).

Startup sequence:
  1. Validate required environment variables (fail fast if missing)
  2. Build the Application with the bot token
  3. Register all command and message handlers
  4. Set bot command menu visible inside Telegram UI
  5. Start long-polling with automatic reconnection

Security:
  - All handlers are guarded by @require_auth (see auth.py)
  - ALLOWED_USER_IDS checked at every update — unauthorized users silently dropped
  - drop_pending_updates=True prevents stale message replay on restart
"""

import logging
import os
import sys

from dotenv import load_dotenv
from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from auth import ALLOWED_IDS
from handlers import (
    echo_text,
    error_handler,
    handle_edited_message,
    handle_unsupported_media,
    help_command,
    start_command,
    status_command,
)

# Load .env before anything else (no-op inside Docker where vars come from Compose)
load_dotenv()

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("atlas.bot")


# ─── Startup Validation ───────────────────────────────────────────────────────


def _validate_env() -> str:
    """
    Validate all required environment variables before starting the bot.
    Exits with a clear error message if anything is missing.
    Returns the bot token.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.critical(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Add it to your .env file and restart the service."
        )
        sys.exit(1)

    if not ALLOWED_IDS:
        logger.critical(
            "ALLOWED_USER_IDS is not set. "
            "The bot would reject ALL messages. Aborting startup."
        )
        sys.exit(1)

    return token


# ─── Bot Commands (visible in Telegram UI) ────────────────────────────────────

BOT_COMMANDS = [
    BotCommand("start", "Wake Atlas up"),
    BotCommand("status", "Check operational status"),
    BotCommand("help", "Show available commands"),
]


# ─── Entry Point ──────────────────────────────────────────────────────────────


def main() -> None:
    """Build and start the Atlas Telegram bot."""
    token = _validate_env()

    logger.info("Initializing Atlas Telegram Bot — Layer 2.")
    logger.info(
        "Authorized user ID(s): %s", ", ".join(str(uid) for uid in sorted(ALLOWED_IDS))
    )

    app: Application = ApplicationBuilder().token(token).build()

    # ── Commands ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))

    # ── Text messages (non-command) ───────────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_text))

    # ── Media / non-text messages ─────────────────────────────────────────────
    app.add_handler(
        MessageHandler(
            ~filters.TEXT & ~filters.COMMAND & filters.UpdateType.MESSAGES,
            handle_unsupported_media,
        )
    )

    # ── Edited messages ───────────────────────────────────────────────────────
    app.add_handler(
        MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_edited_message)
    )

    # ── Global error handler ──────────────────────────────────────────────────
    app.add_error_handler(error_handler)

    logger.info("All handlers registered. Starting long-poll loop.")

    app.run_polling(
        # Only receive the update types we actually handle
        allowed_updates=["message", "edited_message", "callback_query"],
        # Discard messages received while the bot was offline — prevents replay
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
