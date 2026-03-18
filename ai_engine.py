"""
AI Engine module for Ruhi UserBot.
"""

import logging
import asyncio
import time
from typing import Optional
from openai import OpenAI

import config

logger = logging.getLogger("ruhi.ai")

client = OpenAI(
    base_url=config.AI_BASE_URL,
    api_key=config.HF_TOKEN,
)


def _call_llm_sync(system_prompt: str, user_message: str, max_tokens: int = 300) -> str:
    """Synchronous LLM call with retry on rate limit."""
    for attempt in range(3):
        try:
            completion = client.chat.completions.create(
                model=config.AI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=max_tokens,
                temperature=0.85,
                top_p=0.9,
            )
            return completion.choices[0].message.content.strip()
        except Exception as e:
            err = str(e).lower()
            if "rate limit" in err or "429" in err or "too many" in err:
                wait = (attempt + 1) * 10
                logger.warning(f"Rate limit hit, waiting {wait}s (attempt {attempt+1}/3)")
                time.sleep(wait)
            else:
                logger.error(f"LLM API call failed: {e}")
                return "NO_REPLY"
    logger.error("LLM failed after 3 retries — rate limit")
    return "NO_REPLY"


async def call_llm(system_prompt: str, user_message: str, max_tokens: int = 300) -> str:
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _call_llm_sync, system_prompt, user_message, max_tokens)
    return result


def build_context_prompt(
    group_name: str,
    recent_messages: list,
    current_speaker: str,
    current_message: str,
    user_profile_summary: str,
    is_decide_mode: bool = True,
    ignore_detected: bool = False,
    self_name: str = "Ruhi"
) -> str:
    history_lines = []
    for msg in recent_messages:
        name = msg["user_name"] or "Unknown"
        text = msg["text"] or ""
        if msg["is_self"]:
            name = self_name
        if text:
            history_lines.append(f"[{name}]: {text}")

    history_str = "\n".join(history_lines[-config.CONTEXT_WINDOW_SIZE:]) if history_lines else "(No recent history)"

    parts = []
    if is_decide_mode:
        parts.append("[DECIDE_REPLY]")
    parts.append(f"[Chat: {group_name}]")
    parts.append(f"\n[Recent Chat History]:\n{history_str}")
    if user_profile_summary:
        parts.append(f"\n[Relationship with {current_speaker}]: {user_profile_summary}")
    if ignore_detected:
        parts.append(f"\n[SYSTEM NOTE: You (Ruhi) spoke recently but everyone is ignoring you. React naturally.]")
    parts.append(f"\n[Speaker: {current_speaker}]: {current_message}")

    return "\n".join(parts)


async def decide_and_generate_reply(
    group_name: str,
    recent_messages: list,
    current_speaker: str,
    current_message: str,
    user_profile_summary: str,
    ignore_detected: bool = False,
    self_name: str = "Ruhi"
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

    if (response.startswith('"') and response.endswith('"')) or \
       (response.startswith("'") and response.endswith("'")):
        response = response[1:-1].strip()

    if not response or response.upper() == "NO_REPLY":
        return None

    logger.info(f"AI reply for {current_speaker} in {group_name}: {response[:80]}...")
    return response


async def generate_ignore_reaction(
    group_name: str,
    recent_messages: list,
    self_name: str = "Ruhi"
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
    current_notes: str
) -> str:
    system = """You are an internal note-taker. Based on recent chat exchanges, write a BRIEF (1-2 sentence) 
    relationship note about how this person interacts with Ruhi. Note their tone, if they're friendly/rude/funny/flirty.
    Also suggest a tone_preference from: friendly, sarcastic, cold, playful, respectful, annoyed.
    Format: NOTES: <notes> | TONE: <tone>"""

    user_msg = f"""User: {user_name}
    Previous notes: {current_notes or 'None'}
    Recent exchanges:
    {recent_exchanges}"""

    return await call_llm(system, user_msg, max_tokens=80)
