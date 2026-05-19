"""
Atlas PC Worker — Layer 2 Daemon
--------------------------------
Lightweight background executor for local machine tasks.
Connects to the VPS Brain via WebSocket.

Capabilities:
  - File system operations (Read/List/Search)
  - Shell script execution
  - Screenshot capture
  - Clipboard access
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx
import websockets
from dotenv import load_dotenv

# ─── Configuration ────────────────────────────────────────────────────────────

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
API_URL = os.getenv("API_BASE_URL", "http://api:8000") # Use actual VPS URL in prod
WS_URL = API_URL.replace("http", "ws") + "/ws"
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "atlas_pc_worker_secret")
SCOPED_ROOT = Path(os.getenv("SCOPED_ROOT", Path.home() / "Documents" / "Atlas")).expanduser()

# Ensure scoped root exists
SCOPED_ROOT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] pc-worker — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("atlas.pc_worker")

# ─── Capabilities ─────────────────────────────────────────────────────────────

async def handle_file_read(params: dict) -> dict:
    path = SCOPED_ROOT / params.get("path", "").lstrip("/")
    if not path.exists() or not path.is_file():
        return {"error": f"File not found: {params.get('path')}"}
    
    try:
        content = path.read_text(encoding="utf-8")
        return {"content": content, "size": len(content)}
    except Exception as e:
        return {"error": str(e)}

async def handle_file_list(params: dict) -> dict:
    path = SCOPED_ROOT / params.get("path", "").lstrip("/")
    if not path.exists() or not path.is_dir():
        return {"error": f"Directory not found: {params.get('path')}"}
    
    try:
        items = []
        for item in path.iterdir():
            items.append({
                "name": item.name,
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else 0,
                "modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat()
            })
        return {"items": items}
    except Exception as e:
        return {"error": str(e)}

SCRIPT_TIMEOUT_SECONDS = 30

# Allowed commands — extend this list deliberately, never use a blocklist
ALLOWED_COMMANDS = {
    "ls", "dir", "echo", "cat", "head", "tail", "grep", "find",
    "python", "python3", "pip", "pip3",
    "node", "npm",
    "git",
    "curl", "wget",
    "df", "du", "free", "uptime", "whoami", "pwd",
    "cp", "mv", "mkdir", "touch", "rm",
}

# Patterns that indicate path traversal or privilege escalation attempts
_DANGEROUS_PATTERNS = [
    "..", "/etc/", "/root/", "/proc/", "/sys/", "/dev/",
    "~/.ssh", "~/.aws", "~/.env",
    "sudo", "su ", "chmod 777", "chown root",
    "; rm", "&& rm", "| rm",
    "> /dev/", ">> /dev/",
]


def _is_safe_script(script: str) -> tuple[bool, str]:
    """
    Returns (safe, reason). Rejects scripts that:
    1. Use a command not in the ALLOWED_COMMANDS allowlist
    2. Contain dangerous path or privilege-escalation patterns
    """
    stripped = script.strip()
    if not stripped:
        return False, "Empty script."

    # Extract the first token (the command being run)
    first_token = stripped.split()[0].lstrip("./")
    if first_token not in ALLOWED_COMMANDS:
        return False, f"Command '{first_token}' is not on the allowlist."

    lower = script.lower()
    for pattern in _DANGEROUS_PATTERNS:
        if pattern in lower:
            return False, f"Dangerous pattern detected: '{pattern}'."

    return True, ""


async def handle_execute_script(params: dict) -> dict:
    script = params.get("script", "")
    if not script:
        return {"error": "No script provided"}

    safe, reason = _is_safe_script(script)
    if not safe:
        logger.warning("Blocked script execution: %s | Script: %r", reason, script[:200])
        return {"error": f"Script rejected by safety filter: {reason}"}

    try:
        process = await asyncio.create_subprocess_shell(
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=SCOPED_ROOT,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=SCRIPT_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            process.kill()
            return {"error": f"Script timed out after {SCRIPT_TIMEOUT_SECONDS}s."}

        return {
            "exit_code": process.returncode,
            "stdout": stdout.decode(errors="replace").strip(),
            "stderr": stderr.decode(errors="replace").strip(),
        }
    except Exception as e:
        return {"error": str(e)}

# ─── Dispatcher ───────────────────────────────────────────────────────────────

COMMAND_HANDLERS = {
    "file_read": handle_file_read,
    "file_list": handle_file_list,
    "execute_script": handle_execute_script,
}

async def process_command(message: str) -> str:
    try:
        data = json.loads(message)
        cmd = data.get("command")
        params = data.get("params", {})
        request_id = data.get("request_id")

        logger.info(f"Received command: {cmd} (ID: {request_id})")

        if cmd in COMMAND_HANDLERS:
            result = await COMMAND_HANDLERS[cmd](params)
        else:
            result = {"error": f"Unknown command: {cmd}"}

        return json.dumps({
            "type": "response",
            "request_id": request_id,
            "status": "success" if "error" not in result else "error",
            "result": result
        })
    except Exception as e:
        logger.error(f"Failed to process command: {e}")
        return json.dumps({"type": "error", "message": str(e)})

# ─── Worker Loop ──────────────────────────────────────────────────────────────

async def worker_loop():
    logger.info(f"Connecting to Atlas Brain at {WS_URL}...")
    logger.info(f"Scoped root: {SCOPED_ROOT}")

    retry_delay = 5
    while True:
        try:
            async with websockets.connect(
                WS_URL,
                extra_headers={"Authorization": f"Bearer {WORKER_TOKEN}"}
            ) as websocket:
                logger.info("✓ Connected to Brain WebSocket")
                retry_delay = 5 # Reset delay on success
                
                # Send identity
                await websocket.send(json.dumps({
                    "type": "identity",
                    "worker_type": "pc_worker",
                    "name": "duke-laptop"
                }))

                async for message in websocket:
                    response = await process_command(message)
                    await websocket.send(response)

        except (websockets.ConnectionClosed, ConnectionRefusedError) as e:
            logger.warning(f"Connection lost ({e}). Retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60) # Exponential backoff
        except Exception as e:
            logger.error(f"Unexpected error in worker loop: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
