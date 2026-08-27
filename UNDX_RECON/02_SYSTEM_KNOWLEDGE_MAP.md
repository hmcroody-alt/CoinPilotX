# 00 — SYSTEM ARCHITECTURE MAP

Read-only reconnaissance of PulseSoc / CoinPilotX. Every claim below was verified against
the working tree unless explicitly marked `UNVERIFIED`. Legacy `mobile/` is excluded by
mission scope.

Corrections to `CLAUDE.md` are flagged inline as **CORRECTION**.

---

## LAYER A — RUNTIME TOPOLOGY

### A.0 Process manifest

`Procfile` (verified, 5 lines):

```
web:          gunicorn bot:app --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 4 --timeout 120
undx_worker:  python undx_worker.py
email_worker: python email_worker.py
ads_worker:   python pulse_ads_worker.py
alert_worker: python alert_worker.py
```

**CORRECTION:** the Procfile process is named `ads_worker`, not `pulse_ads_worker`.
`media_worker.py`, `pulse_worker.py` and `telegram_worker.py` are **absent from the
Procfile** and therefore do not run in production. Anything that depends on them is
permanently pending in prod.

Build: `nixpacks.toml` → `nixPkgs = ["python311", "ffmpeg"]`. Host: Railway.

---

SYSTEM: web (gunicorn)
PURPOSE: Serves the entire HTTP surface — 1,713 route decorators in `bot.py` plus 8
fail-soft blueprint packs. Also renders the server-side Jinja PWA.
LOCATION: `Procfile:1`; app object `bot:app` (`bot.py:1190`).
STATUS: LIVE. 2 workers x 4 threads, 120s timeout. Single-process-per-worker means
`init_db()` and `ensure_all_once()` latches are per-worker, not per-deploy.
DEPENDENCIES: `bot.py`, `services/db.py`, DATABASE_URL, all route packs.

SYSTEM: undx_worker
PURPOSE: Polls the UNDX mission runtime and logs AI provider availability.
LOCATION: `undx_worker.py` (105 lines). `WORKER_NAME = "coinpilotx-undx-worker"`.
STATUS: LIVE (in Procfile). Loop interval `UNDX_WORKER_SLEEP_SECONDS`, default 60,
floored at 15 via `max(15, …)`. Each cycle calls `undx_router.log_provider_status()`
then `undx_mission_runtime.poll_once()`. Emits a `UNDX_WORKER_START` JSON line carrying
boolean key-presence for openai / claude / gemini / deepseek / groq (never the keys).
Heartbeat: writes to `worker_heartbeats` via `bot.record_worker_heartbeat`, gated on
`UNDX_WORKER_HEARTBEAT_ENABLED`.
DEPENDENCIES: `undx_router.py`, `services/undx_mission_runtime.py` (untracked file — new
in the working tree), `bot.record_worker_heartbeat`.

SYSTEM: email_worker
PURPOSE: Drains the outbound email delivery queue.
LOCATION: `email_worker.py` (45 lines).
STATUS: LIVE. Interval `EMAIL_WORKER_INTERVAL_SECONDS` default 10 (clamped 2–300),
batch `EMAIL_WORKER_BATCH_SIZE` default 20 (clamped 1–50). Calls
`bot.process_email_delivery_jobs(limit=batch_size)`. Sets
`COINPILOTX_INIT_DB_ON_IMPORT=0` and `EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED=0` *before*
`import bot` — so the worker neither re-creates schema nor competes with the web
process's opportunistic in-request email flush.
**Writes no heartbeat.** It is invisible to `worker_heartbeats`-based health views.
DEPENDENCIES: `bot.process_email_delivery_jobs`, Brevo.

SYSTEM: alert_worker
PURPOSE: The crypto/market side. Evaluates user alerts, samples the market board, runs
auto-signals, and drives Sentinel scheduled ingestion.
LOCATION: `alert_worker.py` (95 lines).
STATUS: LIVE. Interval `ALERT_WORKER_INTERVAL_SECONDS` default 45 (min 15),
`ALERT_WORKER_BATCH_LIMIT` 500, `SAMPLE_BOARD_LIMIT` 80.
Cycle order: `auto_signals_service.process_enabled_users(limit=200)` →
`_sample_market()` (`live_market_service.get_crypto_market` then
`market_observations.record_board`) → `alert_engine.evaluate_all_active_alerts(limit,
worker_name="alert_worker")` → `sentinel_runtime.run_scheduled_ingestion()`.
Heartbeat via `alert_engine.record_worker_heartbeat("alert_worker", …)`.
DEPENDENCIES: `services/auto_signals_service.py`, `services/live_market_service.py`,
`services/market_observations.py`, `services/alert_engine.py`,
`services/sentinel/runtime`, CoinGecko.

SYSTEM: ads_worker
PURPOSE: Asynchronous advertising back-office — job queue, operations sweep,
attribution, billing reconciliation, reporting rollups.
LOCATION: `pulse_ads_worker.py` (163 lines), engine in
`services/pulse_ads_worker_service.py`. `WORKER_NAME = "ads_worker"`.
STATUS: LIVE. Base loop `ADS_WORKER_SLEEP_SECONDS` 20 (min 5). Five cadences:
- jobs queue — every cycle
- operations sweep — every cycle
- attribution — every `ADS_WORKER_ATTRIBUTION_SECONDS` (300)
- billing reconciliation — every `ADS_WORKER_BILLING_SECONDS` (600), **report-only**
- reporting rollups — every `ADS_WORKER_REPORTING_SECONDS` (300)
`recover_orphaned_jobs()` re-queues anything stuck in `processing` for >10 minutes.
`_configure_logging()` deliberately attaches a stdout handler because `import bot`
installs a `RotatingFileHandler` that would otherwise swallow all worker logs on Railway.
Module docstring is load-bearing: *"It never charges wallets — the synchronous delivery
path owns money and is idempotent."*
DEPENDENCIES: `services/pulse_ads_worker_service.py`, ad ledger tables, Business OS
ledger schema (see Layer G caveat — flag-dark by default).

SYSTEM: media_worker
PURPOSE: Thumbnail generation, video transcode, and **live replay finalization**.
LOCATION: `media_worker.py` (847 lines). `WORKER_NAME = "coinpilotx-media-engine"`.
STATUS: **NOT IN PROCFILE — DOES NOT RUN IN PRODUCTION.** This is the single most
consequential gap in the runtime topology.
Config: `MEDIA_WORKER_INTERVAL_SECONDS` 5, `MEDIA_WORKER_BATCH_SIZE` 25 (max 100),
`MEDIA_WORKER_MAX_ATTEMPTS` 3,
`MEDIA_JOB_TYPES = {"generate_thumbnail", "process_video", "finalize_live_replay"}`.
`main()` at line 809; heartbeats `booting` / `healthy` / `error`.
Refuses to boot on Railway without `DATABASE_URL` (`sys.exit(78)`).
DEPENDENCIES: `services/agora_cloud_recording_service.py`,
`services/agora_media_push_service.py`, `services/media_covers.py`,
`services/media_service.py`, `services/media_storage.py`,
`services/mux_live_service.py`, ffmpeg, R2.

SYSTEM: pulse_worker
PURPOSE: Social feed job processing plus scheduled AI posts into Spaces.
LOCATION: `pulse_worker.py` (75 lines).
STATUS: **NOT IN PROCFILE — DOES NOT RUN IN PRODUCTION.**
`PULSE_WORKER_SLEEP_SECONDS` 20 (min 5), `PULSE_WORKER_BATCH_SIZE` 12. Cycle:
`pulse_feed_engine.process_pending_jobs()` then
`pulse_ai.run_due_space_ai_posts(cur, bot.PULSE_SPACES, …, limit=24)`.
Heartbeats as `pulse_worker`.
DEPENDENCIES: `services/pulse_feed_engine.py`, `services/pulse_ai/`,
`services/pulse_ai/space_post_scheduler.py`.

