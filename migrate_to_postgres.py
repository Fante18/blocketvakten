"""Migrate local SQLite data to PostgreSQL.

Usage:
  set DATABASE_URL=postgresql://user:pass@host:5432/dbname
  python migrate_to_postgres.py

This reads the local SQLite database (data/blocketvakten.db) and copies
all tables into the PostgreSQL database specified by DATABASE_URL.
Existing data in Postgres is cleared first (DROP + recreate).
"""
import json
import os
import sqlite3
import sys

import psycopg2
import psycopg2.extras

# --- Config ---
SQLITE_PATH = os.environ.get("BLOCKETVAKTEN_DB", "data/blocketvakten.db")
PG_URL = os.environ.get("DATABASE_URL", "")

if not PG_URL:
    print("ERROR: DATABASE_URL is not set.")
    print("  Example: set DATABASE_URL=postgresql://user:pass@host:5432/dbname")
    sys.exit(1)

if not os.path.exists(SQLITE_PATH):
    print(f"ERROR: SQLite database not found at {SQLITE_PATH}")
    sys.exit(1)

# Tables to migrate in dependency order
TABLES = [
    ("users", ["id", "email", "password_hash", "created_at"]),
    ("sessions", ["token", "user_id", "created_at", "expires_at"]),
    ("reset_tokens", ["token", "user_id", "created_at", "expires_at"]),
    ("searches", None),  # None = all columns
    ("listings", None),
    ("notifications", None),
    ("check_logs", None),
    ("price_history", None),
    ("listing_follows", None),
    ("disappeared_listings", None),
    ("settings", None),
]

print(f"SQLite: {SQLITE_PATH}")
print(f"Postgres: {PG_URL}")

# Connect to both databases
sqlite_conn = sqlite3.connect(SQLITE_PATH)
sqlite_conn.row_factory = sqlite3.Row

pg_conn = psycopg2.connect(PG_URL)
pg_conn.autocommit = True

try:
    with pg_conn.cursor() as cur:
        # Drop and recreate all tables
        cur.execute("""
            DROP TABLE IF EXISTS settings CASCADE;
            DROP TABLE IF EXISTS disappeared_listings CASCADE;
            DROP TABLE IF EXISTS listing_follows CASCADE;
            DROP TABLE IF EXISTS price_history CASCADE;
            DROP TABLE IF EXISTS check_logs CASCADE;
            DROP TABLE IF EXISTS notifications CASCADE;
            DROP TABLE IF EXISTS listings CASCADE;
            DROP TABLE IF EXISTS reset_tokens CASCADE;
            DROP TABLE IF EXISTS sessions CASCADE;
            DROP TABLE IF EXISTS searches CASCADE;
            DROP TABLE IF EXISTS users CASCADE;
        """)
        print("Dropped existing tables.")

        # Re-run the schema from db/_postgres.py
        import db._postgres as pgdb
        cur.execute(pgdb.SCHEMA)
        print("Created fresh schema.")

    # Copy data table by table
    for table_name, columns in TABLES:
        # Get SQLite row count
        count = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        if count == 0:
            print(f"  {table_name}: 0 rows (skipped)")
            continue

        # Get columns if not specified
        if columns is None:
            sqlite_conn.row_factory = None
            curs = sqlite_conn.execute(f"SELECT * FROM {table_name} LIMIT 1")
            columns = [desc[0] for desc in curs.description]
            sqlite_conn.row_factory = sqlite3.Row

        # Read from SQLite
        rows = sqlite_conn.execute(
            f"SELECT {', '.join(columns)} FROM {table_name}"
        ).fetchall()

        # Insert into Postgres
        placeholders = ", ".join(["%s"] * len(columns))
        col_names = ", ".join(columns)
        sql = f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"

        with pg_conn.cursor() as cur:
            for row in rows:
                values = [row[c] for c in columns]
                # Convert SQLite 0/1 bools to Python bools for BOOLEAN columns
                bool_cols = {"active", "send_email", "send_sms", "seen", "interesting", "read"}
                for i, col in enumerate(columns):
                    if col in bool_cols and values[i] is not None:
                        values[i] = bool(values[i])
                cur.execute(sql, values)

        # Reset sequences (SERIAL columns)
        if columns[0] == "id":
            with pg_conn.cursor() as cur:
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table_name}), 1))"
                )

        print(f"  {table_name}: {count} rows migrated")

    print("\nMigration complete!")

finally:
    sqlite_conn.close()
    pg_conn.close()
