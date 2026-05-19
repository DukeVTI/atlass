import os
import json
import time
import secrets
import logging
import redis.asyncio as redis

logger = logging.getLogger("atlas.tools.security")

# Initialize Redis client using the environment variable mapped from Docker Compose
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# 16 hex chars = 64 bits of entropy. Birthday collision risk only after ~4 billion
# concurrent pending actions; combined with 1h TTL and per-action binding this
# is well past practical exploitability for a single-user system.
_CONFIRMATION_TTL_SECONDS = 3600


class ConfirmationManager:
    """
    Manages the Security Gate for destructive agentic actions.
    Temporarily caches tool execution parameters in Redis and pauses the orchestrator loop.
    """

    @staticmethod
    async def intercept(tool_name: str, inputs: dict) -> str:
        """
        Intercept a tool execution, cache it in Redis, return an ACTION PAUSED signal.
        """
        confirmation_id = secrets.token_hex(8).upper()  # 16 chars / 64 bits
        cache_data = {
            "tool_name": tool_name,
            "inputs": inputs,
            "created_at": int(time.time()),
        }
        await redis_client.setex(
            f"conf:{confirmation_id}",
            _CONFIRMATION_TTL_SECONDS,
            json.dumps(cache_data),
        )
        logger.warning(
            "Intercepted destructive action %s. Awaiting confirmation %s.",
            tool_name, confirmation_id,
        )
        return (
            f"[CONFIRM:{confirmation_id}] ACTION PAUSED: Awaiting explicit user "
            f"confirmation for ID {confirmation_id}. Ask Duke to confirm."
        )

    @staticmethod
    async def get_pending_action(confirmation_id: str) -> dict | None:
        """
        Retrieve a pending action. Normalizes ID (uppercase, stripped).
        """
        clean_id = str(confirmation_id).strip().upper()
        if not clean_id or not all(c in "0123456789ABCDEF" for c in clean_id):
            return None
        data = await redis_client.get(f"conf:{clean_id}")
        if data:
            return json.loads(data)
        return None

    @staticmethod
    async def clear_action(confirmation_id: str) -> None:
        clean_id = str(confirmation_id).strip().upper()
        if not clean_id:
            return
        await redis_client.delete(f"conf:{clean_id}")
