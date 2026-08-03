# Events + Activity — Completion Report

**Date:** 2026-08-02
**Branch:** codex/store-dashboard-live
**Scope:** Two native seller screens (Events, Activity) + unify every seller header bell onto one unread-count source.

---

## Overall status

| Gate | Result | Evidence |
|---|---|---|
| TypeScript (`tsc --noEmit -p tsconfig.json`) | **PASS** | exit 0, zero diagnostics |
| Jest — `unreadCounts` | **PASS** | 4/4 |
| Jest — `activityFeed` | **PASS** | 16/16 |
| Jest — `eventsManager` | **PASS** | 19/19 |
| On-device / recorded runtime | **NOT_TESTED (BLOCKED)** | No iOS/Android simulator in this sandbox — screen recordings cannot be produced here |

Test total: **39/39 passing.** The two build gates are green. The one thing I cannot deliver is a screen recording: there is no simulator or device attached to the sandbox, so every claim below is a static-analysis / test claim, not an observed-on-glass claim.

---

## What shipped

**Activity screen** (`src/screens/ActivityScreen.tsx`, routed via `src/screens/ActivityRoute.tsx`) — the destination of the bell in every seller header. Renders the unified feed: header with filter chips (All / Social / Marketplace / Orders / System) carrying live unread counts, Mark-all-read, day-grouped sections (New / Today / Yesterday / dated), notification rows with actor avatar + domain mini-badge or a system type-circle, plain-language sentence, `"3h ago · Marketplace"` suffix, LIVE-NOW state, unread blue edge + tint + dot, up to two offer-state-aware inline actions, row-tap deep-link + mark-read, offline notice, empty state.

**Events screen** (`src/screens/EventsManagerScreen.tsx`) — the pre-existing screen from task #212; this session I corrected its sync wiring (see Deviations).

---

## Bell consolidation — one number, one source

Every seller header bell now reads the single `UnreadCountStore` (`src/core/unreadCounts.ts`), and its wiring is initialized exactly once.

| Header | Route on tap | Count source | Change made |
|---|---|---|---|
| Store dashboard | `BusinessOsActivity` | `useBellCount()` | **This mission** — was `→ Notifications`, count was `kpis.openOrders` (double-counted orders) |
| Marketplace manager | `BusinessOsActivity` | `useBellCount()` | **This mission** — was `→ Notifications`, count was hard-coded `0` |
| Business Hub | `BusinessOsActivity` | `useUnreadCounts().bellCount` | Already consistent with the store |
| Insights | — (bell hidden) | — | `hideNotifications` — no bell rendered, no change needed |
| Commerce inbox | n/a | n/a | Its `onNotifications` is an **InboxToolsGrid settings button** (→ `NotificationSettings`), not the header bell — intentionally separate |

**Single-init:** `initUnreadCountSync()` is called once in `AppNavigator.tsx`, inside the same app-level effect that runs `startNativeEventSync`, and is torn down in that effect's cleanup. The store is opt-in, so importing it never triggers network; only this one call wires the `notifications` + `activity` invalidations to a refresh.

**Bell definition:** the bell shows `bellCount` = `alertUnreadCount` (notifications only). Messages are counted separately and badged on the Messages surface, by design — a seller glancing at the bell should not have chat unread folded into it.

---

## Feed aggregation approach (documented client rule)

There is no backend unified typed feed today — that is the #1 MOCK-DATA gap. The Activity screen fetches `listNotifications({ limit: 100 })` and derives the feed through the pure layer in `src/api/activityFeed.ts`:

- `toFeedNotification` normalizes each notification to a typed row (domain, actor vs. system, sentence, target, live, unread).
- `aggregateFeed` collapses same-subject social rows within a **6-hour rolling window** (documented client rule; a server-defined window would replace it).
- `groupFeedByDay` splits into New / Today / Yesterday / dated sections using the persisted last-visit timestamp.
- `inlineActionsFor` returns at most two actions and is offer-state-aware: a deleted subject yields no actions and a graceful landing target; live rows yield "Open live"; offer actions appear only with real offer context.

Every derivation is unit-tested (16 cases) rather than asserted by eye.

---

## MOCK-DATA ledger

