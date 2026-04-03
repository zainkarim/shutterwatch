"""
app.py — Flask application factory and API routes for SnapWatch.

Routes:
  GET  /                       Serve the single-page app
  POST /api/search             Search eBay and return listings + AI summary
  POST /api/alerts             Save a price threshold alert
  GET  /api/alerts             List all saved alerts
  DELETE /api/alerts/<id>      Delete a saved alert
  GET  /api/searches           Recent search history (last 10)
  GET  /api/sold-history       eBay Finding API sold price history (weekly buckets)
  GET  /api/search-history     Local DB search history for price trend chart
  POST /api/assess/<search_id> (Stretch goal) Run LLM condition filter on a search's listings
"""

import logging
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from config import Config
from models import Alert, Listing, Search, db, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app():
    """Flask application factory."""
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize database
    init_db(app)

    # Start the background scheduler for saved search alerts.
    # The WERKZEUG_RUN_MAIN guard prevents double-initialization when Flask's
    # debug reloader spawns a second process in development.
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        from scheduler import init_scheduler
        init_scheduler(app)

    register_routes(app)
    return app


def register_routes(app):
    """Register all URL routes on the app."""

    @app.route("/")
    def index():
        """Serve the single-page frontend application."""
        return render_template("index.html")

    # -------------------------------------------------------------------------
    # Search endpoint — core feature
    # -------------------------------------------------------------------------

    @app.route("/api/search", methods=["POST"])
    def search():
        """
        Search eBay for active camera listings and generate an AI market summary.

        Request body (JSON):
          { "camera_model": "Canon AE-1" }

        Response (JSON):
          {
            "camera_model": "Canon AE-1",
            "search_id": 42,
            "stats": { "average": 120.50, "median": 115.00, "low": 45.00, "high": 299.00, "count": 47 },
            "summary": "The Canon AE-1 is currently...",
            "listings": [ { "title": "...", "price": 120.00, "condition": "Used", "url": "...", "flagged": false } ]
          }
        """
        try:
            data = request.get_json(silent=True) or {}
            camera_model = (data.get("camera_model") or "").strip()

            if not camera_model:
                return jsonify({"error": "camera_model is required"}), 400

            # Import clients here so startup failures don't block the app
            from claude_client import get_market_summary
            from ebay_client import calculate_price_stats, search_listings

            # Fetch listings from eBay
            listings = search_listings(camera_model)

            # Persist the search record
            search_record = Search(camera_model=camera_model)
            db.session.add(search_record)
            db.session.flush()  # Get the search_id before committing

            # Persist individual listings
            listing_records = []
            for l in listings:
                record = Listing(
                    search_id=search_record.id,
                    title=l["title"],
                    price=l["price"],
                    condition=l.get("condition"),
                    url=l["url"],
                )
                db.session.add(record)
                listing_records.append(record)

            db.session.commit()

            # Calculate price statistics
            stats = calculate_price_stats(listings)

            # Generate AI market summary
            summary = get_market_summary(camera_model, listings, stats)

            return jsonify({
                "camera_model": camera_model,
                "search_id": search_record.id,
                "stats": stats,
                "summary": summary,
                "listings": [
                    {
                        "id": r.id,
                        "title": r.title,
                        "price": r.price,
                        "condition": r.condition,
                        "url": r.url,
                        "flagged": r.flagged,
                        "flag_reason": r.flag_reason,
                    }
                    for r in listing_records
                ],
            })

        except Exception as e:
            logger.error("Error in /api/search: %s", e)
            return jsonify({"error": "Search failed. Please try again."}), 500

    # -------------------------------------------------------------------------
    # Alerts endpoints — saved price threshold alerts
    # -------------------------------------------------------------------------

    @app.route("/api/alerts", methods=["POST"])
    def create_alert():
        """
        Save a new price threshold alert.

        Request body (JSON):
          {
            "camera_model": "Canon AE-1",
            "price_threshold": 100.00,
            "discord_webhook_url": "https://discord.com/api/webhooks/..."
          }

        Response: { "success": true, "alert": { ...alert dict... } }
        """
        try:
            data = request.get_json(silent=True) or {}
            camera_model = (data.get("camera_model") or "").strip()
            price_threshold = data.get("price_threshold")
            discord_webhook_url = (data.get("discord_webhook_url") or "").strip() or None

            if not camera_model:
                return jsonify({"error": "camera_model is required"}), 400

            if price_threshold is None:
                return jsonify({"error": "price_threshold is required"}), 400

            try:
                price_threshold = float(price_threshold)
            except (ValueError, TypeError):
                return jsonify({"error": "price_threshold must be a number"}), 400

            if price_threshold <= 0:
                return jsonify({"error": "price_threshold must be greater than 0"}), 400

            alert = Alert(
                camera_model=camera_model,
                price_threshold=price_threshold,
                discord_webhook_url=discord_webhook_url,
            )
            db.session.add(alert)
            db.session.commit()

            return jsonify({"success": True, "alert": alert.to_dict()}), 201

        except Exception as e:
            logger.error("Error in POST /api/alerts: %s", e)
            return jsonify({"error": "Failed to save alert. Please try again."}), 500

    @app.route("/api/alerts", methods=["GET"])
    def list_alerts():
        """Return all saved alerts as a JSON array."""
        try:
            alerts = Alert.query.order_by(Alert.id.desc()).all()
            return jsonify([a.to_dict() for a in alerts])
        except Exception as e:
            logger.error("Error in GET /api/alerts: %s", e)
            return jsonify({"error": "Failed to load alerts."}), 500

    @app.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
    def delete_alert(alert_id):
        """Delete a saved alert by ID."""
        try:
            alert = Alert.query.get(alert_id)
            if not alert:
                return jsonify({"error": "Alert not found"}), 404
            db.session.delete(alert)
            db.session.commit()
            return jsonify({"success": True})
        except Exception as e:
            logger.error("Error in DELETE /api/alerts/%d: %s", alert_id, e)
            return jsonify({"error": "Failed to delete alert."}), 500

    # -------------------------------------------------------------------------
    # Search history endpoint
    # -------------------------------------------------------------------------

    @app.route("/api/searches", methods=["GET"])
    def list_searches():
        """Return the 10 most recent searches."""
        try:
            searches = Search.query.order_by(Search.created_at.desc()).limit(10).all()
            return jsonify([s.to_dict() for s in searches])
        except Exception as e:
            logger.error("Error in GET /api/searches: %s", e)
            return jsonify({"error": "Failed to load search history."}), 500

    # -------------------------------------------------------------------------
    # Price history endpoints — charts
    # -------------------------------------------------------------------------

    @app.route("/api/sold-history", methods=["GET"])
    def sold_history():
        """
        Return eBay sold price history for a camera model, grouped into weekly buckets.

        Uses the eBay Finding API (findCompletedItems) — shows real transaction prices,
        not just asking prices.

        Query params: camera_model (required)

        Response:
          {
            "camera_model": "Canon AE-1",
            "total_sold": 84,
            "weeks": [
              { "week_start": "2026-03-03", "average": 118.00, "median": 115.00,
                "low": 60.00, "high": 250.00, "count": 8 },
              ...
            ]
          }
        """
        try:
            camera_model = (request.args.get("camera_model") or "").strip()
            if not camera_model:
                return jsonify({"error": "camera_model query parameter is required"}), 400

            from ebay_client import calculate_price_stats, find_sold_listings

            sold = find_sold_listings(camera_model)

            if not sold:
                return jsonify({"camera_model": camera_model, "total_sold": 0, "sales": []})

            # Sort chronologically so the chart reads left to right
            sold_sorted = sorted(sold, key=lambda x: x["sold_date"])

            return jsonify({
                "camera_model": camera_model,
                "total_sold": len(sold_sorted),
                "sales": sold_sorted,
            })

        except Exception as e:
            logger.error("Error in GET /api/sold-history: %s", e)
            return jsonify({"error": "Failed to load sold history."}), 500

    @app.route("/api/search-history", methods=["GET"])
    def search_history():
        """
        Return local search history for a camera model as a price trend series.

        Reads from the local DB — each time the user searched this model,
        computes the average/median/low/high of the listings returned at that time.
        Useful for tracking how asking prices have drifted over time.

        Query params: camera_model (required)

        Response:
          {
            "camera_model": "Canon AE-1",
            "history": [
              { "date": "2026-04-01T10:22:00", "average": 120.50, "median": 115.00,
                "low": 45.00, "high": 299.00, "count": 47 },
              ...
            ]
          }
        """
        try:
            camera_model = (request.args.get("camera_model") or "").strip()
            if not camera_model:
                return jsonify({"error": "camera_model query parameter is required"}), 400

            from ebay_client import calculate_price_stats

            # Case-insensitive match, oldest first so the chart reads left-to-right
            searches = (
                Search.query
                .filter(Search.camera_model.ilike(camera_model))
                .order_by(Search.created_at.asc())
                .all()
            )

            history = []
            for s in searches:
                listings = [{"price": l.price} for l in s.listings if l.price and l.price > 0]
                stats = calculate_price_stats(listings)
                if stats["count"] > 0:
                    history.append({
                        "date": s.created_at.isoformat(),
                        **stats,
                    })

            return jsonify({"camera_model": camera_model, "history": history})

        except Exception as e:
            logger.error("Error in GET /api/search-history: %s", e)
            return jsonify({"error": "Failed to load search history."}), 500

    # -------------------------------------------------------------------------
    # Stretch goal: async LLM condition assessment
    # -------------------------------------------------------------------------

    @app.route("/api/assess/<int:search_id>", methods=["POST"])
    def assess_listings(search_id):
        """
        Run the LLM condition filter on listings from a specific search.

        This is a separate endpoint (not inline in /api/search) because assessing
        50 listings individually would take 50+ seconds. The frontend calls this
        after rendering initial results, then re-fetches to show flagged badges.

        Response: { "assessed": 47, "flagged": 3, "listings": [ ...updated listing dicts... ] }
        """
        try:
            from claude_client import assess_listing_condition

            search_record = Search.query.get(search_id)
            if not search_record:
                return jsonify({"error": "Search not found"}), 404

            listings = Listing.query.filter_by(search_id=search_id).all()
            if not listings:
                return jsonify({"error": "No listings for this search"}), 404

            flagged_count = 0
            for listing in listings:
                result = assess_listing_condition(listing.title, "")
                if result["status"] == "FLAGGED":
                    listing.flagged = True
                    listing.flag_reason = result.get("reason", "")
                    flagged_count += 1
                else:
                    listing.flagged = False
                    listing.flag_reason = None

            db.session.commit()

            return jsonify({
                "assessed": len(listings),
                "flagged": flagged_count,
                "listings": [l.to_dict() for l in listings],
            })

        except Exception as e:
            logger.error("Error in POST /api/assess/%d: %s", search_id, e)
            return jsonify({"error": "Condition assessment failed."}), 500


# Allow running directly with `flask run` or `python app.py`
app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
