# PulseSoc — Backend Services & Infrastructure Map

> Scope: service layer, workers/queues, realtime infra, media pipeline, external
> providers, UNDX, other subsystems, and a product-area dependency map.
> **Out of scope** (owned by another analysis): routes, auth, authorization,
> rate limiting, CORS/CSRF.
>
> READ-ONLY analysis. Nothing in the audio/live/call foundation was modified.
> Claims are cited `file:line`. Anything not confirmed from source is marked
> **UNVERIFIED**. `CLAUDE.md` and root `*_REPORT.md` files were NOT trusted.
>
> Generated 2026-09-12 against `main`.

## Contents
1. [Service layer census](#1-service-layer-census)
2. [Background workers and queues](#2-background-workers-and-queues)
3. [Realtime infrastructure](#3-realtime-infrastructure-inventory-only)
4. [Media pipeline](#4-media-pipeline)
5. [External providers](#5-external-providers)
6. [UNDX layer](#6-undx-layer)
7. [Other subsystems](#7-other-subsystems)
8. [Dependency map](#8-dependency-map)

---

## 1. Service layer census

### 1.0 Reconciliation

`ls services/*.py | wc -l` = **344**. That is **343 domain modules + `services/__init__.py`**.
All 343 are classified below; the per-domain counts sum to 343.

**The 344 undercounts the service layer by ~4x.** There are also **10 sub-packages**
holding **364 further `.py` files / ~129,300 lines**:

| Package | Files | Lines | Owns |
|---|---:|---:|---|
| `services/business_os/` | 204 | 65,881 | Business OS: commerce, suppliers/CJ dropshipping, advertising selection, events/ticketing, insights |
| `services/private_office/` | 40 | 29,584 | Private Office: meetings, conversations, documents, relationships, feature matrix |
| `services/sentinel/` | 56 | 12,653 | Security sentinel: rate limiting, providers, runtime |
| `services/undx_brain/` | 20 | 12,408 | UNDX brain: config + reasoning core |
| `services/command_center_worker/` | 13 | 3,553 | The (undeployed) realtime worker — see §3.2 |
| `services/pulse_briefings/` | 5 | 2,097 | Scheduled briefings |
| `services/pulse_ai/` | 9 | 1,515 | Pulse AI helpers |
| `services/intelligence_collectors/` | 11 | 1,465 | Intelligence ingestion |
| `services/providers/` | 6 | 140 | Provider adapters |

Top-level modules total **143,150 lines**. **Whole service layer ≈ 707 files, ≈272,000
lines** — roughly 2.2x `bot.py` itself.

### 1.1 Domains (343 top-level modules)

| # | Domain | Modules | Lines | Owns |
|---:|---|---:|---:|---|
| 1 | **UNDX / AI agent layer** | 50 | 39,109 | Mission/agent runtime, tool gateway, capability registry, knowledge map, cost, policy, privacy, health, canary/shadow, verification |
| 2 | **AI / ML (non-UNDX)** | 24 | 4,619 | `pulse_ai_service` (2,140) + `pulse_ai_provider_router` (642) + `pulse_ai_web_search`; the other 21 are 12–120-line shells |
| 3 | **Live / streaming / RTC** | 27 | 2,822 | Agora push + cloud recording, Mux live, participants, distribution, archive, health, discovery, restream |
| 4 | **Marketplace / commerce / dropship** | 27 | 10,397 | Cart, offers, returns, variants, listing lifecycle, reservations (policy/schema/sweeper/reconciler), settlement, fulfillment, payouts, seller identity/money, Business OS route packs |
| 5 | **Advertising** | 11 | 11,213 | `pulse_ads_service`, ads OS, adsets, audiences, insights, library, reporting, advertiser portal, ad payments, worker service, policy engine |
| 6 | **Arena / games** | 19 | 1,545 | Arena world/events/replay/victory, roast battle, crowd energy, leaderboards |
| 7 | **Crypto / market data** | 25 | 11,901 | `alert_engine` (4,277 — largest service in the repo), CoinGecko client, market intelligence + portfolio, observations, pulse, predictions, news, wallet |
| 8 | **Private office** | 9 | 5,063 | The route surface; the real logic is in `services/private_office/` (29,584 lines) |
| 9 | **Messaging / chat** | 7 | 5,364 | `pulsesoc_communications_engine` (2,330), messenger media foundation, chat bridge, chat health, `command_center_client` |
| 10 | **Media / upload / storage** | 11 | 3,487 | `media_service` (1,365), `media_storage` (R2), upload sessions, upload progress, covers, previews, music, saved content |
| 11 | **Notifications / push** | 9 | 7,950 | `pulsesoc_notification_system` (2,979), `notification_service` (2,306), `push_service` (1,285), email/SMS/Brevo, orchestrator, health |
| 12 | **Feed / ranking / discovery** | 11 | 3,935 | `pulse_feed_engine` (3,077) is effectively the whole domain; ranking/personalization modules are 12–51-line stubs |
| 13 | **Search / SEO** | 3 | 160 | `pulse_search_engine` is **29 lines** — see §7.1 |
| 14 | **Social graph / profile / identity** | 15 | 3,256 | `presence_service` (1,113), presence routes, social graph, profile, viewer permissions, premium identity |
| 15 | **Auth / security / moderation** | 16 | 1,449 | `scam_shield` (412), `pulse_security_core` (267), moderation engine, privilege, safe execution (+ `services/sentinel/` 12,653) |
| 16 | **Payments / premium / monetization** | 18 | 4,038 | `premium_entitlement_service` (1,108), Apple IAP credits + server API, payment router, payment provider, Stripe webhook verification, treasury |
| 17 | **Analytics / intelligence** | 23 | 15,446 | Eight `dashboard_*_command_center` modules, `pulsesoc_intelligence_engine` (3,081), dashboard centers, growth, promotions, mission control |
| 18 | **Realtime transport** | 6 | 791 | `realtime_engine`, `realtime_service`, `websocket_orchestrator`, `event_bus_engine`, `distributed_realtime_engine` — see §3.2 |
| 19 | **i18n / translation** | 2 | 1,056 | `content_translation` (782) + `translation_providers` (274) |
| 20 | **Admin / ops / reliability** | 23 | 4,441 | `app_links` (910), `backend_management_registry` (877), admin gateway, feature flags, schema guard, device classification, Telegram router |
| 21 | **Infra / db / util** | 7 | 5,103 | `db` (1,097), `cache_engine`, `pulsesoc_pages` (2,044), `pulse_settings_routes` (1,227), region prefs, mutation audit |
| | **Total** | **343** | **143,150** | |

Largest single modules: `alert_engine` 4,277 · `undx_agent_runtime` 4,234 ·
`undx_knowledge_map` 3,234 · `undx_agent_tools` 3,171 · `pulsesoc_intelligence_engine`
3,081 · `pulse_feed_engine` 3,077 · `pulsesoc_notification_system` 2,979.

### 1.2 Dead modules

Import graph built by parsing `from services import …` (incl. parenthesised multi-line
forms), `from services.X`, `import services.X`, and intra-package relative imports across
every `.py` in the repo except `mobile/` and `mobile-native/`. Then corrected for
**dynamic registration**: `bot.py:1257` defines `_load_route_pack(name, module_path)` and
`bot.py:1279-1380` loads ~24 route packs **by string path** (e.g.
`_load_route_pack("pulse_presence", "services.presence_routes")` at `bot.py:1280`), which
a naive import scan reports as dead. All `*_routes` modules are live via this mechanism.

**19 genuinely dead modules** (zero importers, not a route pack):

`admin_service`(27) · `ai_memory_engine`(12) · `alert_service`(9) · `analytics_service`(22) ·
`audit_service`(32) · `billing_service`(37) · `leaderboard_engine`(26) ·
`live_network_engine`(27) · `market_service`(13) · `news_intel`(6) · `portfolio_intel`(9) ·
`recovery_service`(13) · `security_service`(20) · `seo_service`(30) · `stripe_service`(11) ·
`telegram_service`(13) · `undx_model_audit`(276) · `user_service`(24) · `wallet_service`(5)

**The pattern is unmistakable:** the plain `<noun>_service.py` names — `user_service`,
`stripe_service`, `wallet_service`, `billing_service`, `analytics_service`,
`audit_service`, `security_service`, `alert_service`, `market_service`, `admin_service`,
`telegram_service`, `seo_service` — are **abandoned first-generation shells**, all under
40 lines, superseded by `pulse_*` / `pulsesoc_*` successors. `undx_model_audit` (276
lines) is the only substantial dead module. Total dead code: **585 lines, 0.4%** — small.

**The real waste is elsewhere.** Roughly **60 modules are live-but-vestigial**: imported
somewhere but under ~40 lines and clearly placeholder (`civilization_ai_engine` 18,
`world_simulation_engine` 12, `identity_sovereignty_engine` 17, `decentralized_trust_engine`
10, `global_social_energy_engine` 12, `ui_state_engine` 12, `self_healing_engine` 15,
`intelligence_products_engine` 12, `marketplace_engine` 12, `scam_shield_service` 5 …).
For the rebuild, treat any `*_engine.py` under 50 lines as **aspirational naming, not
capability** — verify before depending on it.

**20 modules are reachable only from tests or `scripts/`** after excluding route packs:
`ai_service`, `pulse_apple_server_api`, `undx_canary`, `undx_routing_evidence`,
`undx_shadow`. (`undx_shadow` in particular is referenced by memory notes as a live
safety gate — its only *import* sites are tests, so it is presumably invoked via the
UNDX tool gateway by name. **UNVERIFIED**.)

## 2. Background workers and queues

### 2.1 The real Procfile

`Procfile:1-6` — **six process types: `web` + five workers.** CLAUDE.md's claim of
"gunicorn `web` plus `undx_worker` and `email_worker`" is **stale/wrong**.

| Procfile entry | Command | File |
|---|---|---|
| `web` | `gunicorn bot:app --workers ${WEB_CONCURRENCY:-4} --threads ${WEB_THREADS:-8} --timeout 120` | `bot.py` |
| `undx_worker` | `python undx_worker.py` | `undx_worker.py` |
| `email_worker` | `python email_worker.py` | `email_worker.py` |
| `ads_worker` | `python pulse_ads_worker.py` | `pulse_ads_worker.py` |
| `alert_worker` | `python alert_worker.py` | `alert_worker.py` |
| `media_worker` | `python media_worker.py` | `media_worker.py` |

Note the web process is **threaded gunicorn (sync workers + 8 threads)**, not gevent/
eventlet. That is a hard constraint for Section 3: a long-lived WebSocket per client
would consume one of 4x8 = 32 threads. This is the single most important infrastructure
fact for the web rebuild.

### 2.2 Worker-by-worker

| Worker | Lines | Deployed? | What it does | Consumes |
|---|---|---|---|---|
| `undx_worker.py` | 141 | **yes** | Keeps the UNDX Intelligence Router warm, reports provider-config status, drives `undx_mission_runtime` / `undx_agent_runs`. Explicitly documents that it "does not call providers, read files, run commands, or execute repository actions" (`undx_worker.py:3-6`). | UNDX run/mission tables |
| `email_worker.py` | 45 | **yes** | Drains the email outbox by calling `bot.process_email_delivery_jobs(limit=batch)` on a 10s loop (`email_worker.py:33`). Sets `COINPILOTX_INIT_DB_ON_IMPORT=0` and `EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED=0` before importing `bot` (`email_worker.py:11-12`) — i.e. the web tier ALSO opportunistically sends email unless disabled. | DB outbox (`email_delivery_jobs` / `failed_email_queue`) |
| `pulse_ads_worker.py` | 163 | **yes** (as `ads_worker`) | Five cadences in one process: drain `pulse_ad_jobs` every cycle (~20s), campaign ops sweep, attribution every ~300s, billing reconciliation every ~600s, reporting precompute every ~300s (`pulse_ads_worker.py:8-19`). Explicitly **never charges wallets** — the synchronous delivery path owns money. Delegates to `services/pulse_ads_worker_service.py`. | `pulse_ad_jobs` DB table |
| `alert_worker.py` | 143 | **yes** | Crypto/market alerts: `alert_engine`, `auto_signals_service`, `live_market_service`, `market_observations`, `pulse_briefings` (`alert_worker.py:18-20`). Samples an 80-coin board sharing `live_market_service.get_crypto_quote`'s 45s cache (`alert_worker.py:36-39`). | `alert_delivery_jobs`, CoinGecko |
| `media_worker.py` | 1000 | **yes** | Validation, thumbnail fallbacks, media queue jobs, ffmpeg shell-outs, R2 writes, heartbeats (`media_worker.py:1-9`, `:35-37`). The heavyweight worker. | media job table + R2 + ffmpeg |
| `pulse_worker.py` | 296 | **ORPHANED** | Pulse feed background jobs (`pulse_ai`, `pulse_feed_engine`) **and** hosts the marketplace reservation sweep via `marketplace_reservation_sweeper` on a 1–5 min cadence (`pulse_worker.py:10-12`, `:19-44`). | `pulse_jobs`, reservation rows |
| `supplier_worker.py` | 43 | **ORPHANED** (and disabled-by-default) | CJ dropshipping reconciliation tick; gated on `CJ_RECONCILIATION_ENABLED` and `policy.require_network()` (`supplier_worker.py:16-19`). Swallows exception text deliberately because credentials are request-local (`supplier_worker.py:25-26`). | `business_os_supplier_sync_jobs` |
| `telegram_worker.py` | 15 | **ORPHANED** | Thin shim: `bot.main()` long-polls Telegram (`telegram_worker.py:9-11`). | Telegram getUpdates |

**Three orphans.** The most consequential is `pulse_worker.py`: **marketplace stock
reservations are never swept in production** because the only process that hosts the
sweeper is not in the Procfile. Feed precompute jobs (`pulse_jobs`) likewise have no
deployed drainer. This is a live correctness gap, not a rebuild concern —
flagged, **UNVERIFIED** whether Railway defines these services outside the Procfile
(Railway can override start commands per service in its dashboard).

### 2.3 The queue mechanism

**There is no real queue broker. Every queue is a database table polled on a sleep
loop.** Evidence: each worker's main loop is `while RUNNING: ...; time.sleep(interval)`
with a `limit=batch_size` SELECT (e.g. `email_worker.py:31-41`, `pulse_ads_worker.py:35-40`).

Redis is **optional and is a cache/presence accelerator, not a broker**:
`services/cache_engine.py:1-4` — "Optional Redis-backed cache and presence helpers…
Redis becomes an accelerator, not a hard dependency", with an in-memory TTL fallback
when `REDIS_URL` is unset (`services/cache_engine.py:27-39`). Redis appears in ~20
modules including `services/sentinel/rate_limit.py` and the
`services/command_center_worker/` package (`redis_manager.py`, `presence.py`,
`messaging.py`, `realtime_transport.py`, `notifications.py`) — that package is the one
place Redis is used pub/sub-style.

Job tables observed by reference frequency: `pulse_jobs` (65), `push_delivery_jobs` (47),
`failed_email_queue` (43), `alert_delivery_jobs` (43), `moderation_queue` (35),
`payout_queue` (26), `notification_delivery_jobs` (21), `refund_queue` (20),
`pulse_ad_jobs` (18), `intelligence_delivery_jobs` (18), `content_queue` (18),
`business_os_supplier_sync_jobs` (13), `pulse_ad_moderation_queue` (12).

**Web-rebuild impact:** the web client depends on `media_worker` (uploads complete
asynchronously — the browser must poll or be pushed a completion signal),
`email_worker` (verification/transactional mail), `ads_worker` (advertiser dashboards),
and `undx_worker` (AI runs). It does **not** depend on `alert_worker` unless crypto
alerts ship on web. A DB-polling queue with ~20s cadence means the web UI must be
designed for eventual consistency on uploads, ad stats, and AI runs.

## 3. Realtime infrastructure (inventory only)

> **No audio / live / call code was modified.** This section is an inventory.

### 3.1 The RTC provider is **Agora**, not LiveKit

CLAUDE.md is **stale**. Evidence, on `main` today:

| Evidence | Verdict |
|---|---|
| `mobile-native/package.json:71` — `"react-native-agora": "4.6.2"` | Agora is the installed client SDK |
| No `livekit` string anywhere in `mobile-native/package.json` or `package-lock.json` | **no LiveKit client package at all** |
| `requirements.txt:19` — `agora-token-builder==1.0.0`; no livekit server SDK | Agora server-side token minting |
| `.env.example:1168-1177` — `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`, `AGORA_REST_CUSTOMER_ID`, `AGORA_REST_CUSTOMER_SECRET`, `AGORA_TOKEN_TTL_SECONDS`, `AGORA_RECORDING_TIMEOUT_SECONDS`, `AGORA_MEDIA_PUSH_*`. **No `LIVEKIT_*` key exists.** | Agora is the configured provider |
| `services/agora_media_push_service.py:1` — "Server-only Agora Media Push bridge for PulseSoc Live -> Mux"; `services/agora_cloud_recording_service.py` | Agora → Mux is the live pipeline |
| `bot.py:49214` renders `<label>Agora channel<code>{livekit_room}</code></label>` | **the smoking gun**: the UI label says Agora while the Python variable is still named `livekit_room` |

**Conclusion:** the product migrated LiveKit → Agora and left the *vocabulary* behind.
`livekit_*` survives as **string literals in state machines and telemetry**
(`bot.py:48335-48348` `livekit_live_states`, `bot.py:50312-50318` cohost step names,
`bot.py:47678` error codes `LIVEKIT_ROOM_JOIN_FAILED` / `LIVEKIT_PUBLISH_FAILED`) and in
module names (`services/live_distribution_service.py`, `services/live_participants.py`).
Those are **persisted enum values, not a live integration** — a web rebuild must keep
emitting/accepting them but must not conclude LiveKit is in play. Backend modules
mentioning "livekit" (8) are all doing state/health string comparison; the only modules
that *call* a provider are the two `agora_*` services plus `services/mux_live_service.py`.

### 3.2 Realtime messaging: **it is polling, deliberately**

SSE exists in only three places in `bot.py` and **two of the three are disabled by
default behind env flags**:

- `bot.py:89360` `GET /api/pulse/live/stream` — the main Pulse event stream. Gated twice:
  `PULSE_MAIN_APP_SSE_ALLOWED` and `PULSE_LEGACY_SSE_ENABLED`. When off it returns
  **204** with headers `X-Pulse-Realtime-Transport: polling` and
  `X-Pulse-SSE-Disabled-Reason: main_app_worker_protection`. The inline comment
  (`bot.py:89367-89369`) states the reason outright: *"Long-lived browser streams can
  exhaust the main Gunicorn worker pool. The main app stays on fast polling unless a
  dedicated realtime service is explicitly enabled and capacity-tested."*
- `bot.py:36592` `GET /api/arena/realtime/<kind>/<id>/stream` — gated on `ARENA_SSE_ENABLED`,
  same 204 + `X-Pulse-Realtime-Transport: polling` fallback. When enabled it is a
  **bounded** stream: 10 iterations × 2s sleep, then a heartbeat and close
  (`bot.py:36601-36612`). It is polling wearing an SSE costume.
- `services/command_center_worker/app.py` — the only unconditional SSE, and it lives in a
  **separate Flask app** that is not in the Procfile.

The canonical transport is therefore **HTTP long-poll/short-poll against DB-backed
cursors**: `GET /api/arena/realtime/<kind>/<id>?after_id=N` (`bot.py:36576`) and
`GET /api/pulse/messages/<conversation_id>` (`bot.py:90998`) with `since_id`/`after_id`
paging. `services/realtime_service.py` is the shared layer
(`poll_arena_channel`, `publish_arena_event`, `realtime_manager.heartbeat`).

**There is no WebSocket server.** `services/websocket_orchestrator.py`,
`services/realtime_engine.py` and `command_center_worker/realtime_transport.py` exist,
but the last one says so explicitly (`realtime_transport.py:1-6`): *"intentionally
dependency-light… keeps an in-process replay buffer and connection registry today, while
preserving the API shape needed for Redis/WebSocket fanout later."* An in-process registry
inside a 4-worker gunicorn deployment cannot fan out — **UNVERIFIED** whether any of these
are reachable in production; none is in the Procfile.

### 3.3 Presence, typing, read receipts

| Signal | Write endpoint | Read endpoint | Transport |
|---|---|---|---|
| Presence (online) | `POST /api/pulse/presence` heartbeat → `pulse_mark_online()` (`bot.py:89289-89292`) | `GET /api/pulse/messages/<id>/presence` (`bot.py:90931`), `GET /api/world-presence` (`bot.py:31469`), `GET /api/arena/presence` (`bot.py:34902`) | poll |
| Typing | `POST /api/pulse/typing` (`bot.py:89301`), `POST /api/arena/chat/typing` (`bot.py:36912`) | folded into the conversation/presence poll | poll |
| Read receipts | `GET /api/business-os/messages/threads/<id>/read` (`bot.py:25270`); message-level read state in the Pulse message payload | poll |

The Command Center worker defines a richer event vocabulary that the main app does not
use: `presence_updated`, `message_created`, `message_delivered`, `message_read`,
`typing_started`, `typing_stopped`, `unread_count_updated`
(`services/command_center_worker/realtime_transport.py:23-30`). Treat this package as the
**intended future transport**, currently dormant.

Redis backs presence when `REDIS_URL` is set (`services/cache_engine.py`,
`command_center_worker/presence.py`), degrading to per-process memory otherwise — meaning
presence is **per-gunicorn-worker inconsistent** without Redis.

### 3.4 Livestreaming pipeline

`Agora (ingest/RTC) → Agora Media Push (REST) → Mux (RTMP ingest, HLS playback, recording)`

- `services/agora_media_push_service.py` — server-only bridge, pushes the Agora channel to
  a Mux RTMP endpoint. Config check at `:15-26`.
- `services/agora_cloud_recording_service.py` — Agora Cloud Recording.
- `services/mux_live_service.py` — Mux live stream create/get/disable; `.env.example:459-479`
  gives per-call timeouts, `MUX_TOKEN_ID`/`MUX_TOKEN_SECRET`, `MUX_WEBHOOK_SECRET`, and
  `MUX_SIGNING_KEY_ID`/`MUX_SIGNING_PRIVATE_KEY` for **signed playback URLs**.
- `services/live_stream_health_service.py`, `services/live_distribution_service.py`,
  `services/live_participants.py`, `services/live_archive_service.py` — health, fanout,
  participant roster, VOD archive.
- Fallback path: when Mux egress quota is exhausted the stream flips to `livekit_direct` /
  `browser_live_livekit_direct` (`bot.py:49155`) — legacy naming for **direct Agora
  playback without Mux**. `bot.py:51458` `POST /api/pulse/live/<id>/browser-publish`
  already exists for a browser publisher.

### 3.5 What a WEB client needs per realtime system

| System | Native today | Web requirement | Gap |
|---|---|---|---|
| Voice/video calls | `react-native-agora` 4.6.2 | **`agora-rtc-sdk-ng`** (Agora Web SDK). Token minting already server-side via `agora-token-builder`; the web client needs a token endpoint returning appId + channel + RTC token + uid. | Ship the JS SDK; **do not** touch the native audio foundation. Web and native can share one Agora channel — same appId/certificate. |
| Live viewing | Mux HLS | `hls.js` or native `<video>` on Safari, plus signed playback tokens from `MUX_SIGNING_KEY_ID` | Low risk — already just a URL |
| Live broadcasting from browser | n/a | Agora Web SDK publish → existing `POST /api/pulse/live/<id>/browser-publish` (`bot.py:51458`) | Endpoint exists; **UNVERIFIED** how complete |
| Messaging | poll | Same polling API works unchanged in a browser. | None. But a browser tab polling 1–2s multiplies request volume against a 32-thread gunicorn pool. |
| Presence/typing/receipts | poll | Same. | Needs Redis to be consistent across workers |
| Push/notification badge | FCM/APNs | Web Push (VAPID) — see §5 | Separate fan-out path |
| SSE (if ever enabled) | flagged off | Browsers cap **6 concurrent connections per origin**; an SSE connection eats one. And the gunicorn comment already rules it out. | Recommend: keep polling, or stand up the Command Center worker as a real service |

**The decisive constraint for the rebuild:** the backend is a threaded-sync gunicorn app
that has explicitly rejected long-lived connections to protect its worker pool. A web
client should assume **polling parity with native**, not upgrade to sockets, unless
`services/command_center_worker/` is promoted to a deployed process with its own dyno and
a Redis pub/sub fanout.

## 4. Media pipeline

### 4.1 Two upload paths coexist

**Path A — classic multipart POST to Flask** (the majority). 11 endpoints:

| Route | `bot.py` line |
|---|---|
| `POST /api/media/upload` | 103071 |
| `POST /api/pulse/media/upload` | 103481 |
| `POST /api/pulse/messages/upload`, `/api/pulse/messages/media/upload` | 91240–91241 |
| `POST /api/messages/media/init` → `/upload` → `/complete` → `/attach` | 91488, 91504, 91529, 91545 |
| `POST /api/pulse/marketplace/media/upload`, `/digital-files/upload` | 94377, 94556 |
| `POST /api/pulse/ads/accounts/<id>/media/upload` | 19426 |
| `POST /api/pulse/music/upload`, `/api/pulse/reels/sounds/upload` | 43223, 87048 |
| `POST /api/pulse/teachers/documents/upload` | 95092 |

**Path B — presigned direct-to-R2 with multipart** (`services/media_upload_sessions.py`,
"Authorized direct-to-object-storage upload sessions for PulseSoc media", `:1`):

```
POST /api/pulse/media/uploads                      -> create session   (bot.py:103087)
GET  /api/pulse/media/uploads/<upload_id>           -> status           (103097)
POST /api/pulse/media/uploads/<id>/parts/sign       -> presigned parts  (103109)
POST /api/pulse/media/uploads/<id>/refresh          -> re-sign          (103120)
POST /api/pulse/media/uploads/<id>/complete         -> S3 CompleteMPU   (103130)
POST /api/pulse/media/uploads/<id>/finalize         -> register media   (103141)
POST /api/pulse/media/uploads/<id>/abort            -> cleanup          (103151)
```

Session state lives in table `pulse_media_upload_sessions`
(`services/media_upload_sessions.py:44+`). Tunables (`:19-24`): TTL 3600s, signed-URL TTL
**clamped to ≤900s** regardless of env, multipart threshold 16 MB, part size ≥5 MB
(S3 minimum), **max 12 parts signed per request** — so a large video needs repeated
`/parts/sign` calls, which the web client must implement.

**Path C — Mux direct upload** for video: `POST /api/pulse/media/mux/direct-upload`
(103186) and `.../complete` (103281). Mux issues its own upload URL; the browser PUTs
straight to Mux.

### 4.2 Storage backend

`services/media_storage.py:1` — "Media storage abstraction for local dev and R2/S3
production URLs". Provider chosen by `MEDIA_STORAGE_PROVIDER` (`:20`), default `local`.
Credential lookup accepts **both** `R2_*` and `AWS_*`/`S3_*` aliases, and the file carries
a comment about a real incident where the admin panel reported `configured=False` on a
working `S3_BUCKET`/`AWS_*` deployment (`:27-30`). Public URLs are built from
`R2_PUBLIC_BASE_URL` (`.env.example:37` → `https://cdn.coinpilotx.app`, still the old
brand). Filenames go through `werkzeug.utils.secure_filename` + a random token
(`media_storage.py:58-60`). Private media has a separate root
(`PRIVATE_MEDIA_UPLOAD_DIR`, default `instance/private_uploads`, `:15`) and is served
through `GET /api/messages/media/<id>/download` (91612) and `/access` (91576) rather than
a public URL.

### 4.3 Validation and limits (`.env.example:584-601, 1019, 1190-1194`)

| Setting | Default |
|---|---|
| `MEDIA_UPLOAD_ALLOWED_TYPES` | `jpg,jpeg,png,webp,gif,mp4,webm,mov,mp3,m4a,aac,wav,ogg,pdf,txt,doc,docx` |
| `MEDIA_UPLOAD_MAX_IMAGE_MB` / `MAX_FILE_MB` / `MAX_UPLOAD_MB` | 12 |
| `MEDIA_UPLOAD_MAX_GIF_MB` | 8 |
| `MEDIA_UPLOAD_MAX_AUDIO_MB` | 15 |
| `MEDIA_UPLOAD_MAX_VIDEO_MB` | 150 |
| `MEDIA_UPLOAD_MAX_STATUS_VIDEO_MB` | 350 |
| `MEDIA_DIRECT_UPLOAD_MAX_VIDEO_GB` | 25 (Path B/C only) |
| `MEDIA_REQUIRE_DURABLE_UPLOAD` | unset |

Note the **12 MB Flask-POST ceiling vs 25 GB direct-upload ceiling** — a 200x gap. Any web
feature handling video *must* use Path B or C.

### 4.4 Processing (media_worker)

`media_worker.py:78` — `MEDIA_JOB_TYPES = {"generate_thumbnail", "process_video",
"finalize_live_replay"}`. The worker shells out to `ffmpeg` (`:114-121`,
`MEDIA_WORKER_FFMPEG_CRF=23`, `MEDIA_WORKER_FFMPEG_PRESET=veryfast`,
`MEDIA_WORKER_TRANSCODE_TIMEOUT_SECONDS=180`). Batch 25, interval 20s, max 3 attempts
(`.env.example:593-601`).

**It degrades rather than fails when ffmpeg is absent** (`media_worker.py:160-161`:
`MEDIA_ENGINE_FFMPEG_MISSING thumbnails/transcoding will use safe fallbacks`). The
fallback is to set `thumbnail_url = media_url` (`:218-221`) — i.e. a video's "thumbnail"
becomes the video file itself. Browsers will download the whole video to render a grid
cell. **This is a real web-rebuild hazard**: the native app tolerates it; a reels grid in a
browser will not.

### 4.5 Traced end-to-end: publishing a Reel

`POST /api/pulse/reels/create` (alias `POST /api/reels`) — `bot.py:87094-87160`:

1. Client has **already uploaded** — the route takes `media_ids`, not a file
   (`bot.py:87108-87118`). Reels are a **two-phase publish**.
2. Looks up `chat_media_uploads` by `id` + `uploader_user_id`, excluding
   `moderation_status='blocked'` (`:87124-87131`). Note the table name: **all media,
   including reels and posts, lands in `chat_media_uploads`.**
3. `media_service.resolve_media(row)` produces `playback_url`, `media_url`,
   `mux_playback_id`, `mux_status`, `processing_status` (`:87136`).
4. Rejects non-video with "Reels require an MP4, MOV, or WEBM video" (`:87139`).
5. If a Mux playback id exists but `mux_status` is not in `{ready, asset_ready,
   available}`, `processing_status` is forced to `mux_processing` (`:87144-87147`) — the
   reel is created in a **pending** state and the client must poll.
   `POST /api/pulse/videos/<id>/retry` (86604) exists for stuck assets.
6. Optional music: `music_service.attach_music_payload()` with an `is_creator_safe` gate
   (`:87155-87159`).

Full path: `presign → PUT to R2 (or Mux direct) → finalize → row in chat_media_uploads →
media_worker job (thumbnail/transcode) → Mux asset ready → POST /api/pulse/reels/create →
poll until processing_status=ready`.

### 4.6 What changes for browser uploads

| Concern | Native today | Web requirement |
|---|---|---|
| File selection | native picker, SDK gives a path + known size | `<input type=file>` / drag-drop; `File.size` is available so the presign flow works |
| **CORS on direct-to-R2** | irrelevant — RN issues the PUT outside a browser origin | **Blocking.** The R2 bucket needs a CORS policy allowing `PUT` + `Origin: https://pulsesoc.com` and exposing `ETag` (required to complete a multipart upload). No CORS config was found in the repo — it is bucket-side. **UNVERIFIED, and the single most likely thing to break first.** |
| Multipart part signing | same API | Browser must chunk with `Blob.slice()`, honour the **12-parts-per-sign** cap (`media_upload_sessions.py:24`), and re-sign as the ≤900s URL TTL expires mid-upload on slow connections |
| Progress | native SDK callbacks | `XMLHttpRequest.upload.onprogress` (note: `fetch()` has **no** upload progress). Server-side `services/upload_progress_service.py` + `GET /api/pulse/media/uploads/<id>` give a polled fallback |
| Resume | session TTL 3600s | Persist `upload_id` + completed part ETags in `localStorage`; `/refresh` re-signs |
| Thumbnails | worker + native player | Needs the ffmpeg path to actually work (§4.4), or generate a client-side poster from a `<canvas>` frame |
| Video playback | Mux HLS via native player | `hls.js`; Safari plays HLS natively. Signed playback needs `MUX_SIGNING_KEY_ID`/`MUX_SIGNING_PRIVATE_KEY` tokens minted server-side (`.env.example:478-479`) |
| Large files | 25 GB allowed | Browser memory: never read the file whole; slice only |
| Private media | `/access` + `/download` | Same endpoints; must not be `<img src>`-able without the access check |

## 5. External providers

### 5.1 Env surface

`.env.example` documents **650 keys** (`grep -cE '^[A-Za-z0-9_]+=' .env.example`).
CLAUDE.md says "~180" — **stale by 3.6x**. Largest prefixes: `PULSE_` 49, `UNDX_` 45,
`PULSESOC_` 40, `BUSINESS_` 29, `MEDIA_` 26, `MUX_` 17, `BREVO_` 17, `TRANSLATION_` 16,
`COINGECKO_` 16, `GOOGLE_` 15, `PRIVATE_` 14, `STRIPE_` 13, `SENTINEL_` 11, `PUSH_` 11,
`CJ_` 11, `R2_` 9, `AGORA_` 8, `APPLE_` 7, `REALTIME_` 7, `LIVESTREAM_` 7.

### 5.2 Providers

| Provider | Purpose | Integration lives in | Keys | Web client needs it? |
|---|---|---|---|---|
| **Stripe** | Subscriptions, marketplace settlement, ad wallet top-ups, payouts | `services/payment_provider.py`, `services/pulse_payment_router.py`, `services/stripe_webhook_verification.py`, `services/marketplace_settlement_service.py`, `seller_money`. (`services/stripe_service.py` is **dead**, §1.2) | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`, `STRIPE_PRO_LINK` server-side; **`STRIPE_PUBLISHABLE_KEY` (`.env.example:28`) is publishable by design** | **Yes** — Stripe.js / Elements with the publishable key. Everything else stays server-side. |
| **Agora** | Voice/video RTC, cloud recording, media push to Mux | `services/agora_media_push_service.py`, `services/agora_cloud_recording_service.py`, `requirements.txt:19` `agora-token-builder` | `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`, `AGORA_REST_CUSTOMER_ID/SECRET` (`.env.example:1168-1177`) | **Yes** — Agora Web SDK. `APP_ID` is public; the **certificate must never leave the server** — tokens are minted server-side. |
| **Mux** | Live ingest/HLS, VOD assets, direct uploads, signed playback, Mux Data | `services/mux_live_service.py`, `services/live_archive_service.py`, `media_service` | `MUX_TOKEN_ID`, `MUX_TOKEN_SECRET`, `MUX_WEBHOOK_SECRET`, `MUX_SIGNING_KEY_ID`, `MUX_SIGNING_PRIVATE_KEY` — all server-only. `MUX_DATA_ENV_KEY` is client-safe. | **Yes** for playback URLs + direct-upload URLs; obtains both from our API. |
| **Cloudflare R2** (boto3/S3) | Object storage + CDN | `services/media_storage.py`, `services/media_upload_sessions.py` | `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_ENDPOINT`/`R2_ACCOUNT_ID`, `R2_PUBLIC_BASE_URL`; `AWS_*`/`S3_*` accepted as aliases | Indirectly — presigned URLs only. **Requires bucket CORS** (§4.6). |
| **Brevo** | Transactional email + SMS + contact lists | `services/email_service.py`, `services/sms_service.py`, `services/brevo_contacts.py` | `BREVO_API_KEY`, `BREVO_SMS_API_KEY`, sender/list IDs (`.env.example:64-72, 338-342`) | No — server-side only, via `email_worker`. |
| **Firebase / FCM** | Android + web push | `services/push_service.py`, `services/native_push_readiness.py` | `FCM_PROJECT_ID`, `FCM_CLIENT_EMAIL`, `FCM_PRIVATE_KEY`, `FCM_SERVER_KEY` (`.env.example:351-354`) | Only if web push goes through FCM; VAPID is the simpler path. |
| **APNs** | iOS push | `services/push_service.py` | `APNS_TEAM_ID`, `APNS_KEY_ID`, `APNS_PRIVATE_KEY`, `APNS_BUNDLE_ID`, `APNS_USE_SANDBOX` (`:355-359`) | No — native only. |
| **Web Push (VAPID)** | Browser push | `services/push_service.py` | `WEB_PUSH_PUBLIC_KEY`/`PRIVATE_KEY`/`SUBJECT` (`:345-347`) **and** a duplicate `VAPID_PUBLIC_KEY`/`PRIVATE_KEY`/`SUBJECT` (`:348-350`) | **Yes, and it already exists.** Two key pairs for one mechanism — confirm which `push_service` actually reads before wiring a service worker. |
| **Google Cloud Translation** | i18n | `services/translation_providers.py`, `services/content_translation.py` | `GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_TRANSLATION_CREDENTIALS_JSON`, `..._API_KEY`, `..._LOCATION` (`:410-413`) | No — server-side. |
| **Telegram** | Bot: commands, typed questions, alert delivery | `services/telegram_text_router.py`, `bot.main()` via `telegram_worker.py` | `TELEGRAM_BOT_TOKEN` (`:487`) | No. And the worker is **not deployed** (§2.2). |
| **CoinGecko** | Crypto prices/history/news | `services/coingecko_client.py`, `services/live_market_service.py`, `market_data` | 16 keys incl. a **budget guard**: `COINGECKO_MONTHLY_CREDIT`, `_BUDGET_WARN/HIGH/PROTECT`, `_PROTECTIVE_TTL_MULTIPLIER` (`:287-328`) — TTLs stretch automatically as credit depletes | No — server-side, cached. |
| **Redis** | Optional cache + presence (+ pub/sub in the undeployed Command Center worker) | `services/cache_engine.py`, `services/sentinel/rate_limit.py`, `services/command_center_worker/redis_manager.py` | `REDIS_URL` | No, but see §3.3 — without it presence is per-worker. |
| **Apple IAP** | In-app purchases / credits | `services/pulse_apple_iap_credits.py`, `services/pulse_apple_server_api.py` | `APPLE_*` (7 keys) | No — and a **web store cannot use it**, so entitlement parity needs Stripe. |
| **CJ Dropshipping** | Supplier catalogue + fulfilment | `services/business_os/suppliers/*`, `supplier_worker.py` | 11 `CJ_*` keys; gated on `CJ_RECONCILIATION_ENABLED` | No. |
| **AI providers** | See §6 | `undx_router.py`, `services/pulse_ai_provider_router.py` | `OPENAI_API_KEY`, `CLAUDE_AI_API`, `Gemini_AI_API`, `DEEPSEEK_AI_API`, `GROQ_AI_API`, `META_MODEL_API_KEY`, `PERPLEXITY_API_KEY` | **No — and that is a stated invariant** (`undx_router.py:3-4`). |

**Only three provider values are ever legitimately client-visible:**
`STRIPE_PUBLISHABLE_KEY`, `AGORA_APP_ID`, `MUX_DATA_ENV_KEY` (plus the VAPID *public*
key). Everything else must stay behind the API in the web build exactly as it does in the
native build.

## 6. UNDX layer

UNDX is the largest single subsystem in the repo: **50 top-level services (39,109 lines) +
`services/undx_brain/` (20 files, 12,408) + 5 root modules (4,541)** ≈ **56,000 lines**.

### 6.1 Root modules

| Module | Lines | Role |
|---|---:|---|
| `undx_router.py` | 1,849 | Provider selection + failover for all UNDX chat |
| `undx_desktop_connector.py` | 1,171 | **A separate Flask app** meant to run on a local Mac, giving UNDX scoped filesystem access |
| `undx_execution_kernel.py` | 845 | Proposes and applies diffs against the repo |
| `undx_brain_layer.py` | 535 | Shared reasoning front door: mission classification, file selection, multi-agent review, safety |
| `undx_worker.py` | 141 | Deployed worker (§2.2) |

### 6.2 API surface — 29 routes

Two clusters:

- **Core** (`bot.py:29726-29956`): `POST /api/undx/chat`, `GET|POST /api/undx/agent-council`,
  `GET|POST /api/undx/desktop-connector/<path:connector_path>` (a proxy), and the kernel:
  `/api/undx/kernel/scan`, `/propose`, `/apply`, `/validate`, `/git`. Plus
  `GET /health/undx` (121057) and pages `/pulse/undx/actions` (55824),
  `/pulse/premium/undx` (56546).
- **Business OS UNDX** (`bot.py:26808-27047`): policies, requests, tools, permissions,
  confirmations, receipts, **emergency-stop**, decisions, action-center, and an
  agentic marketplace flow (`listings/draft` → `publish/plan` → `publish/execute`).
  This is a *separate, permission-gated* agent surface — note the plan/execute split and
  the explicit confirmation + receipt endpoints.

### 6.3 Provider routing

`undx_router.py:1-4`: *"Server-side provider selection for UNDX chat. The router never
exposes API keys to the browser and keeps OpenAI as the final fallback provider."*

**Seven providers**, not five (`undx_router.py:90-170`):

| Provider | Key env | Default model | Kill switch | JSON enforcement |
|---|---|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` | `UNDX_OPENAI_ENABLED` | `json_object` |
| Claude | `CLAUDE_AI_API` | `claude-haiku-4-5` | `UNDX_CLAUDE_ENABLED` | **none** — Messages API 400s on `response_format` |
| Gemini | `Gemini_AI_API` | `gemini-flash-lite-latest` | — | `response_mime_type` |
| DeepSeek | `DEEPSEEK_AI_API` | `deepseek-chat` | — | unknown (account returns 402) |
| Groq | `GROQ_AI_API` | `llama-3.1-8b-instant` | — | unknown |
| **Meta Muse** | `META_MODEL_API_KEY` | `muse-spark-1.3` | `META_MUSE_ENABLED` | `json_object` |
| **Perplexity** | `PERPLEXITY_API_KEY` | `sonar` | `UNDX_PERPLEXITY_ENABLED` | `json_schema` |

Design notes worth carrying into a web surface:

- **Structured output is a dialect, not a boolean** (`undx_router.py:44-52`): three vendors
  spell "must be JSON" three ways; Perplexity 400s on `json_object` but honours
  `json_schema`. Capability values were established by a live probe
  (`scripts/undx_structured_output_capability_probe.py`), not from docs.
- **A key alone is not consent** — per-provider `enable_env` kill switches exist so a
  provider can leave rotation without deleting the credential (`undx_router.py:62-64`).
- Per-provider timeouts and `reasoning_overhead_tokens` (Meta: 3,000) because reasoning
  tokens come out of the same `max_tokens` budget.
- **Known-broken today, per the source's own comments:** `GROQ_AI_API` holds a multi-line
  JSON document rather than a bare key, so `Bearer <value>` is an illegal header and every
  Groq attempt "spends a slot and a breaker increment on a request that was never sent"
  (`undx_router.py:~145`). DeepSeek returns 402 Insufficient Balance. Two of seven
  providers are dead weight in the failover chain.
- Supporting services: `undx_cost`, `undx_health`, `undx_privacy`, `undx_call_domain`
  (imported at `undx_router.py:20`), plus `undx_call_guard` which wraps `requests`/`urllib`
  **per interpreter** to count unrouted provider calls (`undx_worker.py:22-28`).
- A `COUNCIL_AGENT_PROVIDER_MAP` (`undx_router.py:172+`) maps named agents
  (e.g. "Architect Agent") to providers — that is what `/api/undx/agent-council` serves.

### 6.4 Security model — verified, and stronger than described

`undx_execution_kernel.py`:

- `APPROVAL_PHRASE = "APPROVE UNDX WRITE"` (`:26`) — **confirmed**.
- `PROTECTED_PATTERNS` (`:33-51`) — **confirmed and broader than the CLAUDE.md summary**:
  `.env`, `.env.`, `.git/`, `__pycache__/`, `venv/`, `.venv/`, `credentials`, `credential`,
  `secret`, `token`, `private_key`, `id_rsa`, `.pem`, `.key`, `.sqlite`, `.sqlite3`,
  `coinpilotx.db`.
- Repository containment to `DEFAULT_REPOSITORY_PATH` (`:25`) — note this is a
  **hardcoded absolute path** `/Users/hmcherie/Desktop/CoinPilotX`, which will not exist on
  Railway. The kernel is a local-operator tool, not a production capability.
- Backups to `.undx_backups`, audit log `undx_execution_log.jsonl` (`:27-28`).
- **Undocumented and important: a second approval tier.** `SELF_GOVERNING_PATTERNS`
  (`:66-73`) covers `undx_execution_kernel.py`, `undx_agent_policy.py`,
  `tests/protection/`, `scripts/protection/`, `.github/workflows/`, and
  `config/realtime-audio-protected-paths.json`; writes to those require
  `GUARD_APPROVAL_PHRASE = "APPROVE UNDX GUARD CHANGE"` (`:78`). The rationale in the
  source (`:56-65`) is precise: a single approved "routine refactor" of the kernel could
  empty `PROTECTED_PATTERNS` or blank the approval phrase, so guard changes escalate
  rather than being banned outright.
- `undx_brain_layer.py:18-33` — `PLANNING_ONLY_PHRASES` ("proposal only", "do not write",
  "analysis", …) force a mission into plan-only mode; `undx_execution_kernel.py:534`
  raises `KernelError("Brain Layer blocked legacy HTML generation for a planning-only
  mission.")`.
- `undx_desktop_connector.py:1-6` — "blocks protected paths, refuses path traversal,
  writes only approved proposal files, and runs only allowlisted validation/Git commands."

### 6.5 What a web UNDX surface needs

1. **Chat** — `POST /api/undx/chat` is a plain request/response JSON route, so it works in
   a browser today. But **there is no streaming**: no SSE/chunked token stream exists for
   UNDX, and §3.2 explains why one would be unwelcome on this gunicorn config. A web UNDX
   chat will feel slower than users expect unless the Command Center worker is stood up to
   host the stream.
2. **Agent council** — `/api/undx/agent-council` returns multi-agent output; a web UI
   needs per-agent progress, which again argues for streaming.
3. **Kernel routes must not be exposed to the public web.** `/api/undx/kernel/apply` and
   `/git` write to a hardcoded local path. Keep them on an operator/admin surface only.
   (Authorization is the other agent's scope; flagging the surface here.)
4. **Business OS UNDX** is the agentic surface that *should* be on the web: it already has
   the right shape — policies, permissions, confirmations, receipts, plan/execute split,
   and an emergency stop. A web client needs UI for `action-center`, `confirmations`, and
   `emergency-stop`.
5. **No keys client-side** — the router's stated invariant. A web UNDX surface must call
   our API, never a provider directly.

## 7. Other subsystems

### 7.1 Search — there is no search engine

**No Postgres FTS, no trigram, no external engine.** `grep` for `to_tsvector`, `tsquery`,
`pg_trgm`, `similarity(`, `fts5` across `bot.py` and `services/` returns **nothing**.

`GET /api/pulse/search` (`bot.py:41673`) is `LIKE ?` against
`pulse_posts.title/body/ai_tags_json/tags_json/post_type` and `pulse_comments.body`
(`bot.py:41709-41748`). `services/pulse_search_engine.py` is **29 lines** of in-Python
scoring: regex tokenise, `+8` per token found in a concatenated haystack, plus
`trust*0.18 + freshness*0.1`.

Search surfaces: `/api/pulse/search` (41673), `/api/pulse/users/search` (89881),
`/api/messages/users/search` (89882), `/api/pulse/messages/search` (92132),
`/api/pulse/marketplace/search` (54361), `/api/pulse/music/search` (42916),
`/api/crypto/assets/search` (8100), plus web pages `/search` (1996), `/pulse/search` (42041).

**Rebuild implication:** a web search UX (instant, typo-tolerant, faceted) has **no
backend to build on**. Leading-wildcard `LIKE '%term%'` cannot use an index, so this
degrades with table size. Either accept native-parity (exact-substring, small result sets)
or fund Postgres FTS/`pg_trgm` as a separate workstream. This is the largest capability gap
found.

### 7.2 Ranking and recommendations

`services/pulse_feed_engine.py` (3,077 lines) **is** the feed. The modules that sound like
ranking are stubs: `pulse_feed_ranking_engine` 51 lines, `reel_ranking_engine` 51,
`feed_ai_personalization` 26, `personalization_matrix` 31, `neural_discovery_engine` 13,
`social_loop_engine` 29. Real supporting logic: `feed_intelligence_service` (242),
`content_graph_intelligence_service` (332), `discovery_visibility` (65),
`live_ranking_engine` (64). Precompute jobs go to `pulse_jobs` — **drained only by the
orphaned `pulse_worker.py`** (§2.2).

### 7.3 Moderation and safety

`services/pulse_moderation_engine.py` (102), `services/ai_moderation_core.py` (33),
`services/scam_shield.py` (412) + `scam_shield_engine` (46), `pulse_ai_safety` (105),
`roast_safety_filter` (25), `services/sentinel/` (56 files, 12,653 lines — the real
security engine), `services/pulse_security_core.py` (267). Queues: `moderation_queue`,
`pulse_ad_moderation_queue`, `content_queue`, `review_queue`. Media carries
`moderation_status` on `chat_media_uploads` and is filtered at read time (`bot.py:87128`).
Admin surfaces: `/admin/media-moderation` (28210), `/admin/security-events` (28553).

### 7.4 Marketplace / commerce

27 top-level modules (10,397 lines) + `services/business_os/` (204 files, 65,881). The
reservation subsystem is the interesting part: `marketplace_reservation_policy` /
`_schema` / `_sweeper` / `_reconciler`. `pulse_worker.py:19-44` documents that the sweeper
is the single implementation of release and that the worker only provides scheduling — but
**that worker is not deployed**. Settlement: `marketplace_settlement_service`,
`seller_money`, `marketplace_payout_scheduler`, queues `payout_queue`, `refund_queue`,
`chargeback_queue`. Dropshipping: `services/business_os/suppliers/*` + CJ.

### 7.5 Payments / premium

`premium_entitlement_service` (1,108) is the entitlement authority.
`pulse_payment_router` + `payment_provider` abstract Stripe;
`stripe_webhook_verification` (171) validates webhooks. Two purchase rails:
**Stripe (web/native)** and **Apple IAP** (`pulse_apple_iap_credits` 446,
`pulse_apple_server_api` 217). A web store cannot use IAP — entitlements granted via
Apple must still resolve on web, so the web build depends on
`premium_entitlement_service` being rail-agnostic. Gates: `pro_access`,
`premium_capability_engine`, `premium_crypto_access`, `premium_visibility_engine`.

### 7.6 Notifications and push fan-out

`pulsesoc_notification_system` (2,979) + `notification_service` (2,306) +
`push_service` (1,285) + `notification_orchestrator` (152) + `notification_health_engine`
(201). Entry point `push_service.send_push(user_id, title, body, data, push_type)`
(`services/push_service.py:879`). Transports: APNs, FCM, **Web Push/VAPID (already
present)**. Queues: `push_delivery_jobs` (47 refs), `notification_delivery_jobs` (21),
`delivery_jobs`, `intelligence_delivery_jobs`. Email/SMS via Brevo through
`failed_email_queue` + the deployed `email_worker`.

**Web needs:** a service worker + `pushManager.subscribe()` with the VAPID public key, and
a device-registration call to whatever endpoint `push_service` reads. The server side
already exists; only the browser registration path is missing. **UNVERIFIED** whether the
existing device-token table accepts a web-push subscription shape.

### 7.7 Private Office

9 route modules (5,063 lines) + `services/private_office/` (40 files, 29,584). Sub-domains:
briefings, concierge, conversations, documents, meetings, relationships, shield, structured
records. All registered as route packs (`bot.py:1335-1380`). `private_office/meetings.py`
and `conversations.py` both reference Agora — meetings are RTC calls, so Private Office on
web inherits the §3.5 Agora Web SDK requirement. `PRIVATE_` has 14 env keys.

### 7.8 Market intelligence / crypto

`alert_engine` (4,277 — largest service), `market_intelligence` (1,406) + `_alerts` +
`_portfolio`, `market_observations` (734), `portfolio_service` (716),
`coingecko_client` (634) with a 16-key budget guard, `market_pulse`, `auto_signals_service`,
`crypto_alert_conditions`, `predictions_service`. Driven by the deployed `alert_worker`.
Legacy in origin (this repo began as CoinPilotX) but **actively deployed**, not dead.

### 7.9 Analytics / dashboards

23 modules, 15,446 lines. Eight `dashboard_*_command_center` modules (account, ads, ai,
creator, crypto, economy, intelligence, network) plus `pulsesoc_dashboard_centers` (1,520),
`pulse_dashboard_mission_control` (860), `pulsesoc_intelligence_engine` (3,081),
`pulsesoc_growth_engine` (800), `retention_analytics`, `conversion_funnel_engine`,
`event_stream_analytics`. These are **server-rendered admin surfaces today** — the single
biggest chunk of "website that is not the app".

### 7.10 Audit logging

`admin_audit_logs` is the shared table (`services/admin_gateway.py:145-175`), used for
access logging, login throttling and lockout. `services/audit_service.py` writes to it but
is **dead** (§1.2) — the live writer is `admin_gateway`. Per-feature audit tables are
declared in `services/backend_management_registry.py:49-70`
(`profile_audit_logs`, `account_audit_logs`, …). Also `services/pulse_mutation_audit.py`
and `/api/account/security-events` (84341).

### 7.11 Schema scale

`bot.py` contains **547 `CREATE TABLE IF NOT EXISTS` statements**, and more DDL lives in
`services/*/ensure_schema()` functions. CLAUDE.md's "~170 tables in `AUTO_PK_TABLES`" counts
one list, not the schema. There is no migration framework — schema is imperative and must be
idempotent. (Prior session notes put production at 884 tables; **UNVERIFIED here**, but 547
in `bot.py` alone is consistent with that and inconsistent with 170.)

## 8. Dependency map

Table names below were extracted from the 547 `CREATE TABLE IF NOT EXISTS` statements in
`bot.py` (511 distinct names); more are created by `ensure_schema()` in service modules.
Route line numbers are `bot.py` unless noted.

| Product area | Key routes | Services | DB tables | External | Workers |
|---|---|---|---|---|---|
| **Auth / identity** | `/api/mobile/auth/*`, `/api/account/*`, `/api/account/security-events` (84341) | `auth_service`*, `user_context`, `pulse_id_service`, `pulse_identity_engine`, `premium_identity_engine`, `services/sentinel/`, `pulse_security_core`, `admin_gateway` | `email_verification_tokens`, `password_reset_tokens`, `account_recovery_tokens`, `mobile_security_sessions`, `user_trusted_devices`, `admin_audit_logs`, `blocked_users` | Brevo (verification mail) | `email_worker` |
| **Feed** | `/api/pulse/feed*`, `/pulse` | `pulse_feed_engine` (3,077), `feed_intelligence_service`, `content_graph_intelligence_service`, `discovery_visibility`, `pulse_ai` | `pulse_posts`, `pulse_jobs`, `pulse_follows` | — | `pulse_worker` **(orphaned)** |
| **Posts** | `/api/pulse/posts*` | `pulse_feed_engine`, `media_service`, `pulse_moderation_engine`, `saved_content_service` | `pulse_posts`, `chat_media_uploads`, `moderation_queue` | R2, Mux | `media_worker` |
| **Comments** | `/api/pulse/reels/<id>/comments` (87423), `/api/pulse/videos/<id>/comments` (86361) | `pulse_feed_engine`, `pulse_moderation_engine` | `pulse_comments` | — | — |
| **Reels** | `POST /api/reels` \| `/api/pulse/reels/create` (87094), `/api/pulse/reels/feed` (86914), react/view/save/share/pin (87287–87855), `/api/pulse/reels/sounds/*` (86940, 87014, 87048) | `pulse_feed_engine`, `reel_ranking_engine`†, `media_service`, `music_service`, `media_covers`, `preview_service` | `pulse_reels`, `chat_media_uploads`, `pulse_comments` | **R2 + Mux** | `media_worker` |
| **Status** | `/api/pulse/status/*`, `/api/pulse/status/music/search` (42892) | `media_service`, `music_service` | `pulse_status`, `pulse_status_media`, `pulse_status_music`, `pulse_status_live` | R2 | `media_worker` |
| **Messaging** | `/api/pulse/messages/*` (89579–91241), `/api/pulse/communications/*` (90297–90637), `/api/messages/media/init\|upload\|complete\|attach` (91488–91545), `/api/pulse/typing` (89301) | `pulsesoc_communications_engine` (2,330), `messenger_media_foundation` (1,131), `messenger_intelligence_service`, `pulse_chat_bridge`, `chat_health_service`, `command_center_client` | `pulse_messages`, `pulse_conversations`, `chat_media_uploads` | R2 | `media_worker` |
| **Notifications** | `/api/pulse/notifications/*`, `/unread-count` (38343) | `pulsesoc_notification_system` (2,979), `notification_service` (2,306), `push_service` (1,285), `notification_orchestrator`, `notification_health_engine` | `notification_jobs`, `notification_logs`, `notification_delivery_logs`, `notification_preferences`, `notification_schedules`, `notification_failures`, `push_delivery_jobs`, **`push_subscriptions`**, `user_device_tokens`, `pulse_notification_devices`, `expo_push_tickets` | APNs, FCM, **Web Push/VAPID**, Brevo | `email_worker` (mail); push fan-out is in-request |
| **Profile** | `/api/pulse/profile*`, `/api/pulse/users/search` (89881) | `pulse_profile_service`, `profile_viewer_permissions`, `premium_identity_engine`, `user_trust_engine` | `creator_profiles`, `pulse_user_badges`, `pulse_user_privileges`, `profile_audit_logs` | R2 (avatars) | `media_worker` |
| **Social graph** | `POST /api/pulse/follows/toggle` (89538) | `pulse_social_graph_service`, `social_relationship_service`, `reputation_economy_engine`† | `pulse_follows`, `blocked_users` | — | — |
| **Search** | `/api/pulse/search` (41673) + 8 others (§7.1) | `pulse_search_engine` (**29 lines**) | `pulse_posts`, `pulse_comments` (via `LIKE`) | **none** | — |
| **Marketplace** | `/api/pulse/marketplace/*` (54361, 94377, 94498, 94556), cart/offers/returns route packs (`bot.py:1285-1287`) | 27 modules + `services/business_os/` (204 files); `marketplace_reservation_sweeper`, `_settlement_service`, `seller_money`, `seller_lifecycle` | `marketplace_listings`, `marketplace_orders`, `marketplace_sellers`, `marketplace_inventory_reservations`, `marketplace_product_media`, `marketplace_digital_files`, `marketplace_merchant_applications`/`_documents`, `marketplace_reports`, `marketplace_saved_products`, `payout_queue`, `refund_queue`, `chargeback_queue` | **Stripe**, R2, CJ | `pulse_worker` **(orphaned — reservation sweep)**, `supplier_worker` **(orphaned)** |
| **Payments / premium** | `/api/pulse/premium/*`, Stripe webhook, `/pulse/premium/undx` (56546) | `premium_entitlement_service` (1,108), `pulse_payment_router`, `payment_provider`, `stripe_webhook_verification`, `pulse_apple_iap_credits`, `platform_treasury_service`, `pro_access` | `premium_entitlements`, `pulse_ad_wallet_funding_sessions` | **Stripe**, **Apple IAP** | — |
| **Advertising** | `/api/pulse/ads/*` (19426–20208), `/admin/business-os/advertising/*` (24202) | 11 modules (11,213 lines): `pulse_ads_service`, `pulse_ads_os`, `pulse_ad_payments`, `pulse_ads_adsets/_audiences/_insights/_library/_reporting`, `pulse_advertiser_portal`, `ad_policy_engine` | `pulse_ad_campaigns`, `_creatives`, `_impressions`, `_clicks`, `_events`, `_billing_events`, `_invoices`, `_receipts`, `_refunds`, `_placements`, `_frequency_caps`, `_idempotency`, `_moderation_queue`, `_policy_flags`, `_review_board`, `_media_assets`, `_accounts`, `pulse_ad_jobs` | **Stripe**, R2 | **`ads_worker`** (deployed) |
| **Business OS** | `/api/business-os/*` (199 routes), `/admin/business-os/*` | `services/business_os/` (204 files, 65,881 lines) + 4 route packs | `business_os_*`, `business_os_supplier_sync_jobs` | Stripe, CJ | `supplier_worker` **(orphaned)** |
| **UNDX** | `/api/undx/*` (29726–29956), `/api/business-os/undx/*` (26808–27047), `/health/undx` (121057) — 29 routes | 50 services + `services/undx_brain/` + 5 root modules (~56k lines) | `undx_*` run/mission/cost/health tables | **7 AI providers** (OpenAI, Claude, Gemini, DeepSeek, Groq, Meta, Perplexity) | **`undx_worker`** (deployed) |
| **Private Office** | 9 route packs (`bot.py:1335-1380`) | 9 route modules + `services/private_office/` (40 files, 29,584) | `private_office_*` | **Agora** (meetings), R2 (documents) | — |
| **Live** | `/pulse/live/studio/<id>` (49100), `/api/pulse/live/mux/<id>` (49896), `/api/pulse/live/<id>/browser-publish` (51458), `/api/pulse/live/stream` SSE (89360, **flagged off**) | `mux_live_service`, `agora_media_push_service`, `agora_cloud_recording_service`, `live_participants`, `live_distribution_service`, `live_archive_service`, `live_stream_health_service`, `live_discovery_service`, `live_restream_service` | `pulse_live_streams`, `_sessions`, `_viewers`, `_guests`, `_guest_requests`, `_chat`, `_reactions`, `_clips`, `_destinations`, `_restream_targets`, `_scene_presets`, `_moderation`, `_reports`, `_provider_events`, `_webrtc_signals`, `_audio_profiles`, `_audit_logs`, `livestream_access`, `livestream_eligibility` | **Agora → Mux** | `media_worker` (`finalize_live_replay`) |
| **Calls** | Private Office meetings; Agora token mint | `services/private_office/meetings.py`, `live_participants`, `agora_*` | `pulse_live_webrtc_signals`, `private_office_*` | **Agora** | — |
| **Media** | 11 upload routes + the 7-step presign flow (103087–103151) + Mux direct upload (103186, 103281) | `media_service` (1,365), `media_storage`, `media_upload_sessions`, `upload_progress_service`, `media_covers`, `preview_service`, `embed_service` | `chat_media_uploads`, `pulse_media_upload_sessions` | **R2**, **Mux**, ffmpeg | **`media_worker`** (deployed) |
| **Crypto / alerts** | `/api/crypto/*`, `/api/alerts/events` (37669), `/api/live/news` (30098) | `alert_engine` (4,277), `coingecko_client`, `market_intelligence*`, `portfolio_service`, `auto_signals_service`, `market_observations` | `user_alerts`, `user_alert_rules`, `alert_delivery_jobs` | **CoinGecko**, Telegram | **`alert_worker`** (deployed) |
| **Arena** | `/api/arena/*` (120 routes), `/api/arena/realtime/<kind>/<id>` (36576) + `/stream` (36592, **flagged off**) | 19 arena modules + `realtime_service` | `arena_*`, `arena_live_matches`, `arena_play_sessions` | — | — |

\* dead module (§1.2) — the name is a decoy.  † stub under 60 lines (§1.2).

### 8.1 Top shared-backend risks for the web rebuild

1. **The gunicorn config forbids the realtime architecture a web app expects.** 4 sync
   workers × 8 threads, and `bot.py:89367` explicitly disables SSE to protect that pool.
   Web must poll, or `services/command_center_worker/` must be promoted to a deployed
   service with Redis pub/sub. Adding browser tabs to a polling backend multiplies load
   against 32 threads.
2. **R2 bucket CORS is unconfigured and unverifiable from the repo.** Direct-to-R2 upload
   is the only path for anything over 12 MB, and it cannot work from a browser without a
   CORS policy exposing `ETag`. This will be the first thing to break.
3. **Three workers are not deployed** — and one of them (`pulse_worker`) owns both feed
   precompute (`pulse_jobs`) and the marketplace reservation sweep. Building web features
   on either will surface an existing production gap.
4. **Search does not exist.** `LIKE '%term%'` plus 29 lines of Python scoring. Any web
   search UX is a new backend workstream, not an integration.
5. **Agora, not LiveKit** — and the code still says "livekit" in enum values, state
   machines, and telemetry. A web team reading CLAUDE.md or grepping for the transport will
   reach the wrong conclusion and may attempt a LiveKit web client against an Agora
   backend. The real-time audio foundation is hard-protected; do not modify it.

Runners-up: the ffmpeg-missing fallback that sets `thumbnail_url = media_url` (§4.4);
Apple IAP entitlements having no web purchase rail (§7.5); ~60 live-but-vestigial
`*_engine.py` stubs whose names promise capability they do not have (§1.2); and 547
`CREATE TABLE` statements with no migration framework.
