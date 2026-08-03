# Business Hub rebuild — mission report

Branch `codex/store-dashboard-live`. Tenth and final mission of the seller-surface
series.

---

## 1. What this screen is, and what it deliberately is not

The Business Hub is the seller's front door. It is the one screen in this series
that **owns nothing**. Every number, badge and state line on it belongs to a
section built by one of the nine prior missions. Its job is to consume those
sources faithfully and get the seller to the right section fast.

Two failure modes were designed against, explicitly:

**The hub inventing state.** A hub that re-derives "3 orders awaiting you" from a
raw payload will, sooner or later, disagree with the Orders screen — and the
seller has no way to tell which one is lying. The rule adopted was: *if
implementing a state line requires logic an owner mission didn't build, that's a
change request to the owner, not code in the hub.* Three such change requests
were made across the series (§4). No derivation lives in hub code.

**The hub being slow.** A front door with a full-screen spinner is a front door
that makes the seller wait to find out they have nothing to do. The screen has no
`loading` state, no aggregate model and no "hub is ready" promise, because no such
moment exists (§5).

---

## 2. Binding table — proof that nothing is re-derived

Seven independent bindings, one per owner source. Every value on the screen
traces to a row here, and the "Derivation" column names the **owner's own
exported function** that produced it. There is no column for "computed in the
hub", because there is nothing to put in it.

| Binding | Owner module | Loader | Consumers | Derivation used | Invalidated by |
|---|---|---|---|---|---|
| `ordersBinding` | `api/ordersDashboard` | `loadSellerOrdersModel` | Orders card, strip "To fulfil" | `ordersAwaitingSeller(model.orders)` | `orders`, `marketplace` |
| `storeBinding` | `api/storeDashboard` | `loadStoreDashboard` | Store card, Marketplace card | `storeHealthCounts(deriveRows(snapshotFrom(result)))` | `seller_inventory`, `marketplace` |
| `insightsTodayBinding` | `api/insightsDashboard` | `loadInsights("today")` | strip "Today's sales" | `insightsRevenueMajor(summary)` | `orders` |
| `insights7dBinding` | `api/insightsDashboard` | `loadInsights("7d")` | Insights card | `compareToPrior(...)` via `insightsStateLine` | `orders` |
| `adsBinding` | `api/adsDashboard` | `loadAdsMarketplace` | Advertising card, Payments card | `adAccountCanTransact(account)`; `WalletSummary.balanceLabel` | — (focus/pull only) |
| `verificationBinding` | `api/verification` | `loadVerificationState` (+ cache hydrate) | header tick, header context line, Verification card | `state.status`, read once by `verificationTick` | `verification` |
| `profileBinding` | `api/sellerApplication` | `loadSellerApplication` (+ cache hydrate) | Profile card | `application.completeness` | — |
| *(not a binding)* | `core/unreadCounts` | `refreshUnreadCounts` | Messages card, bell, strip "Unread" | `useUnreadCounts()` | own store |

Three of these deserve a note.

**`adsBinding` feeds two cards.** Advertising and Payments read one binding
because they are one network round of one owner. That is fan-out from a single
source, not duplication — and it is what makes it impossible for the two cards to
disagree about the same wallet.

**`verificationBinding` feeds three surfaces.** The header tick, the header
context line and the Verification card all call `verificationTick` /
`verificationStateLine` on the same `state.status`. Two renderings of one status
is the point; two derivations of one status would be the drift the mission warns
about. A test pins the pairs (`approved` → `verified` tick + `verification.approved`
line, and so on) so they cannot separate.

**Messages uses no binding at all.** `core/unreadCounts` is already a module
singleton with a listener set — the exact shape a hub binding has. Wrapping it
would create a second store for a number that already has exactly one. The card
calls `useUnreadCounts()` directly, and this is documented at the call site.

---

## 3. State-line priority contract

Exactly one line per card. Where two facts are true at once, the table says which
wins and why. Every branch has a stable `key`, and the tests assert **keys, not
copy** — copy may be reworded; the ordering is the decision this mission is
accountable for.

### Business Profile
| Priority | Key | Condition | Line |
|---|---|---|---|
| 1 | `profile.complete` | `completeness >= 100` | "Complete" |
| 2 | `profile.progress` | otherwise | "{n}% complete" |

