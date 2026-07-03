# LogiNexus Intelligence Engine Foundation

## What was built
- Added a central PulseSoc Galaxy Intelligence Engine foundation.
- Added default Intelligence Streams:
  - PulseSoc Discoveries
  - Crypto Pulse
  - Market Pulse
  - World Pulse
  - Security Pulse
  - Technology Pulse
  - PulseSoc Pulse
  - Creator Pulse
  - Music Pulse
- Added confidence scoring for accuracy, freshness, importance, global/regional impact, duplicate confidence, and spam probability.
- Added event dedupe and evidence/source merging.
- Added forecast records with confidence labels.
- Added user stream subscriptions, frequency, digest mode, confidence thresholds, push/email/SMS switches, and feedback.
- Added admin source readiness, collector run history, event review, and safe internal collector trigger.

## Files changed
- `services/pulsesoc_intelligence_engine.py`
- `migrations/pulsesoc_intelligence_engine.sql`
- `pulse_communications_v2/routes.py`
- `templates/pulsesoc_intelligence_center.html`
- `templates/admin_galaxy_intelligence_center.html`
- `static/css/pulsesoc_intelligence_center.css`
- `static/js/pulsesoc_intelligence_center.js`
- `scripts/pulsesoc_intelligence_worker.py`
- `scripts/pulsesoc_intelligence_engine_audit.py`
- `services/pulsesoc_notification_system.py`
- `services/pulse_ai_knowledge.py`
- `services/db.py`

## Routes added
- `GET /pulse/intelligence`
- `GET /pulse/settings/intelligence`
- `GET /api/pulse/intelligence/state`
- `PATCH /api/pulse/intelligence/streams/<stream_key>`
- `POST /api/pulse/intelligence/feedback`
- `GET /admin/intelligence`
- `GET /api/admin/intelligence/health`
- `GET /api/admin/intelligence/state`
- `POST /api/admin/intelligence/collect`

## Database tables
- `intelligence_streams`
- `user_intelligence_streams`
- `intelligence_sources`
- `intelligence_events`
- `intelligence_forecasts`
- `intelligence_feedback`
- `intelligence_collector_runs`
- `intelligence_digest_jobs`
- `intelligence_delivery_log`

## Notification integration
Accepted Intelligence Pulses use the existing `pulsesoc_notification_system.intake_event` path with:
- `event_type=intelligence_pulse`
- `category=intelligence`
- dedupe keys
- in-app creation
- push/email/SMS delivery jobs only when stream settings and notification preferences allow them

## Source strategy
The foundation registers trusted source definitions and readiness status without exposing secrets.
External source credentials are checked by presence only. The user-facing Center never fetches external providers synchronously.

## Performance safeguards
- User pages only read cached database state.
- Source collection is worker/admin-triggered.
- Delivery uses the existing queue-ready notification path.
- CSS animations are lightweight and respect reduced motion.
- No synchronous external HTTP fetches are performed by the Intelligence service.

## Privacy
- Private messages, calls, media, payment data, passwords, tokens, and secrets are not used by collectors.
- Learning comes from stream settings, user engagement, feedback, source reliability, and admin-approved knowledge.
- Pulse AI knowledge was updated to explain Intelligence Streams without claiming access to private conversations.

## Phase boundary
This is the foundation. Real external collectors for Reuters/AP/NASA/CISA/CoinMarketCap/etc. are represented as source definitions and readiness checks. Production collector adapters can be added one source at a time using the worker entry point.

## Known limitations
- External provider fetch adapters are not activated in this phase.
- Forecasts are deterministic confidence-scored records, not model-generated predictions.
- Digest compilation jobs are modeled but not yet scheduled by a dedicated queue worker.

## QA performed
- `venv/bin/python -m py_compile services/pulsesoc_intelligence_engine.py pulse_communications_v2/routes.py services/pulsesoc_notification_system.py services/pulse_ai_knowledge.py services/db.py scripts/pulsesoc_intelligence_worker.py scripts/pulsesoc_intelligence_engine_audit.py`
- `node --check static/js/pulsesoc_intelligence_center.js`
- `venv/bin/python scripts/pulsesoc_intelligence_engine_audit.py`
- `venv/bin/python scripts/pulsesoc_intelligence_worker.py --stream pulsesoc_discoveries`
- `curl -fsS http://127.0.0.1:5069/health`
- Unauthenticated API check: `GET /api/pulse/intelligence/state` returns `401` login-required after server restart, proving the new route is registered and protected.
- Unauthenticated admin route check: `GET /admin/intelligence` redirects to `/admin/login?next=/admin/intelligence`.
- In-app browser QA: `GET /pulse/intelligence?v=intelligence-foundation-qa` rendered the Galaxy Intelligence Center with 9 stream cards, 1 accepted signal, and no horizontal overflow at the active mobile-sized viewport.
- In-app browser admin QA: `/admin/intelligence` correctly redirected to admin login for the current non-admin browser session.
