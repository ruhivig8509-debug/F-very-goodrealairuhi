"""
AI Engine module for Ruhi UserBot.
Handles all LLM interactions via HuggingFace Router using the OpenAI-compatible API.
"""

import os
import logging
import asyncio
from typing import Optional
from openai import OpenAI

import config

logger = logging.getLogger("ruhi.ai")

# Initialize the OpenAI client pointed at HuggingFace Router
client = OpenAI(
    base_url=config.AI_BASE_URL,
    api_key=config.HF_TOKEN,
)


def _call_llm_sync(system_prompt: str, user_message: str, max_tokens: int = 300) -> str:
    """Synchronous LLM call (will be run in executor for async)."""
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
        response = completion.choices[0].message.content.strip()
        return response
    except Exception as e:
        logger.error(f"LLM API call failed: {e}")
        return "NO_REPLY"


async def call_llm(system_prompt: str, user_message: str, max_tokens: int = 300) -> str:
    """Async wrapper for LLM call."""
    loop = asyncio.get_event_loop()
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
    """Build the user-facing prompt with full context for the LLM."""
    
    # Format chat history
    history_lines = []
    for msg in recent_messages:
        name = msg["user_name"] or "Unknown"
        text = msg["text"] or ""
        if msg["is_self"]:
            name = self_name
        if text:
            history_lines.append(f"[{name}]: {text}")
    
    history_str = "\n".join(history_lines[-config.CONTEXT_WINDOW_SIZE:]) if history_lines else "(No recent history)"
    
    # Build the prompt
    parts = []
    
    if is_decide_mode:
        parts.append("[DECIDE_REPLY]")
    
    parts.append(f"[Chat: {group_name}]")
    parts.append(f"\n[Recent Chat History]:\n{history_str}")
    
    if user_profile_summary:
        parts.append(f"\n[Relationship with {current_speaker}]: {user_profile_summary}")
    
    if ignore_detected:
        parts.append(f"\n[SYSTEM NOTE: You (Ruhi) spoke recently in this chat but everyone is ignoring you. No one responded to your last message. React naturally - you can express annoyance or playful frustration.]")
    
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
    """
    Main function: Decides whether to reply and generates a response.
    Returns the reply text or None if NO_REPLY.
    """
    
    # Build context
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
    
    # Call LLM
    response = await call_llm(
        system_prompt=config.USER_PERSONALITY_PROMPT,
        user_message=user_prompt,
        max_tokens=350,
    )
    
    # Check for NO_REPLY
    if not response or response.strip().upper() == "NO_REPLY":
        logger.info(f"AI decided NO_REPLY for message from {current_speaker} in {group_name}")
        return None
    
    # Clean up response - remove any accidental metadata the LLM might have added
    response = response.strip()
    
    # Remove common LLM artifacts
    for prefix in ["[Ruhi]:", "Ruhi:", "[Reply]:", "Reply:"]:
        if response.lower().startswith(prefix.lower()):
            response = response[len(prefix):].strip()
    
    # Remove wrapping quotes if present
    if (response.startswith('"') and response.endswith('"')) or \
       (response.startswith("'") and response.endswith("'")):
        response = response[1:-1].strip()
    
    if not response or response.upper() == "NO_REPLY":
        return None
    
    logger.info(f"AI generated reply for {current_speaker} in {group_name}: {response[:80]}...")
    return response


async def generate_ignore_reaction(
    group_name: str,
    recent_messages: list,
    self_name: str = "Ruhi"
) -> Optional[str]:
    """Generate a reaction when the bot detects it's being ignored."""
    
    user_prompt = build_context_prompt(
        group_name=group_name,
        recent_messages=recent_messages,
        current_speaker="SYSTEM",
        current_message="Everyone is ignoring your messages in this chat. Express your feelings about being ignored.",
        user_profile_summary="",
        is_decide_mode=False,
        ignore_detected=True,
        self_name=self_name,
    )
    
    response = await call_llm(
        system_prompt=config.USER_PERSONALITY_PROMPT,
        user_message=user_prompt,
        max_tokens=200,
    )
    
    if not response or response.strip().upper() == "NO_REPLY":
        return None
    
    return response.strip()


async def generate_relationship_update(
    user_name: str,
    recent_exchanges: str,
    current_notes: str
) -> str:
    """Use LLM to update relationship notes about a user."""
    
    system = """You are an internal note-taker. Based on recent chat exchanges, write a BRIEF (1-2 sentence) 
    relationship note about how this person interacts with Ruhi. Note their tone, if they're friendly/rude/funny/flirty.
    Also suggest a tone_preference from: friendly, sarcastic, cold, playful, respectful, annoyed.
    Format: NOTES: <notes> | TONE: <tone>"""
    
    user_msg = f"""User: {user_name}
    Previous notes: {current_notes or 'None'}
    Recent exchanges:
    {recent_exchanges}"""
    
    response = await call_llm(system, user_msg, max_tokens=100)
    return response
