"""
call_tracker.py — RV Member Call Tracker V2
Listens to Discord channels for explicit investment calls and stores them in SQLite.

V2 changes:
- Added #hive-chat to monitored channels
- Removed hardcoded CRYPTO_SYMBOLS set (fully dynamic via GPT)
- Timeframe tagging: maps raw timeframe to short/medium/long/unspecified
- Resolution window: maps timeframe_tag to 7d/30d/None
- Asset class mapping from GPT asset_type
- Ticker consensus alert: posts when 3+ members call same ticker+direction same day
- status='open' on insert
"""

import os
import sys
import json
import time
import sqlite3
import logging
import asyncio
import aiohttp
import requests
from datetime import datetime, timezone

import discord
from discord import app_commands
from openai import AsyncOpenAI

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN", "")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")
CMC_API_KEY     = os.getenv("COINMARKETCAP_API_KEY", "")

DB_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.db")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "call_tracker.log")

GUILD_ID = 921411652115644436

# V2: 4 monitored channels (added #hive-chat)
MONITORED_CHANNELS = {
    "928036731410849902": "pro-chat",
    "927678002135973918": "crypto",
    "1027573555975688223": "degen",
    "1232495023300546581": "hive-chat",
}

# Delivery channels for alerts (Community Summary + real-time alerts)
DELIVERY_CHANNELS = ["928036731410849902", "1232495023300546581"]

ROLE_LEVEL_MAP = {
    "All Access": "all_access",
    "Pro":        "pro",
}

GPT_SYSTEM_PROMPT = """\
You are a financial call extractor. Given a Discord message, determine if it contains \
an explicit investment call.

Rules:
- Only extract if the direction (bullish/bearish) is EXPLICIT. Do not infer.
- Buying/long/calls/adding = bullish. Shorting/puts/selling = bearish. \
Watching/researching = null.
- Ticker must be a real asset (stock, crypto, ETF, or commodity). Ignore memes/jokes about non-assets.
- Stocks and ETFs are valid (e.g. NVDA, TSLA, SPY, QQQ, GLD, TLT).
- Crypto tokens are valid regardless of whether they are well-known (e.g. HYPE, EDGE, PENGU, JUP).
- Extract the ticker symbol only — NOT the full name. e.g. "EdgeX" -> "EDGE", "Hyperliquid" -> "HYPE".
- Extract timeframe ONLY if explicitly stated (raw string, no interpretation).
- High confidence: clear buy/sell statement with named ticker. Medium: ambiguous. Low: vague.

Return JSON only:
{"has_call": true/false, "ticker": "SYMBOL or null", "direction": "bullish|bearish|null", \
"timeframe": "string or null", "confidence": "high|medium|low", "asset_type": "crypto|stock|etf|commodity|unknown"}\
"""

# Timeframe keyword sets for tagging
TIMEFRAME_SHORT_KW      = {"today", "this week", "next few days", "intraday", "swing", "scalp",
                            "short term", "short-term", "eod", "end of day", "24h", "48h", "quick"}
TIMEFRAME_BIWEEKLY_KW  = {"2 weeks", "two weeks", "14d", "14 days", "biweekly", "bi-weekly",
                            "couple weeks", "next two weeks"}
TIMEFRAME_MEDIUM_KW    = {"this month", "end of month", "next month", "few weeks",
                            "medium term", "medium-term", "monthly", "weeks", "30d", "30 days"}
TIMEFRAME_QUARTERLY_KW = {"3 months", "three months", "quarter", "quarterly", "q1", "q2", "q3", "q4",
                            "3m", "90d", "90 days", "next quarter"}
TIMEFRAME_LONG_KW      = {"long term", "long-term", "cycle", "accumulating", "accumulate",
                            "dca", "next year", "multi month", "multi-month", "hold", "holding",
                            "position", "year"}

ASSET_CLASS_MAP = {
    "crypto":    "crypto",
    "stock":     "equity",
    "etf":       "etf",
    "commodity": "commodity",
    "unknown":   "crypto",
}

# ─────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logger = logging.getLogger("call_tracker")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(LOG_PATH)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


log = setup_logging()

# ─────────────────────────────────────────────
# Timeframe tagging
# ─────────────────────────────────────────────

