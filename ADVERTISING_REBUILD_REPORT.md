# Advertising screen rebuild — completion report

**Scope:** the Advertising screen — the fourth card in the Business dashboard "Sections" grid, route `BusinessOsAdvertising` — is now a two-sided ads manager. **Marketplace ads** (gold = money) is fully backed by the live `/api/pulse/ads/*` surface. **Post ads** (violet = content promotion) is an unbacked, flag-gated preview. A header toggle swaps modes without a navigation push; both panes stay mounted so each keeps its own scroll, and one wallet chip lives on the header so the balance is rendered once from one object and cannot disagree with itself between modes.

**Verdict: PASS on every gate this sandbox can run.** The screen recording and on-device pass are **NOT_TESTED** — they need Xcode/Expo on the Mac, which this environment cannot drive.

## The route split, and why the old screen survived

`BusinessOsAdvertising` stays a single registered route, so every existing deep link, notification and dashboard tile still lands somewhere valid. `AdvertisingRoute` chooses what answers:

- default → `AdsManagerScreen`, the rebuilt manager;
- `{ mode: "classic" }` → `BusinessOsAdvertisingScreen`, restyled onto the `adsLight` palette so the two halves of one route don't look like two different apps, but structurally the same screen — it still owns every form.

The classic screen owns the ad-account form, the campaign composer, the objective and budget-type choosers and the dollars→cents conversion that its own test pins. Reimplementing those in the manager would have created a second creation path free to drift from the first, so the manager routes into them instead: "Create campaign", "Switch account", "Manage all (N)" and a card tap all navigate to `{ mode: "classic" }`. This is the same split `SellerStore` uses for the Store rebuild.

## Verification evidence

| Gate | Result |
|---|---|
| `npx tsc --noEmit -p tsconfig.json` | exit 0, **0 errors** |
| Full Jest suite (6 shards) | **123 suites / 2092 tests, all passing** |
| `npm run i18n:validate` | OK — 11 locales at 100%, 4 pre-existing advisory warnings |
| `src/api/__tests__/adsDashboard.test.ts` (new) | 29/29 |
| `src/screens/__tests__/AdsManagerScreen.test.tsx` (new) | 9/9 |
| `src/screens/__tests__/AdvertisingRoute.test.tsx` (new) | 2/2 |
| `src/screens/__tests__/BusinessOsAdvertisingScreen.test.tsx` (existing) | 9/9 — unaffected, it renders the classic screen directly |

What the new tests actually hold down, rather than merely exercise:

- A double tap on a delivery switch sends **exactly one** `runAdCampaignAction`. The guard is an in-flight `Set` of campaign ids, so a campaign cannot be paused twice by an impatient thumb.
- `deliverySwitchState` never returns an action that `availableAdCampaignActions` — which mirrors the server's own table — would not accept, and every disabled branch carries a reason string. Ended, in-review and verification-blocked campaigns each render their own explanation.
- `estimated_cpc` arrives from `services/pulse_ads_service.py` in **dollars** (it computes `round(spent_cents / 100 / clicks, 2)`). The KPI converts with `Math.round(value * 100)`; a test pins `2.5 → 250` cents. Reading it as cents would have understated cost-per-click a hundredfold.
- A failed wallet call renders **no chip at all** — asserted by the absence of any `Ad wallet balance $…` label — plus a named failure and a retry.
- The empty-wallet banner precedes the verification banner when both apply, and neither suppresses the other.
- An analytics failure shows a section error and leaves the campaign list on screen.
- `ADS_MOCK_DATA_GAPS.length` is pinned at 9, so closing a gap with a real endpoint (or papering over one with invented data) breaks a test.

## Money truth — no new payment path

**No new payment path was created.** Confirmed by inspection of every call the screen makes:

- Balance comes from `getAdWallet`'s `spendable_balance_cents` (falling back to `available_balance_cents`) through `walletSummary`. There is **no client-side balance arithmetic** anywhere; the screen formats the server's number and nothing else.
- If the wallet call fails, the chip is **absent**, never a stale or fabricated zero. During load it shows `—`.
- "Add funds" opens the existing `BusinessOsPayments` screen. The manager creates no charge, no order and no transfer.
- `adFundingIsLive()` is false because the backend hardcodes `live_charging: false`. The wallet chip therefore reads "Wallet" rather than "Add funds", and the empty-wallet banner says adding funds isn't available in this build instead of offering a button that cannot charge.
- Delivery changes go through `runAdCampaignAction` only — the same endpoint the classic screen uses — and are refused outright while offline.
- All currency goes through `useFormatters().currency`, never a hand-rolled string.

## MOCK-DATA

Nine fields the design asks for that this app has no live source for. Each is declared in `ADS_MOCK_DATA_GAPS` with the backend work it needs, and each preview value carries `mock: true` so a real value can later arrive as `mock: false` with no UI change.

| Field | Mode | Backend work needed |
|---|---|---|
| Spend — last 7 days, per day | marketplace | analytics endpoint returning daily spend buckets *(the total spend IS real)* |
| Campaign learning / limited delivery phase | marketplace | campaign status extended with a delivery-phase field |
| Spend / clicks windowed to the last 7 days | marketplace | date range on `getAdAnalytics` — there is none, so totals are lifetime |
| KPI period-over-period trend (▲/▼) | marketplace | a previous-period figure to compare against |
| Advertising notification bell + unread badge | marketplace | an ads-scoped notification feed |
| Post / Reel / Live promotions | post | a post-promotion service (create, list, review, metrics) |
| Post-ads KPIs (reach, new followers, engagements) | post | delivered metrics + follower attribution per promotion |
| Outperforming-post suggestion | post | per-post organic reach signal + promote-worthiness ranking |
| Promote-a-post picker (recent posts) | post | authored-posts endpoint with per-post reach |

