# PulseSoc / UNDX Full Recon Operations Report

Read-only reconnaissance for later UNDX knowledge-corpus work. This is not a
training file and does not assert runtime production health beyond source
evidence.

## Scope and Evidence

- Workspace: `/Users/hmcherie/Desktop/CoinPilotX`
- Branch observed: `codex/premium-crypto-intelligence`
- Existing dirty state observed before this recon: untracked `UNDX_RECON/`
- Source types inspected: repository tree, backend Flask monolith, service
  packages, migrations, native screens/API clients, docs, tests, and prior
  recon files already present in `UNDX_RECON/`.
- No production code was intentionally changed.

## Architecture Summary

| System | Purpose | Location | Status | Dependencies |
|---|---|---|---|---|
| Flask monolith | Main web/API app, PulseSoc web surfaces, auth, social, commerce, admin | `bot.py` | Active, very large | SQLite/Postgres adapter, service modules, workers |
| Service modules | Product-specific domain logic and route packs | `services/` | Active | `services/db.py`, `bot.py` registrations |
| Business OS | Seller/business commerce, ads, store, orders, payments, insights, verification | `services/business_os/`, `mobile-native/src/api/business*.ts` | Active, mixed readiness by module | Ledger, Stripe, StoreKit/IAP, entitlements |
| Communications V2 | Canonical conversations, participants, messages, attachments, presence, reports | `pulse_communications_v2/` | Active foundation | Database tables, auth/session, native Messenger |
| Native app | Parallel Expo/React Native PulseSoc implementation | `mobile-native/` | Active production-update track | Expo, React Native, native iOS/Android folders |
| Legacy mobile app | Older mobile track | `mobile/` | Present; not primary native target | Expo/RN |
| Pulse AI / UNDX | Assistant, action cards, policy, capability gateway, memory, knowledge | `services/pulse_ai/`, `services/undx_*.py`, `services/undx_brain/`, `backend/undx/config/` | Active, guarded | Provider router, capability registry, DB memory, kill switches |
| Media/audio | Uploads, R2/CDN readiness, Reels, Live, calls, voice playback | `services/*media*`, `mobile-native/src/media`, `mobile-native/src/core`, `mobile-native/src/live`, `mobile-native/src/calls` | Active, protected subsystem | Agora/LiveKit history, AVAudioSession owner, Mux/R2 |
| Workers | Alerts, media, Pulse worker, ads worker, email, command center | `alert_worker.py`, `media_worker.py`, `pulse_worker.py`, `pulse_ads_worker.py`, `email_worker.py`, `services/command_center_worker/` | Active/partial by worker | Railway/env vars, provider APIs |
| Admin / Sentinel | Admin auth, moderation, security intelligence, audits | `services/sentinel/`, `tests/admin_auth`, admin routes in `bot.py` | Active | Admin gateway, external intel providers |

## Product Identity

The canonical PulseSoc definition is in `services/undx_company_identity.py`.
It defines PulseSoc as an intelligent digital ecosystem connecting social
interaction, creator tools, business operations, communication, commerce,
advertising, safety, and AI through shared identity and platform infrastructure.

Canonical company facts from source:

- Legal company: `CoinPlotXAI Inc.`
- Primary product: `PulseSoc`
- Founder: `Roody Cherie`, Founder & CEO
- Product categories: social platform, creator economy, business platform,
  marketplace, advertising platform, communications ecosystem, AI platform.

The source explicitly forbids UNDX from inventing revenue, valuation, user
count, funding, partnerships, customer names, feature production-readiness, or
Android availability without verified source.

## Major Product Areas

