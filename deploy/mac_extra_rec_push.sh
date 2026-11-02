#!/usr/bin/env bash
# Mac-side recorder for the added sources the election server cannot reach (AWS WAF / Incapsula challenge its IP):
# Iowa and Colorado Clarity (clarity_*), Ottawa County MI Clarity (mi_ottawa_*), Kenosha County WI (wi_kenosha).
# Runs brunder.us_extra_rec from THIS checkout (no sudo locally) into a local archive and pushes the new snapshot files
# into the server's extra archive every 30 s (rsync over `ssh oracle-slotbank`, files written as user brunder via
# `sudo -n -u brunder rsync`). Only the key folders and the Mac recorder's poll marker .poll/mac.json (per-key last
# successful poll: the server's stale flag) are pushed: never index.jsonl, .poll/extra.json or the manifest, so the
# server-side recorder's own files are untouched; rsync writes into dot-named temp files that the archive reader
# ignores, then renames, so the live process only ever sees complete snapshots. The server never makes a request for
# these hosts. Robust to network drops: the recorder is restarted if it exits early, a failed push is retried on the
# next tick; nothing here exits before UNTIL. Wrapped in caffeinate so the Mac does not sleep.
#
#   deploy/mac_extra_rec_push.sh                     # night: waits for 2026-11-03T23:30Z, runs to 2026-11-04T10:00Z
#   START=now UNTIL=<iso> DEST=<server dir> deploy/mac_extra_rec_push.sh     # test against a scratch dir
set -u
cd "$(dirname "$0")/.."
START=${START:-2026-11-03T23:30:00Z}
UNTIL=${UNTIL:-2026-11-04T10:00:00Z}
LOCAL=${LOCAL:-${BRUNDER_DATA:-$HOME/brunder-data-local}/raw/snap/us_midterms_2026_extra_mac}
DEST=${DEST:-/srv/brunder/repo/data/raw/snap/us_midterms_2026_extra}
HOST=${HOST:-oracle-slotbank}
ONLY=(--only clarity_ --only mi_ottawa --only wi_kenosha --marker mac)
LOG="$LOCAL.log"
UV=${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}

if [ -z "${CAFFEINATED:-}" ]; then
  export CAFFEINATED=1
  exec caffeinate -dims "$0" "$@"
fi
mkdir -p "$LOCAL"
epoch() { python3 -c "import sys,datetime as d;s=sys.argv[1];print(int(d.datetime.now(d.timezone.utc).timestamp()) if s=='now' else int(d.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()))" "$1"; }
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
T_START=$(epoch "$START"); T_UNTIL=$(epoch "$UNTIL")
while [ "$(epoch now)" -lt "$T_START" ]; do sleep 20; done
log "start: local $LOCAL -> $HOST:$DEST until $UNTIL"

record() {   # restart the recorder until UNTIL whatever its exit status
  while [ "$(epoch now)" -lt "$T_UNTIL" ]; do
    "$UV" run --frozen python -m brunder.us_extra_rec --out "$LOCAL" "${ONLY[@]}" --until "$UNTIL" >>"$LOG" 2>&1
    log "recorder exited ($?)"; [ "$(epoch now)" -lt "$T_UNTIL" ] && sleep 30
  done
}
push() {
  ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" "sudo -n -u brunder mkdir -p '$DEST'" >>"$LOG" 2>&1 || return 1
  rsync -a --timeout=30 -e "ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=10" \
    --rsync-path="sudo -n -u brunder rsync" \
    --include='clarity_*/***' --include='mi_ottawa*/***' --include='wi_kenosha*/***' \
    --include='.poll/' --include='.poll/mac.json' --exclude='*' \
    "$LOCAL/" "$HOST:$DEST/" >>"$LOG" 2>&1
}
record &
REC=$!
trap 'kill $REC 2>/dev/null' EXIT
while [ "$(epoch now)" -lt "$T_UNTIL" ]; do
  if push; then log "push ok ($(find "$LOCAL" -name '*Z.*' | wc -l | tr -d ' ') local snapshots)"; else log "push FAILED, retry in 30 s"; fi
  sleep 30
done
push && log "final push ok" || log "final push FAILED"
wait $REC
log "done"
