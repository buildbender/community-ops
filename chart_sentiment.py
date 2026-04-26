#!/usr/bin/env python3
"""
chart_sentiment.py — Generate a sentiment vs. market chart for the RV Daily Discord Summary.

Usage:
    python3 chart_sentiment.py --score 72 --date 2026-04-19
    python3 chart_sentiment.py --score 72  # uses today's date

Outputs:
    /tmp/rv_sentiment_chart.png

Data sources:
    - Sentiment scores: sentiment_log.json (this workspace)
    - BTC:  CoinGecko API (free, no key)
    - SPY, QQQ: Polygon.io (uses POLYGON_API_KEY env or falls back to Finnhub)
"""

import json
import argparse
import os
import sys
import datetime
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from pathlib import Path

SENTIMENT_LOG = Path(__file__).parent / "sentiment_log.json"
OUTPUT_PATH = "/tmp/rv_sentiment_chart.png"
POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
CMC_KEY     = os.environ.get("COINMARKETCAP_API_KEY", "")

LOOKBACK_DAYS = 14


def load_log():
    if not SENTIMENT_LOG.exists():
        return []
    with open(SENTIMENT_LOG) as f:
        return json.load(f)


def save_log(entries):
    with open(SENTIMENT_LOG, "w") as f:
        json.dump(entries, f, indent=2)


def append_score(date_str: str, score: int):
    entries = load_log()
    # Remove existing entry for same date if re-running
    entries = [e for e in entries if e["date"] != date_str]
    entries.append({"date": date_str, "score": score})
    entries.sort(key=lambda e: e["date"])
    save_log(entries)
    return entries


def get_btc_prices(days: int) -> dict:
    """Fetch BTC daily closes from CoinGecko (free, no key needed)."""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
        params = {"vs_currency": "usd", "days": days, "interval": "daily"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        prices = {}
        for ts_ms, price in data["prices"]:
            date = datetime.datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
            prices[date] = price
        return prices
    except Exception as e:
        print(f"[WARN] BTC fetch failed: {e}", file=sys.stderr)
        return {}


def get_equity_prices_polygon(ticker: str, from_date: str, to_date: str) -> dict:
    """Fetch daily closes from Polygon.io."""
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}"
        params = {"adjusted": "true", "sort": "asc", "apiKey": POLYGON_KEY}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        prices = {}
        for bar in data.get("results", []):
            date = datetime.datetime.utcfromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d")
            prices[date] = bar["c"]
        return prices
    except Exception as e:
        print(f"[WARN] Polygon fetch for {ticker} failed: {e}", file=sys.stderr)
        return {}


def normalize(prices: dict, dates: list) -> list:
    """Normalize prices to 100 base at first available date."""
    vals = [prices.get(d) for d in dates]
    # Find first non-None
    base = next((v for v in vals if v is not None), None)
    if base is None or base == 0:
        return [None] * len(dates)
    return [round((v / base) * 100, 2) if v is not None else None for v in vals]


