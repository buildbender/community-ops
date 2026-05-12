#!/usr/bin/env python3
"""
chart_generator.py — Sentiment Overlay Chart Generator for RV Community Summary V2

Generates a line chart overlaying Community Sentiment Score, BTC, SPY, and NASDAQ (QQQ),
all indexed to 100 at the start of the window.

Usage as module:
    from chart_generator import generate_sentiment_chart
    path = generate_sentiment_chart(days=4, output_path="/tmp/community_chart.png")

Usage as CLI:
    python3 chart_generator.py --days 3 --output /tmp/chart.png
    python3 chart_generator.py --days 4 --output /tmp/chart.png --title "Mon-Thu"
"""

import os
import json
import sys
import argparse
import datetime
import requests
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

SENTIMENT_LOG = Path(__file__).parent / "sentiment_log.json"
POLYGON_KEY   = os.environ.get("POLYGON_API_KEY", "")
DEFAULT_OUTPUT = "/tmp/rv_community_chart.png"


# ─── DATA FETCHERS ────────────────────────────────────────────────────────────

def load_sentiment_log(from_date: str, to_date: str) -> dict[str, float]:
    """Load sentiment scores for the given date window from sentiment_log.json."""
    if not SENTIMENT_LOG.exists():
        return {}
    try:
        with open(SENTIMENT_LOG) as f:
            entries = json.load(f)
        return {
            e["date"]: float(e["score"])
            for e in entries
            if from_date <= e["date"] <= to_date
        }
    except Exception as e:
        print(f"[WARN] Could not read sentiment_log.json: {e}", file=sys.stderr)
        return {}


def get_btc_prices(days: int) -> dict[str, float]:
    """Fetch BTC daily closes from CoinGecko (free, no key needed)."""
    try:
        url = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
        params = {"vs_currency": "usd", "days": days + 1, "interval": "daily"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        prices = {}
        for ts_ms, price in r.json()["prices"]:
            date = datetime.datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
            prices[date] = price
        return prices
    except Exception as e:
        print(f"[WARN] BTC fetch failed: {e}", file=sys.stderr)
        return {}


def get_equity_prices(ticker: str, from_date: str, to_date: str) -> dict[str, float]:
    """Fetch daily closes from Polygon.io."""
    if not POLYGON_KEY:
        print(f"[WARN] No POLYGON_API_KEY — skipping {ticker}", file=sys.stderr)
        return {}
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}"
        params = {"adjusted": "true", "sort": "asc", "apiKey": POLYGON_KEY}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        prices = {}
        for bar in r.json().get("results", []):
            date = datetime.datetime.utcfromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d")
            prices[date] = bar["c"]
        return prices
    except Exception as e:
        print(f"[WARN] Polygon fetch for {ticker} failed: {e}", file=sys.stderr)
        return {}


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def get_date_range(days: int) -> tuple[str, str, list[str]]:
    """Returns (from_date, to_date, all_dates_list) for the past N days."""
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days - 1)
    all_dates = []
    d = start
    while d <= today:
        all_dates.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return start.isoformat(), today.isoformat(), all_dates


def normalize_series(price_map: dict[str, float], dates: list[str]) -> list[float | None]:
    """Index a price series to 100 at the first available date."""
    vals = [price_map.get(d) for d in dates]
    base = next((v for v in vals if v is not None), None)
    if not base or base == 0:
        return [None] * len(dates)
    return [round((v / base) * 100, 2) if v is not None else None for v in vals]


# ─── CHART ────────────────────────────────────────────────────────────────────

