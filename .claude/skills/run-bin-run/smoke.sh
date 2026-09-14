#!/usr/bin/env bash
# Smoke-drive Bin Run end to end, the way a user would - two roles, the
# claim race, a process restart - against an isolated throwaway database.
# Safe to run on a machine with the dev server up: it uses its own port
# and its own SQLite file, and touches nothing else.
#
# Usage:  .claude/skills/run-bin-run/smoke.sh          # full drive, PASS/FAIL
#         .claude/skills/run-bin-run/smoke.sh screenshot  # + join-page PNG
#
# Exit 0 = every step behaved. Any other exit = the line it printed last.

set -u
cd "$(dirname "$0")/../../.."          # repo root, wherever we were called from

PORT="${PORT:-8321}"
BASE="http://127.0.0.1:$PORT"
WORK="$(mktemp -d)"
export DATABASE_URL="sqlite:///$WORK/smoke.db"
export SECRET_KEY="smoke-test-secret"
export GEOCODE_ENABLED="0"             # no network; addresses stay text
SERVER_PID=""

say()  { printf '%s\n' "$*"; }
die()  { say "FAIL: $*"; exit 1; }

cleanup() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

# --- 0. venv (bootstraps a clean clone; no-op when .venv exists) -----------
if [ ! -x .venv/bin/uvicorn ]; then
  say "no .venv - creating one (first run on a clean clone)"
  python3 -m venv .venv || die "could not create venv"
  ./.venv/bin/pip install -q -r requirements.txt || die "pip install failed"
fi

start_server() {
  ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
      > "$WORK/server.log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 40); do
    curl -sf "$BASE/healthz" > /dev/null 2>&1 && return 0
    sleep 0.25
  done
  say "--- server log ---"; tail -5 "$WORK/server.log"
  die "server did not come up on :$PORT"
}

start_server
say "ok: server up on :$PORT (isolated db in $WORK)"

# --- 1. resident posts a pickup --------------------------------------------
R="$WORK/resident.jar"
curl -sf -c "$R" -b "$R" -d "name=SmokeThandi&role=resident" \
     -o /dev/null "$BASE/join" || die "resident join"
LOC=$(curl -s -c "$R" -b "$R" -D - -o /dev/null \
     -d "description=Four smoke bags&bag_count=4&size=medium&address=14 Test Ave" \
     "$BASE/pickups" | tr -d '\r' | awk 'tolower($1)=="location:"{print $2}')
case "$LOC" in /pickups/*) PICKUP="${LOC#/pickups/}" ;; *) die "pickup post redirected to '$LOC'" ;; esac
say "ok: pickup #$PICKUP posted (geocoder off -> stored as text, still a real pickup)"

# --- 2. two collectors race the claim --------------------------------------
A="$WORK/a.jar"; B="$WORK/b.jar"
curl -sf -c "$A" -b "$A" -d "name=SmokeSipho&role=collector" -o /dev/null "$BASE/join" || die "collector A join"
curl -sf -c "$B" -b "$B" -d "name=SmokeNadia&role=collector" -o /dev/null "$BASE/join" || die "collector B join"

claim() { # $1 jar  $2 outfile - fired concurrently below
  curl -s -c "$1" -b "$1" -D - -o /dev/null -d "" "$BASE/pickups/$PICKUP/claim" \
    | tr -d '\r' | awk 'tolower($1)=="location:"{print $2}' > "$2"
}
# NB: wait on the two claim pids EXPLICITLY - a bare `wait` also waits
# for the backgrounded uvicorn server, which never exits, and the script
# hangs forever. Cost 2 minutes of staring the first time.
claim "$A" "$WORK/a.loc" & C1=$!
claim "$B" "$WORK/b.loc" & C2=$!
wait "$C1" "$C2"
ALOC=$(cat "$WORK/a.loc"); BLOC=$(cat "$WORK/b.loc")
WINNER=""; [ "$ALOC" = "/pickups/$PICKUP" ] && WINNER="$A"
[ "$BLOC" = "/pickups/$PICKUP" ] && { [ -n "$WINNER" ] && die "BOTH claims won: '$ALOC' '$BLOC'"; WINNER="$B"; }
[ -n "$WINNER" ] || die "no claim won: '$ALOC' '$BLOC'"
{ [ "$ALOC" = "/jobs" ] || [ "$BLOC" = "/jobs" ]; } || die "loser not sent back to the board"
say "ok: claim race - exactly one winner, loser redirected to /jobs"

curl -s -b "$R" "$BASE/pickups/$PICKUP" | grep -q 'ticks-claimed' \
  || die "resident page does not show the claimed ticks"
say "ok: resident sees the claim (status ticks rendered)"

# --- 3. restart the process; the claim must survive -------------------------
kill "$SERVER_PID"; wait "$SERVER_PID" 2>/dev/null; SERVER_PID=""
start_server
curl -s -b "$R" "$BASE/pickups/$PICKUP" | grep -q 'ticks-claimed' \
  || die "claim did not survive a server restart"
say "ok: state survived a full process restart"

# --- 4. winner closes the job ----------------------------------------------
curl -sf -b "$WINNER" -c "$WINNER" -d "" -o /dev/null "$BASE/pickups/$PICKUP/done" \
  || die "mark done"
curl -s -b "$R" "$BASE/pickups/$PICKUP" | grep -q 'ticks-done' \
  || die "done state not visible to the resident"
say "ok: collected - full lifecycle open -> claimed -> done"

# --- optional: screenshot of the join page ----------------------------------
if [ "${1:-}" = "screenshot" ]; then
  SHOT="$PWD/smoke-join.png"
  google-chrome --headless=new --disable-gpu --window-size=900,900 \
      --screenshot="$SHOT" "$BASE/join" > /dev/null 2>&1 \
    && say "ok: screenshot at $SHOT" || say "warn: chrome screenshot failed (non-fatal)"
fi

say "PASS: Bin Run smoke drive complete"
