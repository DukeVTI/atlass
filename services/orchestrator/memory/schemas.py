"""
Atlas Memory Database Schemas
------------------------------
SQLAlchemy async models for all memory tables.
Also includes migration functions to auto-create/upgrade schema on startup.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, 
    Boolean, JSON, ARRAY, ForeignKey, create_engine, 
    MetaData, Table, Index, select, text
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.dialects.postgresql import JSONB
from typing import Optional, List
import logging
import numpy as np

logger = logging.getLogger(__name__)

Base = declarative_base()


# ─── EPISODIC MEMORY TABLE ────────────────────────────────────────────────────

class EpisodicMemoryRecord(Base):
    """Database record for episodic memory."""
    
    __tablename__ = "episodic_memory"
    
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    
    event_type = Column(String(50), nullable=False)  # "tool_execution", "email", etc.
    summary = Column(String(500), nullable=False)
    full_context = Column(Text, nullable=False)
    
    timestamp = Column(DateTime, nullable=False, index=True)
    
    tags = Column(ARRAY(String), default=[])
    embedding_vector = Column(ARRAY(Float), nullable=True)  # JSONB fallback
    embedding = Column(JSONB, nullable=True)  # pgvector as JSONB (384-dim vector)
    
    source = Column(String(50), nullable=False)
    reference_id = Column(String(255), nullable=True)
    confidence = Column(Float, default=1.0)
    
    ttl_days = Column(Integer, default=90)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # For efficient expiry queries
    __table_args__ = (
        Index("idx_episodic_user_timestamp", "user_id", "timestamp"),
        Index("idx_episodic_created_expiry", "created_at"),
        Index("idx_episodic_tags", "tags", postgresql_using="gin"),
    )


class ToolCallMemoryRecord(Base):
    """Database record for tool execution memories (specialized episodic)."""
    
    __tablename__ = "tool_call_memory"
    
    id = Column(String(36), primary_key=True)
    episodic_id = Column(String(36), ForeignKey("episodic_memory.id"), nullable=True)
    user_id = Column(Integer, nullable=False, index=True)
    
    tool_name = Column(String(100), nullable=False)
    tool_input = Column(JSON, nullable=False)
    tool_output = Column(Text, nullable=False)
    execution_time_ms = Column(Integer, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index("idx_tool_calls_user_tool", "user_id", "tool_name"),
    )


class PaymentMemoryRecord(Base):
    """Database record for payment event memories."""
    
    __tablename__ = "payment_memory"
    
    id = Column(String(36), primary_key=True)
    episodic_id = Column(String(36), ForeignKey("episodic_memory.id"), nullable=True)
    user_id = Column(Integer, nullable=False, index=True)
    
    amount = Column(Float, nullable=False)
    currency = Column(String(3), default="NGN")
    customer_email = Column(String(255), nullable=False)
    reference = Column(String(100), nullable=False, index=True)
    transaction_type = Column(String(20), nullable=False)  # "charge", "transfer", "refund"
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index("idx_payments_user_date", "user_id", "created_at"),
    )


# ─── FACTUAL MEMORY TABLE ────────────────────────────────────────────────────

class FactualMemoryRecord(Base):
    """Database record for factual memory (contacts, projects, preferences)."""
    
    __tablename__ = "factual_memory"
    
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    
    key = Column(String(100), nullable=False)  # "contact:Dana", "project:FundMatch"
    category = Column(String(50), nullable=False)  # "contact", "project", "preference"
    value = Column(JSON, nullable=False)
    
    source = Column(String(100), nullable=True)
    verified = Column(Boolean, default=False)
    
    last_updated = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index("idx_factual_user_category", "user_id", "category"),
        Index("idx_factual_user_key", "user_id", "key"),
    )


class ContactMemoryRecord(Base):
    """Database record for contact information (specialized factual)."""
    
    __tablename__ = "contact_memory"
    
    id = Column(String(36), primary_key=True)
    factual_id = Column(String(36), ForeignKey("factual_memory.id"), nullable=True)
    user_id = Column(Integer, nullable=False, index=True)
    
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True, index=True)
    phone = Column(String(20), nullable=True)
    timezone = Column(String(50), nullable=True)
    communication_preference = Column(String(20), nullable=True)  # "email", "whatsapp"
    tags = Column(ARRAY(String), default=[])
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_contacted = Column(DateTime, nullable=True)
    
    __table_args__ = (
        Index("idx_contacts_user_email", "user_id", "email"),
        Index("idx_contacts_user_name", "user_id", "name"),
    )


class ProjectMemoryRecord(Base):
    """Database record for project information (specialized factual)."""
    
    __tablename__ = "project_memory"
    
    id = Column(String(36), primary_key=True)
    factual_id = Column(String(36), ForeignKey("factual_memory.id"), nullable=True)
    user_id = Column(Integer, nullable=False, index=True)
    
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="active")  # "active", "completed", "paused"
    client = Column(String(255), nullable=True)
    start_date = Column(DateTime, nullable=True)
    deadline = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index("idx_projects_user_status", "user_id", "status"),
    )


class PreferenceMemoryRecord(Base):
    """Database record for user preferences (specialized factual)."""
    
    __tablename__ = "preference_memory"
    
    id = Column(String(36), primary_key=True)
    factual_id = Column(String(36), ForeignKey("factual_memory.id"), nullable=True)
    user_id = Column(Integer, nullable=False, index=True)
    
    setting_name = Column(String(100), nullable=False)
    setting_value = Column(JSON, nullable=False)
    applies_to = Column(String(50), nullable=False)  # "global", "communication", "scheduling"
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index("idx_preferences_user_name", "user_id", "setting_name"),
    )


# ─── PROCEDURAL MEMORY TABLE ──────────────────────────────────────────────────

class ProceduralMemoryRecord(Base):
    """Database record for procedural memory (skills, workflows)."""
    
    __tablename__ = "procedural_memory"
    
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    
    skill_name = Column(String(100), nullable=False)
    skill_definition = Column(Text, nullable=False)  # YAML definition
    description = Column(String(500), nullable=True)
    
    tags = Column(ARRAY(String), default=[])
    usage_count = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used = Column(DateTime, nullable=True)
    
    __table_args__ = (
        Index("idx_procedural_user_skill", "user_id", "skill_name"),
    )


# ─── CONVERSATION MEMORY TABLE ────────────────────────────────────────────────

class ConversationMemoryRecord(Base):
    """Database record for conversation sessions."""
    
    __tablename__ = "conversation_memory"
    
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    session_id = Column(String(100), nullable=False)  # Telegram chat ID
    
    turns_json = Column(JSON, default=[])  # Array of turn objects
    embedding = Column(JSONB, nullable=True)  # Latest turn embedding for search
    
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_turn_at = Column(DateTime, default=datetime.utcnow)
    ttl_hours = Column(Integer, default=48)
    
    __table_args__ = (
        Index("idx_conversation_user_session", "user_id", "session_id"),
        Index("idx_conversation_expiry", "last_turn_at"),
    )


# ─── MIGRATION FUNCTIONS ──────────────────────────────────────────────────────

async def init_memory_schema(db_url: str) -> None:
    """
    Initialize memory database schema on startup.
    Creates all tables if they don't exist.
    Enables pgvector extension for native vector search.
    Uses separate transactions so pgvector failure doesn't block table creation.
    """
    try:
        engine = create_async_engine(db_url, echo=False)

        # Transaction 1: Try to enable pgvector — isolated so failure doesn't poison table creation
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                logger.info("✓ pgvector extension enabled")
        except Exception as e:
            logger.warning(f"pgvector extension not available, using JSONB fallback: {e}")
            # Transaction is automatically rolled back when the context manager exits on exception

        # Transaction 2: Create all tables — always runs regardless of pgvector outcome
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("✓ Memory schema initialized successfully")

        await engine.dispose()
    except Exception as e:
        logger.error(f"✗ Failed to initialize memory schema: {e}")
        raise


async def check_schema_health(session: AsyncSession) -> bool:
    """
    Verify that all memory tables exist and are accessible.
    
    Args:
        session: Active AsyncSession
        
    Returns:
        True if all tables are healthy, False otherwise
    """
    tables_to_check = [
        "episodic_memory",
        "factual_memory",
        "procedural_memory",
        "conversation_memory",
    ]
    
    try:
        for table_name in tables_to_check:
            result = await session.execute(
                select(1).select_from(
                    Base.metadata.tables[table_name]
                ).limit(1)
            )
            result.first()  # Just verify table exists
        
        logger.debug("✓ Memory schema health check passed")
        return True
    except Exception as e:
        logger.warning(f"✗ Memory schema health check failed: {e}")
        return False


async def cleanup_expired_memories(session: AsyncSession) -> int:
    """
    Remove expired episodic and conversation memories.
    Should be called periodically (e.g., via Celery Beat, v0.4+).
    
    Args:
        session: Active AsyncSession
        
    Returns:
        Number of records deleted
    """
    from datetime import datetime, timedelta
    from sqlalchemy import delete
    
    try:
        # Delete expired episodic memories
        now = datetime.utcnow()
        cutoff = now - timedelta(days=90)
        
        delete_episodic = delete(EpisodicMemoryRecord).where(
            EpisodicMemoryRecord.created_at < cutoff
        )
        result1 = await session.execute(delete_episodic)
        
        # Delete expired conversation memories
        cutoff_conv = now - timedelta(hours=48)
        delete_conversation = delete(ConversationMemoryRecord).where(
            ConversationMemoryRecord.last_turn_at < cutoff_conv
        )
        result2 = await session.execute(delete_conversation)
        
        await session.commit()
        
        total_deleted = result1.rowcount + result2.rowcount
        logger.info(f"✓ Cleaned up {total_deleted} expired memories")
        return total_deleted
    except Exception as e:
        await session.rollback()
        logger.error(f"✗ Failed to cleanup expired memories: {e}")
        return 0
