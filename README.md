# community-ops

A community intelligence system for Discord — tracks member investment calls, measures P&L performance, and generates daily + weekly AI-powered summaries.

Built for Real Vision's Discord community. Runs as a background service on macOS.

---

## What it does

### Member Call Tracker
- Listens to Discord channels in real-time
- Uses GPT-4o-mini to detect explicit investment calls (e.g. "going long BTC", "shorting NVDA")
- Logs ticker, direction, timeframe, member level, and entry price to SQLite
- Backfills missed messages on restart

### P&L Follow-up Checker
- Checks prices at 24h, 7d, and 30d after each call
- Records % change and win/loss result
- Builds a performance record for every member over time

### Member Leaderboard
- Ranks members by win rate (minimum 3 calls to qualify)
- Shows total calls, wins, losses, and best single call

### Daily Discord Summary
- Runs every morning at 9am ET (via AI agent cron)
- Reads last 24h of channel activity
- Synthesizes top 3 themes, fact-checks specific claims, scores community sentiment (0-100)
- Posts to Discord with member call data and most accurate caller of the week

### Weekly Digest
- Runs every Friday at 9am ET
- Week-in-review across all channels
- Full leaderboard, most accurate caller, 7-day sentiment trend

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/buildbender/rv-community-ops.git
cd rv-community-ops
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

Required keys:
- `DISCORD_TOKEN` — your Discord bot token
- `OPENAI_API_KEY` — for call extraction (gpt-4o-mini)
- `COINMARKETCAP_API_KEY` — crypto price lookups
- `POLYGON_API_KEY` — stock/ETF price lookups
- `FINNHUB_API_KEY` — fallback for sentiment chart

### 3. Initialize the database

```bash
python3 setup_db.py
```

### 4. Run as macOS Launch Agent (recommended)

```bash
# Edit com.rv.calltracker.plist.example with your paths, then:
cp com.rv.calltracker.plist.example com.rv.calltracker.plist
bash install.sh
```

### 5. Run manually

```bash
source .env  # or export vars manually
python3 call_tracker.py
```

---

## Usage

```bash
# Query recent calls
python3 query_calls.py --hours 24
python3 query_calls.py --ticker BTC --hours 48

# View leaderboard
python3 query_calls.py --leaderboard

# Update P&L on all eligible calls
python3 pnl_checker.py

# Preview P&L updates without writing
python3 pnl_checker.py --dry-run

# Export data
python3 query_calls.py --export-csv
python3 query_calls.py --export-json
```

---

## Stack

- Python 3.11+
- discord.py
- OpenAI API (gpt-4o-mini)
- SQLite
- CoinMarketCap, Polygon.io, Hyperliquid, CoinGecko APIs
- matplotlib (sentiment charts)
- macOS launchd (background service)

---

## License

MIT
