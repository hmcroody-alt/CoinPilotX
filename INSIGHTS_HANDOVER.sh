#!/usr/bin/env bash
#
# Insights rebuild — commit and push handover.
#
#   ./INSIGHTS_HANDOVER.sh probe    # verify only. Touches nothing. Run this first.
#   ./INSIGHTS_HANDOVER.sh stage    # stage exactly the Insights files. No commit.
#   ./INSIGHTS_HANDOVER.sh commit   # stage + commit. Does NOT push.
#   ./INSIGHTS_HANDOVER.sh push     # stage + commit + push to the current branch.
#
# Why this exists rather than a plain `git add -A`: the working tree also carries
# uncommitted work from other missions (advertising funding, marketplace payouts,
# an Events/Activity feed). `bot.py` in particular has three unrelated diff hunks
# and only one of them is Insights, so this script stages that single hunk by
# name instead of sweeping the file in.
#
set -euo pipefail

cd "$(dirname "$0")"

MODE="${1:-probe}"
GREEN=$'\033[32m'; RED=$'\033[31m'; DIM=$'\033[2m'; OFF=$'\033[0m'
ok()   { echo "${GREEN}  ok${OFF}   $*"; }
bad()  { echo "${RED}  FAIL${OFF} $*"; }
step() { echo; echo "${DIM}== $*${OFF}"; }

# ---------------------------------------------------------------- the payload

FILES=(
  METRICS.md
  INSIGHTS_REBUILD_REPORT.md
  INSIGHTS_HANDOVER.sh
  services/business_os/insights/seller_analytics.py
  tests/business_os/test_seller_analytics.py
  mobile-native/src/api/insightsDashboard.ts
  mobile-native/src/api/insightsRules.ts
  mobile-native/src/api/__tests__/insightsDashboard.test.ts
  mobile-native/src/api/__tests__/insightsRules.test.ts
  mobile-native/src/theme/insightsLight.ts
  mobile-native/src/theme/insightsMotion.ts
  mobile-native/src/components/insights
  mobile-native/src/screens/BusinessOsInsightsScreen.tsx
  mobile-native/src/screens/__tests__/StoreDashboardScreen.test.tsx
)

# `bot.py` is staged one hunk at a time — see extract_bot_hunk below.
BOT_MARKER='/api/pulse/insights/seller/summary'

MESSAGE='Rebuild seller Insights on a real analytics endpoint

Adds services/business_os/insights/seller_analytics.py: one aggregate read over
the full period window, seller-timezone boundaries, prior-period comparison,
day/week bucketing, source split and ranked listings. Replaces the previous
screen, which had no aggregate source to read from.

The four metrics this platform cannot measure (store views, ads attribution,
on-time dispatch, reply rate) are returned as a named gap list and render as
nothing rather than as zeros. METRICS.md documents every metric with its
definition, source, owner screen and reconciliation status, and files the
Store dashboard'"'"'s unfiltered, LIMIT-100 "Sales today" as a product bug.'

# ------------------------------------------------------------------- helpers

# Writes just the Insights hunk of bot.py to $1, or exits 1 if it cannot find
# exactly one such hunk.
extract_bot_hunk() {
  git diff -U3 -- bot.py | python3 -c '
import sys
lines = sys.stdin.read().splitlines(keepends=True)
head, hunks, cur = [], [], None
for line in lines:
    if line.startswith("@@"):
        if cur is not None:
            hunks.append(cur)
        cur = [line]
    elif cur is None:
        head.append(line)
    else:
        cur.append(line)
if cur is not None:
    hunks.append(cur)
marker = sys.argv[1]
keep = [h for h in hunks if any(marker in l for l in h)]
if len(keep) != 1:
    sys.stderr.write("expected exactly 1 Insights hunk in bot.py, found %d\n" % len(keep))
    raise SystemExit(1)
sys.stdout.write("".join(head) + "".join(keep[0]))
' "$BOT_MARKER" > "$1"
}

# --------------------------------------------------------------------- probe

step "Repository"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "  branch   $BRANCH"
echo "  head     $(git log -1 --format='%h %s' | cut -c1-72)"
echo "  remote   $(git remote get-url origin 2>/dev/null || echo '(none)')"

step "Stale locks"
# A run interrupted mid-write leaves these behind, and every later git write
# then refuses with a message that blames "another git process". There is no
# other process; the file is just litter.
LOCKS="$(ls .git/*.lock .git/refs/heads/*.lock 2>/dev/null || true)"
if [ -n "$LOCKS" ]; then
  echo "$LOCKS" | sed 's/^/  /'
  bad "stale lock file(s) present — git cannot write until they are removed"
  echo "     rm -f $(echo "$LOCKS" | tr '\n' ' ')"
  exit 1
fi
ok "none"

