"""
Atlas API Service
-----------------
Central FastAPI application serving as the hub between all Atlas services.

Responsibilities (Layer 1):
  - /health          — simple liveness probe (returns 200)
  - /health/detailed — checks Postgres, Redis, and ChromaDB connectivity

Future layers will add:
  - /ws              — WebSocket bridge for PC worker
  - /webhook         — Cloudflare Tunnel webhook ingestion endpoint
  - /task            — task queue interface
"""

import logging
import os
import json
from contextlib import asynccontextmanager

import asyncpg
import chromadb
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import hmac
import hashlib

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("atlas.api")


# ─── App lifecycle ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Atlas API starting up…")
    
    # Initialize Audit Logs table
    try:
        dsn = os.environ["POSTGRES_DSN"].replace("+asyncpg", "")
        conn = await asyncpg.connect(dsn)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT,
                tool_name TEXT,
                inputs JSONB,
                status TEXT,
                result TEXT
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_messages (
                id SERIAL PRIMARY KEY,
                remote_jid TEXT,
                sender_name TEXT,
                message_text TEXT,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await conn.close()
        logger.info("Database tables initialized (audit_logs, whatsapp_messages).")
    except Exception as e:
        logger.error(f"Failed to initialize Audit Log table: {e}")
        
    yield
    logger.info("Atlas API shutting down.")


app = FastAPI(
    title="Atlas API",
    description="Central hub for the Atlas personal AI butler system.",
    version="1.0.0",
    lifespan=lifespan,
)


# ─── Health endpoints ─────────────────────────────────────────────────────────

@app.get("/health", tags=["health"])
async def health() -> dict:
    """Liveness probe — always returns 200 if the process is running."""
    return {"status": "ok", "service": "atlas-api"}


@app.get("/health/detailed", tags=["health"])
async def health_detailed() -> JSONResponse:
    """
    Readiness probe — checks connectivity to all upstream services.
    Returns 200 if everything is healthy, 503 if any service is degraded.
    """
    results: dict[str, str] = {}

    # ── Postgres ──
    try:
        dsn = os.environ["POSTGRES_DSN"].replace("+asyncpg", "")
        conn = await asyncpg.connect(dsn)
        await conn.execute("SELECT 1")
        await conn.close()
        results["postgres"] = "ok"
        logger.debug("Postgres: ok")
    except Exception as exc:
        results["postgres"] = f"error: {exc}"
        logger.error("Postgres health check failed: %s", exc)

    # ── Redis ──
    try:
        r = aioredis.from_url(os.environ["REDIS_URL"])
        await r.ping()
        await r.aclose()
        results["redis"] = "ok"
        logger.debug("Redis: ok")
    except Exception as exc:
        results["redis"] = f"error: {exc}"
        logger.error("Redis health check failed: %s", exc)

    # ── ChromaDB ──
    try:
        client = chromadb.HttpClient(
            host=os.environ.get("CHROMADB_HOST", "chromadb"),
            port=int(os.environ.get("CHROMADB_PORT", "8000")),
        )
        client.heartbeat()
        results["chromadb"] = "ok"
        logger.debug("ChromaDB: ok")
    except Exception as exc:
        results["chromadb"] = f"error: {exc}"
        logger.error("ChromaDB health check failed: %s", exc)

    all_ok = all(v == "ok" for v in results.values())
    status_code = 200 if all_ok else 503
    overall = "ok" if all_ok else "degraded"

    logger.info("Health check — overall: %s | %s", overall, results)

    return JSONResponse(
        content={"status": overall, "services": results},
        status_code=status_code,
    )

# ─── Webhooks ─────────────────────────────────────────────────────────────────

@app.post("/webhooks/paystack", tags=["webhooks"])
async def paystack_webhook(request: Request, x_paystack_signature: str = Header(None)):
    """
    Ingests Paystack events. Secured via HMAC-SHA512 signature.
    On successful charge, we would push a message to Telegram natively.
    """
    secret = os.getenv("PAYSTACK_SECRET_KEY", "").encode("utf-8")
    if not secret:
        raise HTTPException(status_code=500, detail="Webhook misconfigured: missing secret.")

    body = await request.body()
    
    # Generate signature using HMAC SHA512
    hash_obj = hmac.new(secret, body, hashlib.sha512).hexdigest()

    if not x_paystack_signature or hash_obj != x_paystack_signature:
        logger.warning(f"Webhook signature mismatch! Incoming: {x_paystack_signature} vs Calculated: {hash_obj}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = await request.json()
    event = payload.get("event")
    
    data = payload.get("data", {})
    logger.info(f"Received authentic Paystack webhook event: {event}")
    
    if event == "charge.success":
        amount = data.get("amount", 0) / 100
        currency = data.get("currency", "NGN")
        ref = data.get("reference")
        
        logger.info(f"ACTION REQUIRED: PUSH NOTIFICATION -> Payment of {amount} {currency} received! Ref: {ref}")
        
        # Dispatch to Redis queue for Telegram delivery:
        try:
            import json
            r = aioredis.from_url(os.environ["REDIS_URL"])
            message_payload = {
                "type": "paystack_payment",
                "amount": amount,
                "currency": currency,
                "reference": ref,
                "text": f"💰 Money just landed, sir — {currency} {amount:,.2f} via Paystack. Ref: `{ref}`"
            }
            await r.rpush("atlas:notifications:telegram", json.dumps(message_payload))
            await r.aclose()
        except Exception as e:
            logger.error(f"Failed to enqueue webhook notification to Redis: {e}")
            
    return {"status": "success"}


@app.post("/webhooks/whatsapp", tags=["webhooks"])
async def whatsapp_webhook(request: Request):
    """
    Ingests incoming WhatsApp messages from the Baileys Node.js sidecar.
    Stores them silently in the database, and pushes them to Redis for the bot to score and optionally alert.
    """
    payload = await request.json()
    remote_jid = payload.get("remote_jid")
    sender_name = payload.get("sender_name", "Unknown")
    message_text = payload.get("message_text", "")
    
    if not remote_jid or not message_text:
        return {"status": "ignored"}
        
    try:
        dsn = os.environ["POSTGRES_DSN"].replace("+asyncpg", "")
        conn = await asyncpg.connect(dsn)
        await conn.execute("""
            INSERT INTO whatsapp_messages (remote_jid, sender_name, message_text)
            VALUES ($1, $2, $3)
        """, remote_jid, sender_name, message_text)
        await conn.close()
        logger.info(f"Stored WhatsApp message from {sender_name} ({remote_jid})")
        
        import json
        r = aioredis.from_url(os.environ["REDIS_URL"])
        message_payload = {
            "type": "whatsapp_incoming",
            "remote_jid": remote_jid,
            "sender_name": sender_name,
            "message_text": message_text
        }
        await r.rpush("atlas:notifications:telegram", json.dumps(message_payload))
        await r.aclose()
        
    except Exception as e:
        logger.error(f"Failed to process WhatsApp webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal processing error")
        
    return {"status": "success"}

# ─── Audit Logging ────────────────────────────────────────────────────────────

@app.post("/audit", tags=["audit"])
async def log_audit(request: Request):
    """
    Internal endpoint to record every tool call Atlas makes.
    """
    payload = await request.json()
    
    try:
        dsn = os.environ["POSTGRES_DSN"].replace("+asyncpg", "")
        conn = await asyncpg.connect(dsn)
        await conn.execute("""
            INSERT INTO audit_logs (user_id, tool_name, inputs, status, result)
            VALUES ($1, $2, $3, $4, $5)
        """, 
        payload.get("user_id"),
        payload.get("tool_name"),
        json.dumps(payload.get("inputs", {})),
        payload.get("status"),
        str(payload.get("result"))
        )
        await conn.close()
        return {"status": "recorded"}
    except Exception as e:
        logger.error(f"Audit Log write failure: {e}")
        raise HTTPException(status_code=500, detail="Failed to write to audit log")

# ─── WebSocket Hub ────────────────────────────────────────────────────────────

class ConnectionManager:
    """Manages active WebSocket connections from PC and Mobile workers."""
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.disconnect_times: Dict[str, float] = {}  # worker_id -> UTC timestamp

    async def connect(self, worker_id: str, websocket: WebSocket):
        self.active_connections[worker_id] = websocket
        self.disconnect_times.pop(worker_id, None)  # Clear any offline record
        logger.info(f"Worker connected: {worker_id}. Total active: {len(self.active_connections)}")

    def disconnect(self, worker_id: str):
        if worker_id in self.active_connections:
            del self.active_connections[worker_id]
            import time
            self.disconnect_times[worker_id] = time.time()
            logger.info(f"Worker disconnected: {worker_id}")

    async def send_command(self, worker_id: str, command: dict):
        if worker_id in self.active_connections:
            await self.active_connections[worker_id].send_json(command)
            return True
        return False

    def get_status(self, worker_id: str) -> dict:
        import time
        connected = worker_id in self.active_connections
        offline_minutes = 0.0
        if not connected and worker_id in self.disconnect_times:
            offline_minutes = (time.time() - self.disconnect_times[worker_id]) / 60
        return {"connected": connected, "offline_minutes": offline_minutes}

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, authorization: str = Header(None)):
    """
    WebSocket endpoint for workers.
    Expects 'Bearer <WORKER_TOKEN>' in Authorization header or query param.
    """
    # Simple token-based auth
    expected_token = os.getenv("WORKER_TOKEN", "atlas_pc_worker_secret")
    
    # Check header or query param
    token = authorization
    if not token:
        token = websocket.query_params.get("token")
    
    if not token or (token != f"Bearer {expected_token}" and token != expected_token):
        logger.warning("Rejected WebSocket connection: Invalid token.")
        await websocket.close(code=1008)
        return

    worker_id = "unknown"
    try:
        await websocket.accept()
        # First message should be identity
        data = await websocket.receive_json()
        if data.get("type") == "identity":
            worker_id = f"{data.get('worker_type')}:{data.get('name')}"
            await manager.connect(worker_id, websocket)
            
            # Keep connection alive and listen for responses
            while True:
                response = await websocket.receive_json()
                logger.info(f"Response from {worker_id}: {response.get('status')}")
                
                # Push response to a task-specific Redis list so the Orchestrator can BLPOP it
                try:
                    task_id = response.get("task_id", "unknown")
                    r = aioredis.from_url(os.environ["REDIS_URL"])
                    # Expire the list after 60 seconds just to keep Redis clean
                    await r.lpush(f"atlas:task_result:{task_id}", json.dumps(response))
                    await r.expire(f"atlas:task_result:{task_id}", 60)
                    await r.aclose()
                except Exception as e:
                    logger.error(f"Failed to push worker response to Redis: {e}")

    except WebSocketDisconnect:
        manager.disconnect(worker_id)
    except Exception as e:
        logger.error(f"WebSocket error for {worker_id}: {e}")
        manager.disconnect(worker_id)

@app.post("/worker/command/{worker_id}", tags=["worker"])
async def send_worker_command(worker_id: str, command: dict):
    """
    Endpoint for the Orchestrator to send commands to a specific worker.
    """
    success = await manager.send_command(worker_id, command)
    if not success:
        raise HTTPException(status_code=404, detail="Worker not connected")
    return {"status": "dispatched"}


@app.get("/worker/status/{worker_id}", tags=["worker"])
async def worker_status(worker_id: str) -> dict:
    """Returns the connection status and offline duration of a worker."""
    return manager.get_status(worker_id)
