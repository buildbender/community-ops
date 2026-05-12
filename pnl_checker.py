#!/usr/bin/env python3
"""
pnl_checker.py — P&L follow-up checker for RV Call Tracker V2

V2 changes:
- Resolution window-aware WIN/LOSS: uses resolution_window column to determine
  which check window is canonical for each call.
- Stamps result_final + sets status='closed' when the canonical window resolves.
- Updates streak_count after each win: counts consecutive wins from most recent closed calls.
- Updates current_price and current_pnl on every run for all open calls (live unrealized P&L).
- Posts hot streak alert when streak_count reaches 3+.

Usage:
    python3 pnl_checker.py
    python3 pnl_checker.py --dry-run
"""

import os
import sqlite3
import time
import asyncio
import aiohttp
import argparse
import requests
from datetime import datetime, timezone

DB_PATH         = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.db")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")
CMC_API_KEY     = os.getenv("COINMARKETCAP_API_KEY", "")
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN", "")

DELIVERY_CHANNELS = ["928036731410849902", "1232495023300546581"]

# ─── DB HELPERS ──────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ─── PRICE FETCHERS ───────────────────────────────────────────────────────────

def get_crypto_price_cmc(symbol: str) -> float | None:
    if not CMC_API_KEY:
        return None
    try:
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
        r = requests.get(url, params={"symbol": symbol.upper()}, headers=headers, timeout=5)
        r.raise_for_status()
        data = r.json()
        return data["data"][symbol.upper()]["quote"]["USD"]["price"]
    except Exception:
        return None


def get_hyperliquid_price(symbol: str) -> float | None:
    try:
        resp = requests.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "metaAndAssetCtxs"},
            timeout=5
        )
        data = resp.json()
        assets = data[0].get("universe", [])
        ctxs = data[1]
        for i, asset in enumerate(assets):
            if asset.get("name", "").upper() == symbol.upper() and i < len(ctxs):
                price = ctxs[i].get("markPx")
                if price:
                    return float(price)
        return None
    except Exception:
        return None


def get_coingecko_price(symbol: str) -> float | None:
    try:
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/search?query={symbol}",
            timeout=5
        )
        results = resp.json().get("coins", [])
        for coin in results[:3]:
            if coin.get("symbol", "").upper() == symbol.upper():
                price_resp = requests.get(
                    f"https://api.coingecko.com/api/v3/simple/price?ids={coin['id']}&vs_currencies=usd",
                    timeout=5
                )
                price_data = price_resp.json()
                if coin["id"] in price_data:
                    return price_data[coin["id"]]["usd"]
        return None
    except Exception:
        return None


def get_stock_price(ticker: str) -> float | None:
    if not POLYGON_API_KEY:
        return None
    try:
        url = f"https://api.polygon.io/v2/last/trade/{ticker.upper()}"
        r = requests.get(url, params={"apiKey": POLYGON_API_KEY}, timeout=5)
        r.raise_for_status()
        return r.json()["results"]["p"]
    except Exception:
        return None


def get_price(ticker: str, asset_class: str = "crypto") -> float | None:
    """Asset-class-aware price lookup chain."""
    if asset_class in ("equity", "etf"):
        return get_stock_price(ticker) or get_crypto_price_cmc(ticker)

    # Crypto / commodity / unknown
    return (
        get_crypto_price_cmc(ticker)
        or get_hyperliquid_price(ticker)
        or get_coingecko_price(ticker)
        or get_stock_price(ticker)
    )

# ─── P&L LOGIC ───────────────────────────────────────────────────────────────

def calculate_pnl(price_at_call: float, current_price: float) -> float | None:
    if not price_at_call or price_at_call == 0:
        return None
    return ((current_price - price_at_call) / price_at_call) * 100


def determine_result(direction: str, pnl: float | None) -> str | None:
    if pnl is None or not direction:
        return None
    d = direction.lower().strip()
    if d == "bullish":
        return "win" if pnl > 0 else "loss"
    elif d == "bearish":
        return "win" if pnl < 0 else "loss"
    return None

# ─── STREAK LOGIC ────────────────────────────────────────────────────────────

def compute_streak(member_id: str, conn) -> int:
    """Count consecutive wins from most recent closed calls for a member."""
    rows = conn.execute(
        """SELECT result_final FROM calls
           WHERE member_id = ? AND status = 'closed' AND result_final IS NOT NULL
           ORDER BY timestamp DESC""",
        (member_id,)
    ).fetchall()

    streak = 0
    for row in rows:
        if row["result_final"] == "win":
            streak += 1
        else:
            break
    return streak

# ─── HOT STREAK ALERT ────────────────────────────────────────────────────────

