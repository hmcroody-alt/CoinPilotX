# 05 — USER JOURNEYS

Thirteen end-to-end journeys traced through the real code. Each step names the mobile
screen, the API endpoint, the service module that owns the logic, the table(s) written,
and the gate or permission that must pass.

Conventions:
- **BREAK** — the journey stops here in a default production configuration.
- **RISK** — it works, but a documented failure mode makes it fragile.
- `UNVERIFIED` — I did not confirm this; treat as a hypothesis, not a fact.

Two facts govern most of the breaks below, so they are stated once:

- **G1 — `DIGITAL_COMMERCE_ENABLED` defaults to `false`** (`mobile-native/src/api/config.ts`).
  Set for Apple guideline 3.1.1. It hides Premium checkout and billing, marketplace
  checkout, and payout onboarding **in the native app**. The server endpoints exist and
  work; the entry points are simply not rendered.
- **G2 — every `BUSINESS_OS_*` flag is blank** in `.env.example:433-469` (28 flags,
  verified). `services/business_os/schema_bootstrap.ensure_all_once()` only runs if at
  least one is truthy, so the Business OS tables are never created. 203
  `/api/business-os` routes + 37 gateway routes + 66 `/admin/business-os` routes are
  non-functional. Because `services/business_os_commerce_routes.py:127` swallows
  `ensure_schemas()` failures in a bare `try/except`, the routes still *register* — so the
  symptom is a **500 on a missing relation**, not a clean 404. That is the exact shape of
  the 2026-08-07 incident recorded in `schema_bootstrap.py`
  (`relation "ledger_balances" does not exist`).

A third, subtler gate applies to Business/Presence modules:

- **G3 — the launch readiness gate** (`mobile-native/src/launch/readiness.ts`). A
  deny-list of three module ids, each with its evidence written down:
  `business:events` = BUILDING, `business:customers` = COMING_SOON,
  `business:team` = COMING_SOON, `presence:businessOs` = BUILDING. Unknown ids default to
  READY (deliberately). `GATED_ROUTES` additionally hard-refuses the `BusinessOsEvents`
  route regardless of how it was reached (deep link, restored state).

---

## JOURNEY 1 — New user: install → account → first feed

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Launch, cold start | `src/launch/` | — | `launch/readiness.ts` | — | — |
| 2 | Sign up | `screens/SignupScreen.tsx` | `POST /api/mobile/auth/register` | bot.py auth block | `users` (PK `user_id` per `AUTO_PK_TABLES`) | email/handle validation via `src/auth/signupValidation.ts` |
| 3 | Confirm email | — | `POST /api/mobile/auth/confirm-email`, `GET /api/mobile/auth/confirmation-status`, `POST /api/mobile/auth/resend-confirmation`, `POST /api/mobile/auth/change-confirmation-email` | email queue | email delivery job table | Brevo key |
| 4 | Email actually sends | — | — | `email_worker.py` → `bot.process_email_delivery_jobs(limit=20)` | delivery job table | **RISK** — worker runs every 10s but **writes no heartbeat**, so an outage is invisible to `worker_heartbeats` |
| 5 | Session established | `src/session/sessionStore.ts` | `GET /api/mobile/auth/session` | `pulseApi()` | session tables | token pair in expo-secure-store, `AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY` |
| 6 | Onboarding steps | — | `POST /api/onboarding/step` (exists, `bot.py`) | bot.py | onboarding state | — |
| 7 | Push permission | — | `/api/push/*` (8 routes) | `services/push_*` | push token tables | OS permission; FCM/APNs/VAPID keys |
| 8 | First feed render | feed screens | `/api/pulse/*` (412 routes) | `services/pulse_feed_engine.py` | feed/post tables | — |

**BREAK (partial, step 8):** the synchronous feed read works. But `pulse_worker.py` —
which runs `pulse_feed_engine.process_pending_jobs()` — is **not in the Procfile**. Any
feed work that is enqueued rather than computed in-request never completes in production.
The same worker publishes scheduled Space AI posts
(`services/pulse_ai/space_post_scheduler.publish_space_ai_post`, imported at
`bot.py:93634`), so a brand-new user's Spaces stay empty of AI seed content.

**RISK (step 2-8):** `/api/mobile/auth/*` lives in `bot.py`, but presence, settings and
messaging live in fail-soft blueprint packs. If a pack fails to import, boot still
succeeds and those endpoints 404 silently. Check `/health/routes` (`bot.py:115206`,
unauthenticated) before debugging as a routing bug.

---

## JOURNEY 2 — Returning user: relaunch → silent auth → feed

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Read stored envelope | — | — | `src/session/sessionStore.ts` | — | expo-secure-store |
| 2 | Optional biometric unlock | — | — | `src/session/biometricAuth.ts` | — | expo-local-authentication; credentials in a **separate keychain slot** |
| 3 | Attach bearer | — | — | `src/api/pulseApi.ts` | — | only sent when `accessTokenExpiresAt > Date.now() + 5000` |
| 4 | Silent refresh | — | `POST /api/mobile/auth/refresh` | `pulseApi()` | session/refresh tables | body `{refresh_token, source:"native_automatic_refresh"}`; single-flight via `refreshPromise`; 12s timeout |
| 5 | Refresh outcome | — | — | — | — | `"refreshed" \| "invalid" \| "temporary" \| "unavailable"`; a 401 triggers exactly **one** retry |
| 6 | Invalid → forced logout | `LoginScreen.tsx` | — | `registerSessionInvalidationHandler()` | — | — |
| 7 | Deletion reversal | — | login path | `bot.cancel_scheduled_account_deletion(cur, user_id)` (`bot.py:1268`); companion `cancel_pending_deletion` exported by `services/pulse_settings_routes.py` | account deletion table | logging in cancels a pending deletion |
| 8 | Feed | feed screens | `/api/pulse/*` | `pulse_feed_engine` | — | — |

