#!/usr/bin/env bash
#
# Pushes the Payments money-hub rebuild (Mission F).
#
# The commit is already made locally — cfbcac84, "feat(payments): rebuild the
# Payments screen as a money hub on real financial records". Only the push is
# left, and that has to happen on your machine: the sandbox proxy blocks
# github.com:22 and returns 403 on the HTTPS path too.
#
# The lock cleanup below is the same housekeeping the earlier push scripts do.
# The shared mount forbids unlinking, so git can create its lock and temp-object
# files but never clean them up, and a stale lock blocks the next commit.
#
# Run:  bash ~/Desktop/CoinPilotX/push-payments-rebuild.sh
#
set -euo pipefail

REPO="$HOME/Desktop/CoinPilotX"
BRANCH="codex/emergency-live-audio-recovery"

cd "$REPO"

echo "→ clearing stale git locks and temp objects"
rm -f .git/index.lock .git/HEAD.lock
find .git/refs -name '*.lock' -delete 2>/dev/null || true
find .git/objects -name 'tmp_obj_*' -delete 2>/dev/null || true

echo "→ current branch: $(git rev-parse --abbrev-ref HEAD)"
if [ "$(git rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]; then
  git checkout "$BRANCH"
fi

echo "→ commits waiting to go out:"
git log --oneline "origin/$BRANCH..HEAD" || true

echo "→ pushing"
git push origin "$BRANCH"

echo
echo "✓ pushed $BRANCH"
echo
echo "Housekeeping: mobile-native/tsconfig.payments.json is a scratch typecheck"
echo "config I could not delete from the sandbox (the mount forbids unlinking)."
echo "It is untracked and not in the commit. Remove it when convenient:"
echo "    rm ~/Desktop/CoinPilotX/mobile-native/tsconfig.payments.json"
