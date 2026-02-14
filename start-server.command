#!/bin/zsh
# Double-click to start the DnD audio server and open the app
set -euo pipefail

APP_DIR="/Users/jonbridgman/Documents/dnd-local"
cd "$APP_DIR"

# Start server in background
python3 server.py > server.log 2>&1 &
SERVER_PID=$!

# Give the server a moment to start
sleep 1

# Open the app in Google Chrome
open -a "Google Chrome" "http://127.0.0.1:8000/"

echo "Server started (PID $SERVER_PID). Logs: $APP_DIR/server.log"
