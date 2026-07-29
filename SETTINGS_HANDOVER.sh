#!/usr/bin/env bash
#
# SETTINGS_HANDOVER.sh — PulseSoc native Settings repair, deploy handover.
#
# Nothing in this file has been run for you. It commits nothing, pushes nothing
# and deploys nothing until you run it. Read it first; every step prints what it
# is about to do.
#
# Usage:
#   ./SETTINGS_HANDOVER.sh probe      # read-only: is production answering?
#   ./SETTINGS_HANDOVER.sh verify     # read-only: types + tests + parse check
#   ./SETTINGS_HANDOVER.sh commit     # stages and commits the eight changed files
#   ./SETTINGS_HANDOVER.sh push       # pushes main (Railway builds main)
#   ./SETTINGS_HANDOVER.sh confirm    # re-probes after the deploy finishes
#
# Run them in that order. `probe` before and `confirm` after is the whole point:
# the two outputs are the evidence that the deploy changed something.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${PULSE_API_BASE_URL:-https://pulsesoc.com}"
NATIVE_DIR="$REPO_ROOT/mobile-native"

CHANGED_FILES=(
  "bot.py"
  "mobile-native/src/settings/schema.ts"
  "mobile-native/src/settings/store.tsx"
  "mobile-native/src/settings/api.ts"
  "mobile-native/src/screens/settings/SecuritySettingsScreen.tsx"
  "mobile-native/src/settings/__tests__/schema.test.ts"
  "mobile-native/src/settings/__tests__/store.test.tsx"
  "mobile-native/src/settings/__tests__/api.test.ts"
)

# Every route the Settings client can call. An unauthenticated request to each
# of these should answer 401, 403 or 405 — all three mean "the route exists and
# refused me", which is the correct answer to a request with no session.
#
# 404 is the failure. It means the rule is not in the URL map on the server this
# build calls, and it is the status that produced the banner users photographed.
declare -a ENDPOINTS=(
  "GET    /api/pulse/mobile/settings"
  "PATCH  /api/pulse/mobile/settings"
  "GET    /api/pulse/mobile/settings/blocked"
  "POST   /api/pulse/mobile/settings/blocked"
  "DELETE /api/pulse/mobile/settings/blocked"
  "GET    /api/pulse/mobile/settings/muted"
  "POST   /api/pulse/mobile/settings/muted"
  "DELETE /api/pulse/mobile/settings/muted"
  "GET    /api/pulse/mobile/settings/sessions"
  "POST   /api/pulse/mobile/settings/sessions/revoke"
  "POST   /api/pulse/mobile/settings/data-export"
  "POST   /api/pulse/mobile/settings/delete-account"
)

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }

# --------------------------------------------------------------------------- #

probe() {
  say "Probing $BASE_URL — unauthenticated, no side effects"
  note "401/403/405 = the route exists and refused an anonymous caller. Good."
  note "404          = the route is missing on this server. This is the blocker."
  note ""

  local failures=0
  for entry in "${ENDPOINTS[@]}"; do
    local method path code
    method="$(awk '{print $1}' <<<"$entry")"
    path="$(awk '{print $2}' <<<"$entry")"
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 20 -X "$method" \
      -H 'Content-Type: application/json' \
      "$BASE_URL$path" || echo "000")"

    case "$code" in
      401|403|405) printf '  \033[32m%-4s\033[0m %-7s %s\n' "$code" "$method" "$path" ;;
      404)         printf '  \033[31m%-4s\033[0m %-7s %s  <-- MISSING\n' "$code" "$method" "$path"; failures=$((failures + 1)) ;;
      000)         printf '  \033[33m%-4s\033[0m %-7s %s  <-- unreachable\n' "----" "$method" "$path"; failures=$((failures + 1)) ;;
      *)           printf '  \033[33m%-4s\033[0m %-7s %s\n' "$code" "$method" "$path" ;;
    esac
  done

  say "Route health (added by this change — 404 here means the deploy predates it)"
  curl -s -m 20 "$BASE_URL/health/routes" | head -c 2000 || true
  echo

  if [ "$failures" -gt 0 ]; then
    say "$failures endpoint(s) not answering. Settings cannot work against this server."
    return 1
  fi
  say "Every Settings endpoint is present on $BASE_URL."
}

