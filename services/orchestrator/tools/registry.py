import logging
import json
import httpx
import os
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

    async def execute(self, tool_name: str, inputs: dict, user_id: str = "duke") -> str:
        """
        Executes a registered tool. Plugs into the Security Gate.
        """
        if tool_name not in self._tools:
            return f"Error: Tool '{tool_name}' not found in registry."

        tool = self._tools[tool_name]

        # Executor-Level Confirmation Gate
        if tool.is_destructive:
            confirm_id = await ConfirmationManager.intercept(tool_name, inputs)
            # Log as 'paused' (Security Gate)
            await self._audit_log(tool_name, inputs, "paused", f"Action paused. Confirmation ID: {confirm_id}", user_id)
            return confirm_id

        # Log "executing" BEFORE call
        await self._audit_log(tool_name, inputs, "executing", "Calling tool.run()...", user_id)

        try:
            result = await tool.run(**inputs)
            serialized_result = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
            
            # Log "success" AFTER call
            await self._audit_log(tool_name, inputs, "success", serialized_result, user_id)
            
            return serialized_result
        except Exception as exc:
            logger.error(f"Execution error in tool {tool_name}: {exc}", exc_info=True)
            error_msg = f"Error executing {tool_name}: {str(exc)}"
            
            # Log failure to Audit Log
            await self._audit_log(tool_name, inputs, "error", error_msg, user_id)
            
            return error_msg

    async def _audit_log(self, tool_name: str, inputs: dict, status: str, result: str, user_id: str):
        """
        Sends tool execution metadata to the central API audit endpoint.
        """
        api_url = f"{os.getenv('API_BASE_URL', 'http://api:8000')}/audit"
        
        payload = {
            "user_id": user_id,
            "tool_name": tool_name,
            "inputs": inputs,
            "status": status,
            "result": result[:1000] # Truncate for DB sanity
        }
        
        try:
            async with httpx.AsyncClient() as client:
                await client.post(api_url, json=payload, timeout=2.0)
        except Exception as e:
            logger.warning(f"Failed to write to central audit log: {e}")

# Global Tool Registry instance for the orchestrator
registry = ToolRegistry()

# ----------------------------------------------------
# Define Native Approver Tool (To break out of the security gate pause)
# ----------------------------------------------------
class ApproveActionTool(Tool):
    name = "approve_action"
    description = "Approves and executes a paused destructive action using its Confirmation ID. Call this immediately when the user provides an approval or confirmation command."
    is_destructive = False

    schema = {
        "name": "approve_action",
        "description": "Approves and executes a paused destructive action using its Confirmation ID. Call this immediately when the user says 'Approve' or 'Go ahead' followed by an ID.",
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
        clean_id = str(confirmation_id).strip().upper()
        pending = await ConfirmationManager.get_pending_action(clean_id)
        if not pending:
            return f"Error: Confirmation ID {clean_id} is invalid or has expired."

        target_tool_name = pending["tool_name"]
        target_inputs = pending["inputs"]
        user_id = kwargs.get("user_id", "duke")

        # Run without re-triggering the gate!
        if target_tool_name not in registry._tools:
            return f"Error: Pending tool {target_tool_name} not found."

        try:
            target_tool = registry._tools[target_tool_name]
            logger.info(f"Executing approved action: {target_tool_name}")
            
            # Log "executing" for the TARGET tool
            await registry._audit_log(target_tool_name, target_inputs, "executing_approved", "Executing approved action...", user_id)
            
            result = await target_tool.run(**target_inputs)
            
            # Clear cache upon success
            await ConfirmationManager.clear_action(confirmation_id)
            
            serialized_result = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
            
            # Log "success" for the TARGET tool
            await registry._audit_log(target_tool_name, target_inputs, "success_approved", serialized_result, user_id)
            
            return serialized_result
            
        except Exception as exc:
            logger.error(f"Execution error in approved tool {target_tool_name}: {exc}")
            error_msg = f"Error executing approved action {target_tool_name}: {str(exc)}"
            
            # Log failure for the TARGET tool
            await registry._audit_log(target_tool_name, target_inputs, "error_approved", error_msg, user_id)
            
            return error_msg

# Register the native Security Gate Tools immediately
registry.register(ApproveActionTool())

class RejectActionTool(Tool):
    name = "reject_action"
    description = "Rejects and cancels a paused destructive action using its Confirmation ID. Call this when the user explicitly denies or rejects a pending action."
    is_destructive = False

    schema = {
        "name": "reject_action",
        "description": "Rejects and permanently cancels a paused destructive action. Call this when the user says 'No' or 'Cancel' followed by an ID.",
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
        clean_id = str(confirmation_id).strip().upper()
        pending = await ConfirmationManager.get_pending_action(clean_id)
        if not pending:
            return f"Error: Confirmation ID {clean_id} is invalid or has already expired."

        target_tool_name = pending["tool_name"]
        await ConfirmationManager.clear_action(confirmation_id)
        logger.info(f"Rejected pending action: {target_tool_name}")
        return f"Successfully canceled the pending action '{target_tool_name}'."

registry.register(RejectActionTool())

# ─── Register PC Worker Tools ───
from .local_file import LocalFileTool
registry.register(LocalFileTool())
