# Production Architecture

Generated: 2026-05-31

## Confirmed Components

- Public domain: `https://coinpilotx.app`.
- Web service: Flask app in `bot.py`.
- Worker services:
  - `media_worker.py`
  - `pulse_worker.py`
  - `alert_worker.py`
  - `telegram_worker.py`
  - `undx_worker.py`
- Database layer: `DATABASE_URL` when configured, local fallback `sqlite:///coinpilotx.db`.
- Media storage layer: local development storage or R2/S3 style durable storage through `MEDIA_STORAGE_PROVIDER`.
- CDN layer: canonical public base from `R2_PUBLIC_BASE_URL`, expected production CDN `https://cdn.coinpilotx.app`.
- Realtime/live layer: Pulse live routes, WebRTC signaling database table, HLS playback URL fields, live replay fields.
- AI layer: OpenAI and UNDX router providers through environment variables.

## Required Production Environment Variables

Core:
- `DATABASE_URL`
- `SECRET_KEY` or `FLASK_SECRET_KEY`
- `SESSION_SECRET`
- `PORT`

Media/R2:
- `MEDIA_STORAGE_PROVIDER`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET`
- `R2_PUBLIC_BASE_URL`
- `R2_ENDPOINT` or `R2_ENDPOINT_URL` or `R2_ACCOUNT_ID`

Workers:
- `MEDIA_WORKER_INTERVAL_SECONDS`
- `MEDIA_WORKER_BATCH_SIZE`
- `PULSE_WORKER_SLEEP_SECONDS`
- `PULSE_WORKER_BATCH_SIZE`
- `ALERT_WORKER_INTERVAL_SECONDS`

AI:
- `OPENAI_API_KEY`
- `CLAUDE_AI_API`
- `Gemini_AI_API`
- `DEEPSEEK_AI_API`
- `GROQ_AI_API`
- `UNDX_ROUTER_ENABLED`
- `UNDX_MULTI_MODEL_MODE`
- `UNDX_DEFAULT_AI_PROVIDER`

Payments/Email/Push:
- `STRIPE_SECRET_KEY`
- `STRIPE_PRICE_ID`
- `STRIPE_WEBHOOK_SECRET`
- `BREVO_API_KEY`
- `VAPID_PUBLIC_KEY`
- `VAPID_PRIVATE_KEY`

## Production Smoke Evidence

- `/` returned 200.
- Protected Pulse routes redirect to login.
- Search API blocks unauthenticated requests.
- CSP exists on public production responses.

## Architecture Risks

- Production truth cannot be fully proven from unauthenticated public checks.
- Live WebRTC client currently has confirmed STUN-only config; TURN is not confirmed.
- Worker health must be checked in Railway, not inferred from local SQLite.
- Production R2/CDN correctness must be proven with real object upload and playback.

