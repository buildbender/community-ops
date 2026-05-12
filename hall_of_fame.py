#!/usr/bin/env python3
"""
hall_of_fame.py — Monthly Hall of Fame for RV Call Tracker V2

Finds the top caller by win% for the calendar month just ended.
Posts a recognition message to both delivery channels.

Run on the last day of each month via cron:
    0 20 28-31 * * [ "$(date +%d)" = "$(cal | awk 'NF{last=$NF}END{print last}')" ] && python3 hall_of_fame.py
Or simply schedule it to run on the 1st of each month at 9am (covers prior month):
    0 9 1 * * /path/to/venv/bin/python3 /path/to/hall_of_fame.py
"""

import os
import sqlite3
import asyncio
import aiohttp
import calendar
from datetime import datetime, timezone

DB_PATH       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.db")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DELIVERY_CHANNELS = ["928036731410849902", "1232495023300546581"]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_month_window(months_ago: int = 1) -> tuple[int, int, str]:
    """Returns (start_ts, end_ts, month_name) for a past month."""
    now = datetime.now(timezone.utc)
    year  = now.year
    month = now.month - months_ago

    if month <= 0:
        month += 12
        year -= 1

    _, last_day = calendar.monthrange(year, month)
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end   = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    name  = start.strftime("%B %Y")

    return int(start.timestamp()), int(end.timestamp()), name


def get_next_month_name(months_ago: int = 1) -> str:
    now = datetime.now(timezone.utc)
    month = now.month - months_ago + 1
    year  = now.year

    if month <= 0:
        month += 12
        year -= 1
    elif month > 12:
        month -= 12
        year += 1

    return datetime(year, month, 1).strftime("%B")


def find_top_caller(start_ts: int, end_ts: int) -> dict | None:
    conn = get_db()

    rows = conn.execute("""
        SELECT
            username,
            member_id,
            COUNT(CASE WHEN result_final = 'win' THEN 1 END) as wins,
            COUNT(CASE WHEN result_final = 'loss' THEN 1 END) as losses,
            MAX(CASE WHEN resolution_window = 7 THEN pnl_7d
                     WHEN resolution_window = 30 THEN pnl_30d
                     WHEN resolution_window IS NULL THEN pnl_30d
                     ELSE NULL END) as best_pnl,
            (SELECT ticker FROM calls c2
             WHERE c2.member_id = calls.member_id
               AND c2.result_final = 'win'
               AND c2.timestamp BETWEEN ? AND ?
             ORDER BY COALESCE(c2.pnl_7d, c2.pnl_30d) DESC
             LIMIT 1) as best_ticker
        FROM calls
        WHERE status = 'closed'
          AND timestamp BETWEEN ? AND ?
        GROUP BY member_id
        HAVING (wins + losses) >= 3
        ORDER BY CAST(wins AS FLOAT) / (wins + losses) DESC, wins DESC
        LIMIT 1
    """, (start_ts, end_ts, start_ts, end_ts)).fetchone()

    conn.close()

    if not rows:
        return None

    wins   = rows["wins"] or 0
    losses = rows["losses"] or 0
    total  = wins + losses
    win_pct = (wins / total * 100) if total > 0 else 0

    return {
        "username":   rows["username"],
        "wins":       wins,
        "losses":     losses,
        "win_pct":    win_pct,
        "best_pnl":   rows["best_pnl"],
        "best_ticker": rows["best_ticker"],
    }


async def post_hall_of_fame(caller: dict, month_name: str, next_month: str):
    best_call_str = ""
    if caller["best_ticker"] and caller["best_pnl"] is not None:
        best_call_str = f"\nBest Call of the Month: **{caller['best_ticker']} {caller['best_pnl']:+.1f}%**"

    msg = (
        f"🏅 **{month_name} Hall of Fame**\n"
        f"Top Caller: **{caller['username']}**\n"
        f"Record: **{caller['win_pct']:.1f}%** win rate ({caller['wins']}W/{caller['losses']}L)"
        f"{best_call_str}\n"
        f"Congrats {caller['username']}. See you in {next_month}'s leaderboard."
    )

    headers = {"Authorization": f"Bot {DISCORD_TOKEN}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        for channel_id in DELIVERY_CHANNELS:
            try:
                await session.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers=headers,
                    json={"content": msg},
                )
                print(f"Posted Hall of Fame to channel {channel_id}")
            except Exception as e:
                print(f"Error posting to {channel_id}: {e}")


async def main():
    start_ts, end_ts, month_name = get_month_window(months_ago=1)
    next_month = get_next_month_name(months_ago=1)

    print(f"Hall of Fame: {month_name}")
    print(f"Window: {start_ts} -> {end_ts}")

    caller = find_top_caller(start_ts, end_ts)

    if not caller:
        print("No qualifying callers this month (min 3 closed calls required). Skipping post.")
        return

    print(f"Top caller: {caller['username']} ({caller['win_pct']:.1f}%, {caller['wins']}W/{caller['losses']}L)")

    if DISCORD_TOKEN:
        await post_hall_of_fame(caller, month_name, next_month)
    else:
        print("No DISCORD_TOKEN set — skipping Discord post")
        print(f"Would post: {caller}")


if __name__ == "__main__":
    asyncio.run(main())
