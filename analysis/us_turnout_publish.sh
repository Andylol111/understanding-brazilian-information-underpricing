#!/bin/sh
# One data-only refresh of the public Election Day turnout files (site/data/ev2026/turnout_day.json, turnout_volume.json, turnout_model.json).
#   research turnout_day.json -> us_turnout_publish.py -> us_turnout_model.py (run from the publish worktree, so it is the version on
#   origin/main; skipped if that is not there yet; a failure keeps the last turnout_model.json) -> site_check on earlyvote.html and
#   turnout.html (chromium + webkit) -> commit ONLY the three turnout_*.json files -> fetch + rebase on origin/main -> push (up to 3
#   tries, never --force).
# It never touches html / js / css / __BUILD: the page ships once, in its reviewed commit, and must already be on origin/main.
# Runs in its own worktree (default ~/.cache/brunder-turnout-publish, branch turnout-publish, reset to origin/main each run),
# so this checkout and any uncommitted work are never touched.
#   - after 2026-11-04T03:00Z: does nothing
#   - 22:25Z to 22:40Z on 2026-11-03 (night stack start): does nothing
#   - JSON byte-identical to what is on origin/main: no commit, no push
#   - anything other than the three turnout_*.json files changed or staged: aborts, no push
#   analysis/us_turnout_publish.sh [--no-push] [--engines chromium,webkit] [--src PATH] [--volume-src PATH]
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WT="${TURNOUT_PUBLISH_WORKTREE:-$HOME/.cache/brunder-turnout-publish}"
BASE="${TURNOUT_PUBLISH_BASE:-origin/main}"
STOP="${TURNOUT_STOP_UTC:-202611040300}"          # YYYYMMDDHHMM, UTC
NOPUSH_FROM="${TURNOUT_BLACKOUT_FROM:-202611032225}"; NOPUSH_TO="${TURNOUT_BLACKOUT_TO:-202611032240}"
DATA=site/data/ev2026
ALLOW="turnout_day.json turnout_volume.json turnout_model.json"      # the commit allowlist (names under $DATA)
PUSH=1; ENGINES="chromium,webkit"; SRC=""; VSRC=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-push) PUSH=0 ;;
    --engines) ENGINES="$2"; shift ;;
    --src) SRC="$2"; shift ;;
    --volume-src) VSRC="$2"; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
NOW="$(date -u +%Y%m%d%H%M)"
[ "$NOW" -lt "$STOP" ] || { say "past the stop time: nothing to do"; exit 0; }
if [ "$NOW" -ge "$NOPUSH_FROM" ] && [ "$NOW" -lt "$NOPUSH_TO" ]; then say "night-stack start window: skipped"; exit 0; fi

LOCK="$HOME/.cache/brunder-turnout-publish.lock"
mkdir -p "$HOME/.cache"
mkdir "$LOCK" 2>/dev/null || { echo "another publish is running ($LOCK)" >&2; exit 3; }
trap 'rmdir "$LOCK"' EXIT

if [ ! -e "$WT/.git" ]; then git -C "$REPO" worktree add -f -B turnout-publish "$WT" "$BASE" >/dev/null 2>&1 || { git -C "$REPO" fetch -q origin main && git -C "$REPO" worktree add -f -B turnout-publish "$WT" "$BASE" >/dev/null; }; fi
git -C "$WT" fetch -q origin main
git -C "$WT" checkout -q -B turnout-publish "$BASE"
git -C "$WT" reset -q --hard "$BASE"
git -C "$WT" clean -qfd "$DATA"

ARGS="--out-dir $WT/$DATA"
[ -z "$SRC" ] || ARGS="$ARGS --src $SRC"
[ -z "$VSRC" ] || ARGS="$ARGS --volume-src $VSRC"
# shellcheck disable=SC2086
(cd "$REPO" && uv run python analysis/us_turnout_publish.py $ARGS)

# the dashboard file: built from the day file just written, history carried over from the one on origin/main
if [ -f "$WT/analysis/us_turnout_model.py" ]; then
  # shellcheck disable=SC2086
  (cd "$REPO" && uv run python "$WT/analysis/us_turnout_model.py" --root "$WT" --out-dir "$WT/$DATA" ${SRC:+--src "$SRC"}) || say "us_turnout_model.py failed: keeping the last turnout_model.json"
fi

CHANGED="$(git -C "$WT" status --porcelain)"
[ -n "$CHANGED" ] || { say "turnout files identical to $BASE: nothing to publish"; exit 0; }
ALLOW_RE="$(for f in $ALLOW; do printf -- '-e %s/%s ' "$DATA" "$f"; done)"
# shellcheck disable=SC2086
OTHER="$(git -C "$WT" status --porcelain | sed 's/^...//' | grep -v -x $ALLOW_RE || true)"
# shellcheck disable=SC2086
if [ -n "$OTHER" ] || [ -n "$(git -C "$WT" diff --cached --name-only | grep -v -x $ALLOW_RE || true)" ]; then
  echo "abort: files other than the turnout JSON changed or staged: $OTHER" >&2; exit 5
fi

say "site_check on earlyvote.html and turnout.html ($ENGINES)"
ONLY=earlyvote; [ ! -f "$WT/site/turnout.html" ] || ONLY=earlyvote,turnout
(cd "$WT" && uv run --with playwright --with pillow python analysis/site_check.py --engines "$ENGINES" --only "$ONLY")

# the clock may have moved into the blackout while the check ran
NOW="$(date -u +%Y%m%d%H%M)"
if [ "$NOW" -ge "$NOPUSH_FROM" ] && [ "$NOW" -lt "$NOPUSH_TO" ]; then say "night-stack start window: not committing"; exit 0; fi
for f in $ALLOW; do [ ! -f "$WT/$DATA/$f" ] || git -C "$WT" add -- "$DATA/$f"; done
git -C "$WT" commit -q -m "Election Day turnout refresh (research file generated $(sed -n 's/.*"generated_utc":"\([^"]*\)".*/\1/p' "$WT/$DATA/turnout_day.json" | head -n 1))" -- "$DATA"
if [ "$PUSH" = 0 ]; then say "committed in $WT (not pushed: --no-push)"; exit 0; fi
n=0
until git -C "$WT" fetch -q origin main && git -C "$WT" rebase -q origin/main && git -C "$WT" push -q origin HEAD:main; do
  git -C "$WT" rebase --abort 2>/dev/null || true
  n=$((n + 1)); [ "$n" -lt 3 ] || { echo "push failed after $n tries" >&2; exit 6; }
  say "push raced with another commit: fetching and rebasing again"; sleep 3
done
say "pushed $(git -C "$WT" rev-parse --short HEAD)"
