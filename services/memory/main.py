"""
Atlas Memory Service
---------------------
FastAPI service for episodic, factual, and procedural memory management.
Provides HTTP endpoints for storing and retrieving memories.
Integrates with PostgreSQL (structured data + pgvector embeddings).

Endpoints:
- GET /health                          — Liveness check
- GET /health/detailed                 — Readiness (all dependencies)
- POST /memory/episodic                — Store episodic memory
- GET /memory/search                   — Semantic search episodic
- POST /memory/factual                 — Store factual memory
- GET /memory/factual/{key}            — Retrieve factual memory
- DELETE /memory/clear/{memory_type}   — Clear all memories of type
- POST /memory/conversation            — Store conversation turn
- GET /memory/conversation/{session}   — Get conversation history
- GET /memory/context                  — Get memory context for LLM
"""

import os
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import httpx

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

# Memory system imports
from memory import (
    EpisodicMemory,
    FactualMemory,
    ProceduralMemory,
    ConversationMemory,
    MemorySystem,
    init_memory_schema,
    check_schema_health,
    cleanup_expired_memories,
    EpisodicMemoryRecord,
    FactualMemoryRecord,
    ConversationMemoryRecord,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ─── ENVIRONMENT & CONFIG ──────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://atlas:atlas_pass@postgres:5432/atlas"
)

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8001")

# ─── REQUEST/RESPONSE MODELS ──────────────────────────────────────────────────

class StoreEpisodicRequest(BaseModel):
    """Request to store episodic memory."""
    user_id: int
    event_type: str  # "tool_execution", "email_received", etc.
    summary: str
    full_context: str
    tags: List[str] = []
    source: str
    reference_id: Optional[str] = None
    ttl_days: int = 90


class SearchRequest(BaseModel):
    """Request to search memories."""
    user_id: int
    query: str
    top_k: int = 5


class StoreFactualRequest(BaseModel):
    """Request to store factual memory."""
    user_id: int
    key: str
    category: str  # "contact", "project", "preference"
    value: Dict[str, Any]
    source: Optional[str] = None
    verified: bool = False


class StoreConversationRequest(BaseModel):
    """Request to store conversation turn."""
    user_id: int
    session_id: str
    turn_number: int
    user_message: str
    assistant_response: str
    tool_calls: List[str] = []


class MemorySearchResult(BaseModel):
    """Single search result."""
    id: str
    text: str
    relevance: float
    source: Optional[str] = None
    timestamp: Optional[str] = None


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    service: str = "atlas-memory"


class DetailedHealthResponse(HealthResponse):
    """Detailed health check with dependency status."""
    postgres: str  # "ok" or "error"
    chromadb: str  # "ok" or "error"
    embeddings: str  # "ok" or "error"


# ─── GLOBAL STATE ──────────────────────────────────────────────────────────

memory_system: Optional[MemorySystem] = None
db_engine = None
SessionLocal = None
http_client: Optional[httpx.AsyncClient] = None


# ─── LIFESPAN ──────────────────────────────────────────────────────────────────

