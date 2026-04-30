"""
Atlas Local File Tool — Layer 2 Bridge
--------------------------------------
Enables Atlas to interact with the user's PC via the PC Worker.
Uses the central API as a WebSocket bridge.

Protocol: sends {"tool": <name>, "kwargs": <dict>, "task_id": <uuid>}
Matches worker.py's expected format exactly.
"""

import asyncio
import json
import logging
import os
import uuid

import httpx
import redis.asyncio as aioredis

from .base import Tool

logger = logging.getLogger("atlas.tools.local_file")

API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
DEFAULT_WORKER = "duke-laptop"
WORKER_TIMEOUT_SECONDS = 30


class LocalFileTool(Tool):

    @property
    def name(self) -> str:
        return "local_pc_command"

    @property
    def description(self) -> str:
        return (
            "Interact with Duke's local Windows laptop via the PC Worker daemon. "
            "Use this for: reading/writing/deleting local files, running shell commands, "
            "checking system stats (CPU/RAM/battery), clipboard read/write, "
            "listing directories, and taking screenshots. "
            "All file paths are absolute Windows paths (e.g. C:/Users/Duke/Documents/file.txt) "
            "or relative to the Atlas Scoped Root."
        )

    @property
    def schema(self) -> dict:
        return {
            "name": "local_pc_command",
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "enum": [
                            "run_shell",
                            "read_file",
                            "write_file",
                            "delete_file",
                            "list_directory",
                            "system_status",
                            "clipboard_read",
                            "clipboard_write",
                            "take_screenshot",
                        ],
                        "description": (
                            "The tool to run on the local PC. "
                            "run_shell: execute any shell command. "
                            "read_file: read a file's contents. "
                            "write_file: write text to a file. "
                            "delete_file: delete a file. "
                            "list_directory: list contents of a directory. "
                            "system_status: get CPU, RAM, battery stats. "
                            "clipboard_read: read current clipboard text. "
                            "clipboard_write: write text to clipboard. "
                            "take_screenshot: capture the screen."
                        ),
                    },
                    "kwargs": {
                        "type": "object",
                        "description": (
                            "Arguments for the tool. "
                            "run_shell: {command: str}. "
                            "read_file: {filepath: str}. "
                            "write_file: {filepath: str, content: str, overwrite: bool}. "
                            "delete_file: {filepath: str}. "
                            "list_directory: {directory: str}. "
                            "system_status: {} (no args). "
                            "clipboard_read: {} (no args). "
                            "clipboard_write: {text: str}. "
                            "take_screenshot: {} (no args)."
                        ),
                        "properties": {
                            "command":   {"type": "string"},
                            "filepath":  {"type": "string"},
                            "directory": {"type": "string"},
                            "content":   {"type": "string"},
                            "overwrite": {"type": "boolean"},
                            "text":      {"type": "string"},
                        },
                    },
                    "worker_name": {
                        "type": "string",
                        "default": DEFAULT_WORKER,
                        "description": "Name of the target PC worker. Defaults to duke-laptop.",
                    },
                },
                "required": ["tool"],
            },
        }

    async def run(self, **kwargs) -> str:
        tool_name = kwargs.get("tool")
        tool_kwargs = kwargs.get("kwargs", {})
        worker_name = kwargs.get("worker_name", DEFAULT_WORKER)
        worker_id = f"pc_worker:{worker_name}"
        task_id = str(uuid.uuid4())

        if not tool_name:
            return "Error: 'tool' parameter is required."

        payload = {
            "tool": tool_name,
            "kwargs": tool_kwargs,
            "task_id": task_id,
        }

        logger.info(
            "Dispatching tool '%s' to worker '%s' (task %s)",
            tool_name, worker_id, task_id
        )

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{API_BASE_URL}/worker/command/{worker_id}",
                    json=payload,
                    timeout=5.0,
                )
                if resp.status_code != 200:
                    return f"Error dispatching to worker: HTTP {resp.status_code} — {resp.text}"

            r = aioredis.from_url(REDIS_URL)
            response_key = f"atlas:responses:{worker_id}"
            polls = WORKER_TIMEOUT_SECONDS * 2

            try:
                for _ in range(polls):
                    await asyncio.sleep(0.5)
                    responses = await r.lrange(response_key, 0, -1)
                    for raw in responses:
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if data.get("task_id") == task_id:
                            await r.lrem(response_key, 0, raw)
                            if data.get("status") == "error":
                                return f"PC Worker error: {data.get('result')}"
                            return data.get("result", "Command completed with no output.")
            finally:
                await r.aclose()

            return f"PC Worker did not respond within {WORKER_TIMEOUT_SECONDS}s. Command may still be running."

        except Exception as e:
            logger.error("LocalFileTool execution failed: %s", e)
            return f"Failed to communicate with PC Worker: {e}"