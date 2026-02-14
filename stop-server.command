#!/bin/zsh
# Double-click to stop the DnD audio server
set -euo pipefail

APP_DIR="/Users/jonbridgman/Documents/dnd-local"
cd "$APP_DIR"

# Find and stop any server.py process running from this app dir
PIDS=$(pgrep -f "python3 .*server.py" || true)
if [[ -z "$PIDS" ]]; then
  echo "No server.py process found."
  exit 0
fi

echo "Stopping server.py processes: $PIDS"
kill $PIDS
