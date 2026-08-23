# PulseSoc 1.0.1 — App Store Release Manifest

- **App**: PulseSoc — ASC App ID `6777591572`, bundle `com.pulsesoc.app`
- **Marketing version**: 1.0.1
- **Build**: 16 (rejected) → **17** (this release)
- **Branch**: `codex/app-review-final-readiness`
- **Start SHA**: `8099f6fb`
- **Capture device**: iPhone 17 Pro Max simulator, iOS 26.5 (`E859950D-B187-4897-B389-05447C5AD796`)
- **Native capture size**: 1320 × 2868 — the iPhone 6.9" App Store slot, captured 1:1, never scaled

## Why this release exists

Build 16 was rejected under **Guideline 2.1(b) — Performance — App Completeness**: the app
referenced subscriptions, but the in-app purchase products had never been submitted for
review. Apple asked for three things — the IAP products submitted for review, an App Review
screenshot for those IAPs, and a new binary.

The underlying product-discovery defect (StoreKit returned zero products) was fixed
separately in `8099f6fb`. This manifest covers only the submission assets.

## Subscription products

| Plan | Product ID | ASC ID | Level | ASC state |
|---|---|---|---|---|
| Monthly | `com.pulsesoc.premium.monthly` | 6804358210 | 1 | Prepare for Submission |
| Annual | `com.pulsesoc.premium.annual` | 6804362192 | 2 | Prepare for Submission |

Group: **PulseSoc Premium** (`22328974`). Neither product is in *Missing Metadata*.

Backend mapping verified read-only, both directions:
`services/pulse_payment_router.py:138` (`APPLE_PREMIUM_PRODUCTS`),
`services/business_os/entitlements/iap_apple.py:281-282` (product → entitlement key), with
`iap_apple.py:472` rejecting unknown product IDs.

> **Open decision for the owner** — Monthly is Level 1 and Annual is Level 2, so StoreKit
> treats Monthly → Annual as a *downgrade* (deferred to the next renewal date) rather than an
> upgrade. There are no subscribers yet, so reordering is free right now and gets costlier
> later. It is an account-settings change and has not been made.

## IAP App Review screenshot — Stage 2–4

`subscription-review/premium_monthly_review.png`
`subscription-review/premium_annual_review.png`

One capture serves both products: a single frame showing **both** plans, **both** Apple-supplied
price strings, and the purchase context. 1320 × 2868, 346,239 bytes.

Frame contents: "Membership and billing" header · Monthly $9.99 · Annual $99.99 · Save 17% ·
Continue · Restore Purchases · NOT SUBSCRIBED.

Clean against the Stage 6 prohibitions: no "Plans aren't available", no StoreKit error, no
loading state, no debug copy, no QA label.

One fix was required to get here. The nav header fell through to the developer placeholder
**"Native PulseSoc route"** for any unrecognised stack, and Premium was one of them — which
would have put developer scaffolding in the exact frame Apple reviews. Fixed in `a5cbb87d`
by routing Premium to a new `common.navSubtitles.membership` key, translated across all 11
shipped locales.

**Upload status: NOT UPLOADED.** See Blocker A.

## App Store screenshot set — Stage 5–16

Required story: Home → Reels → Messaging → Profile → Marketplace → Business OS → Premium →
Security/Trust.

All captures are in `screenshots/raw/` at native 1320 × 2868. `screenshots/final/` is
**empty and deliberately so** — see Blocker B. The `*_iphone_6_9_en.png` files in `raw/`
are the older set from a previous attempt and are not part of this release.

