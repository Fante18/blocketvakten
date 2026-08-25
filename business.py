"""User-scoped resale business data and analytics.

The existing listing tables are intentionally left compatible. New data lives in
small feature tables so deployment migrations are additive and safe for existing
SQLite and PostgreSQL databases.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import db
import profit

IS_PG = bool(getattr(db, "_USE_POSTGRES", False))


def _sql(query: str) -> str:
    return query.replace("?", "%s") if IS_PG else query


def _execute(conn, query: str, params=(), fetch=None):
    if IS_PG:
        import psycopg2.extras
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor if fetch else None)
        cursor.execute(_sql(query), params)
    else:
        cursor = conn.execute(query, params)
    if fetch == "one":
        row = cursor.fetchone()
        return dict(row) if row else None
    if fetch == "all":
        return [dict(row) for row in cursor.fetchall()]
    return cursor


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bool(value) -> bool:
    return bool(value)


def init_db() -> None:
    """Create feature tables; safe to call on every startup."""
    id_type = "SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    bool_type = "BOOLEAN" if IS_PG else "INTEGER"
    with db.connect() as conn:
        _execute(conn, f"""
            CREATE TABLE IF NOT EXISTS listing_finance (
                search_id INTEGER NOT NULL,
                ad_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                purchase_price REAL,
                expected_resale_price REAL,
                transport_cost REAL NOT NULL DEFAULT 0,
                repair_cost REAL NOT NULL DEFAULT 0,
                selling_fee REAL NOT NULL DEFAULT 0,
                other_cost REAL NOT NULL DEFAULT 0,
                labor_cost REAL NOT NULL DEFAULT 0,
                actual_sale_price REAL,
                actual_transport_cost REAL,
                actual_repair_cost REAL,
                actual_selling_fee REAL,
                actual_other_cost REAL,
                status TEXT NOT NULL DEFAULT 'new',
                contacted_at TEXT,
                bought_at TEXT,
                sold_at TEXT,
                notes TEXT NOT NULL DEFAULT '',
                resale_url TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (search_id, ad_id)
            )
        """)
        _execute(conn, f"""
            CREATE TABLE IF NOT EXISTS listing_reminders (
                id {id_type},
                user_id INTEGER NOT NULL,
                search_id INTEGER NOT NULL,
                ad_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                due_at TEXT,
                done {bool_type} NOT NULL DEFAULT {'FALSE' if IS_PG else '0'},
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        _execute(conn, f"""
            CREATE TABLE IF NOT EXISTS price_drop_settings (
                search_id INTEGER NOT NULL,
                ad_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                enabled {bool_type} NOT NULL DEFAULT {'TRUE' if IS_PG else '1'},
                send_email {bool_type} NOT NULL DEFAULT {'TRUE' if IS_PG else '1'},
                send_sms {bool_type} NOT NULL DEFAULT {'FALSE' if IS_PG else '0'},
                min_drop_amount REAL NOT NULL DEFAULT 500,
                min_drop_pct REAL NOT NULL DEFAULT 5,
                last_price REAL,
                last_alerted_old REAL,
                last_alerted_new REAL,
                PRIMARY KEY (search_id, ad_id)
            )
        """)
        _execute(conn, f"""
            CREATE TABLE IF NOT EXISTS price_drop_events (
                id {id_type},
                search_id INTEGER NOT NULL,
                ad_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                old_price REAL NOT NULL,
                new_price REAL NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (search_id, ad_id, old_price, new_price)
            )
        """)


DEFAULT_FINANCE = {
    "purchase_price": None,
    "expected_resale_price": None,
    "transport_cost": 0,
    "repair_cost": 0,
    "selling_fee": 0,
    "other_cost": 0,
    "labor_cost": 0,
    "actual_sale_price": None,
    "actual_transport_cost": None,
    "actual_repair_cost": None,
    "actual_selling_fee": None,
    "actual_other_cost": None,
    "status": "new",
    "contacted_at": None,
    "bought_at": None,
    "sold_at": None,
    "notes": "",
    "resale_url": "",
    "category": "",
}


def get_finance(search_id: int, ad_id: str) -> dict:
    with db.connect() as conn:
        row = _execute(
            conn,
            "SELECT * FROM listing_finance WHERE search_id = ? AND ad_id = ?",
            (search_id, ad_id),
            "one",
        )
    result = dict(DEFAULT_FINANCE)
    if row:
        result.update(row)
    result["search_id"] = search_id
    result["ad_id"] = ad_id
    return result


def save_finance(search_id: int, ad_id: str, user_id: int, fields: dict) -> dict:
    current = get_finance(search_id, ad_id)
    merged = dict(current)
    allowed = set(DEFAULT_FINANCE)
    for key in allowed:
        if key in fields:
            value = fields[key]
            if key in {"status", "notes", "resale_url", "category"}:
                merged[key] = str(value or "").strip()
            else:
                merged[key] = value if value not in ("", None) else None
    merged["search_id"] = search_id
    merged["ad_id"] = ad_id
    merged["user_id"] = user_id
    merged["updated_at"] = _now()
    columns = ["search_id", "ad_id", "user_id"] + list(DEFAULT_FINANCE) + ["updated_at"]
    values = [merged.get(column) for column in columns]
    placeholders = ", ".join("%s" if IS_PG else "?" for _ in columns)
    updates = ", ".join(
        f"{column} = EXCLUDED.{column}" if IS_PG else f"{column} = excluded.{column}"
        for column in columns[2:]
    )
    query = (
        f"INSERT INTO listing_finance ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT (search_id, ad_id) DO UPDATE SET {updates}"
    )
    with db.connect() as conn:
        _execute(conn, query, values)
    return get_finance(search_id, ad_id)


def set_status(search_id: int, ad_id: str, user_id: int, status: str) -> dict:
    current = get_finance(search_id, ad_id)
    fields = {"status": status}
    if status == "contacted" and not current.get("contacted_at"):
        fields["contacted_at"] = _now()
    if status == "bought" and not current.get("bought_at"):
        fields["bought_at"] = _now()
    if status == "sold" and not current.get("sold_at"):
        fields["sold_at"] = _now()
    return save_finance(search_id, ad_id, user_id, fields)


def enrich_listing(search_id: int, listing: dict) -> dict:
    finance = get_finance(search_id, listing["ad_id"])
    result = dict(listing)
    result.update(finance)
    stats = db.listing_statistics(search_id)
    market = db.get_market_values(search_id)
    reference = market if market.get("count", 0) >= 3 else {"avg": stats.get("avg"), "count": stats.get("count", 0)}
    result.update(profit.enrich_listing(result, reference.get("avg"), reference.get("count", 0)))
    result["market_source"] = market.get("source_label") if market.get("count") else "Aktiva annonser"
    return result


def list_inventory(user_id: int, status: str | None = None) -> list[dict]:
    query = "SELECT * FROM listing_finance WHERE user_id = ?"
    params = [user_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY updated_at DESC"
    with db.connect() as conn:
        rows = _execute(conn, query, params, "all")
    result = []
    for row in rows:
        listing = db.get_listing(row["search_id"], row["ad_id"])
        if listing:
            result.append(enrich_listing(row["search_id"], listing))
    return result


def save_reminder(user_id: int, search_id: int, ad_id: str, fields: dict) -> dict:
    values = (
        user_id, search_id, ad_id, str(fields.get("kind") or "Följ upp"),
        fields.get("due_at"), False, str(fields.get("note") or ""), _now()
    )
    with db.connect() as conn:
        cur = _execute(conn, """
            INSERT INTO listing_reminders
                (user_id, search_id, ad_id, kind, due_at, done, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, values)
        if IS_PG:
            row = _execute(conn, "SELECT * FROM listing_reminders WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,), "one")
        else:
            row = _execute(conn, "SELECT * FROM listing_reminders WHERE id = ?", (cur.lastrowid,), "one")
    return row or {}


def list_reminders(user_id: int, include_done: bool = False) -> list[dict]:
    query = "SELECT * FROM listing_reminders WHERE user_id = ?"
    params = [user_id]
    if not include_done:
        query += " AND done = " + ("FALSE" if IS_PG else "0")
    query += " ORDER BY COALESCE(due_at, created_at) ASC"
    with db.connect() as conn:
        return _execute(conn, query, params, "all")


def set_reminder_done(user_id: int, reminder_id: int, done: bool) -> bool:
    with db.connect() as conn:
        cur = _execute(conn, "UPDATE listing_reminders SET done = ? WHERE id = ? AND user_id = ?", (done, reminder_id, user_id))
        return cur.rowcount > 0


def configure_price_drop(user_id: int, search_id: int, ad_id: str, fields: dict) -> dict:
    current = {
        "enabled": True, "send_email": True, "send_sms": False,
        "min_drop_amount": 500, "min_drop_pct": 5,
        "last_price": None, "last_alerted_old": None, "last_alerted_new": None,
    }
    with db.connect() as conn:
        row = _execute(conn, "SELECT * FROM price_drop_settings WHERE search_id = ? AND ad_id = ? AND user_id = ?", (search_id, ad_id, user_id), "one")
    if row:
        current.update(row)
    for key in current:
        if key in fields and fields[key] is not None:
            current[key] = fields[key]
    values = [search_id, ad_id, user_id, current["enabled"], current["send_email"], current["send_sms"], current["min_drop_amount"], current["min_drop_pct"], current.get("last_price"), current.get("last_alerted_old"), current.get("last_alerted_new")]
    columns = ["search_id", "ad_id", "user_id", "enabled", "send_email", "send_sms", "min_drop_amount", "min_drop_pct", "last_price", "last_alerted_old", "last_alerted_new"]
    placeholders = ", ".join("%s" if IS_PG else "?" for _ in columns)
    updates = ", ".join(f"{c} = {'EXCLUDED' if IS_PG else 'excluded'}.{c}" for c in columns[2:])
    with db.connect() as conn:
        _execute(conn, f"INSERT INTO price_drop_settings ({', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT (search_id, ad_id) DO UPDATE SET {updates}", values)
    return get_price_drop_settings(user_id, search_id, ad_id)


def has_price_drop_setting(search_id: int, ad_id: str) -> bool:
    """Return whether the listing uses the new configurable alert system."""
    with db.connect() as conn:
        row = _execute(
            conn,
            "SELECT 1 FROM price_drop_settings WHERE search_id = ? AND ad_id = ?",
            (search_id, ad_id),
            "one",
        )
    return bool(row)


def get_price_drop_settings(user_id: int, search_id: int, ad_id: str) -> dict:
    with db.connect() as conn:
        row = _execute(conn, "SELECT * FROM price_drop_settings WHERE user_id = ? AND search_id = ? AND ad_id = ?", (user_id, search_id, ad_id), "one")
    if row:
        return row
    return {"enabled": False, "send_email": True, "send_sms": False, "min_drop_amount": 500, "min_drop_pct": 5, "search_id": search_id, "ad_id": ad_id}


def check_price_drops(search_id: int, user_id: int) -> list[dict]:
    with db.connect() as conn:
        settings = _execute(conn, "SELECT * FROM price_drop_settings WHERE search_id = ? AND user_id = ? AND enabled = " + ("TRUE" if IS_PG else "1"), (search_id, user_id), "all")
    alerts = []
    for setting in settings:
        listing = db.get_listing(search_id, setting["ad_id"])
        if not listing or listing.get("price") is None:
            continue
        new_price = float(listing["price"])
        old_price = setting.get("last_price")
        if old_price is None:
            configure_price_drop(user_id, search_id, setting["ad_id"], {"last_price": new_price})
            continue
        if new_price >= float(old_price):
            configure_price_drop(user_id, search_id, setting["ad_id"], {"last_price": new_price})
            continue
        drop_amount = float(old_price) - new_price
        drop_pct = drop_amount / float(old_price) * 100 if old_price else 0
        if drop_amount < float(setting.get("min_drop_amount") or 0) and drop_pct < float(setting.get("min_drop_pct") or 0):
            configure_price_drop(user_id, search_id, setting["ad_id"], {"last_price": new_price})
            continue
        event = (search_id, setting["ad_id"], user_id, old_price, new_price, _now())
        inserted = False
        try:
            with db.connect() as conn:
                _execute(conn, "INSERT INTO price_drop_events (search_id, ad_id, user_id, old_price, new_price, created_at) VALUES (?, ?, ?, ?, ?, ?)", event)
                inserted = True
        except Exception:
            inserted = False
        configure_price_drop(user_id, search_id, setting["ad_id"], {"last_price": new_price, "last_alerted_old": old_price, "last_alerted_new": new_price})
        if inserted:
            alert = dict(listing)
            alert.update({"old_price": old_price, "new_price": new_price, "drop_amount": round(drop_amount, 2), "drop_pct": round(drop_pct, 1), "send_email": setting.get("send_email", True), "send_sms": setting.get("send_sms", False)})
            try:
                db.create_notification(search_id, {**listing, "title": f"Prissänkning: {listing.get('title', '')}"})
            except Exception:
                pass
            alerts.append(alert)
    return alerts


def _listing_in_period(listing: dict, cutoff: datetime) -> bool:
    value = listing.get("first_seen_at")
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed >= cutoff
    except (TypeError, ValueError):
        return False


def _finance_for_search(user_id: int, search_id: int) -> list[dict]:
    with db.connect() as conn:
        return _execute(conn, "SELECT * FROM listing_finance WHERE user_id = ? AND search_id = ?", (user_id, search_id), "all")


def overview_statistics(user_id: int, days: int = 30) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    searches = db.list_searches(user_id=user_id)
    by_search = []
    by_category = {}
    total = {"found": 0, "contacted": 0, "bought": 0, "sold": 0, "invested": 0, "actual_profit": 0, "expected_profit": 0, "bound_capital": 0, "price_drops": 0}
    with db.connect() as conn:
        drop_row = _execute(
            conn,
            "SELECT COUNT(*) AS count FROM price_drop_events WHERE user_id = ? AND created_at >= ?",
            (user_id, cutoff.isoformat()),
            "one",
        )
    total["price_drops"] = int((drop_row or {}).get("count") or 0)
    for search in searches:
        listings = [
            listing for listing in db.list_listings(search["id"], limit=5000)
            if _listing_in_period(listing, cutoff)
        ]
        finances = {row["ad_id"]: row for row in _finance_for_search(user_id, search["id"])}
        summary = {"search_id": search["id"], "name": search.get("name") or " · ".join(search.get("keywords", [])), "found": len(listings), "contacted": 0, "bought": 0, "sold": 0, "invested": 0, "actual_profit": 0, "expected_profit": 0, "bound_capital": 0, "avg_days_to_sell": None, "sell_rate": 0}
        sale_days = []
        for listing in listings:
            total["found"] += 1
            finance = finances.get(listing["ad_id"])
            if not finance:
                continue
            enriched = profit.enrich_listing({**listing, **finance}, None, 0)
            status = finance.get("status")
            if finance.get("contacted_at") or status in {"contacted", "bought", "under_repair", "ready", "published", "sold"}:
                summary["contacted"] += 1; total["contacted"] += 1
            if finance.get("bought_at") or status in {"bought", "under_repair", "ready", "published", "sold"}:
                summary["bought"] += 1; total["bought"] += 1
                summary["invested"] += float(finance.get("purchase_price") or listing.get("price") or 0)
                total["invested"] += float(finance.get("purchase_price") or listing.get("price") or 0)
            if status == "sold" or finance.get("actual_sale_price") is not None:
                summary["sold"] += 1; total["sold"] += 1
                summary["actual_profit"] += float(enriched.get("actual_profit") or 0)
                total["actual_profit"] += float(enriched.get("actual_profit") or 0)
                if enriched.get("actual_days_to_sell") is not None: sale_days.append(enriched["actual_days_to_sell"])
            if enriched.get("expected_profit") is not None:
                summary["expected_profit"] += float(enriched["expected_profit"])
                total["expected_profit"] += float(enriched["expected_profit"])
            if status in {"bought", "under_repair", "ready", "published"}:
                summary["bound_capital"] += float(finance.get("purchase_price") or listing.get("price") or 0)
                total["bound_capital"] += float(finance.get("purchase_price") or listing.get("price") or 0)
            category = finance.get("category") or "Okategoriserad"
            item = by_category.setdefault(category, {"category": category, "found": 0, "bought": 0, "sold": 0, "actual_profit": 0, "expected_profit": 0})
            item["found"] += 1
            item["bought"] += 1 if status in {"bought", "under_repair", "ready", "published", "sold"} else 0
            item["sold"] += 1 if status == "sold" or finance.get("actual_sale_price") is not None else 0
            item["actual_profit"] += float(enriched.get("actual_profit") or 0)
            item["expected_profit"] += float(enriched.get("expected_profit") or 0)
        summary["avg_days_to_sell"] = round(sum(sale_days) / len(sale_days), 1) if sale_days else None
        summary["sell_rate"] = round(summary["sold"] / summary["bought"] * 100, 1) if summary["bought"] else 0
        by_search.append(summary)
    for row in by_search:
        row["actual_profit"] = round(row["actual_profit"], 2); row["expected_profit"] = round(row["expected_profit"], 2)
    for row in by_category.values():
        row["actual_profit"] = round(row["actual_profit"], 2); row["expected_profit"] = round(row["expected_profit"], 2)
    by_search.sort(key=lambda row: (-row["actual_profit"], -row["expected_profit"]))
    categories = sorted(by_category.values(), key=lambda row: (-row["actual_profit"], -row["expected_profit"]))
    total["actual_profit"] = round(total["actual_profit"], 2); total["expected_profit"] = round(total["expected_profit"], 2)
    total["avg_profit_per_sale"] = round(total["actual_profit"] / total["sold"], 2) if total["sold"] else None
    return {"days": days, "totals": total, "searches": by_search, "categories": categories, "top_search": by_search[0] if by_search else None, "top_category": categories[0] if categories else None}