SYSTEM: telegram_worker
PURPOSE: Telegram bot long-polling.
LOCATION: `telegram_worker.py` (15 lines) — a thin `bot.main()` call.
STATUS: **NOT IN PROCFILE.** Telegram inbound therefore only works if a webhook is
configured against the `web` process; polling is dead.
DEPENDENCIES: `python-telegram-bot==22.7`, `TELEGRAM_BOT_TOKEN`.

SYSTEM: worker heartbeat substrate
PURPOSE: Liveness telemetry for workers.
LOCATION: table DDL `bot.py:110186` (`CREATE TABLE IF NOT EXISTS worker_heartbeats`),
index `bot.py:112812` (`idx_worker_heartbeats_seen`), writer
`bot.record_worker_heartbeat(...)` at `bot.py:117602`.
STATUS: PARTIAL. Three of the four Procfile workers heartbeat
(`undx_worker` — and only when `UNDX_WORKER_HEARTBEAT_ENABLED` is on —, `alert_worker`,
`ads_worker`). `email_worker` does not. Any dashboard that infers "all workers healthy"
from this table is structurally unable to detect an email_worker outage.
DEPENDENCIES: `services/db.py`.

SYSTEM: command_center_worker
PURPOSE: Appears to be a standalone Flask app skeleton for an operations command center.
LOCATION: `services/command_center_worker/` (13 .py, entrypoint `app.py`).
STATUS: **UNWIRED.** Not in the Procfile, not imported by `bot.py`. It appears only as a
string label in `services/backend_management_registry.py:161` and
`services/notification_health_engine.py:110` — i.e. the ops UI lists a worker that has no
process behind it.
DEPENDENCIES: none active.

### A.1 Scheduled jobs — required output

**Finding: there is no APScheduler-driven scheduler anywhere in first-party code.**

`APScheduler==3.11.2` is present in `requirements.txt`, but it is a **transitive
dependency of `python-telegram-bot`'s JobQueue**. A repo-wide grep across `bot.py`,
`services/`, `scripts/`, `tests/` and the root `*.py` workers for
`APScheduler`, `BackgroundScheduler`, `BlockingScheduler`, `add_job`, `CronTrigger`,
`IntervalTrigger` returned **zero first-party call sites**.

**CORRECTION:** any documentation or mental model that describes "APScheduler scheduled
jobs" is wrong. All periodic work in this system is implemented as `while True:` loops in
the standalone worker processes, with cadence enforced by monotonic-clock deltas.

The complete scheduled-job inventory, therefore, is the following cadence gates:

| Job | Owner process | Cadence (default) | Env override | Runs in prod? |
|---|---|---|---|---|
| UNDX provider status log | undx_worker | 60s (floor 15s) | `UNDX_WORKER_SLEEP_SECONDS` | YES |
| UNDX mission poll (`poll_once`) | undx_worker | 60s | same | YES |
| Email queue drain (batch 20) | email_worker | 10s (clamp 2–300) | `EMAIL_WORKER_INTERVAL_SECONDS` | YES |
| Auto-signals for enabled users (limit 200) | alert_worker | 45s (min 15) | `ALERT_WORKER_INTERVAL_SECONDS` | YES |
| Market board sample (limit 80) | alert_worker | 45s | same | YES |
| Alert evaluation (limit 500) | alert_worker | 45s | `ALERT_WORKER_BATCH_LIMIT` | YES |
| Sentinel scheduled ingestion | alert_worker | 45s | same | YES |
| Ads job queue drain | ads_worker | 20s (min 5) | `ADS_WORKER_SLEEP_SECONDS` | YES |
| Ads operations sweep | ads_worker | 20s | same | YES |
| Ads orphan-job recovery (>10 min in `processing`) | ads_worker | 20s | hardcoded 10 min | YES |
| Ads attribution | ads_worker | 300s | `ADS_WORKER_ATTRIBUTION_SECONDS` | YES |
| Ads billing reconciliation (report-only) | ads_worker | 600s | `ADS_WORKER_BILLING_SECONDS` | YES |
| Ads reporting rollups | ads_worker | 300s | `ADS_WORKER_REPORTING_SECONDS` | YES |
| Thumbnail generation | media_worker | 5s | `MEDIA_WORKER_INTERVAL_SECONDS` | **NO** |
| Video processing | media_worker | 5s | same | **NO** |
| Live replay finalization | media_worker | 5s | same | **NO** |
| Pulse feed pending jobs | pulse_worker | 20s (min 5) | `PULSE_WORKER_SLEEP_SECONDS` | **NO** |
| Space AI post publication (limit 24) | pulse_worker | 20s | same | **NO** |
| Telegram polling | telegram_worker | continuous | — | **NO** |

Two further scheduling-adjacent mechanisms exist but are *not* time-triggered:
- `services/marketplace_payout_scheduler` (imported at `bot.py:100377`) — invoked
  in-request, not on a timer.
- `services/pulse_ai/space_post_scheduler.publish_space_ai_post` (imported at
  `bot.py:93634`) — the publication primitive that `pulse_worker` would call. Because
  `pulse_worker` is not deployed, scheduled Space AI posts never fire in production.

---

## LAYER B — THE FLASK APP OBJECT

SYSTEM: `bot:app` / double-Flask construction
PURPOSE: The single WSGI application object.
LOCATION: `bot.py` — first `webhook_app = Flask(...)` at **line 429** with
`app = webhook_app` at 438; second `webhook_app = Flask(...)` at **line 1181** with
`app = webhook_app` at 1190.
STATUS: The quirk is REAL — the module genuinely constructs two Flask instances and the
second rebinding wins, discarding the first.
**CORRECTION 1:** the line numbers are 429 / 1181, not 384 / 1130 as `CLAUDE.md` states.
**CORRECTION 2 (the important one):** *nothing meaningful is lost.* I read every
statement between 429 and 1181 that touches `webhook_app`. There are exactly two:
`webhook_app.secret_key = …` and `webhook_app.config.update(…)`. Both are re-applied
**identically** after line 1181. No route, blueprint, error handler, before_request hook,
extension, or context processor is attached in the dead window.
The practical risk is prospective, not current: any future code inserted between 429 and
1181 that attaches to `webhook_app` will vanish silently, with no error and a green boot.
DEPENDENCIES: gunicorn entrypoint `bot:app`.

SYSTEM: duplicate `init_db` definition (previously undocumented)
PURPOSE: n/a — dead code.
LOCATION: `def init_db()` at **`bot.py:805`** (body ≈805–910) is shadowed by a second
`def init_db()` at **`bot.py:104466`**, which delegates to `_init_db_impl()` at
**`bot.py:104517`**.
STATUS: **DEAD CODE / LATENT HAZARD.** Same failure shape as the Flask double-bind: two
definitions, later one silently wins, ~105 lines of schema logic at 805 never execute.
This is not mentioned in `CLAUDE.md`. Anyone adding a table to the 805 version would see
it silently not appear.
DEPENDENCIES: `services/db.py`.

SYSTEM: request/response middleware and SEO runtime
PURPOSE: Injects SEO config into every template render; root redirect.
LOCATION: `@webhook_app.context_processor inject_seo_runtime_config` at `bot.py:1288`;
`@webhook_app.route("/")` at `bot.py:1310` → redirects to `/pulse`.
STATUS: LIVE. Note `bot.py:347-348` imports `from seo import schema as seo_schema` and
`from seo.content import (...)` — therefore the root `seo/` directory
(`__init__.py`, `content.py`, `schema.py`) is **live application code, not debris**.
DEPENDENCIES: `seo/`.

