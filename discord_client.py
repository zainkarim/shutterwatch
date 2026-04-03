"""
discord_client.py — Discord webhook integration for ShutterWatch price alerts.

Sends a formatted embed message to a Discord channel when eBay listings
are found below a user's saved price threshold.
"""

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


def send_price_alert(
    camera_model: str,
    threshold: float,
    matching_listings: list,
    webhook_url: str,
) -> bool:
    """
    Send a Discord embed notification when listings are found below the price threshold.

    Args:
        camera_model: The camera model that triggered the alert
        threshold: The user's price threshold in USD
        matching_listings: List of listing dicts (title, price, condition, url) under threshold
        webhook_url: The Discord webhook URL to POST to

    Returns:
        True if Discord returned 204 (success), False otherwise.
    """
    if not webhook_url:
        logger.warning("No Discord webhook URL configured — skipping alert for '%s'", camera_model)
        return False

    if not matching_listings:
        return False

    # Build embed fields — cap at 5 to stay within Discord's embed limits
    fields = []
    for listing in matching_listings[:5]:
        fields.append({
            "name": listing["title"][:100],  # Discord field name max: 256, being conservative
            "value": (
                f"Price: **${listing['price']:.2f}**\n"
                f"Condition: {listing.get('condition', 'Unknown')}\n"
                f"[View on eBay]({listing['url']})"
            ),
            "inline": False,
        })

    count = len(matching_listings)
    extra = f" (+{count - 5} more)" if count > 5 else ""

    payload = {
        "username": "ShutterWatch",
        "embeds": [
            {
                "title": f"Price Alert: {camera_model}",
                "description": (
                    f"**{count} listing{'s' if count != 1 else ''}** found below your "
                    f"**${threshold:.2f}** threshold{extra}."
                ),
                "color": 3066993,  # #2ecc71 — green
                "fields": fields,
                "footer": {
                    "text": f"ShutterWatch \u2022 Threshold: ${threshold:.2f} \u2022 {camera_model}"
                },
                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            }
        ],
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=10,
        )
        # Discord returns 204 No Content on successful webhook delivery
        if response.status_code == 204:
            logger.info(
                "Discord alert sent for '%s' — %d listings below $%.2f",
                camera_model,
                count,
                threshold,
            )
            return True
        else:
            logger.error(
                "Discord webhook returned unexpected status %d for '%s': %s",
                response.status_code,
                camera_model,
                response.text[:200],
            )
            return False

    except requests.RequestException as e:
        logger.error("Failed to send Discord alert for '%s': %s", camera_model, e)
        return False
