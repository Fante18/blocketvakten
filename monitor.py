"""The monitoring pipeline: fetch each saved search, parse, filter, dedupe
by ad id and create notifications for anything new."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import blocket
import config
import db
import notifier


def _is_paused(search: dict, now: datetime | None = None) -> bool:
    if not search.get("pause_until"):
        return False
    now = now or datetime.now(timezone.utc)
    try:
        until = datetime.fromisoformat(search["pause_until"])
    except (ValueError, TypeError):
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > now


def _matches_excludes(title: str, exclude_words: list[str]) -> bool:
    if not exclude_words:
        return False
    title_folded = title.casefold()
    for word in exclude_words:
        token = (word or "").strip().casefold()
        if not token:
            continue
        # Whole-word (or word-prefix) match so "bil" doesn't match "mobil".
        if re.search(r"\b" + re.escape(token) + r"\w*", title_folded):
            return True
    return False


def _matches_location(listing_location: str, wanted: str) -> bool:
    if not wanted:
        return True
    wanted_folded = wanted.strip().casefold()
    return wanted_folded in (listing_location or "").casefold()


def filter_listing(listing: dict, search: dict) -> bool:
    """Apply the saved search's client-side filters (exclusions, location)."""
    title = listing.get("title", "")
    if _matches_excludes(title, search.get("exclude_words", [])):
        return False
    if not _matches_location(listing.get("location", ""), search.get("location", "")):
        return False
    max_price = search.get("max_price")
    price = listing.get("price")
    if max_price is not None and price is not None and price > max_price:
        return False
    return True


def check_search(search: dict) -> dict:
    """Run one check for a single saved search."""
    search_id = search["id"]

    if not search.get("active"):
        db.mark_checked(search_id, "skipped", "Bevakningen är pausad.", 0, 0)
        return {"search_id": search_id, "new": 0, "error": None, "status": "skipped"}

    if _is_paused(search):
        db.mark_checked(search_id, "skipped", "Bevakningen är tillfälligt pausad.", 0, 0)
        return {"search_id": search_id, "new": 0, "error": None, "status": "skipped"}

    merged: dict[str, dict] = {}
    errors: list[str] = []
    keywords = search.get("keywords") or []

    for keyword in keywords:
        try:
            raw = blocket.fetch_search_listings(
                keyword, max_price=search.get("max_price"), timeout=config.FETCH_TIMEOUT
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{keyword}: {exc}")
            continue
        for listing in raw:
            listing.setdefault("published_at", None)
            merged.setdefault(listing["ad_id"], listing)

    if errors and not merged:
        message = "; ".join(errors)
        db.mark_checked(search_id, "error", message, 0, 0)
        return {"search_id": search_id, "new": 0, "error": message, "status": "error"}

    new_listings: list[dict] = []
    fetched = 0
    for listing in merged.values():
        if not filter_listing(listing, search):
            continue
        fetched += 1
        listing["published_at"] = blocket.parse_published(
            listing.get("published_text", "")
        ).isoformat()
        is_new = db.insert_listing(search_id, listing)

        # Always record the current price for history (even for known ads).
        if listing.get("price") is not None:
            db.record_price(search_id, listing["ad_id"], listing["price"])

        if is_new:
            db.create_notification(search_id, listing)
            new_listings.append(listing)

    message = errors[0] if errors else ""
    status = "ok" if not errors else "error"
    db.mark_checked(search_id, status, message, fetched, len(new_listings))

    if new_listings and search.get("send_email"):
        profile = db.get_profile(user_id=search.get("user_id") or 0)
        notifier.send_email_for_listings(
            search.get("name", ""), new_listings, recipient=profile.get("email")
        )

    # Check price drops on followed listings.
    drop_alerts = db.check_follow_price_drops(search_id)
    for alert in drop_alerts:
        notifier.send_price_drop_notification(
            search.get("name", ""), alert, user_id=search.get("user_id") or 0
        )

    # Detect disappeared listings for market-value estimation.
    live_ids = set(merged.keys())
    known_listings = db.list_listings(search_id, limit=2000)
    for known in known_listings:
        if known["ad_id"] not in live_ids:
            # Listing was known but is no longer in search results.
            # Mark it as disappeared (likely sold).
            db.record_disappeared(search_id, known)

    return {
        "search_id": search_id,
        "new": len(new_listings),
        "error": message or None,
        "status": status,
        "fetched": fetched,
    }


def run_all_checks() -> list[dict]:
    """Check every active search once. Returns a summary per search."""
    results = []
    for search in db.list_searches():
        results.append(check_search(search))
    return results
