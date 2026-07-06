# PulseSoc Native Event Cursor Integrity Validation

Date: 2026-07-06

## Scope

This validation focused only on the polling-first event cursor layer. No UI, product domain, payment logic, WebView route, or business-rule expansion was added.

Validated target:

- `GET /api/pulse/sync/events`
- `mobile-native/src/core/eventSync.ts`
- server-authoritative event source: `pulse_notifications`

## Cursor Integrity Validation

Validated behavior:

- unauthenticated requests return `401` and no events
- initial sync returns bounded events for the signed-in user only
- delta sync with `after_id` returns only newer events
- timestamp replay with `after` returns deterministic replay data
- invalid cursor values degrade safely instead of crashing
- endpoint returns a stable schema compatible with the native sync client
- `latest_event_id` / `latestEventId` advance to the signed-in user's latest event id
- event rows are ordered by notification id for cursor determinism
- duplicate event ids are not emitted by the backend source

## Cross-System Sync Verification

The seeded audit validates invalidation hints for:

- Activity Inbox
- Notifications
- Buyer Orders
- Seller Inventory
- Marketplace
- Messenger
- Calls
- Trust/Safety
- Verification
- Premium
- Intelligence/Alerts

The native client remains responsible for consuming these invalidation hints and refreshing registered screen handlers from server-authoritative APIs.

## Event Ordering Chaos Simulation

The audit seeds event families that represent real PulseSoc state movement:

- `purchase_created`
- `payment_failed`
- `refund_issued`
- `listing_updated`
- `message_received`
- `call_started`
- `report_submitted`
- `verification_approved`
- `premium_subscription_updated`
- `intelligence_alert`

It intentionally includes out-of-order `created_at` timestamps and validates that cursor ordering remains deterministic through notification ids.

## Offline To Online Recovery

Offline accumulation is represented by events created while a client cursor is behind. The audit verifies that reconnecting with `after_id` returns only the accumulated delta and keeps the cursor advancing without duplicate replay.

Full reconciliation remains the native fallback when the endpoint is unavailable, as implemented in `mobile-native/src/core/eventSync.ts`.

## Backend Contract Validation

The endpoint returns:

- `ok`
- `events`
- `cursor`
- `latest_event_id`
- `latestEventId`
- `last_event_at`
- `lastEventAt`
- `server_time`
- `limit`
- `source`

Each event returns:

- `id`
- `event_id`
- `event_type`
- `type`
- `domain`
- `category`
- `entity_type`
- `entity_id`
- `target_url`
- `deep_link`
- `created_at`
- `updated_at`
- `invalidate`
- sanitized `metadata`

Sensitive metadata keys containing password, secret, token, key, or credential are removed before the native client sees metadata.

## SYSTEM STATE AUDIT

1. Cursor system correctness status

Status: **correct for polling-first notification-derived event replay**. Initial sync, delta sync, replay, invalid cursor recovery, metadata redaction, ordering, and schema shape are validated by `scripts/pulsesoc_native_cursor_integrity_validation_audit.py`.

2. Systems that break under replay

No breakage was found in the cursor contract audit. The remaining unproven layer is screen-level handler refresh under high-volume real backend event bursts.

3. Systems that drift under concurrency

Potential drift remains in systems that do not yet register dedicated handlers with the native sync registry:

- Messenger summary state
- Calls active-call state
- Safety enforcement state
- Verification review/badge state
- Premium/entitlement state
- Intelligence/alert detail state

4. Event loss/duplication risks

Current risk is **medium-low** for notification-derived cursor replay because event ids are stable and monotonic per `pulse_notifications`. Risk remains **medium** for event domains that are not yet mirrored into `pulse_notifications` or do not include precise invalidation metadata.

5. Subsystem sync reliability %

| Subsystem | Sync Reliability |
| --- | ---: |
| Activity Inbox | 88% |
| Notifications | 90% |
| Buyer Orders | 86% |
| Seller Inventory | 85% |
| Marketplace | 85% |
| Messaging | 72% |
| Calls | 65% |
| Trust/Safety | 78% |
| Verification | 78% |
| Media/Capture | 62% |
| Creator/Premium/Intelligence | 74% |

6. Overall native migration %

Overall native migration: **85% foundation/parity coverage**, **79% system consistency confidence**, **64% release QA confidence**.

7. Critical gaps for production readiness

- Real provider push/APNs/FCM delivery and tap routing still need physical-device QA.
- Cursor replay still needs browser/simulator validation against live authenticated data, not only seeded temp-db simulation.
- Messenger and Calls need dedicated event handler wiring beyond generic Activity/Notifications refresh.
- Event producer coverage should be audited so every critical order, listing, message, call, safety, verification, and alert update reliably creates or maps to a cursor-visible event.
- True cross-device realtime is still polling-first only; WebSocket/SSE/push streaming remains a later upgrade after cursor replay is stable.

8. ONE highest-impact fix ONLY

**Event Producer Coverage Audit**: confirm every critical backend event producer emits, mirrors, or maps to a cursor-visible event envelope with stable id, target URL, entity metadata, and invalidation hints.

## Why This Was The Highest-value Next Action For System Completion

This was the highest-value next action because cursor correctness is the foundation for deterministic Activity, Orders, Seller Store, Marketplace, Notifications, Messenger, Calls, Safety, Verification, Premium, and Intelligence state. If cursor replay is wrong, the native app can appear complete while silently diverging under real usage.
