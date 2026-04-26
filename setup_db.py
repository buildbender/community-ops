"""
setup_db.py — Initialize the RV Call Tracker SQLite database.
Run this once before starting call_tracker.py.
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

    # Add P&L columns if not present (safe to re-run)
    pnl_columns = [
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
    for col_name, col_type in pnl_columns:
        try:
            conn.execute(f"ALTER TABLE calls ADD COLUMN {col_name} {col_type}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

    conn.close()
    print(f"[OK] Database initialized at {DB_PATH}")


if __name__ == "__main__":
    setup_database()
