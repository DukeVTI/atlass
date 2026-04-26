import os
import uuid
import json
import logging
import httpx
import redis.asyncio as aioredis

from .registry import Tool, registry

logger = logging.getLogger("atlas.tools.pc_worker")

API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

async def _dispatch_to_pc(tool_name: str, kwargs: dict) -> str:
    """Helper to send a command to the PC worker and wait for the result."""
    task_id = str(uuid.uuid4())
    payload = {
        "task_id": task_id,
        "tool": tool_name,
        "kwargs": kwargs
    }
    
    # 1. Send command via API Gateway
    worker_id = "pc:local"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{API_BASE_URL}/worker/command/{worker_id}",
                json=payload
            )
            if resp.status_code == 404:
                return "Error: The PC Worker is currently offline or not connected."
            resp.raise_for_status()
    except httpx.RequestError as e:
        logger.error(f"Failed to reach API gateway for PC task: {e}")
        return "Error: Could not dispatch task to PC Worker (API Gateway unreachable)."
        
    # 2. Wait for result on Redis
    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        # BLPOP blocks until an element is pushed, or timeout is reached (35 seconds)
        # It returns a tuple: (key, value)
        result_tuple = await r.blpop(f"atlas:task_result:{task_id}", timeout=35)
        await r.aclose()
        
        if not result_tuple:
            return "Error: Task dispatched to PC Worker, but it timed out waiting for a response."
            
        _, value = result_tuple
        data = json.loads(value)
        
        if data.get("status") == "error":
            return f"PC Worker Error: {data.get('result')}"
            
        return str(data.get("result"))
        
    except Exception as e:
        logger.error(f"Redis error while waiting for PC task: {e}")
        return f"Error: Failed to retrieve result from PC Worker ({e})"


class PCRunShellTool(Tool):
    name = "pc_run_shell"
    description = "Executes a terminal/shell command on the user's local PC."
    is_destructive = True  # Always gate shell commands!
    
    schema = {
        "name": "pc_run_shell",
        "description": "Executes a terminal or shell command on the user's local laptop and returns the stdout/stderr output. Use this for file manipulation, running scripts, or querying local git repositories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The exact shell command to run (e.g. 'ls -la', 'npm test', 'git status')."
                }
            },
            "required": ["command"]
        }
    }
    
    async def run(self, command: str, **kwargs) -> str:
        return await _dispatch_to_pc("run_shell", {"command": command})


class PCReadFileTool(Tool):
    name = "pc_read_file"
    description = "Reads the contents of a file on the user's local PC."
    is_destructive = False
    
    schema = {
        "name": "pc_read_file",
        "description": "Reads the plain text contents of a file on the user's local laptop. Use this to inspect code, configuration files, or documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "The absolute or relative path to the file on the laptop."
                }
            },
            "required": ["filepath"]
        }
    }
    
    async def run(self, filepath: str, **kwargs) -> str:
        return await _dispatch_to_pc("read_file", {"filepath": filepath})


class PCSystemStatusTool(Tool):
    name = "pc_system_status"
    description = "Retrieves the current CPU, RAM, and battery status of the user's laptop."
    is_destructive = False
    
    schema = {
        "name": "pc_system_status",
        "description": "Retrieves the current CPU usage, RAM usage, and Battery level of the user's local laptop.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    }
    
    async def run(self, **kwargs) -> str:
        return await _dispatch_to_pc("system_status", {})
