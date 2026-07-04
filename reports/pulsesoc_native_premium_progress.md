# PulseSoc Native Premium + Entitlements Foundation

Date: 2026-07-04

## Scope

Built the native Premium and Entitlements foundation inside the separate `mobile-native/` Expo app.

The production WebView app, backend payment routes, Stripe/webhook logic, database tables, entitlement services, and Premium web pages were not changed.

## Reuse-First Implementation

The native app now consumes existing PulseSoc Premium contracts:

- `GET /api/premium/status`
- `POST /api/premium/checkout`
- `POST /api/premium/billing-portal`
- `GET /api/dashboard/economy/state`
- `/pulse/premium` web fallback
- `/pulse/profile` profile/theme/badge relationship

The backend remains authoritative for:

- Premium subscription status
- Founder membership
- Founder number assignment
- Premium badge visibility
- Profile theme eligibility
- Premium/creator capability checks
- Checkout session creation
- Billing portal session creation
- Payment provider state
- Webhook-confirmed grants/revocation
- Refunds, disputes, billing, and entitlement audit history

## Native Work Completed

Added `mobile-native/src/api/premium.ts`:

- Premium status fetch through existing `/api/premium/status`.
- Optional economy state fetch through existing `/api/dashboard/economy/state`.
- Cached status fallback through shared `src/core/cache`.
- Normalized plan, subscription, provider, Founder, and entitlement display state.
- Checkout handoff through existing `POST /api/premium/checkout`.
- Billing portal handoff through existing `POST /api/premium/billing-portal`.
- Web Premium hub fallback through the configured `PULSE_API_BASE_URL`.

Added `mobile-native/src/screens/PremiumScreen.tsx`:

- Native Premium status screen.
- Current plan display.
- Founder/Premium badge display.
- Entitlement list.
- Provider/subscription status display.
- Upgrade button using existing safe backend checkout flow.
- Billing portal button using existing backend portal flow.
- Web Premium hub fallback.
- Loading, cached/offline, error, and refresh states.
- App foreground/resume status refresh.
- Profile hook back to native Profile.

Updated native routing:

- Added `Premium` to the native root stack.
- Added `/pulse/premium` deep-link mapping.
- Added notification/deep-link routing for Premium links.
- Added Settings entry point.
- Added owner Profile entry point.

## What Was Intentionally Not Built

Native did not add:

- Stripe SDK
- Native checkout session creation
- Native billing session creation
- In-app purchase logic
- Local Premium entitlement grants
- Local Founder assignment
- Local entitlement revocation
- Payment/refund/dispute logic
- Webhook simulation
- Creator monetization tools

Those remain existing server/provider responsibilities.

## Device Verification

Not device-verified in this mission.

Static/local verification checks the install, TypeScript, Expo health, and premium audit gates. Real-device verification is still required for:

- Provider/browser checkout handoff
- Billing portal handoff
- App resume after provider return
- Deep-link handling from push taps on real devices
- iOS paid-digital policy behavior in production builds

## Files Added Or Updated

- `mobile-native/src/api/premium.ts`
- `mobile-native/src/screens/PremiumScreen.tsx`
- `mobile-native/src/navigation/AppNavigator.tsx`
- `mobile-native/src/navigation/types.ts`
- `mobile-native/src/navigation/linking.ts`
- `mobile-native/src/navigation/notificationRouting.ts`
- `mobile-native/src/screens/SettingsScreen.tsx`
- `mobile-native/src/screens/ProfileScreen.tsx`
- `mobile-native/src/components/ProfileHeader.tsx`
- `reports/pulsesoc_native_premium_progress.md`
- `scripts/pulsesoc_native_premium_audit.py`
- `reports/pulsesoc_native_progress.md`

## Current Gaps

- Backend `/api/premium/status` currently provides concise status fields, so the native entitlement list is a conservative display derived from server-owned status and optional economy-state records.
- Native checkout/billing handoff needs real-device QA.
- Native App Store paid-digital compliance still needs a dedicated release-policy pass before submission.