# --------------------------------------------------------------------------- #

verify() {
  say "Checking bot.py parses"
  ( cd "$REPO_ROOT" && python3 -c "import ast,sys; ast.parse(open('bot.py').read()); print('  bot.py parses OK')" )

  say "Type-checking the native app"
  ( cd "$NATIVE_DIR" && npx tsc --noEmit && echo "  no type errors" )

  say "Running the full native test suite"
  ( cd "$NATIVE_DIR" && npx jest --silent )
}

# --------------------------------------------------------------------------- #

commit() {
  cd "$REPO_ROOT"

  say "Files that will be committed"
  for file in "${CHANGED_FILES[@]}"; do
    if git diff --quiet -- "$file" && git diff --cached --quiet -- "$file"; then
      note "unchanged, skipping: $file"
    else
      note "$file"
    fi
  done

  say "Full diff stat"
  git diff --stat -- "${CHANGED_FILES[@]}"

  printf '\nStage and commit these files? [y/N] '
  read -r answer
  [ "$answer" = "y" ] || { say "Nothing was committed."; return 0; }

  git add -- "${CHANGED_FILES[@]}"
  git commit -F - <<'MSG'
fix(settings): keep device-owned preferences off the account

Four preferences describe the handset rather than the account: whether
biometric unlock is enrolled on this phone, how much space it lends the
media cache, and whether developer tooling is on for this build. All four
were being PATCHed to the server, which made a claim the account cannot
support and produced a loop — two signed-in devices each overwrote the
shared value with its own answer on every launch.

They are now classified in one place, stripped from every outgoing patch,
and restored from local state on every reconcile and rollback. A change
that touches only these keys makes no request at all.

Two related repairs:

Blueprint registration is observable. The settings, presence and
communications route packs were each registered inside a bare `except`
that logged and continued, so a failure took every endpoint in the pack
out of the URL map while /health kept answering 200. Registration now
records its outcome, logs CRITICAL on failure, and is readable without a
shell through a new unauthenticated GET /health/routes, which reports
which required rules are present and which packs failed to load.

Settings errors say something a user can act on. A failed write showed the
backend's generic 404 body — "The requested PulseSoc service was not
found." — verbatim, on every screen, to somebody who had only tapped a
switch. Each status now has its own text, the 404 case states plainly that
the change was not saved and that this is a server-side defect, and it
logs itself as a deployment problem so it stops being indistinguishable
from an ordinary rejected write.

No iOS build number was changed and no TestFlight build was created.
MSG

  say "Committed."
  git --no-pager log --oneline -1
}

# --------------------------------------------------------------------------- #

push() {
  cd "$REPO_ROOT"
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD)"

  say "About to push"
  note "branch: $branch"
  note "HEAD:   $(git rev-parse HEAD)"
  note ""
  note "Railway builds main. Pushing main starts a deploy."

  printf '\nPush %s to origin? [y/N] ' "$branch"
  read -r answer
  [ "$answer" = "y" ] || { say "Nothing was pushed."; return 0; }

  git push origin "$branch"
  say "Pushed. Watch the deploy, then run: ./SETTINGS_HANDOVER.sh confirm"
  note "Record the Railway deployment id and the SHA it built — the report has"
  note "a blank line for each, and they are what makes the deploy auditable."
}

# --------------------------------------------------------------------------- #

confirm() {
  say "Deployed commit, as reported by the server"
  curl -s -m 20 "$BASE_URL/health/routes" || true
  echo
  probe
  say "If every line above is green, re-run the device QA matrix in"
  note "reports/settings_release_blocker_2026-07-26.md, section 3."
}

# --------------------------------------------------------------------------- #

case "${1:-}" in
  probe)   probe ;;
  verify)  verify ;;
  commit)  commit ;;
  push)    push ;;
  confirm) confirm ;;
  *)
    sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
