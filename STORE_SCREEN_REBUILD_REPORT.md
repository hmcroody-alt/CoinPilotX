# Store screen rebuild — build report

Branch `codex/store-dashboard-live`, commit `171cd0ec`.

The Store screen reached from the second card of the Business dashboard "Sections"
grid has been rebuilt as a seller-facing management dashboard. This report covers
what shipped, what could not be sourced from a backend, the one trade-dress
decision that needs a product owner, and what has not been validated.

## Verification

`npm run typecheck` clean. `npm run i18n:validate` clean (11 locales, catalog
version 1.0.0; four pre-existing advisory plural-form warnings, untouched by this
work). Full jest suite run in six shards: **119 suites, 2041 tests, all passing**,
with the three new suites included.

New coverage, 89 tests:

| Suite | Tests | What it pins |
| --- | --- | --- |
| `src/api/__tests__/storeDashboard.test.ts` | 54 | KPI sums and trend baseline, listing health thresholds, tab counts, attention precedence, per-section load failures, cache fallback, the mock-data gap list itself |
| `src/screens/__tests__/StoreDashboardScreen.test.tsx` | 31 | All five states, navigation targets, accessibility labels, reduce-motion |
| `src/screens/__tests__/SellerStoreRoute.test.ts` | 4 | That only `mode: "dashboard"` diverts, so deep links and the Orders card still reach the old screen |

## What a test found

Writing the derivation tests surfaced a real bug and then a larger one behind it.

`listingHealth` read `Number(listing.quantity)` and checked `Number.isFinite`. But
`Number(null)` is `0`, not `NaN`, so a listing reporting no quantity read as zero
and was marked out of stock. Fixing that produced a TypeScript error, and chasing
the error surfaced the more serious problem: `normalizeMarketplaceListing` in
`src/api/marketplace.ts` already applies `quantity: Number(item.quantity || 0)`
before any listing reaches this module. "No stock tracked" and "zero in stock"
have collapsed into the same value upstream, so the null guard could never have
fired in production. Every digital, course and service listing would have shown as
out of stock and disappeared from its own seller's Active tab.

The normalizer is shared with several screens, so it was left alone. `stockCount()`
now checks `product_type` first — the signal that survives normalization — and
treats digital, course and service listings as having no stock concept. The residual
limitation is that a *physical* listing whose seller simply doesn't track stock still
can't be expressed; that is logged as gap 7 below.

## Files

Created:

- `src/theme/storeLight.ts` — the named light palette. The dark theme in
  `src/theme/logiNexus.ts` is unmodified; every other screen keeps reading it.
- `src/theme/storeMotion.ts` — the motion hooks.
- `src/api/storeDashboard.ts` — the derivation layer and a parallel loader.
- `src/screens/StoreDashboardScreen.tsx` — the screen.
- `src/screens/SellerStoreRoute.tsx` — the route-level split.
- `src/components/store/` — nine components plus a barrel: `StoreHeader`,
  `StoreKpiCard`, `StoreSparkline`, `StoreAttentionBanner`, `StoreTabBar`,
  `StoreListingRow`, `StoreStatusLed`, `StoreQuickLinkTile`, `StoreStates`.
  All are standalone and prop-driven so Orders and Insights can reuse them.

Modified: `src/navigation/AppNavigator.tsx` (points `SellerStore` at the router and
scopes `headerShown: false` to dashboard mode only) and `src/navigation/types.ts`
(adds `"dashboard"` to the existing mode union). `SellerStoreScreen.tsx` is untouched.

Nothing was removed. `SellerStore` remains a single registered route with two
screens behind it, so deep links (`pulse/merchant/...`), the `MerchantDashboard`
and `MerchantProfile` aliases, the Orders card's `mode: "orders"`, and every
`navigate("SellerStore", …)` call in the app keep working unchanged. The existing
screen fires no analytics events, so none were added — there is no schema to
extend without inventing one.

## Animation

React Native's core `Animated` with `useNativeDriver: true`. The project has
neither reanimated nor moti, and the brief forbids adding a library when a capable
one exists. Transform and opacity only.

Two exceptions are unavoidable: the sparkline's `strokeDashoffset` and the tab
underline's `left`/`width` cannot run on the native driver. Both are one-shot
animations, never loops, so neither holds the JS thread.

`reducedMotion` is an input to every hook rather than a branch at the call site.
Under reduce-motion each hook `setValue`s to the final state — entrance is instant,
the sparkline is pre-drawn, LEDs are solid, no sweeps run. Ambient loops are gated
on `useAppForegrounded()`, which treats iOS `inactive` (app switcher, incoming call)
as backgrounded.

## MOCK-DATA gaps

Seven fields in the reference design have no source in this app. None of them are
faked. Each is either omitted from the UI or rendered from an honest stand-in, and
the list is exported as `STORE_MOCK_DATA_GAPS` in `src/api/storeDashboard.ts` and
asserted in a test — if someone later invents a value, the count changes and the
test fails.

