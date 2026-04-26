#!/usr/bin/env python3
"""
pnl_checker.py — P&L follow-up checker for RV Call Tracker
Fetches current prices for logged calls at 24h, 7d, and 30d intervals
and records win/loss results.

Usage:
    python3 pnl_checker.py
    python3 pnl_checker.py --dry-run
"""

import os
import sqlite3
import time
import argparse
import requests

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.db")
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
CMC_API_KEY     = os.environ.get("COINMARKETCAP_API_KEY", "")


# ─── DB HELPERS ──────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def add_pnl_columns():
    """Add P&L columns to calls table if they don't exist (safe to re-run)."""
    conn = get_db()
    cur = conn.cursor()
    columns = [
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
    for col_name, col_type in columns:
        try:
            cur.execute(f"ALTER TABLE calls ADD COLUMN {col_name} {col_type}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.close()


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


def get_price(ticker: str) -> float | None:
    """Price lookup chain: CMC → Hyperliquid → CoinGecko → Polygon."""
    for fn in [get_crypto_price_cmc, get_hyperliquid_price, get_coingecko_price, get_stock_price]:
        price = fn(ticker)
        if price:
            return price
    return None


# ─── P&L LOGIC ───────────────────────────────────────────────────────────────

def calculate_pnl(price_at_call: float, current_price: float) -> float | None:
    if not price_at_call or price_at_call == 0:
        return None
    return ((current_price - price_at_call) / price_at_call) * 100


def determine_result(direction: str, pnl: float) -> str | None:
    if pnl is None or not direction:
        return None
    d = direction.lower().strip()
    if d == "bullish":
        return "win" if pnl > 0 else "loss"
    elif d == "bearish":
        return "win" if pnl < 0 else "loss"
    return None


# ─── MAIN ────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False):
    add_pnl_columns()

    conn = get_db()
    cur = conn.cursor()
    now = int(time.time())

    intervals = [
        ("24h",  "price_24h", "pnl_24h", "result_24h", 86400),
        ("7d",   "price_7d",  "pnl_7d",  "result_7d",  604800),
        ("30d",  "price_30d", "pnl_30d", "result_30d", 2592000),
    ]

    total_updated = 0

    for label, price_col, pnl_col, result_col, seconds in intervals:
        cutoff = now - seconds
        cur.execute(f"""
            SELECT call_id, ticker, direction, price_at_call, username
            FROM calls
            WHERE price_at_call IS NOT NULL
              AND {price_col} IS NULL
              AND timestamp < ?
        """, (cutoff,))
        rows = cur.fetchall()
        print(f"\n[{label}] {len(rows)} eligible call(s)")

        for row in rows:
            call_id, ticker, direction, price_at_call, username = (
                row["call_id"], row["ticker"], row["direction"],
                row["price_at_call"], row["username"]
            )
            if not ticker:
                continue

            current_price = get_price(ticker)
            if current_price is None:
                print(f"  #{call_id} {ticker}: no price found, skipping")
                continue

            pnl    = calculate_pnl(price_at_call, current_price)
            result = determine_result(direction, pnl)

            pnl_str = f"{pnl:+.2f}%" if pnl is not None else "N/A"
            print(f"  #{call_id} {username} | {ticker} [{direction}] | "
                  f"entry={price_at_call:.4f} now={current_price:.4f} "
                  f"pnl={pnl_str} → {result}")

            if not dry_run:
                cur.execute(f"""
                    UPDATE calls
                    SET {price_col} = ?, {pnl_col} = ?, {result_col} = ?
                    WHERE call_id = ?
                """, (current_price, pnl, result, call_id))
                conn.commit()
                total_updated += 1

    conn.close()
    label = "(DRY RUN) " if dry_run else ""
    print(f"\n{label}Total updated: {total_updated} record(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P&L checker for RV Call Tracker")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview updates without writing to DB")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
