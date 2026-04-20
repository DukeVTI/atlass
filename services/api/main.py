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
from contextlib import asynccontextmanager

import asyncpg
import chromadb
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Header, HTTPException
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
                "text": f"💳 *Payment Received!*\nYou have received {currency} {amount:,.2f} via Paystack.\nRef: `{ref}`"
            }
            await r.rpush("atlas:notifications:telegram", json.dumps(message_payload))
            await r.aclose()
        except Exception as e:
            logger.error(f"Failed to enqueue webhook notification to Redis: {e}")
            
    return {"status": "success"}
