"""
scheduler.py — APScheduler background job for SnapWatch.

Runs a saved-search check every 24 hours:
  - Queries all Alert records from the database
  - Re-searches eBay for each camera model
  - Fires a Discord webhook if any listing is priced below the user's threshold
  - Updates last_checked timestamp on each alert
"""

import atexit
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from discord_client import send_price_alert
from ebay_client import search_listings

logger = logging.getLogger(__name__)

# Module-level scheduler reference (used for atexit cleanup)
_scheduler = None


def run_saved_search_job(app) -> None:
    """
    Check all saved alerts against current eBay listings and fire Discord alerts.

    Must be called with the Flask app object so we can push an app context
    (APScheduler runs in a background thread, not inside a Flask request context).
    """
    # Import here to avoid circular imports at module load time
    from models import Alert, db

    logger.info("Running saved search job...")

    with app.app_context():
        try:
            alerts = Alert.query.all()
            logger.info("Checking %d saved alert(s)", len(alerts))

            for alert in alerts:
                try:
                    # Fetch current eBay listings for this camera model
                    listings = search_listings(alert.camera_model)

                    if not listings:
                        logger.info("No listings found for '%s'", alert.camera_model)
                        # Still update last_checked so we track that we tried
                        alert.last_checked = datetime.utcnow()
                        db.session.commit()
                        continue

                    # Filter to listings priced below the user's threshold
                    matching = [l for l in listings if l["price"] < alert.price_threshold]

                    if matching:
                        logger.info(
                            "Found %d listing(s) below $%.2f for '%s'",
                            len(matching),
                            alert.price_threshold,
                            alert.camera_model,
                        )
                        # Use the per-alert webhook URL if set, otherwise fall back to
                        # the global DISCORD_WEBHOOK_URL from config
                        from config import Config

                        webhook_url = alert.discord_webhook_url or Config.DISCORD_WEBHOOK_URL
                        send_price_alert(
                            camera_model=alert.camera_model,
                            threshold=alert.price_threshold,
                            matching_listings=matching,
                            webhook_url=webhook_url,
                        )
                    else:
                        logger.info(
                            "No listings below $%.2f for '%s'",
                            alert.price_threshold,
                            alert.camera_model,
                        )

                    alert.last_checked = datetime.utcnow()
                    db.session.commit()

                except Exception as e:
                    logger.error(
                        "Error processing alert %d ('%s'): %s",
                        alert.id,
                        alert.camera_model,
                        e,
                    )
                    # Continue processing remaining alerts even if one fails
                    continue

        except Exception as e:
            logger.error("Saved search job encountered an error: %s", e)

    logger.info("Saved search job completed")


def init_scheduler(app):
    """
    Initialize and start the APScheduler background scheduler.

    Registers the saved-search job to run every 24 hours.
    Uses a daemon thread so Gunicorn can shut down cleanly.

    Args:
        app: The Flask application instance

    Returns:
        The running BackgroundScheduler instance.
    """
    global _scheduler

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        func=run_saved_search_job,
        args=[app],
        trigger="interval",
        hours=24,
        id="saved_searches_job",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started — saved searches will run every 24 hours")

    # Shut down the scheduler cleanly when the process exits
    atexit.register(lambda: _scheduler.shutdown(wait=False))

    return _scheduler
