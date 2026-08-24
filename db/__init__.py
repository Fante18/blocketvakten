"""Database backend selector — SQLite (local) or PostgreSQL (cloud)."""

import config

# The DATABASE_URL env var triggers Postgres mode.
# Without it, the app keeps using local SQLite.
_USE_POSTGRES = bool(config.DATABASE_URL)

if _USE_POSTGRES:
    from db._postgres import *
else:
    from db._sqlite import *
