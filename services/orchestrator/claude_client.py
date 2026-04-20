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
        self.model = os.environ.get("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
        self.max_tokens = int(os.environ.get("CLAUDE_MAX_TOKENS", "700"))
        self.temperature = float(os.environ.get("CLAUDE_TEMPERATURE", "0.6"))

        logger.info("ClaudeClient initialized. Model: %s", self.model)

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
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
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