**RISK (step 4):** the refresh path is the single point of failure for every returning
session, and the `"temporary"` vs `"invalid"` distinction is what prevents a transient
network blip from logging out the entire user base. `pulseApi.ts` also coalesces
in-flight GETs (`inFlightReads`), which is what stops a cold start from firing duplicate
reads.

**BREAK (step 7):** none, but note `presence`/`settings` are pack-dependent (packs #2, #3).

---

## JOURNEY 3 — Creator: capture → post → reel → schedule

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Compose | `src/create/`, `screens/CreatorStudioScreen.tsx` | — | — | — | camera/mic permission |
| 2 | Capture | `src/media/`, `expo-camera ~17.0.10` | — | — | — | `NSMicrophoneUsageDescription` asserted by the `native-build` CI job |
| 3 | Upload media | — | `/api/pulse/*` upload routes | `services/media_storage.py` (boto3) | media tables | R2 keys |
| 4 | Thumbnail / transcode | — | — | `media_worker.py` job types `generate_thumbnail`, `process_video` | media job tables | **BREAK** |
| 5 | Publish post/reel | — | `/api/pulse/*`, `/api/reels/*` (12) | `services/pulse_post_*`, `pulse_reels_*` | post/reel tables | — |
| 6 | Schedule a post (web) | web Creator Command | `POST /api/dashboard/content-planner/item` | bot.py planner | content planner table | **BREAK** |

**BREAK (step 4) — the most consequential gap in the product.** `media_worker.py` (847
lines, `WORKER_NAME = "coinpilotx-media-engine"`, interval 5s, batch 25, max 3 attempts)
handles `{"generate_thumbnail", "process_video", "finalize_live_replay"}`. It is **absent
from the Procfile**. Every media job enqueued in production stays pending forever. Videos
upload but are never transcoded; posts never get thumbnails.

**BREAK (step 6) — and it is an honest one.** `bot.py:8240-8244` renders the Post
Scheduler with the "Bulk Schedule" and "Recurring Post" buttons `disabled`, with title
attributes stating the backend does not exist, plus the text: *"Holiday scheduling, smart
rescheduling, recurring posts, and publish-now remain unavailable until real
scheduler/publisher services are connected. No fake publish success is shown."* The form
itself only creates a planner row — the card explicitly says *"It does not publish until
the existing publishing pipeline is explicitly connected."* This is a deliberately
truthful stub, not a bug.

---

## JOURNEY 4 — Viewer: browse → watch → react → save → report

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Feed / discovery | `src/feed/`, `src/discovery/` (26 files) | `/api/pulse/*` | `pulse_feed_engine`, `pulse_ranking_*`, `pulse_discovery_*` | — | — |
| 2 | Reels playback | `src/reels/` | `/api/reels/*` | `pulse_reels_*` | — | preload harness under `tests/protection/reels_preload_*` |
| 3 | Reel audio | `core/reelsAudioSession.ts` | — | — | — | **on the frozen expo-av allowlist** (see below) |
| 4 | React / like | — | `/api/pulse/*` | — | reaction tables | — |
| 5 | Save | — | `src/api/saved.ts` | — | saved tables | — |
| 6 | Report content | — | `POST /api/pulse/report`, or scoped variants: `/api/pulse/groups/posts/<id>/report`, `/api/pulse/music/<id>/report`, `/api/media/<id>/report`, `/api/pulse/marketplace/listings/report`, `/api/pulse/messages/<id>/report` | `services/moderation_*` | report tables | authenticated |
| 7 | Ad impression | — | `POST /api/pulse/ads/impression`, `/click`, `/viewability`, `/event`; `GET /api/pulse/ads/placements` | `services/pulse_ads_*` | ad event tables | — |

**RISK (step 3):** `core/reelsAudioSession.ts` is one of exactly **six** files permitted to
call `Audio.setAudioModeAsync`. The `expo_av_global_audio_mode` rule in
`config/realtime-audio-protected-paths.json` is `"frozen_at_baseline": true` with
`"max_allowed_paths": 6`; the allowlist is `core/pulseRadio.ts`,
`core/reelsAudioSession.ts`, `core/voiceMessagePlayback.ts`, `calls/callSignalMedia.ts`,
`screens/MusicScreen.tsx`, `screens/ChatScreen.tsx`. A seventh call site fails CI. The
failure this prevents is a reel stealing the audio session from a live call — green build,
passing tests, silent production.

---

## JOURNEY 5 — Messaging: start → send → media → moderate

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Start a thread | `src/api/messenger.ts` | `POST /api/pulse/messages/start` | comm v2 | conversation tables | pack #1 |
| 2 | List / open conversation | `screens/ChatScreen.tsx` | `/api/pulse/communications/v2/conversations/*` | `pulse_communications_v2/routes.py` | conversation, message | pack #1 |
| 3 | Send message | `ChatScreen.tsx` | `.../messages` (POST) | same | message tables | pack #1 |
| 4 | Realtime delivery | — | `.../realtime/stream` (SSE) | same | — | pack #1 |
| 5 | Presence + typing | — | `/api/pulse/presence/*` (9 endpoints) | `services/presence_routes.py` | presence tables | pack #2 |
| 6 | Voice-note playback | `core/voiceMessagePlayback.ts` | — | — | — | expo-av allowlist |
| 7 | AI smart replies / summary | — | `.../ai/smart-replies`, `.../ai/summary` | `services/pulse_ai_service.py` (**modified in tree**) | — | AI provider keys |
| 8 | Report a message | — | `POST /api/pulse/messages/<id>/report`, `/api/messages/report` | `services/moderation_*` | report tables | — |
| 9 | Media in chat | — | upload routes | `services/media_storage.py` | media tables | **RISK** — thumbnails need media_worker |

**RISK (whole journey):** all of messaging, communities, presence heartbeat, SSE and call
*initiation* live in the single `pulse_communications_v2` pack — **158 endpoints**
(73 GET, 77 POST, 5 PATCH, 2 DELETE, 1 `.route`), `register()` at
`pulse_communications_v2/routes.py:1698`. It is registered inside `_load_route_pack`'s
`try/except` (`bot.py:1247`). One import error takes the entire communication surface to
404 while the app boots green and reports healthy. The loader does log
`ROUTE_PACK_IMPORT_FAILED` with the text *"every endpoint in this pack will 404"* — but
nothing is alerting on it.

Note: the blueprint is created with **no `url_prefix`** (`Blueprint("pulse_communications_v2", __name__)`,
line 17). `API_PREFIX = "/api/pulse/communications/v2"` is interpolated into most
decorators, but some routes are registered at absolute paths outside that prefix — e.g.
`@comm_v2_blueprint.post("/api/calls/start")` at line 1301 and
`@comm_v2_blueprint.get("/api/pulse-ai/conversation")` at line 615. Those paths therefore
also die with the pack.

---

## JOURNEY 6 — Calls and Live

### 6a — 1:1 / group call

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Start from a thread | `ChatScreen.tsx` | `POST /api/pulse/communications/v2/conversations/<id>/{voice\|video}/start` | comm v2 | call tables | pack #1 |
| 2 | Or start directly | `screens/CallScreen.tsx` | `POST /api/calls/start` (comm v2, line 1301) | `services/call_engine.py` | call tables | pack #1 |
| 3 | Ring / seen | — | `POST /api/calls/<id>/ring-seen` | call engine | — | — |
| 4 | Accept / decline | `CallScreen.tsx` | `/api/calls/<id>/accept`, `/decline` | call engine | — | — |
| 5 | Mint RTC token | — | `POST /api/calls/<id>/join-token` | `call_engine` → agora-token-builder | — | `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`; **server-side only** |
| 6 | Media session | `src/calls/` (19 files), `calls/callSignalMedia.ts` | — | `react-native-agora 4.6.2` | — | Agora native owns the audio session |
| 7 | Connected / quality / end | — | `/api/calls/<id>/connected`, `/quality`, `/end`, `/events`, `/status` | call engine | call tables | — |
| 8 | VoIP wake for a killed app | — | `/api/calls/voip-token`, `/voip-token/revoke` | push layer | voip token table | **BREAK** |

**BREAK (step 8):** `NATIVE_CALLKIT_ENABLED` defaults **false** in
`mobile-native/src/api/config.ts`, and `react-native-callkeep` is not a dependency. The
`/api/calls/voip-token` endpoints exist and the client file references them, but incoming
calls **cannot wake a terminated iOS app**. In practice a call only rings if the app is
foregrounded or recently backgrounded. This is the single largest UX gap in Journey 6.

### 6b — Live stream

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Go live | `screens/LiveStudioScreen.tsx`, `LiveHostSessionScreen.tsx` | `POST /api/pulse/live/start` | `services/call_engine.py`, `services/mux_live_service.py` | live tables (note the legacy column name `livekit_room`) | Agora + Mux keys |
| 2 | Host RTC token | — | `GET /api/pulse/live/<id>/rtc/token` → `api_pulse_live_agora_token(live_id)` at `bot.py:49321`, calling `call_engine.generate_agora_live_token(...)` at `bot.py:49360` | `call_engine` | — | server-side mint |
| 3 | Native publish | `src/live/` (30), `src/live-audio/` (4) | `POST /api/pulse/live/<id>/native-publish` | — | — | `can_publish` / `canPublish` — **an audio-protected backend diff pattern** |
| 4 | Discovery | `screens/LiveScreen.tsx` | `GET /api/pulse/live-now` | `pulse_live_now_cards` | — | — |
| 5 | Viewer joins | `LiveScreen.tsx` | `POST /api/pulse/live/<id>/join`, `/join-status`, `/state` | — | — | — |
| 6 | Guest request → approve → publish | — | `/join-request`, `/join-requests`, `/join-requests/<id>/<action>`, `/guests/<id>/<action>`, `/guests/<id>/publish-complete`, `/guests/<id>/leave` | — | guest tables | host permission |
| 7 | Live chat + reactions | — | `/live/<id>/chat`, `/chat/<msg>/<action>`, `/react` | — | live chat tables | — |
| 8 | Cloud recording / RTMP out | — | — | `services/agora_cloud_recording_service.py`, `agora_media_push_service.py` | — | Agora cloud-recording key/secret |
| 9 | End stream | — | `POST /api/pulse/live/<id>/end` | — | live tables | — |
| 10 | Replay finalization | — | — | `media_worker.py` job `finalize_live_replay` | replay tables | **BREAK** |

**BREAK (step 10):** `finalize_live_replay` is a `media_worker` job type, and
`media_worker.py` is not in the Procfile. **Live streams can be started, watched and
ended, but replays are never finalized in production.** Note the irony: five of the 22
protection suites are Agora-specific (`agora_cloud_recording`,
`agora_direct_live_contract`, `agora_mux_bridge`, `agora_replay_mux_contract`,
`agora_rtc_provider_contract`, `agora_token_generation`) — the contracts are guarded, but
the process that executes them is undeployed.

**Naming trap:** `bot.py:47550` renders `<label>Agora channel<code>{livekit_room}</code></label>`.
LiveKit is retired; the column name survives. Anyone grepping for `livekit` will find
legacy state strings and error codes at `bot.py` lines 46013, 46670-46683, 47489-47500,
48086, 48640-48646 and may wrongly conclude LiveKit is live. It is not — there is no
LiveKit SDK in `requirements.txt` or `mobile-native/package.json`, and the LiveKit
`AVAudioSession` patch has been deleted from `mobile-native/patches/`.

**Contract:** `live_startup_trace_contract` in the protection manifest requires **30
events with 12 fields** to be emitted during live startup. Also
`required_lease_discipline.must_not_contain: ["audioOwnerIdRef"]` — the retired LiveKit
adapters held JS audio leases; with Agora, session ownership is enforced natively, so
reintroducing a JS lease is itself the defect.

---

## JOURNEY 7 — Buyer: browse → cart → offer → checkout → order → return

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Browse | `screens/MarketplaceScreen.tsx` | `GET /api/pulse/marketplace/search?…` | `services/marketplace_*` | listing tables | — |
| 2 | Product detail | `MarketplaceProductScreen.tsx` | listing routes | same | — | — |
| 3 | Add to cart | `MarketplaceCartScreen.tsx` | `POST /api/pulse/marketplace/cart` | `services/marketplace_cart_routes.py` (pack #4, `register()` at 940) | cart tables | **`MARKETPLACE_CART_ENABLED`** |
| 4 | Edit / remove line | — | `PATCH\|POST /cart/<line_id>`, `DELETE /cart/<line_id>` | same | cart tables | — |
| 5 | Confirm price | — | `POST /cart/<line_id>/confirm-price` | same | — | anti-price-drift |
| 6 | Validate | — | `POST /cart/validate` | same | — | — |
| 7 | Checkout options | `MarketplaceCheckoutScreen.tsx` | `GET /cart/checkout-options` | same | — | — |
| 8 | Make an offer | — | `POST /api/pulse/marketplace/offers` | `marketplace_offers_routes.py` (pack #5, 709) | offer tables | — |
| 9 | Offer lifecycle | — | `/offers/<id>/{accept,decline,withdraw,counter}` | same | offer tables | role checks |
| 10 | Checkout | `MarketplaceCheckoutScreen.tsx` | `POST /cart/checkout` or `/offers/<id>/checkout` or `POST /api/pulse/payments/checkout` | **`services/pulse_payment_router.py`** | order + payment tables | **BREAK (G1)** |
| 11 | Pay | `src/api/stripePaymentSheet.ts` | Stripe | `@stripe/stripe-react-native 0.61.0` | — | `PHYSICAL_ITEM_TYPES` → Stripe |
| 12 | Orders | `BuyerOrdersScreen.tsx`, `OrdersRoute.tsx` | `GET /api/pulse/payments/orders/<transactionId>` | `services/orders_*` | order tables | — |
| 13 | Return | — | `POST /api/pulse/marketplace/returns`, `/returns/<id>`, `/message`, `/resolve`, `/escalate` | `marketplace_returns_routes.py` (pack #6, 470) | return tables | role checks |

**BREAK (step 10) — G1.** `DIGITAL_COMMERCE_ENABLED` is false by default, which hides the
marketplace checkout entry point in the native app. The server path is sound: physical
goods are `PHYSICAL_ITEM_TYPES = {"marketplace_physical", "real_world_service"}` and route
to Stripe, which is Apple-compliant. So this is a **client-side gate on a working server
path** — flipping the flag is the whole fix, contingent on App Review posture.

**The router is fail-closed and worth understanding.** `services/pulse_payment_router.py`
is the single server-side authority; the client never picks a provider. Providers:
`PROVIDER_APPLE_IAP`, `PROVIDER_STRIPE`, `PROVIDER_STRIPE_CONNECT`,
`PROVIDER_INTERNAL_LEDGER`. Item-type partition:
`DIGITAL_ITEM_TYPES = {"ad_credits","premium_subscription","business_subscription"}` →
Apple IAP on iOS; `WALLET_SPEND_ITEM_TYPES = {"post_boost","marketplace_ad"}` → internal
ledger; `PHYSICAL_ITEM_TYPES` → Stripe; `PAYOUT_ITEM_TYPES = {"creator_payout","seller_payout"}`
→ Stripe Connect; `PROMO_ITEM_TYPES = {"promo_credit_grant"}`. **Anything unenumerated is
classified `ambiguous` and REFUSED.** Adding a product without registering its item type
produces a hard refusal rather than a mischarge — the correct trade.

**RISK (steps 3-13):** cart, offers and returns are three separate fail-soft packs. Any
one can vanish independently, producing a partial commerce surface (e.g. a cart you can
fill but no returns flow).

---

## JOURNEY 8 — Seller: apply → list → sell → fulfil → get paid

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Apply | `screens/SellerApplicationScreen.tsx` | `POST /api/pulse/seller/application/draft`, `/submit`, `/withdraw`, `GET /api/pulse/seller/application` | bot.py seller block | application tables | KYC |
| 2 | Upload documents | — | `POST /api/pulse/seller/application/documents`, `/documents/<id>/remove` | `services/media_storage.py` | document tables | R2 |
| 3 | Alternate apply path | `MarketplaceManagerScreen.tsx` | `POST /api/pulse/marketplace/seller/apply` | same | — | — |
| 4 | Identity | — | `src/api/sellerIdentity.ts` | `services/verification_*` | verification tables | — |
| 5 | Create listing | `SellerListingComposerScreen.tsx` | `POST /api/pulse/marketplace/listings/create` | `services/marketplace_*` | listing tables | seller role |
| 6 | Attach media / digital files | — | `POST /api/pulse/marketplace/media/attach`, `/digital-files/upload` | `media_storage` | media tables | **RISK** — thumbnails need media_worker |
| 7 | Commercial terms | — | `POST /api/pulse/marketplace/commercial/terms` | — | terms tables | — |
| 8 | Submit / manage listing | — | `POST /api/pulse/marketplace/seller/listings/<id>/submit`, `/<id>/<action>`, `GET \|PATCH /<id>`, `GET /seller/listings?…` | — | listing tables | — |
| 9 | Store front | `SellerStoreScreen.tsx`, `SellerStoreRoute.tsx`, `src/api/storeDashboard.ts` | `/api/business-os/*` storefront, store-policies, listing-drafts, inventory, seller-dashboard, reports | `services/business_os/commerce_gateway.py` (37 routes, `API_PREFIX="/api/business-os"`, `ROUTES` at line 277) | Business OS tables | **BREAK (G2)** |
| 10 | Orders received | `OrdersManagerScreen.tsx` | `GET /api/pulse/payments/seller/orders` | `services/orders_*` | order tables | — |
| 11 | Fulfil | `src/api/marketplaceFulfillment.ts` | fulfilment routes | — | fulfilment tables | — |
| 12 | Connect payouts | — | `POST /api/pulse/payouts/connect`, `GET /api/pulse/payments/seller/connect/status` | Stripe Connect | connect account tables | **BREAK (G1)** |
| 13 | Payouts | `src/api/sellerPayouts.ts` | `GET /api/pulse/payments/seller/payouts?…` | `services/marketplace_payout_scheduler` (imported `bot.py:100377`) | payout tables | `PAYOUT_ITEM_TYPES` → Stripe Connect |

**BREAK (step 9) — G2.** The seller dashboard, storefront versions, store policies,
inventory and listing drafts are the Business OS commerce gateway. With all
`BUSINESS_OS_*` flags blank, the tables do not exist. Worse, `register()` at
`services/business_os_commerce_routes.py:127` calls `gw.ensure_schemas()` inside a bare
`try/except`, so the 37 routes register anyway — the seller gets a **500 on a missing
relation**, not an honest "unavailable".

**BREAK (step 12) — G1.** Payout onboarding is hidden in the native app.

**Note on the payout "scheduler":** despite the name,
`services/marketplace_payout_scheduler` is invoked **in-request**, not on a timer. There
is no periodic payout job. `UNVERIFIED` whether a payout can therefore be missed if no
request triggers it.

---

## JOURNEY 9 — Premium subscriber

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | View plans | `screens/PremiumCenterScreen.tsx`, `src/api/premium.ts`, `premiumCenter.ts` | `GET /api/premium/status` (`bot.py:11814`), `/api/premium/status-center` (`bot.py:21762`) | `services/premium_*` | subscription tables | — |
| 2 | Economy state | — | `GET /api/dashboard/economy/state` | dashboard | — | — |
| 3 | Start checkout | — | `POST /api/premium/checkout` (`bot.py:11733`) | `pulse_payment_router` | payment tables | **BREAK (G1)** |
| 4 | Provider decision | — | — | `pulse_payment_router` | — | `premium_subscription` ∈ `DIGITAL_ITEM_TYPES` → **Apple IAP on iOS**, Stripe on web |
| 5 | iOS purchase | — | StoreKit 2 | `expo-iap ^4.3.1` | — | App Store Connect keys |
| 6 | Receipt verification | — | — | `services/apple_iap_*` (JWS) | entitlement tables | `BUSINESS_OS_ENTITLEMENTS` blank → **RISK** |
| 7 | Manage billing | — | `GET /api/premium/billing-portal` (`bot.py:11762`) | Stripe billing portal | — | **BREAK (G1)** |
| 8 | Entitlement applied | — | `/pulse/premium/*` (8 routes) | `services/premium_*` | entitlement tables | — |

**BREAK (steps 3, 7) — G1.** Premium checkout and the billing portal are both explicitly
named as surfaces hidden by `DIGITAL_COMMERCE_ENABLED=false`. **In the shipping native
app there is currently no way to buy Premium.** The routing logic behind it is correct and
Apple-compliant — the block is a client flag.

**RISK (step 6):** entitlement persistence lives partly in Business OS
(`BUSINESS_OS_ENTITLEMENTS` is one of the 28 blank flags). `UNVERIFIED` whether the core
`/pulse/premium/*` entitlement check reads a `bot.init_db()` table or a Business OS table;
if the latter, verification succeeds and the entitlement is then unreadable.

---

## JOURNEY 10 — Advertiser: campaign → wallet → delivery → attribution → reports

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Ads home | `AdsManagerScreen.tsx`, `AdsPortalScreen` via `src/api/adsPortal.ts` | `/api/pulse/ads/*` | `services/pulse_ads_*` | ad tables | — |
| 2 | Build campaign | `AdsCampaignWizardScreen.tsx` | campaign routes | `pulse_ads_*` | campaign tables | — |
| 3 | Audiences | `AdsAudiencesScreen.tsx`, `src/api/adsAudiences.ts` | audience routes | — | audience tables | — |
| 4 | Creatives | `src/api/adsCreatives.ts`, `AdsLibraryScreen.tsx` | creative routes | — | creative tables | media upload |
| 5 | Policy review | `AdsPolicyCenterScreen.tsx` | `/api/pulse/ads/appeals`, `/creatives/<id>/appeal`, `/api/pulse/ads/reports` | `services/pulse_ads_*` | policy tables | `BUSINESS_OS_AD_*` guardrails |
| 6 | Fund wallet | `AdsWalletScreen.tsx` | `POST /api/pulse/ads/accounts/<id>/wallet/funding-session` | `pulse_payment_router` | ledger tables | **BREAK (G1 + G2)** |
| 7 | Wallet limits / auto-topup | — | `/api/pulse/ads/wallet/limits`, `/auto-topup` | — | ledger tables | **BREAK (G2)** |
| 8 | Wallet ledger views | — | `/wallet/transactions`, `/wallet/events`, `/wallet/invoices` | Business OS ledger | `ledger_balances` etc. | **BREAK (G2)** |
| 9 | Delivery | viewer devices | `GET /api/pulse/ads/placements`, `POST /impression`, `/click`, `/viewability`, `/event` | synchronous delivery path | ad event tables | LIVE |
| 10 | Charging | — | — | **synchronous delivery path only** | ledger | by design |
| 11 | Attribution | — | — | `ads_worker` → `pulse_ads_worker_service` every 300s | attribution tables | LIVE |
| 12 | Billing reconciliation | — | — | `ads_worker` every 600s, **report-only** | — | LIVE |
| 13 | Reporting rollups | `AdsReportsScreen.tsx`, `AdsInsightsScreen.tsx` | `/api/pulse/ads/*` reports | `ads_worker` every 300s | report tables | LIVE |

**BREAK (steps 6-8) — G2, and this is the documented production incident.** The advertiser
wallet is Business OS ledger. `schema_bootstrap.py`'s own docstring records 2026-08-07:
`relation "ledger_balances" does not exist` behind
**`/api/business-os/advertising/wallet`** — precisely this path. With
`BUSINESS_OS_LEDGER` and `BUSINESS_OS_ADVERTISING` blank, that failure recurs by default.
**Advertisers can build campaigns but cannot fund them.**

**Worth stating plainly:** `ads_worker` is one of only four deployed workers and it works.
`pulse_ads_worker.py`'s docstring is the design invariant — *"It never charges wallets —
the synchronous delivery path owns money and is idempotent."* The worker does jobs,
operations sweep, orphan recovery (re-queues anything `processing` >10 min), attribution,
report-only reconciliation, and reporting. It also deliberately reattaches a stdout
handler in `_configure_logging()` because `import bot` installs a `RotatingFileHandler`
that would otherwise swallow every worker log line on Railway.

---

## JOURNEY 11 — Crypto user: watchlist → alert → fire → portfolio

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Market board | crypto screens | `/api/crypto/*` (25 routes) | `services/live_market_service.py` | market tables | CoinGecko |
| 2 | Watchlist | `src/api/watchlists.ts` | watchlist routes | — | watchlist tables | — |
| 3 | Create alert | `CryptoAlertCenterScreen.tsx`, `AlertManagementScreen.tsx` | `POST /api/crypto/alerts`; options at `/api/crypto/alerts/options` | `services/alert_engine.py` | alert tables | — |
| 4 | Mobile alert CRUD | — | `GET\|POST /api/mobile/crypto/alerts`, `/<alert_id>`, `/history` | same | alert tables | — |
| 5 | Manage | — | `/api/alerts/<id>/{pause,resume,delete,test}`, `/api/crypto/alerts/<id>/{duplicate,history}` | same | alert tables | — |
| 6 | Channel readiness | — | `GET /api/alerts/channel-readiness?push_permission=…`, `/api/alerts/test/<channel>` | push layer | — | OS push permission is passed to the server |
| 7 | Market sampling | — | — | `alert_worker` → `live_market_service.get_crypto_market` → `market_observations.record_board` (limit 80) | market observation tables | every 45s |
| 8 | Alert evaluation | — | — | `alert_engine.evaluate_all_active_alerts(limit=500, worker_name="alert_worker")` | alert event tables | every 45s |
| 9 | Auto-signals | — | — | `auto_signals_service.process_enabled_users(limit=200)` | signal tables | every 45s |
| 10 | Delivery | — | `/api/alerts/events?limit=…` | push + email | — | FCM/APNs/VAPID, Brevo |
| 11 | Portfolio | `CryptoPortfolioScreen.tsx` | `/api/mobile/crypto/portfolio`, `/portfolio/history` | `services/portfolio_*` | portfolio tables | — |

**This is the healthiest journey in the system.** It is the original CoinPilotX product,
it has a deployed worker, and every step has a live owner.

**RISK (steps 7-9):** `alert_worker` does four things serially in one 45s loop — including
`sentinel_runtime.run_scheduled_ingestion()`. A slow Sentinel ingestion or a CoinGecko
timeout delays alert evaluation for every user, because there is no isolation between the
four stages. `ALERT_WORKER_BATCH_LIMIT` is 500; `UNVERIFIED` what happens if active alerts
exceed 500 within one cycle — whether the remainder is deferred fairly or starved.

**RISK (step 10):** email delivery depends on `email_worker`, which does not heartbeat.

---

## JOURNEY 12 — Admin / Moderator

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Admin console | web | `/api/admin/*` (47), `/admin/users/*` (15) | `services/admin_*` | — | admin session |
| 2 | Route health | — | `GET /health/routes` (`bot.py:115206`) | `_record_route_pack` / `ROUTE_PACK_STATUS` (`bot.py:1206-1225`) | — | **UNAUTHENTICATED** |
| 3 | Review reports | web | the ~26 `*/report` endpoints (Journey 4 step 6) | `services/moderation_*` | report tables | moderator role |
| 4 | Act on a user | web | `/admin/users/*` | `services/admin_*` | strike tables | guarded by `tests/protection/test_admin_action_accountability.py` |
| 5 | User appeals | mobile/web | `POST /api/dashboard/account/strikes/<id>/appeal`, `/account/verification/appeal` | — | appeal tables | — |
| 6 | Ads/marketplace appeals | — | `/api/business-os/advertising/appeals`, `/api/business-os/marketplace/appeals` | Business OS | — | **BREAK (G2)** |
| 7 | Business OS admin | web | `/admin/business-os/*` (66 routes) | `services/business_os_web.py` | Business OS tables | **BREAK (G2)** |
| 8 | Sentinel intelligence | — | `/api/admin/sentinel/*` (18 GET endpoints) | `services/sentinel/api.py` | sentinel tables | **BREAK — never registered** |
| 9 | Ops worker health | — | worker health views | `worker_heartbeats` (`bot.py:110186`, writer at `bot.py:117602`) | `worker_heartbeats` | **RISK** |

**BREAK (step 8) — the strangest finding in the codebase.**
`services/sentinel/api.py` defines
`sentinel_bp = Blueprint("sentinel", __name__, url_prefix="/api/admin/sentinel")` with 18
admin-gated GET endpoints, and its docstring says it is *"DELIBERATELY NOT REGISTERED with
bot.py in V1"* — the stated reasons being that `bot.py` is under concurrent change and
protected by the audio diff gate, and that exposing a privileged surface is an owner
decision (SC10). Meanwhile `alert_worker` calls
`sentinel_runtime.run_scheduled_ingestion()` **every 45 seconds**. So Sentinel (53
modules, fed by 11 `intelligence_collectors/`) is continuously ingesting data that **no
one can read over HTTP**. The write path runs; the read path does not exist.

**RISK (step 2):** `/health/routes` exposes internal subsystem names and registration
failures with no authentication. `UNVERIFIED` whether an upstream proxy restricts it.

**RISK (step 9) — ops observability is structurally misleading.** Of four deployed
workers, `email_worker` never heartbeats and `undx_worker` only does so when
`UNDX_WORKER_HEARTBEAT_ENABLED` is set. Separately,
`services/backend_management_registry.py:161` and
`services/notification_health_engine.py:110` reference `command_center_worker` — a
process that exists as a standalone Flask skeleton in
`services/command_center_worker/` (13 files) but is in no Procfile and is imported by
nothing. **The ops UI can list a worker that does not exist and omit an outage of one
that does.** There is a protection suite named
`test_operations_metric_truthfulness.py`, which suggests this class of problem is known.

---

## JOURNEY 13 — UNDX user

| # | Step | Screen | Endpoint | Service | Tables | Gate |
|---|---|---|---|---|---|---|
| 1 | Ask UNDX (mobile) | `src/undx/` (6 files), `src/api/undxSelfKnowledge.ts` | `GET /api/pulse-ai/conversation` — registered by comm v2 at `pulse_communications_v2/routes.py:615` | `services/pulse_ai_service.py` | conversation tables | **pack #1** |
| 2 | Web/priv UNDX chat | web | `/api/pulse/assistant/chat` (`bot.py:28687`) and `POST /api/undx/chat` (`bot.py:28795`) | `undx_router.py` | — | **`require_super_user_api()`** |
| 3 | Provider selection | — | — | `undx_router.py` (502 lines) picks OpenAI/Claude/Gemini/DeepSeek/Groq server-side, OpenAI last | — | keys never reach the client |
| 4 | Agent council | web | `/api/undx/agent-council` | `services/undx_agent_policy.py` (**modified in tree**) | — | super user |
| 5 | Action center (mobile) | `UndxActionCenterScreen.tsx`, `UndxCapabilitiesScreen.tsx`, `src/undx/actionCards.ts` | `/api/business-os/undx/{tools,permissions,policies,requests,receipts,confirmations,emergency-stop}` and `/undx/marketplace/listings/{draft,publish/plan,publish/execute}` — **17 such routes in `bot.py`** | Business OS UNDX | Business OS tables | **BREAK (G2)** |
| 6 | Mission execution | — | — | `undx_worker.py` → `undx_mission_runtime.poll_once()` every 60s | mission tables | **RISK** |
| 7 | Repo diffs | web | `/api/undx/kernel/{propose,scan,validate,apply,git}` | `undx_execution_kernel.py` (845 lines) | `undx_execution_log.jsonl` | writes only after the literal phrase **`APPROVE UNDX WRITE`**; blocks `.env`, `.git`, venvs, secrets, sqlite paths |
| 8 | Desktop connector | — | `/api/undx/desktop-connector/<path>` | `undx_desktop_connector.py` (1171 lines) | `.undx/` logs | — |

**BREAK (step 5) — G2.** The entire mobile UNDX Action Center — tools, permissions,
policies, requests, receipts, confirmations, emergency stop, and AI-drafted marketplace
listings — is served from `/api/business-os/undx/*`. `BUSINESS_OS_UNDX_ACTIONS` and
`BUSINESS_OS_UNDX_DEFAULT_ORG_ID` are both blank. **The most visible AI feature in the
mobile app is dark by default.**

**BREAK (step 2):** `/api/undx/chat` is `require_super_user_api()`-gated, so ordinary
users have no route to the full UNDX chat. Their only path is step 1, which depends on the
comm v2 pack.

**CORRECTION to an earlier assumption:** `/api/pulse-ai/conversation` does **not** 404. It
is absent from `bot.py` but is registered by the comm v2 blueprint at absolute path
(`routes.py:615`), because that blueprint is created with no `url_prefix`. It is live —
conditional on pack #1 importing successfully.

**RISK (step 6) — deployment hazard, worth acting on.** `undx_worker.py` is in the
Procfile and imports `services/undx_mission_runtime.py`, which is **untracked in git**
(along with `tests/undx_agent/test_safety_precedence.py` and
`scripts/undx_railway_variable_audit.py`). If the current tree is deployed without
committing that file, **the UNDX worker crashes on import at boot**. Branch is
`codex/emergency-live-audio-recovery`; also modified and uncommitted: `bot.py`,
`services/pulse_ai_service.py`, `services/undx_agent_policy.py`,
`services/undx_architecture.py`, `services/undx_brain/config.py`, `undx_worker.py`.

---

## SUMMARY — where journeys dead-end

| Journey | Verdict |
|---|---|
| 1 New user | Works; async feed + Space AI seeding dark (`pulse_worker` undeployed) |
| 2 Returning user | **Works** |
| 3 Creator | Posts publish; **thumbnails/transcode never run**; scheduling is an honest stub |
| 4 Viewer | **Works** |
| 5 Messaging | Works — entirely dependent on one fail-soft pack (158 endpoints) |
| 6 Calls & Live | Calls work but **cannot wake a killed iOS app**; **live replays never finalize** |
| 7 Buyer | **Checkout hidden by `DIGITAL_COMMERCE_ENABLED=false`** |
| 8 Seller | Listing works; **dashboard/storefront 500s (G2)**; payout onboarding hidden (G1) |
| 9 Premium | **No way to buy Premium in the native app** (G1) |
| 10 Advertiser | Campaigns build, delivery works, **wallet cannot be funded (G2)** |
| 11 Crypto | **Works** — the healthiest journey |
| 12 Admin | Core admin works; **Sentinel API never registered**; worker health is misleading |
| 13 UNDX | Chat works via comm v2; **Action Center dark (G2)**; full chat is super-user only |

**Three root causes explain almost every break:**
1. `BUSINESS_OS_*` flags all blank → G2 → Journeys 8, 10, 12, 13.
2. `DIGITAL_COMMERCE_ENABLED=false` → G1 → Journeys 7, 8, 9.
3. `media_worker.py` and `pulse_worker.py` missing from the Procfile → Journeys 1, 3, 6.

The first two are configuration decisions with a plausible rationale (App Review posture,
staged rollout). The third looks like an omission — two worker files exist, are complete,
heartbeat correctly, and are simply not declared as processes.
