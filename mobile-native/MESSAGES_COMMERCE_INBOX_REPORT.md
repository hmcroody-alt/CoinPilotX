# Messages → Commerce Inbox — Completion Report

Rebuild of the Business "Sections" card #6 (Messages) as a commerce inbox: every
row is about a money object (offer, order, pickup, listing question, completed
sale), surfaced via a context chip so a seller can triage money-relevant threads
at a glance. Scope was the **inbox list screen + integration points only** — the
thread view is a separate follow-up (needs documented below).

## Status: PASS (build gates), with declared deviations

| Gate | Result |
| --- | --- |
| `tsc --noEmit` (whole app) | PASS — clean |
| Jest: `commerceInbox.test.ts` (new) | PASS — 16/16 |
| Jest: `businessOs.test.ts` (updated) | PASS |
| Jest regression (api + components) | PASS — 400/400, 29 suites |
| Screen recording | NOT_TESTED — no simulator/render harness in this sandbox (same deviation as Orders/Advertising) |

## What was built

**Screen + route (strangler split, mirrors OrdersRoute):**
- `src/screens/CommerceInboxScreen.tsx` — fixed navy header + reply-time strip +
  filter chips; virtualized `FlatList` of `ConversationRow` with the expiry banner
  and offline note as `ListHeaderComponent`, the tools grid as
  `ListFooterComponent`, and the loading/empty/filter-empty/error states as
  `ListEmptyComponent`; pull-to-refresh; live `subscribeConversationUpdates`
  in-place reorder; `registerSyncInvalidation("messenger"/"marketplace")`;
  after-render batched chip resolution merged by id; optimistic read-clear on row
  press; per-session filter persistence via AsyncStorage; client-side search over
  title + snippet + chip line.
- `src/screens/MessagesRoute.tsx` — thin router.
- `BusinessOsMessages` route registered in `navigation/types.ts` + `AppNavigator.tsx`
  (own header, `headerShown: false`).
- `api/businessOs.ts` card #6 repointed from the `Messenger` **tab** to the
  `BusinessOsMessages` **stack** screen (dropped `tab: true`). The old Messenger
  tab is untouched.

**Shared components (prior task, reused verbatim):** ConversationRow, ContextChip
(5 variants), TypingIndicator, PresenceDot, InboxAvatar, ReplyStatsStrip,
AwayModeSwitchTile, MessagesHeader, FilterChips, ExpiryBanner, InboxToolsGrid,
MessagesStates.

**Data layer (`api/commerceInbox.ts`):** unified row model, the reusable
`ContextChipData` contract + `buildContextChip`, the batched/cached
`resolveContextChips`, filter counts/matching, reply stat, expiry banner
derivation (reads Marketplace offer state — no second clock), tools model.

## Context-chip contract + batched resolution (demonstrated by tests)

- One chip per row: `icon + single ellipsizing line`, taps deep-link to the
  **object** (`MarketplaceDetail` / `BuyerOrderDetail`), never the thread.
- `resolveContextChips` is batched (one call per visible set), cached (a resolved
  id is never re-resolved), and never on the row-render path — rows render first,
  chips fill in. It returns **only** ids that resolved to a link.
- No linked object → **no chip**. With the mock flag off and no real association,
  `resolveContextChips` returns an empty map (test-pinned).
- `ContextChipData` is React/navigation-free so the thread-view pinned card can
  reuse it verbatim.

## States

Loading (6-row skeleton), Empty inbox, Empty filter (per-filter copy), Error
(inline retry), Offline (cached + honest stale note). Away indicator lives in the
tools grid tile. All reachable from the screen's `ListEmptyComponent` / header.

## Feature flags (all off by default)

