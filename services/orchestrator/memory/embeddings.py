"""
Atlas Embeddings & Vector Memory
---------------------------------
Handles embedding generation via sentence-transformers.
Vectors stored in PostgreSQL (jsonb) for simplicity.

Model: all-MiniLM-L6-v2 (33MB, CPU-safe, 384-dim embeddings)
"""

import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import numpy as np

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

logger = logging.getLogger(__name__)


class EmbeddingEngine:
    """
    Handles text-to-vector embedding via sentence-transformers.
    Runs in thread pool to avoid blocking event loop.
    """
    
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize embedding model.
        
        Args:
            model_name: HuggingFace model identifier (default: all-MiniLM-L6-v2)
        """
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise ImportError(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            )
        
        self.model_name = model_name
        self.model = None
        self._init_done = False
        logger.info(f"EmbeddingEngine initialized for model: {model_name}")
    
    async def initialize(self) -> None:
        """Load model asynchronously (happens in thread pool)."""
        if self._init_done:
            return
        
        def _load_model():
            return SentenceTransformer(self.model_name)
        
        self.model = await asyncio.get_event_loop().run_in_executor(
            None, _load_model
        )
        self._init_done = True
        logger.info(f"✓ Embedding model loaded: {self.model_name}")
    
    async def embed(self, text: str) -> List[float]:
        """
        Embed a single text string.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector (list of floats)
        """
        if not self._init_done:
            await self.initialize()
        
        def _embed():
            embedding = self.model.encode(text, convert_to_numpy=False)
            return embedding.tolist() if hasattr(embedding, 'tolist') else list(embedding)
        
        result = await asyncio.get_event_loop().run_in_executor(
            None, _embed
        )
        return result
    
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Embed multiple texts efficiently in batch.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        if not self._init_done:
            await self.initialize()
        
        def _embed_batch():
            embeddings = self.model.encode(texts, convert_to_numpy=False)
            return [emb.tolist() if hasattr(emb, 'tolist') else list(emb) 
                    for emb in embeddings]
        
        result = await asyncio.get_event_loop().run_in_executor(
            None, _embed_batch
        )
        return result


class VectorMemoryStore:
    """
    PostgreSQL pgvector-backed vector memory for episodic and conversation memories.
    Falls back to JSONB cosine similarity if pgvector extension not available.
    Provides semantic similarity search with native PostgreSQL support.
    """
    
    def __init__(self, db_session: AsyncSession, use_pgvector: bool = True):
        """
        Initialize PostgreSQL vector store.
        
        Args:
            db_session: Active AsyncSession connection
            use_pgvector: Whether to use pgvector (True) or JSONB fallback (False)
        """
        self.db_session = db_session
        self.use_pgvector = use_pgvector
        self._init_done = False
        logger.info(f"VectorMemoryStore initialized (pgvector={use_pgvector})")
    
    async def initialize(self) -> None:
        """Check pgvector extension availability and configure accordingly."""
        if self._init_done:
            return
        
        try:
            # Try to use pgvector if available
            if self.use_pgvector:
                result = await self.db_session.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname='vector'")
                )
                if not result.scalar():
                    logger.warning("pgvector extension not installed, falling back to JSONB")
                    self.use_pgvector = False
                else:
                    logger.info("✓ pgvector extension confirmed available")
        except Exception as e:
            logger.warning(f"pgvector check failed: {e}, using JSONB fallback")
            self.use_pgvector = False
        
        self._init_done = True
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        arr1 = np.array(vec1, dtype=np.float32)
        arr2 = np.array(vec2, dtype=np.float32)
        
        dot_product = np.dot(arr1, arr2)
        norm1 = np.linalg.norm(arr1)
        norm2 = np.linalg.norm(arr2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return float(dot_product / (norm1 * norm2))
    
    async def store_episodic(
        self,
        user_id: int,
        memory_id: str,
        text: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Store episodic memory with embedding in PostgreSQL.
        
        Args:
            user_id: User ID for scoping
            memory_id: Unique memory identifier
            text: Summary text for search
            embedding: Pre-computed embedding vector
            metadata: Additional metadata to store
        """
        if not self._init_done:
            await self.initialize()
        
        # Store embedding as JSONB (always works)
        embedding_json = json.dumps(embedding)
        
        # SQL to update embedding in existing episodic_memory record
        # (The record should already exist in the database, we're just updating the embedding)
        stmt = text(
            """
            UPDATE episodic_memory 
            SET embedding = :embedding_json
            WHERE id = :memory_id AND user_id = :user_id
            """
        )
        
        await self.db_session.execute(
            stmt,
            {"embedding_json": embedding_json, "memory_id": memory_id, "user_id": user_id}
        )
        await self.db_session.commit()
        logger.debug(f"Stored episodic memory embedding: {memory_id}")
    
    async def store_conversation(
        self,
        user_id: int,
        turn_id: str,
        user_text: str,
        assistant_text: str,
        embeddings: tuple,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Store conversation turn with embeddings.
        
        Args:
            user_id: User ID for scoping
            turn_id: Unique turn identifier
            user_text: User's message
            assistant_text: Assistant's response
            embeddings: Tuple of (user_embedding, assistant_embedding)
            metadata: Additional metadata
        """
        if not self._init_done:
            await self.initialize()
        
        # Store the assistant embedding (more relevant for context)
        assistant_embedding = embeddings[1] if len(embeddings) > 1 else embeddings[0]
        embedding_json = json.dumps(assistant_embedding)
        
        stmt = text(
            """
            UPDATE conversation_memory 
            SET embedding = :embedding_json
            WHERE id = :turn_id AND user_id = :user_id
            """
        )
        
        await self.db_session.execute(
            stmt,
            {"embedding_json": embedding_json, "turn_id": turn_id, "user_id": user_id}
        )
        await self.db_session.commit()
        logger.debug(f"Stored conversation turn embedding: {turn_id}")
    
    async def search_episodic(
        self,
        user_id: int,
        query: str,
        embedding: List[float],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search episodic memories by semantic similarity using PostgreSQL.
        
        Args:
            user_id: User ID for scoping
            query: Query text (for logging)
            embedding: Query embedding vector
            top_k: Number of results to return
            
        Returns:
            List of matching memories with scores
        """
        if not self._init_done:
            await self.initialize()
        
        embedding_json = json.dumps(embedding)
        
        # JSONB cosine similarity search
        # Uses SQL function to calculate similarity for all rows, then sorts by relevance
        stmt = text(
            """
            SELECT 
                id,
                summary,
                embedding,
                (1.0 - (
                    (SELECT SUM(a * b) FROM jsonb_each_text(:embedding) e, jsonb_each_text(embedding) v WHERE e.key = v.key)
                    / 
                    (SQRT(SELECT SUM(POWER(CAST(v AS FLOAT8), 2)) FROM jsonb_each_text(:embedding) e(v))) *
                    (SQRT(SELECT SUM(POWER(CAST(v AS FLOAT8), 2)) FROM jsonb_each_text(embedding) e(v)))
                )) AS relevance
            FROM episodic_memory
            WHERE user_id = :user_id AND embedding IS NOT NULL
            ORDER BY relevance DESC
            LIMIT :top_k
            """
        )
        
        try:
            result = await self.db_session.execute(
                stmt,
                {"user_id": user_id, "embedding": embedding_json, "top_k": top_k}
            )
            rows = result.fetchall()
            
            matches = []
            for row in rows:
                matches.append({
                    "id": row[0],
                    "text": row[1],
                    "relevance": min(1.0, max(0.0, float(row[3]))) if row[3] is not None else 0.0,
                    "metadata": {}
                })
            
            logger.debug(f"Found {len(matches)} episodic memories for user {user_id}")
            return matches
        
        except Exception as e:
            logger.warning(f"Complex SQL similarity search failed, using Python fallback: {e}")
            # Fallback: fetch all embeddings and compute similarity in Python
            stmt_fallback = text(
                """
                SELECT id, summary, embedding
                FROM episodic_memory
                WHERE user_id = :user_id AND embedding IS NOT NULL
                LIMIT 1000
                """
            )
            
            result = await self.db_session.execute(
                stmt_fallback,
                {"user_id": user_id}
            )
            rows = result.fetchall()
            
            matches = []
            for row in rows:
                mem_id, text_summary, emb_json = row
                if emb_json:
                    mem_embedding = json.loads(emb_json)
                    similarity = self._cosine_similarity(embedding, mem_embedding)
                    matches.append({
                        "id": mem_id,
                        "text": text_summary,
                        "relevance": similarity,
                        "metadata": {}
                    })
            
            # Sort by relevance and take top_k
            matches.sort(key=lambda x: x["relevance"], reverse=True)
            matches = matches[:top_k]
            
            logger.debug(f"Found {len(matches)} episodic memories (Python fallback)")
            return matches
    
    async def search_conversation(
        self,
        user_id: int,
        session_id: str,
        embedding: List[float],
        top_k: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Search conversation history by semantic similarity.
        
        Args:
            user_id: User ID for scoping
            session_id: Session/chat ID
            embedding: Query embedding vector
            top_k: Number of results to return
            
        Returns:
            List of matching conversation turns
        """
        if not self._init_done:
            await self.initialize()
        
        embedding_json = json.dumps(embedding)
        
        # Fetch conversation records for session and compute similarity in Python
        stmt = text(
            """
            SELECT id, turns_json, embedding
            FROM conversation_memory
            WHERE user_id = :user_id AND session_id = :session_id AND embedding IS NOT NULL
            LIMIT 100
            """
        )
        
        result = await self.db_session.execute(
            stmt,
            {"user_id": user_id, "session_id": session_id}
        )
        rows = result.fetchall()
        
        matches = []
        for row in rows:
            session_id_db, turns_json, emb_json = row
            if emb_json:
                session_embedding = json.loads(emb_json)
                similarity = self._cosine_similarity(embedding, session_embedding)
                
                # Extract latest turn text from turns_json
                turns = turns_json if isinstance(turns_json, list) else []
                latest_turn_text = turns[-1].get("assistant", "") if turns else ""
                
                matches.append({
                    "turn_id": session_id_db,
                    "text": latest_turn_text,
                    "relevance": similarity,
                    "metadata": {}
                })
        
        # Sort by relevance and take top_k
        matches.sort(key=lambda x: x["relevance"], reverse=True)
        matches = matches[:top_k]
        
        logger.debug(f"Found {len(matches)} conversation turns for session {session_id}")
        return matches
    
    async def delete_user_memories(self, user_id: int) -> None:
        """
        Delete all memories for a user (GDPR cleanup, testing).
        
        Args:
            user_id: User ID to delete
        """
        if not self._init_done:
            await self.initialize()
        
        stmt = text(
            """
            DELETE FROM episodic_memory WHERE user_id = :user_id;
            DELETE FROM conversation_memory WHERE user_id = :user_id;
            """
        )
        
        await self.db_session.execute(stmt, {"user_id": user_id})
        await self.db_session.commit()
        logger.info(f"Deleted all memories for user {user_id}")



# ─── INTEGRATED MEMORY SYSTEM ─────────────────────────────────────────────────

class MemorySystem:
    """
    High-level API combining embeddings + vector store.
    Provides simple interface for storing and retrieving memories.
    Uses PostgreSQL with pgvector (or JSONB fallback) for vector search.
    """
    
    def __init__(
        self,
        db_session: AsyncSession,
        embedding_model: str = "all-MiniLM-L6-v2",
        use_pgvector: bool = True
    ):
        """
        Initialize memory system.
        
        Args:
            db_session: Active AsyncSession connection
            embedding_model: Sentence transformer model to use
            use_pgvector: Whether to use pgvector extension if available
        """
        self.embeddings = EmbeddingEngine(embedding_model)
        self.vector_store = VectorMemoryStore(db_session, use_pgvector)
        self.db_session = db_session
        self._init_done = False
    
    async def initialize(self) -> None:
        """Initialize both embeddings and vector store."""
        if self._init_done:
            return
        
        await self.embeddings.initialize()
        await self.vector_store.initialize()
        self._init_done = True
        logger.info("✓ Memory system fully initialized (PostgreSQL + pgvector)")
    
    async def remember_event(
        self,
        user_id: int,
        memory_id: str,
        summary: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Store an episodic memory event.
        
        Args:
            user_id: User ID
            memory_id: Unique memory ID
            summary: Event summary for embedding
            metadata: Additional context
        """
        if not self._init_done:
            await self.initialize()
        
        embedding = await self.embeddings.embed(summary)
        await self.vector_store.store_episodic(
            user_id, memory_id, summary, embedding, metadata
        )
    
    async def recall_events(
        self,
        user_id: int,
        query: str,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant episodic memories.
        
        Args:
            user_id: User ID
            query: Search query
            top_k: Number of results
            
        Returns:
            List of relevant memories
        """
        if not self._init_done:
            await self.initialize()
        
        embedding = await self.embeddings.embed(query)
        return await self.vector_store.search_episodic(user_id, query, embedding, top_k)
    
    async def remember_conversation(
        self,
        user_id: int,
        session_id: str,
        turn_id: str,
        user_text: str,
        assistant_text: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Store a conversation turn with embeddings.
        
        Args:
            user_id: User ID
            session_id: Conversation session ID
            turn_id: Turn identifier
            user_text: User's message
            assistant_text: Assistant's response
            metadata: Additional context
        """
        if not self._init_done:
            await self.initialize()
        
        meta = metadata or {}
        meta["session_id"] = session_id
        
        embeddings = await self.embeddings.embed_batch([user_text, assistant_text])
        await self.vector_store.store_conversation(
            user_id, turn_id, user_text, assistant_text, tuple(embeddings), meta
        )
    
    async def recall_conversation(
        self,
        user_id: int,
        session_id: str,
        query: str,
        top_k: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Search conversation history for context.
        
        Args:
            user_id: User ID
            session_id: Session ID
            query: Search query
            top_k: Number of results
            
        Returns:
            List of relevant conversation turns
        """
        if not self._init_done:
            await self.initialize()
        
        embedding = await self.embeddings.embed(query)
        return await self.vector_store.search_conversation(
            user_id, session_id, embedding, top_k
        )
        
        embedding = await self.embeddings.embed(query)
        return await self.vector_store.search_conversation(
            user_id, session_id, embedding, top_k
        )