async def post_hot_streak_alert(username: str, streak: int, ticker: str, pnl: float | None):
    pnl_str = f"+{pnl:.1f}%" if pnl and pnl > 0 else (f"{pnl:.1f}%" if pnl else "")
    msg = (
        f"🔥 Hot Streak Alert\n"
        f"**{username}** is on a {streak}-call win streak.\n"
        f"Last call: {ticker} {pnl_str}\n"
        f"`/calls @{username}` to see the full record."
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
            except Exception as e:
                print(f"Error posting hot streak alert to {channel_id}: {e}")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False):
    conn = get_db()
    now = int(time.time())

    intervals = [
        ("24h",  "price_24h", "pnl_24h", "result_24h", 86400),
        ("7d",   "price_7d",  "pnl_7d",  "result_7d",  604800),
        ("14d",  "price_14d", "pnl_14d", "result_14d", 1209600),
        ("30d",  "price_30d", "pnl_30d", "result_30d", 2592000),
        ("90d",  "price_90d", "pnl_90d", "result_90d", 7776000),
    ]

    total_updated = 0
    streak_alerts = []  # (username, streak, ticker, pnl) tuples to alert after run

    # ── Step 1: Update live current_price/current_pnl for all open calls ──
    open_calls = conn.execute(
        """SELECT call_id, ticker, direction, price_at_call, asset_class
           FROM calls WHERE status = 'open' AND price_at_call IS NOT NULL"""
    ).fetchall()

    print(f"\n[Live P&L] Updating {len(open_calls)} open call(s) ...")
    for row in open_calls:
        current_price = get_price(row["ticker"], row["asset_class"] or "crypto")
        if current_price is None:
            continue
        pnl = calculate_pnl(row["price_at_call"], current_price)
        if not dry_run:
            conn.execute(
                "UPDATE calls SET current_price = ?, current_pnl = ? WHERE call_id = ?",
                (current_price, pnl, row["call_id"])
            )
    if not dry_run:
        conn.commit()

    # ── Step 2: Check resolution windows and stamp result_final ──
    for label, price_col, pnl_col, result_col, seconds in intervals:
        cutoff = now - seconds

        # Get calls that have hit this time window but haven't been priced yet
        rows = conn.execute(f"""
            SELECT call_id, ticker, direction, price_at_call, username, member_id,
                   resolution_window, timeframe_tag, asset_class
            FROM calls
            WHERE price_at_call IS NOT NULL
              AND {price_col} IS NULL
              AND timestamp < ?
        """, (cutoff,)).fetchall()

        print(f"\n[{label}] {len(rows)} eligible call(s)")

        for row in rows:
            call_id          = row["call_id"]
            ticker           = row["ticker"]
            direction        = row["direction"]
            price_at_call    = row["price_at_call"]
            username         = row["username"]
            member_id        = row["member_id"]
            resolution_window = row["resolution_window"]
            timeframe_tag    = row["timeframe_tag"] or "unspecified"
            asset_class      = row["asset_class"] or "crypto"

            if not ticker:
                continue

            current_price = get_price(ticker, asset_class)
            if current_price is None:
                print(f"  #{call_id} {ticker}: no price found, skipping")
                continue

            pnl    = calculate_pnl(price_at_call, current_price)
            result = determine_result(direction, pnl)

            pnl_str = f"{pnl:+.2f}%" if pnl is not None else "N/A"
            print(f"  #{call_id} {username} | {ticker} [{direction}] | "
                  f"entry={price_at_call:.4f} now={current_price:.4f} "
                  f"pnl={pnl_str} result={result} window_label={label}")

            if not dry_run:
                conn.execute(f"""
                    UPDATE calls
                    SET {price_col} = ?, {pnl_col} = ?, {result_col} = ?,
                        current_price = ?, current_pnl = ?
                    WHERE call_id = ?
                """, (current_price, pnl, result, current_price, pnl, call_id))
                conn.commit()
                total_updated += 1

                # ── Determine if this window is the canonical resolution ──
                # short / unspecified  -> resolves at 7d
                # biweekly            -> resolves at 14d
                # medium              -> resolves at 30d
                # quarterly           -> resolves at 90d
                # long / None         -> resolves at 90d (falls back to 30d if 90d unavailable)
                is_canonical = False

                if resolution_window == 7 and label == "7d":
                    is_canonical = True
                elif resolution_window == 14 and label == "14d":
                    is_canonical = True
                elif resolution_window == 30 and label == "30d":
                    is_canonical = True
                elif resolution_window == 90 and label == "90d":
                    is_canonical = True
                elif resolution_window is None and label == "90d":
                    is_canonical = True
                elif resolution_window is None and timeframe_tag == "unspecified" and label == "7d":
                    is_canonical = True

                if is_canonical and result and not dry_run:
                    # Stamp result_final and close the call
                    streak = compute_streak(member_id, conn)
                    if result == "win":
                        streak += 1  # Include this win

                    conn.execute(
                        "UPDATE calls SET result_final = ?, status = 'closed', streak_count = ? WHERE call_id = ?",
                        (result, streak, call_id)
                    )
                    conn.commit()
                    print(f"  -> CANONICAL: result_final={result}, status=closed, streak={streak}")

                    # Queue hot streak alert if streak >= 3
                    if result == "win" and streak >= 3:
                        streak_alerts.append((username, streak, ticker, pnl))

    conn.close()
    label_prefix = "(DRY RUN) " if dry_run else ""
    print(f"\n{label_prefix}Total updated: {total_updated} record(s)")

    # ── Post hot streak alerts ──
    if streak_alerts and not dry_run and DISCORD_TOKEN:
        async def send_alerts():
            for username, streak, ticker, pnl in streak_alerts:
                await post_hot_streak_alert(username, streak, ticker, pnl)
        asyncio.run(send_alerts())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P&L checker for RV Call Tracker V2")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview updates without writing to DB")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