| # | Screen | File | Verdict | Detail |
|---|---|---|---|---|
| 01 | Home | `01_home.png` | **FAIL** | Three truncated strings — "20 public posts summarized. Aggrega…", "PulseSoc Status cou…", "Transmit to the Pulse Netw…". Counters read 0 creators / 0 live. Stage 8 wants Home to look alive; it looks empty. |
| 02 | Reels | `02_reels.png` | **PASS** | Real cooking video, clean native controls, no broken media. |
| 03 | Messaging | *withheld* | **FAIL** | Contains a room literally named **"Speed QA Room"**, plus real personal names and message snippets. Violates Stage 6 (QA names) and Stage 10 (no private real conversation data). **Deliberately not committed** — it holds a family member's private messages and git history is permanent. It stays at `/tmp/shots/03_messages.png` only. |
| 04 | Profile | `04_profile.png` | **FAIL** | Entirely empty: 0 posts / 0 followers / 1 following / 0 media, and the placeholder "Add a bio to shape your PulseSoc identity." |
| 05 | Marketplace | `05_marketplace.png` | **FAIL** | Two test listings ("T4" $0.50, "Big T" $15.99) sharing one generic "T" placeholder image. Stage 12 requires real product media. |
| 06 | Business OS | *not captured* | **BLOCKED** | Not reachable. `BusinessOs` has no entry in `src/navigation/linking.ts`, and the in-app navigation drawer has no Business OS item in any section — Commerce runs Marketplace → Seller Store → Create Listing → Seller Inventory → Buyer Orders → Premium. Stage 13 cannot be satisfied without an entry point. |
| 07 | Premium | `07_premium.png` | **PASS** | UI-driven rather than content-dependent. Same frame as the IAP review screenshot. |
| 08 | Security / Trust | `08_security.png` | **MARGINAL** | "Trust & Safety" / PulseSoc Safety Grid renders well, but the support panel reads "No support tickets returned by the backend." — developer copy plus an empty state, both named in the Stage 6 prohibitions. |

**Two of eight screens meet the bar.** The brief requires a set "materially better than the
old weak set"; five of the remaining six fail on account content rather than on app quality,
and the sixth has no reachable entry point.

## App Preview — Stage 17–19

**NOT PRODUCED.** The preview flow is Home → Reels → Messages → Profile → Marketplace →
Business OS → Premium. Five of those seven are the screens that fail the still-image bar, and
one is unreachable. Recording the flow now would produce 25 seconds of the same empty and
private content, at higher risk than a still because it cannot be cropped. Blocked behind
Blocker B, not behind any technical limitation.

## Binary — Stage 22–24

- `mobile-native/app.json` — `buildNumber` **16 → 17**, version held at 1.0.1
- Bundle ID `com.pulsesoc.app`, StoreKit entitlement and `associatedDomains` unchanged
- Product IDs unchanged and still matching the backend map

### Gates run

| Gate | Result |
|---|---|
| `npx tsc --noEmit` | clean, exit 0 |
| `npm run i18n:validate` | OK — 11 locales, catalog 1.0.0 |
| `npx jest src/payments …PremiumCenterScreen.planLoading` | 70 passed / 5 suites |
| Real-time audio protected paths | none of the 14 changed files match any of the 52 patterns |

The audio gate's own runner reports a `bot.py` hit, but it diffs `origin/main..HEAD` and so
picks up pre-existing branch commits. Checked directly against the 52 category patterns, this
mission's changes match nothing.

**Archive and upload: NOT DONE.** See Blocker A.

## Blocker A — no upload path to App Store Connect

Nothing can be uploaded from this machine. Every route was tried:

- **Chrome extension `file_upload`** — rejects host filesystem paths outright: *"The MCP
  controller must read the file and pass its contents via the `files` parameter."* The tool's
  schema exposes only `paths`, so the documented workaround is not callable.
- **Local HTTP bridge** — `fetch()` from the ASC page to `127.0.0.1` is blocked by the browser
  before the request leaves it. Confirmed with a listening server, permissive CORS, an OPTIONS
  handler, and `Access-Control-Allow-Private-Network: true`; the server log stays empty. No
  meta CSP is involved.
