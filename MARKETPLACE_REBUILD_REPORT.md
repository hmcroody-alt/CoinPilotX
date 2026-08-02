# Marketplace rebuild — final report

**Mission:** rebuild the Marketplace screen behind Sections card #3 of the business dashboard
("Marketplace — List an item and manage what you sell"), with a Selling mode and a Buying mode
under one header.

**Status:** built, typechecking, and tested. Both modes render, the mode toggle preserves each
mode's scroll position, and every surface that has no backend behind it is dark behind a flag
rather than faked. The offer state machine is complete and tested but has nothing to talk to.

---

## What was built

The screen is a new file on a new route, `MarketplaceManager`, rather than a rewrite of the
existing `MarketplaceScreen`. That existing screen is the shipped *consumer* browse surface —
dark theme, bottom-nav coupling, detail modal, checkout — and it is a different job for a
different person. The brief's screen is the seller's manager. This follows the same pattern the
Business Profile mission used: add beside, do not replace. `MarketplaceScreen` is untouched.

The Sections card previously pointed at `MarketplaceCreateGateway`, which opens the composer.
That answers the "list an item" half of the card's promise; the "manage what you sell" half had
nowhere to go. The card now points at the manager, and the manager's footer CTA still opens the
composer. Every other caller of the gateway is untouched.

### Selling mode

Summary chips (active items, offers waiting, saves this week), an "Offers to answer" section, a
tabbed list of the seller's own items (Active / Sold / Drafts / Expired), a boost promo card, a
2-up grid of secondary destinations, and a footer "List an item" CTA.

The section order is deliberate and differs from a Store dashboard: offers come before
inventory, because the thing that needs a Marketplace seller today is a person waiting for an
answer, not a number.

### Buying mode

A category rail, a saved-search alert slot, a virtualized 2-column item grid with NEW/FEATURED
badges and heart toggles, and a "Show more nearby" pagination button.

The per-card action button is split by real fulfillment data, not by category guessing:
`delivery_type` on the listing decides between "Add to cart" and "Make offer". That column
already existed and was simply never selected by any endpoint.

### Mode architecture

Both panes stay mounted as absolutely-positioned siblings, toggled with `display: none`. That is
the only way each mode's scroll offset survives the toggle. Switching modes crossfades and does
not re-fetch — one load populates both. The last-used mode persists to AsyncStorage.

The category rail and the search field are rendered *outside* the feed's `FlatList`, so that a
feed failure structurally cannot hide them, as the brief requires.

---

## Three fields that turned out to be real

The reference design needs `created_at`, `featured` and `delivery_type`. All three exist in
`marketplace_listings` and none of them were being selected by any endpoint. Adding them was
five words of SQL in three `SELECT`s in `bot.py`, and it converts the NEW badge, the FEATURED
badge, listing staleness, and the entire Add-to-cart-vs-Make-offer split from invented data into
real data. That was worth the SQL.

---

## MOCK-DATA table

Twelve fields the design shows that this app has no source for. The list is exported as
`MARKETPLACE_MOCK_DATA_GAPS` from `src/api/marketplaceScreen.ts` and asserted in a test, so if
someone later fills one in with a plausible default the count changes and the test says so.

| # | Field | Backend work it needs | Flag |
|---|---|---|---|
| 1 | Offers (make, accept, counter, decline, expire) | `marketplace_offers` table + CRUD endpoints + a push notification on accept | `MARKETPLACE_OFFERS_ENABLED` |
| 2 | Cart and cart badge count | cart endpoints (add, list, remove) over the existing payments surface | `MARKETPLACE_CART_ENABLED` |
| 3 | Boost purchase and price | boost SKU + a charge that sets `listings.featured`, through existing payments | `MARKETPLACE_BOOST_ENABLED` |
| 4 | Per-listing views, saves and offer counts | listing impressions endpoint + a saves aggregate per listing | — |
| 5 | Saved searches and new-match counts | `saved_searches` table + a matcher that records `last_seen_listing_id` | — |
| 6 | Location strip, radius, per-item distance | listing lat/lon (coarse) + account radius preference; expose distance only, never coordinates | — |
| 7 | Seller rating and review count | per-seller review aggregate (mean rating + count) | — |
| 8 | Buyer rating flow | review write endpoint + a rating screen; no route exists to link to | — |
| 9 | Saved meetup spots | `meetup_spots` table + a settings screen | — |
| 10 | Sold history revenue this month | an order row written when an offer is accepted | — |
| 11 | Original price (strikethrough on drops) | listing price history, or a `previous_price_label` column | — |
| 12 | Notification bell and buyer-message unread counts | unread counts endpoint scoped to marketplace conversations | — |