Threshold `HUB_THRESHOLDS.profileComplete = 100`. The mini-bar reads
`profileCompletenessFraction`, a separate export, so the renderer never parses the
percentage back out of the copy.

### Store
| Priority | Key | Condition | Why it outranks |
|---|---|---|---|
| 1 | `store.attention` | `low > 0 \|\| out > 0` | A listing buyers cannot order is a live loss; a healthy catalogue is good news that can wait. |
| 2 | `store.active` | `active > 0` | |
| 3 | `store.empty` | nothing listed | |

### Marketplace
| Priority | Key | Condition |
|---|---|---|
| 1 | `marketplace.offers` | open offers exist — **unreachable**, `HUB_MARKETPLACE_OFFERS` off |
| 2 | `marketplace.items` | `activeItems > 0` |
| 3 | `marketplace.empty` | otherwise |

### Advertising
| Priority | Key | Condition | Why it outranks |
|---|---|---|---|
| 1 | `advertising.blocked` | no account can transact **and** campaigns exist | A seller whose campaigns cannot deliver needs to know that before they know how many they have. |
| 2 | `advertising.delivering` | ≥1 campaign `active` | |
| 3 | `advertising.idle` | campaigns exist, none delivering | |
| 4 | `advertising.noCampaigns` / `advertising.noAccount` | | |

`advertising.noAccount` short-circuits above everything when `accounts.length === 0`.

### Orders
| Priority | Key | Condition |
|---|---|---|
| 1 | `orders.awaiting` | `awaiting > 0` → "{n} orders to fulfil" |
| 2 | `orders.clear` | "No open orders" |

The design's first two branches (due-today with a cutoff, then overdue) are both
unreachable — no order on this platform carries an SLA. A test asserts the copy
matches no deadline vocabulary, so the line cannot drift into implying one.

### Messages
`messages.unread` → `messages.clear`. Typing branch gated off.

### Insights
| Priority | Key | Condition |
|---|---|---|
| 1 | `insights.collecting` | no prior period, **or** prior revenue was zero |
| 2 | `insights.flat` | move within ±`trendFlatBand` |
| 3 | `insights.trend` | ▲/▼ with percent, tone green/warn |

Threshold `HUB_THRESHOLDS.trendFlatBand = 0.0005`, matching `compareToPrior`'s own
dead band so the hub and the Insights screen call the same week flat. The two
`collecting` cases are separate refusals from the owner: a seller who did not
exist last week must not see ▲100%, and a seller whose last week was zero must not
see a percentage of zero.

### Payments
`payments.wallet` → "Ad wallet {balanceLabel}", or **nothing**. See §6.

### Events
`eventsStateLine()` returns `null` unconditionally. Kept as a named resolver so
the fallback path is the same code path every other card uses, and wiring a loader
later is one call site rather than a new branch in the screen.

### Verification
| Priority | Key | Status |
|---|---|---|
| 1 | `verification.rejected` | `rejected`, `suspended` — critical, blink |
| 2 | `verification.needsInfo` | `needs_more_info` — warn, blink |
| 3 | `verification.inReview` | `submitted`, `in_review`, `appealed` — review-blue, blink |
| 4 | `verification.approved` | `approved` — green |
| 5 | `verification.draft` / `verification.notStarted` | muted |

No review ETA. Nothing in the backend exposes one, and "usually 2–3 days" from a
frontend constant is a promise the platform has not made.

### The universal rule
**Every resolver returns `null` when its source is absent**, and a `null` line
means the card keeps its static subtitle. Not an error card, not a zero, not a
guess. This is tested for all ten resolvers in one assertion, and it is why the
screen needs no special case for a failing source.

`ordersStateLine(null)` → `null` but `ordersStateLine(0)` → "No open orders" is
tested explicitly: an absent value and a zero one are different claims, and
collapsing them would tell a seller with a broken connection that they have
nothing to ship.

---

## 4. Owner change requests made (consume-never-derive, enforced)

Three derivations were found living inside an owner's *screen* rather than its
`api/` module. In each case the fix was to move it to the owner and have both read
it — never to copy it into the hub.

| Export added | Owner module | Why |
|---|---|---|
| `ordersAwaitingSeller(orders)` | `api/ordersDashboard` | "awaiting the seller" is a status-set decision; two copies would drift the moment a status is added. |
| `storeHealthCounts(rows)` | `api/storeDashboard` | Low/out/active thresholds belong with the rows they classify. |
| `insightsRevenueMajor(summary)` | `api/insightsDashboard` | The hub needed a money figure. The module returns numbers, not strings, and the Insights screen does `minor / 100` itself. Rather than replicate that arithmetic, the owner now makes both decisions a consumer could get wrong — *which* field is "sales", and what unit it is in — once. |

