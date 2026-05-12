# Community Ops V2 — Spec Document
**Status:** Pending goldmember approval
**Date:** 2026-05-11
**Prepared by:** Marty

---

## Overview

V2 upgrades the existing Member Call Tracker and renames/restructures the Daily Discord Summary into the **Community Summary**. The core philosophy: open calls track live, closed calls build records, and the community gets real-time signal plus a bi-weekly editorial layer.

---

## Part 1: Member Call Tracker V2

### 1.1 Call Timeframe Tagging

At parse time, GPT-4o-mini extracts not just the call but the stated timeframe from the message.

**Timeframe tiers:**
- **Short-term** — "this week", "today", "next few days" → 7d resolution window
- **Medium-term** — "this month", "end of month", "next few weeks" → 30d resolution window
- **Long-term** — "long term", "cycle play", "next year", "accumulating" → 30d+ resolution window, marked open until closed
- **Unspecified** — no timeframe stated → defaults to 7d resolution window

**WIN/LOSS logic:**
- A WIN is when the asset moves in the called direction by the time the resolution window closes. Direction only, not magnitude.
- WIN/LOSS is determined at the resolution window matching the stated timeframe — not cherry-picked across windows.
- All three check windows (24h, 7d, 30d) still run and are stored. Only the timeframe-matched window counts toward the member's record and leaderboard ranking.

**Example:**
```
BTC Long — entry $62,000
Timeframe: Short-term (7d window)
7d: $64,000 → +3.2% WIN
30d: $61,000 → -1.6% (informational only)
```

**DB change required:** Add `timeframe` column (short/medium/long/unspecified) and `resolution_window` column (7/30/open) to calls table.

---

### 1.2 Open Call Live Tracking

Calls that have not hit their resolution window are tracked as live open positions, not marked "pending."

**Display format (in /calls and Community Summary):**
```
BTC Long — Oct 15 @ $62,400
Status: OPEN (3 weeks in)
Current: $67,200 → +7.7%
Resolution: 30d window (7 days left)
```

- WIN/LOSS is NOT stamped until the resolution window closes.
- Open calls are excluded from win rate and leaderboard calculations.
- A "Live Positions" section is surfaced separately in /calls output.
- Hot streak alerts only trigger on closed wins, not open positions.

---

### 1.3 Asset Class Expansion

**Current limitation:** Crypto symbols only (hardcoded list).

**V2 fix:** Dynamic asset detection via GPT-4o-mini at parse time. Extend price fallback chain:

- **Crypto:** CMC → Hyperliquid → CoinGecko (existing)
- **Stocks/ETFs:** Polygon → Finnhub (new)

**Tickers to support:** All crypto + US equities/ETFs (SPY, GLD, TLT, QQQ, NVDA, etc.)

**No hardcoded symbol list.** If the LLM identifies a recognized ticker with confidence, attempt price fetch. Log failures for review.

---

### 1.4 Slash Commands

**`/calls @member`**
- Any member can run this to pull any other member's full call history.
- Shows: all calls (open and closed), win/loss record, win%, avg return, best call, current streaks.
- Format example:

```
📊 woodman's Call History

OPEN POSITIONS
BTC Long — entry $62,400 | 3 weeks in | +7.7% (30d window, 7d left)

CLOSED CALLS (Last 30 Days)
SOL Long — +41.2% WIN (7d)
ETH Long — +18.9% WIN (7d)
AVAX Long — -6.1% LOSS (7d)
BTC Long — +12.4% WIN (30d)

RECORD
Win Rate: 84.6% (11W / 2L)
Avg Return: +12.4% per closed call
Best Call: SOL +41.2%
Current Streak: 4 wins
```

---

**`/leaderboard`**
- Shows full ranked leaderboard sorted by win%.
- Minimum 3 closed calls to qualify.
- Format example:

```
🏆 RV Community Call Leaderboard
Week of May 5-11, 2026

Rank  Member        W    L    Win%    Avg Return  Best Call
1     woodman       11   2    84.6%   +12.4%      SOL +41.2%
2     cryptonaut    8    3    72.7%   +9.1%       ETH +18.9%
3     macro_mike    7    3    70.0%   +5.8%       SPY +6.4%
4     degen_dan     6    4    60.0%   +7.2%       BTC +22.1%
5     rallycat      5    4    55.6%   +8.9%       AVAX +31.7%

Min. 3 closed calls to qualify
woodman is on a 4-call hot streak
3 members have open positions tracking live

/calls @member — view any member's full call history
```

---

### 1.5 Real-Time Alerts (Standalone Posts)

These fire immediately and are NOT held for the bi-weekly Community Summary. They are breaking news, not editorial.

**Hot Streak Alert**
Triggers when a member closes their 3rd consecutive winning call.

```
🔥 Hot Streak Alert
woodman is on a 4-call win streak.
Last call: ETH Long +8.2%
/calls @woodman to see the full record.
```

**Ticker Consensus Alert**
Triggers when 3 or more members make bullish calls on the same ticker within the same calendar day.
Mirror logic applies for bearish calls ("community fade").

Bullish example:
```
📡 Community Consensus: BTC
5 members went bullish on BTC today.
Entry avg: $103,200
/leaderboard to see who's calling it.
```

Bearish example:
```
📡 Community Fade: ETH
4 members went bearish on ETH today.
Entry avg: $2,410
```

---

### 1.6 Monthly Hall of Fame

Runs on the last day of each month. Posts a pinned message to the community channel recognizing the top caller for the month.

