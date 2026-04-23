"""
Atlas Embeddings & Vector Memory
---------------------------------
Handles embedding generation via sentence-transformers and ChromaDB integration.
Provides semantic similarity search for episodic and conversation memories.

Model: all-MiniLM-L6-v2 (33MB, CPU-safe, 384-dim embeddings)
Collections:
  - episodic_memories: Stores episodic events for semantic search
  - conversation_history: Stores conversation turns for session context
"""

import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

try:
    import chromadb
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False

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
    ChromaDB-backed vector memory for episodic and conversation memories.
    Provides semantic similarity search.
    """
    
    def __init__(self, chroma_host: str = "localhost", chroma_port: int = 8000):
        """
        Initialize ChromaDB client.
        
        Args:
            chroma_host: ChromaDB server hostname
            chroma_port: ChromaDB server port
        """
        if not CHROMADB_AVAILABLE:
            raise ImportError(
                "chromadb not installed. "
                "Install with: pip install chromadb"
            )
        
        self.chroma_host = chroma_host
        self.chroma_port = chroma_port
        self.client = None
        self.episodic_collection = None
        self.conversation_collection = None
        self._init_done = False
        logger.info(f"VectorMemoryStore initialized for {chroma_host}:{chroma_port}")
    
    async def initialize(self) -> None:
        """Connect to ChromaDB and initialize collections."""
        if self._init_done:
            return
        
        def _init():
            # Connect to ChromaDB via HTTP (new API - no Settings needed)
            client = chromadb.HttpClient(
                host=self.chroma_host,
                port=self.chroma_port
            )
            
            # Create/get collections with metadata
            episodic = client.get_or_create_collection(
                name="episodic_memories",
                metadata={
                    "description": "Episodic memory events with semantic search",
                    "embedding_model": "all-MiniLM-L6-v2"
                },
                distance_metric="cosine"
            )
            
            conversation = client.get_or_create_collection(
                name="conversation_history",
                metadata={
                    "description": "Conversation turns for session context",
                    "embedding_model": "all-MiniLM-L6-v2"
                },
                distance_metric="cosine"
            )
            
            return client, episodic, conversation
        
        client, episodic, conversation = await asyncio.get_event_loop().run_in_executor(
            None, _init
        )
        
        self.client = client
        self.episodic_collection = episodic
        self.conversation_collection = conversation
        self._init_done = True
        logger.info("✓ ChromaDB collections initialized")
    
    async def store_episodic(
        self,
        user_id: int,
        memory_id: str,
        text: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Store episodic memory with embedding in ChromaDB.
        
        Args:
            user_id: User ID for scoping
            memory_id: Unique memory identifier
            text: Summary text for search
            embedding: Pre-computed embedding vector
            metadata: Additional metadata to store
        """
        if not self._init_done:
            await self.initialize()
        
        meta = metadata or {}
        meta["user_id"] = str(user_id)
        meta["timestamp"] = datetime.utcnow().isoformat()
        
        def _store():
            self.episodic_collection.add(
                ids=[memory_id],
                embeddings=[embedding],
                documents=[text],
                metadatas=[meta]
            )
        
        await asyncio.get_event_loop().run_in_executor(None, _store)
        logger.debug(f"Stored episodic memory: {memory_id}")
    
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
        
        user_emb, assistant_emb = embeddings
        meta = metadata or {}
        meta["user_id"] = str(user_id)
        meta["timestamp"] = datetime.utcnow().isoformat()
        
        def _store():
            # Store both user and assistant messages
            self.conversation_collection.add(
                ids=[f"{turn_id}_user", f"{turn_id}_assistant"],
                embeddings=[user_emb, assistant_emb],
                documents=[user_text, assistant_text],
                metadatas=[meta, meta]
            )
        
        await asyncio.get_event_loop().run_in_executor(None, _store)
        logger.debug(f"Stored conversation turn: {turn_id}")
    
    async def search_episodic(
        self,
        user_id: int,
        query: str,
        embedding: List[float],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search episodic memories by semantic similarity.
        
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
        
        def _search():
            results = self.episodic_collection.query(
                query_embeddings=[embedding],
                n_results=top_k,
                where={"user_id": {"$eq": str(user_id)}}
            )
            
            # Reformat ChromaDB results
            matches = []
            if results and results["ids"] and results["ids"][0]:
                for i, mem_id in enumerate(results["ids"][0]):
                    matches.append({
                        "id": mem_id,
                        "text": results["documents"][0][i],
                        "relevance": 1 - (results["distances"][0][i] / 2),  # Convert distance to similarity
                        "metadata": results["metadatas"][0][i]
                    })
            return matches
        
        results = await asyncio.get_event_loop().run_in_executor(None, _search)
        logger.debug(f"Found {len(results)} episodic memories for user {user_id}")
        return results
    
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
        
        def _search():
            results = self.conversation_collection.query(
                query_embeddings=[embedding],
                n_results=top_k,
                where={
                    "$and": [
                        {"user_id": {"$eq": str(user_id)}},
                        {"session_id": {"$eq": session_id}}
                    ]
                }
            )
            
            matches = []
            if results and results["ids"] and results["ids"][0]:
                for i, turn_id in enumerate(results["ids"][0]):
                    matches.append({
                        "turn_id": turn_id,
                        "text": results["documents"][0][i],
                        "relevance": 1 - (results["distances"][0][i] / 2),
                        "metadata": results["metadatas"][0][i]
                    })
            return matches
        
        results = await asyncio.get_event_loop().run_in_executor(None, _search)
        logger.debug(f"Found {len(results)} conversation turns for session {session_id}")
        return results
    
    async def delete_user_memories(self, user_id: int) -> None:
        """
        Delete all memories for a user (GDPR cleanup, testing).
        
        Args:
            user_id: User ID to delete
        """
        if not self._init_done:
            await self.initialize()
        
        def _delete():
            self.episodic_collection.delete(
                where={"user_id": {"$eq": str(user_id)}}
            )
            self.conversation_collection.delete(
                where={"user_id": {"$eq": str(user_id)}}
            )
        
        await asyncio.get_event_loop().run_in_executor(None, _delete)
        logger.info(f"Deleted all memories for user {user_id}")


# ─── INTEGRATED MEMORY SYSTEM ─────────────────────────────────────────────────

class MemorySystem:
    """
    High-level API combining embeddings + vector store.
    Provides simple interface for storing and retrieving memories.
    """
    
    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        embedding_model: str = "all-MiniLM-L6-v2"
    ):
        """Initialize memory system."""
        self.embeddings = EmbeddingEngine(embedding_model)
        self.vector_store = VectorMemoryStore(chroma_host, chroma_port)
        self._init_done = False
    
    async def initialize(self) -> None:
        """Initialize both embeddings and vector store."""
        if self._init_done:
            return
        
        await self.embeddings.initialize()
        await self.vector_store.initialize()
        self._init_done = True
        logger.info("✓ Memory system fully initialized")
    
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