step "Files present"
MISSING=0
for f in "${FILES[@]}"; do
  if [ -e "$f" ]; then ok "$f"; else bad "$f — missing"; MISSING=1; fi
done
[ "$MISSING" -eq 0 ] || { echo; echo "${RED}Stopping: expected files are missing.${OFF}"; exit 1; }

step "bot.py route hunk"
PATCH="$(mktemp)"
trap 'rm -f "$PATCH"' EXIT

# Ask the index first. Once the hunk is staged it leaves the unstaged diff, so
# looking there would report it missing on a second run — which is exactly what
# a re-run after an interrupted one looks like.
if git diff --cached -- bot.py | grep -q -- "$BOT_MARKER"; then
  BOT_STAGED=1
  ok "already staged from an earlier run"
else
  BOT_STAGED=0
  TOTAL_HUNKS="$(git diff -U0 -- bot.py | grep -c '^@@' || true)"
  if extract_bot_hunk "$PATCH"; then
    ADDED="$(grep -c '^+' "$PATCH" || true)"
    ok "isolated $ADDED added lines carrying $BOT_MARKER"
    ok "leaving $((TOTAL_HUNKS - 1)) unrelated bot.py hunk(s) unstaged"
  else
    bad "could not isolate the Insights hunk in bot.py"; exit 1
  fi
fi

step "Tests"
python3 tests/business_os/test_seller_analytics.py > /dev/null 2>&1 \
  && ok "tests/business_os/test_seller_analytics.py" \
  || { bad "tests/business_os/test_seller_analytics.py"; exit 1; }
python3 tests/business_os/test_insights_core.py > /dev/null 2>&1 \
  && ok "tests/business_os/test_insights_core.py" \
  || { bad "tests/business_os/test_insights_core.py"; exit 1; }

if [ -d mobile-native/node_modules ]; then
  ( cd mobile-native && npx jest --silent src/api/__tests__/insightsDashboard.test.ts \
      src/api/__tests__/insightsRules.test.ts \
      src/screens/__tests__/StoreDashboardScreen.test.tsx ) > /dev/null 2>&1 \
    && ok "insights jest suites + StoreDashboard repair" \
    || { bad "jest"; exit 1; }
  ( cd mobile-native && node_modules/.bin/tsc --noEmit -p tsconfig.json ) > /dev/null 2>&1 \
    && ok "tsc --noEmit clean" \
    || { bad "tsc"; exit 1; }
else
  echo "${DIM}  skipped: mobile-native/node_modules not installed${OFF}"
fi

if [ "$MODE" = "probe" ]; then
  echo
  echo "${GREEN}Probe clean.${OFF} Nothing was staged."
  echo "Next:  ./INSIGHTS_HANDOVER.sh stage    (then review with: git diff --cached)"
  echo "       ./INSIGHTS_HANDOVER.sh commit"
  echo "       ./INSIGHTS_HANDOVER.sh push"
  exit 0
fi

# --------------------------------------------------------------------- stage

step "Staging"
git add -- "${FILES[@]}"

# Re-running after a partial run must not fail. `git add` is idempotent, but
# applying a patch to an index that already carries it is not, so ask the index
# which of the three states it is in before touching it.
if [ "$BOT_STAGED" -eq 1 ]; then
  ok "staged ${#FILES[@]} paths; the bot.py route hunk was already staged"
elif git apply --cached --check "$PATCH" 2>/dev/null; then
  git apply --cached "$PATCH"
  ok "staged ${#FILES[@]} paths + the bot.py route hunk"
elif git apply --cached --reverse --check "$PATCH" 2>/dev/null; then
  ok "staged ${#FILES[@]} paths; the bot.py route hunk was already staged"
else
  bad "the bot.py route hunk neither applies nor is already staged."
  echo "     The file has changed underneath this script. Stage it by hand:"
  echo "       git add -p bot.py     # accept only the hunk adding $BOT_MARKER"
  exit 1
fi
echo
git diff --cached --stat
echo
echo "Unstaged work left alone (other missions):"
git status --porcelain | grep -v '^[MARCD]' | sed 's/^/  /' | head -40

if [ "$MODE" = "stage" ]; then
  echo
  echo "${GREEN}Staged.${OFF} Review with: git diff --cached"
  echo "Then:  ./INSIGHTS_HANDOVER.sh commit"
  exit 0
fi

# -------------------------------------------------------------------- commit

step "Commit"
git commit -m "$MESSAGE"
git log -1 --stat | head -30

if [ "$MODE" = "commit" ]; then
  echo
  echo "${GREEN}Committed.${OFF} Not pushed."
  echo "To push:  git push origin $BRANCH"
  exit 0
fi

# ---------------------------------------------------------------------- push

step "Push"
git push origin "$BRANCH"
echo
echo "${GREEN}Pushed to origin/$BRANCH.${OFF}"