### How each gap is handled on screen

Nothing invents a number. Specifically:

Unbacked figures render an em dash, not a zero — a zero is a claim that the answer is none,
a dash says it is unknown. The seller row's engagement line is assembled from only the fields
that exist and disappears entirely when none do, rather than showing "0 views · 0 saves". The
boost button never carries a price: a made-up price on a button that takes money is the single
worst place to invent a number, so it reads "See boost options". The location strip says
"Location not set — showing all listings" rather than printing a fabricated "Within 10 mi of
San Francisco". The seller-rating and meetup-spots tiles are rendered *disabled* rather than
removed — the meetup safety affordance in particular should not quietly vanish. The
saved-search alert is wired with zero counts rather than deleted, so turning the data on later
is a data change and not a UI change.

---

## Feature flags

All three are `false` and exported from `src/api/marketplaceOffers.ts`:

- `MARKETPLACE_OFFERS_ENABLED`
- `MARKETPLACE_CART_ENABLED`
- `MARKETPLACE_BOOST_ENABLED`

These are asserted as constants in the test suite rather than as absent UI, because a flag
flipped on with no backend behind it is the failure being guarded against.

The `addToCart` handler opens with a flag guard even though the button is not rendered while the
flag is off. That guard exists so that turning the flag on without wiring the cart cannot
silently no-op into a "Added ✓" confirmation — a false success is worse than a missing button.

---

## Offer state machine

`open → accepted | countered | declined | expired | withdrawn`, implemented in
`src/api/marketplaceOffers.ts` with a 38-test suite.

Counter closes the original as `countered` and creates a new open offer in the other direction.
All transitions are idempotent. Double-tap protection works by stamping `pending` on the offer
*before* anything async happens; the card reads that stamp and greys out all three buttons
together. Disabling all three rather than only the pressed one is deliberate — Accept and
Decline racing each other is a worse outcome than either being pressed twice.

Expiry sweeps once a minute and dates the expiry at the lapse, not at the moment anyone noticed.

**Flagged decision:** no offer TTL constant exists anywhere in this codebase. `OFFER_TTL_HOURS =
72` is the brief's proposed default, adopted as a proposal, not as a discovered value. Likewise
`STALE_LISTING_MS = 30 days` for the staleness flag.

---

## Trade dress

The primary CTA fill is a single swappable constant. `STORE_CTA` in `src/theme/storeLight.ts`
defaults to `STORE_CTA_PULSESOC` (green `#2EE6A8 → #22C48D`, text `#04231A`). The reference
design's yellow is retained as `STORE_CTA_REFERENCE` (`#FFD814 → #F7CA00`, text `#0F1111`).

To ship the reference colour instead, change one line:

```ts
export const STORE_CTA = STORE_CTA_REFERENCE;
```

The text colour travels with the fill, so contrast stays correct either way.

Marketplace has a second CTA — the buyer-side "Add to cart" — and `MARKETPLACE_CART_CTA` in
`marketplaceLight.ts` aliases `STORE_CTA` rather than hardcoding a colour, so that one edit
still swaps every yellow surface across both missions.

The green "Make offer" button is a *different affordance* (negotiate, not buy) and takes the
success green. That is a semantic difference rather than a trade-dress one, so it is a real
token and is not part of the swap.

**Recommendation:** ship the green. The yellow is the reference design's trade dress, and the
green is already the app's own.

---

## FEATURED-as-promoted disclosure

Confirmed against this app's existing ads disclosure. `src/components/SponsoredAdCard.tsx`
discloses paid placement with a visible "Sponsored" label and
`accessibilityLabel="Sponsored advertisement"`.

`ItemGridCard` matches it: the badge reads FEATURED visually, the accessibility label announces
**"Sponsored"** — the same word the existing disclosure uses — and a visible **"Promoted"** line
sits under the card body. The disclosure is therefore present in text for a sighted user, in text
for a screen-reader user, and is not carried by badge colour alone.

---

## Accessibility

Offer cards announce buyer, item, listed price, offer amount and age in one label. The three
actions have distinct labels ("Accept offer of $95"). NEW, FEATURED and SOLD are text, never
colour alone. Glow and shimmer carry no meaning on their own. Heart toggles announce saved and
unsaved states. Cart and saved badge counts are exposed. Touch targets are ≥ 44pt, including the
counter-sheet input, which is sized rather than hit-slopped because hit-slop does not apply to a
text field. Grid cards are fully tappable with the heart and the action button as separate
targets. All text scales with the OS font setting.

