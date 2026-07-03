# PulseSoc Rock-Solid Reliability Foundation

## What Changed

- Hardened password reset request and completion paths.
- Added provider-readiness snapshots that never call optional external services from public health.
- Added lightweight health routes:
  - `/health/live`
  - `/health/ready`
  - `/admin/health/deep`
- Added a reliability audit script for password reset and health behavior.

## Routes Hardened

- `POST /forgot-password`
- `POST /api/mobile/auth/recover`
- `POST /reset-password/<token>`
- `POST /api/mobile/auth/reset-password`

## Provider Safety

Optional providers are treated as configuration-dependent and non-critical for app startup:

- Brevo email
- Brevo SMS
- Stripe
- LiveKit
- Web Push
- FCM
- APNs
- crypto market providers
- AI providers
- R2 media

Provider readiness is reported by admin diagnostics but does not make public liveness fail.

## Password Reset Safety

- Generic response for known and unknown emails.
- HMAC-hashed reset token storage for new tokens.
- Legacy raw-token lookup fallback for old links.
- Database rollback on reset failures.
- Email enqueue failure is logged and does not crash the route.
- Password-changed email failure does not undo a completed password reset.

## Health Checks

- `/health/live` only proves the app process is alive.
- `/health/ready` checks database readiness.
- `/admin/health/deep` is admin-only and includes provider config status.

## Remaining Risks

- Production root cause should still be confirmed against Railway logs if available.
- Full circuit breakers around every provider call should continue in a follow-up pass where provider clients are touched directly.
- Public health should stay lightweight; do not add optional provider checks to `/health/live`.

## Verification

Run:

```bash
venv/bin/python -m py_compile bot.py services/pulsesoc_reliability.py pulse_communications_v2/routes.py scripts/pulsesoc_reliability_audit.py
venv/bin/python scripts/pulsesoc_reliability_audit.py
curl -fsS http://127.0.0.1:5069/health
curl -fsS http://127.0.0.1:5069/health/live
curl -fsS http://127.0.0.1:5069/health/ready
```
