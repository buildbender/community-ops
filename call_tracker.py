"""
call_tracker.py — RV Member Call Tracker
Listens to Discord channels for explicit investment calls and stores them in SQLite.
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
from openai import AsyncOpenAI

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

DISCORD_TOKEN   = os.environ.get("DISCORD_TOKEN", "")
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "")
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
CMC_API_KEY     = os.environ.get("COINMARKETCAP_API_KEY", "")

DB_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.db")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "call_tracker.log")

GUILD_ID = 921411652115644436

MONITORED_CHANNELS = {
    "928036731410849902": "pro-chat",
    "927678002135973918": "crypto",
    "1027573555975688223": "degen",
}

CRYPTO_SYMBOLS = {
    # Layer 1s / majors
    "BTC", "ETH", "SOL", "BNB", "XRP", "AVAX", "ADA", "DOT", "ATOM", "NEAR",
    "APT", "SUI", "TON", "TRX", "FTM", "ALGO",
    # DeFi
    "LINK", "UNI", "AAVE", "MKR", "CRV", "LDO", "PENDLE", "GMX",
    "ARB", "OP", "JUP", "ORCA", "RAY", "JTO",
    # Perp DEXes
    "HYPE", "EDGE",
    # Meme / culture
    "DOGE", "SHIB", "PEPE", "WIF", "BONK", "BOME", "POPCAT", "MEW",
    # NFT tokens
    "PENGU", "BLUR", "LOOKS",
    # AI / infra
    "RENDER", "FET", "AGIX", "IO", "AKT", "WLD",
    # Other notable
    "IMX", "INJ", "SEI", "TIA", "PYTH", "DRIFT", "KMNO",
}

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
- Stocks and ETFs are valid (e.g. NVDA, TSLA, DRAM, AEHR, HIMZ, SPY, QQQ).
- Crypto tokens are valid regardless of whether they are well-known (e.g. HYPE, EDGE, PENGU, JUP).
- Extract the ticker symbol only — NOT the full name. e.g. "EdgeX" -> "EDGE", "Hyperliquid" -> "HYPE".
- Extract timeframe ONLY if explicitly stated.
- High confidence: clear buy/sell statement with named ticker. Medium: ambiguous. Low: vague.

Return JSON only:
{"has_call": true/false, "ticker": "SYMBOL or null", "direction": "bullish|bearish|null", \
"timeframe": "string or null", "confidence": "high|medium|low", "asset_type": "crypto|stock|etf|commodity|unknown"}\
"""

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
# Database helpers
# ─────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
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
) -> bool:
    """Returns True if inserted, False if duplicate."""
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO calls
                   (member_id, username, ticker, direction, timeframe,
                    price_at_call, message_id, channel_id, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (member_id, username, ticker, direction, timeframe,
                 price, message_id, channel_id, timestamp),
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
    """CoinGecko search fallback — tries to match symbol to a coin id."""
    try:
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/search?query={symbol}",
            timeout=5
        )
        results = resp.json().get("coins", [])
        if not results:
            return None
        # Use first result that matches symbol exactly
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


def get_price(ticker: str, asset_type: str = "unknown") -> float | None:
    """Price lookup chain depending on asset type.
    crypto/unknown: CMC → Hyperliquid → CoinGecko → Polygon
    stock/etf:      Polygon → CMC (in case it's also a crypto ticker)
    """
    if asset_type in ("stock", "etf"):
        # Stocks/ETFs: go straight to Polygon
        price = get_stock_price(ticker)
        if price:
            return price
        # Some tickers overlap (e.g. LINK is also a stock), try crypto as fallback
        price = get_crypto_price(ticker)
        if price:
            return price
        return None

    # Crypto or unknown
    if ticker in CRYPTO_SYMBOLS:
        price = get_crypto_price(ticker)
        if price:
            return price
    # Hyperliquid — great coverage for newer/Solana tokens
    price = get_hyperliquid_price(ticker)
    if price:
        return price
    # CoinGecko search fallback
    price = get_coingecko_price(ticker)
    if price:
        return price
    # Last resort: maybe it's a stock
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
    """
    Returns parsed dict if has_call=True and confidence=high, else None.
    """
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

    log.info(f"Call detected: {ticker} ({asset_type}) {direction} by {author_name} in #{MONITORED_CHANNELS.get(channel_id, channel_id)}")

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

    # Price lookup (non-blocking; failure is tolerated)
    price = await asyncio.get_event_loop().run_in_executor(
        None, get_price, ticker, asset_type
    )

    stored = insert_call(
        member_id=author_id,
        username=author_name,
        ticker=ticker,
        direction=direction,
        timeframe=timeframe,
        price=price,
        message_id=message_id,
        channel_id=channel_id,
        timestamp=timestamp,
    )

    if stored:
        log.info(f"Stored call #{message_id}: {ticker} {direction} @{price} ({level})")
    else:
        log.debug(f"Duplicate message {message_id}, skipped")


# ─────────────────────────────────────────────
# Backfill helpers
# ─────────────────────────────────────────────

async def fetch_messages_after(
    channel_id: str,
    after_id: str,
    token: str,
) -> list[dict]:
    """
    Paginate Discord REST API to fetch all messages after after_id.
    Returns messages in chronological order.
    """
    headers = {"Authorization": f"Bot {token}"}
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    all_messages: list[dict] = []
    last_id = after_id
    retry_after = 1.0

    async with aiohttp.ClientSession() as session:
        while True:
            params = {"after": last_id, "limit": 100}
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 429:
                    data = await resp.json()
                    wait = data.get("retry_after", retry_after)
                    log.warning(f"Rate limited on backfill, waiting {wait}s")
                    await asyncio.sleep(wait)
                    retry_after = min(retry_after * 2, 60)
                    continue

                if resp.status != 200:
                    log.error(f"Discord API error {resp.status} fetching channel {channel_id}")
                    break

                retry_after = 1.0
                batch: list[dict] = await resp.json()

                if not batch:
                    break

                # Discord returns newest-first; reverse to chronological
                batch.sort(key=lambda m: int(m["id"]))
                all_messages.extend(batch)
                last_id = batch[-1]["id"]

    return all_messages


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
        # Skip bots
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

        # Track progress in case of crash mid-backfill
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

    async def on_ready(self):
        log.info(f"Bot connected as {self.user} (id={self.user.id})")

        guild = self.get_guild(GUILD_ID)
        if guild is None:
            log.error(f"Guild {GUILD_ID} not found — check bot is in the server")
            return

        # Backfill missed messages for each monitored channel
        for channel_id in MONITORED_CHANNELS:
            try:
                await backfill_channel(channel_id, guild, self.openai_client)
            except Exception as exc:
                log.error(f"Backfill error for channel {channel_id}: {exc}")

        log.info("Listening for new messages ...")

    async def on_message(self, message: discord.Message):
        # Skip bots
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

        # Update the backfill anchor
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

    log.info("Starting RV Call Tracker ...")
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