SYSTEM: `cancel_scheduled_account_deletion`
PURPOSE: Reverses a pending account deletion when the user logs back in.
LOCATION: `bot.py:1268`, called from login paths; the companion
`cancel_pending_deletion` is exported by `services/pulse_settings_routes.py`.
STATUS: LIVE.
DEPENDENCIES: account deletion tables.

---

## LAYER C — ROUTE PACK / BLUEPRINT REGISTRATION

SYSTEM: fail-soft route pack loader
PURPOSE: Registers optional blueprint packs such that one broken feature cannot block
boot, while still recording *which* pack died.
LOCATION: `bot.py:1206` `ROUTE_PACK_STATUS = {}`; `bot.py:1209` `_record_route_pack`;
`bot.py:1225` `_load_route_pack`.
STATUS: LIVE and better-instrumented than the docs suggest. On failure it emits
`logging.critical("ROUTE_PACK_REGISTRATION_FAILED …")` or `ROUTE_PACK_IMPORT_FAILED`,
whose message explicitly says *"every endpoint in this pack will 404"*. Status is exposed
at the **unauthenticated** endpoint `/health/routes` (`bot.py:115206`) — that is the
correct first stop when a feature 404s in prod.
FAILURE MODE: import error or `register()` exception → pack is skipped, boot succeeds,
every endpoint in that pack 404s. There is no retry and no alert wired to this.
DEPENDENCIES: the 8 packs below.

The 8 registration sites, `bot.py:1247-1264`:

| # | Pack name | Module | Blueprint | URL prefix | Endpoints |
|---|---|---|---|---|---|
| 1 | `pulse_communications_v2` | `pulse_communications_v2.routes` | `comm_v2_blueprint` | `/api/pulse/communications/v2` | **158** |
| 2 | `pulse_presence` | `services.presence_routes` | `presence_blueprint` | `/api/pulse/presence` | 9 |
| 3 | `pulse_mobile_settings` | `services.pulse_settings_routes` | `settings_blueprint` | `/api/pulse/mobile/settings` | 12 |
| 4 | `pulse_marketplace_cart` | `services.marketplace_cart_routes` | `cart_blueprint` | `/api/pulse/marketplace/cart` | 8 |
| 5 | `pulse_marketplace_offers` | `services.marketplace_offers_routes` | `offers_blueprint` | `/api/pulse/marketplace/offers` | 7 |
| 6 | `pulse_marketplace_returns` | `services.marketplace_returns_routes` | `returns_blueprint` | `/api/pulse/marketplace/returns` | 6 |
| 7 | `business_os_web` | `services.business_os_web` | `business_os_web_bp` | — (page route) | 1 + hook |
| 8 | `business_os_commerce` | `services.business_os_commerce_routes` | `commerce_blueprint` | `/api/business-os` | 1 + **37** |

SYSTEM: pulse_communications_v2
PURPOSE: The modern messaging/communities/realtime stack — conversations, messages,
communities, moderation, presence heartbeat, SSE stream, voice/video session start, Mux
live bridge, AI smart-replies and summaries, control-center.
LOCATION: `pulse_communications_v2/routes.py`, `API_PREFIX =
"/api/pulse/communications/v2"`, `register()` at line 1698.
STATUS: LIVE. 158 endpoints (73 GET, 77 POST, 5 PATCH, 2 DELETE, 1 `.route`). This is the
largest single pack and the highest-blast-radius failure: if it fails to import, all of
chat, communities, presence and call initiation 404 while the app reports healthy.
DEPENDENCIES: Agora, Mux, SSE, AI provider layer.

SYSTEM: marketplace cart / offers / returns
PURPOSE: Buyer-side commerce primitives split into three packs.
LOCATION: `services/marketplace_cart_routes.py` (`register()` at 940),
`services/marketplace_offers_routes.py` (709),
`services/marketplace_returns_routes.py` (470).
STATUS: LIVE-but-flagged. Cart is additionally gated by `MARKETPLACE_CART_ENABLED`.
Cart endpoints: `/checkout-options`, root GET/POST, `/<line_id>` PATCH/POST,
`/<line_id>` DELETE, `/<line_id>/confirm-price`, `/validate`, `/checkout`.
Offers: POST/GET root, `accept`, `decline`, `withdraw`, `counter`, `checkout`.
Returns: POST/GET root, `/<return_id>`, `message`, `resolve`, `escalate`.
DEPENDENCIES: `services/pulse_payment_router.py`, marketplace tables.

SYSTEM: business_os_web + before_request schema bootstrap
PURPOSE: Registers the Business OS web page and installs a lazy schema bootstrap hook.
LOCATION: `services/business_os_web.py`:

```python
def register(app):
    app.register_blueprint(business_os_web_bp)
    app.before_request(_bootstrap_business_os_schema_if_needed)
    return True
```

STATUS: LIVE but effectively inert by default. The hook only fires for request paths
starting `/api/business-os` or `/business-os`, calls
`schema_bootstrap.ensure_all_once()`, and never raises. Because
`ensure_all_once()` itself no-ops unless at least one `BUSINESS_OS_*` flag is truthy, and
all such flags are blank in `.env.example`, the entire Business OS surface is dark by
default (see Layer G).
DEPENDENCIES: `services/business_os/schema_bootstrap.py`.

SYSTEM: business_os_commerce gateway
PURPOSE: 37 commerce API endpoints registered programmatically rather than by decorator.
LOCATION: `services/business_os_commerce_routes.py` (`register()` at 127) binds
`services/business_os/commerce_gateway.py::ROUTES` (line 277,
`API_PREFIX = "/api/business-os"`) via `add_url_rule`.
STATUS: LIVE-if-flagged. `register()` calls `gw.ensure_schemas()` inside a **bare
try/except** — a schema failure is swallowed and the routes still register, so endpoints
can be reachable while their tables do not exist. That is precisely the 2026-08-07
incident shape documented in `schema_bootstrap.py`.
Coverage: offers, returns, inventory, listing drafts, seller dashboard, reports, store
policies, storefront versions.
DEPENDENCIES: Business OS ledger + commerce schema.

SYSTEM: Sentinel admin API — **UNREGISTERED**
PURPOSE: 18 read-only admin endpoints for the Sentinel intelligence subsystem.
LOCATION: `services/sentinel/api.py` —
`sentinel_bp = Blueprint("sentinel", __name__, url_prefix="/api/admin/sentinel")`,
18 GET endpoints, admin-session-gated `before_request`.
STATUS: **DEAD SURFACE — deliberately not registered.** Module docstring: *"DELIBERATELY
NOT REGISTERED with bot.py in V1"*, with the stated rationale that `bot.py` is under
concurrent change and protected by the audio diff gate, and that exposing a privileged
surface is an owner decision (SC10). Meanwhile `alert_worker` *does* run
`sentinel_runtime.run_scheduled_ingestion()` every 45s — so Sentinel is **ingesting data
that nothing can read over HTTP**.
DEPENDENCIES: `services/sentinel/` (53 modules).

### C.1 Route family volumes (verified counts)

`/api/pulse` 412 · `/api/business-os` 203 · `/api/arena` 120 · `/admin/business-os` 66 ·
`/api/admin` 47 · `/api/dashboard` 29 · `/api/crypto` 25 · `/api/pages` 21 ·
`/api/mobile` 20 · `/api/messages` 17 · `/api/account` 16 · `/admin/users` 15 ·
`/api/reels` 12 · `/api/progress` 11 · `/api/alerts` 9 · `/pulse/premium` 8 ·
`/api/undx` 8 · `/api/push` 8 · `/api/payments` 8.

**CORRECTION:** `CLAUDE.md` states 1,538 routes and `/api/pulse` 323; the tree currently
has **1,713** `@webhook_app.route` decorators and **412** `/api/pulse` paths. `bot.py` is
**117,902** lines, not 111k.

