#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Wake the Render API and confirm it answers /health.
#
# Called by .github/workflows/keepalive.yml, and runnable by hand.
#
# Why a deadline loop and not `curl --retry`: GitHub delays scheduled runs by
# hours, so the ping nearly always lands on a sleeping instance, and Render's
# wake is sometimes well over 90 seconds. The old step gave curl three 90 s
# attempts and then failed with exit 28 — a red run for an instance that was
# only slow to wake. Here each attempt is bounded, but the run as a whole keeps
# trying until DEADLINE_SECONDS, and treats the responses a waking instance
# gives (timeouts, refused connections, 502/503/504) as "not yet".
#
# A definite answer ends the loop early either way:
#   200            → healthy, exit 0
#   other 4xx      → the URL is wrong; retrying cannot fix it, exit 2
#   deadline hit   → the service did not come up, exit 1
#
# Environment:
#   URL                  endpoint to ping (required)
#   DEADLINE_SECONDS     total budget                         (default 480)
#   ATTEMPT_SECONDS      per-request timeout                  (default 60)
#   RETRY_DELAY_SECONDS  pause between attempts               (default 10)
# ─────────────────────────────────────────────────────────────────────────────
set -u

: "${URL:?URL is required}"
DEADLINE_SECONDS="${DEADLINE_SECONDS:-480}"
ATTEMPT_SECONDS="${ATTEMPT_SECONDS:-60}"
RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-10}"

body="$(mktemp)"
trap 'rm -f "$body"' EXIT

start=$(date +%s)
attempt=0

while :; do
  attempt=$((attempt + 1))
  elapsed=$(( $(date +%s) - start ))
  remaining=$(( DEADLINE_SECONDS - elapsed ))
  if [ "$remaining" -le 0 ]; then
    echo "::error::$URL did not answer 200 within ${DEADLINE_SECONDS}s ($((attempt - 1)) attempts)"
    exit 1
  fi

  # Never let one attempt run past the overall deadline.
  max_time=$ATTEMPT_SECONDS
  [ "$remaining" -lt "$max_time" ] && max_time=$remaining

  code=$(curl -sS -o "$body" -w '%{http_code}' --max-time "$max_time" "$URL" 2>/dev/null)
  status=$?
  echo "attempt $attempt after ${elapsed}s: curl exit $status, HTTP ${code:-000}"

  if [ "$status" -eq 0 ] && [ "$code" = "200" ]; then
    cat "$body"; echo
    echo "healthy after $(( $(date +%s) - start ))s"
    exit 0
  fi

  if [ "$status" -eq 0 ] && [ "${code#4}" != "$code" ]; then
    cat "$body"; echo
    echo "::error::$URL returned HTTP $code; check the URL (KEEPALIVE_URL)"
    exit 2
  fi

  # Timeout, refused connection or a 5xx from Render's proxy: still waking.
  sleep "$RETRY_DELAY_SECONDS"
done
