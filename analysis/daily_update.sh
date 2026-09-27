#!/bin/bash
# Daily refresh of the East Metro dashboard and 3D twin from the canonical Hermes data.
# Runs from launchd (com.cd.eastmetro.daily) at 15:17, after Hermes's 06:00 inventory and ~12:55 3-bed research.
# Reads Hermes read-only; rebuilds the pages and marks them for publishing only when the analysis changed.
set -euo pipefail

ROOT="/Users/cd/east-metro-analysis"
OUT="$ROOT/analysis/output"
LOG="$OUT/daily_update.log"
HERMES="$HOME/.hermes/projects/real-estate-radar"
PY=/usr/bin/python3
export PATH="/Users/cd/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

exec >>"$LOG" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') daily update"
cd "$ROOT"

# Don't read while Hermes's inventory writer holds its lock (wait up to 30 min).
for i in $(seq 1 30); do
  pid=$(sed -nE 's/^pid=([0-9]+).*/\1/p' "$HERMES/data/real_estate.sqlite3.inventory.lock" 2>/dev/null || true)
  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then break; fi
  echo "Hermes inventory writer $pid active; waiting"; sleep 60
done

before=$(shasum -a 256 "$OUT/analysis.json" 2>/dev/null | cut -d' ' -f1 || true)
$PY analysis/market_analysis.py --hermes "$HERMES" --out "$OUT/analysis.json" 2>&1 | grep -v -i "warning" | head -3
$PY -c "import json,sys; json.loads(open('$OUT/analysis.json').read(), parse_constant=lambda c: sys.exit('non-strict JSON: '+c))"
after=$(shasum -a 256 "$OUT/analysis.json" | cut -d' ' -f1)

if [ "$before" = "$after" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "No change in the Hermes data since the last run; nothing to publish."
  exit 0
fi

$PY analysis/build_dashboard.py
(cd analysis && $PY build_twin.py | tail -1)
$PY analysis/build_home_app.py
$PY analysis/build_platform.py

# Headless Claude has no Artifact tool, so publishing happens in a Claude session: leave a marker for it.
date '+%F %T' > "$OUT/PENDING_PUBLISH"
echo "Rebuilt; marked for publishing ($OUT/PENDING_PUBLISH)."
echo "=== done $(date '+%H:%M:%S')"
