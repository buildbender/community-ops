"""
setup_db.py — Initialize the RV Call Tracker SQLite database.
Run this once before starting call_tracker.py, and again after V2 migration.
Safe to re-run: uses ALTER TABLE pattern that skips existing columns.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.db")


def setup_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS members (
            member_id TEXT PRIMARY KEY,
            username TEXT,
            level TEXT,
            first_seen INTEGER
        );

        CREATE TABLE IF NOT EXISTS calls (
            call_id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id TEXT,
            username TEXT,
            ticker TEXT,
            direction TEXT,
            timeframe TEXT,
            price_at_call REAL,
            message_id TEXT UNIQUE,
            channel_id TEXT,
            timestamp INTEGER,
            FOREIGN KEY (member_id) REFERENCES members(member_id)
        );

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    conn.commit()

    # V1 P&L columns
    v1_columns = [
        ("price_24h",  "REAL"),
        ("price_7d",   "REAL"),
        ("price_30d",  "REAL"),
        ("pnl_24h",    "REAL"),
        ("pnl_7d",     "REAL"),
        ("pnl_30d",    "REAL"),
        ("result_24h", "TEXT"),
        ("result_7d",  "TEXT"),
        ("result_30d", "TEXT"),
    ]

    # V3 columns (14d + 90d windows)
    v3_columns = [
        ("price_14d",  "REAL"),
        ("price_90d",  "REAL"),
        ("pnl_14d",    "REAL"),
        ("pnl_90d",    "REAL"),
        ("result_14d", "TEXT"),
        ("result_90d", "TEXT"),
    ]

    # V2 columns
    v2_columns = [
        ("timeframe_tag",      "TEXT"),    # short / medium / long / unspecified
        ("resolution_window",  "INTEGER"), # 7 / 30 / NULL (open-ended)
        ("asset_class",        "TEXT"),    # crypto / equity / etf / commodity
        ("status",             "TEXT"),    # open / closed
        ("streak_count",       "INTEGER"), # running win streak at time of close
        ("result_final",       "TEXT"),    # canonical WIN/LOSS based on resolution window
        ("current_price",      "REAL"),    # latest price (updated every pnl run)
        ("current_pnl",        "REAL"),    # latest % change from entry
    ]

    for col_name, col_type in v1_columns + v2_columns + v3_columns:
        try:
            conn.execute(f"ALTER TABLE calls ADD COLUMN {col_name} {col_type}")
            conn.commit()
            print(f"  ✓ Added column: {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    conn.close()
    print(f"[OK] Database initialized at {DB_PATH}")


if __name__ == "__main__":
    setup_database()