| Product area | Purpose | Primary backend | Native surfaces | Evidence-backed status |
|---|---|---|---|---|
| Home Feed / Posts | Timeline, post creation, reactions, comments, saves, media | `bot.py` `/api/pulse/feed`, `/api/pulse/posts*` | `HomeScreen`, `PostDetailScreen`, `api/feed.ts` | PARTIALLY READY; active native parity work exists |
| Reels / Video | Short-form and long-form video feed, reactions, comments, saves | `/api/pulse/reels*`, `/api/reels*`, `/api/pulse/videos*` | `ReelsScreen`, `api/reels.ts` | PARTIALLY READY |
| Status / Stories | Ephemeral status rail, create/view/react/reply/share | `/api/pulse/status*` | `StatusScreen`, `api/status.ts` | PARTIALLY READY |
| Messaging / Chat | Direct, group, room, UNDX conversations, attachments, safety | `pulse_communications_v2`, legacy message tables/routes | `MessengerScreen`, `ChatScreen`, `api/messenger.ts` | PARTIALLY READY |
| Calls | Voice/video calls, tokens, push/call lifecycle | `services/pulsesoc_communications_engine.py`, call routes | `CallScreen`, `src/calls/*` | PARTIALLY READY; physical proof remains critical |
| Live | Native host/viewer, comments, guest, audio/video, replay | Live routes in `bot.py`, `models/live_session.py`, `src/live/*` | `LiveScreen`, `LiveHostSessionScreen`, `LiveStudioScreen` | PARTIALLY READY |
| Groups / Communities | Groups, rooms, memberships, moderation | group/community route packs and communications V2 | `GroupsScreen`, room/group native components | PARTIALLY READY |
| Presence / Page OS | Page identity for artists/business/organizations | `services/pulsesoc_pages.py`, `/api/pages/*` | `PresenceHubScreen`, `PageScreen`, page edit/team screens | PARTIALLY READY |
| Business OS | Business dashboard and seller/operator surfaces | `services/business_os/*` | `BusinessOsScreen`, Business sub-screens | PARTIALLY READY |
| Marketplace / Store | Listings, products, cart, checkout, orders, seller tools | `services/business_os/marketplace`, marketplace routes, store services | `MarketplaceScreen`, product/cart/checkout/seller screens | PARTIALLY READY |
| Payments / Premium | IAP, Stripe, entitlements, wallets, payouts | entitlements, payments, ledger, Stripe handlers | `PremiumCenterScreen`, `MoneyLayerScreen`, `AdsWalletScreen` | PARTIALLY READY |
| Crypto / Alerts | Portfolio, watchlists, crypto alerts, market observations | alert engine, crypto services, dashboard crypto | `PortfolioScreen`, `WatchlistsScreen`, `CryptoAlert*` | PARTIALLY READY |
| Advertising | Campaigns, ads, audiences, wallet, reporting | `services/business_os/advertising`, `ads_intelligence` | `AdsManagerScreen`, ads sub-screens | PARTIALLY READY |
| Notifications | Push/in-app/email jobs, counters, prefs, deeplinks | notification tables/routes/services | `NotificationCenterScreen`, `NotificationPreferencesScreen` | PARTIALLY READY |
| Search | Native search/deeplink routing | search routes/API | `SearchScreen`, `api/search.ts` | UNDER DEVELOPMENT |
| Settings / Account / Security | Login, signup, session, Face ID, recovery, preferences | mobile auth routes, account/security services | `LoginScreen`, `SignupScreen`, `SettingsScreen`, account screens | PARTIALLY READY |
| UNDX | Intelligence companion, policy, actions, knowledge, cards | `services/undx_*`, `services/undx_brain`, `backend/undx/config` | `PulseAiScreen`, `UndxActionCenterScreen`, `UndxCapabilitiesScreen` | PARTIALLY READY |

## Route and API Findings

The existing `UNDX_RECON/03_API_MAP.md` statically extracted 2,007 route
registrations. Largest route families: Admin, Business OS, Arena, Messaging,
Account/Auth/Security, Crypto/Alerts/Portfolio/Watchlists, Feed/Posts/Media,
Payments/Premium/Subscriptions, Dashboard, Ads, Groups/Communities, and Live.