def generate_sentiment_chart(days: int, output_path: str = DEFAULT_OUTPUT,
                              title_suffix: str = "") -> str:
    """
    Generate the sentiment overlay chart.

    Args:
        days: Number of days to cover (3 for Mon post Fri-Sun, 4 for Fri post Mon-Thu)
        output_path: Where to save the PNG
        title_suffix: Optional label e.g. "Fri-Sun" or "Mon-Thu"

    Returns:
        output_path on success
    """
    from_date, to_date, all_dates = get_date_range(days)

    print(f"[INFO] Generating chart: {from_date} -> {to_date}", file=sys.stderr)

    # Fetch data
    btc_raw  = get_btc_prices(days)
    spy_raw  = get_equity_prices("SPY", from_date, to_date)
    qqq_raw  = get_equity_prices("QQQ", from_date, to_date)
    sentiment_map = load_sentiment_log(from_date, to_date)

    # Normalize market series
    btc_norm = normalize_series(btc_raw, all_dates)
    spy_norm = normalize_series(spy_raw, all_dates)
    qqq_norm = normalize_series(qqq_raw, all_dates)

    # Sentiment series (only dates with data)
    sentiment_dates = [d for d in all_dates if d in sentiment_map]
    sentiment_vals  = [sentiment_map[d] for d in sentiment_dates]
    has_sentiment   = len(sentiment_dates) > 0

    # Convert to datetime objects for plotting
    dt_all       = [datetime.date.fromisoformat(d) for d in all_dates]
    dt_sentiment = [datetime.date.fromisoformat(d) for d in sentiment_dates]

    # ── Plot ──
    fig, ax1 = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor("#1a1a2e")
    ax1.set_facecolor("#16213e")

    ax1.set_ylabel("Normalized Price (base=100)", color="#aaaaaa", fontsize=10)
    ax1.tick_params(colors="#aaaaaa")

    def plot_line(ax, dates, vals, color, label, lw=2.0, alpha=0.9):
        valid = [(d, v) for d, v in zip(dates, vals) if v is not None]
        if not valid:
            return
        xs, ys = zip(*valid)
        ax.plot(xs, ys, color=color, linewidth=lw, alpha=alpha, label=label)

    plot_line(ax1, dt_all, btc_norm, "#f7931a", "BTC")          # orange
    plot_line(ax1, dt_all, spy_norm, "#26a65b", "SPY")           # green
    plot_line(ax1, dt_all, qqq_norm, "#f5d800", "NASDAQ (QQQ)")  # yellow

    # Sentiment on secondary axis
    ax2 = ax1.twinx()
    ax2.set_ylabel("Community Sentiment (0-100)", color="#e8b4f8", fontsize=10)
    ax2.set_ylim(0, 100)
    ax2.tick_params(colors="#e8b4f8")

    if has_sentiment:
        ax2.plot(dt_sentiment, sentiment_vals,
                 color="#e8b4f8", linewidth=2.5, marker="o", markersize=6,
                 label="RV Sentiment", zorder=5)
        # Annotate latest value
        ax2.annotate(
            f"{sentiment_vals[-1]:.0f}",
            xy=(dt_sentiment[-1], sentiment_vals[-1]),
            xytext=(8, 4), textcoords="offset points",
            color="#e8b4f8", fontsize=11, fontweight="bold"
        )
    else:
        # Show a note if no sentiment data
        ax2.text(
            0.5, 0.5, "Sentiment data unavailable",
            transform=ax2.transAxes, color="#888888",
            ha="center", va="center", fontsize=10, style="italic"
        )

    # X-axis formatting
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax1.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=30, ha="right", color="#aaaaaa")

    # Spines
    for spine in ax1.spines.values():
        spine.set_edgecolor("#333355")
    for spine in ax2.spines.values():
        spine.set_edgecolor("#333355")

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

    # Title
    date_range_label = title_suffix or f"{from_date} to {to_date}"
    plt.title(
        f"Community Sentiment vs Markets — {date_range_label}",
        color="white", fontsize=13, fontweight="bold", pad=12
    )

    plt.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()

    print(f"[OK] Chart saved to {output_path}", file=sys.stderr)
    return output_path


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate RV Community Sentiment Overlay Chart")
    parser.add_argument("--days",   type=int, required=True,
                        help="Number of days to cover (3 for Mon post, 4 for Fri post)")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT,
                        help=f"Output path for PNG (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--title",  type=str, default="",
                        help="Optional title suffix e.g. 'Fri-Sun' or 'Mon-Thu'")
    args = parser.parse_args()

    path = generate_sentiment_chart(days=args.days, output_path=args.output,
                                    title_suffix=args.title)
    print(path)
