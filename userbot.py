"""
Core UserBot module - Telegram event handling, message processing, and human mimicry.
"""

import asyncio
import random
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.messages import SetTypingRequest, ReadHistoryRequest
from telethon.tl.types import (
    SendMessageTypingAction,
    SendMessageCancelAction,
    Channel,
    Chat,
    User,
    PeerChannel,
    PeerChat,
)

import config
import database as db
import ai_engine

logger = logging.getLogger("ruhi.userbot")


class RuhiUserBot:
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.me = None
        self.my_id: int = 0
        self.my_name: str = "Ruhi"
        self._initializing_groups = set()
        self._ignore_cooldowns = {}
        self._reply_locks = {}
        self._running = False

    async def start(self):
        logger.info("Starting Ruhi UserBot...")
        await db.init_db()

        self.client = TelegramClient(
            StringSession(config.SESSION_STRING),
            config.API_ID,
            config.API_HASH,
            connection_retries=5,
            retry_delay=3,
        )

        await self.client.start()

        self.me = await self.client.get_me()
        self.my_id = self.me.id
        self.my_name = self.me.first_name or "Ruhi"

        logger.info(f"Logged in as: {self.me.first_name} (ID: {self.my_id})")

        self.client.add_event_handler(
            self._handle_new_message,
            events.NewMessage(incoming=True)
        )

        self._running = True

        asyncio.create_task(self._initialize_all_groups())
        asyncio.create_task(self._ignore_detection_loop())
        asyncio.create_task(self._periodic_prune())

        logger.info("Ruhi UserBot is now running!")

    async def stop(self):
        self._running = False
        if self.client:
            await self.client.disconnect()
        await db.close_db()
        logger.info("Ruhi UserBot stopped.")

    # --- Initialization ---

    async def _initialize_all_groups(self):
        await asyncio.sleep(3)
        try:
            dialogs = await self.client.get_dialogs(limit=None)
            groups = [d for d in dialogs if d.is_group or d.is_channel]
            logger.info(f"Found {len(groups)} groups/channels to check.")

            for dialog in groups:
                group_id = dialog.entity.id
                group_name = dialog.name or f"Group_{group_id}"
                try:
                    await db.register_group(group_id, group_name)
                    if not await db.is_group_initialized(group_id):
                        logger.info(f"Initializing group: {group_name} ({group_id})")
                        await self._scrape_group_history(dialog.entity, group_id, group_name)
                    else:
                        logger.info(f"Group already initialized: {group_name}")
                except Exception as e:
                    logger.error(f"Error initializing group {group_name}: {e}")
                await asyncio.sleep(1)

            logger.info("All groups initialization check complete.")
        except Exception as e:
            logger.error(f"Error during group initialization: {e}")

    async def _scrape_group_history(self, entity, group_id: int, group_name: str):
        if group_id in self._initializing_groups:
            return
        self._initializing_groups.add(group_id)
        try:
            messages_data = []
            count = 0
            async for message in self.client.iter_messages(entity, limit=config.INIT_MESSAGE_COUNT):
                if message.text:
                    sender_name = "Unknown"
                    sender_id = 0
                    is_self = False
                    if message.sender:
                        sender_id = message.sender_id or 0
                        is_self = (sender_id == self.my_id)
                        if isinstance(message.sender, User):
                            sender_name = message.sender.first_name or "Unknown"
                            if message.sender.last_name:
                                sender_name += f" {message.sender.last_name}"
                        else:
                            sender_name = getattr(message.sender, 'title', 'Unknown')
                    timestamp = message.date
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    messages_data.append((
                        group_id, sender_id, sender_name,
                        message.text[:4000], timestamp, is_self,
                        message.reply_to_msg_id if message.reply_to else None,
                    ))
                    count += 1
                if count % 100 == 0:
                    await asyncio.sleep(0.5)

            if messages_data:
                await db.store_messages_bulk(messages_data)
                logger.info(f"Stored {len(messages_data)} messages from {group_name}")
            await db.mark_group_initialized(group_id, group_name)
            logger.info(f"Group {group_name} fully initialized with {count} messages.")
        except Exception as e:
            logger.error(f"Error scraping {group_name}: {e}")
        finally:
            self._initializing_groups.discard(group_id)

    # --- Message Handler ---

    async def _handle_new_message(self, event):
        try:
            if not event.is_group:
                return
            message = event.message
            if not message.text:
                return

            chat = await event.get_chat()
            group_id = chat.id
            group_name = getattr(chat, 'title', f'Group_{group_id}')

            if group_id in self._initializing_groups:
                return

            if not await db.is_group_initialized(group_id):
                asyncio.create_task(self._scrape_group_history(chat, group_id, group_name))
                return

            sender = await event.get_sender()
            if not sender:
                return

            sender_id = sender.id
            is_self = (sender_id == self.my_id)

            sender_name = "Unknown"
            if isinstance(sender, User):
                sender_name = sender.first_name or "Unknown"
                if sender.last_name:
                    sender_name += f" {sender.last_name}"
            else:
                sender_name = getattr(sender, 'title', 'Unknown')

            timestamp = message.date
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            await db.store_message(
                group_id=group_id,
                user_id=sender_id,
                user_name=sender_name,
                text=message.text[:4000],
                timestamp=timestamp,
                is_self=is_self,
                reply_to_msg_id=message.reply_to_msg_id if message.reply_to else None,
            )

            if is_self:
                return

            if group_id not in self._reply_locks:
                self._reply_locks[group_id] = asyncio.Lock()

            async with self._reply_locks[group_id]:
                await self._process_and_maybe_reply(
                    event, group_id, group_name, sender_id, sender_name, message.text
                )

        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)

    async def _process_and_maybe_reply(
        self, event, group_id: int, group_name: str,
        sender_id: int, sender_name: str, message_text: str
    ):
        directly_addressed = self._is_directly_addressed(message_text)

        replying_to_self = False
        if event.message.reply_to:
            try:
                replied_msg = await event.message.get_reply_message()
                if replied_msg and replied_msg.sender_id == self.my_id:
                    replying_to_self = True
            except Exception:
                pass

        ignore_detected = await self._check_if_ignored(group_id)
        recent_messages = await db.get_recent_messages(group_id, config.CONTEXT_WINDOW_SIZE)
        user_summary = await db.get_user_interaction_summary(sender_id, group_id)

        if directly_addressed or replying_to_self:
            logger.info(f"Directly addressed by {sender_name} in {group_name}")

        reply = await ai_engine.decide_and_generate_reply(
            group_name=group_name,
            recent_messages=recent_messages,
            current_speaker=sender_name,
            current_message=message_text,
            user_profile_summary=user_summary,
            ignore_detected=ignore_detected,
            self_name=self.my_name,
        )

        if reply:
            await self._simulate_human_behavior(event, reply, group_id)
            await event.reply(reply)

            now = datetime.now(timezone.utc)
            await db.store_message(
                group_id=group_id,
                user_id=self.my_id,
                user_name=self.my_name,
                text=reply,
                timestamp=now,
                is_self=True,
            )

            await db.update_user_profile(
                user_id=sender_id,
                group_id=group_id,
                user_name=sender_name,
            )

            profile = await db.get_user_profile(sender_id, group_id)
            if profile and profile["interaction_count"] % 10 == 0:
                asyncio.create_task(
                    self._update_relationship_notes(sender_id, group_id, sender_name)
                )

            logger.info(f"Replied to {sender_name} in {group_name}: {reply[:60]}...")

    def _is_directly_addressed(self, text: str) -> bool:
        text_lower = text.lower()
        triggers = [
            "ruhi", "रूही", "roohi", "zoya",
            "@ruhi", "ruhi?", "ruhi!", "ruhi,",
            "ruhi bhai", "ruhi di", "ruhi sis",
        ]
        for trigger in triggers:
            if trigger in text_lower:
                return True
        return False

    async def _check_if_ignored(self, group_id: int) -> bool:
        try:
            last_self_time = await db.get_last_self_message_time(group_id)
            if not last_self_time:
                return False

            now = datetime.now(timezone.utc)
            if last_self_time.tzinfo is None:
                last_self_time = last_self_time.replace(tzinfo=timezone.utc)

            time_since = (now - last_self_time).total_seconds()

            if time_since < 30 or time_since > config.IGNORE_THRESHOLD_SECONDS:
                return False

            messages_after = await db.get_messages_after_self(group_id, config.IGNORE_CHECK_MESSAGES)

            if len(messages_after) >= 4:
                any_addressed = any(
                    self._is_directly_addressed(msg["text"] or "") for msg in messages_after
                )
                if not any_addressed:
                    return True

            return False
        except Exception as e:
            logger.error(f"Error checking ignore status: {e}")
            return False

    # --- Human Mimicry ---

    async def _simulate_human_behavior(self, event, reply_text: str, group_id: int):
        try:
            chat = await event.get_chat()

            read_delay = random.uniform(config.MIN_REPLY_DELAY, config.MAX_REPLY_DELAY)
            await asyncio.sleep(read_delay)

            try:
                await self.client(ReadHistoryRequest(
                    peer=chat,
                    max_id=event.message.id
                ))
            except Exception:
                pass

            await asyncio.sleep(random.uniform(0.3, 1.0))

            typing_duration = len(reply_text) / config.TYPING_SPEED_CPS
            typing_duration = max(1.0, min(typing_duration, 15.0))
            typing_duration *= random.uniform(0.8, 1.3)

            try:
                await self.client(SetTypingRequest(
                    peer=chat,
                    action=SendMessageTypingAction()
                ))
            except Exception:
                pass

            await asyncio.sleep(typing_duration)

            try:
                await self.client(SetTypingRequest(
                    peer=chat,
                    action=SendMessageCancelAction()
                ))
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error in human behavior simulation: {e}")
            await asyncio.sleep(random.uniform(2.0, 4.0))

    # --- Background Tasks ---

    async def _ignore_detection_loop(self):
        await asyncio.sleep(60)

        while self._running:
            try:
                pool = await db.get_pool()
                async with pool.acquire() as conn:
                    groups = await conn.fetch("""
                        SELECT group_id, group_name FROM groups
                        WHERE initialized = TRUE
                        AND last_activity > NOW() - INTERVAL '30 minutes'
                    """)

                for group in groups:
                    group_id = group["group_id"]
                    group_name = group["group_name"]

                    last_reaction = self._ignore_cooldowns.get(group_id, 0)
                    now_ts = datetime.now(timezone.utc).timestamp()
                    if now_ts - last_reaction < 600:
                        continue

                    if await self._check_if_ignored(group_id):
                        logger.info(f"Detected being ignored in {group_name}")

                        recent_messages = await db.get_recent_messages(group_id, 20)

                        reaction = await ai_engine.generate_ignore_reaction(
                            group_name=group_name,
                            recent_messages=recent_messages,
                            self_name=self.my_name,
                        )

                        if reaction:
                            try:
                                entity = await self.client.get_entity(group_id)
                                typing_time = len(reaction) / config.TYPING_SPEED_CPS
                                typing_time = max(1.0, min(typing_time, 10.0))

                                await self.client(SetTypingRequest(
                                    peer=entity,
                                    action=SendMessageTypingAction()
                                ))
                                await asyncio.sleep(typing_time)
                                await self.client.send_message(entity, reaction)

                                await db.store_message(
                                    group_id=group_id,
                                    user_id=self.my_id,
                                    user_name=self.my_name,
                                    text=reaction,
                                    timestamp=datetime.now(timezone.utc),
                                    is_self=True,
                                )

                                self._ignore_cooldowns[group_id] = now_ts
                                logger.info(f"Sent ignore reaction in {group_name}: {reaction[:60]}...")

                            except Exception as e:
                                logger.error(f"Error sending ignore reaction: {e}")

            except Exception as e:
                logger.error(f"Error in ignore detection loop: {e}")

            await asyncio.sleep(120)

    async def _periodic_prune(self):
        await asyncio.sleep(300)

        while self._running:
            try:
                pool = await db.get_pool()
                async with pool.acquire() as conn:
                    groups = await conn.fetch("SELECT group_id FROM groups WHERE initialized = TRUE")

                for group in groups:
                    await db.prune_old_messages(group["group_id"], config.MAX_STORED_MESSAGES)
                    await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error in periodic prune: {e}")

            await asyncio.sleep(3600)

    async def _update_relationship_notes(self, user_id: int, group_id: int, user_name: str):
        try:
            pool = await db.get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT user_name, text, is_self FROM messages
                    WHERE group_id = $1 AND (user_id = $2 OR is_self = TRUE)
                    ORDER BY timestamp DESC
                    LIMIT 20
                """, group_id, user_id)

            if len(rows) < 4:
                return

            exchanges = "\n".join([
                f"{'Ruhi' if r['is_self'] else r['user_name']}: {r['text']}"
                for r in reversed(rows) if r['text']
            ])

            profile = await db.get_user_profile(user_id, group_id)
            current_notes = profile["relationship_notes"] if profile else ""

            ai_response = await ai_engine.generate_relationship_update(
                user_name=user_name,
                recent_exchanges=exchanges,
                current_notes=current_notes,
            )

            if ai_response and "NOTES:" in ai_response:
                try:
                    notes_part = ai_response.split("NOTES:")[1]
                    if "TONE:" in notes_part:
                        notes = notes_part.split("TONE:")[0].strip().strip("|").strip()
                        tone = notes_part.split("TONE:")[1].strip().lower()

                        valid_tones = ["friendly", "sarcastic", "cold", "playful", "respectful", "annoyed"]
                        if tone not in valid_tones:
                            tone = "neutral"

                        await db.update_user_profile(
                            user_id=user_id,
                            group_id=group_id,
                            user_name=user_name,
                            relationship_notes=notes,
                            tone_preference=tone,
                        )
                        logger.info(f"Updated relationship notes for {user_name}: {notes[:50]}...")
                except Exception as e:
                    logger.error(f"Error parsing relationship update: {e}")

        except Exception as e:
            logger.error(f"Error updating relationship notes: {e}")


# Global instance
bot = RuhiUserBot()
