"""
Dummy web server to keep Render free-tier web service alive.
Provides health check endpoint and basic status info.
"""

import os
import logging
from flask import Flask, jsonify
from datetime import datetime, timezone

logger = logging.getLogger("ruhi.web")

app = Flask(__name__)

# Track startup time
_start_time = datetime.now(timezone.utc)


@app.route("/")
def home():
    """Root endpoint - health check."""
    uptime = (datetime.now(timezone.utc) - _start_time).total_seconds()
    return jsonify({
        "status": "alive",
        "bot": "Ruhi UserBot",
        "uptime_seconds": int(uptime),
        "message": "愛 | 𝗥𝗨𝗛𝗜 𝗫 𝗤𝗡𝗥〆 is running!",
    })


@app.route("/health")
def health():
    """Health check endpoint for monitoring."""
    return jsonify({"status": "ok"}), 200


@app.route("/ping")
def ping():
    """Simple ping endpoint."""
    return "pong", 200


def run_web_server():
    """Start the Flask web server."""
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Starting web server on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
