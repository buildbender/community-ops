# Build Complete

## Files Created

| File | Description |
|------|-------------|
| `pnl_checker.py` | New script — checks prices at 24h/7d/30d after each call and records win/loss/pnl to the DB. Run via cron or manually. Supports --dry-run. |
| `weekly_digest_prompt.txt` | Full agent prompt for the Friday 9am weekly digest cron — includes leaderboard, most accurate caller, sentiment trend, themes, and pin/unpin logic. |
| `daily_summary_patch.md` | Patch instructions for adding "Most Accurate Caller (Last 7 Days)" to the existing daily summary cron. |
| `BUILD_COMPLETE.md` | This file. |

## Files Modified

| File | What Changed |
|------|-------------|
| `setup_db.py` | Fixed hardcoded DB path → relative path. Added P&L column migration (ALTER TABLE with try/except, safe to re-run). |
| `query_calls.py` | Fixed hardcoded DB path → relative path. Added --leaderboard flag (rank, win rate, best call). Added --export-csv flag. Added --export-json flag. Added --min-calls option. |

## New DB Columns (added to `calls` table)

| Column | Type | Description |
|--------|------|-------------|
| price_24h | REAL | Price fetched 24h after call |
| price_7d | REAL | Price fetched 7d after call |
| price_30d | REAL | Price fetched 30d after call |
| pnl_24h | REAL | % change at 24h |
| pnl_7d | REAL | % change at 7d |
| pnl_30d | REAL | % change at 30d |
| result_24h | TEXT | "win" or "loss" at 24h |
| result_7d | TEXT | "win" or "loss" at 7d |
| result_30d | TEXT | "win" or "loss" at 30d |

## Next Steps

1. Run `python3 setup_db.py` to migrate the live DB with the new columns
2. Run `python3 pnl_checker.py --dry-run` to preview what would be updated
3. Add pnl_checker.py to cron (suggested: every 6 hours)
4. Apply daily_summary_patch.md to the existing daily summary cron prompt
5. Create the weekly digest cron using weekly_digest_prompt.txt (suggested: every Friday 9am ET)
6. Do `gh auth login` then push everything to GitHub as `buildbender`
