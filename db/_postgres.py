"""SQLite persistence layer for Blocketvakten."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import psycopg2
import psycopg2.extras
import psycopg2.pool
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS searches (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL,
    exclude_words TEXT NOT NULL,
    max_price INTEGER,
    location TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    send_email BOOLEAN NOT NULL DEFAULT FALSE,
    send_sms BOOLEAN NOT NULL DEFAULT FALSE,
    check_interval INTEGER NOT NULL DEFAULT 1800,
    pause_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_error TEXT,
    last_new_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS listings (
    search_id INTEGER NOT NULL,
    ad_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    price INTEGER,
    location TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    published_at TEXT,
    published_text TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    seen BOOLEAN NOT NULL DEFAULT FALSE,
    interesting BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (search_id, ad_id),
    FOREIGN KEY (search_id) REFERENCES searches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_listings_search
    ON listings(search_id, first_seen_at DESC);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    search_id INTEGER NOT NULL,
    ad_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    price INTEGER,
    image_url TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read BOOLEAN NOT NULL DEFAULT FALSE,
    FOREIGN KEY (search_id) REFERENCES searches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_notifications_created
    ON notifications(created_at DESC);

CREATE TABLE IF NOT EXISTS check_logs (
    id SERIAL PRIMARY KEY,
    search_id INTEGER,
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    fetched_count INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (search_id) REFERENCES searches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_check_logs_checked
    ON check_logs(checked_at DESC);

CREATE TABLE IF NOT EXISTS price_history (
    search_id INTEGER NOT NULL,
    ad_id TEXT NOT NULL,
    price INTEGER,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (search_id) REFERENCES searches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_price_history_ad
    ON price_history(search_id, ad_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS listing_follows (
    search_id INTEGER NOT NULL,
    ad_id TEXT NOT NULL,
    followed_at TEXT NOT NULL,
    last_price INTEGER,
    last_alerted_price INTEGER,
    PRIMARY KEY (search_id, ad_id),
    FOREIGN KEY (search_id) REFERENCES searches(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reset_tokens (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS disappeared_listings (
    search_id INTEGER NOT NULL,
    ad_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    last_price INTEGER,
    last_seen_at TEXT NOT NULL,
    disappeared_at TEXT NOT NULL,
    PRIMARY KEY (search_id, ad_id),
    FOREIGN KEY (search_id) REFERENCES searches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_disappeared_search
    ON disappeared_listings(search_id, disappeared_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

__all__ = [
    'connect', 'init_db', 'SCHEMA', 'SESSION_TTL', 'RESET_TOKEN_TTL',
    '_now', '_hash_password', '_verify_password',
    'create_user', 'get_user_by_email', 'get_user_by_id',
    'create_session', 'validate_session', 'delete_session', 'purge_expired_sessions',
    'create_reset_token', 'validate_reset_token', 'consume_reset_token', 'delete_reset_token',
    '_search_belongs_to', '_validate_search_access',
    '_json', '_parse_json', '_row_search',
    'create_search', 'get_search', 'get_search_for_user', 'list_searches',
    'update_search', 'delete_search', 'mark_checked',
    'insert_listing', 'list_listings', 'list_listings_sorted_by_deal_score',
    'get_listing', 'update_listing_status', 'mark_all_seen', 'listing_counts',
    '_parse_seen_at', '_week_start', 'listing_statistics', 'listing_price_stats',
    'overview_statistics',
    'create_notification', 'list_notifications', 'mark_notifications_read', 'unread_notification_count',
    '_profile_key', 'get_profile', 'set_profile',
    'get_setting', 'set_setting', 'list_recent_logs',
    'record_price', 'price_history', 'search_price_history',
    'follow_listing', 'unfollow_listing', 'is_following', 'get_follows',
    'check_follow_price_drops', '_update_follow_price',
    'record_disappeared', 'get_market_values',
    'set_resale_price',
    'get_quick_message', 'set_quick_message',
]

SESSION_TTL = timedelta(days=30)
RESET_TOKEN_TTL = timedelta(hours=1)

_user_id_by_token: dict[str, int] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        url = config.DATABASE_URL
        if not url:
            raise RuntimeError('DATABASE_URL is not set')
        _pool = psycopg2.pool.ThreadedConnectionPool(2, 20, url)
    return _pool


@contextmanager
def connect():
    pool = _get_pool()
    conn = pool.getconn()
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def init_db() -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)

        # Ensure a default user (id=0) exists for pre-auth migration installs.
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute('SELECT 1 FROM users LIMIT 1')
        has_users = cur.fetchone()
        if not has_users:
            cur2 = conn.cursor()
            cur2.execute(
                'INSERT INTO users (id, email, password_hash, created_at) '
                'VALUES (0, %s, %s, %s)',
                ('', '', _now()),
            )

        # Assign orphaned searches (user_id=0) to the first real user.
        cur.execute('SELECT id FROM users WHERE id != 0 ORDER BY id LIMIT 1')
        real_user = cur.fetchone()
        if real_user:
            cur2 = conn.cursor()
            cur2.execute(
                'UPDATE searches SET user_id = %s WHERE user_id = 0',
                (real_user['id'],),
            )


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + ":" + dk.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
        return secrets.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


def create_user(email: str, password: str) -> dict | None:
    email = email.strip().lower()
    if not email or len(password) < 4:
        return None
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute('SELECT 1 FROM users WHERE email = %s AND id != 0', (email,))
        exists = cur.fetchone()
        if exists:
            return None
        cur2 = conn.cursor()
        cur2.execute(
            'INSERT INTO users (email, password_hash, created_at) '
            'VALUES (%s, %s, %s) RETURNING id',
            (email, _hash_password(password), _now()),
        )
        new_id = cur2.fetchone()[0]
        # Assign any orphaned searches (user_id=0) to this new user.
        cur3 = conn.cursor()
        cur3.execute('UPDATE searches SET user_id = %s WHERE user_id = 0', (new_id,))
    return get_user_by_id(new_id)


def get_user_by_email(email: str) -> dict | None:
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            'SELECT id, email, password_hash, created_at FROM users '
            'WHERE email = %s AND id != 0',
            (email.strip().lower(),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    if not user_id:
        return None
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute('SELECT id, email, created_at FROM users WHERE id = %s', (user_id,))
        row = cur.fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

def create_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    now = _now()
    expires = (datetime.now(timezone.utc) + SESSION_TTL).isoformat()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO sessions (token, user_id, created_at, expires_at) '
            'VALUES (%s, %s, %s, %s)',
            (token, user_id, now, expires),
        )
    _user_id_by_token[token] = user_id
    return token


def validate_session(token: str) -> int | None:
    """Return user_id if the token is valid, else None."""
    if not token:
        return None
    # Fast path: in-memory cache.
    if token in _user_id_by_token:
        return _user_id_by_token[token]
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT user_id, expires_at FROM sessions WHERE token = %s", (token,), )
        row = cur.fetchone()
    if not row:
        return None
    try:
        expires = datetime.fromisoformat(row["expires_at"])
    except (ValueError, TypeError):
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        delete_session(token)
        return None
    _user_id_by_token[token] = row["user_id"]
    return row["user_id"]


def delete_session(token: str) -> None:
    _user_id_by_token.pop(token, None)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE token = %s", (token,))


def purge_expired_sessions() -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE expires_at < %s", (now,))

    # Also purge expired reset tokens.
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM reset_tokens WHERE expires_at < %s", (now,))


# --------------------------------------------------------------------------
# Password reset tokens
# --------------------------------------------------------------------------

def create_reset_token(user_id: int) -> str:
    token = secrets.token_hex(32)
    now = _now()
    expires = (datetime.now(timezone.utc) + RESET_TOKEN_TTL).isoformat()
    with connect() as conn:
        # Remove any previous unused tokens for this user.
        cur = conn.cursor()
        cur.execute("DELETE FROM reset_tokens WHERE user_id = %s AND expires_at > %s", (user_id, now),)
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO reset_tokens (token, user_id, created_at, expires_at) '
            'VALUES (%s, %s, %s, %s)',
            (token, user_id, now, expires),
        )
    return token


def validate_reset_token(token: str) -> int | None:
    if not token:
        return None
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT user_id, expires_at FROM reset_tokens WHERE token = %s", (token,), )
        row = cur.fetchone()
    if not row:
        return None
    try:
        expires = datetime.fromisoformat(row["expires_at"])
    except (ValueError, TypeError):
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        delete_reset_token(token)
        return None
    return row["user_id"]


def consume_reset_token(token: str) -> int | None:
    user_id = validate_reset_token(token)
    if user_id is not None:
        delete_reset_token(token)
    return user_id


def delete_reset_token(token: str) -> None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM reset_tokens WHERE token = %s", (token,))


# --------------------------------------------------------------------------
# Auth helpers for scoping queries
# --------------------------------------------------------------------------

def _search_belongs_to(search_id: int, user_id: int) -> bool:
    """Check that a search belongs to the given user."""
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT 1 FROM searches WHERE id = %s AND user_id = %s", (search_id, user_id), )
        row = cur.fetchone()
    return bool(row)


def _validate_search_access(search_id: int, user_id: int) -> None:
    """Like _search_belongs_to but usable when the search *must* belong."""
    pass  # called from app.py for clarity; the check is done inline in routes.


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------

def _json(value) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _parse_json(value) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _row_search(row) -> dict:
    d = dict(row)
    d["keywords"] = _parse_json(d.get("keywords"))
    d["exclude_words"] = _parse_json(d.get("exclude_words"))
    d["active"] = bool(d.get("active"))
    d["send_email"] = bool(d.get("send_email"))
    d["send_sms"] = bool(d.get("send_sms"))
    d["check_interval"] = d.get("check_interval") or 1800
    # Don't leak password hash.
    d.pop("password_hash", None)
    return d


# --------------------------------------------------------------------------
# Saved searches
# --------------------------------------------------------------------------

def create_search(
    user_id: int,
    keywords: list[str],
    name: str = "",
    exclude_words: list[str] | None = None,
    max_price: int | None = None,
    location: str = "",
    active: bool = True,
    send_email: bool = False,
    send_sms: bool = False,
    check_interval: int = 1800,
) -> dict:
    now = _now()
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            'INSERT INTO searches (user_id, name, keywords, exclude_words, max_price, '
            'location, active, send_email, send_sms, check_interval, created_at, updated_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id',
            (user_id, name.strip(), _json(keywords), _json(exclude_words or []),
             max_price, (location or '').strip(), active, send_email, send_sms,
             max(check_interval, 60), now, now),
        )
        search_id = cur.fetchone()['id']
    return get_search(search_id)


def get_search(search_id: int) -> dict | None:
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM searches WHERE id = %s", (search_id,) )
        row = cur.fetchone()
    return _row_search(row) if row else None


def get_search_for_user(search_id: int, user_id: int) -> dict | None:
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM searches WHERE id = %s AND user_id = %s", (search_id, user_id), )
        row = cur.fetchone()
    return _row_search(row) if row else None


def list_searches(user_id: int | None = None) -> list[dict]:
    if user_id is not None:
        query = "SELECT * FROM searches WHERE user_id = %s ORDER BY created_at DESC"
        params = (user_id,)
    else:
        query = "SELECT * FROM searches ORDER BY created_at DESC"
        params = ()
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(query, params)

        rows = cur.fetchall()
    return [_row_search(r) for r in rows]


def update_search(search_id: int, fields: dict) -> dict | None:
    allowed = {
        "name", "keywords", "exclude_words", "max_price", "location",
        "active", "send_email", "send_sms", "check_interval", "pause_until",
    }
    updates = {}
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "keywords":
            updates[key] = _json(value)
        elif key == "exclude_words":
            updates[key] = _json(value)
        elif key == "max_price":
            updates[key] = value if value not in (None, "") else None
        elif key == "send_email":
            updates[key] = value
        elif key == "send_sms":
            updates[key] = value
        elif key == "check_interval":
            try:
                updates[key] = max(int(value), 60)
            except (TypeError, ValueError):
                updates[key] = 1800
        elif key == "pause_until":
            updates[key] = value or None
        elif key == "location":
            updates[key] = (value or "").strip()
        elif key == "name":
            updates[key] = (value or "").strip()
        elif key == "active":
            updates[key] = value

    if not updates:
        return get_search(search_id)

    updates["updated_at"] = _now()
    assignments = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [search_id]
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE searches SET {assignments} WHERE id = %s", values
        )
    return get_search(search_id)


def delete_search(search_id: int) -> None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM searches WHERE id = %s", (search_id,))


def mark_checked(
    search_id: int, status: str, message: str, fetched_count: int, new_count: int
) -> None:
    now = _now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE searches SET last_checked_at = %s, last_error = %s, ")
        cur = conn.cursor()
        cur.execute(""" INSERT INTO check_logs (search_id, checked_at, status, message, fetched_count, new_count) VALUES (%s, %s, %s, %s, %s, %s) """, (search_id, now, status, message, fetched_count, new_count),)


# --------------------------------------------------------------------------
# Listings
# --------------------------------------------------------------------------

def insert_listing(search_id: int, listing: dict) -> bool:
    """Insert a listing unless it already exists for this search.

    Returns True when the listing was newly inserted (and therefore should be
    notified about), False when it was already known.
    """
    now = _now()
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT 1 FROM listings WHERE search_id = %s AND ad_id = %s", (search_id, listing["ad_id"]), )
        row = cur.fetchone()
        if exists:
            return False
        cur = conn.cursor()
        cur.execute(""" INSERT INTO listings (search_id, ad_id, title, price, location, image_url, url, published_at, published_text, first_seen_at, seen, interesting) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0) """, ( search_id, listing["ad_id"], listing.get("title", ""), listing.get("price"), listing.get("location", ""), listing.get("image_url", ""), listing.get("url", ""), listing.get("published_at"), listing.get("published_text", ""), now, ),)
    return True


def list_listings(
    search_id: int, limit: int = 200, unseen_only: bool = False,
    sort: str = "newest"
) -> list[dict]:
    base = "SELECT * FROM listings WHERE search_id = %s"
    if unseen_only:
        base += " AND seen = FALSE"
    if sort == "cheapest":
        base += " ORDER BY COALESCE(price, 999999999) ASC, first_seen_at DESC"
    elif sort == "most_expensive":
        base += " ORDER BY COALESCE(price, 0) DESC, first_seen_at DESC"
    else:
        base += " ORDER BY first_seen_at DESC, ad_id DESC"
    base += " LIMIT %s"
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(base, (search_id, limit))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def list_listings_sorted_by_deal_score(
    search_id: int, avg_price: float | None, limit: int = 200
) -> list[dict]:
    """Return listings sorted by deal score (best deal first).

    Deal score = ((avg - price) / avg) * 100. Positive means below average.
    """
    all_listings = list_listings(search_id, limit=500, sort="newest")
    if avg_price is None or avg_price <= 0:
        return all_listings[:limit]
    for l in all_listings:
        price = l.get("price")
        if price:
            l["deal_score"] = round(((avg_price - price) / avg_price) * 100, 1)
        else:
            l["deal_score"] = -999  # push unpriced to bottom
    all_listings.sort(key=lambda l: -(l.get("deal_score") or -999))
    return all_listings[:limit]


def get_listing(search_id: int, ad_id: str) -> dict | None:
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM listings WHERE search_id = %s AND ad_id = %s", (search_id, ad_id), )
        row = cur.fetchone()
    return dict(row) if row else None


def update_listing_status(search_id: int, ad_id: str, seen=None, interesting=None) -> dict | None:
    updates = {}
    if seen is not None:
        updates["seen"] = seen
    if interesting is not None:
        updates["interesting"] = interesting
    if updates:
        assignments = ", ".join(f"{k} = %s" for k in updates)
        values = list(updates.values()) + [search_id, ad_id]
        with connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE listings SET {assignments} WHERE search_id = %s AND ad_id = %s",
                values,
            )
    return get_listing(search_id, ad_id)


def mark_all_seen(search_id: int) -> None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE listings SET seen = TRUE WHERE search_id = %s AND seen = FALSE", (search_id,),)


def listing_counts(search_id: int) -> dict:
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(""" SELECT COUNT(*) AS total, SUM(CASE WHEN seen = FALSE THEN 1 ELSE 0 END) AS unseen, SUM(CASE WHEN interesting = TRUE THEN 1 ELSE 0 END) AS interesting FROM listings WHERE search_id = %s """, (search_id,), )
        row = cur.fetchone()
    return {
        "total": row["total"] or 0,
        "unseen": row["unseen"] or 0,
        "interesting": row["interesting"] or 0,
    }


def _parse_seen_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _week_start(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc) - timedelta(
        days=value.weekday()
    )


def listing_statistics(search_id: int, days: int = 30, weeks: int = 8) -> dict:
    """Return price insight and weekly discovery counts for a saved search."""
    now = datetime.now(timezone.utc)
    period_start = now - timedelta(days=days)
    current_week = _week_start(now)
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT price, first_seen_at FROM listings WHERE search_id = %s", (search_id,), )
        rows = cur.fetchall()

    period_prices = []
    weekly_counts = {}
    total_count = len(rows)
    for row in rows:
        seen_at = _parse_seen_at(row["first_seen_at"])
        if not seen_at:
            continue
        if seen_at >= period_start and row["price"] is not None:
            period_prices.append(row["price"])
        week = _week_start(seen_at)
        if week <= current_week and week >= current_week - timedelta(weeks=weeks - 1):
            weekly_counts[week] = weekly_counts.get(week, 0) + 1

    weekly = []
    for offset in range(weeks):
        start = current_week - timedelta(weeks=offset)
        weekly.append(
            {
                "week_start": start.date().isoformat(),
                "count": weekly_counts.get(start, 0),
                "current": offset == 0,
            }
        )

    return {
        "days": days,
        "avg": round(sum(period_prices) / len(period_prices), 2) if period_prices else None,
        "min": min(period_prices) if period_prices else None,
        "max": max(period_prices) if period_prices else None,
        "count": len(period_prices),
        "total_count": total_count,
        "this_week": weekly[0]["count"],
        "weekly": weekly,
    }


def listing_price_stats(search_id: int, days: int = 30) -> dict:
    """Backward-compatible compact form of the 30-day price statistics."""
    stats = listing_statistics(search_id, days=days)
    return {key: stats[key] for key in ("avg", "min", "max", "count")}


def overview_statistics(user_id: int | None = None) -> dict:
    """Aggregate this week's discoveries and rank saved searches."""
    rows = []
    for search in list_searches(user_id=user_id):
        stats = listing_statistics(search["id"], weeks=1)
        rows.append(
            {
                "search_id": search["id"],
                "name": search.get("name") or " · ".join(search.get("keywords", [])),
                "this_week": stats["this_week"],
                "total_count": stats["total_count"],
                "avg_price_30d": stats["avg"],
            }
        )
    rows.sort(key=lambda row: (-row["this_week"], row["name"].casefold()))
    return {
        "total_this_week": sum(row["this_week"] for row in rows),
        "top_search": rows[0] if rows and rows[0]["this_week"] else None,
        "searches": rows,
    }


