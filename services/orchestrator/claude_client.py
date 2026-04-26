"""
Atlas — Claude Haiku 3 Client
-------------------------------
Single LLM provider. No routing. No fallbacks. Claude Haiku 3 for all tasks.
Per AGENTS.md: Anthropic Claude Haiku 3 (claude-3-haiku-20240307) — sole LLM.

Wraps the Anthropic async client with error handling and structured return types.
"""

import logging
import os

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    RateLimitError,
)
from anthropic.types import Message

from system_prompt import SYSTEM_PROMPT

logger = logging.getLogger("atlas.orchestrator.claude")


class ClaudeError(Exception):
    """Raised when Claude fails in a way the butler loop cannot recover from."""

    pass


class ClaudeClient:
    """
    Async wrapper around the Anthropic Messages API.

    All calls inject the hardcoded butler system prompt.
    Tool definitions are passed in per-call so Layer 4+ can plug them in
    without touching this class.
    """

    def __init__(self) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. Cannot start the orchestrator."
            )

        self.client = AsyncAnthropic(api_key=api_key)
        self.model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip()
        self.max_tokens = int(os.environ.get("CLAUDE_MAX_TOKENS", "700"))
        self.temperature = float(os.environ.get("CLAUDE_TEMPERATURE", "0.6"))

        # Calculate time offset from worldtimeapi to bypass broken VPS clocks
        self.time_offset_seconds = 0
        self._sync_time_offset()

        logger.info("ClaudeClient initialized. Model: %s", self.model)

    def _sync_time_offset(self):
        """Fetches true UTC time to calculate offset and bypass drifting VPS clocks."""
        import urllib.request
        import json
        import time
        try:
            req = urllib.request.Request(
                "http://worldtimeapi.org/api/timezone/Africa/Lagos", 
                headers={'User-Agent': 'Atlas-Butler-AI'}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                true_unixtime = data.get("unixtime")
                if true_unixtime:
                    self.time_offset_seconds = true_unixtime - time.time()
                    logger.info("Time offset synced from WorldTimeAPI: %s seconds", self.time_offset_seconds)
        except Exception as e:
            logger.warning("Failed to sync true time. Falling back to system clock: %s", e)

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> dict:
        """
        Send a message to Claude and return a structured response dict.

        Args:
            messages:  The full conversation history (role/content dicts).
            tools:     Optional list of tool schemas (JSON schema format).
                       Pass None if no tools are available yet (Layer 3).

        Returns:
            {
                "content":     str   — The text response from Claude.
                "stop_reason": str   — "end_turn" | "tool_use" | "max_tokens".
                "tool_calls":  list  — List of tool_use blocks (may be empty).
                "raw":         Message — The raw Anthropic response object.
            }

        Raises:
            ClaudeError on unrecoverable API failures.
        """
        import time
        from datetime import datetime, timezone, timedelta

        # Get true UTC timestamp by applying the offset
        true_utc_timestamp = time.time() + self.time_offset_seconds
        
        # Lagos (WAT) is strictly UTC+1 all year round.
        wat_tz = timezone(timedelta(hours=1), name="WAT")
        now = datetime.fromtimestamp(true_utc_timestamp, tz=timezone.utc).astimezone(wat_tz)
        
        # Forceful time injection so Claude doesn't hallucinate its cutoff date
        time_context = (
            f"\n\n[CRITICAL SYSTEM INSTRUCTION]\n"
            f"The current real-world time is EXACTLY: {now.strftime('%A, %B %d, %Y - %I:%M %p (WAT)')}\n"
            f"You MUST use this exact time and date whenever the user asks about the current time or date. "
            f"Do not use your training cutoff date."
        )
        
        dynamic_system_prompt = SYSTEM_PROMPT + time_context

        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": dynamic_system_prompt,
            "messages": messages,
        }

        if tools:
            kwargs["tools"] = tools

        try:
            response: Message = await self.client.messages.create(**kwargs)
        except RateLimitError as exc:
            logger.error("Claude rate limit hit: %s", exc)
            raise ClaudeError(
                "I've been throttled by my own mind, sir — too many requests. "
                "Please try again in a moment."
            ) from exc
        except APITimeoutError as exc:
            logger.error("Claude API timeout: %s", exc)
            raise ClaudeError(
                "My thoughts are taking unusually long to form. "
                "The request timed out — please try again."
            ) from exc
        except APIConnectionError as exc:
            logger.error("Claude connection error: %s", exc)
            raise ClaudeError(
                "I appear to have lost connection to my own intelligence, sir. "
                "Network error — please try again shortly."
            ) from exc
        except APIStatusError as exc:
            logger.error("Claude API status error %d: %s", exc.status_code, exc.message)
            raise ClaudeError(
                f"Claude API returned an error ({exc.status_code}). "
                "This has been logged."
            ) from exc

        text_blocks = [b.text for b in response.content if b.type == "text"]
        tool_blocks = [b for b in response.content if b.type == "tool_use"]

        return {
            "content": "\n".join(text_blocks),
            "stop_reason": response.stop_reason,
            "tool_calls": tool_blocks,
            "raw": response,
        }
