import asyncio
import websockets
import json
import os
from dotenv import load_dotenv

load_dotenv("workers/pc-worker/.env")

VPS_URL = os.getenv("ATLAS_VPS_URL", "ws://localhost:8000")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "change_me_before_deploy")

async def test():
    uri = f"{VPS_URL}/ws?token={WORKER_TOKEN}"
    print(f"Connecting to {uri}...")
    try:
        async with websockets.connect(uri) as websocket:
            print("✅ Handshake successful.")
            
            # Send identity
            identity = {
                "type": "identity",
                "worker_type": "pc_worker",
                "name": "duke-laptop"
            }
            await websocket.send(json.dumps(identity))
            print("✅ Identity sent.")
            
            # Wait to see if it stays open
            print("Waiting 5 seconds to see if connection is closed by server...")
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=5)
                print(f"Received message: {msg}")
            except asyncio.TimeoutError:
                print("✅ Connection stayed open for 5 seconds (Token likely valid).")
            except websockets.exceptions.ConnectionClosed as e:
                print(f"❌ Connection CLOSED by server. Code: {e.code}, Reason: {e.reason}")
                if e.code == 1008:
                    print("!!! ERROR 1008: POLICY VIOLATION (Invalid Token) !!!")
    except Exception as e:
        print(f"❌ Connection FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test())
