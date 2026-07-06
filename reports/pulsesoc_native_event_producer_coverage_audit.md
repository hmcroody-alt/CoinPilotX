# PulseSoc Native Event Producer Coverage Audit

Date: 2026-07-06

## Scope

This audit scanned the current PulseSoc backend and native migration state for event production coverage feeding the native polling/cursor sync layer.

Validated event path:

1. backend state change
2. event producer
3. `pulse_notifications`
4. `GET /api/pulse/sync/events`
5. native `eventSync.ts`
6. screen cache invalidation and refresh

No UI, product feature, auth redesign, WebSocket/SSE, or new product domain was added.

## Shared Event Emission Normalization

The shared `notify_user(...)` emitter now writes standardized metadata into notification rows:

- `event_type`
- `entity_type`
- `entity_id`
- `actor_id`
- `timestamp`
- `sync_cursor_key`

This means existing `notify_user` producers immediately become cursor-visible standardized event producers without duplicating business logic across individual routes.

## Event Producer Mapping Audit

| Backend Area | Existing Producer Coverage | Cursor Visibility | Status |
| --- | --- | --- | --- |
| Notification events | `notify_user`, `notification_service`, `pulsesoc_notification_system` | High | Covered |
| Intelligence / crypto alerts | `alert_engine.dispatch_alert_event`, `notify_crypto_alert` | High | Covered |
| Feed social actions | `pulse_feed_engine` notification helpers | Medium-high | Covered/partial |
| Payment success | Stripe webhook `notify_user` calls for buyer/seller | Medium-high | Covered |
| Buyer order reads | server-authoritative order APIs | Medium | Reads covered, not every mutation emits |
| Marketplace listing create/update/pause/resume/delete | direct listing mutations | Low-medium | Gap |
| Seller application | direct seller mutation | Low | Gap |
| Marketplace report/save | direct mutation | Low | Gap |
| Messaging send/delete/seen | `pulse_emit_event` plus message notifications | Medium | Partial cursor visibility |
| Calls | communications engine notifications | Medium | Partial |
| Safety/report/block/mute | direct trust/safety mutations | Low-medium | Gap |
| Verification review/status | selected `notify_user` paths | Medium | Partial |
| Premium/entitlements | payment/entitlement APIs | Medium | Partial |

## Event Coverage Gaps

Critical actions that can still mutate state without guaranteed cursor-visible event envelopes:

1. Marketplace listing create, edit, pause, resume, and soft delete.
2. Marketplace seller application save/update.
3. Marketplace listing report/save actions.
4. Checkout blocked states before provider handoff, including Stripe-not-configured and payout-onboarding-required branches.
5. Message seen/delete/report actions that rely on realtime/local events but may not mirror to `pulse_notifications`.
6. Safety block, mute, report, appeal, and enforcement status changes.
7. Verification request, appeal, document handoff, and admin-review state changes.
8. Premium entitlement refreshes outside payment notification paths.
9. Calls active/ringing/ended state transitions that do not create notification rows.
10. Intelligence source/forecast/read-state changes outside alert delivery.

## Duplicate / Unsafe Producer Findings

- There are multiple event-producing systems: `notify_user`, `notification_service`, `pulsesoc_notification_system`, alert delivery, feed notifications, realtime message events, and command-center events.
- This is acceptable only if `pulse_notifications` remains the cursor-visible truth source for native sync.
- Risk remains where a subsystem emits a realtime/local event but not a cursor-visible notification event.
- The shared emitter now adds `sync_cursor_key`, but true idempotency still depends on producer-specific dedupe where retries can call the emitter more than once.

## Sync Integration Validation

Validated by script:

- `notify_user` writes standardized metadata.
- `/api/pulse/sync/events` exists and reads from `pulse_notifications`.
- Native `eventSync.ts` consumes `/api/pulse/sync/events`.
- The current cursor endpoint returns metadata used by native invalidation.

## SYSTEM STATE AUDIT

1. Event producer coverage completeness %

Estimated coverage: **72%**.

2. Missing event emitters (critical list)

- Marketplace seller inventory mutations.
- Marketplace seller application changes.
- Checkout blocked/failure states before Stripe.
- Message seen/delete/report cursor mirroring.
- Calls state transitions beyond notification routes.
- Safety block/mute/report/appeal state transitions.
- Verification request/review/appeal state transitions.
- Premium entitlement refresh outside payment success.
- Intelligence read/source/forecast state outside delivered alerts.

3. Duplicate / unsafe event producers

- `notify_user`, `notification_service`, and `pulsesoc_notification_system` can all create notification-style records.
- Message realtime events use `pulse_emit_event` and are not always cursor-visible.
- Alert delivery has its own alert event tables plus notification rows; duplicate user-facing events are possible if retry/idempotency is not enforced at the producer.

4. Systems not emitting events at all

No major system is completely without any event path, but several important mutation branches are silent for cursor sync:

- seller inventory controls
- marketplace report/save
- trust/safety control actions
- verification request/appeal details
- some call lifecycle changes

5. Sync pipeline integrity score

**78 / 100**.

6. Overall native migration %

Overall native migration: **85% foundation/parity coverage**, **80% system consistency confidence**, **64% release QA confidence**.

7. Critical production risk gaps

- Native sync can only converge for events that are emitted into or mapped to `pulse_notifications`.
- Silent marketplace/safety/verification/message/call mutations can still leave Activity Inbox and dependent screens stale until a full refresh.
- Event idempotency is not uniformly enforced across all producer families.
- Provider/device push remains release-gated.

8. ONE highest-impact fix ONLY

**Wire Marketplace Seller Inventory mutations into the standardized event emitter.**

## Recommended Next Native Feature

Recommended next feature/action: **Marketplace Seller Inventory Event Emission Hardening**.

Why this should come next:

- Seller inventory is one of the most complete native commerce subsystems and directly depends on real-time consistency.
- Listing create/update/pause/resume/delete changes affect Seller Store, Marketplace, Buyer Orders, Activity Inbox, and Notifications.
- These routes are currently among the clearest high-value silent mutation gaps.

Reusable existing PulseSoc code/API/database/business logic:

- existing marketplace seller/listing APIs
- `marketplace_listings`
- `marketplace_product_media`
- `marketplace_sellers`
- `pulse_notifications`
- `notify_user`
- `/api/pulse/sync/events`
- native `eventSync.ts`
- native Seller Store, Marketplace, Activity Inbox, and NativeMediaViewer components

What must be rebuilt natively:

- No new native UI is required for the next action.
- Existing native screens should only consume refreshed server state through the current sync invalidation layer.

Dependencies/blockers:

- Need scoped backend wiring for listing create/update/pause/resume/delete.
- Need seeded backend audit that confirms each mutation emits exactly one cursor-visible event per successful state change.

Risk level: medium-low.

Estimated complexity: low to medium.

Safest implementation plan:

1. Add a small backend helper around `notify_user` for marketplace listing events.
2. Emit events after successful seller listing create/update/pause/resume/delete.
3. Include stable `sync_cursor_key` values for idempotency review.
4. Validate with seeded backend tests and cursor endpoint checks.
5. Do not alter listing approval, payment, moderation, or WebView behavior.

## Why This Was The Highest-value Next Action For System Completion

The cursor system is now correct for events it can see. The highest system-completion risk is silent backend mutations that never enter the cursor-visible event stream. Auditing and normalizing producers is the necessary bridge between a working sync endpoint and full backend/native state convergence.
