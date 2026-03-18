"""
Database module using asyncpg for PostgreSQL/Neon.
Handles all DB operations: schema creation, message storage, group tracking, user profiles.
"""

import asyncpg
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger("ruhi.database")

# Global connection pool
_pool: Optional[asyncpg.Pool] = None


async def init_db():
    """Initialize the database connection pool and create tables."""
    global _pool
    
    try:
        _pool = await asyncpg.create_pool(
            dsn=config.RAW_DATABASE_URL,
            min_size=2,
            max_size=10,
            ssl="require",
            command_timeout=30,
        )
        logger.info("Database connection pool created successfully.")
        
        async with _pool.acquire() as conn:
            # Create tables
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    group_id BIGINT PRIMARY KEY,
                    group_name TEXT,
                    initialized BOOLEAN DEFAULT FALSE,
                    init_timestamp TIMESTAMPTZ,
                    last_activity TIMESTAMPTZ DEFAULT NOW(),
                    message_count INTEGER DEFAULT 0
                );
            """)
            
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id BIGSERIAL PRIMARY KEY,
                    group_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    user_name TEXT,
                    text TEXT,
                    timestamp TIMESTAMPTZ NOT NULL,
                    is_self BOOLEAN DEFAULT FALSE,
                    reply_to_msg_id BIGINT,
                    CONSTRAINT fk_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
                );
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_group_time 
                ON messages(group_id, timestamp DESC);
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_group_user 
                ON messages(group_id, user_id);
            """)
            
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id BIGINT NOT NULL,
                    group_id BIGINT NOT NULL,
                    user_name TEXT,
                    relationship_notes TEXT DEFAULT '',
                    interaction_count INTEGER DEFAULT 0,
                    tone_preference TEXT DEFAULT 'neutral',
                    last_interaction TIMESTAMPTZ,
                    personality_tags JSONB DEFAULT '[]'::jsonb,
                    PRIMARY KEY (user_id, group_id)
                );
            """)
            
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            
            logger.info("All database tables created/verified successfully.")
            
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


async def close_db():
    """Close the database connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        logger.info("Database connection pool closed.")


async def get_pool() -> asyncpg.Pool:
    """Get the connection pool, initializing if needed."""
    global _pool
    if _pool is None:
        await init_db()
    return _pool


# --- Group Operations ---

async def is_group_initialized(group_id: int) -> bool:
    """Check if a group has been initialized (500 messages scraped)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT initialized FROM groups WHERE group_id = $1", group_id
        )
        return row is not None and row["initialized"]


async def mark_group_initialized(group_id: int, group_name: str):
    """Mark a group as initialized after scraping."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO groups (group_id, group_name, initialized, init_timestamp, last_activity)
            VALUES ($1, $2, TRUE, NOW(), NOW())
            ON CONFLICT (group_id) DO UPDATE SET 
                initialized = TRUE, 
                init_timestamp = NOW(),
                group_name = $2
        """, group_id, group_name)


async def register_group(group_id: int, group_name: str):
    """Register a group without marking as initialized."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO groups (group_id, group_name, last_activity)
            VALUES ($1, $2, NOW())
            ON CONFLICT (group_id) DO UPDATE SET 
                group_name = $2,
                last_activity = NOW()
        """, group_id, group_name)


# --- Message Operations ---

async def store_message(
    group_id: int,
    user_id: int,
    user_name: str,
    text: str,
    timestamp: datetime,
    is_self: bool = False,
    reply_to_msg_id: int = None
):
    """Store a single message in the database."""
    if not text:
        return
        
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO messages (group_id, user_id, user_name, text, timestamp, is_self, reply_to_msg_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, group_id, user_id, user_name, text, timestamp, is_self, reply_to_msg_id)
        
        # Update group activity
        await conn.execute("""
            UPDATE groups SET last_activity = NOW(), message_count = message_count + 1
            WHERE group_id = $1
        """, group_id)


async def store_messages_bulk(messages: list):
    """Store multiple messages at once (for initialization)."""
    if not messages:
        return
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO messages (group_id, user_id, user_name, text, timestamp, is_self, reply_to_msg_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT DO NOTHING
        """, messages)


async def get_recent_messages(group_id: int, limit: int = 40) -> list:
    """Get recent messages from a group for context."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_name, text, is_self, timestamp
            FROM messages
            WHERE group_id = $1 AND text IS NOT NULL AND text != ''
            ORDER BY timestamp DESC
            LIMIT $2
        """, group_id, limit)
        
        # Return in chronological order
        return list(reversed(rows))


