# Message notification read reconciliation

## Verdict

**PARTIAL.** The implementation and focused automated checks pass, but the
required physical-iPhone matrix has not run: no authorized two-account test
conversations were supplied. This is not physical-device PASS evidence.

Start: `main` at `f4a5f2f1413a4a7a60c9a6345aac57966d945a3c`. Work branch:
`codex/message-notification-read-reconciliation`. Final commit/remote SHA are
recorded after commit/push. Pre-existing graphite/profile work remains outside
this change.

## Root cause and architecture

The active native app used `expo-notifications` for badge writes but never
enumerated or dismissed delivered notifications. A conversation read updated
the server watermark while its iOS Notification Center entries remained. Badge
clearing does not dismiss delivered requests.

Communications v2 creates a message in `pulse_communications_v2/service.py`,
passes it through `services/pulsesoc_notification_system.py`, and delivers it
through `services/push_service.py`/Expo. The existing Expo foundation is reused;
no native bridge or call/Live handling was changed.

New Communications v2 pushes carry `schemaVersion: 1`,
`notificationType: "message"`, `messageNamespace: "comm_v2"`, `messageId`,
`conversationId`, `recipientUserId`, `senderId`, `sentAt`, and existing deep
links. Expo also receives a recipient-scoped conversation `threadId`. The
logical message key is distinct from the OS request identifier.

## Behavior implemented

`mobile-native/src/core/messageNotificationReconciliation.ts` is the centralized
single-flight reconciler. It gets presented notifications, strictly parses data,
submits opaque identifiers to an authenticated server decision endpoint, and
dismisses only OS request identifiers confirmed as read, deleted, blocked, or
removed. It never identifies notifications by title, sender, or preview text.

Unknown, malformed, unrelated, and wrong-account entries are preserved. Legacy
entries are removed only after the server proves notification ownership and the
message/conversation relationship; shared membership alone is deliberately
insufficient. Security, marketplace, payment, crypto, reaction, system,
missed-call, and Live notifications are outside the filter.

Reads are now bounded by the displayed `through_message_id`; an arriving later
message remains unread. Offline reads become scoped durable `messenger.read`
operations, dismiss their exact local notifications immediately, and retry the
server acknowledgement. The reconciler runs after reads/retries, foreground and
session restoration, message sync, notification taps, and account transitions.
It recalculates the existing authoritative combined badge rather than decrementing.

## Changed files

* Communications v2 service/routes/reconciliation module
* Central notification and Expo push payload builders
* Native Messenger, outbox, app lifecycle, notification routing, and session code
* Deterministic Python and Jest reconciliation tests

## Validation

Passed:

* Python reconciliation + receipt tests: **37 passed**.
* Native reconciliation + outbox Jest tests: **60 passed**.
* Python compilation and `git diff --check`.

`npm run typecheck --prefix mobile-native` is **NO-GO** due to an unrelated,
pre-existing error in `mobile-native/src/screens/ProfileScreen.tsx:784`:
`Cannot find name 'profileSurface'`. No release build, install, or push is
claimed while that gate fails.

## Device evidence and limitations

Paired iPhone 16 Pro `P3r7or` (`F45E640F-6D02-514E-877C-B764E8D6818F`) is
available but not used for this change. There is no simulator or physical
Notification Center evidence. iOS does not guarantee background execution on a
terminated secondary device; next foreground/cold start is the mandatory repair
path.

## Rollback

Revert the dedicated commit once created. The payload fields are additive and
older clients ignore them; no data migration is required.