def generate_chart(score: int, date_str: str):
    entries = append_score(date_str, score)

    # Build date range for lookback
    end_date = datetime.date.fromisoformat(date_str)
    start_date = end_date - datetime.timedelta(days=LOOKBACK_DAYS)
    from_str = start_date.isoformat()
    to_str = end_date.isoformat()

    # All dates in range
    all_dates = []
    d = start_date
    while d <= end_date:
        all_dates.append(d.isoformat())
        d += datetime.timedelta(days=1)

    print(f"[INFO] Fetching price data ({from_str} → {to_str})...", file=sys.stderr)

    # Fetch prices
    btc_raw = get_btc_prices(LOOKBACK_DAYS + 2)
    spy_raw = get_equity_prices_polygon("SPY", from_str, to_str)
    qqq_raw = get_equity_prices_polygon("QQQ", from_str, to_str)

    # Build sentiment series (only dates we have scores for, within range)
    sentiment_dates = [e["date"] for e in entries if from_str <= e["date"] <= to_str]
    sentiment_vals = [e["score"] for e in entries if from_str <= e["date"] <= to_str]

    # Normalize market data
    btc_norm = normalize(btc_raw, all_dates)
    spy_norm = normalize(spy_raw, all_dates)
    qqq_norm = normalize(qqq_raw, all_dates)

    # Convert dates to datetime objects for plotting
    dt_all = [datetime.date.fromisoformat(d) for d in all_dates]
    dt_sentiment = [datetime.date.fromisoformat(d) for d in sentiment_dates]

    # --- Plot ---
    fig, ax1 = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor("#1a1a2e")
    ax1.set_facecolor("#16213e")

    # Market lines on ax1 (normalized, right scale)
    ax1.set_ylabel("Normalized Price (base=100)", color="#aaaaaa", fontsize=10)
    ax1.tick_params(colors="#aaaaaa")

    def plot_line(ax, dates, vals, color, label, lw=1.8, alpha=0.85):
        valid = [(d, v) for d, v in zip(dates, vals) if v is not None]
        if not valid:
            return
        xs, ys = zip(*valid)
        ax.plot(xs, ys, color=color, linewidth=lw, alpha=alpha, label=label)

    plot_line(ax1, dt_all, btc_norm, "#f7931a", "BTC")   # orange
    plot_line(ax1, dt_all, spy_norm, "#26a65b", "SPY")   # green
    plot_line(ax1, dt_all, qqq_norm, "#f5d800", "QQQ/Nasdaq")  # yellow

    # Sentiment on ax2 (0-100, left scale)
    ax2 = ax1.twinx()
    ax2.set_ylabel("Community Sentiment (0–100)", color="#5b9bd5", fontsize=10)
    ax2.set_ylim(0, 100)
    ax2.tick_params(colors="#5b9bd5")

    if dt_sentiment:
        ax2.plot(dt_sentiment, sentiment_vals, color="#5b9bd5", linewidth=2.5,
                 marker="o", markersize=6, label="RV Sentiment", zorder=5)
        # Annotate latest score
        ax2.annotate(f"{sentiment_vals[-1]}", xy=(dt_sentiment[-1], sentiment_vals[-1]),
                     xytext=(8, 4), textcoords="offset points",
                     color="#5b9bd5", fontsize=11, fontweight="bold")

    # Formatting
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax1.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=30, ha="right", color="#aaaaaa")

    for spine in ax1.spines.values():
        spine.set_edgecolor("#333355")
    for spine in ax2.spines.values():
        spine.set_edgecolor("#333355")

    ax1.yaxis.label.set_color("#aaaaaa")
    ax1.tick_params(axis="y", colors="#aaaaaa")
    ax1.tick_params(axis="x", colors="#aaaaaa")

    ax1.grid(axis="y", color="#2a2a4a", linewidth=0.6, linestyle="--")
    ax1.grid(axis="x", color="#2a2a4a", linewidth=0.4, linestyle=":")

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2,
               loc="upper left", facecolor="#1a1a2e", edgecolor="#333355",
               labelcolor="white", fontsize=9)

    plt.title(f"RV Community Sentiment vs. Markets — {date_str}",
              color="white", fontsize=13, fontweight="bold", pad=12)

    plt.tight_layout()
    plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close()
    print(f"[OK] Chart saved to {OUTPUT_PATH}", file=sys.stderr)
    print(OUTPUT_PATH)  # stdout = path for caller


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--score", type=int, required=True, help="Sentiment score 0-100")
    parser.add_argument("--date", type=str, default=datetime.date.today().isoformat(),
                        help="Date for this score (YYYY-MM-DD), defaults to today")
    args = parser.parse_args()

    if not (0 <= args.score <= 100):
        print("[ERROR] Score must be between 0 and 100", file=sys.stderr)
        sys.exit(1)

    generate_chart(args.score, args.date)
