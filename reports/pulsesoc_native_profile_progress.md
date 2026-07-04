# PulseSoc Native Profile Foundation Progress

Date: 2026-07-04

## Scope

This milestone builds the native Profile foundation inside `mobile-native/`. It does not touch production WebView paths, web templates, database logic, profile authorization, moderation, privacy rules, premium entitlement logic, badge assignment, follower logic, or media storage rules.

The native app remains a faster client for the existing PulseSoc profile system. Server APIs stay authoritative for profile identity, username validation, profile update rate limits, media validation, avatar/cover persistence, profile privacy, premium profile themes, and profile notification targets.

## Existing Web/Backend Implementation Inspected

Native implementation was mapped from the existing PulseSoc profile surfaces:

- Web current profile route: `/pulse/profile`
- Web public profile route: `/pulse/profile/<profile_key>`
- Web profile edit route: `/pulse/profile/edit`
- Current profile API: `GET /api/pulse/profile/me`
- Profile update API: `POST /api/pulse/profile/update`
- Avatar upload API: `POST /api/pulse/profile/avatar`
- Cover upload API: `POST /api/pulse/profile/cover`
- Avatar remove API: `POST /api/pulse/profile/avatar/remove`
- Cover remove API: `POST /api/pulse/profile/cover/remove`
- Premium profile theme API: `GET/POST /api/pulse/premium/profile-theme`
- Profile posts through existing feed filter: `GET /api/pulse/feed?profile=<profile_key>`
- Existing profile notification targets from `services/notification_service.py` and `static/notifications.js`

## Reused API Contract

Native Profile uses these existing endpoints:

- `GET /api/pulse/profile/me`
- `POST /api/pulse/profile/update`
- `POST /api/pulse/profile/avatar`
- `POST /api/pulse/profile/cover`
- `POST /api/pulse/profile/avatar/remove`
- `POST /api/pulse/profile/cover/remove`
- `GET /api/pulse/premium/profile-theme`
- `POST /api/pulse/premium/profile-theme`
- `GET /api/pulse/feed?profile=<profile_key>`

No native-only profile authorization, username validation, premium status, badge, privacy, follower, moderation, or media validation rules were introduced.

## Implemented

- Native current Profile screen.
- Native public Profile route for `/pulse/profile/<profile_key>`.
- Reusable native `ProfileHeader`.
- Avatar, cover, name, handle, bio, badges, premium state, verification state, theme label, and stats layout.
- Profile tabs:
  - Posts
  - Media
  - About
- Profile posts using the existing feed profile filter.
- Native Profile Edit screen.
- Edit display name, username, bio, links, expertise tags, and privacy.
- Avatar upload with native image picker permission handling.
- Cover upload with native image picker permission handling.
- Avatar remove.
- Cover remove.
- Profile theme selection through the existing premium theme API.
- Save/cancel behavior.
- Loading, offline, empty, error, and retry states.
- Offline current-profile cache through `AsyncStorage`.
- Author header to profile navigation from native feed/post cards.
- Notification profile target routing.
- Messenger profile navigation when existing conversation payload includes a public profile id.

## Native Routing Behavior

Supported native profile routes now:

- `/pulse/profile`
- `/pulse/profile/edit`
- `/pulse/profile/<profile_key>`

Public profile detail currently uses existing feed profile filtering for native posts and falls back to the full web profile for complete public about/badge/listing surfaces because the existing public profile route is web-rendered and does not yet expose a full JSON profile-detail contract.

## Native Rebuild Boundaries

Rebuilt natively:

- Profile detail UI.
- Profile edit UI.
- Image picker flow and upload progress states.
- Profile tabs.
- Profile post/media list.
- Native navigation and deep-link routing.
- Offline current-profile cache restore.

Still server-authoritative:

- Profile authorization.
- Username validation and uniqueness.
- Profile update rate limits.
- Privacy visibility.
- Premium entitlement for themes.
- Badge/verification assignment.
- Avatar/cover media validation and CDN durability checks.
- Moderation/report/block/follow rules.
- Profile posts and feed visibility.

## Device-Only Behavior Not Verified

The following need iOS/Android simulator or real-device QA:

- Native image picker permission prompt behavior.
- Avatar upload progress against real device file URIs.
- Cover upload progress against real device file URIs.
- Large photo memory behavior.
- Keyboard behavior on Profile Edit.
- Public profile routing from killed/background notification state.

Source verification is in place, but these are not marked as passed without device access.

## Current Status

Native Profile now has a reusable foundation that preserves the existing PulseSoc profile backend while moving current profile, profile edit, profile posts, profile media, and profile deep links into native screens. Full public profile parity still needs a public profile JSON contract or a dedicated native adapter before web fallback can be removed for complete public about/badge/listing details.