async def _init_db_with_retry(max_retries: int = 5, initial_delay: float = 2.0):
    """Initialize database with exponential backoff retry."""
    global db_engine, SessionLocal
    
    # Wait for postgres to fully initialize (health check can pass before auth is ready)
    logger.info("Waiting 5 seconds for postgres to fully initialize...")
    await asyncio.sleep(5)
    
    delay = initial_delay
    last_error = None
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Database connection attempt {attempt + 1}/{max_retries}...")
            
            # Create engine and test connection
            if db_engine is None:
                db_engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
                SessionLocal = sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
            
            # Test connection with a simple query
            async with SessionLocal() as session:
                await session.execute(text("SELECT 1"))
            
            logger.info("✓ PostgreSQL connection successful")
            
            # Now initialize schema
            await init_memory_schema(DATABASE_URL)
            logger.info("✓ Memory schema initialized successfully")
            return
            
        except Exception as e:
            last_error = e
            logger.warning(f"Connection attempt {attempt + 1} failed: {type(e).__name__}: {e}")
            
            if db_engine:
                await db_engine.dispose()
                db_engine = None
                SessionLocal = None
            
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {delay}s...")
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                logger.error(f"Failed to connect after {max_retries} attempts")
    
    raise last_error


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    global memory_system, http_client, db_engine, SessionLocal
    
    logger.info("🚀 Memory Service starting up...")
    
    try:
        # Initialize database with retry logic
        await _init_db_with_retry()
        
        # Create a session for MemorySystem initialization
        async with SessionLocal() as session:
            # Initialize memory system with database session
            memory_system = MemorySystem(
                embedding_model="all-MiniLM-L6-v2",
                use_pgvector=True
            )
            await memory_system.initialize()
            logger.info("✓ Memory system initialized (PostgreSQL + pgvector)")
        
        # Initialize HTTP client
        http_client = httpx.AsyncClient(timeout=30.0)
        logger.info("✓ HTTP client ready")
        
        logger.info("✅ Memory Service fully operational")
    
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("🛑 Memory Service shutting down...")
    try:
        if http_client:
            await http_client.aclose()
        if db_engine:
            await db_engine.dispose()
        logger.info("✓ Cleanup complete")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


# ─── FASTAPI APP ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Atlas Memory Service",
    description="Persistent memory management for Atlas butler",
    version="0.2.0",
    lifespan=lifespan
)


# ─── HEALTH CHECKS ────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Liveness check - service is running."""
    return HealthResponse(status="ok")


@app.get("/health/detailed", response_model=DetailedHealthResponse, tags=["Health"])
async def health_detailed():
    """Readiness check - all dependencies available."""
    
    postgres_status = "error"
    chromadb_status = "error"
    embeddings_status = "error"
    
    # Check PostgreSQL
    try:
        async with SessionLocal() as session:
            await session.execute("SELECT 1")
            postgres_status = "ok"
    except Exception as e:
        logger.warning(f"PostgreSQL check failed: {e}")
    
    # Check ChromaDB
    try:
        if memory_system and memory_system.vector_store._init_done:
            chromadb_status = "ok"
    except Exception as e:
        logger.warning(f"ChromaDB check failed: {e}")
    
    # Check Embeddings
    try:
        if memory_system and memory_system.embeddings._init_done:
            embeddings_status = "ok"
    except Exception as e:
        logger.warning(f"Embeddings check failed: {e}")
    
    overall = "ok" if all([
        postgres_status == "ok",
        chromadb_status == "ok",
        embeddings_status == "ok"
    ]) else "degraded"
    
    return DetailedHealthResponse(
        status=overall,
        postgres=postgres_status,
        chromadb=chromadb_status,
        embeddings=embeddings_status
    )


# ─── EPISODIC MEMORY ENDPOINTS ────────────────────────────────────────────────

@app.post("/memory/episodic", tags=["Episodic"])
async def store_episodic(request: StoreEpisodicRequest):
    """Store episodic memory (tool execution, email, event, etc.)."""
    
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")
    
    try:
        import uuid
        memory_id = str(uuid.uuid4())
        
        metadata = {
            "event_type": request.event_type,
            "source": request.source,
            "reference_id": request.reference_id,
            "tags": request.tags,
            "full_context": request.full_context,
            "ttl_days": request.ttl_days,
        }
        
        # 1. First, INSERT the record into Postgres
        from datetime import datetime
        async with SessionLocal() as session:
            record = EpisodicMemoryRecord(
                id=memory_id,
                user_id=request.user_id,
                event_type=request.event_type,
                summary=request.summary,
                full_context=request.full_context,
                timestamp=datetime.utcnow(),
                tags=request.tags,
                source=request.source,
                reference_id=request.reference_id,
                ttl_days=request.ttl_days
            )
            session.add(record)
            await session.commit()

        # 2. Then, generate and store embedding via MemorySystem
        async with SessionLocal() as session:
            await memory_system.remember_event(
                db_session=session,
                user_id=request.user_id,
                memory_id=memory_id,
                summary=request.summary,
                metadata=metadata
            )
        
        logger.info(f"✓ Stored episodic memory: {memory_id} ({request.event_type})")
        
        return {
            "success": True,
            "memory_id": memory_id,
            "event_type": request.event_type
        }
    
    except Exception as e:
        logger.error(f"Failed to store episodic memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory/search", response_model=List[MemorySearchResult], tags=["Episodic"])
