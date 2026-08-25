"""Database backend selector — SQLite (local) or PostgreSQL (cloud)."""

import config

_USE_POSTGRES = False
_backend_is_postgres = False

if config.DATABASE_URL:
    try:
        from db._postgres import *
        _backend_is_postgres = True
    except ImportError:
        print('[db] psycopg2 saknas — faller tillbaka till SQLite')
        from db._sqlite import *
    except Exception as exc:
        print(f'[db] Postgres-anslutning misslyckades: {exc}')
        print('[db] Faller tillbaka till SQLite')
        from db._sqlite import *
else:
    from db._sqlite import *

# Keep the selected backend visible to feature modules without relying on
# symbols imported through __all__.
_USE_POSTGRES = _backend_is_postgres