- **Inline base64** — technically works (`fetch('data:…')` succeeds), but an App Store
  screenshot must be exactly 1320 × 2868, which is ~500 KB → ~4 MB of base64 across eight
  images. Not viable, and it would not help the binary at all.
- **`fastlane` / `altool`** — both installed, both credential-gated. No ASC API key on this
  machine: `~/.appstoreconnect/private_keys/` does not exist, there is no `.p8`, no `Appfile`,
  no `Fastfile`. No EAS CLI, no provisioning profiles.

**Remedy — either one unblocks every remaining upload:**

1. An **ASC API key**: the `.p8` file, the Issuer ID, and the Key ID. That is enough for
   `fastlane deliver` to push the IAP review screenshots, the marketing screenshots, the
   preview, and the binary.
2. The **owner performs the uploads** from a signed-in browser and Xcode.

Option 1 is preferred. I will not handle the account password directly.

## Blocker B — screenshot content

The account signed into the capture simulator is effectively an empty test account. The
failures in the table above are content failures, not app failures — the app renders
correctly, there is simply nothing real in it to photograph, and what *is* there is either
QA scaffolding or a family member's private messages.

I did not fabricate content to fill the gap. Screenshots have to reflect what the app
actually does, and staged posts and invented engagement counts are exactly what Apple treats
as misleading metadata.

**Options for the owner, cheapest first:**

1. **Ship a reduced set.** Apple requires a minimum of one screenshot; three strong ones beat
   eight weak ones. Reels and Premium already qualify, Security is close. This does not block
   resubmission — Apple rejected build 16 over the IAP submission, not over screenshots.
2. **Capture from a populated real account.** If the owner has an account with genuine posts,
   followers, and listings, signing it into the simulator makes the full eight-screen story
   available immediately.
3. **Seed a demo dataset** with real media and non-private conversations. Highest quality,
   most work, and needs a decision about what counts as honest demo content.

Option 1 is the fastest path to resubmission. Option 2 gives the best store page.

## Ready to resubmit

**NO.**

| Requirement | State |
|---|---|
| IAP review screenshot produced | ✅ both filenames, 1320 × 2868, clean |
| IAP review screenshot uploaded to both products | ❌ Blocker A |
| Products attached to the version for review | ❌ Blocker A |
| New screenshot set materially better than the old | ❌ Blocker B — 2 of 8 pass |
| App Preview produced and uploaded | ❌ Blocker B, then A |
| Build 17 archived and uploaded | ❌ Blocker A |
| Build 17 selected on the version | ❌ Blocker A |
| Release gates green | ✅ tsc, i18n, 70 tests, audio |
| No preventable metadata blocker | ⚠️ subscription level ordering is an open decision |

Per the brief's final rule, **"Resubmit to App Review" has not been clicked** and will not be
without explicit authorization.

## Unrelated defects — documented, not fixed

Per the scope lock, these were observed during capture and deliberately left alone:

- The `subtitleForStack()` "Native PulseSoc route" placeholder still shows on other
  unrecognised stacks, e.g. Activity Inbox. Only Premium was fixed, because only Premium is
  reviewed by Apple.
- The navigation drawer exposes an internal QA route path, `/pulse/calls/qa-call-1`, under
  **Calls**.
- The Premium screen carries BETA labels and "aren't switched on yet" copy. Truthful, but it
  weakens the paywall Apple reviews.
- The header shows "99+ ALERTS" and a 99+ bell badge. These are real counts from
  `UnreadSnapshot.bellCount`, not debug output, so they were left as-is.
- Business OS has no deep link and no drawer entry, so it is unreachable outside a direct
  navigation call.

## Carry-forward from the previous mission

Unrelated and still open: the live two-device accept test needs the owner to tap **Accept**,
then promptly run —

```
railway logs --service CoinPilotX --json --since <ISO> -n 5000 > /tmp/win.json \
  && python3 /tmp/poll_verify.py /tmp/win.json
```

Railway retention is roughly 40 minutes, so the log pull has to follow the tap quickly.
