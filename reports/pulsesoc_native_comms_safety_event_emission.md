# PulseSoc Native Communications and Safety Event Emission

Date: 2026-07-06

Scope: backend event emission hardening for native cursor sync.

Important roadmap rule:

- Do not focus on Android right now.
- Android remains tracked as a later release-readiness gap.
- Current priority remains iPhone/iOS native app, server-authoritative event consistency, checkout/order trust correctness, PulseSoc production parity, and LogiNexus UI polish.

## Goal

Eliminate silent event gaps in the high-frequency communications and safety workflows that feed Native Activity Inbox, Messenger, Calls, Notifications, Trust/Safety, and Account Health.

This work does not add UI, change WebView routes, or duplicate moderation/call/message business logic. It only makes existing server-authoritative state changes cursor-visible through `/api/pulse/sync/events`.

## Implemented Event Producers

Message events:

- `message_received`
- `message_seen`
- `message_deleted`
- `message_reported`

Call events:

- `call_started`
- `call_accepted`
- `call_declined`
- `call_ended`
- `call_missed`
- `call_failed`

Safety events:

- `user_blocked`
- `report_submitted`
- `safety_appeal_submitted`

## Event Metadata Contract

Each new durable event includes:

- `event_type`
- `entity_type`
- `entity_id`
- `actor_id`
- `timestamp`
- `sync_cursor_key`
- safe domain/category metadata
- subsystem invalidation hints

## Event visibility through sync cursor

The following flows are validated through `/api/pulse/sync/events`:

- canonical Pulse message send to recipient as `message_received`
- message seen by recipient to sender as `message_seen`
- message deletion to conversation members as `message_deleted`
- message report as `message_reported` and `report_submitted`
- user block as `user_blocked` and `report_submitted`
- generic report as `report_submitted`
- call start as `call_started`
- call accept as `call_accepted`
- call decline as `call_declined`
- call end as `call_ended`
- call timeout as `call_missed`
- call token failure as `call_failed`

## Activity/Messenger/Calls/Safety consistency impact

Activity Inbox:

- Receives durable comms/safety/call state transitions instead of depending only on transient realtime events.

Messenger:

- Message received, seen, deleted, and reported states can invalidate native message caches deterministically.

Calls:

- Active call lifecycle transitions now emit cursor-visible events for native recovery and foreground/background consistency.

Notifications:

- Communication and safety events flow through `pulse_notifications`, the same source used by the native sync cursor.

Trust/Safety and Account Health:

- Block/report/appeal events now have a durable sync signal for Safety Hub and Account Health refresh.

## Message/call/safety event coverage %

Estimated coverage: 78%.

Covered:

- message receive/seen/delete/report
- call start/accept/decline/end/missed/failed
- user block
- generic report submit
- message report submit
- verification appeal submit

Partially covered:

- report updated, because admin/moderator update routes still need explicit event emission.
- safety appeal updated, because review/update flows are not yet fully mapped.

Not covered by active routes:

- user unblocked
- user muted
- user unmuted

Those are tracked as remaining gaps because no first-class active native/backend mutation route was identified in this pass. Adding those APIs would be feature expansion, so this mission did not invent them.

## Remaining silent mutation paths

- user unblock if/when a first-class route exists
- user mute/unmute if/when a first-class route exists
- report status updates from admin/moderator review
- safety appeal status updates from review tools
- group/comment/media report variants not yet fully mirrored into the unified Safety Hub event stream
- refund requested and order cancelled from commerce if routes are later added

## Event producer coverage %

Estimated overall backend event producer coverage after this pass: 86%.

## Overall native migration %

- Foundation/parity coverage: 88%
- System consistency confidence: 86%
- Release QA confidence: 64%

## Critical production risk gaps

- Admin review and safety update routes need durable `report_updated` / `safety_appeal_updated` events.
- User mute/unmute/unblock route coverage depends on first-class backend mutations.
- Real APNs/FCM delivery and lock-screen behavior remain release-readiness blockers, not development blockers.
- Multi-device event ordering is still polling/cursor-based, not full realtime streaming.

## ONE highest-impact fix ONLY

Trust/Safety Review Update Event Emission Hardening.

Reason: report submission and block events are now cursor-visible, but admin/moderator resolution paths can still leave Safety Hub and Account Health stale. The next highest-value consistency fix is to emit `report_updated` and `safety_appeal_updated` from real review/status mutation paths.
