# PulseSoc Native Intelligence + Alerts Progress

Date: 2026-07-04

## Scope

Built the native Intelligence + Alerts foundation as a read-first native client over existing PulseSoc Intelligence and Alert backends.

The backend remains authoritative for intelligence streams, event collection, forecasts, sources, alert rules, crypto/market alert evaluation, premium intelligence access, notification delivery, cadence, moderation, privacy, AI providers, and business rules.

Do not duplicate backend business logic. Native only presents existing state, routes users through existing native surfaces, and uses safe PulseSoc web fallback for unsupported or sensitive operations.

## Existing PulseSoc Logic Reused

- `GET /api/dashboard/intelligence/state`
- `GET /api/crypto/alerts`
- `/dashboard/intelligence`
- `/dashboard/intelligence/<subsystem_key>`
- `/dashboard/crypto/alerts`
- `/dashboard/crypto/alerts/create`
- Existing `services/dashboard_intelligence_command_center.py`
- Existing `services/alert_engine.py`
- Existing `services/dashboard_crypto_command_center.py`
- Existing `services/pulsesoc_notification_system.py`
- Existing notification badge/preference APIs
- Existing Premium status API
- Existing Growth Center, Creator Studio, Feed, Search, Profile, and Notification navigation patterns
- Existing native cache helper under `mobile-native/src/core/cache.ts`

## Native Work Added

- `mobile-native/src/api/intelligence.ts`
  - Fetches and normalizes `/api/dashboard/intelligence/state`.
  - Fetches and normalizes `/api/crypto/alerts`.
  - Caches intelligence state and alert lists.
  - Opens safe PulseSoc web fallbacks.
  - Formats display-only alert labels without evaluating alert triggers.
- `mobile-native/src/screens/IntelligenceCenterScreen.tsx`
  - Native Intelligence dashboard.
  - Stream/forecast card overview.
  - Crypto/market alert overview.
  - Alert detail route.
  - Notification unread/badge summary.
  - Premium status summary.
  - Refresh and app-resume refresh.
  - Offline cache fallback.
  - Loading/error/offline states.
  - Navigation to Notifications, Preferences, Growth, Premium, Creator Studio, Search, and Profile.
  - Safe web fallback for advanced Intelligence, alert creation, collector/provider administration, and unsupported operations.
- Native navigation:
  - `IntelligenceCenter` stack route.
  - `/dashboard/intelligence/<subsystem?>` deep-link support.
  - notification/deep-link routing for `/dashboard/intelligence`, `/pulse/intelligence`, `/dashboard/crypto/alerts`, and `/pulse/crypto/alerts`.
  - Settings, Growth Center, and Premium entry points.

## Intentionally Not Built

These remain backend/web/provider-owned:

- Native alert trigger evaluation
- Native crypto/market price interpretation
- Native buy/sell/hold or investment recommendations
- Native provider polling
- Native alert delivery rules
- Native alert dedupe windows
- Native premium intelligence access grants
- Native AI provider routing
- Native collector/source administration
- Advanced alert editing and provider administration

## QA-Driven Development Rule

From this checkpoint forward, every major native feature should be validated through the QA browser or device/simulator when available before the next major feature is considered complete.

The required cadence is:

1. Build the feature.
2. Run automated verification.
3. Launch the native app where tooling is available.
4. Test navigation, loading/error/offline states, deep links, notifications, back navigation, responsiveness, and visual consistency.
5. Record issues.
6. Fix blockers.
7. Repeat until no significant issues remain.
8. Only then commit and push.

Browser/device behavior must not be marked verified unless it was actually tested.

## Verification Notes

- Static code verification and Expo checks are required for this checkpoint.
- Real QA browser/device behavior was attempted but not completed in this environment.
- Expo web launch was blocked because the project does not include `react-native-web`, `react-dom`, or `@expo/metro-runtime`; those dependencies were not added because this mission is native/hybrid-native scoped.
- Android device QA was blocked because `adb` is unavailable.
- iOS simulator QA was blocked because `xcrun simctl` is not available in the active developer toolchain.
- Real QA browser/device behavior was not claimed in this report.
- Notification tap routing is statically wired but still needs real-device push/deep-link QA.
- Advanced alert web fallback behavior still needs browser/device validation.
- No production WebView routes were intentionally modified.

## Current Gaps

- Native alert create/edit/delete remains deferred to web fallback.
- Alert history details remain web fallback until stable native-safe detail payloads are scoped.
- Intelligence subsystem detail screens are routed through the shared Intelligence screen with web fallback for subsystem-specific tools.
- Provider freshness, market correctness, forecast reliability, and delivery status remain server-owned and not inferred locally.

## Recommended Next Feature

Recommendation: create a Native Feature Parity + QA Readiness Report next, before adding another major module.

## Why This Comes Next

- The native app now covers many major PulseSoc pillars: Feed, Messenger, Notifications, Profile, Reels, Status, Marketplace, Search, Saved, Groups, Live viewer, Premium, Creator Studio, Growth, and Intelligence.
- Continuing to add major modules without QA will increase integration risk.
- A parity report can compare the current WebView PulseSoc product against native coverage and identify the remaining gaps by user impact.
- This is the right checkpoint to shift from build-first to QA-driven development.

## Reusable APIs, Code, Database, And Business Logic For The Next Step

- Existing native progress reports and audit scripts.
- Existing production route inventory in `bot.py`.
- Existing native screens, navigation, API wrappers, cache helpers, and deep-link routing.
- Existing WebView/production feature inventory reports.
- Existing backend route/database/service ownership for each PulseSoc pillar.

## What Must Be Built Natively Next

- A report, not a major feature:
  - Native-vs-WebView feature matrix.
  - Device/browser QA status per native feature.
  - Missing parity gaps.
  - Highest-risk integration gaps.
  - Recommended hardening order.
  - QA browser/device checklist.
  - Release blockers before replacing WebView surfaces.

## Risk And Complexity

Risk: Low.

Complexity: Medium.

Reason: the work is mostly reconnaissance and reporting, but it must be accurate and based on current code rather than roadmap assumptions.

## Safest Implementation Plan

1. Inspect current production PulseSoc routes and major feature surfaces.
2. Inspect current `mobile-native` screens, API wrappers, navigation, and reports.
3. Build a parity matrix across Feed, Messenger, Notifications, Profile, Reels, Status, Marketplace, Search, Saved, Groups, Live, Premium, Creator, Growth, Intelligence, Calls, Camera, and settings.
4. Mark each feature as native-ready, partial, web fallback, not started, or device-QA required.
5. Identify blockers before native can become the primary client.
6. Add a static audit for the parity report.