---

## LAYER D — services/ TAXONOMY

**285 top-level `.py` modules** in `services/` (CLAUDE.md says 239 — **CORRECTION**),
plus 7 subpackages.

### D.0 Subpackages

| Subpackage | Files | Role | Status |
|---|---|---|---|
| `business_os/` | 179 .py across 25 subdirs | Commerce/ledger/advertising back-office | Flag-dark by default |
| `sentinel/` | 53 | Intelligence ingestion + 18-endpoint admin API | Ingests; API unregistered |
| `undx_brain/` | 20 | UNDX cognition layer, incl. `config.py` (modified in tree) | LIVE |
| `command_center_worker/` | 13 | Standalone Flask ops app | UNWIRED |
| `intelligence_collectors/` | 11 | Data collectors feeding Sentinel | LIVE via alert_worker |
| `pulse_ai/` | 9 | Social AI: Space posts, smart replies, summaries | Partially dark (needs pulse_worker) |
| `providers/` | 6 | Third-party provider adapters | LIVE |

### D.1 Functional families (top-level modules)

SYSTEM: Social core (feed / posts / reels / spaces)
PURPOSE: The primary product surface — timeline assembly, ranking, reels, Spaces.
LOCATION: `services/pulse_feed_engine.py`, `pulse_feed_*`, `pulse_post_*`,
`pulse_reels_*`, `pulse_spaces_*`, `pulse_ranking_*`, `pulse_discovery_*`.
STATUS: LIVE for read/write paths served synchronously by `web`. **Asynchronous** feed
work (`process_pending_jobs`) is DARK because `pulse_worker` is not deployed.
DEPENDENCIES: db, media pipeline, R2.

SYSTEM: Messaging & communications
PURPOSE: DMs, group chats, communities, presence, SSE realtime.
LOCATION: `pulse_communications_v2/` (pack), `services/presence_routes.py`,
`services/messaging_*`, `services/pulse_messages_*`.
STATUS: LIVE.
DEPENDENCIES: pack #1 registration, SSE, db.

SYSTEM: Realtime media (calls / live)
PURPOSE: Voice/video calls and live streaming.
LOCATION: `services/call_engine.py`, `services/agora_*` (token, cloud recording, media
push), `services/mux_live_service.py`, `services/media_service.py`,
`services/media_storage.py`, `services/media_covers.py`.
STATUS: LIVE for session start/join. **Replay/archive finalization is DARK**
(`finalize_live_replay` is a media_worker job type).
DEPENDENCIES: Agora, Mux, R2, ffmpeg, media_worker (missing).

SYSTEM: Marketplace & commerce
PURPOSE: Listings, cart, offers, returns, seller onboarding, payouts.
LOCATION: `services/marketplace_*` (cart/offers/returns routes, payout scheduler,
listing, inventory), `services/business_os_commerce_routes.py`,
`services/business_os/commerce_gateway.py`.
STATUS: MIXED — cart/offers/returns packs LIVE; Business OS commerce gateway flag-dark;
mobile checkout hidden by `DIGITAL_COMMERCE_ENABLED=false`.
DEPENDENCIES: `pulse_payment_router`, Stripe, Stripe Connect.

