"""
AI Engine module for Ruhi UserBot.

All LLM calls are now routed to the local Puter.js microservice (puter_server.js),
which provides keyless, unlimited access to Llama-3.3-70b via Puter.js.
Groq API / OpenAI client have been removed entirely.
"""

import logging
import asyncio
from typing import Optional

import aiohttp

import config

logger = logging.getLogger("ruhi.ai")


async def call_llm(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 300,
) -> str:
    """
    Send a prompt to the local Puter.js microservice and return the AI reply.
    Returns "NO_REPLY" on any error so callers can handle it gracefully.
    """
    payload = {
        "system_prompt": system_prompt,
        "user_message": user_message,
        "max_tokens": max_tokens,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                config.PUTER_LOCAL_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(
                        f"Puter microservice returned HTTP {resp.status}: {body[:200]}"
                    )
                    return "NO_REPLY"

                data = await resp.json()
                reply = data.get("reply", "NO_REPLY")
                return reply if reply else "NO_REPLY"

    except aiohttp.ClientConnectorError:
        logger.error(
            "Cannot connect to Puter microservice at %s. "
            "Make sure puter_server.js is running.",
            config.PUTER_LOCAL_URL,
        )
        return "NO_REPLY"
    except asyncio.TimeoutError:
        logger.error("Puter microservice timed out after 60 s.")
        return "NO_REPLY"
    except Exception as exc:
        logger.error("Unexpected error calling Puter microservice: %s", exc)
        return "NO_REPLY"


def build_context_prompt(
    group_name: str,
    recent_messages: list,
    current_speaker: str,
    current_message: str,
    user_profile_summary: str,
    is_decide_mode: bool = True,
    ignore_detected: bool = False,
    self_name: str = "Ruhi",
) -> str:
    history_lines = []
    for msg in recent_messages:
        name = msg["user_name"] or "Unknown"
        text = msg["text"] or ""
        if msg["is_self"]:
            name = self_name
        if text:
            history_lines.append(f"[{name}]: {text}")

    history_str = (
        "\n".join(history_lines[-config.CONTEXT_WINDOW_SIZE:])
        if history_lines
        else "(No recent history)"
    )

    parts = []
    if is_decide_mode:
        parts.append("[DECIDE_REPLY]")
    parts.append(f"[Chat: {group_name}]")
    parts.append(f"\n[Recent Chat History]:\n{history_str}")
    if user_profile_summary:
        parts.append(f"\n[Relationship with {current_speaker}]: {user_profile_summary}")
    if ignore_detected:
        parts.append(
            "\n[SYSTEM NOTE: You (Ruhi) spoke recently but everyone is ignoring you. React naturally.]"
        )
    parts.append(f"\n[Speaker: {current_speaker}]: {current_message}")

    return "\n".join(parts)


async def decide_and_generate_reply(
    group_name: str,
    recent_messages: list,
    current_speaker: str,
    current_message: str,
    user_profile_summary: str,
    ignore_detected: bool = False,
    self_name: str = "Ruhi",
) -> Optional[str]:
    user_prompt = build_context_prompt(
        group_name=group_name,
        recent_messages=recent_messages,
        current_speaker=current_speaker,
        current_message=current_message,
        user_profile_summary=user_profile_summary,
        is_decide_mode=True,
        ignore_detected=ignore_detected,
        self_name=self_name,
    )

    response = await call_llm(
        system_prompt=config.USER_PERSONALITY_PROMPT,
        user_message=user_prompt,
        max_tokens=300,
    )

    if not response or response.strip().upper() == "NO_REPLY":
        return None

    response = response.strip()

    for prefix in ["[Ruhi]:", "Ruhi:", "[Reply]:", "Reply:"]:
        if response.lower().startswith(prefix.lower()):
            response = response[len(prefix):].strip()

    if (response.startswith('"') and response.endswith('"')) or (
        response.startswith("'") and response.endswith("'")
    ):
        response = response[1:-1].strip()

    if not response or response.upper() == "NO_REPLY":
        return None

    logger.info(f"AI reply for {current_speaker} in {group_name}: {response[:80]}...")
    return response


async def generate_ignore_reaction(
    group_name: str,
    recent_messages: list,
    self_name: str = "Ruhi",
) -> Optional[str]:
    user_prompt = build_context_prompt(
        group_name=group_name,
        recent_messages=recent_messages,
        current_speaker="SYSTEM",
        current_message="Everyone is ignoring your messages. Express your feelings.",
        user_profile_summary="",
        is_decide_mode=False,
        ignore_detected=True,
        self_name=self_name,
    )

    response = await call_llm(
        system_prompt=config.USER_PERSONALITY_PROMPT,
        user_message=user_prompt,
        max_tokens=150,
    )

    if not response or response.strip().upper() == "NO_REPLY":
        return None
    return response.strip()


async def generate_relationship_update(
    user_name: str,
    recent_exchanges: str,
    current_notes: str,
) -> str:
    system = (
        "You are an internal note-taker. Based on recent chat exchanges, write a BRIEF (1-2 sentence) "
        "relationship note about how this person interacts with Ruhi. Note their tone, if they're friendly/rude/funny/flirty.\n"
        "Also suggest a tone_preference from: friendly, sarcastic, cold, playful, respectful, annoyed.\n"
        "Format: NOTES: <notes> | TONE: <tone>"
    )

    user_msg = f"""User: {user_name}
Previous notes: {current_notes or 'None'}
Recent exchanges:
{recent_exchanges}"""

    return await call_llm(system, user_msg, max_tokens=80)
