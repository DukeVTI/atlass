"""
Atlas Butler Loop
------------------
The core agentic execution engine.

Per AGENTS.md and PRD:
- Maximum 7 iterations per user message
- Circuit breaker: abort if the exact same set of tool calls is repeated
  3 times consecutively (prevents infinite loops)
- Tools execute sequentially (parallelism is a Layer 4+ optimisation)
- On hitting the iteration limit, Claude is called once more with all
  gathered context to synthesise a best-effort final response

Design:
  The tool_registry is a simple dict mapping tool names to async callables.
  At Layer 3 it is empty ΓÇö tools are plugged in Layer 4.
  The butler_loop handles the empty-tools case cleanly (no tool calls happen).
"""

import logging
import json
from typing import Any, Callable, Awaitable
import httpx

from claude_client import ClaudeClient, ClaudeError

logger = logging.getLogger("atlas.orchestrator.butler_loop")

# Token budget — Claude Haiku 4.5 has 200k context.
# We cap input at 150k to leave headroom for system prompt, tools, and output.
INPUT_TOKEN_BUDGET = 150_000


def _trim_messages_to_budget(
    messages: list[dict],
    current_token_count: int,
    budget: int = INPUT_TOKEN_BUDGET,
) -> list[dict]:
    """
    Trim the oldest turns from the history until estimated token usage is under budget.
    Trims by complete turns (User + Assistant [+ Tool Sequence]) to avoid leaving
    orphaned tool results at the top of the history.

    Uses a crude per-message token estimate (chars / 4) to avoid extra API calls.
    The real count_tokens call happens before each butler iteration — this just
    gets us roughly under budget in one pass.
    """
    if current_token_count <= budget or len(messages) <= 2:
        return messages

    def is_fresh_user_message(msg: dict) -> bool:
        """True if this is a plain user turn (not a tool_result return)."""
        if msg.get("role") != "user":
            return False
        content = msg.get("content")
        if not isinstance(content, list):
            return True
        return not any(
            isinstance(c, dict) and c.get("type") == "tool_result" for c in content
        )

    # Indices where a clean cut can be made (start of a fresh user turn)
    safe_indices = [i for i, m in enumerate(messages) if is_fresh_user_message(m)]

    if not safe_indices:
        # Extreme fallback: keep only the last message
        return messages[-1:]

    # Only consider cut points that leave at least the final user turn intact
    valid_cuts = [idx for idx in safe_indices if idx < len(messages) - 1]

    if not valid_cuts:
        return messages

    trimmed = messages
    estimated_tokens = current_token_count

    for cut_idx in valid_cuts:
        if estimated_tokens <= budget:
            break
        shed = trimmed[:cut_idx]
        shed_chars = sum(
            len(str(m.get("content", ""))) for m in shed
        )
        estimated_tokens -= shed_chars // 4
        trimmed = messages[cut_idx:]
        logger.info(
            "Shed %d message(s) from history. Estimated tokens now ~%d (budget: %d).",
            cut_idx, estimated_tokens, budget,
        )

    logger.info(
        "Trimmed message history: %d → %d messages to stay under %dk token budget.",
        len(messages), len(trimmed), budget // 1000,
    )
    return trimmed


MAX_ITERATIONS = 7

# Type alias for tool functions
ToolFn = Callable[..., Awaitable[Any]]