```
🏅 May Hall of Fame
Top Caller: woodman
Record: 84.6% win rate (11W/2L)
Best Call of the Month: SOL +41.2%
Congrats woodman. See you in June's leaderboard.
```

---

## Part 2: Community Summary (formerly Daily Discord Summary)

### 2.1 Schedule Change

**Old:** Daily at 9am ET + separate Weekly Digest on Fridays.
**New:** Twice weekly. No separate Weekly Digest cron — the Friday summary IS the week-in-review.

| Post | Time | Covers |
|------|------|--------|
| Weekend Wrap | Monday 9am ET | Friday, Saturday, Sunday |
| Week-in-Review | Friday 9am ET | Monday, Tuesday, Wednesday, Thursday |

**Monitored channels (V2 — expanded):**
| Channel | ID | Purpose |
|---|---|---|
| #pro-chat | 928036731410849902 | Call tracking + Community Summary data source |
| #crypto | 927678002135973918 | Call tracking + Community Summary data source |
| #degen | 1027573555975688223 | Call tracking + Community Summary data source |
| #hive-chat | 1232495023300546581 | Call tracking + Community Summary data source |

All four channels feed both the call tracker and the Community Summary analysis (themes, sentiment, fact-checking). #hive-chat is a full data source, not just a delivery target.

**Delivery channels for Community Summary and real-time alerts:**
| Channel | ID |
|---|---|
| #pro-chat | 928036731410849902 |
| #hive-chat | 1232495023300546581 |

Same post, same timing. No content differences between the two channels.
Implementation: content is generated once, then posted to each channel sequentially via two Discord API calls. No timeout risk — delivery adds ~2s of overhead after content generation.

**Cron changes required:**
- Remove daily 9am cron
- Remove separate weekly digest cron
- Add Monday 9am ET cron (Fri-Sun window)
- Add Friday 9am ET cron (Mon-Thu window)

---

### 2.2 Community Summary Structure

Sections are ordered as follows. Conditional sections are omitted if no data exists.

1. **Top 3 Themes** — key discussion topics from the period (existing)
2. **Sentiment Score** — community health score + delta vs previous summary (existing)
3. **Sentiment Overlay Chart** — BTC, SPY, NASDAQ vs Community Sentiment Score (new, see 2.3)
4. **Community Consensus** — tickers with 3+ same-direction calls in the period (new, conditional)
5. **Hot Streak** — any member currently on an active win streak (new, conditional)
6. **Member Calls** — calls logged during the coverage period (existing)
7. **Fact-Checked Claims** — verified claims with source tags (existing, upgraded per 2.4)
8. **Top 3 Callers** — snapshot leaderboard for the period (new)
9. **Most Accurate Caller** — #1 by win rate for the period with best call highlighted (existing patch)

---

### 2.3 Sentiment Overlay Chart

A line chart image posted with every Community Summary.

**Data series:**
- Community Sentiment Score (0-100, our internal metric)
- BTC price (indexed to 100 at start of window)
- SPY price (indexed to 100 at start of window)
- NASDAQ / QQQ price (indexed to 100 at start of window)

**Chart windows:**
- Monday post: Fri-Sun (3-day window)
- Friday post: Mon-Thu (4-day window)

**Visual spec:**
- Dark background, consistent with existing sentiment chart style
- Clear date labels on x-axis
- Legend identifying each series
- Sentiment line in brand accent color, price lines in neutral tones

**Price data sources:** Polygon (SPY/NASDAQ), CMC/CoinGecko (BTC)
**Long-term value:** Correlation patterns between community sentiment and price action become readable after 60-90 days of data.

---

### 2.4 Fact-Check Sourcing

Every fact-checked claim in the summary must be verified against a live API before posting. No unverified claims.

**Logic:**
- Price/asset claims → Polygon (stocks/ETFs) or CMC/CoinGecko (crypto)
- News/macro claims → NewsAPI or NewsData.io
- If no match found → mark as `[Source: unverified]`

**Format:** Append a source tag inline after each checked claim. No links.

Examples:
```
BTC is up 3.2% today [Source: CoinMarketCap]
SPY hit a new 52-week high [Source: Polygon]
Fed held rates at 4.25% [Source: NewsAPI]
Claim could not be verified [Source: unverified]
```

---

## Part 3: Database Schema Changes

The following columns need to be added to `calls.db`:

| Column | Type | Notes |
|--------|------|-------|
| `timeframe` | TEXT | short / medium / long / unspecified |
| `resolution_window` | INTEGER | 7 / 30 / null (open-ended) |
| `asset_class` | TEXT | crypto / equity / etf / commodity |
| `status` | TEXT | open / closed |
| `streak_count` | INTEGER | running win streak at time of close |

---

## Part 4: Build Priority

1. DB schema migration (timeframe, resolution_window, asset_class, status, streak_count)
2. Call parser update (timeframe tagging + asset expansion)
3. pnl_checker.py update (resolution window logic, open call live tracking)
4. /calls and /leaderboard slash commands
5. Real-time alert triggers (hot streak + ticker consensus)
6. Community Summary cron restructure (Monday/Friday, remove daily + weekly digest)
7. Sentiment overlay chart generator
8. Monthly Hall of Fame cron

---

## Resolved Questions

- Real-time alerts (hot streak, ticker consensus): same channels as Community Summary (#pro-chat + #hive-chat)
- Monthly Hall of Fame: regular post (not pinned)
- Ticker consensus threshold: 3+

---

*Spec prepared by Marty. Pending goldmember approval before build begins.*
