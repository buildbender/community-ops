"""
query_calls.py — Query stored investment calls from the RV Call Tracker database.

Usage:
    python3 query_calls.py --ticker BTC --hours 24
    python3 query_calls.py --hours 48
    python3 query_calls.py --leaderboard
    python3 query_calls.py --export-csv
    python3 query_calls.py --export-json
"""

import os
import csv
import json
import sqlite3
import argparse
import time
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.db")

LEVEL_DISPLAY = {
    "all_access": "All Access",
    "pro":        "Pro",
    "general":    "General",
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─── QUERY ───────────────────────────────────────────────────────────────────

def query_calls(ticker: str | None, hours: int) -> list[dict]:
    since = int(time.time()) - (hours * 3600)
    conn = get_db()

    if ticker:
        rows = conn.execute(
            """SELECT c.call_id, c.ticker, c.direction, c.username, m.level,
                      c.price_at_call, c.pnl_24h, c.result_24h,
                      c.pnl_7d, c.result_7d, c.timestamp, c.channel_id
               FROM calls c
               LEFT JOIN members m ON c.member_id = m.member_id
               WHERE c.ticker = ? AND c.timestamp >= ?
               ORDER BY c.ticker, c.timestamp DESC""",
            (ticker.upper(), since),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT c.call_id, c.ticker, c.direction, c.username, m.level,
                      c.price_at_call, c.pnl_24h, c.result_24h,
                      c.pnl_7d, c.result_7d, c.timestamp, c.channel_id
               FROM calls c
               LEFT JOIN members m ON c.member_id = m.member_id
               WHERE c.timestamp >= ?
               ORDER BY c.ticker, c.timestamp DESC""",
            (since,),
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def format_results(rows: list[dict]) -> str:
    if not rows:
        return "(no calls found)"

    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_ticker[row["ticker"]].append(row)

    lines = []
    for ticker, calls in sorted(by_ticker.items()):
        parts = []
        for c in calls:
            level_label = LEVEL_DISPLAY.get(c.get("level") or "general", "General")
            direction   = c["direction"]
            username    = c["username"]
            parts.append(f"{username} ({direction}, {level_label})")
        lines.append(f"${ticker} — {' | '.join(parts)}")

    return "\n".join(lines)


# ─── LEADERBOARD ─────────────────────────────────────────────────────────────

def get_leaderboard(min_calls: int = 3) -> str:
    conn = get_db()
    rows = conn.execute("""
        SELECT
            username,
            COUNT(*) as total_calls,
            SUM(CASE WHEN result_24h = 'win' OR result_7d = 'win' OR result_30d = 'win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN result_24h = 'loss' OR result_7d = 'loss' OR result_30d = 'loss' THEN 1 ELSE 0 END) as losses,
            MAX(CASE
                WHEN pnl_24h IS NOT NULL THEN pnl_24h
                WHEN pnl_7d IS NOT NULL THEN pnl_7d
                WHEN pnl_30d IS NOT NULL THEN pnl_30d
                ELSE NULL
            END) as best_pnl,
            (SELECT ticker FROM calls c2
             WHERE c2.username = calls.username
               AND (c2.pnl_24h IS NOT NULL OR c2.pnl_7d IS NOT NULL OR c2.pnl_30d IS NOT NULL)
             ORDER BY COALESCE(c2.pnl_24h, c2.pnl_7d, c2.pnl_30d) DESC
             LIMIT 1) as best_ticker
        FROM calls
        GROUP BY username
        HAVING COUNT(*) >= ?
        ORDER BY
            CASE WHEN (wins + losses) > 0 THEN CAST(wins AS FLOAT) / (wins + losses) ELSE 0 END DESC,
            total_calls DESC
    """, (min_calls,)).fetchall()
    conn.close()

    if not rows:
        return f"(no members with {min_calls}+ calls yet)"

    header = f"{'Rank':<5} {'Username':<20} {'Calls':<7} {'W':<5} {'L':<5} {'Win%':<8} {'Best Call'}"
    divider = "-" * 70
    lines = [header, divider]

    for i, row in enumerate(rows, 1):
        wins   = row["wins"] or 0
        losses = row["losses"] or 0
        total  = wins + losses
        win_pct = f"{(wins / total * 100):.0f}%" if total > 0 else "N/A"
        best = f"{row['best_ticker']} {row['best_pnl']:+.1f}%" if row["best_ticker"] and row["best_pnl"] is not None else "—"
        lines.append(
            f"{i:<5} {row['username']:<20} {row['total_calls']:<7} {wins:<5} {losses:<5} {win_pct:<8} {best}"
        )

    return "\n".join(lines)


# ─── EXPORT ──────────────────────────────────────────────────────────────────

def export_csv():
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls_export.csv")
    conn = get_db()
    rows = conn.execute("""
        SELECT c.call_id, c.username, c.ticker, c.direction, c.timeframe,
               c.price_at_call, c.price_24h, c.pnl_24h, c.result_24h,
               c.price_7d, c.pnl_7d, c.result_7d,
               c.price_30d, c.pnl_30d, c.result_30d,
               c.timestamp, c.channel_id
        FROM calls c
        ORDER BY c.timestamp DESC
    """).fetchall()
    conn.close()

    fieldnames = [
        "call_id", "username", "ticker", "direction", "timeframe",
        "price_at_call", "price_24h", "pnl_24h", "result_24h",
        "price_7d", "pnl_7d", "result_7d",
        "price_30d", "pnl_30d", "result_30d",
        "timestamp", "channel_id"
    ]

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))

    print(f"[OK] Exported {len(rows)} calls to {out_path}")


def export_json():
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls_export.json")
    conn = get_db()
    rows = conn.execute("""
        SELECT c.call_id, c.username, c.ticker, c.direction, c.timeframe,
               c.price_at_call, c.price_24h, c.pnl_24h, c.result_24h,
               c.price_7d, c.pnl_7d, c.result_7d,
               c.price_30d, c.pnl_30d, c.result_30d,
               c.timestamp, c.channel_id
        FROM calls c
        ORDER BY c.timestamp DESC
    """).fetchall()
    conn.close()

    data = [dict(row) for row in rows]

    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"[OK] Exported {len(data)} calls to {out_path}")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Query RV Call Tracker database")
    parser.add_argument("--ticker",       type=str,  default=None,
                        help="Filter by ticker symbol (e.g. BTC, NVDA)")
    parser.add_argument("--hours",        type=int,  default=24,
                        help="Look back N hours (default: 24)")
    parser.add_argument("--leaderboard",  action="store_true",
                        help="Show member leaderboard (min 3 calls)")
    parser.add_argument("--min-calls",    type=int,  default=3,
                        help="Minimum calls to qualify for leaderboard (default: 3)")
    parser.add_argument("--export-csv",   action="store_true",
                        help="Export all calls to calls_export.csv")
    parser.add_argument("--export-json",  action="store_true",
                        help="Export all calls to calls_export.json")
    args = parser.parse_args()

    if args.leaderboard:
        print(get_leaderboard(min_calls=args.min_calls))
    elif args.export_csv:
        export_csv()
    elif args.export_json:
        export_json()
    else:
        rows = query_calls(args.ticker, args.hours)
        print(format_results(rows))


if __name__ == "__main__":
    main()