def tag_timeframe(raw: str | None) -> tuple[str, int | None]:
    """Map raw timeframe string to (timeframe_tag, resolution_window).

    Resolution windows:
      short      ->  7d
      biweekly   -> 14d
      medium     -> 30d
      quarterly  -> 90d
      long       -> None (open-ended, resolves at 90d)
      unspecified->  7d (default)
    """
    if not raw:
        return "unspecified", 7

    tf = raw.lower().strip()

    for kw in TIMEFRAME_SHORT_KW:
        if kw in tf:
            return "short", 7

    for kw in TIMEFRAME_BIWEEKLY_KW:
        if kw in tf:
            return "biweekly", 14

    for kw in TIMEFRAME_MEDIUM_KW:
        if kw in tf:
            return "medium", 30

    for kw in TIMEFRAME_QUARTERLY_KW:
        if kw in tf:
            return "quarterly", 90

    for kw in TIMEFRAME_LONG_KW:
        if kw in tf:
            return "long", None

    return "unspecified", 7

# ─────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def get_meta(key: str) -> str | None:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_meta(key: str, value: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def upsert_member(member_id: str, username: str, level: str):
    now = int(time.time())
    with get_db() as conn:
        conn.execute(
            "INSERT INTO members (member_id, username, level, first_seen) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(member_id) DO UPDATE SET username = excluded.username, level = excluded.level",
            (member_id, username, level, now),
        )


def insert_call(
    member_id: str,
    username: str,
    ticker: str,
    direction: str,
    timeframe: str | None,
    price: float | None,
    message_id: str,
    channel_id: str,
    timestamp: int,
    timeframe_tag: str,
    resolution_window: int | None,
    asset_class: str,
) -> bool:
    """Returns True if inserted, False if duplicate."""
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO calls
                   (member_id, username, ticker, direction, timeframe,
                    price_at_call, message_id, channel_id, timestamp,
                    timeframe_tag, resolution_window, asset_class, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
                (member_id, username, ticker, direction, timeframe,
                 price, message_id, channel_id, timestamp,
                 timeframe_tag, resolution_window, asset_class),
            )
        return True
    except sqlite3.IntegrityError:
        return False  # duplicate message_id

# ─────────────────────────────────────────────
# Price lookup
# ─────────────────────────────────────────────

def get_crypto_price(symbol: str) -> float | None:
    """CoinMarketCap latest quote."""
    try:
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
        r = requests.get(url, params={"symbol": symbol}, headers=headers, timeout=5)
        r.raise_for_status()
        data = r.json()
        return data["data"][symbol]["quote"]["USD"]["price"]
    except Exception as exc:
        log.warning(f"CMC price lookup failed for {symbol}: {exc}")
        return None


def get_stock_price(ticker: str) -> float | None:
    """Polygon.io latest trade price."""
    try:
        url = f"https://api.polygon.io/v2/last/trade/{ticker}"
        r = requests.get(url, params={"apiKey": POLYGON_API_KEY}, timeout=5)
        r.raise_for_status()
        data = r.json()
        return data["results"]["p"]
    except Exception as exc:
        log.warning(f"Polygon price lookup failed for {ticker}: {exc}")
        return None


def get_hyperliquid_price(symbol: str) -> float | None:
    """Hyperliquid API — covers HYPE, JTO, WIF, BONK, and 200+ tokens."""
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
    except Exception as exc:
        log.warning(f"Hyperliquid price lookup failed for {symbol}: {exc}")
        return None


def get_coingecko_price(symbol: str) -> float | None:
    """CoinGecko search fallback."""
    try:
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/search?query={symbol}",
            timeout=5
        )
        results = resp.json().get("coins", [])
        if not results:
            return None
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
    except Exception as exc:
        log.warning(f"CoinGecko price lookup failed for {symbol}: {exc}")
        return None


def get_price(ticker: str, asset_class: str = "crypto") -> float | None:
    """Price lookup chain depending on asset class."""
    if asset_class in ("equity", "etf"):
        price = get_stock_price(ticker)
        if price:
            return price
        price = get_crypto_price(ticker)
        if price:
            return price
        return None

    # Crypto or commodity
    price = get_crypto_price(ticker)
    if price:
        return price
    price = get_hyperliquid_price(ticker)
    if price:
        return price
    price = get_coingecko_price(ticker)
    if price:
        return price
    return get_stock_price(ticker)

# ─────────────────────────────────────────────
# Member role resolution
# ─────────────────────────────────────────────

