"""Blocketvakten – web app + background monitor for saved Blocket searches.

Usage:
    python app.py            # serve the web app and run the scheduler
    python app.py --check    # run one check cycle for all searches, then exit
    python app.py --init     # create the database and exit

The app is self-contained (Python standard library only) and stores its data
in SQLite under data/.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import config
import db
import monitor

CHECK_LOCK = threading.Lock()

# --------------------------------------------------------------------------
# Auth helper
# --------------------------------------------------------------------------

AUTH_WHITELIST = {"/api/auth/register", "/api/auth/login", "/api/health"}


def get_user_id(handler) -> int | None:
    """Extract user_id from Authorization: Bearer <token> header."""
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    return db.validate_session(token) if token else None


def require_user(handler) -> int | None:
    """Return user_id or send 401 and return None."""
    user_id = get_user_id(handler)
    if user_id is None:
        json_response(handler, {"error": "Logga in för att fortsätta."}, 401)
    return user_id


# --------------------------------------------------------------------------
# JSON helpers
# --------------------------------------------------------------------------

def json_response(handler, payload, status=200):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


def parse_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_keywords(value) -> list[str]:
    """Accept a list or a comma-separated string of keyword variants."""
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = re.split(r"[,\n]", value)
    else:
        items = []
    seen = []
    for item in items:
        token = str(item).strip()
        if token and token not in seen:
            seen.append(token)
    return seen


def search_to_public(search: dict) -> dict:
    counts = db.listing_counts(search["id"])
    stats = db.listing_statistics(search["id"])
    search.update(
        {
            "counts": counts,
            "avg_price_30d": stats["avg"],
            "price_stats": stats,
        }
    )
    return search


def listing_to_public_full(listing: dict, search_id: int) -> dict:
    """Enrich a listing dict with good_price, deal_score, avg, follow status, profit."""
    stats = db.listing_statistics(search_id)
    avg = stats["avg"]
    good = False
    deal_score = None
    price = listing.get("price")
    if price is not None and avg is not None and avg > 0:
        good = price <= avg * config.GOOD_PRICE_RATIO
        deal_score = round(((avg - price) / avg) * 100, 1) if price <= avg else round(((avg - price) / avg) * 100, 1)
    listing["good_price"] = good
    listing["deal_score"] = deal_score
    listing["avg_price_30d"] = avg
    listing["following"] = db.is_following(search_id, listing["ad_id"])
    resale = listing.get("resale_price")
    if price and resale:
        listing["profit"] = resale - price
        listing["profit_pct"] = round(((resale - price) / price) * 100, 1)
    else:
        listing["profit"] = None
        listing["profit_pct"] = None
    return listing


def listing_to_public(listing: dict, avg_price_30d) -> dict:
    good = False
    if (
        listing.get("price") is not None
        and avg_price_30d is not None
        and avg_price_30d > 0
    ):
        good = listing["price"] <= avg_price_30d * config.GOOD_PRICE_RATIO
    listing["good_price"] = good
    listing["avg_price_30d"] = avg_price_30d
    return listing


# --------------------------------------------------------------------------
# Static files
# --------------------------------------------------------------------------

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/manifest.json": ("manifest.json", "application/json; charset=utf-8"),
    "/sw.js": ("sw.js", "application/javascript; charset=utf-8"),
}


def serve_static(handler, path: str) -> bool:
    entry = STATIC_FILES.get(path)
    if entry is None:
        return False
    filename, content_type = entry
    try:
        data = (config.STATIC_DIR / filename).read_bytes()
    except FileNotFoundError:
        handler.send_response(404)
        handler.end_headers()
        return True
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return True


# --------------------------------------------------------------------------
# API routes
# --------------------------------------------------------------------------

def route(handler, method: str, path: str) -> None:
    parts = [p for p in path.split("/") if p]

    # ── public endpoints (no auth required) ──

    if method == "GET" and path == "/api/health":
        return json_response(
            handler,
            {
                "ok": True,
                "searches": len(db.list_searches()),
                "unread_notifications": db.unread_notification_count(),
                "email_enabled": config.EMAIL_ENABLED,
            },
        )

    if method == "POST" and path == "/api/auth/register":
        body = read_json_body(handler)
        email = str(body.get("email") or "").strip()
        password = str(body.get("password") or "")
        if not email or len(password) < 4:
            return json_response(
                handler,
                {"error": "Ange en giltig e-postadress och lösenord (minst 4 tecken)."},
                400,
            )
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return json_response(handler, {"error": "Ange en giltig e-postadress."}, 400)
        user = db.create_user(email, password)
        if user is None:
            return json_response(handler, {"error": "E-postadressen används redan."}, 409)
        token = db.create_session(user["id"])
        return json_response(handler, {"token": token, "user": {"id": user["id"], "email": user["email"]}}, 201)

    if method == "POST" and path == "/api/auth/login":
        body = read_json_body(handler)
        email = str(body.get("email") or "").strip()
        password = str(body.get("password") or "")
        user = db.get_user_by_email(email)
        if user is None or not db._verify_password(password, user.get("password_hash", "")):
            return json_response(handler, {"error": "Fel e-post eller lösenord."}, 401)
        token = db.create_session(user["id"])
        return json_response(handler, {"token": token, "user": {"id": user["id"], "email": user["email"]}})

    if method == "POST" and path == "/api/auth/logout":
        token = get_user_id(handler)
        if token is not None:
            auth = handler.headers.get("Authorization", "")
            db.delete_session(auth[7:].strip())
        return json_response(handler, {"ok": True})

    if method == "GET" and path == "/api/auth/me":
        user_id = require_user(handler)
        if user_id is None:
            return
        user = db.get_user_by_id(user_id)
        return json_response(handler, {"user": user} if user else {"error": "Hittades inte."}, 404)

    # ── forgot password (public) ──

    if method == "POST" and path == "/api/auth/forgot-password":
        body = read_json_body(handler)
        email = str(body.get("email") or "").strip()
        if not email:
            return json_response(handler, {"error": "Ange din e-postadress."}, 400)
        user = db.get_user_by_email(email)
        # Don't reveal whether the email exists — always return success.
        result: dict = {
            "ok": True,
            "message": (
                "Om e-postadressen finns i systemet har ett återställningsmail skickats."
            ),
        }
        if user and user.get("id"):
            token = db.create_reset_token(user["id"])
            import notifier
            sent = notifier.send_reset_email(email, token)
            if not sent:
                # SMTP not configured — return the token so the UI can show it directly.
                result["token"] = token
                result["message"] = (
                    "E-postserver är inte konfigurerad. "
                    "Använd återställningslänken nedan för att sätta ett nytt lösenord."
                )
        return json_response(handler, result)

    if method == "POST" and path == "/api/auth/reset-password":
        body = read_json_body(handler)
        token = str(body.get("token") or "").strip()
        password = str(body.get("password") or "")
        if not token or len(password) < 4:
            return json_response(handler, {
                "error": "Ange en giltig token och ett nytt lösenord (minst 4 tecken).",
            }, 400)
        user_id = db.consume_reset_token(token)
        if user_id is None:
            return json_response(handler, {
                "error": "Länken är ogiltig eller har gått ut. Begär en ny.",
            }, 400)
        # Update the user's password.
        with db.connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (db._hash_password(password), user_id),
            )
        return json_response(handler, {"ok": True})

    # ── everything below requires auth ──

    user_id = require_user(handler)
    if user_id is None:
        return

    if method == "GET" and path == "/api/searches":
        return json_response(
            handler,
            [search_to_public(s) for s in db.list_searches(user_id=user_id)],
        )

    if method == "POST" and path == "/api/searches":
        body = read_json_body(handler)
        keywords = _clean_keywords(body.get("keywords"))
        if not keywords:
            return json_response(handler, {"error": "Minst ett sökord krävs."}, 400)
        search = db.create_search(
            user_id=user_id,
            keywords=keywords,
            name=body.get("name", ""),
            exclude_words=_clean_keywords(body.get("exclude_words")),
            max_price=parse_int(body.get("max_price")),
            location=body.get("location", ""),
            active=bool(body.get("active", True)),
            send_email=bool(body.get("send_email", False)),
            send_sms=bool(body.get("send_sms", False)),
            check_interval=parse_int(body.get("check_interval"), 1800),
        )
        return json_response(handler, search_to_public(search), 201)

    if method in ("GET", "PUT", "DELETE") and len(parts) == 3 and parts[0] == "api" and parts[1] == "searches":
        search_id = parse_int(parts[2])
        search = db.get_search_for_user(search_id, user_id) if search_id is not None else None
        if not search:
            return json_response(handler, {"error": "Hittades inte."}, 404)

        if method == "GET":
            return json_response(handler, search_to_public(search))
        if method == "DELETE":
            db.delete_search(search_id)
            return json_response(handler, {"ok": True})

        if method == "PUT":
            body = read_json_body(handler)
            fields = {}
            if "name" in body:
                fields["name"] = body["name"]
            if "keywords" in body:
                fields["keywords"] = _clean_keywords(body["keywords"])
            if "exclude_words" in body:
                fields["exclude_words"] = _clean_keywords(body["exclude_words"])
            if "max_price" in body:
                fields["max_price"] = parse_int(body["max_price"])
            if "location" in body:
                fields["location"] = body["location"]
            if "active" in body:
                fields["active"] = bool(body["active"])
            if "send_email" in body:
                fields["send_email"] = bool(body["send_email"])
            if "send_sms" in body:
                fields["send_sms"] = bool(body["send_sms"])
            if "check_interval" in body:
                fields["check_interval"] = parse_int(body["check_interval"], 1800)
            if "pause_until" in body:
                fields["pause_until"] = body["pause_until"]
            updated = db.update_search(search_id, fields)
            return json_response(handler, search_to_public(updated))

    # Listings for a search.
    if method == "GET" and len(parts) >= 4 and parts[0] == "api" and parts[1] == "searches" and parts[3] == "listings":
        search_id = parse_int(parts[2])
        search = db.get_search_for_user(search_id, user_id) if search_id is not None else None
        if not search:
            return json_response(handler, {"error": "Hittades inte."}, 404)
        stats = db.listing_statistics(search_id)
        sort = handler.query.get("sort", ["newest"])[0]
        if sort == "best_deal":
            listings = db.list_listings_sorted_by_deal_score(
                search_id, stats["avg"], limit=500
            )
        else:
            listings = db.list_listings(
                search_id, limit=500,
                sort="cheapest" if sort == "cheapest" else (
                    "most_expensive" if sort == "most_expensive" else "newest"
                ),
            )
        return json_response(
            handler,
            {
                "search": search_to_public(search),
                "avg_price_30d": stats["avg"],
                "statistics": stats,
                "listings": [
                    listing_to_public_full(l, search_id) for l in listings
                ],
            },
        )

    if method == "POST" and len(parts) >= 5 and parts[0] == "api" and parts[1] == "searches" and parts[3] == "listings":
        search_id = parse_int(parts[2])
        ad_id = parts[4]
        if not db.get_search_for_user(search_id, user_id):
            return json_response(handler, {"error": "Hittades inte."}, 404)
        body = read_json_body(handler)
        listing = db.update_listing_status(
            search_id, ad_id, seen=body.get("seen"), interesting=body.get("interesting")
        )
        if not listing:
            return json_response(handler, {"error": "Hittades inte."}, 404)
        return json_response(handler, listing_to_public_full(listing, search_id))

    if method == "POST" and len(parts) >= 4 and parts[0] == "api" and parts[1] == "searches" and parts[3] == "seen":
        search_id = parse_int(parts[2])
        if not db.get_search_for_user(search_id, user_id):
            return json_response(handler, {"error": "Hittades inte."}, 404)
        db.mark_all_seen(search_id)
        return json_response(handler, {"ok": True})

    if method == "GET" and path == "/api/notifications":
        since_id = parse_int(handler.query.get("since_id", [None])[0])
        items = db.list_notifications(user_id=user_id, limit=100, since_id=since_id)
        return json_response(
            handler,
            {
                "unread": db.unread_notification_count(user_id=user_id),
                "notifications": items,
            },
        )

    if method == "POST" and path == "/api/notifications/read":
        db.mark_notifications_read(user_id=user_id)
        return json_response(handler, {"ok": True})

    if method == "GET" and path == "/api/logs":
        return json_response(handler, db.list_recent_logs(user_id=user_id))

    if method == "POST" and path == "/api/check":
        with CHECK_LOCK:
            results = monitor.run_all_checks()
        return json_response(handler, {"results": results})

    if method == "GET" and path == "/api/statistics":
        overview = db.overview_statistics(user_id=user_id)
        return json_response(handler, overview)

    if method == "GET" and len(parts) == 4 and parts[0] == "api" and parts[1] == "searches" and parts[3] == "statistics":
        search_id = parse_int(parts[2])
        if search_id is None or not db.get_search_for_user(search_id, user_id):
            return json_response(handler, {"error": "Hittades inte."}, 404)
        return json_response(handler, db.listing_statistics(search_id))

    if method in ("GET", "PUT") and path == "/api/profile":
        if method == "GET":
            profile = db.get_profile(user_id=user_id)
            profile["quick_message"] = db.get_quick_message(user_id)
            return json_response(handler, profile)
        body = read_json_body(handler)
        email = str(body.get("email") or "").strip()
        phone = str(body.get("phone") or "").strip()
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return json_response(handler, {"error": "Ange en giltig e-postadress."}, 400)
        profile = db.set_profile(user_id, {"email": email, "phone": phone})
        # Also save quick_message if provided.
        if "quick_message" in body:
            db.set_quick_message(user_id, str(body.get("quick_message") or ""))
            profile["quick_message"] = db.get_quick_message(user_id)
        return json_response(handler, profile)

    if method in ("GET", "PUT") and path == "/api/settings":
        if method == "GET":
            value = db.get_setting("notifications", "{}")
            try:
                settings = json.loads(value)
            except ValueError:
                settings = {}
            settings.setdefault("push_notify", True)
            settings["email_enabled"] = config.EMAIL_ENABLED
            settings["sms_enabled"] = config.SMS_ENABLED
            profile = db.get_profile(user_id=user_id)
            settings["profile_email"] = profile.get("email", "")
            settings["profile_phone"] = profile.get("phone", "")
            settings["user"] = db.get_user_by_id(user_id)
            return json_response(handler, settings)
        body = read_json_body(handler)
        current = {}
        raw = db.get_setting("notifications", "{}")
        try:
            current = json.loads(raw)
        except ValueError:
            current = {}
        if "push_notify" in body:
            current["push_notify"] = bool(body["push_notify"])
        db.set_setting("notifications", json.dumps(current))
        current["email_enabled"] = config.EMAIL_ENABLED
        current["sms_enabled"] = config.SMS_ENABLED
        profile = db.get_profile(user_id=user_id)
        current["profile_email"] = profile.get("email", "")
        current["profile_phone"] = profile.get("phone", "")
        current["user"] = db.get_user_by_id(user_id)
        return json_response(handler, current)

    # Price history for a listing.
    if method == "GET" and len(parts) >= 6 and parts[0] == "api" and parts[1] == "searches" and parts[3] == "listings" and parts[5] == "history":
        search_id = parse_int(parts[2])
        ad_id = parts[4]
        if search_id is None or not db.get_search_for_user(search_id, user_id):
            return json_response(handler, {"error": "Hittades inte."}, 404)
        return json_response(handler, db.price_history(search_id, ad_id))

    # Price history aggregated for a whole search.
    if method == "GET" and len(parts) == 5 and parts[0] == "api" and parts[1] == "searches" and parts[3] == "price-history":
        search_id = parse_int(parts[2])
        if search_id is None or not db.get_search_for_user(search_id, user_id):
            return json_response(handler, {"error": "Hittades inte."}, 404)
        return json_response(handler, db.search_price_history(search_id))

    # Follow / unfollow a listing.
    if method == "POST" and len(parts) >= 6 and parts[0] == "api" and parts[1] == "searches" and parts[3] == "listings" and parts[5] == "follow":
        search_id = parse_int(parts[2])
        ad_id = parts[4]
        if search_id is None or not db.get_search_for_user(search_id, user_id):
            return json_response(handler, {"error": "Hittades inte."}, 404)
        db.follow_listing(search_id, ad_id)
        return json_response(handler, {"ok": True, "following": True})

    if method == "POST" and len(parts) >= 6 and parts[0] == "api" and parts[1] == "searches" and parts[3] == "listings" and parts[5] == "unfollow":
        search_id = parse_int(parts[2])
        ad_id = parts[4]
        if search_id is None or not db.get_search_for_user(search_id, user_id):
            return json_response(handler, {"error": "Hittades inte."}, 404)
        db.unfollow_listing(search_id, ad_id)
        return json_response(handler, {"ok": True, "following": False})

    # Set resale price on a listing (profit calculator).
    if method == "POST" and len(parts) >= 6 and parts[0] == "api" and parts[1] == "searches" and parts[3] == "listings" and parts[5] == "resale":
        search_id = parse_int(parts[2])
        ad_id = parts[4]
        if search_id is None or not db.get_search_for_user(search_id, user_id):
            return json_response(handler, {"error": "Hittades inte."}, 404)
        body = read_json_body(handler)
        resale_price = parse_int(body.get("resale_price"))
        if resale_price is not None and resale_price < 0:
            return json_response(handler, {"error": "Priset kan inte vara negativt."}, 400)
        ok = db.set_resale_price(search_id, ad_id, resale_price)
        if not ok:
            return json_response(handler, {"error": "Annonsen hittades inte."}, 404)
        listing = db.get_listing(search_id, ad_id)
        listing["resale_price"] = resale_price
        if listing.get("price") and resale_price:
            listing["profit"] = resale_price - listing["price"]
        return json_response(handler, listing_to_public_full(listing, search_id))

    # Market-value estimates from disappeared listings.
    if method == "GET" and len(parts) == 5 and parts[0] == "api" and parts[1] == "searches" and parts[3] == "market-values":
        search_id = parse_int(parts[2])
        if search_id is None or not db.get_search_for_user(search_id, user_id):
            return json_response(handler, {"error": "Hittades inte."}, 404)
        days = parse_int(handler.query.get("days", ["90"])[0], 90)
        active_stats = db.listing_statistics(search_id, days=days)
        market = db.get_market_values(search_id, days=days)
        return json_response(handler, {
            "active": {"avg": active_stats["avg"], "min": active_stats["min"], "max": active_stats["max"], "count": active_stats["count"]},
            "market": market,
        })

    return json_response(handler, {"error": "Not found"}, 404)


# --------------------------------------------------------------------------
# HTTP handler + server
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "Blocketvakten/1.0"

    def log_message(self, fmt, *args):  # keep the terminal quiet-ish
        pass

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        self.query = parse_qs(parsed.query)
        if method == "GET" and serve_static(self, parsed.path):
            return
        route(self, method, parsed.path)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")


def scheduler_loop() -> None:
    """Wake every SCHEDULER_TICK seconds and check only searches whose
    per-search interval has elapsed since their last check."""
    import backup  # lazy import so config is already loaded
    while True:
        time.sleep(config.SCHEDULER_TICK)
        try:
            with CHECK_LOCK:
                _check_due_searches()
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] Fel: {exc}")
        # Daily backup (runs outside the check lock).
        try:
            backup.run_backup_if_due()
        except Exception as exc:  # noqa: BLE001
            print(f"[backup] Fel: {exc}")


def _check_due_searches() -> None:
    """Run check_search for every active search whose interval has elapsed."""
    now = time.time()
    for search in db.list_searches():
        if not search.get("active"):
            continue
        last_checked = search.get("last_checked_at")
        if last_checked:
            try:
                last_ts = datetime.fromisoformat(last_checked).timestamp()
            except (ValueError, TypeError):
                last_ts = 0
            elapsed = now - last_ts
        else:
            elapsed = float("inf")
        interval = search.get("check_interval") or 1800
        if elapsed >= interval:
            monitor.check_search(search)


def serve() -> None:
    db.init_db()
    server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    print(f"Blocketvakten körs på http://{config.HOST}:{config.PORT}")

    if not config.DISABLE_SCHEDULER:
        thread = threading.Thread(target=scheduler_loop, daemon=True)
        thread.start()
        print(
            f"Schemaläggaren körs – kollar var {int(config.SCHEDULER_TICK)}:e sekund"
            " och kör varje bevakning enligt sitt eget intervall."
        )
    else:
        print("Schemaläggaren är avstängd – kör `python app.py --check` via cron.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStoppar.")


def run_check_once() -> None:
    db.init_db()
    print("Kör kontroll av alla bevakningar...")
    results = monitor.run_all_checks()
    for result in results:
        status = result.get("status")
        new = result.get("new", 0)
        error = result.get("error")
        line = f"  bevakning {result['search_id']}: {status}, {new} nya"
        if error:
            line += f" ({error})"
        print(line)


def main() -> None:
    parser = argparse.ArgumentParser(description="Blocketvakten")
    parser.add_argument("--check", action="store_true", help="kör en kontroll och avsluta")
    parser.add_argument("--init", action="store_true", help="skapa databasen och avsluta")
    args = parser.parse_args()

    if args.check:
        run_check_once()
    elif args.init:
        db.init_db()
        print(f"Databas skapad: {config.DB_PATH}")
    else:
        serve()


if __name__ == "__main__":
    main()
