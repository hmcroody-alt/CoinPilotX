# PulseSoc Native Architecture Health Report

Date: 2026-07-04

## Scope

This checkpoint does not add a major user-facing feature. It inspects the current PulseSoc production codebase and the current `mobile-native` codebase to keep the native app reusable, scalable, and aligned with the existing PulseSoc platform.

Production WebView routes, templates, backend business logic, database rules, moderation rules, payment rules, LiveKit/Mux flows, and notification delivery logic were not changed.

## Codebase Inspected

Native areas inspected:

- `mobile-native/src/api`
- `mobile-native/src/screens`
- `mobile-native/src/components`
- `mobile-native/src/media`
- `mobile-native/src/navigation`
- `mobile-native/src/session`
- `mobile-native/src/theme`
- `mobile-native/src/utils`

Production/backend areas inspected for next-feature context:

- Group and room routes/APIs added in the previous foundation.
- Live/LiveKit/Mux production surfaces and existing audits.
- Existing Live Studio browser path, LiveKit direct playback fallback, Mux egress behavior, and live-session database/reporting references.

## Health Summary

Overall native architecture state: healthy but at the point where shared-core discipline matters.

The project is still correctly separated under `mobile-native/`, reuses the PulseSoc backend, and avoids duplicating backend business rules. The main risk is not correctness yet; it is accumulating repeated client patterns as more features are added.

## Duplicated Patterns Found

API/cache duplication:

- Most feature API wrappers use the same AsyncStorage pattern: read JSON, normalize, remove corrupt cache, write sliced payloads.
- Duplicated in Feed, Messenger, Profile, Reels, Status, Marketplace, Search, Saved, and Groups.
- Consolidation status: partially addressed.

Loading/error/offline state duplication:

- Most screens repeat `loading`, `refreshing`, `offline`, `error`, and initial center-state UI.
- Present in Home, Reels, Status, Marketplace, Search, Saved, Groups, Profile, Post Detail, Messenger, and Notifications.
- Consolidation status: recommended for a shared screen-state component/hook after visual QA.

Routing/deep-link duplication:

- `notificationRouting.ts` is the correct central route resolver.
- Feature screens correctly reuse `routeNotificationTarget(...)` for item/source URLs.
- The resolver is growing and should be split into tested target parser helpers before Live/Calls deep links are added.
- Consolidation status: recommended next architecture task, not extracted in this checkpoint.

Card/list duplication:

- Marketplace, Saved, Groups, Search, and Messenger have similar card/list/button structures.
- Some duplication is feature-specific and should stay local until the UX settles.
- Repeated primitives such as `EmptyState`, `LoadingState`, `ActionButton`, `FilterChip`, and `HorizontalRail` are good candidates.
- Consolidation status: deferred to avoid UI churn without device screenshots.

Media handling:

- `NativeMediaViewer`, `MediaUploadPreview`, and `useNativeMediaUpload` are strong shared foundations.
- Feed/Post, Marketplace, Status, and Messenger are already integrating shared media viewer/upload paths.
- Reels and Status player cards still have feature-specific playback needs that should stay local until native performance QA.
- Consolidation status: healthy.

Oversized files:

- `ChatScreen.tsx`, `GroupsScreen.tsx`, `MarketplaceScreen.tsx`, `StatusScreen.tsx`, `SavedScreen.tsx`, `ReelsScreen.tsx`, and `StatusCreator.tsx` are large enough to watch.
- These are not automatically wrong because many are still feature-foundation screens, but complex modals/cards should be extracted after device QA.

## Consolidation Completed

Added:

- `mobile-native/src/core/cache.ts`

This provides:

- `readJsonCache(...)`
- `writeJsonCache(...)`

Refactored first adopters:

- `mobile-native/src/api/groups.ts`
- `mobile-native/src/api/saved.ts`
- `mobile-native/src/api/marketplace.ts`

Reason this was safe:

- The same cache pattern existed in at least three features.
- The helper is tiny and feature-agnostic.
- It does not change network requests, backend API contracts, normalization behavior, or business logic.
- It preserves corrupt-cache cleanup behavior.

## Recommended Shared-Core Structure

Recommended near-term structure:

- `mobile-native/src/core/cache.ts`
- `mobile-native/src/core/routes.ts`
- `mobile-native/src/shared/state/LoadingState.tsx`
- `mobile-native/src/shared/state/EmptyState.tsx`
- `mobile-native/src/shared/actions/ActionButton.tsx`
- `mobile-native/src/shared/lists/FilterChip.tsx`
- `mobile-native/src/shared/lists/HorizontalRail.tsx`

Only promote a component/service when at least three features depend on it and the behavior is stable.

## What Should Stay Feature-Local

Keep feature-local for now:

- Reels video playback logic.
- Status viewer timing and gesture behavior.
- Messenger voice/file upload flows.
- Group membership/action state.
- Marketplace checkout/provider handoff.
- Profile edit upload controls.
- Feed composer publishing state.

These areas either depend on feature-specific backend behavior or still need real-device QA before a shared abstraction is safe.

## Production WebView Safety

No production WebView routes were modified in this checkpoint.

The native architecture continues to use the existing PulseSoc platform as the source of truth:

- Backend APIs remain authoritative.
- Database rules remain server-owned.
- Authorization and moderation remain server-owned.
- Payment, premium, LiveKit/Mux, media processing, push, and email logic remain server-owned.

## Device-Only Behavior Not Verified

The following are not marked as passed without device access:

- Whether refactored cache helpers preserve restore behavior after app termination.
- Whether future shared loading/empty components visually match all screens.
- Whether large screens need extraction for real-device performance.
- Whether Live/Calls routing creates additional navigation complexity on device.

## Next Recommendation

Recommended next native feature: Native Live Discovery + Live Viewer Foundation.

Why this comes next:

- Production already has substantial Live/LiveKit/Mux infrastructure, including Live Studio, LiveKit direct playback fallback, Mux egress handling, live sessions, live chat, and multiple live audits.
- The native app now has the social prerequisites: Feed, Notifications, Profile, Search, Saved, Groups/Rooms, Messenger, and Media Viewer.
- A native Live discovery/viewer slice is safer than jumping directly into native hosting/calls because viewing can reuse server-owned LiveKit/Mux/session state while keeping browser Studio as the host fallback.

Reusable PulseSoc logic:

- Existing `/pulse/live` and Live Studio flows.
- Existing LiveKit/Mux bridge and fallback behavior.
- Existing `pulse_live_sessions`, live chat, live destination, replay, and feed insertion tables/services.
- Existing moderation, notification, and profile routing behavior.
- Existing native navigation, Search/Saved/Notifications routing, Profile, Messenger, and Media Viewer patterns.

What must be native:

- Live discovery screen.
- Live session card/list.
- Live viewer shell.
- Native playback where existing payloads expose supported HLS/Mux/LiveKit direct URLs.
- Chat/read-only interaction hooks where existing APIs support them.
- Web fallback for hosting, Studio, cohost, restream, and unsupported playback modes.

Risk: Medium-high.

Complexity: Medium-high.

Safest implementation plan:

1. Inspect current live API/payload shape and live viewer routes.
2. Build native read-only Live discovery first.
3. Build native viewer only for supported playback URLs.
4. Keep native Go Live/Studio on existing web fallback.
5. Reuse notification/search/saved routing.
6. Add a focused Live audit and be explicit about device-only playback verification.
