# PulseSoc System Knowledge Map

## Repository Structure

| Location | Purpose | Notes |
|---|---|---|
| `bot.py` | Main Flask monolith and route registry | Contains many route handlers and runtime table bootstraps. |
| `services/` | Domain services and route packs | Business OS, UNDX, Pulse AI, notifications, media, presence, security, payments. |
| `pulse_communications_v2/` | Newer communications domain | Conversations, participants, messages, attachments, reports, blocks, read receipts, typing. |
| `migrations/` | SQL migrations | Business OS, communications, notifications, intelligence, growth, indexes. |
| `models/` | Supplemental models | Includes live session model. |
| `mobile-native/` | Current React Native/Expo native app | Main native migration target with committed native iOS/Android projects. |
| `mobile/` | Older mobile app track | Present but not the active native production target. |
| `backend/undx/config/` | Existing UNDX YAML knowledge/config packs | Existing source packs, not generated in this recon. |
| `docs/` | Architecture, policy, readiness, product reports | Important source-backed docs for Business OS, pages, Sentinel, audio, marketplace. |
| `tests/` | Backend and integration tests | Wide coverage across Business OS, marketplace, alerts, UNDX, app review, messaging. |
| `mobile-native/src/__tests__` and feature tests | Native Jest tests | API clients, navigation, media, calls, live, UNDX, session, accessibility. |
| `scripts/` | Audits, probes, operational scripts | Many product-specific audits and source inventory tools. |

## Services and Workers

| Worker/service | Location | Purpose | Status |
|---|---|---|---|
| Alert worker | `alert_worker.py` | Scheduled alert/notification ingestion | Active foundation |
| Pulse worker | `pulse_worker.py` | Pulse background jobs | Active foundation |
| Media worker | `media_worker.py` | Media processing/recovery | Active foundation |
| Ads worker | `pulse_ads_worker.py` | Advertising delivery/processing | Active foundation |
| Email worker | `email_worker.py` | Email delivery | Active foundation |
| Command center worker | `services/command_center_worker/` | Internal sidecar AI/messaging/security notifications | Separate Flask app |

## Native Architecture

| Layer | Location | Purpose |
|---|---|---|
| Navigation | `mobile-native/src/navigation/` | App stack, tabs, notification routing, bottom nav policy. |
| Screens | `mobile-native/src/screens/` | Home, Reels, Messaging, Live, Business OS, Marketplace, Settings, UNDX, etc. |
| API clients | `mobile-native/src/api/` | Typed native wrappers over backend endpoints. |
| Session | `mobile-native/src/session/` | Auth persistence, Face ID, remembered accounts, QA auth. |
| Media | `mobile-native/src/media/` | Upload manager, queues, previews, native media access. |
| Core runtime | `mobile-native/src/core/` | Cache, audio ownership, realtime audio, radio, event sync, perf trace. |
| Calls | `mobile-native/src/calls/` | Call room hooks, CallKit bridge, tone lifecycle, call session store. |
| Live | `mobile-native/src/live/` | Live broadcast room, chat overlay, reaction layer, RTC video views. |
| UNDX native | `mobile-native/src/undx/` | Action-card parsing, privacy-sanitized context, tests. |

## Third-Party Providers / Integrations

| Provider | Evidence area | Used for |
|---|---|---|
| Apple StoreKit / IAP | `mobile-native/store`, `services/business_os/entitlements/iap_apple.py`, StoreKit tests | iOS digital purchases/ad credits/Premium |
| Stripe | `services/business_os/payments`, marketplace payment services/tests | Marketplace physical payments, connected accounts, payouts |
| Agora | `mobile-native/src/live`, `mobile-native/src/calls`, RTC memory notes | Native live/call RTC provider work |
| LiveKit | Historical/live rollback paths and docs | Existing/rollback real-time provider foundation |
| Mux | media/live replay docs/routes/tests | Video upload/processing/replay/HLS |
| Cloudflare R2/CDN | media docs/scripts | Production media object storage/CDN target |
| Expo Push/APNs | push/notification services and native APIs | Native push notifications |
| Brevo/email | notification/email services | Email delivery/contact sync |
| Railway | deployment docs/memory | Hosting/deployed services |
| OpenAI/Claude/Gemini/DeepSeek/Groq | provider router docs/source references | UNDX/Pulse AI provider routing |