async def search_episodic(
    user_id: int = Query(...),
    query: str = Query(...),
    top_k: int = Query(5, ge=1, le=20)
):
    """Search episodic memories by semantic similarity."""
    
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")
    
    try:
        results = await memory_system.recall_events(user_id, query, top_k)
        
        return [
            MemorySearchResult(
                id=r["id"],
                text=r["text"],
                relevance=r["relevance"],
                source=r["metadata"].get("source"),
                timestamp=r["metadata"].get("timestamp")
            )
            for r in results
        ]
    
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── FACTUAL MEMORY ENDPOINTS ──────────────────────────────────────────────────

@app.post("/memory/factual", tags=["Factual"])
async def store_factual(request: StoreFactualRequest):
    """Store factual memory (contact, project, preference, etc.)."""
    
    if not SessionLocal:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        import uuid
        memory_id = str(uuid.uuid4())
        
        async with SessionLocal() as session:
            record = FactualMemoryRecord(
                id=memory_id,
                user_id=request.user_id,
                key=request.key,
                category=request.category,
                value=request.value,
                source=request.source,
                verified=request.verified
            )
            session.add(record)
            await session.commit()
        
        logger.info(f"✓ Stored factual memory: {memory_id} ({request.key})")
        
        return {
            "success": True,
            "memory_id": memory_id,
            "key": request.key
        }
    
    except Exception as e:
        logger.error(f"Failed to store factual memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory/factual/{key}", tags=["Factual"])
async def get_factual(user_id: int = Query(...), key: str = None):
    """Retrieve factual memory by key."""
    
    if not SessionLocal:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        from sqlalchemy import select
        
        async with SessionLocal() as session:
            query = select(FactualMemoryRecord).where(
                (FactualMemoryRecord.user_id == user_id) &
                (FactualMemoryRecord.key == key)
            )
            result = await session.execute(query)
            record = result.scalars().first()
            
            if not record:
                raise HTTPException(status_code=404, detail="Memory not found")
            
            return {
                "id": record.id,
                "key": record.key,
                "value": record.value,
                "verified": record.verified,
                "updated": record.last_updated.isoformat()
            }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retrieve factual memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── CONVERSATION MEMORY ENDPOINTS ─────────────────────────────────────────────

@app.post("/memory/conversation", tags=["Conversation"])
async def store_conversation(request: StoreConversationRequest):
    """Store conversation turn with embeddings."""
    
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")
    
    try:
        import uuid
        turn_id = str(uuid.uuid4())
        
        # 1. First, ensure the conversation record exists in Postgres
        from datetime import datetime
        async with SessionLocal() as session:
            from sqlalchemy import select
            # Check if session exists
            stmt = select(ConversationMemoryRecord).where(
                (ConversationMemoryRecord.user_id == request.user_id) &
                (ConversationMemoryRecord.session_id == request.session_id)
            )
            result = await session.execute(stmt)
            record = result.scalars().first()
            
            if not record:
                # Create new session record
                record = ConversationMemoryRecord(
                    id=str(uuid.uuid4()),
                    user_id=request.user_id,
                    session_id=request.session_id,
                    turns_json=[],
                    started_at=datetime.utcnow()
                )
                session.add(record)
            
            # Update turns and timestamp
            turns = record.turns_json or []
            turns.append({
                "user": request.user_message,
                "assistant": request.assistant_response,
                "timestamp": datetime.utcnow().isoformat(),
                "tools": request.tool_calls
            })
            record.turns_json = turns
            record.last_turn_at = datetime.utcnow()
            
            await session.commit()

        # 2. Then, generate and store embedding via MemorySystem
        async with SessionLocal() as session:
            await memory_system.remember_conversation(
                db_session=session,
                user_id=request.user_id,
                session_id=request.session_id,
                turn_id=turn_id,
                user_text=request.user_message,
                assistant_text=request.assistant_response,
                metadata={
                    "turn_number": request.turn_number,
                    "tool_calls": request.tool_calls
                }
            )
        
        logger.info(f"✓ Stored conversation turn: {turn_id}")
        
        return {
            "success": True,
            "turn_id": turn_id,
            "session_id": request.session_id
        }
    
    except Exception as e:
        logger.error(f"Failed to store conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory/conversation/{session_id}", tags=["Conversation"])
