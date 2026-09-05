# Premium Expiry Addendum — Lock UNDX Crypto Intelligence

**Status: PARTIAL — code complete, gates unrun.** The sandbox shell has been
unavailable for this entire mission (`ensure user: useradd failed: fork/exec
/usr/sbin/useradd: input/output error` on every call), so `tsc --noEmit`, Jest,
pytest and `scripts/realtime_audio_change_gate.py` have not executed. Nothing is
committed. Every correctness claim below rests on reading the code, not on a
green gate, and is labelled accordingly.

## What the lock is anchored to

Effective Premium is whatever the canonical resolver says: an active Premium
subscription, an active trial, or a higher tier that inherits Premium. Nothing
else counts. On the server that is `services/crypto_premium_gate.has_crypto_capability`
resolving through `services.business_os.entitlements.facade`; on the client it is
`useCanonicalTier` → `tierSatisfies(answer, "PREMIUM")`, reading
`GET /api/private-office/entitlement`. Both sides ask the same resolver, which is
the whole point of the addendum — a stale client boolean, an old subscription
row, "ever subscribed", or the Premium Center's own display state are not inputs
anywhere in this change.

## Changes

**`services/undx_agent_tools.py`** — `crypto_market_quote`, `crypto_market_history`,
`crypto_market_compare` and `crypto_market_overview` each call `_premium_denial(...)`
for `CAP_CRYPTO_INTELLIGENCE` as their first statement, *before* `_resolve_market_symbol`.
That ordering is the Stage 5 answer: symbol resolution is what reads the parked
Market Pulse context, so gating ahead of it means a `crypto_asset` context can
never function as a grant. A refusal returns `error_code = "premium_required"`;
an unimportable or throwing gate returns `premium_gate_unavailable` /
`premium_gate_error` — closed, not open.

**`services/undx_market_context.py`** — `grounding_block` returns a locked
envelope (`premium_required: true`, `capability: premium.crypto.intelligence`)
carrying instructions and no figures when `_premium_crypto_active` is false. It
sits *after* the `if not target and not market_wide: return None` early exit,
which is what keeps Stage 6 provable: a general turn never reaches the resolver
at all, so "Hello" is untouched.

**`mobile-native/src/screens/PremiumCenterScreen.tsx`** — `CryptoIntelligenceFeature`
gained `premium: boolean`. `alerts`, `watchlists`, `undx` and `marketPulse` are
`true`; `portfolio` is `false`. A row locks only when
`answer.state === "resolved" && !tierSatisfies(answer, "PREMIUM") && feature.premium`.
Locked rows render a padlock, drop the chevron, carry
`accessibilityState={{ disabled: true }}`, and press through to `onUpgrade` —
which scrolls to the plans/restore block already on this screen rather than
opening a second paywall. The section subhead swaps to `premium:gate.lockedBody`
so the heading cannot promise what the rows below it refuse. A foreground
`AppState` listener re-asks the resolver.

**Tests** — `tests/crypto_premium/test_premium_expiry_crypto_lock.py` (new) and
`mobile-native/src/screens/__tests__/PremiumCenterScreen.expiryLock.test.tsx`
(new); the existing `PremiumCenterScreen.cryptoIntelligence.test.tsx` was updated
for the new required prop.

## Two judgements worth stating plainly

**The portfolio row is not locked, on purpose.** `PortfolioScreen` gives free and
Premium members the same valuation, the same prices and the same rows; Premium
lifts a three-holding ceiling that the server enforces in `_limit_check`, and the
screen already renders the refusal in place. Locking the row would have hidden
holdings the member entered themselves — an over-lock dressed up as
consistency. Stage 6 forbids exactly this, so the lock is per row.

**An unavailable answer locks nothing.** Only a *resolved* denial closes a row. A
padlock earned by a failed request is a lie about someone's account, so during an
outage the rows stay navigable and the destination's own `PremiumFeatureGate`
shows "we couldn't confirm your membership" with a retry. The capability is still
safe: the server refuses independently.

**Direct routes and deep links needed no new gate.** Every Premium crypto
destination already wraps its entire body at the exported component —
`MarketPulseScreen:94`, `AssetDetailScreen:144`, `WatchlistsScreen:84`,
`IntelligenceCenterScreen:30`, `AlertManagementScreen:144` — so a deep link, a
direct `navigate()`, a saved route and restored stale navigation state all
resolve entitlement before anything mounts. Verified by reading; unexecuted.

## Test matrix

Fourteen states, walked on both sides. The backend file drives the four real
executors and the real `grounding_block`; the native file presses real rows and
asserts on `navigation.navigate`. Neither file asserts on an icon alone — Stage 10
is satisfied by `MarketSpy.reads == []` (no market read happened) and by
`expect(navigation.navigate).not.toHaveBeenCalled()` (no capability opened).

| Case | Expected |
| --- | --- |
| FREE | locked |
| Active trial | open |
| Expired trial | locked |
| Active Premium | open |
| Cancelled, inside paid period | open |
| Expired Premium | locked |
| Revoked Premium | locked |
| Expired Premium + active PRIVATE | open (inherited) |
| Expired Premium + active PRIVATE_OFFICE | open (inherited) |
| Stale client premium=true, backend expired | locked |
| Direct route while expired | locked |
| Deep link while expired | locked |
| Market Pulse context handoff while expired | locked, zero market reads |
| Reactivated | open, no restart |

## Verdict

| Item | Result |
| --- | --- |
| PREMIUM CENTER ROW LOCKED | YES (unverified — Jest unrun) |
| UNDX CRYPTO BACKEND GATE | WRITTEN, UNVERIFIED (pytest unrun) |
| DIRECT ROUTE | LOCKED by existing `PremiumFeatureGate` wrapping (read, unrun) |
| DEEP LINK | LOCKED by the same wrapping (read, unrun) |
| MARKET PULSE CONTEXT BYPASS | 0 by construction — gate precedes context read (unrun) |
| GENERAL UNDX REGRESSION | 0 by construction — gate sits after the non-crypto early exit (unrun) |
| HIGHER-TIER INHERITANCE | PRESERVED via `tier_satisfies` / `tierSatisfies` (unrun) |
| REACTIVATION | Restores on the existing refresh lifecycle (unrun) |
| TESTS | 2 new files (~30 cases) + 1 updated; **0 executed** |
| AGORA FILES CHANGED | 0 by inspection — no audio path touched; **gate unrun** |
| COMMIT SHA | none — nothing staged or committed |
| **VERDICT** | **PARTIAL** |

## To finish

Once a shell is available, from `/Users/hmcherie/Desktop/CoinPilotX`:

```
cd mobile-native && npx tsc --noEmit
npx jest src/screens/__tests__/PremiumCenterScreen.expiryLock.test.tsx \
         src/screens/__tests__/PremiumCenterScreen.cryptoIntelligence.test.tsx
cd .. && python3 -m pytest tests/crypto_premium -q
python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD
```

Then stage only the five mission-owned paths explicitly (no `git add -A`), commit,
and fill in the SHA and the gate results above.
