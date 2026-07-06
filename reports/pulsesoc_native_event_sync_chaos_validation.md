# PulseSoc Native Event Sync Chaos Validation

Date: 2026-07-06

Scope: final stability validation for PulseSoc native event consistency under seeded burst, duplicate, delayed, out-of-order, and offline/recovery-style event conditions.

No new features, UI changes, backend redesign, WebSocket/SSE transport, payment logic, provider behavior, or production WebView behavior were added or modified.

## Architecture Scan

### Event Producers

Current server-authoritative event producers include:

- Marketplace listing APIs and `marketplace_listings`
- Seller-owned listing and seller inventory APIs
- Payment/checkout flow and `seller_transactions`
- Stripe/provider webhook state, including payment failure, refund, dispute, and duplicate webhook idempotency
- Notification creation via `notify_user`
- Messaging APIs and conversation/message sync endpoints
- Calls APIs and active/status/event endpoints
- Safety/report/block/mute/appeal backend paths

### Event Consumers

Current native consumers include:

- Activity Inbox via `loadActivityInboxState`
- Notification Center via `listNotifications` and badge counts
- Buyer Orders via `listBuyerOrders`
- Seller Store / Seller Inventory via `loadSellerStoreSnapshot`
- Marketplace via `searchMarketplace`
- Messenger via conversation list and `syncConversation`
- Calls via `getActiveCalls` and call status/event APIs
- Native polling/cursor invalidation via `eventSync.ts`

## Chaos Simulation Coverage

The audit seeds controlled event bursts for:

- `purchase_created`
- `payment_failed`
- `refund_issued`
- `dispute_created`
- `listing_created`
- `listing_updated`
- `listing_removed`
- `order_cancelled`
- `message_received`
- `call_started`
- `call_ended`
- `notification_delivered`

The seeded chaos conditions include:

- rapid bursts
- duplicate sync delivery
- out-of-order arrival
- delayed arrival
- offline batch replay semantics
- same order updated twice in different states
- refund after cancellation
- listing deleted during active order
- duplicate webhook replay coverage through existing provider idempotency checks
- partial sync recovery modeled through cursor/deduped event envelopes

## Validation Results

### Activity Inbox Correctness

Status: stable for seeded backend notification events.

- activity notifications were created through existing backend notification records
- commerce, marketplace, message, call, and generic notification signals remain visible through server notification APIs
- unread counts include seeded activity
- notification resolution keeps a safe target or native-preserved original target
- no native Activity-only source of truth was introduced

### Buyer Orders State Consistency

Status: stable after final server state wins.

- buyer orders expose paid, failed, refunded, cancelled, dispute, and shipped-like state groups from backend transaction rows
- same-order conflicting updates resolve to the final server transaction state
- refund after cancellation resolves to backend-final refunded state
- deleted/seller-deleted listing references remain safe in historical orders

### Seller Inventory Correctness

Status: stable after refresh.

- seller-owned listings remain available through the seller endpoint
- seller orders read the same transaction ledger as buyer orders
- listing-deleted-during-order remains safe for historical order references
- public Marketplace visibility remains approval/status-gated

### Marketplace Listing Correctness

Status: stable after refresh.

- public marketplace search remains backed by approved/live listing state
- seller-deleted/removed listings are not treated as normal public inventory
- listing state changes require refresh or sync invalidation to appear in already-open native screens

### Notification Duplication Or Loss

Status: structurally stable, provider-live duplicate delivery still release-gated.

- backend fixture checks validate seeded notifications are present
- native sync event dedupe is present for duplicate event envelopes
- invalidation handlers are deduped so one event does not repeatedly reload the same screen
- Stripe duplicate webhook replay remains protected by existing idempotency tokens

### Cursor-Based Sync Correctness

Status: structurally stable.

- native cursor stores `latestEventId`
- polling sends `after_id` and timestamp cursors where present
- missing/unavailable event feed degrades to full subsystem refresh
- duplicate native event envelopes are deduped
- handler invalidation uses unique handler dispatch

### Cache Invalidation Correctness

Status: stable for wired subsystems.

Wired:

- Activity Inbox
- Notifications / badge counts
- Buyer Orders
- Seller Store / Seller Inventory
- Marketplace

Mapped but not yet wired:

