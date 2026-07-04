# PulseSoc Native Groups/Communities + Rooms Progress

Date: 2026-07-04

## Scope

The native Groups/Communities + Rooms foundation lives under `mobile-native/` plus a narrow read-only backend JSON bridge for group browse/detail. It does not touch production WebView routes, templates, existing group pages, group membership rules, role logic, moderation logic, room seeding rules, Messenger business logic, or database ownership rules.

Server APIs stay authoritative. Native Groups is a new client for existing PulseSoc group, room, membership, moderation, and Messenger contracts.

## Existing Web/Backend Implementation Inspected

Current PulseSoc group/room surfaces inspected before implementation:

- Group browse page: `/pulse/groups`.
- Group create page: `/pulse/groups/create`.
- Group detail page: `/pulse/groups/<group_slug>`.
- Group create API: `POST /api/pulse/groups/create`.
- Group join/leave APIs: `/api/pulse/groups/<id-or-slug>/join` and `/leave`.
- Group chat open APIs: `/api/pulse/groups/<id-or-slug>/chat/open`.
- Group invite/report/update/member role/ban/unban/delete APIs.
- Group post/comment APIs and group post moderation/report/delete/pin flows.
- Room APIs: `/api/pulse/communications/rooms`, `/api/pulse/messages/rooms`, `/api/pulse/messages/rooms/<room_id>/join`, and `/api/pulse/messages/room/open`.
- Default room helpers: `pulse_default_room_cards()` and `pulse_ensure_default_rooms(...)`.
- Group tables: `pulse_groups`, `pulse_group_members`, `pulse_group_posts`, `pulse_group_post_comments`, `pulse_group_post_reactions`, reports, roles, bans, invite, media, and action-log tables.
- Native reuse points: Messenger `Chat` route, Search/Saved result routing, native loading/error/cache patterns, and shared navigation fallback.

## Implemented Native Foundation

- Thin read-only JSON group browse endpoint: `GET /api/pulse/groups`.
- Thin read-only JSON group detail endpoint: `GET /api/pulse/groups/<group_slug>`.
- Native groups API wrapper over existing group, room, join/leave, chat-open, and report APIs.
- Native Groups tab and Group detail stack route.
- Communities browse with search, category chips, pull-to-refresh, pagination, offline cache, loading/empty/error states.
- Community detail modal with description, rules, membership state, member count, post count, role indicators, and compact group feed preview.
- Join/leave actions through existing membership APIs.
- Group chat open action through existing group chat API and native Messenger route.
- Group report hook through existing report API.
- Rooms rail using existing Communications room API and Messenger chat handoff.
- Room join/open action through existing room join API and native Chat route.
- Deep-link routing for `/pulse/groups`, `/pulse/groups/<slug>`, and room query links where supported.
- Web fallback remains for unsupported admin, moderation, create/edit, invite, and advanced member-management surfaces.

## Reuse-First Boundaries

Native Groups does not implement its own:

- Group ownership checks.
- Membership authorization.
- Private/invite-only join rules.
- Role or moderator permission checks.
- Invite-link generation.
- Report/moderation flows.
- Group chat creation/linkage rules.
- Room seeding or room conversation creation.
- Group post/comment validation.
- Server-side validation.

Those remain owned by the existing PulseSoc backend, database, group services/helpers, Messenger services, moderation rules, and room helpers.

## Native-Only Layer

The rebuilt native layer is limited to:

- Group and room browse UI.
- Group card UI.
- Group detail UI.
- Compact group feed preview UI.
- Room card UI.
- Button states for join, leave, open chat, open room, and report.
- Cached-result display.
- Loading, empty, offline, and error states.
- Native navigation and deep-link routing.

## Known Gaps

- The first native slice uses compact group post preview payloads. Full group post creation, reactions, comments, media galleries, and moderation tools remain backend/web-owned until a dedicated native group feed slice.
- Group creation/editing/admin/moderation is intentionally not native yet.
- Invite links and member role management remain existing backend/web/provider flows.
- Group chat open can return unavailable if advanced group chat mode is disabled by the backend.
- Room realtime behavior is reused through Messenger once a conversation is opened; this slice does not add a separate realtime room UI.

## Device-Only Behavior Not Verified

The following are not marked as passed without device access:

- Real-device group list scroll performance.
- Real-device group detail modal feel.
- Real-device group join/leave recovery.
- Real-device room open and Messenger handoff.
- Real-device group chat handoff.
- Real-device deep-link routing from push notifications.
- Real-device offline cache restore after app termination.

## Next Recommendation

Recommended next step: Native Architecture Health Report + Shared Core Consolidation.

Reason: the native app now has many migrated surfaces using repeated patterns: API wrappers, AsyncStorage cache, loading/empty/error states, card layouts, native/web fallback routing, media previews, and action-state handling. Before moving into Live, Calls, Creator Studio, Growth, and Premium, the safest next step is to audit and consolidate shared patterns into documented `mobile-native/src/shared` or `mobile-native/src/core` modules where three or more features already depend on them.
