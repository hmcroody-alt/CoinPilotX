# PulseSoc Native Home Code Reuse Audit

Date: 2026-07-11

## Reused Existing Native Code

- `HomeScreen`
- `HomePulseComposer`
- `PostCard`
- `MasterNavigationDrawer`
- `LogiNexusGlobalHeader`
- `LogiNexusBottomNavigation`
- `NativeMediaViewer`
- Existing feed/status/composer API wrappers
- Existing `addPostComment` API wrapper from Post Detail
- Existing `PostCard` media, author, safety, and action wiring

## Reused Production Logic / Contracts

- Feed categories and feed API contract
- Status rail API contract
- Create post API contract
- Media upload contract
- Hide/mute/follow/save/repost/react feed actions
- SafetyHub routing for report/block
- Event invalidation after publish/follow/hide/mute/comment
- Server-authoritative comment creation through `/api/pulse/posts/:id/comments`

## Refactored / Extended

- Existing native composer was extended in place with production title and full production mode rail visibility.
- Existing Home command strip was adjusted to remove non-production subtitle copy.
- Existing Status rail copy was aligned with production wording.
- Existing `PostCard` was extended in place with production header controls, production action order, social context, overflow actions, and inline comment composer.
- Existing `HomeScreen` feed state was extended to reconcile inline comment count and preview comments from the server response.

## Rebuilt Natively

- Browser CSS layout, hover states, and backdrop filters remain native surfaces instead of WebView wrappers.
- DOM-based inline comment dock behavior was rebuilt as native `TextInput`/`Pressable` controls while preserving the production endpoint and action intent.

## Obsolete Browser Code Excluded

- DOM selectors, CSS-only hover logic, fixed desktop viewport hacks, browser scroll containers, and Web-only backdrop filters.
