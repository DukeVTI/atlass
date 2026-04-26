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
    
    def __init__(self, use_pgvector: bool = True):
        """
        Initialize PostgreSQL vector store.
        
        Args:
            use_pgvector: Whether to use pgvector (True) or JSONB fallback (False)
        """
        self.use_pgvector = use_pgvector
        self._init_done = False
        logger.info(f"VectorMemoryStore initialized (pgvector={use_pgvector})")
    
    async def initialize(self, db_session: AsyncSession) -> None:
        """Check pgvector extension availability and configure accordingly."""
        if self._init_done:
            return
        
        try:
            # Try to use pgvector if available
            if self.use_pgvector:
                result = await db_session.execute(
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
        db_session: AsyncSession,
        user_id: int,
        memory_id: str,
        text_summary: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Store episodic memory with embedding in PostgreSQL.
        """
        if not self._init_done:
            await self.initialize(db_session)
        
        embedding_json = json.dumps(embedding)
        
        stmt = text(
            """
            UPDATE episodic_memory 
            SET embedding = :embedding_json
            WHERE id = :memory_id AND user_id = :user_id
            """
        )
        
        await db_session.execute(
            stmt,
            {"embedding_json": embedding_json, "memory_id": memory_id, "user_id": user_id}
        )
        await db_session.commit()
    
    async def store_conversation(
        self,
        db_session: AsyncSession,
        user_id: int,
        turn_id: str,
        user_text: str,
        assistant_text: str,
        embeddings: tuple,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Store conversation turn with embeddings.
        """
        if not self._init_done:
            await self.initialize(db_session)
        
        assistant_embedding = embeddings[1] if len(embeddings) > 1 else embeddings[0]
        embedding_json = json.dumps(assistant_embedding)
        
        stmt = text(
            """
            UPDATE conversation_memory 
            SET embedding = :embedding_json
            WHERE id = :turn_id AND user_id = :user_id
            """
        )
        
        await db_session.execute(
            stmt,
            {"embedding_json": embedding_json, "turn_id": turn_id, "user_id": user_id}
        )
        await db_session.commit()
    
    async def search_episodic(
        self,
        db_session: AsyncSession,
        user_id: int,
        query: str,
        embedding: List[float],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search episodic memories by semantic similarity using PostgreSQL.
        """
        if not self._init_done:
            await self.initialize(db_session)
        
        embedding_json = json.dumps(embedding)
        
        # JSONB cosine similarity search
        # Uses SQL function to calculate similarity for all rows, then sorts by relevance
        stmt = text(
            """
            SELECT 
                id,
                summary,
                embedding,
                (1.0 - (CAST(embedding AS text)::vector <=> CAST(:embedding AS text)::vector)) AS relevance
            FROM episodic_memory
            WHERE user_id = :user_id AND embedding IS NOT NULL
            ORDER BY relevance DESC
            LIMIT :top_k
            """
        )
        
        try:
            result = await db_session.execute(
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
            
            result = await db_session.execute(
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
        db_session: AsyncSession,
        user_id: int,
        session_id: str,
        embedding: List[float],
        top_k: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Search conversation history by semantic similarity.
        """
        if not self._init_done:
            await self.initialize(db_session)
        
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
        
        result = await db_session.execute(
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
    
    async def delete_user_memories(self, db_session: AsyncSession, user_id: int) -> None:
        """
        Delete all memories for a user.
        """
        if not self._init_done:
            await self.initialize(db_session)
        
        stmt = text(
            """
            DELETE FROM episodic_memory WHERE user_id = :user_id;
            DELETE FROM conversation_memory WHERE user_id = :user_id;
            """
        )
        
        await db_session.execute(stmt, {"user_id": user_id})
        await db_session.commit()
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
        embedding_model: str = "all-MiniLM-L6-v2",
        use_pgvector: bool = True
    ):
        """
        Initialize memory system.
        """
        self.embeddings = EmbeddingEngine(embedding_model)
        self.vector_store = VectorMemoryStore(use_pgvector)
        self._init_done = False
    
    async def initialize(self, db_session: AsyncSession) -> None:
        """Initialize both embeddings and vector store."""
        if self._init_done:
            return
        
        await self.embeddings.initialize()
        await self.vector_store.initialize(db_session)
        self._init_done = True
        logger.info("✓ Memory system fully initialized")
    
    async def remember_event(
        self,
        db_session: AsyncSession,
        user_id: int,
        memory_id: str,
        summary: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Store an episodic memory event.
        """
        if not self._init_done:
            await self.initialize(db_session)
        
        embedding = await self.embeddings.embed(summary)
        await self.vector_store.store_episodic(
            db_session, user_id, memory_id, summary, embedding, metadata
        )
    
    async def recall_events(
        self,
        db_session: AsyncSession,
        user_id: int,
        query: str,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant episodic memories.
        """
        if not self._init_done:
            await self.initialize(db_session)
        
        embedding = await self.embeddings.embed(query)
        return await self.vector_store.search_episodic(db_session, user_id, query, embedding, top_k)
    
    async def remember_conversation(
        self,
        db_session: AsyncSession,
        user_id: int,
        session_id: str,
        turn_id: str,
        user_text: str,
        assistant_text: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Store a conversation turn with embeddings.
        """
        if not self._init_done:
            await self.initialize(db_session)
        
        meta = metadata or {}
        meta["session_id"] = session_id
        
        embeddings = await self.embeddings.embed_batch([user_text, assistant_text])
        await self.vector_store.store_conversation(
            db_session, user_id, turn_id, user_text, assistant_text, tuple(embeddings), meta
        )
    
    async def recall_conversation(
        self,
        db_session: AsyncSession,
        user_id: int,
        session_id: str,
        query: str,
        top_k: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Search conversation history for context.
        """
        if not self._init_done:
            await self.initialize(db_session)
        
        embedding = await self.embeddings.embed(query)
        return await self.vector_store.search_conversation(
            db_session, user_id, session_id, embedding, top_k
        )