| # | Field | Backend work needed | Shipped behaviour |
| --- | --- | --- | --- |
| 1 | Views · 7 days | Seller impressions endpoint (views per storefront per day) | KPI tile omitted |
| 2 | Seller rating | Per-seller review aggregate (mean rating + count) | KPI tile omitted |
| 3 | On-time dispatch % | `order.ship_by` and `order.dispatched_at` | Omitted |
| 4 | Open orders — "N ship today" | `order.ship_by` | Open-orders count ships; the "N ship today" subtitle is omitted |
| 5 | Listing rating and review count | Per-listing review aggregate | Row renders without a star line |
| 6 | Store open / paused | Seller-level storefront status flag | Derived: a store with listings, none orderable, reads as paused. A store with no listings is empty, not paused |
| 7 | Stock tracked / not tracked | `quantity` preserved as `null` through listing normalization, or an explicit `tracks_stock` flag | `product_type` used as a stand-in — correct for digital/course/service, cannot express an untracked physical listing |

Gaps 1–5 are additive endpoints. Gap 6 is a schema field. **Gap 7 is a live
correctness issue** rather than a missing feature, and is the one worth prioritising:
until quantity survives normalization, the seller's Active tab is guessing.

## Decision required: CTA colour

The reference design pairs a yellow CTA gradient (`#FFD814 → #F7CA00`) with a navy
header (`#131A22 → #232F3E`). That combination is close enough to Amazon's trade
dress to warrant a deliberate choice rather than being inherited by following a mock.

**Shipped default is PulseSoc green** (`#2EE6A8 → #22C48D`, dark text `#04231A`).

Both palettes are defined in `src/theme/storeLight.ts`. To ship the reference
yellow instead, change one line:

```ts
export const STORE_CTA = STORE_CTA_REFERENCE;   // was STORE_CTA_PULSESOC
```

`ctaText` travels with the fill, so contrast stays correct either way and nothing
else needs touching. Everything else from the reference — layout density, KPI-first
hierarchy, row anatomy, the navy header itself — is standard marketplace convention
and was kept.

## Deviations, with reasons

**Two KPI tiles omitted, not faked.** Views and Seller rating have no source. The
grid renders Today's sales and Open orders. Adding placeholder tiles reading "—"
would have been worse than a two-tile grid: it teaches the seller that the screen
lies.

**"Storefront design" retargeted.** The brief asks for a storefront designer. This
app has no such screen. The tile is labelled "Storefront" and opens the buyer-facing
marketplace view — the closest real destination — with the subtitle "See your store
the way buyers do".

**Shipping and Returns tiles are disabled.** Neither has a screen or a backend. They
render greyed with an honest subtitle rather than wired to something unrelated. A
tile that opens the wrong screen is worse than one that says "not yet".

**Inventory and Collections filter in place.** Rather than navigating to screens that
don't exist, Inventory sets the tab to Low stock (or All when nothing is low) and
expands the list; Collections shows all listings. Both subtitles are computed from
real data — item counts, low count, distinct category count.

**Listing rows route to `mode: "create"`.** That is the mode carrying the `listings`
panel, which contains the editor. Pinned by `SellerStoreRoute.test.ts` so a future
change to the mode/panel map fails loudly.

## The five states

All five are implemented and covered by render tests.

Loading shows the header, status strip and skeleton cards matching the final layout
— no spinner over a blank screen. Empty store keeps the header, status strip and
KPIs, replacing the listing section with "Your store is ready. It just needs
something to sell." and a route to the create gateway. Error is per-section and
inline: `loadStoreDashboard` reports which half failed, so the screen says
"Listings didn't load." or "Orders didn't load." with a retry on that section only.
A test asserts the string "something went wrong" appears nowhere. Paused swaps the
strip to a gray dot, "Paused — buyers can't order" and "Reopen" while the KPIs stay.
Offline renders cached content with a last-updated note, falling back to the error
treatment when the cache is empty.

The per-section error state required a new loader. The existing
`loadSellerStoreSnapshot` uses `Promise.allSettled` and collapses a failure into an
empty array, which makes "orders failed" indistinguishable from "no orders yet".
`loadStoreDashboard` was added alongside it; the original is untouched because other
screens depend on its behaviour.

## Accessibility

Every interactive element is focusable with a meaningful label. Rows announce
title, price, stock and units sold. Status is never colour-only — an LED is always
paired with text ("Only 2 left", "Out of stock — hidden", "Finish listing"). Tabs
expose their counts to screen readers as e.g. `"Low stock, 1"`. The attention banner
is an alert region. Targets are ≥44pt. The title clamp goes to three lines under OS
font scaling.

Formatting runs through the existing `useFormatters` from `src/i18n/hooks.ts`. The
derivation layer returns numbers, never strings; there is no hardcoded `"$"`
anywhere in the new code.

## Not done

**No live simulator or device run.** The screen typechecks, all tests pass and the
component tree renders under jest, but it has not been launched on iOS or Android.

**No screen recording.** The definition of done calls for a recording of the
entrance sequence, tab switching, one ambient cycle and reduce-motion. That requires
the simulator run above.

The five states are demonstrated through render tests rather than on a device.

**The reference HTML was never attached.** `store-marketplace-live.html` was declared
the visual and motion source of truth but did not arrive. The build follows the
written spec — every colour, radius and timing in it. If the file exists, the two
should be diffed before this is called finished.

## Open questions

1. CTA colour — ship PulseSoc green, or switch to the reference yellow?
2. Gap 7 is a correctness bug on a shared code path. Fix the normalizer, or add a
   `tracks_stock` flag?
3. Shipping settings and returns policy: is there a plan for these screens, or should
   the tiles come out?
4. Analytics — the old screen fires nothing. Should the rebuild introduce events, and
   against what schema?
