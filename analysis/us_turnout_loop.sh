#!/bin/sh
# Run analysis/us_turnout_publish.sh every 20 minutes until 2026-11-04T03:00Z, then exit. A failed run is logged and the loop goes on.
#   nohup sh analysis/us_turnout_loop.sh >> ~/.cache/brunder-turnout-loop.log 2>&1 &      stop early: kill the printed pid
# Start it from a checkout whose analysis/ has these scripts; the page itself must already be on origin/main.
STOP="${TURNOUT_STOP_UTC:-202611040300}"
HERE="$(cd "$(dirname "$0")" && pwd)"
echo "$(date -u +%FT%TZ) turnout loop started, pid $$, until ${STOP}"
while [ "$(date -u +%Y%m%d%H%M)" -lt "$STOP" ]; do
  sh "$HERE/us_turnout_publish.sh" "$@" || echo "$(date -u +%FT%TZ) run failed (exit $?)"
  sleep 1200
done
echo "$(date -u +%FT%TZ) stop time reached: loop ends"