---

## 5. Freshness and performance

**No loading state.** `BusinessHubScreen` has no `loading` flag, no full-screen
spinner and no aggregate model. `startHubBindings()` uses `void` per binding
rather than `Promise.all`, deliberately: there is no promise that resolves "when
the hub is ready", because inventing one would recreate the all-or-nothing load
this design removes.

**Instant first paint.** `verificationBinding` and `profileBinding` hydrate from
their owners' caches before the network. The hydrate path refuses to overwrite a
network answer that landed first — tested, because that race is what makes warm
launch dangerous. A missing or corrupt cache is treated as "no paint yet", never
as an error.

**Refresh triggers:** mount, screen focus, pull-to-refresh, and the owners'
existing `registerSyncInvalidation` subsystems. **No new polling loop was added.**
`adsBinding` has no sync subsystem — no server event says "your campaign
changed" — so it refreshes on focus and pull only. That is named in the code
rather than left to be discovered as a bug.

**Per-card re-render isolation, structurally.** The mission asks that bindings not
cascade. This is not enforced by discipline; it is enforced by composition:

- `BusinessHubScreen` (the root) calls `useHubBinding` **zero times**.
- The header, the strip, the offline note and each of the eleven cards are
  separate components, each subscribing to only its own source.
- A card that never reads another binding *cannot* re-render when it changes.

The one place to be careful is the screen file itself — adding a `useHubBinding`
call to `BusinessHubScreen` would quietly re-couple all eleven cards to one
source. The file header says so in as many words.

Tested at the binding layer, where the guarantee actually lives: refreshing one
binding notifies its own listeners and leaves another binding's listener at zero
calls; `getSnapshot()` returns a stable object between reads; and the publish
guard means an equal snapshot emits nothing at all, so a focus refresh that
changes nothing repaints nothing.

**One deliberate exception**, documented at the call site: `HubOfflineNote` reads
two bindings, because "as of {time}" is genuinely cross-cutting. It renders no
card state, so the coupling costs a one-line repaint.

**Warm-launch time-to-interactive: NOT MEASURED.** This requires the on-device
run listed in §11 as outstanding. What can be stated from the code is that the
screen has no code path that blocks first paint on a network call.

---

## 6. Staleness rules

`HUB_STALENESS_MS`, with `Infinity` written out explicitly for the non-critical
sources so that adding a binding forces a decision rather than inheriting a
default.

| Source | Window | Reasoning |
|---|---|---|
| `live` | 0 | A live session that ended two minutes ago is not "slightly stale", it is wrong. |
| `offers` | 5 min | Shorter than the smallest unit it displays (hours), so it can never show an hour that has already elapsed. |
| `orderDeadlines` | 5 min | Same, for ship-by pressure. |
| everything else | ∞ | A listing count from four minutes ago is still true. Degrading it would blank the screen for no gain. |

**The degradation, demonstrated.** A stale time-critical value degrades to its
**non-deadline fallback** — it does not disappear. `marketplaceStateLine` takes an
`offersStale` flag: fresh, it reads "3 offers · 1 expires in 5h"; stale, it reads
"3 offers". The count survives, because an offer that existed five minutes ago
almost certainly still exists; the countdown is dropped, because five minutes of
drift is enough to make a displayed hour wrong. Showing a wrong hour is the one
failure the seller cannot detect for themselves. Tested both ways.

**A small owner gap, recorded:** the verification cache stores no write time, so
it hydrates at `savedAt: 0`. `isStale` reads a zero as stale, which is the safe
direction, and verification's window is infinite so nothing downstream degrades.
Worth an owner fix eventually; harmless today.

---

## 7. Money and verification — the standing rule

Neither ever appears as mocked data, anywhere. Where unavailable, the surface
degrades to **silence**.

The hub performs **no money arithmetic and no money formatting decisions**:

- Payments takes `WalletSummary.balanceLabel` **verbatim** — the Advertising
  mission's own formatting of the server's `spendable_balance_cents`. The figure
  on the hub is character-for-character the one on the Advertising screen.
