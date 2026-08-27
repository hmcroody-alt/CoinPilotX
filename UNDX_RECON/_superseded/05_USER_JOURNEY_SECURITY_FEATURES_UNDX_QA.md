# User Journeys, Security, Feature Status, UNDX Capability, and Q/A Map

## User Journey Map

| Journey | Steps | Evidence sources | Permissions/data needed |
|---|---|---|---|
| New user | Signup -> session restore -> profile -> feed -> follow/create | `SignupScreen`, `LoginScreen`, `sessionStore.ts`, auth routes, feed/profile APIs | Valid account, session/cookie/refresh token, profile bootstrap |
| Creator | Profile -> create post/reel/status/live -> audience engagement -> analytics/monetization | Feed/Reels/Status/Live screens, media APIs, creator/business services | User identity, media upload permission, creator/seller/entitlement where required |
| Buyer | Marketplace -> product -> checkout -> Stripe PaymentSheet -> order -> fulfillment/returns | Marketplace screens/API, marketplace/payment services/tests | Buyer session, product inventory, Stripe PaymentIntent, order tables |
| Seller | Seller application -> store/profile -> listing -> inventory -> order -> payout | `SellerApplicationScreen`, seller/store/marketplace services | Seller eligibility, business/seller records, Stripe Connect/payout records |
| Premium | Premium center -> StoreKit purchase -> server verification -> entitlement -> restore/manage | `PremiumCenterScreen`, IAP/entitlement services/tests | Apple transaction/JWS, entitlement grant, provider subscription |
| Messaging | Messenger -> conversation -> messages/attachments/voice -> safety actions | `MessengerScreen`, `ChatScreen`, `api/messenger.ts`, `pulse_communications_v2` | Conversation membership, message permissions, upload contracts |
| Live host | Live Studio -> host session -> camera/mic -> comments/reactions/guest/music -> end/replay | `LiveStudioScreen`, `LiveHostSessionScreen`, `src/live/*`, live routes | Live eligibility, RTC credentials, media/audio permissions |
| Call | Start call -> server session -> recipient -> answer/decline -> room/token -> end/quality | `CallScreen`, `src/calls/*`, call routes/services | Call participants, VoIP push where configured, RTC token grants |
| Business operator | Business OS -> ads/orders/store/payments/insights/events/verification | `BusinessOsScreen`, Business OS APIs/services | Business membership/role, entitlements, seller status |
| UNDX | PulseAI/Messenger -> text -> provider/context -> action cards -> confirmation -> verified receipt | `PulseAiScreen`, `api/messenger.ts`, `services/undx_*` | Authenticated user, conversation context, capability flags, confirmation token for writes |

## Security Knowledge Map

| Area | Implementation evidence | Rule |
|---|---|---|
| Login/session | `mobile-native/src/api/auth.ts`, `sessionStore.ts`, mobile auth routes | Session API, cookies/refresh/access tokens, cached user state. |
| Face ID | `mobile-native/src/session/biometricAuth.ts`, `sessionStore.ts` | Refresh token is stored behind iOS keychain biometric access; sign-out preserves or clears biometric state depending path. |
| Password/account recovery | auth/recovery screens and token tables | Recovery tokens and email verification tables exist. |
| Device/security events | trusted-device/security event tables and account health services | Device recognition and account health/restriction surfaces exist. |
| Authorization | Business OS roles, page roles, messaging participants, admin gateway | Server remains authority for owner/member/admin/moderator actions. |
| Moderation/safety | report/block routes, comm_v2 reports/blocks/moderation events, Sentinel docs | Reports/blocks/moderation are explicit records and routes. |
| UNDX action safety | `undx_agent_policy.py`, `undx_tool_gateway.py`, `undx_verification.py` | Model proposes; gateway enforces capability, permission, confirmation, idempotency, verification, and kill switches. |
| Secrets | docs and memory emphasize no tokens/secrets in source/logs/reports | UNDX must not reveal provider credentials, tokens, hidden prompts, or private user data. |

## Payment + Commerce Knowledge Map

| System | Source | Verified architecture |
|---|---|---|
| Apple IAP / StoreKit | `services/business_os/entitlements/iap_apple.py`, StoreKit tests, native store config | iOS digital purchases and Premium entitlements use Apple purchase verification. |
| Stripe / PaymentSheet | marketplace payment services/API/tests | Physical goods use Stripe PaymentSheet/PaymentIntent and webhook-authoritative order completion. |
| Stripe Connect / payouts | `services/business_os/payments/connect_accounts.py`, seller payout services | Seller payout destination is Stripe connected-account based. |
| Ledger | `services/business_os/ledger/ledger.py`, ledger migrations/tests | Transactions/entries/balances centralize money movement. |
| Marketplace | `services/business_os/marketplace/*`, native marketplace screens/APIs | Products, orders, returns, refunds, disputes, inventory, seller dashboards. |
| Store | `services/business_os/store/*`, native Store Dashboard | Storefront/products/collections/policies/versioning distinct from marketplace product rows. |

## Feature Status Map

Status is source-evidence based, not live QA.

