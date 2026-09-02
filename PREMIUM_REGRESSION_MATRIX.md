# Premium Regression Matrix

**Mission:** Verify installed premium binary + isolate Portfolio runtime failure
**Date:** 2026-09-02
**Verdict:** PARTIAL — see [Hard Gate](#hard-gate-stage-9)

---

## Headline

**There was no regression.** Nothing was deleted from the source tree, and Portfolio
had no broken boundary. All three reported symptoms trace to one cause: **the installed
binary was cut from source older than the features being looked for.**

The trap is a build-number/source-age inversion. The device carried
`CFBundleVersion 20`, which is *numerically higher* than build 19 — but build 20 was cut
from a branch that does not contain build 19's commits. **A higher build number did not
mean newer code.**

| | Build 20 (installed) | Build 19 (fresh, this mission) |
|---|---|---|
| Source commit | `48776877` "ios: sync embedded CFBundleVersion to 20" | `16d4a98a` (HEAD) |
| Branch | `claude/naughty-jepsen-4586d4` | `release/full-sweep-20260826` |
| Committed | 2026-08-31 22:07 | current HEAD |

Evidence that nothing was removed:

```
git diff --diff-filter=D --name-only ffbc4db0 HEAD -- mobile-native/   # (empty)
git diff --name-only origin/main HEAD -- mobile-native                 # (empty)
git diff --stat ffbc4db0 HEAD -- mobile-native/  # 5031 insertions, 54 deletions
```

---

## Matrix

| Feature | Build 20 (installed) | Current Source (HEAD) | Fresh Build | Backend | Runtime Status | Root Cause | Fix | Evidence |
|---|---|---|---|---|---|---|---|---|
| **Portfolio** | Tile present, routed to legacy `CryptoPortfolio` | `PortfolioScreen.tsx`, no whole-screen gate | ✅ opens, add-holding form, empty state | `/api/portfolio` → 401 (exists) | **WORKING** — was never broken | Legacy screen correctly honored server `premium_required` for an **Expired** account and drew the upsell. The "large empty blue area" *was* `PremiumUpsellPanel` in an otherwise-empty ScrollView. | None needed in code. Fresh build. | `09f4a416` (08-25) `in_build20=NO`, `in_HEAD=YES`; sim screenshot |
| **Watchlists** | Screen + route + resolver present; **tile absent** from Premium surface | Tile in `CRYPTO_INTELLIGENCE_FEATURES` | ✅ opens, shows real lists *Eth*/*Btc*, live prices, "1 alert" | `/api/crypto/watchlists` → 401; `/api/crypto/favorites` → 401 | **WORKING** | Failure class = **visibility**. Was Dashboard-reachable in build 20, just not from Premium Center. | None needed in code. Fresh build. | `ee2559b0` (08-25) `in_build20=NO`, `in_HEAD=YES` |
| **Market Pulse** | **NOT SHIPPED IN INSTALLED BUILD** | Tile + `MarketPulseScreen` + route + backend | ✅ opens, live global market card + ranked coin list | `/api/pulse/market/global` → 401; `services/market_pulse_routes.py` @ `bot.py:1284` | **WORKING** | Feature postdates the installed binary. Not a regression. | None needed in code. Fresh build. | `0ecbe5ed` (08-30) `in_build20=NO`, `in_HEAD=YES` |
| **UNDX** | Tile present but **dead** — no `go`, no chevron, not tappable | Tile with `go: nav.navigate("UndxCapabilities")` | ✅ opens catalog: 124 available / 76 read / 48 actions / 43 needs-confirm | n/a (bundled catalog + auth) | **WORKING** | Same stale binary. `CryptoIntelligenceRow` renders a plain `View` (not `Pressable`) when `go` is absent. | None needed in code. Fresh build. | `PremiumCenterScreen.tsx:1169-1204` |
| **Alerts** | Tile present, working | Tile present | ✅ opens Alert Management, 1 alert, "Checks Running" | live | **WORKING** — no change | n/a | n/a | sim screenshot |

---

## Binary Provenance (Stages 1–2)

| Surface | Physical (P3r7or) | Simulator | HEAD |
|---|---|---|---|
| Installed version *before* | 1.0.1 (20) | 1.0.1 (20) | — |
| Market Pulse | ABSENT | ABSENT (all markers 0) | PRESENT |
| Watchlists tile | ABSENT | ABSENT | PRESENT |
| Portfolio (new screen) | ABSENT (legacy present) | ABSENT (legacy present) | PRESENT |

Simulator build-20 provenance was proven by SHA-256: the installed `main.jsbundle` hash
matched the `build-sim20` product in worktree `hardcore-jemison-f28ede` @ `48776877`.

**Method note.** `main.jsbundle` is Hermes bytecode (v96), so `grep -F` against it is
unreliable — it initially returned 0 for *every* marker including strings known to be
present. All marker counts here use `strings -a | grep -c -F`. Markers were chosen to be
decisive: API path literals (e.g. `/api/pulse/market/global`) exist only in a feature's
own API module. i18n catalog keys were rejected as markers — they are bundled wholesale
and prove nothing about screen code.

---

## Fresh Build Verification (Stage 7)

Built from HEAD `16d4a98a` in an **isolated worktree** (`/Users/hmcherie/wt-premium-verify`),
*not* the dirty main checkout — see [Hazard](#outstanding-hazard) for why.

Both artifacts carry **1.0.1 (19)**, numerically *lower* than the installed 20. The
simulator accepted the downgrade in place, so the signed-in session survived and no
re-authentication was needed.

- Simulator `main.jsbundle` SHA-256 `3ad44c8c…5aea` — **matches the build product exactly**.
- Device install confirmed at OS level: `devicectl device info apps` → `PulseSoc  com.pulsesoc.app  1.0.1  19`.
- All 7 feature markers = **1** in both the simulator and device bundles (all were **0** in build 20).

### 8-point check — Simulator: 8/8 ✅

| # | Check | Result |
|---|---|---|
| 1 | Premium Center loads | ✅ loads, status *Expired* |
| 2 | Market Pulse tile visible | ✅ |
| 3 | Watchlists tile visible | ✅ |
| 4 | Portfolio tile visible | ✅ |
| 5 | Each tile opens correct native screen | ✅ 5/5 |
| 6 | Back navigation works | ✅ every time, scroll position preserved |
| 7 | No premium entitlement regression | ✅ improved — Expired account now reaches Portfolio |
| 8 | No unrelated Premium tiles disappeared | ✅ Alerts + UNDX both work |

The Crypto intelligence section renders **five rows instead of three**, every row with a chevron.

### 8-point check — Physical device (P3r7or): NOT VERIFIED

Fresh build **installed and proven** on device. Visual verification **not performed** —
the device was locked, and launching requires the owner's passcode. This is the sole
outstanding item.

---

## Gates (Stage 9)

| Gate | Result |
|---|---|
| `tsc --noEmit` | ✅ clean |
| i18n validate | ✅ 100%, 2800/2800 keys × 11 locales (2 advisory plural warnings) |
| jest | ✅ **5086/5086** |
| iOS simulator build | ✅ succeeded |
| iOS device build | ✅ succeeded |

**On the jest result.** The first run reported 10 failed suites, *all* `Exceeded timeout of
5000 ms`, all in screens unrelated to Premium (Home, Reels, Ads, Pages, BusinessOS,
settings). Re-running exactly those 10 suites with `--runInBand` and no simulator load:
**353/353 in 22 s** against a 361 s estimate. Load-induced flake, proven rather than
assumed. Every Premium suite passed on the *first* run under the same load — including
`PremiumCenterScreen.cryptoIntelligence.test.tsx`, the golden guard asserting all five tiles.

---

## Outstanding Hazard

`/api/private-office/entitlement` returns **404** in production — it is undeployed.

The uncommitted change in `mobile-native/src/navigation/AppNavigator.tsx` swaps the drawer
premium badge from `premium_status` to `isMember(useCanonicalTier())`, and `canonicalTier`
reads that missing endpoint:

```diff
-  premium: ["active", "premium", "founder"].includes(String(profile?.premium_status || …)),
+  premium: isMember(canonicalTier),
```

Shipping as-is would **dark the premium badge for every member**. This is why the
verification build was cut from HEAD in an isolated worktree: building the dirty tree
would have injected a fresh defect into the very run meant to prove the tree clean.

Not acted on — the mission prohibits modifying premium code.

Cosmetic, also not acted on: Portfolio's empty state renders `$0.000000` (six decimals)
with a `-- · --` subtitle.

---

## Hard Gate (Stage 9)

| Requirement | Status |
|---|---|
| Binary version proven | ✅ |
| All three classifications proven | ✅ |
| Portfolio root cause proven | ✅ (no defect existed) |
| Fresh build installed | ✅ simulator + device |
| All tiles visually verified | ⚠️ simulator only — device locked |
| All routes verified | ✅ |
| Nothing removed | ✅ |
| TS / tests / build gates pass | ✅ |

# VERDICT: PARTIAL

Every technical finding is proven and every gate is green. The single unmet requirement is
**on-device visual confirmation**, which is blocked by the device lock and requires the
owner. Per the mission's "No false PASS," this is not a PASS.

**To close to PASS:** unlock P3r7or, open Premium Center, and confirm five tiles under
Crypto intelligence.

---

## Working-Tree Integrity

No `stash`, `reset`, `checkout`, or `clean` was used at any point. The main checkout's 13
dirty entries are intact. `/Users/hmcherie/wt-premium-verify` contains zero source
modifications — it was a build host only.