- The "Today's sales" cell takes `{amount, currency}` from `insightsRevenueMajor`
  and hands it to the app's shared `useFormatters().currency`.
- `paymentsStateLine(null)` returns `null`. Never a stale or fabricated zero —
  "$0.00" and "—" are different claims, and only the source knows which is true.

Verification likewise: no tick at all for a seller who never started (a greyed
tick reads as "failed" when the truth is "not started"), and no review ETA.

---

## 8. MOCK-DATA table

**Empty.** No value on this screen is mocked, estimated or synthesised. Every
figure comes from an owner module's real loader, and every design element without
a source is *absent*, not filled.

What is absent, and why, is the `HUB_DATA_GAPS` ledger — six entries, each naming
the exact backend work that would make it real:

| Design element | Surface | Backend work needed |
|---|---|---|
| Orders due today, overdue count, carrier cutoff | Orders card urgent treatment + "Ship today" cell | A per-order fulfillment SLA on the seller-orders payload |
| Open offer count and soonest expiry | Marketplace card + Offers cell | An offers endpoint behind the existing local state machine |
| Today's ad spend | Advertising card | A date range on `getAdAnalytics` — today's figures are lifetime |
| Campaign limited-by-budget phase | Advertising card | A delivery-phase field; backend collapses `limited` into `active` |
| Live typing indicator | Messages card | A hub-cheap presence channel, plus a product decision |
| Seller's own hosted events and live session | Events card + LiveNowBanner | A hosted-events loader on `api/eventsManager` (mission in flight) |

Money and verification are deliberately **not** on this table — per the standing
rule they degrade to silence rather than to a declared gap.

---

## 9. Feature flags

All `false`. Each is a named constant with a doc comment naming the backend work,
not an experiment toggle.

| Flag | Kills | Turns on when |
|---|---|---|
| `HUB_ORDER_DEADLINES` | Orders urgent treatment, "Ship today" label + hot cell | Orders carry an SLA |
| `HUB_MARKETPLACE_OFFERS` | Offer count, expiry clause, violet badge, Offers cell | An offers endpoint exists |
| `HUB_ADS_TODAY_SPEND` | "· $12.40 today" on Advertising | `getAdAnalytics` takes a date range |
| `HUB_ADS_LIMITED_STATE` | "Limited by budget" as a distinct state | Backend stops collapsing it into `active` |
| `HUB_PAYOUTS` | Failed payout, refund, next-payout-day branches | A payout contract exists |
| `HUB_MESSAGES_TYPING` | Live typing on Messages | A presence channel cheap enough for a front door |
| `HUB_EVENTS` | The whole Events card state line, and the live banner | A hosted-events loader lands |
| `HUB_VERIFICATION_ETA` | Review duration on Verification | `VerificationState` carries an estimate |

`HUB_VERIFICATION_ETA` is deliberately absent from `HUB_DATA_GAPS`, per the
verification rule above.

---

## 10. States covered

- **Warm launch** — cache hydrate then per-binding refresh; tested including the
  hydrate-loses-to-network race.
- **Cold / new seller** — every binding starts `idle` with no data and no error,
  so nothing is claimed before anything is known. An empty result is `ready`, not
  an error: a brand-new seller with zero listings reads "No listings yet", not
  "Couldn't refresh".
- **Partial availability** — one binding in `error` leaves the others `ready`;
  the failing card shows its static subtitle plus a refresh affordance. Never an
  error card, never blocking the other ten. Tested.
- **Offline** — a binding that already holds data **keeps it** when a later
  refresh fails, and `HubOfflineNote` renders "Showing your last update · {time}".
  Losing a good number because a poll failed is worse than showing it a bit old;
  the "as of" note is what tells the seller which it is.
