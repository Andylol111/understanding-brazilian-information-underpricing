#!/bin/sh
# Refresh the public early-vote data (site/data/ev2026) from the election server's database and push it.
#   pull ev.sqlite (+ the newest PA extract, + the NC per-voter file) -> build -> site_check on earlyvote.html ->
#   commit ONLY site/data/ev2026 -> rebase on origin/main -> push.
# Everything happens in a dedicated worktree (default ~/.cache/brunder-ev-publish, branch ev-publish) that is reset to
# origin/main each run, so the working checkout and its uncommitted work are never touched. Code (builder, page, checker)
# is whatever origin/main has: merge the page before scheduling this.
#
#   analysis/us_ev_publish.sh [--no-push] [--db /path/ev.sqlite] [--engines chromium,firefox] [--no-pull]
# Needs: ssh access to oracle-slotbank with passwordless sudo rsync (read only: the server database is never written),
# $BRUNDER_DATA (the SSD: NC per-voter file, 2016-2024 panels), uv, playwright browsers, git push rights.
# Scheduling is the integrator's (e.g. cron / launchd every 3 h at :40, after the collector's :20 pass).
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export BRUNDER_DATA="${BRUNDER_DATA:-$HOME/brunder-data-local}"
SERVER="${EV_SERVER:-oracle-slotbank}"
REMOTE="${EV_REMOTE_DIR:-/srv/brunder/evdata/us_ev}"
WT="${EV_PUBLISH_WORKTREE:-$HOME/.cache/brunder-ev-publish}"
WORK="$BRUNDER_DATA/us_ev/publish"
BASE="${EV_PUBLISH_BASE:-origin/main}"     # test override: a local commit that already has the builder and the page
PUSH=1; PULL=1; DB=""; ENGINES="chromium,firefox"
while [ $# -gt 0 ]; do
  case "$1" in
    --no-push) PUSH=0 ;;
    --no-pull) PULL=0 ;;
    --db) DB="$2"; shift ;;
    --engines) ENGINES="$2"; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$WORK" "$BRUNDER_DATA/us_ev/daily/src"
LOCK="$WORK/publish.lock"
mkdir "$LOCK" 2>/dev/null || { echo "another publish is running ($LOCK)" >&2; exit 3; }
trap 'rmdir "$LOCK"' EXIT

say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

# 1. inputs ------------------------------------------------------------------------------------------------------
if [ -z "$DB" ]; then
  DB="$WORK/ev.sqlite"
  if [ "$PULL" = 1 ]; then
    say "pulling the database from $SERVER"
    rsync -a --rsync-path="sudo -n rsync" "$SERVER:$REMOTE/ev.sqlite" "$WORK/ev.sqlite.new"
    sqlite3 "$WORK/ev.sqlite.new" "PRAGMA quick_check;" | grep -qx ok || { echo "pulled database fails quick_check" >&2; exit 4; }
    mv "$WORK/ev.sqlite.new" "$DB"
    LATEST="$(ssh "$SERVER" "sudo -n sh -c 'ls -1 $REMOTE/raw/pa/*_pa_mail_socrata.json 2>/dev/null | tail -n 1'")"
    if [ -n "$LATEST" ]; then
      rsync -a --rsync-path="sudo -n rsync" "$SERVER:$LATEST" "$BRUNDER_DATA/us_ev/daily/src/pa_mail_socrata_latest.json"
    fi
  fi
fi
say "NC per-voter file (one conditional request)"
(cd "$REPO" && uv run python analysis/us_ev_daily.py refresh-nc) || say "NC refresh failed: the last file on the SSD is used"

# 2. dedicated worktree at origin/main ---------------------------------------------------------------------------
if [ ! -e "$WT/.git" ]; then
  git -C "$REPO" worktree add -f -B ev-publish "$WT" "$BASE" >/dev/null
fi
git -C "$WT" fetch -q origin main
git -C "$WT" checkout -q -B ev-publish "$BASE"
git -C "$WT" clean -qfd site/data/ev2026 2>/dev/null || true

# 3. build + check -----------------------------------------------------------------------------------------------
OLD_BUILT="$(sed -n 's/.*"built":"\([^"]*\)".*/\1/p' "$WT/site/data/ev2026/index.json" 2>/dev/null | head -n 1)"
say "building"
(cd "$WT" && uv run python analysis/us_ev_site.py --db "$DB" --strict)
NEW_BUILT="$(sed -n 's/.*"built":"\([^"]*\)".*/\1/p' "$WT/site/data/ev2026/index.json" | head -n 1)"
if [ -n "$OLD_BUILT" ] && [ "$NEW_BUILT" \< "$OLD_BUILT" ]; then
  echo "refusing to publish: the database ($NEW_BUILT) is older than the published data ($OLD_BUILT)" >&2
  git -C "$WT" checkout -q -- site/data/ev2026; exit 5
fi
say "site_check on earlyvote.html ($ENGINES)"
(cd "$WT" && uv run --with playwright --with pillow python analysis/site_check.py --engines "$ENGINES" --only earlyvote)

# 4. commit only the data directory, rebase, push ------------------------------------------------------------------
if [ -z "$(git -C "$WT" status --porcelain -- site/data/ev2026)" ]; then
  say "no change in site/data/ev2026: nothing to publish"; exit 0
fi
git -C "$WT" add -A -- site/data/ev2026
git -C "$WT" commit -q -m "US early-vote data refresh (database through $NEW_BUILT)" -- site/data/ev2026
if [ "$PUSH" = 0 ]; then say "committed in $WT (not pushed: --no-push)"; exit 0; fi
n=0
until git -C "$WT" pull -q --rebase origin main && git -C "$WT" push -q origin HEAD:main; do
  n=$((n + 1)); [ "$n" -lt 4 ] || { echo "push failed after $n tries" >&2; exit 6; }
  say "push raced with another commit: rebasing again"; sleep 3
done
say "pushed $(git -C "$WT" rev-parse --short HEAD)"
