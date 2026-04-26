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

class PCListDirectoryTool(Tool):
    name = "pc_list_directory"
    description = "Lists the contents of a directory on the user's laptop."
    is_destructive = False
    
    schema = {
        "name": "pc_list_directory",
        "description": "Lists the files and folders inside a specific directory on the user's local laptop.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "The absolute or relative path to the directory."
                }
            },
            "required": ["directory"]
        }
    }
    
    async def run(self, directory: str, **kwargs) -> str:
        return await _dispatch_to_pc("list_directory", {"directory": directory})

class PCWriteFileTool(Tool):
    name = "pc_write_file"
    description = "Writes text content to a file on the user's laptop."
    is_destructive = True
    
    schema = {
        "name": "pc_write_file",
        "description": "Writes text content to a local file on the user's laptop. You MUST ask the user before overwriting an existing file unless they explicitly gave permission.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "The absolute or relative path to the file."
                },
                "content": {
                    "type": "string",
                    "description": "The text content to write into the file."
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Whether to overwrite the file if it already exists. Default is false."
                }
            },
            "required": ["filepath", "content"]
        }
    }
    
    async def run(self, filepath: str, content: str, overwrite: bool = False, **kwargs) -> str:
        return await _dispatch_to_pc("write_file", {"filepath": filepath, "content": content, "overwrite": overwrite})

class PCDeleteFileTool(Tool):
    name = "pc_delete_file"
    description = "Deletes a file on the user's laptop."
    is_destructive = True
    
    schema = {
        "name": "pc_delete_file",
        "description": "Deletes a specific file on the user's local laptop. Cannot delete directories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "The absolute or relative path to the file to delete."
                }
            },
            "required": ["filepath"]
        }
    }
    
    async def run(self, filepath: str, **kwargs) -> str:
        return await _dispatch_to_pc("delete_file", {"filepath": filepath})

class PCTakeScreenshotTool(Tool):
    name = "pc_take_screenshot"
    description = "Captures a screenshot of the user's laptop monitor."
    is_destructive = False
    
    schema = {
        "name": "pc_take_screenshot",
        "description": "Captures a live screenshot of the user's primary monitor on their local laptop. Returns the image so you can see what they are looking at.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    }
    
    async def run(self, **kwargs) -> str | list:
        # Note: _dispatch_to_pc returns a string normally.
        # But for screenshots, the PC worker returns a LIST containing the Anthropic image block!
        # _dispatch_to_pc uses json.loads(), so if the result was a list, it will return a list...
        # Wait! _dispatch_to_pc forces a string return: `return str(data.get("result"))`
        # I need to fix _dispatch_to_pc to return list or string!
        
        # Let's override the dispatch logic just for the screenshot so it doesn't force a string cast.
        task_id = str(uuid.uuid4())
        payload = {
            "task_id": task_id,
            "tool": "take_screenshot",
            "kwargs": {}
        }
        
        worker_id = "pc:local"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{API_BASE_URL}/worker/command/{worker_id}", json=payload)
                if resp.status_code == 404:
                    return "Error: The PC Worker is currently offline or not connected."
                resp.raise_for_status()
        except Exception as e:
            return f"Error: Could not dispatch task to PC Worker ({e})."
            
        try:
            r = aioredis.from_url(REDIS_URL, decode_responses=True)
            result_tuple = await r.blpop(f"atlas:task_result:{task_id}", timeout=35)
            await r.aclose()
            
            if not result_tuple:
                return "Error: Task dispatched, but timed out waiting for a screenshot."
                
            _, value = result_tuple
            data = json.loads(value)
            
            if data.get("status") == "error":
                return f"PC Worker Error: {data.get('result')}"
                
            # For screenshots, the result IS a list! Return it directly, do not cast to str.
            return data.get("result")
            
        except Exception as e:
            return f"Error: Failed to retrieve screenshot from PC Worker ({e})"
