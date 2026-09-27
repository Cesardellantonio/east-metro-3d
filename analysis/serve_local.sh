#!/bin/bash
# Serve the full (private) platform and dashboard on this Mac only: http://localhost:8791/
OUT=/Users/cd/east-metro-analysis/analysis/output
PORT=${PORT:-8791}
cat > "$OUT/index.html" <<HTML
<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=platform/local.html"><a href="platform/local.html">Open Two Keys</a>
HTML
pkill -f "http.server $PORT" 2>/dev/null
cd "$OUT" && nohup /usr/bin/python3 -m http.server "$PORT" --bind 127.0.0.1 > "$OUT/local_server.log" 2>&1 &
echo "serving $OUT on http://localhost:$PORT/"
