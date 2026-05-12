"""
slash_commands.py — Discord slash commands for RV Call Tracker V2

Commands:
    /calls @member  — View a member's full call history (open + closed)
    /leaderboard    — View full ranked leaderboard by win%
"""

import sqlite3
import os
import time
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.db")
log = logging.getLogger("call_tracker")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def register_commands(tree: app_commands.CommandTree):
    """Register slash commands onto the provided CommandTree."""

    @tree.command(name="calls", description="View a member's call history and record")
    @app_commands.describe(member="The member whose calls you want to see")
    async def calls_command(interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=False)
        try:
            conn = get_db()
            now = int(time.time())
            thirty_days_ago = now - (30 * 86400)

            # Open positions
            open_calls = conn.execute(
                """SELECT ticker, direction, price_at_call, current_price, current_pnl,
                          timeframe_tag, resolution_window, timestamp
                   FROM calls
                   WHERE member_id = ? AND status = 'open'
                   ORDER BY timestamp DESC""",
                (str(member.id),)
            ).fetchall()

            # Closed calls (last 30 days)
            closed_calls = conn.execute(
                """SELECT ticker, direction, result_final, pnl_7d, pnl_14d, pnl_30d, pnl_90d,
                          resolution_window, timeframe_tag, timestamp
                   FROM calls
                   WHERE member_id = ? AND status = 'closed' AND timestamp >= ?
                   ORDER BY timestamp DESC""",
                (str(member.id), thirty_days_ago)
            ).fetchall()

            # Record stats
            stats = conn.execute(
                """SELECT
                    COUNT(CASE WHEN result_final = 'win' THEN 1 END) as wins,
                    COUNT(CASE WHEN result_final = 'loss' THEN 1 END) as losses,
                    AVG(CASE WHEN result_final = 'win' AND resolution_window = 7  THEN pnl_7d
                             WHEN result_final = 'win' AND resolution_window = 14 THEN pnl_14d
                             WHEN result_final = 'win' AND resolution_window = 30 THEN pnl_30d
                             WHEN result_final = 'win' AND resolution_window = 90 THEN pnl_90d
                             WHEN result_final = 'win' AND resolution_window IS NULL THEN COALESCE(pnl_90d, pnl_30d)
                             ELSE NULL END) as avg_return,
                    MAX(CASE WHEN resolution_window = 7  THEN pnl_7d
                             WHEN resolution_window = 14 THEN pnl_14d
                             WHEN resolution_window = 30 THEN pnl_30d
                             WHEN resolution_window = 90 THEN pnl_90d
                             WHEN resolution_window IS NULL THEN COALESCE(pnl_90d, pnl_30d)
                             ELSE NULL END) as best_pnl,
                    MAX(streak_count) as best_streak,
                    (SELECT ticker FROM calls c2
                     WHERE c2.member_id = calls.member_id AND c2.result_final = 'win'
                     ORDER BY COALESCE(c2.pnl_7d, c2.pnl_30d) DESC LIMIT 1) as best_ticker,
                    (SELECT streak_count FROM calls c3
                     WHERE c3.member_id = calls.member_id AND c3.status = 'closed'
                     ORDER BY timestamp DESC LIMIT 1) as current_streak
                   FROM calls
                   WHERE member_id = ? AND status = 'closed'""",
                (str(member.id),)
            ).fetchone()

            conn.close()

            if not open_calls and not closed_calls and (not stats or (stats["wins"] or 0) + (stats["losses"] or 0) == 0):
                await interaction.followup.send(f"No calls tracked for **{member.display_name}** yet.")
                return

            lines = [f"📊 **{member.display_name}'s Call History**\n"]

            # Open positions
            if open_calls:
                lines.append("**OPEN POSITIONS**")
                for c in open_calls:
                    days_in = int((time.time() - c["timestamp"]) / 86400)
                    rw = c["resolution_window"]
                    days_left = max(0, (rw or 30) - days_in) if rw else "?"
                    pnl_str = f"{c['current_pnl']:+.1f}%" if c["current_pnl"] is not None else "pending"
                    window_str = f"{rw}d" if rw else "open"
                    price_str = f"${c['price_at_call']:,.4f}" if c["price_at_call"] else "no price"
                    lines.append(
                        f"• {c['ticker']} {c['direction'].upper()} — "
                        f"entry {price_str} | {days_in}d in | {pnl_str} "
                        f"({window_str} window, {days_left}d left)"
                    )
                lines.append("")

            # Closed calls
            if closed_calls:
                lines.append("**CLOSED CALLS (Last 30 Days)**")
                for c in closed_calls:
                    rw = c["resolution_window"]
                    if rw == 7:
                        pnl = c["pnl_7d"]
                    elif rw == 14:
                        pnl = c["pnl_14d"]
                    elif rw == 30:
                        pnl = c["pnl_30d"]
                    elif rw == 90:
                        pnl = c["pnl_90d"]
                    else:
                        pnl = c["pnl_90d"] or c["pnl_30d"]
                    pnl_str = f"{pnl:+.1f}%" if pnl is not None else "N/A"
                    result_emoji = "✅" if c["result_final"] == "win" else "❌"
                    window_label = f"{rw}d" if rw else "90d"
                    lines.append(
                        f"• {c['ticker']} {c['direction'].upper()} — "
                        f"{pnl_str} {result_emoji} ({window_label})"
                    )
                lines.append("")

            # Record
            if stats:
                wins   = stats["wins"] or 0
                losses = stats["losses"] or 0
                total  = wins + losses
                if total > 0:
                    win_pct = (wins / total) * 100
                    avg_ret = stats["avg_return"]
                    best_pnl = stats["best_pnl"]
                    best_ticker = stats["best_ticker"]
                    current_streak = stats["current_streak"] or 0

                    lines.append("**RECORD**")
                    lines.append(f"Win Rate: **{win_pct:.1f}%** ({wins}W / {losses}L)")
                    if avg_ret is not None:
                        lines.append(f"Avg Return: **{avg_ret:+.1f}%** per closed call")
                    if best_ticker and best_pnl is not None:
                        lines.append(f"Best Call: **{best_ticker} {best_pnl:+.1f}%**")
                    if current_streak >= 2:
                        lines.append(f"Current Streak: 🔥 {current_streak} wins")

            await interaction.followup.send("\n".join(lines))

        except Exception as exc:
            log.error(f"/calls command failed: {exc}", exc_info=True)
            try:
                await interaction.followup.send("⚠️ Something went wrong fetching that data. Try again in a moment.")
            except Exception:
                pass

    @tree.command(name="leaderboard", description="View the RV community call leaderboard")
    async def leaderboard_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        try:
            conn = get_db()

            rows = conn.execute("""
                SELECT
                    username,
                    COUNT(CASE WHEN result_final = 'win' THEN 1 END) as wins,
                    COUNT(CASE WHEN result_final = 'loss' THEN 1 END) as losses,
                    AVG(CASE WHEN result_final = 'win' AND resolution_window = 7  THEN pnl_7d
                             WHEN result_final = 'win' AND resolution_window = 14 THEN pnl_14d
                             WHEN result_final = 'win' AND resolution_window = 30 THEN pnl_30d
                             WHEN result_final = 'win' AND resolution_window = 90 THEN pnl_90d
                             WHEN result_final = 'win' AND resolution_window IS NULL THEN COALESCE(pnl_90d, pnl_30d)
                             ELSE NULL END) as avg_return,
                    MAX(CASE WHEN resolution_window = 7  THEN pnl_7d
                             WHEN resolution_window = 14 THEN pnl_14d
                             WHEN resolution_window = 30 THEN pnl_30d
                             WHEN resolution_window = 90 THEN pnl_90d
                             WHEN resolution_window IS NULL THEN COALESCE(pnl_90d, pnl_30d)
                             ELSE NULL END) as best_pnl,
                    (SELECT ticker FROM calls c2
                     WHERE c2.member_id = calls.member_id AND c2.result_final = 'win'
                     ORDER BY COALESCE(c2.pnl_7d, c2.pnl_30d) DESC LIMIT 1) as best_ticker,
                    (SELECT streak_count FROM calls c3
                     WHERE c3.member_id = calls.member_id AND c3.status = 'closed'
                     ORDER BY timestamp DESC LIMIT 1) as current_streak
                FROM calls
                WHERE status = 'closed'
                GROUP BY member_id
                HAVING (wins + losses) >= 3
                ORDER BY CAST(wins AS FLOAT) / (wins + losses) DESC, wins DESC
            """).fetchall()

            open_count = conn.execute(
                "SELECT COUNT(DISTINCT member_id) FROM calls WHERE status = 'open'"
            ).fetchone()[0]

            conn.close()

            if not rows:
                await interaction.followup.send(
                    "🏆 **RV Community Call Leaderboard**\n\nNo members with 3+ closed calls yet. Keep calling!"
                )
                return

            today = datetime.now(timezone.utc).strftime("%b %d, %Y")
            lines = [f"🏆 **RV Community Call Leaderboard**\n*As of {today}*\n"]

            medals = ["🥇", "🥈", "🥉"]
            hot_streak_member = None

            for i, row in enumerate(rows[:10], 1):
                wins   = row["wins"] or 0
                losses = row["losses"] or 0
                total  = wins + losses
                win_pct = (wins / total * 100) if total > 0 else 0
                avg_ret = row["avg_return"]
                best    = f"{row['best_ticker']} {row['best_pnl']:+.1f}%" if row["best_ticker"] and row["best_pnl"] is not None else "—"
                streak  = row["current_streak"] or 0
                rank    = medals[i - 1] if i <= 3 else f"{i}."

                avg_str = f"{avg_ret:+.1f}%" if avg_ret is not None else "—"
                lines.append(
                    f"{rank} **{row['username']}** — {win_pct:.1f}% ({wins}W/{losses}L) "
                    f"avg {avg_str} | best: {best}"
                )

                if streak >= 3 and hot_streak_member is None:
                    hot_streak_member = (row["username"], streak)

            lines.append(f"\n📊 Min. 3 closed calls to qualify")
            if hot_streak_member:
                lines.append(f"🔥 **{hot_streak_member[0]}** is on a {hot_streak_member[1]}-call hot streak")
            if open_count:
                lines.append(f"⏳ {open_count} member(s) have open positions tracking live")
            lines.append("\n`/calls @member` — view any member's full call history")

            await interaction.followup.send("\n".join(lines))

        except Exception as exc:
            log.error(f"/leaderboard command failed: {exc}", exc_info=True)
            try:
                await interaction.followup.send("⚠️ Something went wrong fetching the leaderboard. Try again in a moment.")
            except Exception:
                pass
