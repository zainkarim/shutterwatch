"""
claude_client.py — Anthropic Claude API integration for ShutterWatch.

Provides two functions:
  get_market_summary()      — AI-generated market analysis for a camera search
  assess_listing_condition() — Classify a listing as FUNCTIONAL or FLAGGED (stretch goal)

All API calls use model claude-sonnet-4-20250514 with max_tokens=1000.
"""

import json
import logging

import anthropic

from config import Config

logger = logging.getLogger(__name__)


def _get_client() -> anthropic.Anthropic:
    """Return an Anthropic client instance."""
    if not Config.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY must be set in environment")
    return anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)


def get_market_summary(camera_model: str, listings: list, stats: dict) -> str:
    """
    Generate a 3-4 sentence AI market summary for a camera model's eBay listings.

    Passes listing titles, prices, conditions, and aggregate stats to Claude
    to produce a natural language assessment of current market conditions.

    Args:
        camera_model: The camera model being analyzed (e.g., "Canon AE-1")
        listings: List of listing dicts with keys: title, price, condition
        stats: Price stats dict with keys: average, median, low, high, count

    Returns:
        A plain-prose market summary string.
        Returns a fallback message if the API call fails.
    """
    if not listings:
        return (
            f"No active eBay listings were found for \"{camera_model}\" at this time. "
            "Try a different search term or check back later."
        )

    # Format up to 20 listings to stay well within context limits
    listing_lines = []
    for i, listing in enumerate(listings[:20], 1):
        listing_lines.append(
            f'{i}. "{listing["title"]}" — ${listing["price"]:.2f} ({listing.get("condition", "Unknown")})'
        )
    listing_block = "\n".join(listing_lines)

    prompt = f"""You are an expert in the used camera equipment market with deep knowledge of film and digital cameras, their collectibility, and typical resale values.

Analyze the following {stats.get("count", len(listings))} active eBay listings for "{camera_model}":

{listing_block}

Price statistics across these listings:
- Count: {stats.get("count", 0)} listings
- Average price: ${stats.get("average", 0):.2f}
- Median price: ${stats.get("median", 0):.2f}
- Lowest price: ${stats.get("low", 0):.2f}
- Highest price: ${stats.get("high", 0):.2f}

Write a 3-4 sentence market summary covering:
1. The current going rate and whether prices seem high, low, or fair for this camera
2. Whether this is a good time to buy based on price spread and availability
3. Any notable anomalies in the listings (suspiciously cheap, unusually expensive, parts-only listings skewing data)

Be direct and specific. Use dollar amounts. Do not use headers or bullet points. Write in plain prose only."""

    try:
        client = _get_client()
        message = client.messages.create(
            model=Config.CLAUDE_MODEL,
            max_tokens=Config.CLAUDE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()

    except Exception as e:
        logger.error("Claude market summary failed for '%s': %s", camera_model, e)
        return (
            f"Market analysis is temporarily unavailable. "
            f"Based on {stats.get('count', 0)} listings, prices range from "
            f"${stats.get('low', 0):.2f} to ${stats.get('high', 0):.2f} "
            f"with a median of ${stats.get('median', 0):.2f}."
        )


def assess_listing_condition(title: str, description: str) -> dict:
    """
    Classify a single eBay listing as FUNCTIONAL or FLAGGED using Claude.

    FUNCTIONAL = camera appears to be in working/usable condition.
    FLAGGED = listing indicates broken, parts-only, needs repair, or major damage.

    This is the stretch-goal condition filter. Listings classified as FLAGGED
    are excluded from price average calculations and labeled separately in the UI.

    Args:
        title: The eBay listing title
        description: The listing description (will be truncated to 500 chars)

    Returns:
        Dict with keys: status ("FUNCTIONAL" or "FLAGGED"), reason (string)
        Defaults to {"status": "FUNCTIONAL", "reason": "Could not assess"} on any error.
    """
    fallback = {"status": "FUNCTIONAL", "reason": "Could not assess listing condition"}

    prompt = f"""You are a used camera equipment specialist. Classify this eBay listing as FUNCTIONAL or FLAGGED.

FUNCTIONAL: Camera appears to be in working or usable condition for photography, even if cosmetically worn.
FLAGGED: Listing indicates the camera is broken, for parts only, needs repair, has major functional defects, is water/fire damaged, or is missing critical components.

Listing title: {title}
Listing description: {description[:500] if description else "(no description provided)"}

Respond with only valid JSON, no other text:
{{"status": "FUNCTIONAL", "reason": "brief reason"}}
or
{{"status": "FLAGGED", "reason": "brief reason"}}"""

    try:
        client = _get_client()
        message = client.messages.create(
            model=Config.CLAUDE_MODEL,
            max_tokens=Config.CLAUDE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        # Parse the JSON response
        result = json.loads(raw)

        # Validate expected structure
        if result.get("status") not in ("FUNCTIONAL", "FLAGGED"):
            logger.warning("Unexpected status from Claude condition filter: %s", raw)
            return fallback

        return result

    except json.JSONDecodeError:
        logger.warning("Claude returned non-JSON for condition filter: title='%s'", title[:80])
        return fallback
    except Exception as e:
        logger.error("Claude condition filter failed for listing '%s': %s", title[:80], e)
        return fallback
