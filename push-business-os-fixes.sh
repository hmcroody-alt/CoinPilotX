#!/bin/bash
# Pushes the Business OS fix commits. Run from anywhere:
#   bash ~/Desktop/CoinPilotX/push-business-os-fixes.sh
#
# Why this script exists: the sandbox that produced these commits cannot reach
# github.com (SSH port 22 and HTTPS CONNECT are both refused by its proxy), and
# it cannot delete the stale .git/*.lock files that accumulated during the work
# ("Operation not permitted"). Both are trivial from your machine.
set -e
cd ~/Desktop/CoinPilotX

BRANCH="codex/emergency-live-audio-recovery"

echo "==> Clearing stale git locks"
# These are leftovers from interrupted git processes, not active locks. If a git
# command really is running in another window, stop here and close it first.
rm -f .git/index.lock .git/HEAD.lock .git/next-index-*.lock

echo "==> Refreshing the index"
# The commits were made against an alternate index because .git/index.lock was
# unremovable. This re-syncs the real index so `git status` tells the truth.
git reset >/dev/null

echo "==> Working tree status (expect: clean)"
git status --short

echo
echo "==> Commits to push"
git log --oneline origin/"$BRANCH".."$BRANCH" || true

echo
echo "==> NOTE: verify the remote actually has these afterwards."
# The remote-tracking ref was updated at one point without a successful push from
# the sandbox, so it may be stale. If it is, git would say "Everything
# up-to-date" and silently send nothing. The ls-remote below is the real check.

echo "==> Pushing $BRANCH"
git push origin "$BRANCH"

echo
echo "==> Verifying against the actual remote"
LOCAL=$(git rev-parse "$BRANCH")
REMOTE=$(git ls-remote origin "refs/heads/$BRANCH" | cut -f1)
echo "local:  $LOCAL"
echo "remote: $REMOTE"
if [ "$LOCAL" = "$REMOTE" ]; then
  echo "OK — remote matches local."
else
  echo "MISMATCH — the push did not land. Do not assume it worked."
  exit 1
fi
