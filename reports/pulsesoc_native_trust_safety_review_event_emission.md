# PulseSoc Native Trust/Safety Review Event Emission

Date: 2026-07-06

Scope: backend event emission hardening for admin/moderator review updates and safety report variants.

Important roadmap rule:

- Do not focus on Android right now.
- Android remains tracked as a later release-readiness gap.
- Current priority remains iPhone/iOS native app, server-authoritative event consistency, payment/checkout/order trust correctness, PulseSoc production parity, and LogiNexus UI polish.

## Goal

Eliminate silent mutation gaps in review/update paths that can leave Native Activity Inbox, Trust/Safety, Account Health, Notifications, Profile, and Messenger stale after a server-side safety decision.

This work does not add UI, change WebView routes, or duplicate moderation business logic. It only makes existing server-authoritative review and report mutations cursor-visible through `/api/pulse/sync/events`.

## Implemented Event Producers

Review decision events:

- `safety_appeal_approved`
- `safety_appeal_rejected`
- `safety_appeal_updated`
- `report_reviewed`
- `report_dismissed`

Report variant events:

- marketplace listing reports as `report_submitted` with `entity_type=marketplace_report`
- group reports as `report_submitted` with `entity_type=group_report`
- group comment reports as `report_submitted` with `entity_type=group_comment_report`
- group post reports as `report_submitted` with `entity_type=group_post_report`
- music reports as `report_submitted` with `entity_type=music_report`

## Event Metadata Contract

Each new durable event includes:

- `event_type`
- `entity_type`
- `entity_id`
- `actor_id`
- `timestamp`
- `sync_cursor_key`
- `review_status` or safe report metadata where applicable
- subsystem invalidation hints for activity, notifications, safety, and account health

## Event visibility through sync cursor

Seeded backend checks validate the following through `/api/pulse/sync/events`:

- marketplace listing report submission
- group report submission
- music report submission
- verification review rejection
- music report review after admin removal
- Pulse report dismissal from Trust/Safety department action

## Activity/Trust/Safety/Account Health consistency impact

Activity Inbox:

- Receives durable review and report lifecycle events instead of relying on admin-only audit logs.

Trust/Safety:

- Safety Hub can refresh after report submission, review, dismissal, and verification review decisions.

Account Health:

- Account Health can reflect report and appeal decision state from the same cursor-backed notification source as other native surfaces.

Notifications:

- Review decisions and report updates flow through `pulse_notifications`, preserving the native sync cursor source of truth.

Profile/Messenger:

- Block/mute state still depends on first-class mutation routes. Existing block events are covered; mute/unmute/unblock remain tracked gaps until server-authoritative routes exist.

## Trust/safety review event coverage %

Estimated coverage: 84%.

Covered:

- verification review approve/reject/update paths
- legacy verification approve/reject path
- marketplace report submission
- group report submission
- group comment report submission
- group post report submission
- music report submission
- music report reviewed path
- Pulse report dismissed path

Partially covered:

- generic moderation case status updates, because many cases are admin-operational and not always tied to a user-facing report.
- group/comment/media report review resolution, because active admin review routes for those specific report tables are not fully first-class yet.

Not covered by active routes:

- user unblocked
- user muted
- user unmuted

Those remain listed because no first-class active native/backend mutation route was identified in this pass. This mission did not invent new APIs.

## Remaining silent mutation paths

- user unblock if/when a first-class route exists
- user mute/unmute if/when first-class routes exist
- group/comment/media report review-update routes that are still admin-dashboard only or not first-class mutation endpoints
- moderation case updates without a user-facing report recipient
- APNs/FCM delivery-state confirmation for safety updates on physical devices

## Event producer coverage %

Estimated overall backend event producer coverage after this pass: 89%.

## Overall native migration %

- Foundation/parity coverage: 88%
- System consistency confidence: 88%
- Release QA confidence: 64%

## Critical production risk gaps

- User unblock/mute/unmute cannot be fully event-covered until server-authoritative routes are first-class.
- Specific group/comment/media report review resolution needs dedicated backend mutation paths or a unified moderation-review endpoint.
- Multi-device ordering and APNs/FCM delivery confirmation remain release-readiness gaps.

## ONE highest-impact fix ONLY

Unified Moderation Review Endpoint Event Emission Hardening.

Reason: submission and several review paths are now cursor-visible, but report review state is still fragmented across marketplace, music, group, moderation case, and department-action surfaces. A unified server-authoritative review endpoint with standardized event emission would close the largest remaining safety consistency gap without expanding UI.
