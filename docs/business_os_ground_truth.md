# Business OS — Ground Truth

A corrected inventory, written against the source rather than against screenshots.

The mission document titled *"PulseSoc Business OS — Deep Mission Review and Corrected Expansion"* asks for a rebuild ordered around a specific stated priority, and grounds that ordering in a set of observations taken from the running app. Several of those observations are accurate. One of them — the one the document names as blocking everything else — is not, and because it gates the rest of the plan, correcting it changes what should be built first.

This document exists because a module existing is not evidence a feature works, and equally, a screenshot showing a defect is not evidence the defect is unfixed. Both directions of error appear below. Every claim carries a `file:line` citation and was opened and read directly; nothing here rests on a summary.

## The correction that changes the plan

The mission document's stated top priority is:

> remove commerce threads from PulseSoc social Messages and establish a dedicated Commerce Inbox before building further order, offer, return, and advertising workflows.

Both halves of that sentence describe work that has already been done, and the observation behind it is a misread of which screen was being looked at.

The Commerce Inbox exists as its own screen. `mobile-native/src/screens/CommerceInboxScreen.tsx` is a full implementation, and `mobile-native/src/screens/MessagesRoute.tsx:16-18` renders it directly. That route wrapper documents its own purpose at `:1-6`: the Business "Messages" card and message deep links point at the commerce inbox, deliberately, "so the old Messenger tab stays intact while the Business surface uses the new inbox."

That is the screen the mission document screenshotted. It is labelled "Messages" in the Business surface, it shows Offers and Orders chips because `LEGACY_INBOX_FILTERS` at `mobile-native/src/api/commerceInbox.ts:605-612` lists them, and it is not the social inbox. The social inbox is `MessengerScreen.tsx`, whose filter union at `:35` is `all | direct | groups | rooms | ai | unread` — no offers, no orders, no commerce of any kind.

The separation is real below the UI too. `mobile-native/src/api/conversationDomain.ts` defines a five-value `conversation_domain` discriminator derived at the read boundary, and `commerceInbox.ts:150` records the derivation order: explicit field, then conversation type, then commerce association, then a SOCIAL fallback. `pulse_communications_v2/service.py:20` constrains conversation types to `direct`, `group`, `room`, and `community_channel`, enforced at `:1070`.

So the domain model, the storage partition, the scoped queries and the dedicated screen all ship today. What does not ship is the *switch*: `inboxFilterRail()` at `commerceInbox.ts:614-615` returns the commerce-split rail only when `conversationSplitEnabled()` is true, and that flag defaults off. The Offers and Orders chips the mission document objected to are the legacy rail showing because the replacement is gated. This is a rollout decision, not a build.

Since the mission document names this as the gate on all order, offer, return and advertising work, and the gate is a config value rather than a program of work, that work is not blocked.

## The systemic pattern the screenshots could not show

The commerce split is not an isolated case. At least seven completed pieces of engineering — several of them fixing defects the mission document names by name — are finished, tested, and shipping disabled behind build-time flags that default to off:

| Flag | What it turns on | Reader |
| --- | --- | --- |
| `EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT` | Commerce/social inbox rail split | `commerceInbox.ts:575` |
| `EXPO_PUBLIC_STORE_READINESS` | Real store-readiness ladder + checklist | `storeDashboard.ts:469` |
| `EXPO_PUBLIC_ADS_POST_MODE` | Honest ad post mode instead of mock promotions | `adsDashboard.ts:63` |
| ~~`EXPO_PUBLIC_ACCOUNT_NAME_FIRST`~~ | Business name before internal account id | **deleted — now unconditional** |
| `EXPO_PUBLIC_INSIGHTS_ERROR_CAUSES` | Insights failure taxonomy | `insightsDashboard.ts:545` |
| ~~`EXPO_PUBLIC_STATE_LANGUAGE`~~ | Unified state/absence vocabulary | **deleted — now unconditional** |
| `EXPO_PUBLIC_MARKETPLACE_LOCATION_HONESTY` | Truthful location labelling | `marketplaceScreen.ts:149` |

`mobile-native/src/core/envFlag.ts:29-33` states the posture explicitly: an unset variable resolves to false, "which is what keeps every gate off in a default build." `docs/business_os/FLAG_REGISTRY.md` is the inventory and confirms it.

The practical consequence is that an audit conducted by looking at the running app will report these features as missing, will be correct about what is on screen, and will be wrong about what needs to be written. Anyone planning the next phase should first decide which to enable, then re-screenshot, then plan — in that order. Doing it the other way round produces a plan to rebuild things that already exist.

**Two of the seven have since been resolved, by deleting the flag rather than setting it.** Phase 1 of the Advertising OS mission removed `EXPO_PUBLIC_ACCOUNT_NAME_FIRST` and `EXPO_PUBLIC_STATE_LANGUAGE`; both corrections are now what every build renders. The reasoning generalises to the five that remain: the value of a flag is the ability to turn a thing off, and nobody wanted the em dash or the exposed account id turned on. Keeping the switch only preserved the possibility of shipping the defect, which is exactly what happened for the life of both flags.

