# PulseSoc Lightspeed Inventory

Date: 2026-07-03

This inventory maps critical PulseSoc surfaces to their primary runtime assets and performance risks. External provider calls are expected to run through services, queues, collectors, or workers rather than normal page rendering.

| Subsystem | Routes / surfaces | Primary frontend | Services / data | Performance observations |
|---|---|---|---|---|
| Home feed | `/pulse` | `static/js/pulse_home_core.js`, `static/css/pulse_home_os.css`, `templates/index.html` | feed/posts/reactions/media tables | 32-40 ms locally; media renderer uses observer-based loading |
| Reels | `/pulse/reels` | `pulse_home_core.js`, `pulse_reels_experience.css` | `pulse_reels`, reactions, media storage | 14-15 ms; 236 KB HTML warrants continued DOM discipline |
| Status | `/pulse/status` | `pulse_status_viewer.js`, `pulse_status_system.css` | statuses, views, music/media | 15-17 ms; CSS is 130 KB but page-scoped |
| Messenger | `/pulse/messages` | `pulse_messages_v2.js`, `pulse_messages_v2.css`, `pulse_realtime.js` | conversations, participants, messages, notifications | 5-8 ms; fallback polling now backs off when realtime is connected |
| Calls | Messenger call overlay and `/api/calls/*` | `pulsesoc_calls.js`, LiveKit vendor | communication call/participant/event/quality tables | LiveKit bundle is 423 KB but Messenger-scoped; active-call fallback polling is adaptive |
| Live streaming | `/pulse/live` | `pulse_live_studio.js`, `pulse_live_studio_runtime.js` | live sessions, participants, media/Mux/LiveKit | 29-31 ms; 250 ms UI timers require continued device profiling |
| Notifications | `/pulse/notifications` | `notifications.js`, service worker | notifications, push jobs, delivery jobs/preferences | 36-37 ms; delivery is queue-based |
| Intelligence alerts | `/pulse/alerts`, `/pulse/intelligence` | `pulsesoc_intelligence_center.js/css` | intelligence signals, forecasts, delivery jobs/logs | 11-13 ms; collectors and delivery run outside page loads |
| Pulse AI | Messenger Pulse AI conversation and APIs | Messenger client | Pulse AI services, provider router, knowledge/cache | provider calls remain outside navigation; provider latency requires production telemetry |
| Growth Engine | `/pulse/growth` | `pulse_advertiser_portal.js/css` | growth accounts, wallets, workspaces, analytics | reduced from 86 to 29 queries by avoiding reprovisioning on every view |
| Mission Control | `/dashboard` | `templates/dashboard.html` | role-aware dashboard service and module APIs | reduced from 819 to 6-7 queries by summary-first rendering |
| Admin dashboard | `/admin/global-command`, `/admin/performance` | admin templates | cached metrics, audit/performance traces | 14-21 ms locally; logs should stay paginated |
| Galaxy Intelligence Center | `/admin/intelligence` | admin intelligence template | collectors, source health, signals, forecasts | 22-23 ms and 2 queries locally |
| Growth admin | growth/admin routes | admin growth templates | growth accounts/campaigns/wallets | large account lists must remain paginated |
| Calls Command Center | `/admin/calls` | `admin_calls_command_center.html` | communications engine diagnostics | reduced from 107 to 41 queries by removing schema setup from request path |
| Email | `/admin/emails` | admin email template | email logs, failed email queue, provider adapter | 16-17 ms; 359 retryable/pending records observed locally |
| SMS | notification settings/admin diagnostics | notification UI | SMS logs/provider adapters | provider delivery must remain queued |
| Crypto alerts | alert APIs and Pulse Alerts | notification/intelligence UI | alert rules/events/worker heartbeat | worker-based evaluation; no external fetch in page path |
| Market alerts | intelligence and alert surfaces | intelligence UI | market collectors/signals/forecasts | scheduled collectors and cache required |
| Marketplace | `/pulse/marketplace` | marketplace template/assets | products/orders/sellers/media | 18-19 ms locally |
| Wallet | dashboard/economy surfaces | dashboard modules | wallets, ledger, payouts | financial lists require ownership checks and pagination |
| Premium / Stripe | `/pulse/premium`, `/pulse/premium/undx` | premium templates and UNDX inline runtime | Stripe/customer/subscription tables | Premium is 159-230 ms; UNDX returns 1.94 MB because of a 1.39 MB inline script |
| Search | `/search` | `search.html`, search bridge | indexed/local search services | under 1 ms local shell response |
| Profiles | `/pulse/profile` and profile routes | profile templates | users/follows/profile views | redirect shell under 10 ms |
| Media uploads | composer/create flows | upload manager/media picker/renderer | local or object storage, media processing | large source media must not load as general UI assets |
| Music | `/pulse/music` | music/radio assets | tracks, saves, status/reels audio | 14 ms locally |
| Service worker / PWA | `/static/service-worker.js`, manifest, offline | service worker, offline pages | browser cache and push payloads | immutable static caching verified; service worker remains a release-critical asset |
| Workers / jobs | background heartbeat and queue processors | none | push, email, alerts, intelligence, background jobs | bounded retries and dead-letter controls verified |
| Database migrations | `migrations/*.sql`, runtime schema guards | none | PostgreSQL-compatible schema plus local SQLite | 613 local tables and 694+ indexes; runtime schema work was removed from hot admin routes |

## Third-party dependencies

- LiveKit: real-time audio/video media and room signaling.
- Mux: media/recording paths where configured.
- Stripe: premium and billing.
- Brevo/email and optional SMS providers: queued transactional delivery.
- OpenAI, Anthropic, Gemini, DeepSeek, Groq: optional Pulse AI providers.
- Crypto, market, world, and security providers: collector-only paths with cache/timeouts.
- Redis: optional shared cache/queue acceleration; local environment currently uses memory/database fallbacks.

## Large local assets

- `static/audit/video-with-original-audio.mp4`: 2.85 MB, audit-only.
- `static/Coinpilot Logo/NewLogo.png`: 2.52 MB, legacy branding asset.
- `static/uploads/pulse_ads/pulse-radio-sponsored-ad.png`: 2.43 MB, uploaded campaign media.
- `static/brand/pulse-logo-20260606.png`: 1.22 MB.
- `static/brand/pulsesoc-logo-20260606.png`: 1.09 MB.
- `static/vendor/livekit-client.umd.js`: 423 KB, isolated to Messenger.

These assets were not deleted because several are user/generated or release assets. Compression and archival should be a separate ownership-reviewed operation.