SYSTEM: Payments & monetization
PURPOSE: Server-authoritative routing of every money movement to the correct provider.
LOCATION: `services/pulse_payment_router.py`, `services/stripe_*`,
`services/apple_iap_*`, `services/premium_*`, `services/wallet_*`,
`services/ledger_*`.
STATUS: LIVE (see Layer G for the router's refusal semantics).
DEPENDENCIES: Stripe, Apple App Store Server API, internal ledger.

SYSTEM: Advertising
PURPOSE: Campaigns, targeting, delivery, wallet spend, attribution, reporting.
LOCATION: `services/pulse_ads_*` (incl. `pulse_ads_worker_service.py`),
`services/business_os/advertising/`.
STATUS: LIVE synchronous delivery + LIVE `ads_worker`; the Business OS wallet/ledger
surface behind it is flag-dark.
DEPENDENCIES: ads_worker, ledger schema.

SYSTEM: Crypto subsystem
PURPOSE: The original CoinPilotX product — market data, alerts, auto-signals, boards.
LOCATION: `services/alert_engine.py`, `services/auto_signals_service.py`,
`services/live_market_service.py`, `services/market_observations.py`,
`services/crypto_*`, `services/coingecko_*`.
STATUS: LIVE via `alert_worker`.
DEPENDENCIES: CoinGecko, alert_worker.

SYSTEM: UNDX AI layer (services side)
PURPOSE: Mission runtime, agent policy, architecture reasoning, brain config.
LOCATION: ~25 `services/undx_*.py` + `services/undx_brain/`; notably
`services/undx_mission_runtime.py` (**untracked/new**),
`services/undx_agent_policy.py` and `services/undx_architecture.py` (**modified**).
STATUS: LIVE via `undx_worker`; HTTP surface is super-user-gated.
DEPENDENCIES: `undx_router.py`, provider keys.

SYSTEM: Notifications & delivery
PURPOSE: Push (FCM/APNs/web), email queue, SMS, in-app notification fanout.
LOCATION: `services/notification_*` (incl. `notification_health_engine.py`),
`services/push_*`, `services/email_*`, `services/brevo_*`.
STATUS: LIVE. Email async path depends on `email_worker` (deployed, but heartbeat-blind).
DEPENDENCIES: Firebase, APNs, pywebpush, Brevo.

SYSTEM: Identity, auth, sessions, moderation, admin
PURPOSE: Accounts, sessions, roles, super-user gating, reports, moderation cases,
admin tooling.
LOCATION: `services/auth_*`, `services/session_*`, `services/moderation_*`,
`services/admin_*`, `services/backend_management_registry.py`.
STATUS: LIVE.
DEPENDENCIES: db, `services/db.py`.

SYSTEM: Platform infrastructure
PURPOSE: DB access, caching, i18n/translation, storage, health, observability.
LOCATION: `services/db.py`, `services/cache_engine.py`,
`services/translation_*`, `services/media_storage.py`, `services/health_*`.
STATUS: LIVE. `cache_engine.py` does `try: import redis / except: redis = None`;
`redis_client()` returns `None` absent `REDIS_URL`, and there is an in-memory TTL
fallback. Because **`redis` is not in `requirements.txt`**, the import always fails in
prod — Redis is effectively never used.
DEPENDENCIES: SQLAlchemy, boto3, Google Cloud Translation.

---

## LAYER E — DATA LAYER

SYSTEM: `services/db.py` — the single DB accessor
PURPOSE: Provides one connection API that behaves identically over SQLite (local) and
PostgreSQL (prod), by translating SQL dialect at runtime.
LOCATION: `services/db.py` (1,018 lines).
STATUS: LIVE and load-bearing for the whole platform.
Key mechanics:
- `LOCAL_SQLITE_FILE = "coinpilotx.db"`; `_normalize_engine_url()` rewrites
  `postgres://` → `postgresql+psycopg2://`.
- Postgres engine: `pool_pre_ping=True`, `pool_recycle=300`,
  `pool_size=DB_POOL_SIZE(5)`, `max_overflow=DB_MAX_OVERFLOW(10)`,
  `pool_timeout=DB_POOL_TIMEOUT_SECONDS(3)`,
  `connect_args={"connect_timeout": DB_CONNECT_TIMEOUT_SECONDS(3)}`.
- Compatibility shims: `CompatRow(Mapping)` line 113, `CompatCursor` 640,
  `CompatConnection` 744.
- Dialect translation: `_translate_create_table`
  (`INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL PRIMARY KEY`),
  `_translate_alter_table` (injects `IF NOT EXISTS` into `ADD COLUMN`),
  `_translate_sql` (`datetime('now')` → `CURRENT_TIMESTAMP`,
  `INSERT OR IGNORE` → `INSERT`, `?` → `%s`, `%` → `%%` escaping).
- `_savepoint_op()` maintains the savepoint stack **by parsing SQL strings**, because no
  driver exposes it. This is the most fragile part of the file: an unrecognized savepoint
  statement corrupts transaction nesting silently.
- `connect()` (line 817) on SQLite sets `PRAGMA busy_timeout`
  (`SQLITE_BUSY_TIMEOUT_MS` 10000, clamped 1000–60000), `journal_mode=WAL`,
  `synchronous=NORMAL`.
- Helpers: `get_table_columns()` 860 (queries `information_schema.columns` scoped to
  `current_schema()`), `session_scope()` 891, `ping()` 915, `health_check()` 961,
  `log_startup_diagnostics()` 1001.
DEPENDENCIES: SQLAlchemy 2.x, psycopg2-binary, `DATABASE_URL`.

SYSTEM: `AUTO_PK_TABLES`
PURPOSE: Declares each table's auto-increment primary-key column so the compat layer can
emulate SQLite `lastrowid` on Postgres (via `RETURNING`).
LOCATION: `services/db.py`.
STATUS: LIVE. **354 entries** (CLAUDE.md says ~170 — **CORRECTION**). `"users"` maps to
`"user_id"`; the overwhelming majority map to `"id"`.
FAILURE MODE: a new table absent from this dict silently loses `lastrowid` on Postgres
while working fine on local SQLite — a classic works-on-my-machine trap.
DEPENDENCIES: kept in sync by hand with `bot.init_db()`.

SYSTEM: schema creation — `bot.init_db()` / `_init_db_impl()`
PURPOSE: Imperative, idempotent schema creation at boot.
LOCATION: real definition `bot.py:104466` → `_init_db_impl()` at `bot.py:104517`;
**547** `CREATE TABLE IF NOT EXISTS` occurrences. Dead shadowed copy at `bot.py:805`.
STATUS: LIVE. Import-time execution is suppressible with
`COINPILOTX_INIT_DB_ON_IMPORT=0` (used by `email_worker`).
DEPENDENCIES: `services/db.py`.

SYSTEM: `migrations/` directory — **NOT EXECUTED**
PURPOSE: Nominally holds SQL migrations.
LOCATION: 9 root `.sql` files — `pulse_id_identity`,
`pulsesoc_notification_delivery_phase2`, `pulse_ai_messenger`,
`pulsesoc_growth_engine`, `pulsesoc_lightspeed_indexes`,
`pulsesoc_notifications_foundation`, `pulsesoc_intelligence_engine`,
`pulsesoc_reels_load_speed_indexes`, `pulsesoc_communications_engine` — plus
`migrations/business_os/` `0001`–`0013`, each with a matching `.down.sql`.
STATUS: **INERT.** Nothing at runtime reads this directory. The only references anywhere
are `scripts/database_integrity_audit.py` and
`scripts/generate_undx_source_training_yaml.py`. There is no Alembic, no version table,
no runner. These files are documentation of intent, not applied schema.
**Consequence:** an index defined only in `pulsesoc_lightspeed_indexes.sql` does not exist
in production unless it was also hand-added to `init_db()`.
DEPENDENCIES: none.

SYSTEM: `services/business_os/schema_bootstrap.py` — the real second schema mechanism
PURPOSE: Lazily creates Business OS tables on first matching request.
LOCATION: `services/business_os/schema_bootstrap.py`; `ensure_all_once()` at line 117.
STATUS: LIVE but gated. Mechanics:
- `_ENSURES` tuple covers ≈18 subsystems: ledger, webhook_inbox, business, profile,
  advertising, ad_guardrails, commerce, reputation, confirmations, messages,
  commerce_links, entitlements, insights, attribution, performance, …
- Process-once latch; **only fires if at least one `BUSINESS_OS_*` env flag is truthy**,
  where `_TRUTHY = ("1","true","on","yes","enabled","canonical","shadow")`.
- Per-subsystem isolation; never fatal — failures log
  `BUSINESS_OS_SCHEMA_BOOTSTRAP_FAILED` and continue.
The module docstring records the production incident that motivated it (2026-08-07:
`relation "ledger_balances" does not exist` behind
`/api/business-os/advertising/wallet`).
DEPENDENCIES: `BUSINESS_OS_*` flags (all blank in `.env.example:433-469`).

---

## LAYER F — mobile-native ARCHITECTURE

SYSTEM: mobile-native app shell
PURPOSE: The production React Native client.
LOCATION: `mobile-native/`. Expo `~54.0.36`, React Native `^0.81.5`, React `19.1.0`,
TypeScript 5.9, React Navigation 6, Zustand.
STATUS: ACTIVE. Bundle `com.pulsesoc.app` (dev `com.pulsesoc.nativeapp.dev`).
DEPENDENCIES: `PULSE_API_BASE_URL` → `https://pulsesoc.com`.

SYSTEM: `src/` domain layout (file counts)
LOCATION/STATUS: api 171 · screens 190 (183 `.tsx`, 108 top-level) · components 158 ·
core 48 · navigation 41 · theme 31 · live 30 · discovery 26 · spatial 24 · calls 19 ·
i18n 18 · media 16 · settings 12 · social 12 · session 10 · payments 8 · create 8 ·
launch 7 · undx 6 · advertising 5 · profile 5 · community 4 · marketplace 4 ·
**live-audio 4** · sharing 4 · video 3 · feed 2 · reels 2 · auth 2 · money 2 · utils 2 ·
native 1 · pulseCommand 1 · data 1.
NOTE: `src/live-audio/` is a Live-owned copy of the audio control flow — a **second
sanctioned owner** of audio logic alongside `src/calls/`. This duplication is deliberate
and is exactly what the protection manifest's import-boundary rules police.

SYSTEM: `pulseApi()` — the shared HTTP wrapper
PURPOSE: One choke point for auth, refresh, timeouts, telemetry and error shape.
LOCATION: `mobile-native/src/api/pulseApi.ts`.
STATUS: LIVE. Mechanics:
- `perfRouteLabel()` collapses ids to `:id` so telemetry cardinality stays bounded.
- Native UA `PulseSocNativeApp/${APP_VERSION} (${platform}; Expo)` plus
  `X-PulseSoc-Platform` header.
- `PulseApiError` carries `status` / `code` / `details`.
- `PULSE_API_READ_TIMEOUT_MS = 15_000`, `PULSE_API_REFRESH_TIMEOUT_MS = 12_000`.
- In-flight GET coalescing via `inFlightReads` (dedupes concurrent identical reads).
- Sends `Authorization: Bearer ${envelope.accessToken}` only when
  `accessTokenExpiresAt > Date.now() + 5000`; also attaches `Cookie` on non-web.
- Refresh: `POST /api/mobile/auth/refresh` with body
  `{refresh_token, source: "native_automatic_refresh"}`, single-flight via
  `refreshPromise`, returning
  `RefreshResult = "refreshed" | "invalid" | "temporary" | "unavailable"`.
  A 401 triggers exactly one retry.
- `registerSessionInvalidationHandler()` lets the session layer force logout.
DEPENDENCIES: `src/api/config.ts`, `src/session/sessionStore.ts`.

SYSTEM: session & credential storage
PURPOSE: Persist the auth envelope securely.
LOCATION: `mobile-native/src/session/sessionStore.ts`.
STATUS: LIVE. expo-secure-store with
`AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY`; AsyncStorage on web. `NativeSessionEnvelope`
holds the token pair; biometric credentials live in a **separate keychain slot**;
`clearActiveSessionKeepBiometric()` supports logout-without-losing-biometric-enrolment.
DEPENDENCIES: expo-secure-store, expo-local-authentication.

SYSTEM: client feature flags
PURPOSE: Compile/runtime gating of commercially sensitive surfaces.
LOCATION: `mobile-native/src/api/config.ts`.
STATUS: Two flags are **OFF by default** and they define most of the app's dead ends:
- `DIGITAL_COMMERCE_ENABLED` — default **false** for Apple guideline 3.1.1. Hides Premium
  checkout and billing, marketplace checkout, and payout onboarding.
- `NATIVE_CALLKIT_ENABLED` — default **false**; requires `react-native-callkeep` plus a
  VoIP push certificate.
Three QA fixture flags exist but are ANDed with a loopback base-URL regex, so they cannot
activate against production.
DEPENDENCIES: build-time env.

SYSTEM: native modules (two, not one)
PURPOSE: Platform capabilities RN cannot express.
LOCATION: `mobile-native/modules/pulse-now-playing/` (iOS lock-screen / now-playing
controls, Swift) and `mobile-native/modules/pulse-video-mixer/`. Both are linked as
`file:` dependencies in `package.json`.
STATUS: LIVE. **CORRECTION:** `CLAUDE.md` mentions only `pulse-now-playing`.
DEPENDENCIES: Expo modules API.

SYSTEM: native patches
PURPOSE: Fix upstream build/runtime defects.
LOCATION: `mobile-native/patches/` contains exactly **one** patch:
`react-native+0.81.5.patch` — a Hermes build fix adding `#include <atomic>` and
`#include <thread>` to `HermesExecutorFactory.cpp`.
STATUS: LIVE. **CORRECTION:** the LiveKit WebRTC `AVAudioSession` patch described in
`CLAUDE.md` **no longer exists** — LiveKit has been removed from the app entirely.
Applied by `"postinstall": "bash scripts/apply-native-patches.sh"` — **not**
`patch-package`, despite `patch-package` still being a devDependency.
DEPENDENCIES: postinstall hook.

SYSTEM: RTC provider — Agora
PURPOSE: Voice, video and live audio/video transport.
LOCATION: `"react-native-agora": "4.6.2"` in `mobile-native/package.json`;
`src/calls/`, `src/live/`, `src/live-audio/`.
STATUS: LIVE and sole provider. **There is no LiveKit dependency in the mobile app.**
DEPENDENCIES: server token mint (Layer G).

SYSTEM: EAS build profiles
PURPOSE: Build matrix.
LOCATION: `mobile-native/eas.json`.
STATUS: `development`, `development-simulator`, `preview`, `production`.
DEPENDENCIES: Expo EAS.

SYSTEM: `npm run verify` — the local CI gate
PURPOSE: Prevent regressions before push.
LOCATION: `mobile-native/package.json`:
`"verify": "npm run typecheck && npm run i18n:validate && npm test"`.
STATUS: LIVE. i18n validation is a hard gate — hardcoded user-facing strings fail.
A separate `"test:realtime-audio-critical"` script names **11** test paths explicitly and
is what the audio CI job invokes.
DEPENDENCIES: jest, tsc, i18n validator.

---

## LAYER G — THIRD-PARTY INTEGRATIONS

Status vocabulary: **LIVE** = wired end-to-end and reachable in prod;
**CONFIGURED-BUT-UNUSED** = code and env keys exist but no active call path;
**STUBBED** = placeholder or legacy remnant.

SYSTEM: Payment routing authority (read this before any payment integration)
PURPOSE: A single server-side decision point that chooses the payment provider for every
transaction, so the client can never pick.
LOCATION: `services/pulse_payment_router.py`.
STATUS: LIVE. Providers: `PROVIDER_APPLE_IAP`, `PROVIDER_STRIPE`,
`PROVIDER_STRIPE_CONNECT`, `PROVIDER_INTERNAL_LEDGER`. Item-type partition:
- `DIGITAL_ITEM_TYPES = {"ad_credits", "premium_subscription", "business_subscription"}`
  → Apple IAP on iOS (guideline 3.1.1)
- `WALLET_SPEND_ITEM_TYPES = {"post_boost", "marketplace_ad"}` → internal ledger
- `PHYSICAL_ITEM_TYPES = {"marketplace_physical", "real_world_service"}` → Stripe
- `PAYOUT_ITEM_TYPES = {"creator_payout", "seller_payout"}` → Stripe Connect
- `PROMO_ITEM_TYPES = {"promo_credit_grant"}`
Anything not enumerated is classified `ambiguous` and **REFUSED**. This is a
fail-closed design: adding a new product without registering its item type produces a
hard refusal, not a mischarge. Rationale is App Review 3.1.1 / 3.1.3(e) / 3.1.5(b).
DEPENDENCIES: Stripe, Apple, ledger.

SYSTEM: Stripe
PURPOSE: Card payments for physical goods/services; Premium web checkout; billing portal.
LOCATION: `services/stripe_*`, `bot.py:11733` `/api/premium/checkout`,
`bot.py:11762` `/api/premium/billing-portal`, `bot.py:11814` `/api/premium/status`,
`bot.py:21762` `/api/premium/status-center`; `stripe==15.1.0`;
mobile `@stripe/stripe-react-native 0.61.0`.
STATUS: **LIVE on web / GATED on mobile** — mobile checkout entry points are hidden
because `DIGITAL_COMMERCE_ENABLED` defaults false.
GATING ENV: `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`,
`STRIPE_PRICE_*`.
DEPENDENCIES: payment router.

SYSTEM: Stripe Connect
PURPOSE: Seller and creator payouts.
LOCATION: `services/marketplace_payout_scheduler` (imported `bot.py:100377`),
`services/business_os/` payout modules.
STATUS: LIVE server-side; **mobile payout onboarding is hidden** by
`DIGITAL_COMMERCE_ENABLED`.
GATING ENV: `STRIPE_CONNECT_*`.
DEPENDENCIES: Stripe.

SYSTEM: Apple StoreKit 2 / App Store Server API
PURPOSE: In-app purchase of digital goods on iOS; JWS transaction verification.
LOCATION: `services/apple_iap_*`; mobile `expo-iap ^4.3.1`.
STATUS: LIVE (this is the mandated path for `DIGITAL_ITEM_TYPES` on iOS).
GATING ENV: `APPLE_IAP_*` / App Store Connect issuer id, key id, private key, bundle id.
DEPENDENCIES: payment router.

SYSTEM: Agora RTC
PURPOSE: **The** realtime voice/video/live provider — calls, live audio, live video,
cloud recording, media push (RTMP out).
LOCATION: `services/call_engine.py` (`generate_agora_live_token(...)` called at
`bot.py:49360` from `api_pulse_live_agora_token(live_id)` at `bot.py:49321`),
`services/agora_cloud_recording_service.py`, `services/agora_media_push_service.py`;
`agora-token-builder==1.0.0`; mobile `react-native-agora 4.6.2`.
STATUS: LIVE. Token minting is server-side only.
GATING ENV: `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`, plus cloud-recording customer
key/secret.
DEPENDENCIES: media_worker for recording finalization (**not deployed**).

SYSTEM: LiveKit — **RETIRED**
PURPOSE: former RTC provider.
LOCATION: residual only in `bot.py` as legacy state strings and error codes
(lines 46013, 46670–46683, 47489–47500, 48086, 48640–48646). Line 47550 is the
tell: the admin template renders `<label>Agora channel<code>{livekit_room}</code></label>`
— an Agora value stored in a column still named `livekit_room`.
STATUS: **STUBBED / LEGACY NAMING.** No LiveKit SDK in `requirements.txt` or
`mobile-native/package.json`; the LiveKit audio-session patch is gone.
**CORRECTION:** `CLAUDE.md` describes LiveKit as a live integration. It is not.
DEPENDENCIES: none.

SYSTEM: Mux
PURPOSE: Live stream ingest/playback and VOD.
LOCATION: `services/mux_live_service.py`; Mux endpoints inside
`pulse_communications_v2/routes.py` (`live/mux/*`).
STATUS: LIVE for stream creation/playback. Replay finalization path depends on
media_worker (**not deployed**).
GATING ENV: `MUX_TOKEN_ID`, `MUX_TOKEN_SECRET`, `MUX_WEBHOOK_SECRET`.
DEPENDENCIES: media_worker, R2.

SYSTEM: Cloudflare R2 (via boto3)
PURPOSE: Object storage for all user media.
LOCATION: `services/media_storage.py`; `boto3>=1.35,<2`.
STATUS: LIVE.
GATING ENV: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
`R2_BUCKET`, `R2_PUBLIC_BASE_URL`.
DEPENDENCIES: none.

SYSTEM: Firebase / FCM + APNs + Web Push
PURPOSE: Push notification delivery across Android, iOS and browsers.
LOCATION: `services/push_*`, `services/notification_*`;
`firebase-admin>=6.5,<7`, `pywebpush==2.3.0`; mobile `expo-notifications`.
STATUS: LIVE.
GATING ENV: `FIREBASE_*` / service-account JSON, `APNS_*`,
`VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY`.
CAVEAT: VoIP push (CallKit) is **not** active — `NATIVE_CALLKIT_ENABLED` is false and
`react-native-callkeep` is not installed, so incoming calls cannot wake a killed iOS app.
DEPENDENCIES: notification engine.

SYSTEM: Brevo
PURPOSE: Transactional email and SMS.
LOCATION: `services/brevo_*`, `services/email_*`, drained by `email_worker`.
STATUS: LIVE.
GATING ENV: `BREVO_API_KEY`, `BREVO_SENDER_*`.
DEPENDENCIES: email_worker.

SYSTEM: Telegram Bot
PURPOSE: The original CoinPilotX bot interface.
LOCATION: `bot.main()`, `telegram_worker.py`, `python-telegram-bot==22.7`.
STATUS: **CONFIGURED-BUT-UNUSED in prod** — the polling worker is not in the Procfile.
Only a webhook against `web` could serve it.
GATING ENV: `TELEGRAM_BOT_TOKEN`.
DEPENDENCIES: none.

SYSTEM: Google Cloud Translation
PURPOSE: Server-side translation for i18n content.
LOCATION: `services/translation_*`.
STATUS: LIVE when keyed; degrades to passthrough otherwise. `UNVERIFIED` whether any
mobile surface currently calls it.
GATING ENV: `GOOGLE_TRANSLATE_API_KEY` / GCP credentials.
DEPENDENCIES: none.

SYSTEM: CoinGecko
PURPOSE: Crypto market data for the alert/board subsystem.
LOCATION: `services/live_market_service.py` (`get_crypto_market`),
`services/coingecko_*`.
STATUS: LIVE via alert_worker every 45s.
GATING ENV: `COINGECKO_API_KEY` (optional; free tier works unkeyed).
DEPENDENCIES: alert_worker.

SYSTEM: Redis
PURPOSE: Intended shared cache / rate-limit store.
LOCATION: `services/cache_engine.py`.
STATUS: **STUBBED IN PRACTICE.** `try: import redis / except ImportError: redis = None`;
`redis_client()` returns `None` without `REDIS_URL`. Critically, **`redis` is not listed
in `requirements.txt`**, so the import fails unconditionally on Railway and every call
falls through to the process-local in-memory TTL cache.
CONSEQUENCE: with `--workers 2`, any cache is per-worker and inconsistent; anything
relying on Redis for cross-process coordination (locks, rate limits, fan-out) is not
actually coordinating.
GATING ENV: `REDIS_URL` (present in `.env.example`, functionally inert).
DEPENDENCIES: none.

SYSTEM: AI providers (via UNDX router)
PURPOSE: LLM inference for UNDX, smart replies, summaries, Space AI posts.
LOCATION: `undx_router.py` (502 lines) selects among OpenAI / Claude / Gemini /
DeepSeek / Groq entirely server-side so keys never reach the browser; OpenAI is the final
fallback. Provider key presence is logged (as booleans) by `undx_worker` at start.
STATUS: LIVE.
GATING ENV: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`,
`DEEPSEEK_API_KEY`, `GROQ_API_KEY`.
DEPENDENCIES: undx_worker, `services/undx_brain/config.py`.

SYSTEM: Business OS feature flags — the largest dark surface
PURPOSE: Gate the entire commerce/ledger/advertising back-office.
LOCATION: `.env.example:433-469` — **every `BUSINESS_OS_*` flag is blank**.
STATUS: **DARK BY DEFAULT.** With all flags blank, `schema_bootstrap.ensure_all_once()`
no-ops, the tables are never created, and the associated surface — **203**
`/api/business-os` routes + **37** gateway routes + **66** `/admin/business-os` routes —
is non-functional. Some routes still register (the gateway swallows schema errors), so
the observed symptom is a 500 on missing relations rather than a clean 404.
DEPENDENCIES: `services/business_os/schema_bootstrap.py`.

`.env.example` documents ~180 keys in total.

---

## LAYER H — PROTECTION & CI

SYSTEM: Realtime-audio change gate (the hard lock)
PURPOSE: Prevent any change from silently stealing the audio session from a live call —
the failure mode where the build is green, tests pass, and production goes silent.
LOCATION: manifest `config/realtime-audio-protected-paths.json`; gate
`scripts/realtime_audio_change_gate.py`; workflow
`.github/workflows/realtime-audio.yml`; policy `docs/realtime_audio_change_policy.md`.
STATUS: LIVE and unusually rigorous.
Manifest specifics (all verified):
- Baseline commit `ce03e160eaf4649a8e02bc3b609a3182ca9d3859`, tag
  `realtime-audio-stable-v1`, verified 2026-08-02.
- **15** protected path categories.
- A declaration block with **8 required sections** plus the PR label
  `audio-critical-change`.
- `backend_diff_patterns = ["pulse_rtc_", "pulse_live_audio_v2_", "AGORA_",
  "LIVESTREAM_AUDIO_V2_", "can_publish", "canPublish", "audioV2Enabled"]`.
  **This is the key subtlety: `bot.py` is protected by diff *content*, not by path.** A
  `bot.py` change counts as audio-critical only if a changed line matches one of these
  seven patterns.
- **6 `forbidden_apis` rules.** The `expo_av_global_audio_mode` rule is
  `"frozen_at_baseline": true` with `"max_allowed_paths": 6` and an allowlist of exactly:
  `core/pulseRadio.ts`, `core/reelsAudioSession.ts`, `core/voiceMessagePlayback.ts`,
  `calls/callSignalMedia.ts`, `screens/MusicScreen.tsx`, `screens/ChatScreen.tsx`.
  A seventh `Audio.setAudioModeAsync` call site fails CI.
- `import_boundary`: 9 protected modules, 13 permitted importers.
- `required_lease_discipline.must_not_contain: ["audioOwnerIdRef"]`, with the note that
  the retired LiveKit adapters held JavaScript audio leases and that **Agora session
  ownership is enforced by its native call/live implementations** — so reintroducing a JS
  lease is now itself the bug.
- `live_startup_trace_contract`: 30 required events with 12 fields.
- `dependency_watch`: 7 files; `must_be_exactly_pinned: ["react-native-agora", "expo-av"]`
  (hence `4.6.2` and `~54`-era exact pins, no carets).
- `unrelated_mission_policy`: 9 named counter-examples plus a 5-condition exception.
Local invocation:
`python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`
(exit 0 pass / 1 fail / 2 error; supports `--changed-files-from` and `--json`;
`TEMPLATE_MARKER = "TEMPLATE-NOT-YET-FILLED"` rejects an unfilled declaration).
DEPENDENCIES: git history, GitHub PR labels.

SYSTEM: `.github/workflows/realtime-audio.yml`
PURPOSE: Enforce the above in CI.
LOCATION: 7 jobs.
STATUS: LIVE.
- `detect` — runs the gate plus an indirect-path regex.
- `architecture` — **runs on every PR, always.**
- `critical` — runs when protected OR indirect OR non-PR.
- `backend` — **unconditional**; runs `scripts/protection/run_protection_suite.py`.
- `native-build` — macOS, only when protected paths change; uses PlistBuddy to assert
  `NSMicrophoneUsageDescription` exists and `UIBackgroundModes` contains `audio`.
- `declaration` — **the hard gate; this is the job to put in branch protection.**
- `label` — advisory only.
The workflow header documents the historical *"Ran 0 tests … OK"* bug, where the LiveKit
publish-grant job passed while verifying nothing. That incident is why the suite runner
now fails on zero-assertion success.
DEPENDENCIES: the gate script, the protection suite.

SYSTEM: Protection suite runner
PURPOSE: Run all protection suites and refuse to accept vacuous passes.
LOCATION: `scripts/protection/run_protection_suite.py`.
STATUS: LIVE. Discovers `tests/protection/test_*.py`, runs each as a subprocess, parses
`PROTECTION_TESTS_RUN=(\d+)` or `^Ran (\d+) tests?`, and **fails any suite that exits 0
having executed zero checks** — the direct fix for the "Ran 0 tests" incident.
DEPENDENCIES: `tests/protection/`.

SYSTEM: `tests/protection/` — 22 suites
PURPOSE: Contract tests for the subsystems that cannot be allowed to regress.
LOCATION/STATUS: LIVE. Suites: `admin_action_accountability`, `agora_cloud_recording`,
`agora_direct_live_contract`, `agora_mux_bridge`, `agora_replay_mux_contract`,
`agora_rtc_provider_contract`, `agora_token_generation`,
`backend_registry_verification`, `backup_and_secret_integrity`,
`core_platform_contract`, `environment_contract`, `ios_build_version_contract`,
`live_social_distribution`, `media_playback_contract`,
`operations_metric_truthfulness`, `protection_suite_integrity`,
`realtime_audio_architecture`, `schema_declaration_integrity`, `undx_kernel_guard`,
`undx_router_credentials` — plus `_runner.py`, `reels_preload_harness.js`,
`reels_preload_runner.py`.
**CORRECTION:** `CLAUDE.md` says the suite covers 21 subsystems; there are 22 suite
files. Five of them are Agora-specific, confirming the provider migration.
LIMITATION (stated in policy): static checks do not replace device QA for livestream,
push, checkout, or uploads.
DEPENDENCIES: pytest.

SYSTEM: UNDX execution kernel safety
PURPOSE: Let an AI propose repo diffs without letting it write unsupervised.
LOCATION: `undx_execution_kernel.py` (845 lines); routes
`/api/undx/kernel/{apply,git,propose,scan,validate}`.
STATUS: LIVE. Writes only after the literal approval phrase **`APPROVE UNDX WRITE`**;
blocks `.env`, `.git`, virtualenvs, secrets and sqlite paths; appends to
`undx_execution_log.jsonl`. Guarded by `tests/protection/test_undx_kernel_guard.py`.
DEPENDENCIES: git.

---

## LAYER I — REPO HYGIENE

SYSTEM: Live code directories
LOCATION/STATUS: `bot.py`, `services/`, `pulse_communications_v2/`, `seo/`
(**live** — imported at `bot.py:347-348`), `templates/`, `static/`, `models/`,
`mobile-native/`, `scripts/`, `tests/`, `config/`, `docs/` (~40 files plus subdirs
`business_os`, `mobile`, `pages`, `performance`, `progress`, `protection`), the root
`*_worker.py` files, and the root `undx_*.py` modules.

SYSTEM: Debris and stale artifacts
LOCATION/STATUS: all verified present.
- **974 `.fuse_hidden*` files at repo root** — orphaned FUSE handles from deleted-while-open
  files. Pure noise; they pollute every `ls`, `git status` and recursive grep.
- **22 root `*_REPORT.md`** mission writeups.
- `reports/` — **805 files**.
- `backups/` — 3 files (`backup_log.jsonl` + two sqlite `.sql.gz`).
- `.gate_removed/` — quarantined construction-gate orphans
  (`ENGINEER_ACCESS_SECURITY_EVIDENCE.md`, `engineerAccess.test.ts`, mirrored
  `mobile-native/scripts|services|tests` dirs, `remove_construction_gate_orphans.sh`).
- `.undx/` — `desktop_backups/`, `desktop_connector_log.jsonl`,
  `desktop_workspaces.json`, stdout logs. Runtime state, not source.
- `outputs/` — `jest-profile.log`, `mutate20-24.py`. Scratch.
- `.claude/worktrees/eloquent-herschel-64cb81/` — **a git worktree containing a divergent
  copy of `services/` and `tests/`.** This is the most dangerous piece of debris: a
  recursive grep for a symbol returns hits from a parallel universe, and it is easy to
  edit the wrong copy.

SYSTEM: Ambiguous directories (live but easily mistaken for debris)
LOCATION/STATUS:
- `backend/` — contains only `backend/undx/config/*.yaml`, six UNDX training corpora. Not
  a backend.
- `storage/messenger_uploads` — runtime upload spill.
- `data/pulse_ai` — runtime AI data.
- `migrations/` — real SQL, never executed (Layer E).
- `mobile/` — LEGACY Expo 51 app; out of scope, do not develop.

SYSTEM: Working-tree state at recon time
LOCATION/STATUS: branch `codex/emergency-live-audio-recovery`, dirty.
Modified: `bot.py`, `services/pulse_ai_service.py`, `services/undx_agent_policy.py`,
`services/undx_architecture.py`, `services/undx_brain/config.py`, `undx_worker.py`.
Untracked: `services/undx_mission_runtime.py`,
`tests/undx_agent/test_safety_precedence.py`,
`scripts/undx_railway_variable_audit.py`.
Note that `undx_worker.py` (deployed) imports `services/undx_mission_runtime.py`
(untracked) — **if that file is not committed, the deployed UNDX worker crashes on
import.**

---

## SUMMARY OF CORRECTIONS TO `CLAUDE.md`

1. `bot.py` is 117,902 lines with 1,713 route decorators (not 111k / 1,538).
2. Double-Flask lines are **429 / 1181** (not 384 / 1130), and **nothing meaningful is
   lost** — only `secret_key` and `config.update`, both re-applied.
3. A second, undocumented shadowing exists: dead `init_db()` at `bot.py:805`.
4. **LiveKit is retired.** Agora is the sole RTC provider. The LiveKit WebRTC patch no
   longer exists in `mobile-native/patches/`.
5. **APScheduler has zero first-party usage.** All scheduling is worker `while` loops.
6. `AUTO_PK_TABLES` has **354** entries, not ~170.
7. `services/` has **285** top-level modules, not 239.
8. There are **two** local native modules (`pulse-now-playing`, `pulse-video-mixer`).
9. `tests/protection/` holds **22** suites.
10. The Procfile process is `ads_worker`; `pulse_ads_worker.py` is the file.
11. Postinstall runs `scripts/apply-native-patches.sh`, not `patch-package`.
12. Additional undocumented facts: the unregistered 18-endpoint Sentinel admin API, the
    Redis package absence, and the fully dark Business OS flag surface.