## The four money bugs

These are not in the mission document. They are worse than anything in it, and they are the reason the audit was worth doing.

### 1. The overdraft guard is a no-op on Postgres

`services/business_os/ledger/ledger.py:59-67` — `_begin()` takes a write lock only when the engine is SQLite:

```python
def _begin(conn) -> None:
    if db.ENGINE_NAME == "sqlite":
        try:
            conn.isolation_level = None
        except Exception:
            pass
        conn.execute("BEGIN IMMEDIATE")
```

The insufficient-funds check at `:472-480` then reads the balance with `get_balance(source, currency, conn=conn)`, which at `:219-234` is an unlocked `SELECT balance_cents FROM ledger_balances` — no `FOR UPDATE`, no advisory lock, no conditional write.

On SQLite, `BEGIN IMMEDIATE` serializes writers and the guard holds. On Postgres under READ COMMITTED, `_begin()` does nothing at all, two concurrent posters read the same pre-debit balance, both pass the check, and both post. The account goes negative by up to the smaller of the two debits.

A guard that is correct only on the development engine is not a guard. Any test suite exercising this on SQLite passes, and passes for a reason that disappears in production.

### 2. Marketplace refunds are not idempotent

`services/business_os/marketplace/refunds.py:90-93`:

```python
rid = "mktr_" + uuid.uuid4().hex
try:
    txn = _ledger.post_entry(
        idempotency_key=f"mkt_refund:{rid}",
```

The identifier is generated fresh inside the call, so every retry produces a new key and the ledger — which is correctly keyed and would otherwise deduplicate — sees a distinct operation and posts again. The signature at `:56-57` accepts no idempotency parameter, so a caller cannot supply one even knowing this.

The correct pattern already exists in the same codebase. `services/business_os/advertising/funding.py:489` derives its key with `_ledger_key(_RESERVE, key)` from a caller-supplied reservation key, which survives retries. Marketplace refunds should adopt it.

### 3. Stripe refunds double-count

`services/business_os/payments/stripe_ledger_handler.py:158` posts `obj.get("amount_refunded")` with the idempotency key `f"stripe:{event_id}:refund"` at `:165`.

Stripe's `amount_refunded` is cumulative for the charge, not the amount of the individual refund. `_REFUND_EVENTS` at `:48-50` includes `charge.refunded`, which fires again on each partial refund with a new event id and a larger cumulative total. Two partial refunds of $5 and $3 produce one event reporting 500 and a second reporting 800; both have distinct event ids, so per-event idempotency correctly admits both, and $13 leaves the ledger against an $8 refund.

The idempotency here is right. The field is wrong. It needs the per-refund delta, not the running total.

### 4. Payment capture is not atomic and has no repair path

`services/business_os/marketplace/orders.py:241-317` — `pay_order`. The guarded inventory decrement at `:267-271` is correct and race-safe:

```sql
UPDATE business_os_mkt_products SET inventory_qty = inventory_qty - ?
WHERE product_id = ? AND inventory_qty IS NOT NULL AND inventory_qty >= ?
```

with `rowcount == 0` raising `insufficient_inventory` at `:272-279`. But the inventory and status commit happens only `if owned` at `:285-286`, while the ledger capture posts on a different connection at `:290`, and the failure compensation at `:302-308` always runs on a fresh connection `c2`. A caller passing its own connection gets an uncommitted decrement paired with a committed ledger post, and the compensation path can credit back inventory that was never committed as debited.

Nothing reconciles this afterwards. There is no repair job, no drift detector, no invariant check between `business_os_mkt_orders` and the ledger.

## Where the mission document is right

**Store bypasses seller eligibility entirely.** `services/business_os/store/service.py:172-186` — `_require_biz_permission` resolves `biz_svc._effective_role` and checks it against a permission rank, and that is all it does. The file contains zero references to entitlements, seller lifecycle, approval, or eligibility. Meanwhile `services/business_os/entitlements/service.py:225` is a well-built `has_entitlement` with a documented precedence ladder at `:13-25`, and `services/seller_lifecycle.py:48-62` defines ten seller states with a declarative actor-gated transition table at `:107-144`. Both exist. Store calls neither. Anyone with a staff role on a business can list products regardless of whether that business is approved to sell.

**There is no unified commerce entity graph.** Three separate product tables, no foreign keys between them, no shared identifier: `business_os_mkt_products` at `marketplace/schema.py:104`, `business_os_store_products` at `store/schema.py:76`, `business_os_ent_products` at `entitlements/schema.py:102`. A store product and a marketplace listing for the same physical item are unrelated rows. This is genuinely absent and genuinely needed.

