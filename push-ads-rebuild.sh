#!/usr/bin/env bash
#
# Commits the Advertising rebuild and pushes it.
#
# Why this is a script instead of something already done: the sandbox mount
# this repo is shared through forbids unlinking files, so git can create its
# lock files but never clean them up. One commit went through (the Marketplace
# manager screen) and left a stale .git/HEAD.lock behind, which blocks every
# commit after it. Removing those locks and pushing both need to happen on your
# machine — the sandbox proxy also blocks github.com:22.
#
# Run:  bash ~/Desktop/CoinPilotX/push-ads-rebuild.sh
#
set -euo pipefail

REPO="$HOME/Desktop/CoinPilotX"
BRANCH="codex/store-dashboard-live"

cd "$REPO"

echo "→ clearing stale git locks"
rm -f .git/index.lock .git/HEAD.lock
find .git/refs -name '*.lock' -delete 2>/dev/null || true
find .git/objects -name 'tmp_obj_*' -delete 2>/dev/null || true

echo "→ current branch: $(git rev-parse --abbrev-ref HEAD)"
git checkout "$BRANCH"

# The sandbox committed once through a copied index (GIT_INDEX_FILE), so the
# real .git/index never saw that commit and still lists its files as deleted.
# Rebuilding the index from HEAD clears those phantom deletions; the working
# tree is not touched, so nothing you have on disk is at risk.
echo "→ rebuilding the index from HEAD (working tree untouched)"
git read-tree HEAD

echo "→ staging the Advertising rebuild"
git add -A mobile-native/src ADVERTISING_REBUILD_REPORT.md

echo "→ staged:"
git diff --cached --name-status HEAD

echo "→ committing"
git commit -F - <<'MSG'
feat(business): two-sided ads manager behind the Advertising route

Rebuilds the Advertising screen as a two-sided manager: Marketplace ads,
fully backed by /api/pulse/ads/*, and Post ads, an unbacked preview gated
on EXPO_PUBLIC_ADS_POST_MODE. A header toggle swaps modes without a push;
both panes stay mounted so each keeps its scroll, and the wallet chip is
rendered once on the header so the balance cannot disagree with itself
between modes.

No new payment path. The balance is the server's spendable_balance_cents
read through walletSummary — there is no client-side balance arithmetic,
a failed wallet call renders no chip rather than a fabricated zero, and
Add funds opens the existing BusinessOsPayments screen. adFundingIsLive()
is false because the backend pins live_charging false, so the UI says so
instead of offering a control that cannot charge.

deliverySwitchState is the single place deciding whether a pause switch
may be pressed, derived from availableAdCampaignActions so the switch is
enabled exactly when the server would accept. Drafts show no switch;
ended, in-review and verification-blocked campaigns show a disabled one
with a rendered, announced reason — no switch silently no-ops.

BusinessOsAdvertising stays one route so deep links keep working:
AdvertisingRoute sends the default case to the manager and mode:"classic"
to the previous screen, which still owns the ad-account and campaign
creation forms the manager routes into. Also registers MarketplaceManager
and repoints the Business OS Marketplace card at it.

Nine unsourced fields are declared in ADS_MOCK_DATA_GAPS, whose length a
test pins. tsc clean; 123 suites / 2092 tests pass.
MSG

echo "→ rebasing on origin/$BRANCH"
git pull --rebase origin "$BRANCH"

echo "→ pushing"
git push origin "$BRANCH"

echo
echo "Done. Latest commits:"
git log --oneline -3