async def get_last_self_message_time(group_id: int) -> Optional[datetime]:
    """Get the timestamp of the last message sent by self in a group."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT timestamp FROM messages
            WHERE group_id = $1 AND is_self = TRUE
            ORDER BY timestamp DESC
            LIMIT 1
        """, group_id)
        return row["timestamp"] if row else None


async def get_messages_after_self(group_id: int, limit: int = 10) -> list:
    """Get messages sent after the bot's last message (to detect being ignored)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        last_self = await conn.fetchrow("""
            SELECT timestamp FROM messages
            WHERE group_id = $1 AND is_self = TRUE
            ORDER BY timestamp DESC
            LIMIT 1
        """, group_id)
        
        if not last_self:
            return []
        
        rows = await conn.fetch("""
            SELECT user_name, text, is_self, timestamp
            FROM messages
            WHERE group_id = $1 AND timestamp > $2 AND is_self = FALSE
            ORDER BY timestamp ASC
            LIMIT $3
        """, group_id, last_self["timestamp"], limit)
        
        return list(rows)


async def prune_old_messages(group_id: int, keep_count: int = 2000):
    """Delete old messages to prevent database bloat."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM messages WHERE group_id = $1", group_id
        )
        if count > keep_count:
            await conn.execute("""
                DELETE FROM messages WHERE id IN (
                    SELECT id FROM messages
                    WHERE group_id = $1
                    ORDER BY timestamp ASC
                    LIMIT $2
                )
            """, group_id, count - keep_count)
            logger.info(f"Pruned {count - keep_count} old messages from group {group_id}")


# --- User Profile Operations ---

async def get_user_profile(user_id: int, group_id: int) -> Optional[dict]:
    """Get a user's profile/relationship data."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT * FROM user_profiles
            WHERE user_id = $1 AND group_id = $2
        """, user_id, group_id)
        return dict(row) if row else None


async def update_user_profile(
    user_id: int,
    group_id: int,
    user_name: str,
    relationship_notes: str = None,
    tone_preference: str = None,
    personality_tags: list = None
):
    """Create or update a user profile."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM user_profiles WHERE user_id = $1 AND group_id = $2",
            user_id, group_id
        )
        
        if existing:
            updates = []
            params = [user_id, group_id]
            param_idx = 3
            
            if relationship_notes is not None:
                updates.append(f"relationship_notes = ${param_idx}")
                params.append(relationship_notes)
                param_idx += 1
            
            if tone_preference is not None:
                updates.append(f"tone_preference = ${param_idx}")
                params.append(tone_preference)
                param_idx += 1
            
            if personality_tags is not None:
                updates.append(f"personality_tags = ${param_idx}::jsonb")
                params.append(json.dumps(personality_tags))
                param_idx += 1
            
            updates.append("interaction_count = interaction_count + 1")
            updates.append("last_interaction = NOW()")
            updates.append(f"user_name = ${param_idx}")
            params.append(user_name)
            
            if updates:
                query = f"UPDATE user_profiles SET {', '.join(updates)} WHERE user_id = $1 AND group_id = $2"
                await conn.execute(query, *params)
        else:
            await conn.execute("""
                INSERT INTO user_profiles (user_id, group_id, user_name, relationship_notes, 
                    interaction_count, tone_preference, last_interaction, personality_tags)
                VALUES ($1, $2, $3, $4, 1, $5, NOW(), $6::jsonb)
            """, user_id, group_id, user_name,
                relationship_notes or "",
                tone_preference or "neutral",
                json.dumps(personality_tags or []))


async def get_user_interaction_summary(user_id: int, group_id: int) -> str:
    """Get a summary of past interactions with a user for AI context."""
    profile = await get_user_profile(user_id, group_id)
    if not profile:
        return "New user - no previous interactions."
    
    summary_parts = [
        f"User: {profile['user_name']}",
        f"Interactions: {profile['interaction_count']}",
        f"Tone: {profile['tone_preference']}",
    ]
    
    if profile["relationship_notes"]:
        summary_parts.append(f"Notes: {profile['relationship_notes']}")
    
    tags = profile.get("personality_tags")
    if tags:
        if isinstance(tags, str):
            tags = json.loads(tags)
        if tags:
            summary_parts.append(f"Tags: {', '.join(tags)}")
    
    return " | ".join(summary_parts)


# --- Bot State Operations ---

async def get_bot_state(key: str) -> Optional[str]:
    """Get a stored bot state value."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM bot_state WHERE key = $1", key
        )
        return row["value"] if row else None


async def set_bot_state(key: str, value: str):
    """Set a bot state value."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO bot_state (key, value, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()
        """, key, value)
