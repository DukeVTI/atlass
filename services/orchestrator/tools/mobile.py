"""
Atlas Mobile Tool — VPS Side
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

    @property
    def name(self) -> str:
        return "mobile_command"

    @property
    def description(self) -> str:
        return (
            "Control Duke's Android phone. Use this to speak text aloud, "
            "get GPS location, send or read SMS, push notifications, "
            "get battery stats, search contacts, create contacts, "
            "control flashlight, set volume, read or write clipboard, "
            "open any app, or read recent notifications from any app. "
            "Only works when Duke's phone is online."
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
                            "speak", "stop_speaking", "get_location",
                            "read_sms", "send_sms", "read_notifications",
                            "get_device_stats", "read_contacts", "create_contact",
                            "set_flashlight", "set_volume", "get_clipboard",
                            "set_clipboard", "open_app",
                        ],
                        "description": (
                            "speak: {text, rate?, pitch?, language?}. "
                            "stop_speaking: {}. "
                            "get_location: {}. "
                            "read_sms: {box?, limit?, filter?}. "
                            "send_sms: {to, message}. "
                            "read_notifications: {limit?, appFilter?}. "
                            "get_device_stats: {}. "
                            "read_contacts: {query?, limit?}. "
                            "create_contact: {name, phone?, email?}. "
                            "set_flashlight: {on: true/false}. "
                            "set_volume: {stream?, level}. "
                            "get_clipboard: {}. "
                            "set_clipboard: {text}. "
                            "open_app: {package}."
                        ),
                    },
                    "kwargs": {
                        "type": "object",
                        "properties": {
                            "text":      {"type": "string"},
                            "rate":      {"type": "number"},
                            "pitch":     {"type": "number"},
                            "language":  {"type": "string"},
                            "to":        {"type": "string"},
                            "message":   {"type": "string"},
                            "title":     {"type": "string"},
                            "body":      {"type": "string"},
                            "box":       {"type": "string"},
                            "limit":     {"type": "integer"},
                            "filter":    {"type": "string"},
                            "appFilter": {"type": "string"},
                            "query":     {"type": "string"},
                            "name":      {"type": "string"},
                            "phone":     {"type": "string"},
                            "email":     {"type": "string"},
                            "on":        {"type": "boolean"},
                            "stream":    {"type": "string"},
                            "level":     {"type": "integer"},
                            "package":   {"type": "string"},
                        },
                    },
                    "worker_name": {
                        "type": "string",
                        "default": DEFAULT_WORKER,
                    },
                },
                "required": ["tool"],
            },
        }

    async def run(self, **kwargs) -> str:
        tool_name   = kwargs.get("tool")
        tool_kwargs = kwargs.get("kwargs", {})
        worker_name = kwargs.get("worker_name", DEFAULT_WORKER)
        worker_id   = f"mobile:{worker_name}"
        task_id     = str(uuid.uuid4())

        if not tool_name:
            return "Error: 'tool' parameter is required."

        payload = {"tool": tool_name, "kwargs": tool_kwargs, "task_id": task_id}
        logger.info("Dispatching mobile tool '%s' to '%s'", tool_name, worker_id)

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{API_BASE_URL}/worker/command/{worker_id}",
                    json=payload,
                    timeout=5.0,
                )
                if resp.status_code != 200:
                    return f"Phone not reachable: HTTP {resp.status_code}. Phone may be offline."

            r = aioredis.from_url(REDIS_URL)
            response_key = f"atlas:task_result:{task_id}"
            polls = WORKER_TIMEOUT_SECONDS * 2

            try:
                for _ in range(polls):
                    await asyncio.sleep(0.5)
                    raw = await r.lpop(response_key)
                    if raw:
                        data = json.loads(raw)
                        if data.get("status") == "error":
                            return f"Phone error: {data.get('result')}"
                        return data.get("result", "Command completed.")
            finally:
                await r.aclose()

            return f"Phone did not respond within {WORKER_TIMEOUT_SECONDS}s. It may be offline."

        except Exception as e:
            logger.error("MobileTool execution failed: %s", e)
            return f"Failed to reach phone: {e}"
