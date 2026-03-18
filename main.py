"""
Main entry point for Ruhi UserBot.
Flask starts FIRST and stays alive regardless of bot status.
"""

import asyncio
import logging
import threading
import sys

from web_server import run_web_server
from userbot import bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

logger = logging.getLogger("ruhi.main")


def start_flask_in_thread():
    """Flask daemon thread — stays alive no matter what."""
    t = threading.Thread(target=run_web_server, daemon=True)
    t.start()
    logger.info("Flask web server thread started.")
    return t


async def run_bot_forever():
    """Run bot with auto-restart on crash."""
    while True:
        try:
            logger.info("=" * 60)
            logger.info("  愛 | 𝗥𝗨𝗛𝗜 𝗫 𝗤𝗡𝗥〆 - AI-Powered Telegram UserBot")
            logger.info("=" * 60)
            await bot.start()
            logger.info("Bot is fully operational. Waiting for messages...")
            await bot.client.run_until_disconnected()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutdown signal received.")
            break
        except Exception as e:
            logger.error(f"Bot crashed: {e} — restarting in 15 seconds...", exc_info=True)
            try:
                await bot.stop()
            except Exception:
                pass
            await asyncio.sleep(15)  # wait before restart


if __name__ == "__main__":
    # Flask FIRST — so Render health check passes immediately
    flask_thread = start_flask_in_thread()

    # Give Flask 2 seconds to bind to port
    import time
    time.sleep(2)

    try:
        asyncio.run(run_bot_forever())
    except KeyboardInterrupt:
        logger.info("Process interrupted. Exiting.")