def resolve_member_level(member: discord.Member) -> str:
    for role in member.roles:
        if role.name in ROLE_LEVEL_MAP:
            return ROLE_LEVEL_MAP[role.name]
    return "general"

# ─────────────────────────────────────────────
# GPT call parser
# ─────────────────────────────────────────────

async def parse_call(content: str, client: AsyncOpenAI) -> dict | None:
    """Returns parsed dict if has_call=True and confidence=high, else None."""
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": GPT_SYSTEM_PROMPT},
                {"role": "user",   "content": content},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=150,
        )
        raw = response.choices[0].message.content
        parsed = json.loads(raw)
        if parsed.get("has_call") and parsed.get("confidence") == "high":
            return parsed
    except Exception as exc:
        log.error(f"GPT parse error: {exc}")
    return None

# ─────────────────────────────────────────────
# Alert helpers
# ─────────────────────────────────────────────

async def post_to_channels(content: str, channel_ids: list[str], token: str):
    """Post a message to multiple Discord channels via REST API."""
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        for channel_id in channel_ids:
            try:
                await session.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers=headers,
                    json={"content": content},
                )
            except Exception as exc:
                log.warning(f"Failed to post alert to channel {channel_id}: {exc}")


async def check_ticker_consensus(ticker: str, direction: str):
    """Post consensus alert when exactly 3 same-ticker same-direction calls happen today."""
    today_start = int(datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())

    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(DISTINCT member_id) as cnt, AVG(price_at_call) as avg_price
               FROM calls
               WHERE ticker = ? AND direction = ? AND timestamp >= ?""",
            (ticker, direction, today_start)
        ).fetchone()

    count     = row["cnt"] if row else 0
    avg_price = row["avg_price"] if row else None

    if count != 3:  # Only fire at exactly 3 to avoid re-alerting
        return

    if direction == "bullish":
        msg = f"📡 Community Consensus: **{ticker}**\n{count} members went bullish on {ticker} today."
    else:
        msg = f"📡 Community Fade: **{ticker}**\n{count} members went bearish on {ticker} today."

    if avg_price:
        msg += f"\nEntry avg: ${avg_price:,.2f}"
    msg += "\n`/leaderboard` to see who's calling it."

    await post_to_channels(msg, DELIVERY_CHANNELS, DISCORD_TOKEN)
    log.info(f"Ticker consensus alert posted: {ticker} {direction} (count={count})")

# ─────────────────────────────────────────────
# Core processing
# ─────────────────────────────────────────────

async def process_message(
    message_id: str,
    channel_id: str,
    author_id: str,
    author_name: str,
    content: str,
    timestamp: int,
    guild: discord.Guild,
    openai_client: AsyncOpenAI,
):
    """Parse a message and store call if detected."""
    if not content.strip():
        return

    parsed = await parse_call(content, openai_client)
    if parsed is None:
        return

    ticker     = parsed.get("ticker") or ""
    direction  = parsed.get("direction") or ""
    timeframe  = parsed.get("timeframe")
    asset_type = parsed.get("asset_type", "unknown")

    if not ticker or not direction:
        return

    ticker = ticker.upper()

    # V2: tag timeframe and determine resolution window
    timeframe_tag, resolution_window = tag_timeframe(timeframe)
    asset_class = ASSET_CLASS_MAP.get(asset_type, "crypto")

    log.info(f"Call detected: {ticker} ({asset_class}) {direction} [{timeframe_tag}] "
             f"by {author_name} in #{MONITORED_CHANNELS.get(channel_id, channel_id)}")

    # Resolve member level
    level = "general"
    try:
        member = guild.get_member(int(author_id))
        if member is None:
            member = await guild.fetch_member(int(author_id))
        level = resolve_member_level(member)
    except Exception as exc:
        log.warning(f"Could not resolve member {author_id}: {exc}")

    upsert_member(author_id, author_name, level)

    # Fetch price at time of call (run in thread pool to avoid blocking event loop)
    price = await asyncio.to_thread(get_price, ticker, asset_class)
    if price is None:
        log.warning(f"Could not fetch price for {ticker}, storing call without price")

    inserted = insert_call(
        member_id=author_id,
        username=author_name,
        ticker=ticker,
        direction=direction,
        timeframe=timeframe,
        price=price,
        message_id=message_id,
        channel_id=channel_id,
        timestamp=timestamp,
        timeframe_tag=timeframe_tag,
        resolution_window=resolution_window,
        asset_class=asset_class,
    )

    if inserted:
        log.info(f"Stored call: {ticker} {direction} @ {price} (timeframe: {timeframe_tag}, window: {resolution_window}d)")
        # Check ticker consensus after insert
        await check_ticker_consensus(ticker, direction)
    else:
        log.info(f"Duplicate message {message_id}, skipping")

# ─────────────────────────────────────────────
# Backfill helper
# ─────────────────────────────────────────────

async def fetch_messages_after(channel_id: str, after_id: str, token: str) -> list[dict]:
    """Fetch messages after a given message ID using Discord REST API."""
    headers = {"Authorization": f"Bot {token}"}
    messages = []
    last_id = after_id

    async with aiohttp.ClientSession() as session:
        while True:
            url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
            params = {"after": last_id, "limit": 100}
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    break
                batch = await resp.json()
                if not batch:
                    break
                batch.sort(key=lambda m: int(m["id"]))
                messages.extend(batch)
                last_id = batch[-1]["id"]
                if len(batch) < 100:
                    break
                await asyncio.sleep(0.5)

    return messages


async def backfill_channel(
    channel_id: str,
    guild: discord.Guild,
    openai_client: AsyncOpenAI,
):
    meta_key = f"last_processed_{channel_id}"
    last_id = get_meta(meta_key)

    if not last_id:
        log.info(f"No backfill anchor for channel {channel_id}, skipping backfill")
        return

    channel_name = MONITORED_CHANNELS.get(channel_id, channel_id)
    log.info(f"Backfilling #{channel_name} after message {last_id} ...")

    messages = await fetch_messages_after(channel_id, last_id, DISCORD_TOKEN)
    log.info(f"Backfill: {len(messages)} missed messages in #{channel_name}")

    for msg in messages:
        if msg.get("author", {}).get("bot"):
            continue

        await process_message(
            message_id=msg["id"],
            channel_id=channel_id,
            author_id=msg["author"]["id"],
            author_name=msg["author"].get("username", "unknown"),
            content=msg.get("content", ""),
            timestamp=int(
                datetime.fromisoformat(
                    msg["timestamp"].replace("Z", "+00:00")
                ).timestamp()
            ),
            guild=guild,
            openai_client=openai_client,
        )

        set_meta(meta_key, msg["id"])

    log.info(f"Backfill complete for #{channel_name}")

# ─────────────────────────────────────────────
# Discord bot
# ─────────────────────────────────────────────

class CallTrackerBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        """Register slash commands on startup."""
        from slash_commands import register_commands
        register_commands(self.tree)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Slash commands synced")

    async def on_ready(self):
        log.info(f"Bot connected as {self.user} (id={self.user.id})")

        guild = self.get_guild(GUILD_ID)
        if guild is None:
            log.error(f"Guild {GUILD_ID} not found")
            return

        async def run_backfills():
            for channel_id in MONITORED_CHANNELS:
                try:
                    await backfill_channel(channel_id, guild, self.openai_client)
                except Exception as exc:
                    log.error(f"Backfill error for channel {channel_id}: {exc}")
            log.info("Backfill complete for all channels.")

        asyncio.create_task(run_backfills())
        log.info("Listening for new messages ...")

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        channel_id = str(message.channel.id)
        if channel_id not in MONITORED_CHANNELS:
            return

        guild = message.guild
        if guild is None:
            return

        ts = int(message.created_at.replace(tzinfo=timezone.utc).timestamp())

        await process_message(
            message_id=str(message.id),
            channel_id=channel_id,
            author_id=str(message.author.id),
            author_name=str(message.author),
            content=message.content,
            timestamp=ts,
            guild=guild,
            openai_client=self.openai_client,
        )

        set_meta(f"last_processed_{channel_id}", str(message.id))

    async def on_error(self, event_method: str, *args, **kwargs):
        log.exception(f"Unhandled error in {event_method}")

# ─────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────

def main():
    if not DISCORD_TOKEN:
        log.error("DISCORD_TOKEN is not set — exiting")
        sys.exit(1)
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY is not set — exiting")
        sys.exit(1)

    log.info("Starting RV Call Tracker V2 ...")
    bot = CallTrackerBot()

    try:
        bot.run(DISCORD_TOKEN, log_handler=None)
    except discord.LoginFailure:
        log.error("Invalid Discord token")
        sys.exit(1)
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
