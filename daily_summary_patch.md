# Daily Summary Cron — Patch Notes

## What to add to the existing daily summary cron prompt

After the "Member Calls" section in the daily summary, add the following step and output block:

---

### Additional step (add to data gathering):

Run the leaderboard query to get the current top caller:
```
/Users/bijanmaleki/.openclaw/workspace/call-tracker/venv/bin/python3 /Users/bijanmaleki/.openclaw/workspace/call-tracker/query_calls.py --leaderboard --min-calls 3
```

### Additional section (add to the posted summary, after Member Calls):

```
🎯 Most Accurate Caller (Last 7 Days)
[username] — [win rate]% ([X]W/[Y]L) — Best call: [TICKER] +[pnl]%
```

- Pull rank #1 from the leaderboard output.
- If the leaderboard returns "(no members with 3+ calls yet)", omit this section entirely — do not post a placeholder.
- Format the pnl with a + sign for gains (e.g. +12.4%), - for losses (e.g. -3.1%).

---

## Why this is additive, not a rewrite

The existing daily summary cron prompt and all other sections remain unchanged. This patch adds one data-gathering step and one output section only.
