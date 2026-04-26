#!/usr/bin/env bash
# install.sh — Set up and launch the RV Call Tracker as a macOS Launch Agent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.rv.calltracker.plist"
PLIST_LABEL="com.rv.calltracker"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DEST="$LAUNCH_AGENTS_DIR/$PLIST_LABEL.plist"

echo "=== RV Call Tracker Installer ==="

# ── 1. Set up virtual environment + install deps ───────────
VENV_DIR="$SCRIPT_DIR/venv"
echo ""
echo "[1/4] Setting up virtual environment and installing dependencies ..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

# ── 2. Initialize database ───────────────────
echo ""
echo "[2/4] Initializing database ..."
"$VENV_DIR/bin/python3" "$SCRIPT_DIR/setup_db.py"

# ── 3. Copy plist to LaunchAgents ─────────────
echo ""
echo "[3/4] Preparing Launch Agent plist ..."
mkdir -p "$LAUNCH_AGENTS_DIR"
cp "$PLIST_SRC" "$PLIST_DEST"
echo "Plist written to $PLIST_DEST"

# ── 4. Load the Launch Agent ─────────────────
echo ""
echo "[4/4] Loading Launch Agent ..."

# Unload first if already loaded (ignore errors)
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load -w "$PLIST_DEST"

# ── 5. Confirm it's running ──────────────────
echo ""
echo "Verifying service is running ..."
sleep 2

if launchctl list | grep -q "$PLIST_LABEL"; then
    STATUS=$(launchctl list "$PLIST_LABEL" 2>/dev/null | grep '"PID"' | awk '{print $3}' | tr -d ';' || echo "unknown")
    echo ""
    echo "=== DONE ==="
    echo "Service '$PLIST_LABEL' is loaded."
    echo "PID: $STATUS"
    echo "Logs: $SCRIPT_DIR/logs/call_tracker.log"
    echo ""
    echo "To stop:    launchctl unload $PLIST_DEST"
    echo "To restart: launchctl unload $PLIST_DEST && launchctl load -w $PLIST_DEST"
    echo "To check:   launchctl list $PLIST_LABEL"
else
    echo ""
    echo "WARNING: Service does not appear in launchctl list."
    echo "Check logs at: $SCRIPT_DIR/logs/call_tracker.log"
    exit 1
fi
