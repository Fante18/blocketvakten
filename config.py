"""App configuration, read from environment variables with sensible defaults."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("BLOCKETVAKTEN_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("BLOCKETVAKTEN_DB", DATA_DIR / "blocketvakten.db"))
STATIC_DIR = BASE_DIR / "static"

# Postgres connection string. When set the app switches from local SQLite
# to a hosted PostgreSQL database (Railway / Render / Supabase / …).
# Most platforms set this automatically; you can also provide it manually.
DATABASE_URL = os.environ.get('DATABASE_URL', '')
# Public URL used in password-reset links. Set this to your Railway domain.
APP_URL = os.environ.get('BLOCKETVAKTEN_APP_URL', '').rstrip('/')

_default_host = "0.0.0.0" if DATABASE_URL else "127.0.0.1"
HOST = os.environ.get("BLOCKETVAKTEN_HOST", _default_host)
PORT = int(os.environ.get(
    "BLOCKETVAKTEN_PORT",
    int(os.environ.get("PORT", "8080"))  # Railway / Render sets PORT automatically
))

# How often the built-in scheduler checks active searches (seconds).
CHECK_INTERVAL = float(os.environ.get("BLOCKETVAKTEN_CHECK_INTERVAL", "60"))

# When set, the scheduler never runs automatically (use cron + `--check`).
DISABLE_SCHEDULER = os.environ.get("BLOCKETVAKTEN_DISABLE_SCHEDULER", "") == "1"

# Fetch timeout for each Blocket request (seconds).
FETCH_TIMEOUT = float(os.environ.get("BLOCKETVAKTEN_FETCH_TIMEOUT", "20"))

# A listing is flagged as a "good price" when it is this far below the
# 30-day average price (0.85 == 15% below).
GOOD_PRICE_RATIO = float(os.environ.get("BLOCKETVAKTEN_GOOD_PRICE_RATIO", "0.85"))

# --- E-mail notifications (optional) -------------------------------------
# Configure these to receive e-mail for new listings. The recipient is stored
# in the local profile and can be changed from the app; EMAIL_TO remains a
# legacy fallback for existing deployments.
SMTP_HOST = os.environ.get("BLOCKETVAKTEN_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("BLOCKETVAKTEN_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("BLOCKETVAKTEN_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("BLOCKETVAKTEN_SMTP_PASSWORD", "")
SMTP_USE_TLS = os.environ.get("BLOCKETVAKTEN_SMTP_TLS", "1") == "1"
EMAIL_FROM = os.environ.get("BLOCKETVAKTEN_EMAIL_FROM", "")
EMAIL_TO = os.environ.get("BLOCKETVAKTEN_EMAIL_TO", "")

# Brevo HTTPS API (uses port 443; recommended on cloud platforms).
BREVO_API_KEY = os.environ.get("BLOCKETVAKTEN_BREVO_API_KEY", "")
BREVO_API_URL = os.environ.get("BLOCKETVAKTEN_BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")

# SMTP transport is ready even before the user enters a profile address.
EMAIL_ENABLED = bool((BREVO_API_KEY or SMTP_HOST) and EMAIL_FROM)

# --- SMS notifications (optional, not yet active – UI + DB only) ---------
SMS_ENABLED = os.environ.get("BLOCKETVAKTEN_SMS_ENABLED", "") == "1"
SMS_API_URL = os.environ.get("BLOCKETVAKTEN_SMS_API_URL", "")
SMS_API_KEY = os.environ.get("BLOCKETVAKTEN_SMS_API_KEY", "")
SMS_FROM = os.environ.get("BLOCKETVAKTEN_SMS_FROM", "Blocketvakten")

# --- Daily backup (optional) ---------------------------------------------
# Webhook URL or file:// path where a daily gzip-compressed JSON backup is sent.
# Empty = backup is disabled.
BACKUP_URL = os.environ.get("BLOCKETVAKTEN_BACKUP_URL", "")

# UTC hour (0-23) when the daily backup runs.  Default: midnight.
BACKUP_HOUR = int(os.environ.get("BLOCKETVAKTEN_BACKUP_HOUR", "0"))

# Minimum granularity the scheduler wakes up to check per-search intervals (seconds).
SCHEDULER_TICK = float(os.environ.get("BLOCKETVAKTEN_SCHEDULER_TICK", "60"))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
