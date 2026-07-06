# PulseSoc Native User Dashboard Parity Foundation

Date: 2026-07-06

Scope: native User Dashboard foundation parity against the current production PulseSoc dashboard.

## Result

Dashboard foundation parity: 92%.

The native dashboard now represents the current production dashboard module universe as a migration map. This pass focused on route parity, module coverage, status labels, access locks, quick actions, and safe navigation, not final UI/UX polish.

Production WebView routes changed: no.

## Production Dashboard Source Inspected

- `templates/dashboard.html`
- `services/pulse_dashboard_mission_control.py`
- dashboard command-center service imports in `bot.py`
- existing native dashboard files in `mobile-native/src/api/dashboard.ts` and `mobile-native/src/screens/UserDashboardScreen.tsx`

## Native Module Groups Now Represented

- Account Command Center
- Pulse Network
- Creator Studio
- Intelligence
- Economy & Earnings
- Pulse Radio & Media
- Crypto Command Center
- Moderation / Safety
- Ads & Sponsorships
- PulseSoc AI
- System Status

Admin / Moderator Only modules are intentionally not exposed in the owner User Dashboard foundation.

## Native Module Coverage

- 135 production user-visible dashboard widgets are represented in `mobile-native/src/data/dashboardModules.ts`.
- 11 production dashboard groups are represented.
- 12 production quick actions are represented.
- Locked, beta, partial, coming soon, and production-ready labels are carried into the native foundation.
- Premium, creator, and seller-required modules show locked state and route to native unlock/management surfaces where possible.

## Reused Native Surfaces

- Profile
- Verification Center
- Account Health
- Account/Security/Settings
- Activity Inbox
- Messenger
- Groups
- Reels
- Status
- Live Viewer
- Marketplace
- Seller Store
- Buyer Orders
- Premium
- Creator Studio
- Content Planner / Scheduler / Draft Studio
- Growth Center
- Intelligence / Alert Management
- Safety Hub
- Saved
- Camera Studio
- Pulse AI

## Safe Web Fallbacks

Fallback remains intentional for modules where the current native app does not yet own the advanced workflow:

- Pulse Radio and music workflow
- long-form video library and advanced video management
- advanced ads/campaign provider workflows
- advanced dashboard system diagnostics
- provider billing, payout, and payment pages
- advanced AI workspace tooling
- advanced crypto/provider data tools

## What Is Not UI/UX Polish Yet

- no final dashboard animation pass
- no final desktop/tablet responsive redesign
- no advanced dashboard personalization
- no final accessibility polish pass
- no advanced module-level data visualizations
- no final release-grade visual density tuning

This is a foundation parity pass. Polish comes after the dashboard module structure and route coverage are complete.

## Current Gaps

- Persistent authenticated dashboard QA fixtures are still needed for repeatable rich visual data.
- Physical iPhone-only camera, push, installed deep-link, and media behavior remain release QA gaps.
- Some advanced production dashboard cards route to existing native umbrella screens rather than dedicated native detail screens.
- Some provider-owned workflows remain safe web fallback by design.

## Next Dashboard Task

ONE next dashboard task ONLY: add dashboard module detail shells for fallback-heavy groups.

Reason: the dashboard now represents the full production module universe, but several modules still land on umbrella native screens or web fallback. Lightweight native detail shells would preserve route parity while keeping backend/provider logic authoritative.
