"""
Atlas Butler Loop
------------------
The core agentic execution engine with memory integration.

Per AGENTS.md and PRD:
- Maximum 3 iterations per user message
- Circuit breaker: abort if the exact same set of tool calls is repeated
  3 times consecutively (prevents infinite loops)
- Tools execute sequentially (parallelism is a Layer 4+ optimisation)
- On hitting the iteration limit, Claude is called once more with all
  gathered context to synthesise a best-effort final response
- Memory integration (v0.2+): retrieve relevant episodic/factual context
  before LLM calls; store tool results and conversation turns after execution

Design:
  The tool_registry is a simple dict mapping tool names to async callables.
  At Layer 3 it is empty — tools are plugged in Layer 4.
  The butler_loop handles the empty-tools case cleanly (no tool calls happen).
  
Memory Integration:
  - Before iteration 1: call memory service GET /memory/context
  - Prepend retrieved context to messages (after system prompt)
  - After each tool execution: store result as episodic memory
  - After full loop: store conversation turn with both user + assistant messages
"""

import logging
from typing import Any, Callable, Awaitable, Optional
import httpx

from claude_client import ClaudeClient, ClaudeError

logger = logging.getLogger("atlas.orchestrator.butler_loop")

MAX_ITERATIONS = 3

# Type alias for tool functions
ToolFn = Callable[..., Awaitable[Any]]


class ButlerLoop:
    """
    Manages the LLM → tool → LLM cycle for a single user request.
    Integrates with memory service for context injection and conversation logging.

    Usage:
        loop = ButlerLoop(claude_client=claude, tool_registry={})
        response = await loop.run(messages=history, user_id=123, session_id="chat_123")
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
        # Memory service URL for context retrieval
        self.memory_service_url = memory_service_url
        self.http_client: Optional[httpx.AsyncClient] = None

    def register_tool(
        self, name: str, fn: ToolFn, schema: dict
    ) -> None:
        """Register a tool at runtime. Called by Layer 4+ setup."""
        self.tool_registry[name] = fn
        self.tool_schemas.append(schema)
        logger.info("Tool registered: %s", name)

    def set_schemas(self, schemas: list[dict]):
        self.tool_schemas = schemas

    async def _get_memory_context(
        self, user_id: int, session_id: str, query: str
    ) -> str:
        """
        Retrieve relevant memory context from memory service.
        
        Args:
            user_id: User ID for memory scoping
            session_id: Conversation session ID
            query: Query to search for relevant memories
            
        Returns:
            Formatted memory context string to inject into messages
        """
        if not self.http_client:
            return ""
        
        try:
            response = await self.http_client.get(
                f"{self.memory_service_url}/memory/context",
                params={
                    "user_id": user_id,
                    "session_id": session_id,
                    "query": query
                },
                timeout=5.0
            )
            
            if response.status_code != 200:
                logger.warning(f"Memory service returned {response.status_code}")
                return ""
            
            data = response.json()
            context = data.get("context", "")
            
            if context:
                logger.debug(f"Retrieved memory context: {len(context)} chars")
            
            return context
        
        except Exception as e:
            logger.warning(f"Failed to retrieve memory context: {e}")
            return ""
    
    async def _store_memory_event(
        self, user_id: int, tool_name: str, inputs: dict, output: str
    ) -> None:
        """
        Store tool execution result as episodic memory.
        
        Args:
            user_id: User ID
            tool_name: Name of tool executed
            inputs: Tool inputs
            output: Tool output/result
        """
        if not self.http_client:
            return
        
        try:
            await self.http_client.post(
                f"{self.memory_service_url}/memory/episodic",
                json={
                    "user_id": user_id,
                    "event_type": "tool_execution",
                    "summary": f"Executed {tool_name} tool",
                    "full_context": f"Tool: {tool_name}\nInputs: {inputs}\nOutput: {output[:500]}",
                    "source": "tool_call",
                    "tags": [tool_name, "tool_execution"],
                },
                timeout=5.0
            )
            logger.debug(f"Stored memory event for {tool_name}")
        
        except Exception as e:
            logger.warning(f"Failed to store memory event: {e}")
    
    async def _store_conversation_turn(
        self,
        user_id: int,
        session_id: str,
        turn_number: int,
        user_message: str,
        assistant_message: str,
        tools_used: list[str]
    ) -> None:
        """
        Store conversation turn in memory service.
        
        Args:
            user_id: User ID
            session_id: Session ID
            turn_number: Turn sequence number
            user_message: User's input
            assistant_message: Assistant's response
            tools_used: List of tools used in this turn
        """
        if not self.http_client:
            return
        
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
                timeout=5.0
            )
            logger.debug(f"Stored conversation turn {turn_number}")
        
        except Exception as e:
            logger.warning(f"Failed to store conversation turn: {e}")

    async def run(
        self,
        messages: list[dict],
        user_id: int,
        session_id: str = "default",
    ) -> str:
        """
        Execute the full butler loop for one user message.

        Args:
            messages: Full conversation history (working memory).
            user_id:  Telegram user ID — used for logging and memory scoping.
            session_id: Conversation session ID (e.g., Telegram chat ID).

        Returns:
            The final text response from Atlas.

        Raises:
            ClaudeError: If Claude itself fails unrecoverably.
        """
        # Initialize HTTP client for memory service if needed
        if not self.http_client:
            self.http_client = httpx.AsyncClient(timeout=30.0)
        
        # Extract first user message for memory context search
        first_user_message = ""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                first_user_message = msg.get("content", "")
                if isinstance(first_user_message, list):
                    first_user_message = str(first_user_message)
                break
        
        # Retrieve relevant memory context before starting loop
        memory_context = await self._get_memory_context(
            user_id, session_id, first_user_message
        )
        
        current_messages = list(messages)
        
        # Prepend memory context if available (after system prompt, before history)
        if memory_context and current_messages:
            # Inject memory context as a system-level insight
            context_msg = {
                "role": "user",
                "content": f"{memory_context}\n[Please use the above context to inform your response.]"
            }
            # Insert after system message (index 0) but before conversation history
            current_messages.insert(1, context_msg)
        
        iteration = 0
        turn_number = 0
        last_tool_signature: list[str] = []  # For circuit breaker
        identical_count = 0  # How many times the same tools were called in a row
        tools_used_in_turn: list[str] = []  # Track tools for this turn

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
                
                # Store tool execution as episodic memory
                tools_used_in_turn.append(tool_call.name)
                await self._store_memory_event(
                    user_id,
                    tool_call.name,
                    tool_call.input,
                    str(result)
                )
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
            final_response = final["content"]
            
            # Store synthesis turn in memory
            await self._store_conversation_turn(
                user_id,
                session_id,
                turn_number,
                first_user_message,
                final_response,
                tools_used_in_turn
            )
            
            return final_response
        except ClaudeError:
            error_response = (
                "I've exhausted my tool call allowance without completing your request, sir. "
                "The task may be more complex than I can handle in one pass. "
                "Please break it into smaller steps."
            )
            
            # Store error turn in memory
            await self._store_conversation_turn(
                user_id,
                session_id,
                turn_number,
                first_user_message,
                error_response,
                tools_used_in_turn
            )
            
            return error_response
        finally:
            # Cleanup HTTP client
            if self.http_client:
                await self.http_client.aclose(        "I've halted the process. Please rephrase your request or "
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
        from tools.registry import registry
        
        name = tool_call.name
        inputs = tool_call.input

        logger.info("Executing tool: %s — inputs: %s", name, inputs)
        return await registry.execute(name, inputs)
