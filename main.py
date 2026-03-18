"""
Main entry point for Ruhi UserBot.
Runs both the Telegram UserBot and the Flask web server concurrently.
"""

import asyncio
import logging
import threading
import sys
import signal

from web_server import run_web_server
from userbot import bot

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

# Reduce noise from libraries
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

logger = logging.getLogger("ruhi.main")


def start_flask_in_thread():
    """Run Flask web server in a background daemon thread."""
    flask_thread = threading.Thread(target=run_web_server, daemon=True)
    flask_thread.start()
    logger.info("Flask web server thread started.")


async def main():
    """Main async function - starts the UserBot."""
    logger.info("=" * 60)
    logger.info("  愛 | 𝗥𝗨𝗛𝗜 𝗫 𝗤𝗡𝗥〆 - AI-Powered Telegram UserBot")
    logger.info("=" * 60)
    
    # Start Flask in background thread (for Render health check)
    start_flask_in_thread()
    
    # Start the UserBot
    await bot.start()
    
    # Keep running
    logger.info("Bot is fully operational. Waiting for messages...")
    
    try:
        # Keep the event loop running
        await bot.client.run_until_disconnected()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received.")
    finally:
        await bot.stop()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Process interrupted. Exiting.")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
