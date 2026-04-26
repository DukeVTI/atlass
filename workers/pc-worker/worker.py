import asyncio
import websockets
import json
import os
import logging
from dotenv import load_dotenv

from local_tools import TOOL_REGISTRY

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] PC-WORKER — %(message)s",
)
logger = logging.getLogger("pc-worker")

# Load configuration
VPS_URL = os.getenv("ATLAS_VPS_URL", "ws://localhost:8000")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "atlas_pc_worker_secret")
WORKER_NAME = os.getenv("WORKER_NAME", "local")

async def connect_and_listen():
    uri = f"{VPS_URL}/ws?token={WORKER_TOKEN}"
    logger.info(f"Attempting to connect to VPS: {uri}")
    
    while True:
        try:
            async with websockets.connect(uri) as websocket:
                logger.info("✅ Connected to Atlas VPS successfully.")
                
                # Identify self
                identity = {
                    "type": "identity",
                    "worker_type": "pc",
                    "name": WORKER_NAME
                }
                await websocket.send(json.dumps(identity))
                
                # Listen loop
                while True:
                    message = await websocket.recv()
                    task = json.loads(message)
                    logger.info(f"Received task: {task.get('task_id')} | Tool: {task.get('tool')}")
                    
                    # Execute tool
                    tool_name = task.get("tool")
                    kwargs = task.get("kwargs", {})
                    task_id = task.get("task_id")
                    
                    response = {
                        "task_id": task_id,
                        "status": "success",
                        "result": None
                    }
                    
                    if tool_name in TOOL_REGISTRY:
                        try:
                            # Run synchronously for now (simple tools)
                            func = TOOL_REGISTRY[tool_name]
                            result = func(**kwargs)
                            response["result"] = result
                        except Exception as e:
                            logger.error(f"Error executing {tool_name}: {e}")
                            response["status"] = "error"
                            response["result"] = str(e)
                    else:
                        response["status"] = "error"
                        response["result"] = f"Unknown tool: {tool_name}"
                        
                    # Send response back
                    logger.info(f"Sending response for task {task_id}")
                    await websocket.send(json.dumps(response))
                    
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"Connection closed. Retrying in 5 seconds... ({e})")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Connection error: {e}. Retrying in 5 seconds...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    logger.info("Starting Atlas PC Worker Daemon...")
    asyncio.run(connect_and_listen())