async def get_conversation_history(
    session_id: str,
    user_id: int = Query(...)
):
    """Get conversation history for a session."""
    
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")
    
    try:
        # Retrieve last 10 turns from conversation history
        async with SessionLocal() as session:
            results = await memory_system.recall_conversation(
                db_session=session,
                user_id=user_id,
                session_id=session_id,
                query="",  # Empty query to get full history
                top_k=10
            )
        
        return {
            "session_id": session_id,
            "turns": results
        }
    
    except Exception as e:
        logger.error(f"Failed to retrieve conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── MEMORY CONTEXT FOR LLM ────────────────────────────────────────────────────

@app.get("/memory/context", tags=["Context"])
async def get_memory_context(
    user_id: int = Query(...),
    session_id: Optional[str] = Query(None),
    query: Optional[str] = Query(None)
):
    """
    Get memory context to inject into LLM prompt.
    Returns relevant episodic and factual memories + conversation history.
    """
    
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")
    
    try:
        search_query = query or "general context"
        
        async with SessionLocal() as session:
            # Recall relevant episodic memories
            episodic = await memory_system.recall_events(
                db_session=session,
                user_id=user_id,
                query=search_query,
                top_k=5
            )
            
            # Recall conversation context if session provided
            conversation = []
            if session_id:
                conversation = await memory_system.recall_conversation(
                    db_session=session,
                    user_id=user_id,
                    session_id=session_id,
                    query=search_query,
                    top_k=3
                )
        
        # Format for LLM injection
        context = ""
        
        if episodic:
            context += "\n[RELEVANT MEMORIES]\n"
            for mem in episodic:
                relevance_pct = int(mem["relevance"] * 100)
                context += f"- {mem['text']} ({relevance_pct}% relevant)\n"
        
        if conversation:
            context += "\n[RECENT CONVERSATION]\n"
            for turn in conversation:
                context += f"- {turn['text']}\n"
        
        return {
            "user_id": user_id,
            "context": context,
            "episodic_count": len(episodic),
            "conversation_count": len(conversation)
        }
    
    except Exception as e:
        logger.error(f"Failed to get memory context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── CLEANUP ENDPOINTS ─────────────────────────────────────────────────────────

@app.delete("/memory/clear/{memory_type}", tags=["Admin"])
async def clear_memories(
    memory_type: str,  # "episodic", "factual", "conversation", "all"
    user_id: int = Query(...)
):
    """
    Clear all memories of a specific type for a user.
    Used for user-initiated wipe or testing.
    """
    
    if memory_type not in ["episodic", "factual", "conversation", "all"]:
        raise HTTPException(
            status_code=400,
            detail="memory_type must be: episodic, factual, conversation, or all"
        )
    
    if not memory_system or not SessionLocal:
        raise HTTPException(status_code=503, detail="Memory system not initialized")
    
    try:
        deleted_count = 0
        
        if memory_type in ["episodic", "all"]:
            await memory_system.vector_store.delete_user_memories(user_id)
            deleted_count += 1
        
        if memory_type in ["factual", "all"]:
            async with SessionLocal() as session:
                from sqlalchemy import delete
                stmt = delete(FactualMemoryRecord).where(
                    FactualMemoryRecord.user_id == user_id
                )
                result = await session.execute(stmt)
                deleted_count += result.rowcount
                await session.commit()
        
        logger.info(f"✓ Cleared {memory_type} memories for user {user_id}")
        
        return {
            "success": True,
            "memory_type": memory_type,
            "records_deleted": deleted_count
        }
    
    except Exception as e:
        logger.error(f"Failed to clear memories: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── STARTUP MESSAGE ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="info")
