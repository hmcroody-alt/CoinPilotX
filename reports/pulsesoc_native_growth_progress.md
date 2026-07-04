# PulseSoc Native Growth Center Progress

Date: 2026-07-04

## Scope

Built the native Growth Center foundation as a new client surface over the existing PulseSoc Growth backend.

The implementation keeps the backend authoritative for growth account state, growth scoring, wallet state, promotion readiness, audience modeling, targeting, ad billing, provider handoff, campaign launch, moderation, and promotion review.

Do not duplicate backend business logic. Growth Center remains server authoritative; native only presents existing state and routes unsupported actions to safe PulseSoc web/provider flows.

## Existing PulseSoc Logic Reused

- `GET /api/pulse/growth`
- `/pulse/growth`
- `/pulse/promote`
- `services/pulsesoc_growth_engine.py`
- Existing growth account, workspace, wallet, audience, score, preference, and risk-profile database behavior
- Existing premium status and entitlement display pattern through `/api/premium/status`
- Existing native cache helper under `mobile-native/src/core/cache.ts`
- Existing navigation/deep-link and notification routing patterns
- Existing Feed/Post, Reels, Profile, Marketplace, Premium, Creator Studio, Settings, and web fallback surfaces

## Native Work Added

- `mobile-native/src/api/growth.ts`
  - Fetches and normalizes `/api/pulse/growth`.
  - Caches read-only growth state.
  - Builds safe PulseSoc web fallback URLs.
  - Builds promotion fallback paths without creating promotions in native.
- `mobile-native/src/screens/GrowthCenterScreen.tsx`
  - Native Growth Center dashboard.
  - Growth score/status summary.
  - Wallet/budget summary where backend returns it.
  - Audience/targeting preview where backend returns it.
  - Campaign overview from backend modules.
  - Analytics snapshot.
  - Promotion context cards for selected content.
  - Safe fallback buttons for campaign launch, wallet funding, billing, and unsupported advanced tools.
  - Loading, refresh, offline-cache, and error states.
- Native navigation:
  - `GrowthCenter` stack route.
  - `/pulse/growth` linking.
  - notification/deep-link routing for `/pulse/growth` and `/pulse/promote`.
  - Settings entry.
- Native promote shortcuts:
  - Feed post card to Growth Center.
  - Post detail card to Growth Center.
  - Reel player to Growth Center.
  - Owner profile header to Growth Center.

## Intentionally Not Built

These remain on existing PulseSoc web/provider/backend flows:

- Native campaign launch writes
- Native wallet funding
- Native ad billing
- Native targeting configuration
- Native ad review
- Native refund/provider handling
- Local growth score calculation
- Local audience model calculation
- Local premium/growth entitlement decisions

## Verification Notes

- Static code verification and Expo checks are required for this checkpoint.
- Real-device QA was not claimed in this report.
- Billing/provider handoff and promotion fallback behavior still need simulator or real-device testing.
- No production WebView routes were intentionally modified.

## Current Gaps

- Growth dashboard quality depends on the actual `/api/pulse/growth` payload returned for each account.
- Promotion launch remains a safe web fallback until native paid-promotion UX is separately scoped.
- Wallet and billing flows remain provider/web handoffs.
- Deep-link handling for individual promotion IDs can be expanded after the backend exposes stable native-safe payload contracts.

## Recommended Next Feature

Recommendation: build Native Intelligence + Alerts Foundation next.

## Why This Comes Next

- Growth Center, Creator Studio, Premium, Notifications, Feed, Search, and Profile are now native enough to support a native intelligence and alerts hub.
- The production codebase already includes `/api/dashboard/intelligence/state`, `/dashboard/intelligence`, crypto alert routes, central notification delivery, and `services/alert_engine.py`.
- Alerts are already tied to notifications and deep links, so native handling can reuse the existing notification center, preferences, and routing work.
- This feature gives users direct access to high-value PulseSoc intelligence without starting native LiveKit hosting or calls too early.

## Reusable APIs, Code, Database, And Business Logic

- `GET /api/dashboard/intelligence/state`
- `/dashboard/intelligence`
- `/dashboard/intelligence/<subsystem_key>`
- `/dashboard/crypto/alerts`
- `/api/crypto/alerts`
- `services/alert_engine.py`
- `services/notification_service.py`
- `services/privacy_intelligence_engine.py`
- `services/global_intelligence_graph.py`
- `services/universal_intelligence_fabric.py`
- Existing `alert_rules`, `user_alert_rules`, notification, delivery-job, crypto/news/market cache, and intelligence graph tables
- Existing premium and permission checks
- Existing alert dedupe, delivery, push, in-app notification, and deep-link behavior

## What Must Be Rebuilt Natively Next

- Native Intelligence/Alerts dashboard.
- Alert list and alert detail where APIs support it.
- Alert create/edit entry points where APIs support safe native payloads.
- Crypto/market alert cards and watchlist preview.
- Notification/deep-link routing into alert/intelligence screens.
- Loading, empty, error, offline, locked, and unavailable states.
- Safe web fallback for advanced alert creation, premium intelligence, and unsupported intelligence subsystems.

## Risk And Complexity

Risk: Medium-high.

Reason: intelligence and market-alert surfaces are accuracy-sensitive and notification-sensitive. Native must present server-owned state and avoid financial advice or client-side alert evaluation.

Complexity: Medium-high.

## Safest Implementation Plan

1. Inspect current intelligence and alert payloads.
2. Build a read-first native Intelligence/Alerts screen.
3. Reuse notification preferences and deep-link routing.
4. Keep alert evaluation, crypto/market logic, premium gates, and delivery entirely backend-owned.
5. Add safe web fallbacks for unsupported intelligence tools.
6. Add a static audit that fails if native code evaluates alert triggers, market advice, delivery eligibility, or premium intelligence access locally.
