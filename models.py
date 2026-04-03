"""
models.py — SQLAlchemy database models for ShutterWatch.

Tables:
  searches  — records of each camera search performed
  listings  — individual eBay listings returned by a search
  alerts    — saved price threshold alerts with Discord webhook
"""

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Search(db.Model):
    """Records each camera model the user has searched for."""

    __tablename__ = "searches"

    id = db.Column(db.Integer, primary_key=True)
    camera_model = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationship: one search has many listings
    listings = db.relationship("Listing", backref="search", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "camera_model": self.camera_model,
            "created_at": self.created_at.isoformat(),
        }


class Listing(db.Model):
    """An individual eBay listing returned from a search."""

    __tablename__ = "listings"

    id = db.Column(db.Integer, primary_key=True)
    search_id = db.Column(db.Integer, db.ForeignKey("searches.id"), nullable=False)
    title = db.Column(db.String(500), nullable=False)
    price = db.Column(db.Float, nullable=False)
    condition = db.Column(db.String(100))
    url = db.Column(db.String(1000), nullable=False)
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    # Used by the LLM condition filter (stretch goal): True = likely broken/parts-only
    flagged = db.Column(db.Boolean, default=False, nullable=False)
    flag_reason = db.Column(db.String(500))  # Claude's reason for flagging

    def to_dict(self):
        return {
            "id": self.id,
            "search_id": self.search_id,
            "title": self.title,
            "price": self.price,
            "condition": self.condition,
            "url": self.url,
            "fetched_at": self.fetched_at.isoformat(),
            "flagged": self.flagged,
            "flag_reason": self.flag_reason,
        }


class Alert(db.Model):
    """A saved price threshold alert for a camera model."""

    __tablename__ = "alerts"

    id = db.Column(db.Integer, primary_key=True)
    camera_model = db.Column(db.String(255), nullable=False)
    price_threshold = db.Column(db.Float, nullable=False)
    # Per-alert Discord webhook URL (overrides the global DISCORD_WEBHOOK_URL env var)
    discord_webhook_url = db.Column(db.String(1000))
    last_checked = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "camera_model": self.camera_model,
            "price_threshold": self.price_threshold,
            "discord_webhook_url": self.discord_webhook_url,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
        }


def init_db(app):
    """Initialize the database with the Flask app and create all tables."""
    db.init_app(app)
    with app.app_context():
        db.create_all()