**Activity** (`ACTIVITY_MOCK_DATA_GAPS`, 4):

| Field | Gated by |
|---|---|
| Unified notification feed (types/read-state/aggregation) | **TOP PRIORITY** — client-synthesized until a backend feed exists |
| Cursor pagination | limit-based fetch today (no cursor endpoint) |
| Offer amount on offer notifications | `MARKETPLACE_OFFERS_ENABLED` — inline offer actions honour real state when present |
| Aggregation window length | client rule (`aggregateFeed` windowMs = 6h) |

**Events** (`EVENTS_MOCK_DATA_GAPS`, 6):

| Field | Gated by |
|---|---|
| Hosted-event model (lifecycle/capacity/tickets/venue) | real events render; deterministic samples behind `EXPO_PUBLIC_EVENTS_MOCK` |
| RSVP / attendee identities + check-in | shown only when the event carries real attendees |
| Live orders-in-last-10-min stat | `EXPO_PUBLIC_EVENTS_LIVE_STATS` (banner shows no order number until backed) |
| Promoted-event reach / follows | read from linked campaign impressions; omitted when absent |
| Attributed sales on past-event results | `EXPO_PUBLIC_EVENTS_ATTRIBUTION` (metric withheld as "—" until backed) |
| Offer/ticket TTL + waitlist | waitlist only shown if the platform exposes one (it does not yet) |

No fabricated numbers render: where the backend can't back a figure, the UI withholds it (shows "—") rather than inventing one.

---

## Feature flags

`MARKETPLACE_OFFERS_ENABLED`, `EXPO_PUBLIC_EVENTS_MOCK`, `EXPO_PUBLIC_EVENTS_LIVE_STATS`, `EXPO_PUBLIC_EVENTS_ATTRIBUTION`, and the card-#9 naming token `EVENTS_CARD_CONFIG`.

---

## Card-#9 decision

Card #9 remains **hosted Events** (config token `EVENTS_CARD_CONFIG`); the activity feed lives behind the bell. Decision recorded (task #208). **Owner sign-off: PENDING.**

---

## Deviations from the brief

1. **Events sync subsystems corrected.** `EventsManagerScreen` registered for sync subsystems `"events"`, `"live"`, `"advertising"` — none of which exist in the `NativeSyncSubsystem` union. This was both a `tsc` error *and* dead wiring: nothing in the sync backend ever tags those names, so those handlers would never fire. I repointed the screen to the real subsystems that carry the same signals — `orders` (a live sale settles as an order), `marketplace` (a promoted listing changes), and `activity` + `notifications` (an RSVP / reminder lands as a notification) — and updated the comment to explain why.

2. **Transient `paymentsHub.ts` errors.** An early `tsc` run reported two errors in `src/api/paymentsHub.ts` (advertising wallet code, outside this mission). They did not reproduce on the clean run once the file settled — it was being edited concurrently. Final `tsc` is zero-error.

---

## Files changed this session

- `src/navigation/AppNavigator.tsx` — import + one-time `initUnreadCountSync()` wire-up and teardown.
- `src/screens/StoreDashboardScreen.tsx` — bell → `BusinessOsActivity`, count → `useBellCount()`.
- `src/screens/MarketplaceManagerScreen.tsx` — bell → `BusinessOsActivity`, count → `useBellCount()`.
- `src/screens/EventsManagerScreen.tsx` — sync-subsystem correction.
- `src/screens/ActivityScreen.tsx`, `src/screens/ActivityRoute.tsx` — the Activity feed screen + route (created within this mission).

Supporting layers built earlier in the mission and relied on here: `src/api/activityFeed.ts`, `src/core/unreadCounts.ts`, `src/api/eventsManager.ts`, the shared `components/events` + `components/activity` sets, and the `BusinessOsEvents` / `BusinessOsActivity` route registrations.

---

## Honest gaps

- **No runtime proof.** tsc + jest are green, but I have not seen either screen render — no simulator here. Before shipping, run both on device to confirm the bell number matches Activity's total, deep-links land, and the live/countdown states animate.
- The unified feed is client-synthesized; the real fix is a backend typed feed (gap #1).
- Card-#9 naming still needs owner sign-off.