- Messenger
- Calls
- Safety
- Verification
- Premium
- Intelligence / Alerts

## QA Browser Check

Built-in QA browser route checks confirm that the local native web shell renders protected routes without console errors. The route sweep covered:

- `/pulse/activity`
- `/pulse/orders`
- `/dashboard/orders?order_id=1`
- `/pulse/marketplace`
- `/pulse/seller-store?title=Seller%20%2F%20Store`
- `/pulse/messages`
- `/pulse/calls/qa-call-1`

Result: each route preserved the requested URL, rendered the protected Login surface, kept the React root present, and reported zero captured local console errors. Since the current web QA build uses the default production API base, the QA simulator-login shortcut is intentionally disabled and protected routes show Login when unauthenticated.

Authenticated browser chaos-flow validation remains blocked until a local QA API base or valid authenticated QA browser session is available.

## SYSTEM STATE AUDIT

### 1. Systems Stable Under Chaos

- Backend commerce transaction truth.
- Buyer Orders final-state rendering.
- Seller Orders ledger consistency.
- Seller Inventory after refresh.
- Marketplace approval/status public visibility.
- Activity Inbox notification visibility for seeded event families.
- Notification unread/read/delete controls.
- Native polling/cursor/invalidation structure for wired subsystems.
- Duplicate native event envelope handling.
- Duplicate invalidation handler prevention.

### 2. Systems That Drift Under Load

- Already-open Marketplace, Buyer Orders, and Seller Store screens can drift briefly until polling, foreground refresh, or manual refresh.
- Activity Inbox can drift from Messenger/Calls when those surfaces update because their event-sync handlers are mapped but not wired.
- Safety/Verification/Alerts/Intelligence can drift until their screen handlers are wired into the sync registry.

### 3. Systems That Fail Under Concurrency

No repo-local concurrency failure was found in the seeded validation.

Not fully proven under concurrency:

- provider-live Stripe webhook replay timing
- real push/badge delivery timing
- two-device call/message timing
- real offline network partition recovery on physical devices
- Android device behavior

### 4. Sync Engine Weaknesses Exposed

- The production event feed endpoint remains unconfirmed.
- Current sync readiness depends on fallback full refresh when `/api/pulse/sync/events` is absent.
- Messenger/Calls and Trust/Verification/Alerts/Intelligence mappings exist but do not yet refresh their screens through the shared registry.
- No browser-authenticated chaos flow could be completed with the current production-API web build.

### 5. Missing Real-Time Guarantees

- stable backend event cursor contract
- durable event replay ordering
- provider push event timing
- APNs/FCM foreground/background badge timing
- cross-device message/call ordering
- WebSocket/SSE transport after polling-first proof

### 6. Subsystem Completion %

| Subsystem | Native coverage | Chaos consistency confidence | Release QA confidence |
| --- | ---: | ---: | ---: |
| Commerce truth / payments | 88% | 85% | 62% |
| Buyer Orders | 92% | 87% | 72% |
| Seller Inventory | 93% | 87% | 72% |
| Marketplace | 92% | 85% | 72% |
| Activity + Notifications | 88% | 84% | 70% |
| Native event sync foundation | 76% | 72% | 52% |
| Messenger | 76% | 68% | 58% |
| Calls | 63% | 60% | 42% |
| Trust/Safety/Verification | 85% | 78% | 66% |
| Intelligence/Alerts | 80% | 72% | 60% |
| Native media/camera | 72% | 66% | 45% |
| iOS readiness | 68% | 64% | 52% |
| Android readiness | 35% | 32% | 24% |

### 7. Overall Native Migration %

Overall native migration estimate:

- 83% foundation/parity coverage
- 75% system consistency confidence
- 61% release QA confidence

### 8. ONE Highest-Impact Fix Only

Next fix: **Expose or confirm an authenticated server event cursor endpoint for native polling.**

Reason:

The native polling/cursor/invalidation layer is ready, and seeded backend truth is consistent. The biggest remaining gap is not another UI feature; it is a production-confirmed, authenticated event cursor feed that can deliver order, listing, notification, message, call, safety, and alert deltas to the existing native sync service.

This should remain polling-first. Do not jump to WebSockets/SSE until the cursor endpoint is confirmed and seeded replay QA passes.