| Flag | Effect when on |
| --- | --- |
| `EXPO_PUBLIC_MESSAGES_TYPING` | Live typing dots (else static "typing…" for AT only) |
| `EXPO_PUBLIC_MESSAGES_PRESENCE` | Presence dot on rows |
| `EXPO_PUBLIC_MESSAGES_REALTIME` | Row-reorder animation (list still updates without it) |
| `EXPO_PUBLIC_MESSAGES_MOCK_CHIPS` | Deterministic mock commerce associations (design review) |
| `EXPO_PUBLIC_MESSAGES_AWAY` | Away/auto-reply tile interactive (optimistic-local) |
| `EXPO_PUBLIC_MESSAGES_REPLY_BADGE` | Fast-responder incentive framing on the reply stat |

## Real-time mechanism (statement)

**No websocket, no new socket library.** `subscribeConversationUpdates` is an
in-process listener fired when any thread writes the local cache; the screen rides
it to upsert + reorder the touched conversation to the top **in place** (no reload,
no scroll jump). `registerSyncInvalidation("messenger"/"marketplace")` and
pull-to-refresh are the manual refresh paths. Typing/presence are conversation
fields with no live push, hence flag-gated off — the default inbox is a correct
pull-to-refresh list.

## MOCK-DATA table

Every field with no live backend source (mirrors `INBOX_MOCK_DATA_GAPS`, length
locked by test = 8):

| Field | Backend work needed | Gated by |
| --- | --- | --- |
| conversation → offer/order/listing association (the context chip) | Server-side join (offer_id/order_id/listing_id on conversation, or a resolver endpoint) | real association always renders; mock behind `MESSAGES_MOCK_CHIPS` |
| avg reply time | Per-seller median first-response latency on the inbox payload | shown only when a real stat is present |
| fast-responder badge / ranking rule | Define the badge threshold rule | `MESSAGES_REPLY_BADGE` (off — no rule found) |
| away mode / auto-reply state + text | Persist away flag + template; apply server-side | `MESSAGES_AWAY` (optimistic-local only) |
| saved-reply templates count | Store saved replies per seller; return count | count hidden when unknown |
| spam / blocked filtered counts | Expose spam + blocked thread counts | count hidden when unknown |
| starred / archived state | Persist per-conversation + return on list | best-effort from fields; absent = filter empty honestly |
| offer expiry TTL (72h) | Confirm real TTL once offers backend exists | `MARKETPLACE_OFFERS_ENABLED` (banner dark until then) |

The expiry banner is **dark by default**: `deriveExpiryBanner` is gated on
`MARKETPLACE_OFFERS_ENABLED` (false), test-pinned to return null even for an offer
minutes from expiry. It collects offers from resolved chip links, so flipping the
flag lights it with zero further wiring.

## Navigation wiring

- Row tap → `Chat` (existing thread) with conversationId/title/avatar/presence;
  unread cleared optimistically before navigating.
- Compose → `NewChat` (sellers can initiate → compose shown).
- Chip tap → `chip.target` (`MarketplaceDetail` / `BuyerOrderDetail`); null target = inert.
- Banner "Open conversation ›" → `Chat` by conversationId.
- Tools: Spam & blocked → `BlockedUsers` (exists); Notifications →
  `NotificationSettings` (exists); Saved replies → honest no-op (no manager route
  exists — declared gap); Away → inline optimistic toggle (flag-gated).

## Thread-view follow-up (what the next mission needs)

1. **Pinned context card** reusing `ContextChipData` verbatim — the contract is
   already navigation-free for exactly this.
2. **Inline offer actions** (accept/counter/decline) must read + write the same
   Marketplace offer state machine the chip/banner read; no second expiry clock.
3. **Auto-reply rendering** once `MESSAGES_AWAY` is backed (render the applied
   template inline, distinct from human replies).
4. Row tap already lands on `Chat` with the inbox in the back stack, so notification
   deep links to a thread keep the inbox reachable via back.

## Deviations & open questions

- **No `messages-live.html`** visual source of truth exists in the repo (same as
  the Orders mission); built from the written spec.
- **No screen recording** — no render/simulator harness in this sandbox. Build
  gates (tsc + jest) are the evidence here.
- **72h offer TTL is proposed, not discovered** — no TTL constant existed to
  inherit (carried over from marketplaceOffers). Open question for product.
- **Saved-replies** has no destination route; the tile is an honest no-op rather
  than a crash.
