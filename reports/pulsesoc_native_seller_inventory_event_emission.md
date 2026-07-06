# PulseSoc Native Seller Inventory Event Emission Hardening

Date: 2026-07-06

## Scope

This pass eliminated the highest-value silent mutation gap identified by the native event producer coverage audit: marketplace seller inventory and listing lifecycle mutations.

No UI was added. The production WebView marketplace payloads and routes remain compatible. The backend remains the source of truth.

## Hardened Event Emitters

The backend now emits standardized cursor-visible seller inventory events for:

- `seller_application_submitted`
- `seller_application_changed`
- `seller_listing_created`
- `seller_listing_updated`
- `seller_listing_paused`
- `seller_listing_resumed`
- `seller_listing_deleted`
- `seller_listing_review_changed`

The shared helper is `pulse_emit_marketplace_inventory_event(...)`.

## Standard Event Envelope

Each seller inventory event flows through `notify_user(...)` and is visible through `/api/pulse/sync/events`.

Required normalized metadata:

- `event_type`
- `entity_type`
- `entity_id`
- `actor_id`
- `timestamp`
- `sync_cursor_key`

Safe marketplace metadata:

- `domain: marketplace`
- `category: seller_inventory`
- `listing_id`
- `seller_user_id`
- `status`
- `approval_status`
- `title`
- `invalidates`

## Event Visibility Through Sync Cursor

Event visibility through sync cursor is validated by the seeded audit.

Seeded audit coverage validates:

- seller application submit emits `seller_application_submitted`
- listing create emits `seller_listing_created`
- listing update emits `seller_listing_updated`
- listing pause emits `seller_listing_paused`
- listing resume emits `seller_listing_resumed`
- listing soft delete emits `seller_listing_deleted`
- all emitted events are returned by `/api/pulse/sync/events`
- all emitted events include normalized metadata
- listing lifecycle events invalidate `orders` where buyer order state may be affected

Admin review paths are statically covered:

- merchant application review emits `seller_application_changed`
- marketplace listing review emits `seller_listing_review_changed`

## Activity/Marketplace/Seller Store Consistency Impact

Activity/Marketplace/Seller Store consistency impact is now explicit through invalidation metadata.

The native polling cursor can now invalidate:

- Activity Inbox
- Notifications
- Marketplace
- Seller Store / Seller Inventory
- Buyer Orders where relevant

This means seller listing lifecycle changes no longer rely only on a full screen refresh or manual navigation to converge.

## Seller inventory event coverage %

Seller inventory lifecycle event coverage is now estimated at 95%.

Covered:

- seller application submit
- seller application review/change
- listing create
- listing edit
- pause
- resume
- soft delete
- review status change

Not fully proven:

- physical multi-device cursor delivery
- real provider APNs/FCM delivery for these event types
- duplicate webhook/idempotency behavior for future provider-driven marketplace events

## Remaining silent mutation paths

Known remaining non-seller-inventory event risks:

- marketplace save/report mutations need a dedicated pass if Activity wants to surface those actions
- checkout blocked/failure states before Stripe handoff remain partially event-covered
- message seen/delete/report cursor mirroring remains incomplete
- call lifecycle cursor mirroring remains incomplete
- safety block/mute/report/appeal state changes remain partially event-covered
- verification request/appeal details remain partially event-covered

## Event producer coverage %

Estimated system-wide event producer coverage after this pass: 76%.

Previous estimate: 72%.

The increase is intentionally modest because this pass fixed one high-impact subsystem, not the entire event graph.

## Critical production risk gaps

- Cursor-visible events now exist for seller inventory, but large-scale replay and duplicate-event idempotency still need broader event-system validation.
- Physical provider push delivery still needs APNs/FCM device QA.
- Marketplace commerce state is structurally consistent, but payment/refund/dispute event emissions still need a separate producer-hardening pass.

## ONE highest-impact fix ONLY

Payment and checkout failure event emission hardening.

Reason:

- Seller inventory mutations are now cursor-visible.
- The next most important silent mutation risk is payment/order state, where stale state can create financial trust problems.
- Payment/order events affect Buyer Orders, Seller Inventory, Activity Inbox, Notifications, and Marketplace listing status.
