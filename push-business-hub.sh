#!/usr/bin/env bash
# Push the Business Hub revert. The sandbox has no route to github.com on
# either 22 or 443, so this runs host-side. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

# The sandbox mount forbids unlink, so git can create .git/*.lock but never
# clean them up. Locks were parked in .git/_sandbox_trash/; clear the lot.
rm -f .git/index.lock .git/HEAD.lock .git/packed-refs.lock
find .git/refs -name '*.lock' -delete 2>/dev/null || true
rm -rf .git/_sandbox_trash
find .git/objects -name 'tmp_obj_*' -delete 2>/dev/null || true

BRANCH=codex/emergency-live-audio-recovery
COMMIT=f019bc40

[ "$(git rev-parse --abbrev-ref HEAD)" = "$BRANCH" ] || { echo "Not on $BRANCH"; exit 1; }
git merge-base --is-ancestor "$COMMIT" HEAD || { echo "$COMMIT not in HEAD"; exit 1; }

echo "About to push $BRANCH. Commit $COMMIT contains:"
git show --stat --oneline "$COMMIT" | sed 's/^/  /'
read -rp "Push? [y/N] " a
[ "$a" = y ] || { echo "Aborted."; exit 0; }
git push origin "$BRANCH"