**The unread badge leaks commerce into social.** `services/notification_service.py:1315-1348` sums unread across three sources — `pulse_conversation_participants` at `:1316-1325`, `comm_v2_participants` at `:1326-1335`, and a `private_messages` / `conversation_members` join at `:1336-1348`. None of the three carries a domain predicate. The `conversation_domain` discriminator that partitions the inbox is not consulted here at all, so a commerce message increments the social badge unconditionally, flag or no flag.

**Developer copy is still reaching users.** `mobile-native/src/screens/UndxActionCenterScreen.tsx:96` renders "Server-authoritative decisions, approvals, receipts, and Marketplace workflow state." as the page subtitle. There is a CI gate for exactly this at `mobile-native/src/__tests__/userFacingCopy.test.ts:36-52`, and its very first banned pattern is `/\bserver[- ]authoritative\b/i` — the gate is looking for this precise phrase and does not find it, because the scanner inspects quoted string literals (`:122`, `:139-148`) and this is a bare JSX text node. The gate needs to read text nodes; the string needs rewriting.

## Where the mission document is wrong or stale

The duplicate Payments navigation shell, the duplicate Payments error state, the clipped four-column cards, and the non-status-aware verification actions are all already fixed — the last one twice over. `mobile-native/src/api/verification.ts:221-323` computes status-aware actions client-side, and `services/dashboard_account_command_center.py:1197` now emits `"Review Verification"` for submitted, in-review, appealed and approved states rather than always saying `"Continue Verification"`. The comment at `VerificationCenterScreen.tsx:183-185`, which explains that the screen ignores the server field because that field is not status-aware, is now describing a server that no longer behaves that way. Stale explanatory comments are their own hazard: the next reader trusts them, and this one would send someone to fix a bug that is already gone.

The advertising hierarchy is described as missing. It is built and unreachable, which is a different problem with a different fix. `services/business_os/advertising/schema.py:102`, `:233` and `:282` define `business_os_ad_campaigns`, `business_os_ad_sets` and `business_os_ad_creatives` — a real three-level hierarchy. Searching `mobile-native/src` for any reference to ad sets or creatives returns nothing outside unrelated code. The mobile app ships `api/ads.ts` and `api/adsDashboard.ts` and neither reaches the ad-set or creative layer. The work is a client surface over an existing backend, not a schema design.

The "Ad account 8" defect was real and is now **fixed**. `AdsManagerScreen` used to render `{account.business_name || "Ad account"} · Ad account {account.id}`, making a database primary key the most prominent text in the row. `EXPO_PUBLIC_ACCOUNT_NAME_FIRST` addressed it and was off, so the defect shipped anyway. Phase 1 of the Advertising OS mission deleted the flag and made the correction unconditional: the row is now two lines — the business name, then `adAccountStanding()`'s standing line, which is one of `"Advertising account · Active"`, `"· Verification pending"`, `"· Restricted"` or `"· Not configured"`. The account number moved to the account details sub-page, where it belongs and where a seller can read it out to support. `adAccountStanding()` returns line and tone from a single switch so the status dot cannot contradict the words next to it, and a test asserts that no status can put a digit in that line.

## What should happen first

Not the commerce inbox. That is done and needs a flag flipped.

The ordering that follows from the evidence is: fix the four money bugs, because they are silent, they are in production, and three of the four lose real money rather than merely displaying it wrong. Then wire Store to the entitlements and seller-lifecycle services that already exist, because the authorization hole is one call site wide. Then decide the flag posture for the gated features and re-baseline the UI audit against a build with them on, because every screenshot-derived finding is unreliable until that happens. Then build the unified entity graph, which is the largest genuinely-absent item and the prerequisite for the end-to-end chain the mission document wants. Then the ad-set and creative client surface.

On the third of those: for two of the seven the decision has been taken and the answer was neither "on" nor "off" but "delete the switch". Where a flag guards a surface the backend cannot serve — the Payments six, the Orders pair, `ADS_POST_MODE` — off is correct and stays correct until an endpoint exists. Where it guards a correction to something already on screen, the flag is not protecting anyone; it is preserving the ability to ship the defect, and that ability was exercised for the whole life of both flags that have now gone. The five that remain should be sorted into those two piles before anything else is planned around them.

## Confidence

Every claim above was verified by opening the cited file and reading the cited lines during this audit. The four money bugs, the Store authorization gap, the three-catalog split, the three unscoped unread sources, the inbox routing, and the seven flag defaults were each checked individually rather than accepted from an intermediate report.

Two limits are worth stating. First, this audit reads code, not a running system; a defect that depends on deployment configuration or on data state will not appear here. Second, the classification of the mission document's remaining items as already-fixed rests on reading the current source, and the repository was being modified concurrently while this was written — the line numbers are accurate as of the commit this document accompanies and should be re-resolved by symbol name if they drift.
