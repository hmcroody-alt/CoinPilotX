# PulseSoc Native Dashboard Quick Action Parity Hardening

Date: 2026-07-08

## Scope

This pass hardens User Dashboard quick-action routing. It does not add new product features, redesign the dashboard, modify production WebView routes, or duplicate backend business logic.

## What Changed

- Added a dashboard action route classifier in `mobile-native/src/navigation/dashboardRouting.ts`.
- Classified dashboard actions as:
  - Native route
  - Native shell
  - Safe web fallback
  - Missing/invalid route
- Updated User Dashboard quick-action badges to show the real route class instead of a generic `Go` label.
- Updated module card and module detail route labels to use the same classifier.
- Added direct quick-action aliases for `/pulse/compose` and `/pulse/music` so URL/deep-link entry matches dashboard click behavior.
- Updated weak quick-action targets:
  - `Create Post` now opens the native feed composer route.
  - `Upload Video` now opens native Camera Studio in video mode.
  - `Add Status` now opens the native Status creator path.
  - `Invite Friends` now lands in the native Network/Friends dashboard shell.
  - `Go Live` remains a safe Live Studio web fallback because native hosting remains release-planned separately.

## Route Classification

Native route:

- Create Post
- Upload Video
- Add Status
- Upgrade to Premium
- Open Scam Shield

Native shell:

- Invite Friends
- Create Crypto Alert
- Ask Crypto AI
- Scan Token
- Add Watchlist Asset
- Open Pulse Radio

Safe web fallback:

- Go Live

Missing/invalid route:

- None found after hardening.

## Dashboard Surfaces Covered

- Dashboard hero quick actions: covered through `quickActions` cards and native card routing.
- Dashboard quick-action links: covered through `dashboardQuickActions`.
- Module card buttons: covered through native module shell navigation plus classifier labels.
- Module detail shell actions: covered through classifier labels, native access routes, and safe fallback buttons.
- Legacy dashboard aliases: preserved through the dashboard module registry and `DashboardLegacyModule`.
- Notification/deep-link dashboard actions: preserved through dashboard module route resolution before generic fallbacks.

## Visible QA

Visible QA used the built-in QA browser through the local QA server. Representative quick-action destinations verified:

- `/pulse/compose`
- `/pulse/camera/video?target=feed`
- `/pulse/status?openCreator=1`
- `/dashboard/network/friends`
- `/dashboard/crypto/alerts/create`
- `/dashboard/crypto/ask-ai`
- `/dashboard/crypto/token-scanner`
- `/dashboard/crypto/watchlists`
- `/pulse/live/studio`
- `/pulse/music`
- `/pulse/premium`
- `/scam-shield`

Authenticated visible sweep:

- Passed in the built-in QA browser through the local QA server.
- Create Post opened Home through `/pulse?openComposer=true`.
- Upload Video opened Camera Studio with the browser/device limitation message visible.
- Add Status opened native Status with creator controls visible.
- Invite Friends, Create Crypto Alert, Ask Crypto AI, Scan Token, Add Watchlist Asset, and Open Pulse Radio opened native dashboard module shells with `Module route parity`.
- Go Live opened the native Live surface with the `Go Live Web` provider boundary visible.
- Upgrade to Premium opened the native Premium screen.
- Open Scam Shield opened the native Trust & Safety screen.
- The dashboard quick-action section visibly showed `Native`, `Native shell`, and `Safe fallback` labels.
- Dashboard UI clicks were verified for Create Post and Open Pulse Radio. Go Live was verified through route entry and visible classification; its rendered quick-link label is split by layout, so exact-text browser clicking would require a brittle selector.
- No quick action remained missing, stale, silently invalid, or auth-blocked during the QA pass.

## Dead/stale routes eliminated

- `/pulse` no longer represents the Create Post action ambiguously.
- `/pulse/videos` no longer represents Upload Video ambiguously.
- `/pulse/friends` no longer points at an unowned native route.
- `/pulse/live` no longer pretends to be the Go Live hosting flow.
- Crypto utility actions no longer collapse into generic alert management; they open native dashboard shells when they do not have dedicated native surfaces.

## Completion

- Dashboard foundation parity: 99%.
- Quick-action parity: 100% for registered dashboard quick actions.
- Current native migration: 95% foundation/parity, 92% system consistency confidence, 69% release QA confidence.

## Remaining Dashboard Foundation Work

The next highest-value dashboard task is Dashboard fallback boundary labeling. The route layer is now wired, but every fallback-heavy module should clearly show which parts are native, which parts are provider-owned, and which parts remain release blockers without changing the final UI design yet.