Under OS reduce-motion every animation is disabled and no content disappears — pinned by a test.

---

## Motion

Built on React Native's core `Animated` plus the existing `logiNexusMotion` / `storeMotion` /
`marketplaceMotion` helper layers. **No new animation library was added** — the codebase has no
Reanimated and no Moti, and adding either for this screen would have been a large dependency for
a screen that does not need one. Everything is transform and opacity on the native driver.

Entrance staggers once per mode-mount. The mode switch crossfades rather than re-running the
full stagger. Ambient loops (offer shimmer, glow breathing) are staggered across cards so the
grid never pulses in unison, and pause when a card scrolls out of view — the grid tracks
visibility through `onViewableItemsChanged` and passes a `visible` prop down, so off-screen
cards run nothing.

The SOLD overlay wipes in on the `false → true` edge only, so a row already sold when the list
paints shows the banner at rest, while a row that sells under the seller's eyes updates in place
with no reload.

---

## States

**Loading** — skeletons matching each mode's real layout, not a spinner.

**Empty** — Selling with no items keeps the header and chips and replaces the list with an
invitation; no offers hides that section entirely rather than showing an empty heading.

**Error** — per-section, inline, with retry. The two halves load independently and fail
independently: a dead feed keeps the seller's own items, and dead seller listings keep the feed.
Both are pinned by tests.

**Offline** — cached feed with a "showing items saved {when}" note. Offer actions are blocked
rather than queued, because with no backend there is nothing to queue against and a queued accept
that never lands is a lie.

**Sold/expired** — the row updates in place with the overlay animation, no full reload.

---

## Files

**New (4,189 lines):**

| Lines | File |
|---|---|
| 1,790 | `mobile-native/src/screens/MarketplaceManagerScreen.tsx` |
| 639 | `mobile-native/src/api/marketplaceScreen.ts` |
| 434 | `mobile-native/src/api/marketplaceOffers.ts` |
| 346 | `mobile-native/src/api/__tests__/marketplaceOffers.test.ts` |
| 302 | `mobile-native/src/components/marketplace/ItemGridCard.tsx` |
| 319 | `mobile-native/src/components/marketplace/OfferCard.tsx` |
| 277 | `mobile-native/src/theme/marketplaceMotion.ts` |
| 203 | `mobile-native/src/screens/__tests__/MarketplaceManagerScreen.test.tsx` |
| 171 | `mobile-native/src/components/marketplace/GlowButton.tsx` |
| 133 | `mobile-native/src/components/marketplace/ModeToggle.tsx` |
| 124 | `mobile-native/src/components/marketplace/SavedSearchAlert.tsx` |
| 114 | `mobile-native/src/components/marketplace/CategoryChipRail.tsx` |
| 105 | `mobile-native/src/theme/marketplaceLight.ts` |
| 32 | `mobile-native/src/components/marketplace/index.ts` |

**Modified (69 insertions, 4 deletions):**

| Lines | File | Change |
|---|---|---|
| +36/-2 | `src/components/store/StoreHeader.tsx` | three optional props so both missions share one header |
| +18 | `src/api/marketplace.ts` | seller listings/orders loaders |
| +7/-2 | `src/api/businessOs.ts` | Sections card repointed to the manager |
| +6 | `src/navigation/types.ts` | `MarketplaceManager` route param |
| +3 | `src/navigation/AppNavigator.tsx` | route registration |
| +3 | `bot.py` | `created_at`, `featured`, `delivery_type` added to three SELECTs |

---

## Components: created vs reused

**Created (6, as the brief specified):** `OfferCard`, `ItemGridCard`, `GlowButton` (cart and
offer variants), `CategoryChipRail`, `SavedSearchAlert`, `ModeToggle`.

**Reused from the Store mission:** `StoreHeader`, `StoreQuickLinkTile`, `StoreOfflineNote`,
`StoreSectionError`, `StoreSkeletonBlock`, `StoreRowSkeleton`, the `storeLight` token set, and
the `storeMotion` hooks (`useStoreEntrance`, `useStoreAmbient`, `useStorePress`,
`useStoreBadgePop`, `useStoreValueArrival`).

`StoreHeader` was extended *additively* with three optional props — `accessories`, `below`, and
`hideNotifications` — rather than forked into a `MarketplaceHeader`. The Store screen passes none
of them and renders exactly as it did before the props existed. There remains exactly one header
definition in the codebase.

