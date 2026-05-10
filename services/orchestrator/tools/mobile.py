"""
Atlas Mobile Tool - VPS Side
-----------------------------
Dispatches commands to Duke's Android phone via the mobile WebSocket worker.
Protocol matches LocalFileTool exactly: sends task to the API hub which routes
via WebSocket to the 'mobile:duke-android' worker, then polls Redis for result.
"""

import asyncio
import json
import logging
import os
import uuid

import httpx
import redis.asyncio as aioredis

from .base import Tool

logger = logging.getLogger("atlas.tools.mobile")

API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
DEFAULT_WORKER = "duke-android"
WORKER_TIMEOUT_SECONDS = 20


class MobileTool(Tool):
    """Bridge tool that sends commands to Duke's Android phone."""

    @property
    def name(self) -> str:
        return "mobile_command"

    @property
    def description(self) -> str:
        return (
            "Control or query Duke's Android phone. "
            "Use this to: speak text aloud through his phone speaker, "
            "get his live GPS location and address, send or read SMS messages, "
            "send push notifications to his phone, get battery and device stats, "
            "search his phone contacts, or read recent notifications from any app. "
            "Only works when Duke's phone is online and connected."
        )

    @property
    def schema(self) -> dict:
        return {
            "name": "mobile_command",
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "enum": [
                            "speak",
                            "stop_speaking",
                            "get_location",
                            "read_sms",
                            "send_sms",
                            "push_notification",
                            "read_notifications",
                            "get_device_stats",
                            "read_contacts",
                        ],
                        "description": (
                            "The tool to run on the phone. "
                            "speak: {text, rate?, pitch?, language?}. "
                            "stop_speaking: {} - stops current TTS. "
                            "get_location: {} - returns GPS coordinates and address. "
                            "read_sms: {box?, maxCount?, filter?} - read SMS inbox/sent. "
                            "send_sms: {to, message} - send SMS to a number. "
                            "push_notification: {title, body, urgent?} - push to phone. "
                            "read_notifications: {limit?, appFilter?} - recent notifications. "
                            "get_device_stats: {} - battery, OS, memory. "
                            "read_contacts: {query?, limit?} - search phone contacts."
                        ),
                    },
                    "kwargs": {
                        "type": "object",
                        "description": "Arguments for the tool.",
                        "properties": {
                            "text":      {"type": "string"},
                            "rate":      {"type": "number"},
                            "pitch":     {"type": "number"},
                            "language":  {"type": "string"},
                            "to":        {"type": "string"},
                            "message":   {"type": "string"},
                            "title":     {"type": "string"},
                            "body":      {"type": "string"},
                            "urgent":    {"type": "boolean"},
                            "box":       {"type": "string", "enum": ["inbox", "sent", "draft"]},
                            "maxCount":  {"type": "integer"},
                            "filter":    {"type": "string"},
                            "limit":     {"type": "integer"},
                            "appFilter": {"type": "string"},
                            "query":     {"type": "string"},
                        },
                    },
                    "worker_name": {
                        "type": "string",
                        "default": DEFAULT_WORKER,
                        "description": "Name of the mobile worker. Defaults to duke-android.",
                    },
                },
                "required": ["tool"],
            },
        }

    async def run(self, **kwargs) -> str:
        tool_name = kwargs.get("tool")
        tool_kwargs = kwargs.get("kwargs", {})
        worker_name = kwargs.get("worker_name", DEFAULT_WORKER)
        worker_id = f"mobile:{worker_name}"
        task_id = str(uuid.uuid4())

        if not tool_name:
            return "Error: 'tool' parameter is required."

        payload = {"tool": tool_name, "kwargs": tool_kwargs, "task_id": task_id}

        logger.info(
            "Dispatching mobile tool '%s' to '%s' (task %s)",
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
                    return f"Phone not reachable: HTTP {resp.status_code} — {resp.text}"

            r = aioredis.from_url(REDIS_URL)
            response_key = f"atlas:task_result:{task_id}"

            try:
                for _ in range(WORKER_TIMEOUT_SECONDS * 2):
                    await asyncio.sleep(0.5)
                    raw = await r.lpop(response_key)
                    if raw:
                        data = json.loads(raw)
                        if data.get("status") == "error":
                            return f"Phone error: {data.get('result')}"
                        return str(data.get("result", "Command completed."))
            finally:
                await r.aclose()

            return f"Phone did not respond within {WORKER_TIMEOUT_SECONDS}s. It may be offline or screen locked."

        except Exception as e:
            logger.error("MobileTool execution failed: %s", e)
            return f"Failed to reach phone: {e}"
