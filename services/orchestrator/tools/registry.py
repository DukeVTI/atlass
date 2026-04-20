import logging
import json
from typing import Any
from tools.base import Tool
from tools.security import ConfirmationManager

logger = logging.getLogger("atlas.tools.registry")

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name}")

    def get_schemas(self) -> list[dict]:
        return [tool.schema for tool in self._tools.values()]

    async def execute(self, tool_name: str, inputs: dict) -> str:
        """
        Executes a registered tool. Plugs into the Security Gate.
        """
        if tool_name not in self._tools:
            return f"Error: Tool '{tool_name}' not found in registry."

        tool = self._tools[tool_name]

        # Executor-Level Confirmation Gate
        if tool.is_destructive:
            return await ConfirmationManager.intercept(tool_name, inputs)

        try:
            result = await tool.run(**inputs)
            if isinstance(result, (dict, list)):
                return json.dumps(result)
            return str(result)
        except Exception as exc:
            logger.error(f"Execution error in tool {tool_name}: {exc}", exc_info=True)
            return f"Error executing {tool_name}: {str(exc)}"

# Global Tool Registry instance for the orchestrator
registry = ToolRegistry()

# ----------------------------------------------------
# Define Native Approver Tool (To break out of the security gate pause)
# ----------------------------------------------------
class ApproveActionTool(Tool):
    name = "approve_action"
    description = "Approves and executes a paused destructive action using its Confirmation ID. You must only call this AFTER asking Duke."
    is_destructive = False

    schema = {
        "name": "approve_action",
        "description": "Approves and executes a paused destructive action using its Confirmation ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmation_id": {
                    "type": "string",
                    "description": "The exact ID returned by the ACTION PAUSED message."
                }
            },
            "required": ["confirmation_id"]
        }
    }

    async def run(self, confirmation_id: str, **kwargs) -> str:
        pending = await ConfirmationManager.get_pending_action(confirmation_id)
        if not pending:
            return f"Error: Confirmation ID {confirmation_id} is invalid or has expired."

        target_tool_name = pending["tool_name"]
        target_inputs = pending["inputs"]

        # Run without re-triggering the gate!
        if target_tool_name not in registry._tools:
            return f"Error: Pending tool {target_tool_name} not found."

        try:
            target_tool = registry._tools[target_tool_name]
            logger.info(f"Executing approved action: {target_tool_name}")
            result = await target_tool.run(**target_inputs)
            
            # Clear cache upon success
            await ConfirmationManager.clear_action(confirmation_id)
            
            if isinstance(result, (dict, list)):
                return json.dumps(result)
            return str(result)
            
        except Exception as exc:
            logger.error(f"Execution error in approved tool {target_tool_name}: {exc}")
            return f"Error executing approved action {target_tool_name}: {str(exc)}"

# Register the native Security Gate Tools immediately
registry.register(ApproveActionTool())

class RejectActionTool(Tool):
    name = "reject_action"
    description = "Rejects and cancels a paused destructive action using its Confirmation ID. You must only call this AFTER asking Duke and Duke says NO."
    is_destructive = False

    schema = {
        "name": "reject_action",
        "description": "Rejects and permanently cancels a paused destructive action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmation_id": {
                    "type": "string",
                    "description": "The exact ID returned by the ACTION PAUSED message."
                }
            },
            "required": ["confirmation_id"]
        }
    }

    async def run(self, confirmation_id: str, **kwargs) -> str:
        pending = await ConfirmationManager.get_pending_action(confirmation_id)
        if not pending:
            return f"Error: Confirmation ID {confirmation_id} is invalid or has already expired."

        target_tool_name = pending["tool_name"]
        await ConfirmationManager.clear_action(confirmation_id)
        logger.info(f"Rejected pending action: {target_tool_name}")
        return f"Successfully canceled the pending action '{target_tool_name}'."

registry.register(RejectActionTool())
