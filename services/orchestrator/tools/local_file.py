"""
Atlas Local File Tool — Layer 2 Bridge
--------------------------------------
Enables Atlas to interact with the user's PC via the PC Worker.
Uses the central API as a WebSocket bridge.
"""

import json
import logging
import os
import uuid
import httpx
from typing import Any, Dict, Optional

from .base import Tool

logger = logging.getLogger("atlas.tools.local_file")

class LocalFileTool(Tool):
    """
    Bridge tool to the PC Worker.
    Sends commands to the API, which routes them via WebSocket.
    """
    
    @property
    def name(self) -> str:
        return "local_pc_command"

    @property
    def description(self) -> str:
        return (
            "Interact with the local PC (Duke's laptop). "
            "Supported commands: 'file_read', 'file_list', 'execute_script'. "
            "Paths are relative to the Atlas Scoped Root."
        )

    @property
    def schema(self) -> dict:
        return {
            "name": "local_pc_command",
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["file_read", "file_list", "execute_script"],
                        "description": "The command to execute on the local PC."
                    },
                    "params": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path for file_read/file_list"},
                            "script": {"type": "string", "description": "Shell script for execute_script"}
                        },
                        "description": "Parameters for the command."
                    },
                    "worker_name": {
                        "type": "string",
                        "default": "duke-laptop",
                        "description": "The name of the worker to target."
                    }
                },
                "required": ["command"]
            }
        }

    async def run(self, **kwargs) -> Any:
        command = kwargs.get("command")
        params = kwargs.get("params", {})
        worker_name = kwargs.get("worker_name", "duke-laptop")
        worker_id = f"pc_worker:{worker_name}"
        
        request_id = str(uuid.uuid4())
        api_base_url = os.getenv("API_BASE_URL", "http://api:8000")
        
        payload = {
            "command": command,
            "params": params,
            "request_id": request_id
        }
        
        logger.info(f"Dispatching command to {worker_id}: {command}")
        
        try:
            async with httpx.AsyncClient() as client:
                # Dispatch command via API
                resp = await client.post(
                    f"{api_base_url}/worker/command/{worker_id}",
                    json=payload,
                    timeout=5.0
                )
                
                if resp.status_code != 200:
                    return f"Error dispatching to worker: {resp.text}"
                
                # In a real implementation, we would wait for the response from Redis.
                # Since Layer 2 is primarily for infrastructure, we'll implement 
                # a short poll here for the response.
                
                import asyncio
                import redis.asyncio as aioredis
                
                r = aioredis.from_url(os.getenv("REDIS_URL", "redis://redis:6379"))
                
                # Poll for result (max 10 seconds)
                for _ in range(20):
                    await asyncio.sleep(0.5)
                    # Use a unique response queue for this request_id or just peek at worker queue
                    # For simplicity, we peek at the worker's response queue
                    responses = await r.lrange(f"atlas:responses:{worker_id}", 0, -1)
                    for raw_resp in responses:
                        resp_data = json.loads(raw_resp)
                        if resp_data.get("request_id") == request_id:
                            # Found our response!
                            await r.lrem(f"atlas:responses:{worker_id}", 0, raw_resp)
                            await r.aclose()
                            return resp_data.get("result")
                
                await r.aclose()
                return "Command dispatched, but worker response timed out. It may still be executing."
                
        except Exception as e:
            logger.error(f"LocalFileTool execution failed: {e}")
            return f"Failed to communicate with PC Worker: {str(e)}"
