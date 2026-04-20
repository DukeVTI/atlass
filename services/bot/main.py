"""
Atlas Bot Service — Layer 3 Entry Point
-----------------------------------------
Telegram gateway using python-telegram-bot v20 (fully async, long-polling).
Now wired to the orchestrator service for real AI responses.
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
    clear_command,
    error_handler,
    handle_edited_message,
    handle_text,
    handle_unsupported_media,
    help_command,
    start_command,
    status_command,
)

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("atlas.bot")


def _validate_env() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.critical("TELEGRAM_BOT_TOKEN is not set. Aborting.")
        sys.exit(1)
    if not ALLOWED_IDS:
        logger.critical("ALLOWED_USER_IDS is not set. Aborting.")
        sys.exit(1)
    orchestrator_url = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8001")
    logger.info("Orchestrator URL: %s", orchestrator_url)
    return token


BOT_COMMANDS = [
    BotCommand("start", "Wake Atlas up"),
    BotCommand("status", "Check operational status"),
    BotCommand("clear", "Clear conversation history"),
    BotCommand("help", "Show available commands"),
]


def main() -> None:
    token = _validate_env()

    logger.info("Initializing Atlas Telegram Bot — Layer 3.")
    logger.info(
        "Authorized user(s): %s", ", ".join(str(uid) for uid in sorted(ALLOWED_IDS))
    )

    app: Application = ApplicationBuilder().token(token).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("clear", clear_command))

    # Text messages → orchestrator
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Non-text media
    app.add_handler(
        MessageHandler(
            ~filters.TEXT & ~filters.COMMAND & filters.UpdateType.MESSAGES,
            handle_unsupported_media,
        )
    )

    # Edited messages
    app.add_handler(
        MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_edited_message)
    )

    app.add_error_handler(error_handler)

    logger.info("All handlers registered. Starting long-poll loop.")

    app.run_polling(
        allowed_updates=["message", "edited_message", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
