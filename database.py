"""
Database module using asyncpg for PostgreSQL/Neon.
"""

import asyncpg
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger("ruhi.database")

_pool: Optional[asyncpg.Pool] = None


async def init_db():
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
            # Each table in its own execute — asyncpg + Neon multi-statement issue fix
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    group_id BIGINT PRIMARY KEY,
                    group_name TEXT,
                    initialized BOOLEAN DEFAULT FALSE,
                    init_timestamp TIMESTAMPTZ,
                    last_activity TIMESTAMPTZ DEFAULT NOW(),
                    message_count INTEGER DEFAULT 0
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id BIGSERIAL PRIMARY KEY,
                    group_id BIGINT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    user_name TEXT,
                    text TEXT,
                    timestamp TIMESTAMPTZ NOT NULL,
                    is_self BOOLEAN DEFAULT FALSE,
                    reply_to_msg_id BIGINT
                )
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
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_group_time
                ON messages(group_id, timestamp DESC)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_group_user
                ON messages(group_id, user_id)
            """)

            logger.info("All database tables created/verified successfully.")

    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        logger.info("Database connection pool closed.")


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        await init_db()
    return _pool


async def is_group_initialized(group_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT initialized FROM groups WHERE group_id = $1", group_id
        )
        return row is not None and row["initialized"]


async def mark_group_initialized(group_id: int, group_name: str):
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO groups (group_id, group_name, last_activity)
            VALUES ($1, $2, NOW())
            ON CONFLICT (group_id) DO UPDATE SET
                group_name = $2,
                last_activity = NOW()
        """, group_id, group_name)


async def store_message(
    group_id: int,
    user_id: int,
    user_name: str,
    text: str,
    timestamp: datetime,
    is_self: bool = False,
    reply_to_msg_id: int = None
):
    if not text:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO messages (group_id, user_id, user_name, text, timestamp, is_self, reply_to_msg_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, group_id, user_id, user_name, text, timestamp, is_self, reply_to_msg_id)
        await conn.execute("""
            UPDATE groups SET last_activity = NOW(), message_count = message_count + 1
            WHERE group_id = $1
        """, group_id)


async def store_messages_bulk(messages: list):
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_name, text, is_self, timestamp
            FROM messages
            WHERE group_id = $1 AND text IS NOT NULL AND text != ''
            ORDER BY timestamp DESC
            LIMIT $2
        """, group_id, limit)
        return list(reversed(rows))


async def get_last_self_message_time(group_id: int) -> Optional[datetime]:
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


async def get_user_profile(user_id: int, group_id: int) -> Optional[dict]:
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


async def get_bot_state(key: str) -> Optional[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM bot_state WHERE key = $1", key
        )
        return row["value"] if row else None


async def set_bot_state(key: str, value: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO bot_state (key, value, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()
        """, key, value)
