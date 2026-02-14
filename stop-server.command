#!/bin/zsh
# Double-click to stop the DnD audio server
set -euo pipefail

APP_DIR="/Users/jonbridgman/Documents/dnd-local"
cd "$APP_DIR"

# Stop anything listening on localhost:8000 (the app server)
PIDS=$(lsof -t -iTCP:8000 -sTCP:LISTEN 2>/dev/null || true)
if [[ -z "$PIDS" ]]; then
  echo "No process found listening on port 8000."
  exit 0
fi

echo "Stopping processes on port 8000: $PIDS"
kill $PIDS