Two consequences visible in the UI rather than hidden in this table: because there is no date range, KPI labels read "**· to date**", never "· 7d"; and because there is no prior period, **no tile shows a trend arrow** — the design's "▼ N% cheaper" CPC treatment was dropped rather than invented. Likes and follows attributable to a promotion have no source at all, so those cells render `"—"` even inside the preview, announced to screen readers as "not yet available".

The suggestion heuristic is client-side and documented: a post is flagged when its reach is at least `HOT_POST_MULTIPLE` (3×) the author's median, and the badge shows the multiple as a number — a badge that only says HOT tells the person nothing they can weigh.

## Feature flags

- **`EXPO_PUBLIC_ADS_POST_MODE`** (exported as `ADS_POST_MODE_FLAG`) gates the entire Post-ads product and the mock per-day spend series. Off by default. Read at call time rather than module load, so it is toggleable in tests. With it off, Post mode renders a "Post ads is coming" card that names the flag; with it on, every figure is tagged Preview.

No other flag was added.

## State machines

Campaign: `draft → in_review → learning → delivering → limited | paused | ended`, with a `blocked_verification` overlay. Backed statuses map deterministically in `campaignPhase`; `learning` and `limited` are declared gaps (the backend collapses both into `active`).

Promotion: `submitted → in_review → promoting → completed | rejected | paused`. `promotionSwitchState` returns `disabled: true` in **every** live phase, because no endpoint accepts the transition — a preview switch that appeared to work would be exactly the "silently no-ops" failure the marketplace side avoids. Rejected promotions render the reason and an "Edit and resubmit ›" link, never a bare "rejected".

Drafts show **no** switch (there is nothing to pause). Ended, in-review and blocked campaigns show a **disabled** switch with its reason rendered beneath it and announced by the `switch` role — shown rather than hidden, so the control the user expects is present and explains itself.

## Trade-dress token swap

The CTA colour is one swappable assignment in `theme/adsLight.ts`: `export const ADS_CTA = ADS_CTA_PULSESOC`. Changing it to `ADS_CTA_REFERENCE` matches the reference product's gold CTA. `adsLight.cta` is a `{ from, to, text }` object and no other file hardcodes a CTA colour. The semantic palette rule holds throughout: **gold/yellow = money, violet = content promotion, blue = analytics**.

## Files

**Added:** `screens/AdsManagerScreen.tsx`, `screens/AdvertisingRoute.tsx`, `api/adsDashboard.ts`, `theme/adsLight.ts`, `theme/adsMotion.ts`, `components/ads/` (AdsHeader, ModeToggle, WalletChip, PauseSwitch, BudgetPacingBar, SpendBarChart, CampaignCard, PromotedPostCard, PromoteRail, SuggestionCard, AdsStatusPill, AdsStates, AdsTabBar, index), and three test files.

**Modified:** `navigation/AppNavigator.tsx` (route now points at `AdvertisingRoute`; the native header is shown only in classic mode, since the manager draws its own), `navigation/types.ts` (route params gained `mode?: "manager" | "classic"`), `api/businessOs.ts`, `screens/BusinessOsAdvertisingScreen.tsx` and its test (restyled onto `adsLight`; the account and campaign forms, the objective and budget-type choosers and the dollars→cents conversion are unchanged, and its 9 tests still pass).

**Reused rather than rebuilt:** `StoreKpiCard` and `StoreQuickLinkTile` from `components/store`, the `useStoreEntrance` / `useStorePress` motion hooks, `useFormatters`, `readJsonCache` / `writeJsonCache`, and `registerSyncInvalidation`.

## Deviations from the brief

1. **No trend arrows on KPI tiles.** No prior-period figure exists. Sourcing it would mean inventing it.
2. **KPIs are lifetime, labelled "· to date".** `getAdAnalytics` takes no date range.
3. **The classic screen was kept, not replaced.** It owns the creation forms; deleting it would have meant duplicating them.
4. **`registerSyncInvalidation` subscribes to `verification` and `marketplace`,** not an ads-specific subsystem — the `NativeSyncSubsystem` union has no ads member. Verification approval unblocks a blocked campaign, and listing boosts are campaigns in the same ledger, so both are correct triggers.
5. **Animation** is React Native's core `Animated` with `useNativeDriver: true` throughout, except the budget-bar fill and the tab underline, which animate layout properties and are one-shots rather than loops. Reduce-motion is a hook input, not a call-site branch: each hook `setValue`s straight to the final state.

## NOT_TESTED / open questions

- **Screen recording and on-device QA: NOT_TESTED.** Entrance choreography per mode, the toggle preserving scroll with a persistent wallet, the chart cascade, the pause/resume round trip, the budget bar's amber transition and the reduce-motion pass all need a running app. tsc and Jest cannot exercise real layout, motion or gestures. This is the same gap the Store rebuild left open, for the same reason: the sandbox grants Terminal at a tier that blocks typing.
- **The two reference HTML files** (`advertising-live.html`, `advertising-posts-live.html`) were never attached. The build follows the written spec alone; visual details that lived only in those files may differ.
- **Review-time claim:** no copy anywhere states how long review takes, because nothing in the backend exposes it. The in-review states say "you'll be able to pause it once it starts", not "usually 24 hours".
- **Post ads must not ship enabled** until the post-promotion service exists.
- **VoiceOver** behaviour is implemented (real switch roles announcing name and state, a text summary on the chart with the bars decorative, LEDs always paired with text, `"—"` announced as "not yet available", metric strips wrapping to 2×2 at large font sizes) and asserted where testable, but final confirmation is a device check.