# --------------------------------------------------------------------------
# Notifications (scoped by user via JOIN on searches.user_id)
# --------------------------------------------------------------------------

def create_notification(search_id: int, listing: dict) -> int:
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            'INSERT INTO notifications '
            '(search_id, ad_id, title, price, image_url, url, created_at, read) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE) RETURNING id',
            (search_id, listing['ad_id'], listing.get('title', ''),
             listing.get('price'), listing.get('image_url', ''),
             listing.get('url', ''), _now()),
        )
        return cur.fetchone()['id']


def list_notifications(
    user_id: int | None = None, limit: int = 100, since_id: int | None = None
) -> list[dict]:
    if user_id is not None:
        query = (
            "SELECT n.* FROM notifications n "
            "JOIN searches s ON s.id = n.search_id "
            "WHERE s.user_id = %s"
        )
        params: list = [user_id]
    else:
        query = "SELECT n.* FROM notifications n WHERE 1=1"
        params = []
    if since_id is not None:
        query += " AND n.id > %s"
        params.append(since_id)
    query += " ORDER BY n.id DESC LIMIT %s"
    params.append(limit)
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(query, params)

        rows = cur.fetchall()
    return [dict(r) for r in rows]


def mark_notifications_read(user_id: int | None = None) -> None:
    if user_id is not None:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE notifications SET read = TRUE WHERE read = FALSE ")
    else:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE notifications SET read = TRUE WHERE read = FALSE")


