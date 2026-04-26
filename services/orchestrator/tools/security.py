import os
import uuid
import json
import logging
from typing import Any
import redis.asyncio as redis

logger = logging.getLogger("atlas.tools.security")

# Initialize Redis client using the environment variable mapped from Docker Compose
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

class ConfirmationManager:
    """
    Manages the Security Gate for destructive agentic actions.
    Temporarily caches tool execution parameters in Redis and pauses the orchestrator loop.
    """
    
    @staticmethod
    async def intercept(tool_name: str, inputs: dict) -> str:
        """
        Intercepts a tool execution, caches it in Redis, and returns an ACTION PAUSED signal.
        """
        confirmation_id = str(uuid.uuid4())[:8].upper() # 8-character ID for simplicity
        
        # Cache for 1 hour (3600 seconds)
        cache_data = {
            "tool_name": tool_name,
            "inputs": inputs
        }
        await redis_client.setex(f"conf:{confirmation_id}", 3600, json.dumps(cache_data))
        
        logger.warning(f"Intercepted destructive action {tool_name}. Awaiting confirmation {confirmation_id}.")
        return f"[CONFIRM:{confirmation_id}] ACTION PAUSED: Awaiting explicit user confirmation for ID {confirmation_id}. Ask Duke to confirm."

    @staticmethod
    async def get_pending_action(confirmation_id: str) -> dict | None:
        """
        Retrieves a pending action.
        """
        data = await redis_client.get(f"conf:{confirmation_id}")
        if data:
            return json.loads(data)
        return None

    @staticmethod
    async def clear_action(confirmation_id: str):
        """
        Deletes the action from cache.
        """
        await redis_client.delete(f"conf:{confirmation_id}")