Important caveat: route registration does not prove runtime readiness. Some
route packs are optional, gated, flag-dependent, or rely on environment/provider
configuration.

## Database Findings

Static table extraction found roughly 994 `CREATE TABLE IF NOT EXISTS`
declarations across `bot.py`, migrations, service schema modules, and
communications models. This includes duplicate declarations for the same table
in migrations and runtime schema bootstraps.

Dominant table families:

- Account/auth/security: `users`, sessions, auth events, failed login controls,
  recovery tokens, trusted devices, security events.
- Social: `pulse_posts`, reels/videos/audio/music/status/media/saved tables.
- Messaging: legacy `pulse_*` chat tables plus `comm_v2_*` tables.
- Calls/Live: `communication_calls*`, `comm_v2_live_streams`, live session
  models, livestream eligibility/access.
- Business OS: ledger, entitlements, marketplace, store, advertising, ads
  intelligence, business, orders, events, localization, merchant automation,
  verification, performance, recommendations, UNDX actions.
- Commerce/payments: Stripe, creator wallets/ledger/payouts, marketplace orders,
  refunds/returns/disputes, provider webhooks, IAP entitlements.
- UNDX/AI: `pulse_ai_*`, `ai_*`, `business_os_undx_*`, `global_intelligence_*`.
- Admin/Sentinel/Arena/Education/Progress: extensive auxiliary table sets.

## Security Summary

- Session/auth surfaces exist in web and native (`/api/mobile/auth/session`,
  native `sessionStore.ts`, `biometricAuth.ts`).
- Face ID protects refresh credentials through SecureStore/keychain options in
  native session code.
- UNDX action execution is guarded by policy flags, capability registry,
  confirmation tokens, permission scopes, idempotency, and verification.
- Admin/security surfaces include admin gateway tests, Sentinel docs, fraud and
  device-intelligence documentation.
- The source repeatedly models “fail closed” for sensitive operations and “do
  not fabricate” for unsupported facts/capabilities.

## Commerce and Payment Summary

- iOS digital goods/Premium/ad credits use Apple StoreKit/IAP paths with
  server-side verification and entitlement normalization.
- Physical Marketplace payments use Stripe/PaymentSheet/Apple Pay/Card/Link
  where eligible; memory and docs warn not to replace this with StoreKit.
- Business OS uses a ledger foundation and provider webhook events to normalize
  payments, refunds, payouts, and balances.
- Existing docs identify money-risk areas that need source-specific verification
  before release claims: ledger concurrency, refund idempotency/delta handling,
  capture atomicity, and seller/store eligibility boundaries.

## UNDX Summary

UNDX has multiple layers:

- Canonical identity and company grounding: `services/undx_company_identity.py`
- Fact classification: `services/undx_fact_policy.py`
- Agent contracts: `services/undx_agent_contracts.py`
- Capability registry/policy/gateway/tools/verification: `services/undx_*`
- Domain reasoning and cross-domain reading: `services/undx_domain_reasoning.py`,
  `services/undx_cross_domain.py`
- Durable task/mission runtime: `services/undx_mission_runtime.py`
- Brain modules: `services/undx_brain/*`
- Existing config/training-like source packs: `backend/undx/config/*.yaml`
- Native rendering/action cards/context: `mobile-native/src/undx/*`,
  `mobile-native/src/api/undx*.ts`, `mobile-native/src/api/messenger.ts`

UNDX can read and reason over governed sources and can propose/execute only
registered capabilities when flags, authorization, confirmation, idempotency,
and verification allow. It must not silently invent unsupported actions or
complete actions without backend verification.

## Readiness Caveat

This recon is static. It does not prove:

- deployed environment variables are correct,
- live provider credentials are present,
- App Store/TestFlight state,
- physical-device audio/camera behavior,
- Apple/Stripe webhook reachability,
- production data quality,
- performance under load.

Those require live QA and provider dashboards.
