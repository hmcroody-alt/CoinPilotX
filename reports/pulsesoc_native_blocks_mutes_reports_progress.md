# PulseSoc Native Blocks, Mutes, and Report Management Foundation

Date: 2026-07-05

## Scope

This mission built the native Unified User Safety Control Layer for PulseSoc without changing production WebView routes or duplicating backend moderation logic.

The native layer is a control and visibility surface only. PulseSoc backend systems remain authoritative for moderation, filtering, enforcement, block state, report review, notification safety, account health, and visibility decisions.

## Production Codebase Inspection

Existing production systems reused:

- `POST /api/pulse/report`
- `POST /api/pulse/block`
- `POST /api/security/report`
- `GET /api/dashboard/network/state`
- Existing protected network safety route `/dashboard/network/network-security`
- Existing protected network blocks/mutes route alias through `/dashboard/network/blocks-mutes`
- Existing Trust/Safety support ticket APIs
- Existing PulseSoc feed filtering using `blocked_users`
- Existing Communications V2 message report and block APIs
- Existing account health, trust, safety, moderation, and network-governance state
- Existing notification routing and deep-link normalization

Important backend gaps discovered:

- No user-safe native JSON endpoint currently exposes the full blocked-user list.
- No user-safe native JSON endpoint currently exposes unblock.
- No user-safe native JSON endpoint currently exposes user mute/unmute.
- No user-safe native JSON endpoint currently exposes complete report review history/status.
- Conversation mute exists in Communications V2, but user mute is not exposed as a general native safety API.

Because of those gaps, native unblock, mute, full block-list, and full report-history behaviors use protected web fallback or clearly labeled local handoff history. The native app does not pretend local state is authoritative.

## Native Implementation

Added:

- `mobile-native/src/api/safety.ts`
- `mobile-native/src/screens/SafetyHubScreen.tsx`
- Native `SafetyHub` route
- Native `SafetyWebHub` route alias
- Deep links:
  - `/pulse/safety`
  - `/pulse/safety/blocks`
  - `/pulse/safety/mutes`
  - `/pulse/safety/reports`
  - `/dashboard/network/network-security`
  - `/dashboard/network/blocks-mutes`

Updated native entry points:

- Settings: `Safety Hub`
- Trust & Safety: `Safety Hub`
- Account Health: `Safety Hub`
- Profile: `Safety`
- Messenger: `Safety`
- Notification/deep-link routing for safety paths

## Supported Native Actions

### Block User

Supported through existing backend:

- Calls `POST /api/pulse/block`
- Accepts public PulseSoc ID or numeric user ID
- Uses backend authorization, duplicate handling, moderation report creation, and feed filtering
- Records local action history only as device-side visibility

### Create Report

Supported through existing backend:

- Calls `POST /api/pulse/report`
- Supports target types:
  - user
  - post
  - reel
  - message
  - marketplace
  - status
- Uses backend moderation/review state
- Records local action history only as device-side visibility

### Mute Handoff

Not server-authoritative in native yet:

- User mute/unmute API was not found.
- Native records the handoff locally and routes to protected network controls.
- Conversation mute remains owned by existing Messenger/Communications V2 infrastructure.

### Unblock Handoff

Not server-authoritative in native yet:

- Unblock API was not found.
- Native records the handoff locally and routes to protected network controls.

## Native Safety Hub UX

Implemented tabs:

- Overview
- Blocks
- Mutes
- Reports

Implemented states:

- Loading
- Pull-to-refresh
- Cached/offline fallback
- Error state
- Empty action history
- Supported action success
- Unsupported action handoff
- Protected web fallback

The screen follows the PulseSoc internal design standard through a calm control-center layout, strong hierarchy, glowing accent states, and clear safety boundaries without exposing internal brand-language terms in user-facing UI.

## QA Notes

Static verification passed.

Practical QA browser route checks were run through the built-in QA browser against a temporary local QA backend/proxy and an authenticated local QA account. Production credentials were not used.

Verified:

- `/pulse/safety`
- `/pulse/safety/blocks`
- `/pulse/safety/mutes`
- `/pulse/safety/reports`
- `/dashboard/network/network-security`
- `/dashboard/network/blocks-mutes`
- Settings entry point
- Trust/Safety entry point
- Account Health entry point
- Profile entry point
- Messenger entry point
- No browser console errors in the final route checks

Observed authenticated UI:

- Safety overview rendered network/safety metrics and authority boundary.
- Blocks route rendered block-user controls and the blocked-list visibility disclaimer.
- Mutes route rendered mute-duration choices and server-authoritative fallback copy.
- Reports route rendered target-type choices and report-history fallback copy.
- Account Health rendered the Safety Hub entry point.
- Messenger rendered the Safety entry point.
- Profile rendered the Safety entry point.

Actions not executed in browser QA:

- Real block submission was not executed to avoid creating relationship/moderation side effects outside a seeded fixture.
- Real report submission was not executed to avoid creating moderation queue side effects outside a seeded fixture.
- Mute/unblock handoffs were visually verified but not used as release evidence because they are documented fallback-only until backend APIs exist.

Device-only behavior:

- None required for the foundation.
- Push/deep-link tap behavior still needs device QA when provider/device testing resumes.

## Remaining Gaps

Recommended backend/API follow-up:

1. Add user-safe `GET /api/pulse/blocks` for current-user blocked list.
2. Add user-safe `DELETE /api/pulse/blocks/<blocked_user_id>` or equivalent unblock API.
3. Add user-safe mute/unmute user APIs if product policy supports user-level mutes.
4. Add user-safe `GET /api/pulse/reports` for current-user report history/status without moderator notes.
5. Expose report categories/schema from the backend so the native UI never hardcodes category policy.

## Risk

Risk level: medium-high.

Reason: block, mute, report, account health, and moderation actions affect user relationships, visibility, trust state, and enforcement.

Risk mitigation:

- No production WebView route changes.
- No client-side moderation authority.
- Unsupported actions clearly route to protected backend flows.
- Native report/block actions call existing server-authoritative APIs.

## Recommendation Summary

Recommended next highest-value action: Native Notifications + Inbox + Activity Graph Unification.

Reason: PulseSoc now has native Feed, Messenger, Notifications, Profile, Trust/Safety, Verification, Account Health, Appeals, Safety Hub, Calls, Creator, Growth, Premium, Intelligence, Marketplace, Reels, Status, and Search surfaces. The next leverage point is unifying notifications, inbox activity, safety events, account events, creator/growth events, alert events, and deep-link destinations into one native activity graph so the app feels like a coherent operating system instead of separate modules.

Reusable PulseSoc systems for the next action:

- Existing notification APIs
- Existing notification read/delete/preference flows
- Existing Messenger unread state
- Existing account health and safety event state
- Existing alert/intelligence events
- Existing creator/growth summaries
- Existing deep-link router
- Existing native Notification Center, Messenger, Account Health, Safety Hub, Intelligence, Alerts, and Growth components

What must be rebuilt natively:

- Unified Activity Inbox screen
- Cross-surface activity cards
- Activity grouping/filtering UI
- Native read/unread/archive/delete controls
- Safety/account/creator/market/intelligence routing polish
- Cached activity timeline
- Practical browser QA for route coverage

Risk level: medium.

Estimated complexity: medium-high.

Safest implementation plan:

1. Inspect current notification, activity, alert, support, security, account, and message event APIs.
2. Reuse existing native Notification Center and deep-link router.
3. Build read-only unified activity timeline first.
4. Add mutations only where existing APIs already support them.
5. Keep unsupported event administration on safe web fallback.
6. Run static verification, audit, and short QA browser route checks before commit.
