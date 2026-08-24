"""Database backend selector — SQLite (local) or PostgreSQL (cloud)."""

import config

_USE_POSTGRES = False

if config.DATABASE_URL:
    try:
        from db._postgres import *
        _USE_POSTGRES = True
    except ImportError:
        print('[db] psycopg2 saknas — faller tillbaka till SQLite')
        from db._sqlite import *
    except Exception as exc:
        print(f'[db] Postgres-anslutning misslyckades: {exc}')
        print('[db] Faller tillbaka till SQLite')
        from db._sqlite import *
else:
    from db._sqlite import *