- **All-clear** — every card falls to its calm branch ("No open orders", "All
  caught up", "Complete").
- **Verification transitions** — tick, context line and card move together from
  one status; tested as pairs.

## Accessibility

`cardAccessibilityLabel` reads title + subtitle + state line as **one element**,
so a screen-reader user hears the card the way a sighted user sees it. Urgency
arrives as a word (`"Urgent: …"`), not only as a colour. Badges announce a count
with its meaning — "2 orders awaiting you", never a bare "2". Tap targets are
≥44pt via `storeLight.size.tapTarget`. The grid reflows to one column at the
largest font scales (`hubGridColumns(fontScale)`), and state lines wrap rather
than truncate.

---

## 11. Deviations, stated plainly

**Eleven cards, not ten.** `businessOsHubSections()` yields eleven backed sections
because the Events mission split the hosted-events manager out from the activity
feed. The grid renders the registry rather than a hard-coded ten — a fixed count
would start silently dropping real sections the moment another one is backed.

**"To fulfil", not "Ship today".** The design's second strip cell asserts a
deadline, and no order on this platform carries one. The cell shows the real count
of open unfulfilled orders under an honest label, rather than "—" under a label
promising something better. When an SLA lands, the label and the hot variant come
back together.

**No live banner rendered.** `HUB_EVENTS` is off; `listScheduledLiveEvents` is
platform-wide discovery, so someone else's event would appear as the seller's own.
The slot is empty rather than filled with the wrong thing.

---

## 12. Verification status

| Check | Result |
|---|---|
| `tsc --noEmit` on this mission's files | Clean |
| `src/api/__tests__/businessHub.test.ts` | 57 tests passing (with the binding suite) |
| `src/core/__tests__/hubBindings.test.ts` | included above |
| Full Jest suite | **2511 passed / 0 failed**, 139 suites, 139 passing |
| `npm run i18n:validate` | OK — 11 locales, 923/923 keys, 100% coverage each |
| Screen recording | **NOT DONE** — see §13 |
| Warm-launch TTI measurement | **NOT DONE** — needs the device run |

The full suite was run in six chunks (`jest --listTests` split 6 ways, 25/23/23/23/22/23
files) rather than as one invocation, because the sandbox terminates any single
command that exceeds ~45s. Per-chunk results were 473, 253, 687, 296, 263 and 539
passing tests, zero failures in every chunk. Chunking changes nothing about
coverage — every one of the 139 discovered test files ran exactly once — but it is
recorded here because the run was not a single green line on one screen.

The four `i18n:validate` warnings (Arabic zero/one/two forms omitting the count;
Spanish, French and Portuguese omitting the advisory `many` form) pre-date this
mission and are unrelated to the hub's keys.

Known unrelated `tsc` errors in `screens/EventsManagerScreen.tsx`,
`core/mediaQualityFlags.ts`, `screens/ActivityRoute.tsx` and `api/paymentsHub.ts`
belong to a parallel agent working the same tree; they were filtered, not fixed,
and not edited.

---

## 13. Series closeout punch list

Cross-mission debt compiled from all ten reports. Nothing here is a blocker for
this mission; all of it is real.

### Product-owner decisions awaiting sign-off
1. **Card #9 naming** — the "Sections" card label was flagged in the Store
   mission and never resolved.
2. **Trade-dress token** — the navy/orange pair is hard-coded per surface rather
   than a named brand token.
3. **Escrow copy** — the Orders mission raised wording for held funds; still
   unratified.
4. **Unlock claims** — the Advertising mission's "unlock" language was never
   reviewed against what the tier actually grants.
5. **Events card route** — `EVENTS_CARD_CONFIG` carries a literal
   `"owner sign-off: PENDING"` comment.

### Backend work the frontend is waiting on
6. Per-order fulfillment SLA (unblocks four surfaces: the Orders urgent state,
   the "Ship today" cell and label, and two flags).
7. An offers endpoint (unblocks the Marketplace state line, the violet badge and
   the Offers cell).
8. A date range on `getAdAnalytics`.
9. A campaign delivery-phase field.
10. A payout contract (the largest single gap — the entire Payments design
    beyond the ad wallet).
11. A hosted-events loader on `api/eventsManager`.
12. A write timestamp on the verification cache.

### Engineering debt
13. **Two unbacked registry sections** — `customers` and `team` exist in
    `BUSINESS_OS_SECTIONS` with no route.
14. **`adsBinding` has no invalidation subsystem** — no server event says "your
    campaign changed", so it is focus/pull only.
15. **Route splits are now a pattern of three** (`SellerStore`,
    `BusinessOsAdvertising`, `BusinessOs`). Worth extracting once a fourth
    appears; not before.
16. **Classic screens retained** behind `mode: "classic"` on all three splits.
    They are dead weight once the rebuilds are confirmed, and each has a test
    importing it directly that will need to move.
17. **On-device screen recordings outstanding for Store, Advertising and Hub.**
    Blocked in this environment: the terminal is granted at a tier that blocks
    typing, so a simulator run cannot be driven from here.