## Mandatory Next-Feature Recommendation

Recommendation: build Native Creator Studio Foundation next.

## Why Creator Studio Comes Next

- Native now has Feed, Reels, Status, Media Upload, Media Viewer, Marketplace, Search, Saved, Groups, Live viewer, and Premium/Entitlements foundations.
- Creator Studio is the orchestration layer that ties those creation surfaces together without requiring native Live hosting or calls yet.
- The existing backend already exposes creator state and creator AI surfaces that can be consumed as read/write native UI without duplicating business logic.
- Premium entitlement status is now available natively, which gives Creator Studio a safe way to show locked/active creator capabilities.

## Reusable Existing PulseSoc Logic

Reuse directly:

- `GET /api/dashboard/creator/state`
- `/pulse/creator/dashboard`
- `/pulse/creator-studio`
- `/api/pulse/creator-ai/<tool>`
- Existing creator metrics from `services/dashboard_creator_command_center.py`
- Existing post/reel/video/status/live/media owner counts
- Existing moderation and processing state
- Existing creator recommendations and event-bus summaries
- Existing media upload foundation
- Existing Feed Composer and Status Creator
- Existing Reels, Status, Live viewer, Media Viewer, Profile, Marketplace, Premium, and Search navigation

Existing database/business logic to preserve:

- `pulse_posts`
- `pulse_reels`
- `pulse_videos`
- `pulse_status` / `pulse_statuses`
- `pulse_status_views`
- `pulse_comments`
- `pulse_saved_items`
- `chat_media_uploads`
- live session tables
- moderation/review state
- premium entitlement state
- creator command-center metrics

## What Must Be Rebuilt Natively

- Creator Studio dashboard screen.
- Creator metric cards.
- Content shortcut cards into native Feed Composer, Reels, Status Creator, Media Upload, Live viewer/web Studio fallback, and Marketplace.
- Creator AI tool form using existing backend AI hooks.
- Processing/moderation warning states.
- Premium/locked capability display using native Premium status.
- Deep-link routing for `/pulse/creator/dashboard` and `/pulse/creator-studio`.
- Safe web fallback for unsupported Studio tools and native Live hosting.

## Dependencies And Blockers

Dependencies:

- Confirm `GET /api/dashboard/creator/state` payload shape.
- Keep Creator AI requests server-authoritative.
- Reuse shared native upload, media viewer, Premium status, routing, cache, and error components.

Blockers:

- Native Live hosting remains deferred.
- Creator monetization, payouts, paid courses, and in-app purchases require separate payment-policy planning.
- Some creator dashboard web tools may require web fallback until native equivalents exist.

## Risk Level

Risk: Medium.

Creator Studio touches many existing features, but backend risk stays low if native remains an owner-scoped client over existing dashboard APIs and keeps payments, hosting, and entitlement enforcement server-side.

## Complexity

Complexity: Medium-high.

The first slice should focus on dashboard visibility, routing, metrics, content shortcuts, and creator AI hooks. Monetization, scheduling writes, payouts, and native Live hosting should stay out of the first slice.

## Safest Implementation Plan

1. Inspect `GET /api/dashboard/creator/state`, `/pulse/creator/dashboard`, `/pulse/creator-studio`, and `/api/pulse/creator-ai/<tool>`.
2. Add a native creator API wrapper that reads existing creator state and calls creator AI hooks only.
3. Build native Creator Studio dashboard cards from server-owned state.
4. Reuse existing native navigation into Feed Composer, Reels, Status Creator, Media Upload, Marketplace, Live viewer, Premium, and Profile.
5. Add web fallback for unsupported Creator Studio tools, payouts, monetization, and Live hosting.
6. Add a report and audit verifying no duplicated creator ranking, monetization, payout, or entitlement logic.
7. Run standard verification before commit.
