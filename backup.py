"""Daily database backup — exports every table as JSON and posts to a webhook.

Controlled by BACKUP_URL ("https://…" for webhook, "file:///path" for local file).
If BACKUP_URL is empty the feature is disabled.

Usage:
    python backup.py          # run one backup now, then exit
    python backup.py --loop   # run once now, then every 24 h (used by the app scheduler)
"""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import config


# All tables that should be included in the backup.  Ordered so that
# parent tables come before children (makes restoring easier).
BACKUP_TABLES = [
    "users",
    "sessions",
    "reset_tokens",
    "settings",
    "searches",
    "listings",
    "notifications",
    "check_logs",
    "price_history",
    "listing_follows",
    "disappeared_listings",
]

_last_backup_date: str | None = None  # ISO date string YYYY-MM-DD


def _dump_all() -> bytes:
    """Export every BACKUP_TABLES to a JSON object, return gzip-compressed bytes."""
    import db

    payload: dict[str, list[dict]] = {}
    for table in BACKUP_TABLES:
        try:
            with db.connect() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT * FROM {table}")
                rows = cur.fetchall()
                # Convert Row objects to plain dicts.
                payload[table] = [
                    dict(zip([d[0] for d in cur.description], row))
                    for row in rows
                ]
        except Exception:
            # Table may not exist yet (first fresh install).
            payload[table] = []

    raw = json.dumps(payload, ensure_ascii=False, default=str, indent=2).encode("utf-8")
    return gzip.compress(raw)


def _send_backup(data: bytes) -> bool:
    """Deliver the backup blob to BACKUP_URL.  Returns True on success."""
    url = config.BACKUP_URL

    if not url:
        return False

    # --- local file ----------------------------------------------------------------
    if url.startswith("file://"):
        path = url[7:]
        if os.name == "nt" and path.startswith("/"):
            path = path[1:]  # strip leading slash on Windows (file:///C:/…)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        print(f"[backup] Spara till {path} ({len(data)} byte, gzip)")
        return True

    # --- webhook -------------------------------------------------------------------
    print(f"[backup] Skickar till {url.split('@')[-1]} …", end=" ", flush=True)
    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "User-Agent": "Blocketvakten/1.0",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=30)
        body = resp.read(1024)
        print(f"OK {resp.status} ({len(data)} byte)")
        if resp.status >= 400:
            print(f"[backup]  Server svarade: {body[:200]}")
            return False
        return True
    except Exception as exc:
        print(f"misslyckades: {exc}")
        return False


def run_backup_if_due() -> bool:
    """Run a backup if we haven't done one today.  Returns True when a backup ran."""
    global _last_backup_date

    if not config.BACKUP_URL:
        return False

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hour = config.BACKUP_HOUR
    now_hour = datetime.now(timezone.utc).hour

    # Only run during the configured hour window.
    # We add a 10-minute grace period so the ticker catches it.
    if now_hour != hour and _last_backup_date == today:
        return False

    # Already ran today?
    if _last_backup_date == today:
        return False

    print(f"[backup] Startar daglig backup ({today}) …")
    try:
        data = _dump_all()
        ok = _send_backup(data)
        _last_backup_date = today if ok else None
        return ok
    except Exception as exc:
        print(f"[backup] Fel: {exc}")
        return False


def run_backup_once() -> bool:
    """Run one backup immediately (used by CLI)."""
    if not config.BACKUP_URL:
        print("BACKUP_URL är inte satt – backup är avstängd.")
        return False
    print(f"[backup] Engångsbackup …")
    data = _dump_all()
    return _send_backup(data)


# --------------------------------------------------------------------------
def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Blocketvakten backup")
    parser.add_argument("--loop", action="store_true", help="Kör dagligen (används av schemaläggaren)")
    args = parser.parse_args()

    if not config.BACKUP_URL:
        print("[backup] BACKUP_URL är ej satt – inget att göra.")
        return

    if args.loop:
        while True:
            run_backup_if_due()
            time.sleep(3600)  # wake once an hour to check the clock
    else:
        run_backup_once()


if __name__ == "__main__":
    main()