class ButlerLoop:
    """
    Manages the LLM ΓåÆ tool ΓåÆ LLM cycle for a single user request.

    Usage:
        loop = ButlerLoop(claude_client=claude, tool_registry={})
        response = await loop.run(messages=history, user_id=123)
    """

    def __init__(
        self,
        claude: ClaudeClient,
        tool_registry: dict[str, ToolFn] | None = None,
        tool_schemas: list[dict] | None = None,
        memory_service_url: str = "http://memory:8002",
    ) -> None:
        self.claude = claude
        # Maps tool name → async callable
        self.tool_registry: dict[str, ToolFn] = tool_registry or {}
        # Claude-compatible JSON schema defs for all registered tools
        self.tool_schemas: list[dict] = tool_schemas or []
        self.memory_service_url = memory_service_url
        self.http_client = httpx.AsyncClient()

    def register_tool(
        self, name: str, fn: ToolFn, schema: dict
    ) -> None:
        """Register a tool at runtime. Called by Layer 4+ setup."""
        self.tool_registry[name] = fn
        self.tool_schemas.append(schema)
        logger.info("Tool registered: %s", name)

    def set_schemas(self, schemas: list[dict]):
        self.tool_schemas = schemas

    async def run_stream(
        self,
        messages: list[dict],
        user_id: int,
        session_id: str | None = None,
        prior_summary: str | None = None,
    ):
        """
        Execute the full butler loop for one user message.

        Args:
            messages: Full conversation history (working memory).
            user_id:  Telegram user ID — used for logging and memory.
            session_id: Optional session ID for memory tracking.

        Returns:
            The final text response from Atlas.

        Raises:
            ClaudeError: If Claude itself fails unrecoverably.
        """
        if session_id is None:
            session_id = f"user_{user_id}"
        
        current_messages = list(messages)
        first_user_message = None
        if current_messages and current_messages[-1].get("role") == "user":
            first_user_message = current_messages[-1].get("content", "")
        
        # Retrieve memory context before starting iterations
        memory_context = await self._get_memory_context(
            user_id, session_id, first_user_message or ""
        )

        # Build memory/summary addendum for the system prompt (not a fake user turn)
        self._ephemeral_system_addendum = ""
        if prior_summary or memory_context:
            parts = []
            if prior_summary:
                parts.append(
                    f"PRIOR CONVERSATION CONTEXT (summarised):\n{prior_summary}\n"
                    f"The messages below are the most recent continuation of this conversation."
                )
            if memory_context:
                parts.append(memory_context)
            self._ephemeral_system_addendum = "\n\n---\n" + "\n\n".join(parts)
        
        iteration = 0
        turn_number = 0
        last_tool_signature: list[str] = []  # For circuit breaker
        identical_count = 0  # How many times the same tools were called in a row
        tools_used_in_turn: list[str] = []  # Track tools for this turn
        pending_confirmations: list[str] = []  # Track paused actions for UI rendering

        tools = self.tool_schemas if self.tool_schemas else None

        while iteration < MAX_ITERATIONS:
            iteration += 1
            logger.info(
                "Butler loop ΓÇö iteration %d/%d for user %d",
                iteration,
                MAX_ITERATIONS,
                user_id,
            )

            # Count tokens and trim history if approaching context limit
            token_count = await self.claude.count_tokens(
                messages=current_messages,
                tools=tools,
            )
            if token_count > INPUT_TOKEN_BUDGET:
                logger.warning(
                    "Token budget exceeded for user %d: %d tokens (budget: %d). Trimming history.",
                    user_id, token_count, INPUT_TOKEN_BUDGET,
                )
                current_messages = _trim_messages_to_budget(
                    current_messages, token_count
                )

            response = await self.claude.chat(
                messages=current_messages,
                tools=tools,
                system_addendum=getattr(self, "_ephemeral_system_addendum", ""),
            )

            # ΓöÇΓöÇ Case 1: clean text response ΓÇö done ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
            if response["stop_reason"] == "end_turn" or not response["tool_calls"]:
                logger.info(
                    "Butler loop completed in %d iteration(s) for user %d.",
                    iteration,
                    user_id,
                )
                
                final_content = response["content"]
                for cid in pending_confirmations:
                    if f"[CONFIRM:{cid}]" not in final_content:
                        final_content += f"\n\n[CONFIRM:{cid}]"

                # Store conversation turn in memory
                await self._store_conversation_turn(
                    user_id,
                    session_id,
                    turn_number,
                    first_user_message or "",
                    final_content,
                    tools_used_in_turn
                )
                
                yield f"data: {json.dumps({'type': 'message', 'content': final_content})}\n\n"
                return

            # ΓöÇΓöÇ Case 2: Claude wants to call tools ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
            tool_calls = response["tool_calls"]
            tool_signature = sorted(t.name for t in tool_calls)

            # Circuit breaker ΓÇö same tools called repeatedly?
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
                circuit_response = (
                    "I appear to have caught myself in a repetitive loop, sir ΓÇö "
                    "the same operations were requested three times in succession, "
                    "which suggests something unexpected is happening. "
                    "I've halted the process. Please rephrase your request or "
                    "try a more specific command."
                )
                
                # Store circuit breaker turn in memory
                await self._store_conversation_turn(
                    user_id,
                    session_id,
                    turn_number,
                    first_user_message or "",
                    circuit_response,
                    tools_used_in_turn
                )
                
                yield f"data: {json.dumps({'type': 'message', 'content': circuit_response})}\n\n"
                return

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
                yield f"data: {json.dumps({'type': 'status', 'content': f'Executing tool: {tool_call.name}...'})}\n\n"
                result = await self._execute_tool(tool_call, user_id=str(user_id))
                tools_used_in_turn.append(tool_call.name)
                
                result_str = str(result)
                if "[CONFIRM:" in result_str:
                    try:
                        conf_id = result_str.split("[CONFIRM:")[1].split("]")[0]
                        pending_confirmations.append(conf_id)
                    except Exception:
                        pass
                
                # Check if action was paused for confirmation (old logic fallback)
                if isinstance(result, str) and result.startswith("confirm_"):
                    result_msg = (
                        f"ACTION PAUSED. Confirmation ID: {result}. "
                        "I have paused this destructive action and am awaiting your approval, sir. "
                        "Please tell the user that the action is paused and provide them with the "
                        "Confirmation ID if they wish to approve it using the 'approve_action' tool."
                    )
                    result = result_msg

                # Store tool execution as episodic memory
                await self._store_memory_event(
                    user_id,
                    tool_call.name,
                    tool_call.input,
                    result_str
                )
                
                final_content = result if isinstance(result, list) else result_str
                
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "content": final_content,
                    }
                )

            # Feed tool results back to Claude
            current_messages.append({"role": "user", "content": tool_results})

        # ΓöÇΓöÇ Iteration limit reached ΓÇö get a best-effort final response ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
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
                        "You MUST explicitly inform the user that you hit your internal iteration limit "
                        "and then provide the best possible answer using the information "
                        "you have gathered so far. Be honest about any gaps in the task.]"
                    ),
                }
            ]
            # Guard synthesis call too
            synth_token_count = await self.claude.count_tokens(messages=synthesis_messages)
            if synth_token_count > INPUT_TOKEN_BUDGET:
                synthesis_messages = _trim_messages_to_budget(
                    synthesis_messages, synth_token_count
                )
            final = await self.claude.chat(
                messages=synthesis_messages,
                system_addendum=getattr(self, "_ephemeral_system_addendum", ""),
            )
            final_response = final["content"]
            
            for cid in pending_confirmations:
                if f"[CONFIRM:{cid}]" not in final_response:
                    final_response += f"\n\n[CONFIRM:{cid}]"
            
            # Store synthesis turn in memory
            await self._store_conversation_turn(
                user_id,
                session_id,
                turn_number,
                first_user_message or "",
                final_response,
                tools_used_in_turn
            )
            
            yield f"data: {json.dumps({'type': 'message', 'content': final_response})}\n\n"
        except ClaudeError:
            error_response = (
                "I've exhausted my tool call allowance without completing your request, sir. "
                "Anthropic's API may be having difficulty understanding the context. Please rephrase."
            )
            
            # Store error turn in memory
            await self._store_conversation_turn(
                user_id,
                session_id,
                turn_number,
                first_user_message or "",
                error_response,
                tools_used_in_turn
            )
            
            yield f"data: {json.dumps({'type': 'message', 'content': error_response})}\n\n"
            return

    async def _execute_tool(self, tool_call: Any, user_id: str = "duke") -> Any:
        """
        Look up and execute a tool from the registry.
        Returns the result or an error string — never raises.
        """
        from tools.registry import registry
        
        name = tool_call.name
        inputs = tool_call.input

        logger.info("Executing tool: %s — inputs: %s — user: %s", name, inputs, user_id)
        return await registry.execute(name, inputs, user_id=user_id)

    async def _get_memory_context(
        self, user_id: int, session_id: str, query: str
    ) -> str:
        """
        Retrieve episodic and procedural memory context from the memory service.
        Returns formatted context string or empty string if service unavailable.
        """
        try:
            response = await self.http_client.get(
                f"{self.memory_service_url}/memory/context",
                params={
                    "user_id": user_id,
                    "session_id": session_id,
                    "query": query,
                },
                timeout=5.0,
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("context", "")
            return ""
        except Exception as e:
            logger.warning("Failed to retrieve memory context: %s", e)
            return ""

    async def _store_memory_event(
        self, user_id: int, tool_name: str, inputs: dict, output: str
    ) -> None:
        """
        Store a tool execution event in episodic memory.
        Silently fails if memory service unavailable.
        """
        try:
            await self.http_client.post(
                f"{self.memory_service_url}/memory/episodic",
                json={
                    "user_id": user_id,
                    "event_type": "tool_execution",
                    "summary": f"Executed tool: {tool_name}",
                    "full_context": f"Input: {inputs}\n\nOutput: {output}",
                    "source": "tool_call",
                },
                timeout=5.0,
            )
        except Exception as e:
            logger.warning("Failed to store memory event for tool %s: %s", tool_name, e)

    async def _store_conversation_turn(
        self,
        user_id: int,
        session_id: str,
        turn_number: int,
        user_message: str,
        assistant_message: str,
        tools_used: list[str],
    ) -> None:
        """
        Store a conversation turn in memory for later retrieval.
        Silently fails if memory service unavailable.
        """
        try:
            await self.http_client.post(
                f"{self.memory_service_url}/memory/conversation",
                json={
                    "user_id": user_id,
                    "session_id": session_id,
                    "turn_number": turn_number,
                    "user_message": user_message,
                    "assistant_response": assistant_message,
                    "tool_calls": tools_used,
                },
                timeout=5.0,
            )
        except Exception as e:
            logger.warning("Failed to store conversation turn: %s", e)
