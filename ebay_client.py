"""
ebay_client.py — eBay API integration for ShutterWatch.

Handles:
  - OAuth2 app token acquisition and caching (Browse API, client credentials flow)
  - Searching for active camera listings via the Browse API
  - Fetching sold/completed listings via the Finding API (no OAuth2 needed)
  - Calculating price statistics from a list of listings
"""

import base64
import logging
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests

from config import Config

logger = logging.getLogger(__name__)

# Module-level token cache — avoids re-fetching the token on every request
_token_cache = {
    "token": None,
    "expires_at": 0,  # Unix timestamp
}


def get_app_token() -> str:
    """
    Fetch (or return cached) an eBay OAuth2 application access token.

    Uses the client credentials grant flow:
    POST /identity/v1/oauth2/token with Basic auth and the public API scope.

    Returns the bearer token string, or raises an exception on failure.
    """
    now = time.time()

    # Return cached token if it won't expire in the next 60 seconds
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    if not Config.EBAY_CLIENT_ID or not Config.EBAY_CLIENT_SECRET:
        raise ValueError("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET must be set in environment")

    # Encode credentials as Basic auth header
    credentials = base64.b64encode(
        f"{Config.EBAY_CLIENT_ID}:{Config.EBAY_CLIENT_SECRET}".encode()
    ).decode()

    try:
        response = requests.post(
            Config.EBAY_TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        _token_cache["token"] = data["access_token"]
        _token_cache["expires_at"] = now + data.get("expires_in", 7200)

        logger.info("eBay app token refreshed successfully")
        return _token_cache["token"]

    except requests.RequestException as e:
        logger.error("Failed to obtain eBay app token: %s", e)
        raise


def search_listings(camera_model: str, limit: int = None) -> list:
    """
    Search eBay for active camera listings matching the given model name.

    Queries the Browse API with category_ids=625 (Cameras) to filter results
    to camera equipment only.

    Args:
        camera_model: The camera model to search for (e.g., "Canon AE-1")
        limit: Max number of results to return (defaults to Config.EBAY_SEARCH_LIMIT)

    Returns:
        List of normalized listing dicts with keys: title, price, condition, url, item_id
        Returns an empty list on any error (never raises).
    """
    if limit is None:
        limit = Config.EBAY_SEARCH_LIMIT

    try:
        token = get_app_token()
    except Exception as e:
        logger.error("Cannot search eBay — failed to get token: %s", e)
        return []

    try:
        response = requests.get(
            Config.EBAY_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": Config.EBAY_MARKETPLACE_ID,
                "Content-Type": "application/json",
            },
            params={
                "q": camera_model,
                "category_ids": Config.EBAY_CATEGORY_ID,
                "limit": limit,
                "filter": "buyingOptions:{FIXED_PRICE}",
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()

    except requests.RequestException as e:
        logger.error("eBay Browse API request failed for '%s': %s", camera_model, e)
        return []

    items = data.get("itemSummaries", [])
    if not items:
        logger.info("No eBay listings found for '%s'", camera_model)
        return []

    listings = []
    for item in items:
        # Extract price — Browse API returns price as {"value": "120.00", "currency": "USD"}
        price_info = item.get("price", {})
        try:
            price = float(price_info.get("value", 0))
        except (ValueError, TypeError):
            price = 0.0

        # Skip listings with no price (can't be useful for market analysis)
        if price <= 0:
            continue

        listings.append({
            "item_id": item.get("itemId", ""),
            "title": item.get("title", "Untitled"),
            "price": price,
            "condition": item.get("condition", "Unknown"),
            "url": item.get("itemWebUrl", ""),
        })

    logger.info("Found %d listings for '%s'", len(listings), camera_model)
    return listings


def calculate_price_stats(listings: list) -> dict:
    """
    Calculate summary price statistics from a list of listing dicts.

    Args:
        listings: List of listing dicts each containing a 'price' key

    Returns:
        Dict with keys: average, high, low, median, count
        All values are 0 if the listings list is empty.
    """
    prices = [l["price"] for l in listings if l.get("price", 0) > 0]

    if not prices:
        return {"average": 0, "high": 0, "low": 0, "median": 0, "count": 0}

    return {
        "average": round(sum(prices) / len(prices), 2),
        "high": round(max(prices), 2),
        "low": round(min(prices), 2),
        "median": round(statistics.median(prices), 2),
        "count": len(prices),
    }


def find_sold_listings(camera_model: str, days: int = 90) -> list:
    """
    Fetch recently sold camera listings using the Browse API.

    Uses filter=soldItems:true to restrict results to completed sales,
    returning real transaction prices (not asking prices). Uses the same
    OAuth2 token flow as search_listings.

    Args:
        camera_model: The camera model to search for (e.g., "Canon AE-1")
        days: How many days back to include (default 90)

    Returns:
        List of dicts with keys: title, price (float), sold_date (ISO string)
        Returns an empty list on any error (never raises).
    """
    try:
        token = get_app_token()
    except Exception as e:
        logger.error("Cannot fetch sold listings — failed to get token: %s", e)
        return []

    try:
        response = requests.get(
            Config.EBAY_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": Config.EBAY_MARKETPLACE_ID,
                "Content-Type": "application/json",
            },
            params={
                "q": camera_model,
                "category_ids": Config.EBAY_CATEGORY_ID,
                "filter": "soldItems:true",
                "limit": 200,
                "sort": "endingSoonest",
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()

    except requests.RequestException as e:
        logger.error("Browse API sold listings request failed for '%s': %s", camera_model, e)
        return []

    items = data.get("itemSummaries", [])
    if not items:
        logger.info("No sold listings found for '%s'", camera_model)
        return []

    sold = []

    for item in items:
        try:
            price_info = item.get("lastSoldPrice") or item.get("price", {})
            price = float(price_info.get("value", 0))

            date_str = item.get("lastSoldDate") or item.get("itemEndDate", "")
            sold_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            continue

        if price <= 0:
            continue

        sold.append({
            "title": item.get("title", "Untitled"),
            "price": price,
            "sold_date": sold_date.isoformat(),
        })

    logger.info("Found %d sold listings for '%s' in the last %d days", len(sold), camera_model, days)
    return sold
