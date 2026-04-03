"""
config.py — Centralized configuration for SnapWatch.
Loads all settings from environment variables so nothing is hardcoded.
"""

import os
from dotenv import load_dotenv

# Load .env file in development (no-op in production where env vars are set by Render)
load_dotenv()


class Config:
    # eBay Developer credentials (OAuth2 client credentials flow)
    EBAY_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID")
    EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET")

    # Anthropic API key
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

    # Discord webhook URL for price alerts (optional — alerts disabled if not set)
    DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

    # SQLite database path
    # Uses /data/ (Render persistent disk) in production, local file in development
    _db_path = "/data/snapwatch.db" if os.path.isdir("/data") else "snapwatch.db"
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{_db_path}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # eBay Browse API constants
    EBAY_CATEGORY_ID = "625"  # Cameras category
    EBAY_SEARCH_LIMIT = 50
    EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
    EBAY_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    EBAY_MARKETPLACE_ID = "EBAY_US"

    # Claude model configuration
    CLAUDE_MODEL = "claude-sonnet-4-20250514"
    CLAUDE_MAX_TOKENS = 1000

    # Scheduler interval for rechecking saved alerts
    SCHEDULER_INTERVAL_HOURS = 24
