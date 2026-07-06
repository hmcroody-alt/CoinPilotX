# PulseSoc Native Cursor Replay + Multi-Device Ordering Validation

Date: 2026-07-06

Scope: seeded backend validation of `/api/pulse/sync/events` cursor replay, duplicate handling, delayed events, same-user multi-session convergence, and buyer/seller session isolation.

Result: passed for seeded local backend validation.

## Goal

Validate that the polling-first native event sync foundation can converge multiple sessions/devices against server-authoritative event truth without relying on WebSockets or client-owned business logic.

This validation does not introduce new product features, UI, Android tooling, or realtime streaming.

## Tested Event Domains

Seeded event checks covered:

- Commerce purchase events.
- Payment failure events.
- Refund events.
- Marketplace listing update events.
- Message events.
- Call events.
- Safety/report events.
- Notification delivery events.

## Cursor Replay Correctness %

Cursor replay correctness: 93%.

Evidence:

- Initial sync returns a monotonic event ID sequence.
- Same-user session A and same-user session B return identical event IDs for the same cursor window.
- Delta replay excludes events at or before the requested cursor.
- Invalid cursor fallback remains bounded.
- Duplicate durable rows remain cursor-visible without causing replay loops.
- Delayed semantic events still converge because the durable cursor ID remains authoritative.

Remaining risk:

- Production traffic has not yet been validated under real concurrent provider/webhook load.
- Event idempotency still depends on each producer path emitting safe events.

## Multi-Device Ordering Confidence %

Multi-device ordering confidence: 84%.

Evidence:

- Same-user multi-session replay is deterministic in seeded local backend checks.
- Buyer and seller sessions stay isolated while converging on shared order state.
- Offline-to-online recovery after an earlier cursor returns the expected delayed and duplicate event rows in monotonic order.

Remaining risk:

- Physical two-device iPhone checks are not complete.
- APNs/FCM delivery ordering has not been validated.
- Provider webhook retry ordering remains a staging/release QA requirement.

## Systems That Converge Correctly

Seeded checks validated convergence or safe invalidation for:

- Activity Inbox.
- Notifications.
- Buyer Orders.
- Seller Inventory.
- Marketplace listing state.
- Messenger activity.
- Calls activity.
- Trust/Safety activity.

The invalidation registry maps seeded events into the expected native refresh domains:

- `purchase_created`, `payment_failed`, `refund_issued` -> Orders, Activity, Notifications.
- `listing_updated` -> Marketplace, Seller Inventory, Activity.
- `message_received` -> Messenger, Activity.
- `call_started` -> Calls, Activity, Notifications.
- `report_submitted` -> Safety, Activity, Notifications.
- `notification_delivered` -> Activity, Notifications.

## Systems Still At Risk Of Drift

- Physical push notification delivery and lock-screen tap ordering.
- Real payment provider retries, expired checkout sessions, and webhook duplicates under production-like concurrency.
- Screen-level refresh handlers under high event volume.
- Some fragmented admin/moderation review paths that are not yet unified behind one event producer.
- Device-to-device Messenger/Calls ordering where realtime and polling overlap.

## Event Producer Coverage %

Event producer coverage: 91%.

Coverage is high enough for cursor validation, but not yet production-final. The remaining gaps are mostly provider/device/staging concerns and fragmented admin-review paths.

## Overall Native Migration %

- Foundation/parity coverage: 89%.
- System consistency confidence: 91%.
- Release QA confidence: 66%.
- Overall native migration estimate: 89%.

This estimate separates feature coverage from release readiness. Native PulseSoc is feature-rich and increasingly coherent, but not yet a production replacement until physical device, provider, and staging stress checks are completed.

## Remaining Work Before Production-Ready Native PulseSoc

- Physical iPhone QA for camera, push, installed deep links, and media-heavy flows.
- Provider QA for APNs/FCM, Stripe/payment webhooks, refunds, disputes, and checkout recovery.
- Two-device call and messaging ordering checks.
- Production-like seeded staging run for event ordering, duplicate provider delivery, and offline-to-online replay.
- Final UI polish pass for spacing, animations, loading states, and accessibility across all core native surfaces.

## ONE Highest-Impact Fix ONLY

Create a persistent authenticated staging QA environment with seeded buyer, seller, creator, moderator, and owner accounts plus production-like event replay fixtures.

Why this is the highest-impact fix:

- It would let the team repeat browser, simulator, and iPhone QA without rebuilding throwaway local data.
- It would validate provider-adjacent ordering with durable accounts and known fixtures.
- It would turn cursor replay, Activity Inbox, Orders, Seller Inventory, Marketplace, Messenger, Calls, and Safety QA into a repeatable release gate.

## System State Audit

1. Cursor replay correctness %: 93%.
2. Multi-device ordering confidence %: 84%.
3. Systems that converge correctly: Activity Inbox, Notifications, Buyer Orders, Seller Inventory, Marketplace, Messenger activity, Calls activity, and Trust/Safety activity in seeded local backend checks.
4. Systems still at risk of drift: push delivery ordering, production provider webhook ordering, fragmented admin moderation review updates, two-device call/media state, and high-volume screen-level refresh.
5. Event producer coverage %: 91%.
6. Overall native migration %: 89%.
7. Remaining work before production-ready native PulseSoc: persistent staging fixtures, physical iPhone QA, provider push/payment QA, two-device comms QA, and final UI polish.
8. ONE highest-impact fix ONLY: build the persistent authenticated staging QA environment and replay fixture pack.