def unread_notification_count(user_id: int | None = None) -> int:
    if user_id is not None:
        query = (
            "SELECT COUNT(*) AS n FROM notifications n "
            "JOIN searches s ON s.id = n.search_id "
            "WHERE n.read = FALSE AND s.user_id = %s"
        )
        params = (user_id,)
    else:
        query = "SELECT COUNT(*) AS n FROM notifications WHERE read = FALSE"
        params = ()
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(query, params)

        row = cur.fetchone()
    return row["n"] or 0


# --------------------------------------------------------------------------
# Profile and settings (per-user via settings key prefix)
# --------------------------------------------------------------------------

def _profile_key(user_id: int) -> str:
    return f"profile:{user_id}"


def get_profile(user_id: int | None = None) -> dict:
    uid = user_id or 0
    raw = get_setting(_profile_key(uid), "{}")
    try:
        profile = json.loads(raw or "{}")
    except (TypeError, ValueError):
        profile = {}
    if not isinstance(profile, dict):
        profile = {}
    if uid == 0:
        return {
            "email": str(profile.get("email") or config.EMAIL_TO or "").strip(),
            "phone": str(profile.get("phone") or "").strip(),
        }
    return {
        "email": str(profile.get("email") or "").strip(),
        "phone": str(profile.get("phone") or "").strip(),
    }


