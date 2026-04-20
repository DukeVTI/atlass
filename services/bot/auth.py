"""
Atlas Bot — Auth Module
------------------------
Enforces the strict security gate required by AGENTS.md.

Only users whose numeric Telegram ID appears in ALLOWED_USER_IDS are
permitted to interact with Atlas. Every other user is silently ignored —
no error message, no acknowledgement. This prevents probing and leaking
the bot's existence.

Loaded once at startup from the environment. Restart required if IDs change.
"""

import logging
import os
from functools import wraps
from typing import Any, Callable

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("atlas.bot.auth")


def _load_allowed_ids() -> frozenset[int]:
    """
    Parse ALLOWED_USER_IDS from the environment variable.

    Format: comma-separated integer Telegram user IDs.
    Example: ALLOWED_USER_IDS=123456789,987654321

    Edge cases handled:
    - Whitespace around IDs is stripped
    - Non-integer values are logged and skipped
    - Empty string results in an empty set (bot rejects everyone)
    """
    raw = os.getenv("ALLOWED_USER_IDS", "").strip()
    if not raw:
        logger.critical(
            "ALLOWED_USER_IDS is not set! The bot will silently reject ALL "
            "messages. Set this in your .env file immediately."
        )
        return frozenset()

    parsed: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            parsed.add(int(part))
        except ValueError:
            logger.warning(
                "Invalid Telegram user ID %r in ALLOWED_USER_IDS — skipping.", part
            )

    if parsed:
        logger.info(
            "Security gate initialized. Authorized user ID(s): %s",
            ", ".join(str(uid) for uid in sorted(parsed)),
        )
    return frozenset(parsed)


# Evaluated once at import time — fast and thread-safe for all handlers
ALLOWED_IDS: frozenset[int] = _load_allowed_ids()


def is_allowed(user_id: int) -> bool:
    """Return True if the given Telegram user ID is on the allow-list."""
    return user_id in ALLOWED_IDS


def require_auth(handler: Callable) -> Callable:
    """
    Decorator for python-telegram-bot v20 async handlers.

    Silently drops any update whose sender is not in ALLOWED_IDS.
    Works with Update objects that may have no user (channel posts, etc.).

    Usage:
        @require_auth
        async def my_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            ...
    """

    @wraps(handler)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        # Guard: some update types have no user (e.g. channel posts)
        if update.effective_user is None:
            logger.debug("Received update with no user attached — dropping silently.")
            return

        uid = update.effective_user.id
        if not is_allowed(uid):
            # Silent rejection — do NOT reply or acknowledge the unauthorized user
            logger.debug(
                "Unauthorized access attempt from Telegram user %d — rejected silently.",
                uid,
            )
            return

        return await handler(update, context, *args, **kwargs)

    return wrapper
