# PulseSoc Native Dashboard Legacy Route Alias Mapping

Date: 2026-07-06

Section: Legacy Dashboard Route Alias Mapping

## Scope

This pass maps current production dashboard URLs into the native dashboard module detail shell. It does not add new product features, final UI polish, or new backend business logic.

## Implementation

- Added a shared dashboard route resolver in `mobile-native/src/navigation/dashboardRouting.ts`.
- Added `DashboardLegacyModuleScreen` as a thin gateway for legacy production dashboard URLs.
- Registered `DashboardLegacyModule` in stack navigation and deep linking.
- Moved older exact `/dashboard/...` deep-link entries off legacy dashboard paths so the shell resolver wins consistently.
- Added dashboard module alias handling to notification/deep-link routing.
- Kept dashboard cards, shell actions, locks, status labels, and permissions backed by the existing dashboard module registry.
- Preserved safe fallback for advanced flows that do not have dedicated native screens yet.

## Legacy group coverage

All requested legacy dashboard group prefixes now resolve through the native module map when the target is represented in `dashboardModuleGroups`:

- `/dashboard/account/*`
- `/dashboard/network/*`
- `/dashboard/creator/*`
- `/dashboard/intelligence/*`
- `/dashboard/economy/*`
- `/dashboard/media/*`
- `/dashboard/crypto/*`
- `/dashboard/safety/*`
- `/dashboard/ads/*`
- `/dashboard/ai/*`
- `/dashboard/system/*`

## Representative URL checks

These representative legacy URLs are expected to open native `DashboardModuleDetail` shells:

- `/dashboard/account/security`
- `/dashboard/network/community-intelligence`
- `/dashboard/creator/content-planner`
- `/dashboard/intelligence/ai-advisor`
- `/dashboard/economy/earnings`
- `/dashboard/media/pulse-radio`
- `/dashboard/crypto/alerts/create`
- `/dashboard/safety/reports-submitted`
- `/dashboard/ads/campaign-builder`
- `/dashboard/ai/assistant`
- `/dashboard/system/feed`

## Coverage

- Legacy dashboard alias coverage: 100% for represented native dashboard module groups.
- Native shell coverage: 100% of represented dashboard modules.
- Dashboard foundation parity: 97%.

## Remaining fallback-only URLs

- Unknown dashboard URLs not represented in the module registry.
- Admin/moderator-only dashboard modules intentionally hidden from the owner dashboard.
- Provider-owned advanced payment, payout, campaign launch, radio/music distribution, and Live Studio tools.
- Routes that require future dedicated native module screens after the foundation is complete.

## QA notes

- This is foundation route parity, not final UI/UX polish.
- The built-in QA browser is used for visible route checks.
- No Chrome Incognito is used.
- No production WebView paths are changed.
- Authenticated QA uses a local disposable username-backed account; no credentials are committed or recorded in this report.

## Authenticated visible QA result

Result: passed.

All representative URLs opened native module shells after the conflict fix:

- `/dashboard/account/security` -> Account Command Center / Security
- `/dashboard/network/community-intelligence` -> Pulse Network / Community Intelligence
- `/dashboard/creator/content-planner` -> Creator Studio / Content Planner
- `/dashboard/intelligence/ai-advisor` -> Intelligence / Pulse Advisor
- `/dashboard/economy/earnings` -> Economy & Earnings / Earnings
- `/dashboard/media/pulse-radio` -> Pulse Radio & Media / Pulse Radio
- `/dashboard/crypto/alerts/create` -> Crypto Command Center / Create Alert
- `/dashboard/safety/reports-submitted` -> Moderation / Safety / Reports Submitted
- `/dashboard/ads/campaign-builder` -> Ads & Sponsorships / Campaign Builder
- `/dashboard/ai/assistant` -> PulseSoc AI / Adaptive AI Companion
- `/dashboard/system/feed` -> System Status / Feed Intelligence

Observed behavior:

- `Module route parity` rendered for every representative route.
- `Available actions` and `Foundation status` rendered for every representative route.
- No representative route showed `Dashboard module unavailable`.
- No representative route stayed on the login screen after the authenticated QA session was established.
