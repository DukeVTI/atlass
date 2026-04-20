"""
Atlas Butler Loop
------------------
The core agentic execution engine.

Per AGENTS.md and PRD:
- Maximum 3 iterations per user message
- Circuit breaker: abort if the exact same set of tool calls is repeated
  3 times consecutively (prevents infinite loops)
- Tools execute sequentially (parallelism is a Layer 4+ optimisation)
- On hitting the iteration limit, Claude is called once more with all
  gathered context to synthesise a best-effort final response

Design:
  The tool_registry is a simple dict mapping tool names to async callables.
  At Layer 3 it is empty — tools are plugged in Layer 4.
  The butler_loop handles the empty-tools case cleanly (no tool calls happen).
"""

import logging
from typing import Any, Callable, Awaitable

from claude_client import ClaudeClient, ClaudeError

logger = logging.getLogger("atlas.orchestrator.butler_loop")

MAX_ITERATIONS = 3

# Type alias for tool functions
ToolFn = Callable[..., Awaitable[Any]]


class ButlerLoop:
    """
    Manages the LLM → tool → LLM cycle for a single user request.

    Usage:
        loop = ButlerLoop(claude_client=claude, tool_registry={})
        response = await loop.run(messages=history, user_id=123)
    """

    def __init__(
        self,
        claude: ClaudeClient,
        tool_registry: dict[str, ToolFn] | None = None,
        tool_schemas: list[dict] | None = None,
    ) -> None:
        self.claude = claude
        # Maps tool name → async callable
        self.tool_registry: dict[str, ToolFn] = tool_registry or {}
        # Claude-compatible JSON schema defs for all registered tools
        self.tool_schemas: list[dict] = tool_schemas or []

    def register_tool(
        self, name: str, fn: ToolFn, schema: dict
    ) -> None:
        """Register a tool at runtime. Called by Layer 4+ setup."""
        self.tool_registry[name] = fn
        self.tool_schemas.append(schema)
        logger.info("Tool registered: %s", name)

    async def run(
        self,
        messages: list[dict],
        user_id: int,
    ) -> str:
        """
        Execute the full butler loop for one user message.

        Args:
            messages: Full conversation history (working memory).
            user_id:  Telegram user ID — used for logging only.

        Returns:
            The final text response from Atlas.

        Raises:
            ClaudeError: If Claude itself fails unrecoverably.
        """
        current_messages = list(messages)
        iteration = 0
        last_tool_signature: list[str] = []  # For circuit breaker
        identical_count = 0  # How many times the same tools were called in a row

        tools = self.tool_schemas if self.tool_schemas else None

        while iteration < MAX_ITERATIONS:
            iteration += 1
            logger.info(
                "Butler loop — iteration %d/%d for user %d",
                iteration,
                MAX_ITERATIONS,
                user_id,
            )

            response = await self.claude.chat(
                messages=current_messages,
                tools=tools,
            )

            # ── Case 1: clean text response — done ────────────────────────────
            if response["stop_reason"] == "end_turn" or not response["tool_calls"]:
                logger.info(
                    "Butler loop completed in %d iteration(s) for user %d.",
                    iteration,
                    user_id,
                )
                return response["content"]

            # ── Case 2: Claude wants to call tools ────────────────────────────
            tool_calls = response["tool_calls"]
            tool_signature = sorted(t.name for t in tool_calls)

            # Circuit breaker — same tools called repeatedly?
            if tool_signature == last_tool_signature:
                identical_count += 1
            else:
                identical_count = 0
                last_tool_signature = tool_signature

            if identical_count >= 2:  # 3rd repetition = abort
                logger.warning(
                    "Circuit breaker triggered for user %d after %d iterations. "
                    "Repeated tools: %s",
                    user_id,
                    iteration,
                    tool_signature,
                )
                return (
                    "I appear to have caught myself in a repetitive loop, sir — "
                    "the same operations were requested three times in succession, "
                    "which suggests something unexpected is happening. "
                    "I've halted the process. Please rephrase your request or "
                    "try a more specific command."
                )

            logger.info(
                "Claude requested tools: %s (iteration %d)", tool_signature, iteration
            )

            # Append assistant's full response (including tool_use blocks) to history
            current_messages.append(
                {"role": "assistant", "content": response["raw"].content}
            )

            # Execute each requested tool and collect results
            tool_results = []
            for tool_call in tool_calls:
                result = await self._execute_tool(tool_call)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "content": str(result),
                    }
                )

            # Feed tool results back to Claude
            current_messages.append({"role": "user", "content": tool_results})

        # ── Iteration limit reached — get a best-effort final response ────────
        logger.warning(
            "Butler loop hit max iterations (%d) for user %d. "
            "Requesting synthesis from Claude.",
            MAX_ITERATIONS,
            user_id,
        )

        try:
            synthesis_messages = current_messages + [
                {
                    "role": "user",
                    "content": (
                        "[SYSTEM: You have reached the maximum number of tool calls. "
                        "Provide the best possible answer using the information "
                        "you have gathered so far. Be brief and honest about any gaps.]"
                    ),
                }
            ]
            final = await self.claude.chat(messages=synthesis_messages)
            return final["content"]
        except ClaudeError:
            return (
                "I've exhausted my tool call allowance without completing your request, sir. "
                "The task may be more complex than I can handle in one pass. "
                "Please break it into smaller steps."
            )

    async def _execute_tool(self, tool_call: Any) -> Any:
        """
        Look up and execute a tool from the registry.
        Returns the result or an error string — never raises.
        """
        name = tool_call.name
        inputs = tool_call.input

        logger.info("Executing tool: %s — inputs: %s", name, inputs)

        if name not in self.tool_registry:
            logger.warning("Unknown tool requested: %s", name)
            return (
                f"Tool '{name}' is not yet available. "
                "This capability will be added in a future layer."
            )

        try:
            fn = self.tool_registry[name]
            return await fn(**inputs)
        except TypeError as exc:
            logger.error("Tool %s called with wrong arguments: %s", name, exc)
            return f"Error: Wrong arguments passed to tool '{name}': {exc}"
        except Exception as exc:
            logger.error("Tool %s raised an exception: %s", name, exc, exc_info=True)
            return f"Error executing '{name}': {exc}"