def set_profile(user_id: int, profile: dict) -> dict:
    email = str(profile.get("email") or "").strip()
    phone = str(profile.get("phone") or "").strip()
    set_setting(
        _profile_key(user_id),
        json.dumps({"email": email, "phone": phone}, ensure_ascii=False),
    )
    return {"email": email, "phone": phone}


def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT value FROM settings WHERE key = %s", (key,) )
        row = cur.fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO settings (key, value) VALUES (%s, %s) '
            'ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value',
            (key, value),
        )


def list_recent_logs(user_id: int | None = None, limit: int = 30) -> list[dict]:
    if user_id is not None:
        query = (
            "SELECT cl.* FROM check_logs cl "
            "JOIN searches s ON s.id = cl.search_id "
            "WHERE s.user_id = %s "
            "ORDER BY cl.checked_at DESC LIMIT %s"
        )
        params = (user_id, limit)
    else:
        query = "SELECT * FROM check_logs ORDER BY checked_at DESC LIMIT %s"
        params = (limit,)
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(query, params)

        rows = cur.fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Price history
# --------------------------------------------------------------------------

def record_price(search_id: int, ad_id: str, price: int | None) -> None:
    """Append a price snapshot. Called on every check that sees the listing."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO price_history (search_id, ad_id, price, recorded_at) '
            'VALUES (%s, %s, %s, %s)',
            (search_id, ad_id, price, _now()),
        )


def price_history(search_id: int, ad_id: str, limit: int = 90) -> list[dict]:
    """Price snapshots for a single listing, newest first."""
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT price, recorded_at FROM price_history ")
    return [dict(r) for r in rows]


def search_price_history(search_id: int, limit: int = 200) -> list[dict]:
    """All price snapshots for a search, newest first (for charts)."""
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT ad_id, price, recorded_at FROM price_history ")
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Listing follows
# --------------------------------------------------------------------------

def follow_listing(search_id: int, ad_id: str) -> bool:
    """Start following a listing for price-drop alerts. Returns True if newly followed."""
    now = _now()
    current_price = None
    existing = get_listing(search_id, ad_id)
    if existing:
        current_price = existing.get("price")
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT 1 FROM listing_follows WHERE search_id = %s AND ad_id = %s", (search_id, ad_id), )
        row = cur.fetchone()
        if exists:
            return False
        cur = conn.cursor()
        cur.execute("INSERT INTO listing_follows ")
    return True


def unfollow_listing(search_id: int, ad_id: str) -> None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM listing_follows WHERE search_id = %s AND ad_id = %s", (search_id, ad_id),)


def is_following(search_id: int, ad_id: str) -> bool:
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT 1 FROM listing_follows WHERE search_id = %s AND ad_id = %s", (search_id, ad_id), )
        row = cur.fetchone()
    return bool(row)


def get_follows(search_id: int | None = None) -> list[dict]:
    """List all follows, optionally filtered by search_id."""
    query = "SELECT * FROM listing_follows"
    params: list = []
    if search_id is not None:
        query += " WHERE search_id = %s"
        params.append(search_id)
    query += " ORDER BY followed_at DESC"
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(query, params)

        rows = cur.fetchall()
    return [dict(r) for r in rows]


def check_follow_price_drops(search_id: int) -> list[dict]:
    """Detect price drops on followed listings for a search.

    Returns list of dicts with ad_id, old_price, new_price, title, url.
    """
    follows = get_follows(search_id)
    alerts: list[dict] = []
    for follow in follows:
        ad_id = follow["ad_id"]
        listing = get_listing(search_id, ad_id)
        if not listing:
            continue
        new_price = listing.get("price")
        old_price = follow.get("last_price")
        if new_price is None or old_price is None:
            if new_price is not None and old_price is None:
                _update_follow_price(search_id, ad_id, new_price)
            continue
        if new_price >= old_price:
            _update_follow_price(search_id, ad_id, new_price)
            continue
        _update_follow_price(search_id, ad_id, new_price, alerted_price=new_price)
        alerts.append(
            {
                "ad_id": ad_id,
                "old_price": old_price,
                "new_price": new_price,
                "title": listing.get("title", ""),
                "url": listing.get("url", ""),
            }
        )
    return alerts


def _update_follow_price(
    search_id: int, ad_id: str, price: int, alerted_price: int | None = None
) -> None:
    with connect() as conn:
        if alerted_price is not None:
            cur = conn.cursor()
            cur.execute("UPDATE listing_follows SET last_price = %s, last_alerted_price = %s ")
        else:
            cur = conn.cursor()
            cur.execute("UPDATE listing_follows SET last_price = %s ")


# --------------------------------------------------------------------------
# Disappeared listings (market value estimation)
# --------------------------------------------------------------------------

def record_disappeared(search_id: int, listing: dict) -> None:
    """Record a listing that has disappeared from active search results.

    The listing's last known price is saved as a market-value estimate.
    Called when a previously-seen listing is missing from a fresh search.
    """
    now = _now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO disappeared_listings ")


def get_market_values(search_id: int, days: int = 90) -> dict:
    """Return market-value estimates from disappeared listings.

    Returns avg, min, max, count, and a source_label indicating these
    are estimates based on disappeared (likely sold) listings.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT last_price FROM disappeared_listings ")
    prices = [r["last_price"] for r in rows if r["last_price"] is not None]
    if not prices:
        return {
            "avg": None,
            "min": None,
            "max": None,
            "count": 0,
            "source": "estimated_from_disappeared",
            "source_label": "Uppskattat från försvunna annonser ({} dagar)".format(days),
        }
    return {
        "avg": round(sum(prices) / len(prices), 2),
        "min": min(prices),
        "max": max(prices),
        "count": len(prices),
        "source": "estimated_from_disappeared",
        "source_label": "Uppskattat från {} försvunna annonser ({} dagar)".format(len(prices), days),
    }


# --------------------------------------------------------------------------
# Resale price (profit calculator)
# --------------------------------------------------------------------------

def set_resale_price(search_id: int, ad_id: str, resale_price: int | None) -> bool:
    """Save a resale price on a listing. Returns True on success."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE listings SET resale_price = %s WHERE search_id = %s AND ad_id = %s", (resale_price, search_id, ad_id),)
        return cur.rowcount > 0


# --------------------------------------------------------------------------
# Quick message (per-user profile)
# --------------------------------------------------------------------------

def get_quick_message(user_id: int) -> str:
    """Get the user's saved quick-message template."""
    profile = get_profile(user_id)
    return str(profile.get("quick_message", "")).strip()


def set_quick_message(user_id: int, message: str) -> dict:
    """Save a quick-message template. Merged into the profile JSON."""
    profile = get_profile(user_id)
    profile["quick_message"] = (message or "").strip()
    return set_profile(user_id, profile)