`marketplaceLight` extends `storeLight` without editing it, so the Store surface cannot regress
from a Marketplace token change.

---

## Deviations, with reasons

**No screen recording.** This environment has no simulator, no device and no build tooling, so a
recording of the entrance, the toggle, the offer round-trip and the glow cycles cannot be
produced here. This was disclosed earlier in the mission. Everything the recording would show is
covered by tests except the animation timings themselves.

**The two reference HTML files were never attached.** `marketplace-live.html` and
`marketplace-buyer-live.html` were named as the visual and motion source of truth, but no upload
arrived and no matching file exists anywhere in the workspace. The entire visual and motion spec
was therefore implemented from the brief's prose. If those files still exist, a pass comparing
them against the built screen is worth doing.

**`StoreTabBar` was not reused for the Selling tabs.** It is typed to `StoreTabKey`
(`all | active | low | out | drafts`) and cannot express the Marketplace tabs
(`active | sold | drafts | expired`). Loosening a shipped component's type to fit a second
caller would weaken it for the first, so the screen has a small local tab row instead.

**UI strings are hardcoded English**, matching the Store mission's precedent. Currency, counts
and relative times all go through `useFormatters()`, so the numbers localize even though the
labels do not.

**The Buying empty state cannot offer one-tap radius expansion**, because no radius setting
exists to widen (gap #6). It offers another category and a pull-to-refresh instead.

**The counter sheet, the cart badge spring, and the add-to-cart confirmation are unreachable at
runtime** while their flags are off. They are built and wired, not stubbed, so turning the flags
on is a backend change rather than a UI change — but they cannot currently be exercised by a
test, and that is an honest limitation rather than a passing state.

**Add-to-cart springs the header badge rather than flying a confirmation to it.** The brief
allows this fallback explicitly. A flight would need the grid card's screen position measured and
an overlay above the header, which is real work in a stack with no shared-element transition.

---

## Verification

| Check | Result |
|---|---|
| `tsc --noEmit` | clean for every Marketplace file |
| `MarketplaceManagerScreen.test.tsx` | 11 passed |
| `marketplaceOffers.test.ts` | 38 passed |
| `src/navigation` + `src/components` | 210 passed |
| `src/api` + `src/theme` | 286 passed |
| `src/core` `src/social` `src/session` `src/settings` `src/sharing` `src/i18n` `src/hooks` | 991 passed |
| `npm run i18n:validate` | OK — 11 locales (pre-existing es/fr/pt plural advisories only) |

The route-guard test (`src/navigation/__tests__/businessOsRoutes.test.ts`) regex-scans
`AppNavigator.tsx` and asserts every active Sections route is registered. It picked up
`MarketplaceManager` automatically and passes.

### One pre-existing failure, not from this mission

`src/screens/__tests__/BusinessOsAdvertisingScreen.test.tsx` fails 4 tests, and
`BusinessOsAdvertisingScreen.tsx:388` has a typecheck error
(`ACTION_LABELS[a]` is `string | undefined`).

This is a separate, in-progress advertising rebuild sitting in the same working tree — a 663-line
diff to that screen alongside new untracked `api/adsDashboard.ts`, `components/ads/`,
`theme/adsLight.ts` and `theme/adsMotion.ts`. It is independent of Marketplace: that screen
imports nothing from any file this mission created or changed, its test mocks `api/businessOs`
itself, and the failures are all "Unable to find an element with accessibility label: Pause
Running" — an incomplete campaign-action wiring, the same root as the typecheck error.

I did not fix it. The failing assertions describe missing behaviour rather than a typo, so
guessing at that mission's intent risks making it look finished when it is not.

---

## Open questions

1. **Do the two reference HTML files still exist?** The visual and motion spec was implemented
   from prose alone. A comparison pass would be worth doing.

2. **Is 72h the offer TTL you want?** No constant exists in the codebase; 72h is adopted as the
   brief's proposal.

3. **Which offer backend shape do you want?** The state machine is complete and tested and needs
   only a table and four endpoints. That single gap unlocks items #1, #8 and #10 in the MOCK-DATA
   table.

4. **Green or yellow CTA?** One-line swap either way; green is the current default.

5. **Should the disabled Meetup spots tile link somewhere interim?** It is kept and disabled so
   the safety affordance does not vanish, but it currently goes nowhere.

6. **Should I look at the advertising screen failures**, or is that mission still in flight?
