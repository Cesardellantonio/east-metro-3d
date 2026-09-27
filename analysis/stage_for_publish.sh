#!/bin/bash
# Copy the rebuilt pages into the Claude session's scratchpad, where the Artifact tool may attach supporting files.
set -euo pipefail
DEST="${1:?usage: stage_for_publish.sh <scratchpad>/publish}"
OUT=/Users/cd/east-metro-analysis/analysis/output
mkdir -p "$DEST/twin" "$DEST/platform"
cp "$OUT"/twin/{index.html,buildings.json,ground.png,walk.png,twin_data.json,terrain.png} "$DEST/twin/"
cp "$OUT"/platform/{index.html,platform_data.json,buildings.json,ground.png,walk.png,terrain.png} "$DEST/platform/"
echo "staged into $DEST"