| Feature | Status | Evidence |
|---|---|---|
| Home Feed | PARTIALLY READY | Native `HomeScreen`, feed APIs/tests; active visual parity history. |
| Reels | PARTIALLY READY | Native `ReelsScreen`, `/api/pulse/reels*`, media/audio tests. |
| Status/Stories | PARTIALLY READY | Status APIs, native `StatusScreen`, creation audits. |
| Messaging | PARTIALLY READY | Native Messenger/Chat, communications V2, tests; known parity work ongoing. |
| Calls | PARTIALLY READY | Call room hooks/routes/tests; physical-device proof is required for final claims. |
| Live | PARTIALLY READY | Native host/viewer screens, live APIs, RTC/audio tests; physical remote audio proof remains required. |
| Groups/Rooms | PARTIALLY READY | Groups screen/API and communications V2 foundations; detail parity not fully proven here. |
| Business OS | PARTIALLY READY | Many services/screens/tests; some gated modules and known money-risk docs. |
| Marketplace | PARTIALLY READY | Screens/API/services/tests; physical checkout and fulfillment must be verified live. |
| Store | PARTIALLY READY | Store services/screens; eligibility/linking boundaries need careful evidence. |
| Advertising | PARTIALLY READY | Backend hierarchy and native manager; ads intelligence extensive. |
| Premium/IAP | PARTIALLY READY | StoreKit/entitlement foundation and tests; App Store Connect config/runtime verification needed. |
| Crypto/Alerts | PARTIALLY READY | Alert engine, crypto services, native screens/tests. |
| Notifications | PARTIALLY READY | Notification system tables/routes/services and native notification screens. |
| Search | UNDER DEVELOPMENT | Native `SearchScreen` and routes exist; not enough evidence for production-ready. |
| UNDX | PARTIALLY READY | Extensive server/native policy/action/brain code, but capability rollout and live provider behavior need verification. |
| Arena/Education | UNDER DEVELOPMENT | Large route/table footprint but not part of current native release proof. |

## UNDX Capability Map

| Capability area | Can do, source-backed | Cannot claim/do without verification |
|---|---|---|
| Identity/company answers | State PulseSoc/CoinPlotXAI/founder facts from `undx_company_identity.py` | Revenue, valuation, user count, partnerships, funding, production readiness without source. |
| Read-only product intelligence | Build domain readings from registered tool results and product context | Access unrelated private data or unregistered sources. |
| Action cards | Render confirmations, receipts, failures, unsupported-capability states | Treat missing confirmation token as actionable. |
| Account/notifications/crypto/social actions | Capability registry/gateway/verifiers include patterns for alerts, notification prefs, saved posts, following, feed reactions/deletes, reels/profile prefs | Execute writes if flags/permissions/confirmation/verifier fail. |
| Business OS actions | `services/business_os/undx_actions/*` defines request/decision/receipt/confirmation/tool registry tables and workflow services | Bypass business/seller/payment policy or owner permissions. |
| Long-running missions | `undx_mission_runtime.py` provides durable worker/task graph primitives | Execute product tools without request context or governed gateway. |
| Knowledge corpus | Existing `backend/undx/config/*.yaml` packs exist | Treat this recon as final training data; it is source intelligence only. |

## Questions / Answers Collection

| User question | Expected UNDX answer source | Required data | Permission level |
|---|---|---|---|
| What is PulseSoc? | `services/undx_company_identity.py` | Canonical company/product facts | Public |
| Who founded PulseSoc? | `services/undx_company_identity.py` | Founder fact | Public |
| What can UNDX do for me? | capability registry + native/action-card contracts | Enabled capabilities for account | Authenticated |
| What are my notifications? | notification APIs/tables/preferences | User notification records | Self account |
| Show my saved posts. | saved item APIs/tables | Saved records for current user | Self account |
| Create a business. | Business OS business APIs | User/business eligibility and required fields | Authenticated, may require confirmation |
| Why did my crypto alert trigger? | alert engine events/history | Alert rule/event history | Self account |
| Where is my order? | marketplace/orders APIs | Buyer/seller order records | Buyer/seller authorized |
| Who follows me? | profile/social graph APIs | Follower records | Authenticated/self or public rules |
| Explain my Premium benefits. | entitlements/premium APIs | Entitlement catalog and user grants | Authenticated |
| Can you delete this post? | UNDX capability registry/gateway/verifier + post ownership | Target post id, owner auth, confirmation | Self owner + confirmation |
| Can you refund this order? | marketplace/payments/refund services | Order/payment state, seller/admin permission | Seller/admin; high-risk confirmation |
| Are my payouts connected? | Stripe Connect/payout services | Seller payout account state | Seller owner |
| Why can’t I go Live? | live eligibility/access routes | Eligibility flags/reasons | Authenticated |
| Is this feature production ready? | release reports + live QA evidence | Current verified release evidence | Must cite source; otherwise say unknown |

## Unknown Areas Requiring More Investigation

1. Current production deployment variables and provider dashboard state.
2. Runtime route-pack enablement and feature-flag values in the deployed app.
3. Actual production database migration status and schema drift.
4. Physical-device evidence for calls, Live audio/video, camera, microphone,
   Bluetooth, push, and background behavior.
5. App Store Connect/TestFlight metadata/build state.
6. Exact production data quality for posts, profiles, marketplace, orders,
   notifications, and UNDX memory.
7. Performance under realistic production traffic.
8. Whether Arena/Education surfaces are intended for current native release.
9. Current public/legal approved marketing claims and privacy disclosures.
10. Which UNDX capabilities are enabled for normal users versus QA/admin users.
