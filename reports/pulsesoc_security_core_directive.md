# PulseSoc Security Core Directive

## Implemented

- Added `services/pulse_security_core.py` as the shared server-authoritative security layer.
- Added request-boundary enforcement in `bot.py` through `pulse_security_core_guard()`.
- Added high-risk per-IP, per-user, and per-device throttling for auth, uploads, Live, messaging, ads, checkout, and PulseShell validation.
- Added emergency kill switches:
  - `PULSESOC_DISABLE_SIGNUP`
  - `PULSESOC_DISABLE_LIVE`
  - `PULSESOC_DISABLE_COHOST`
  - `PULSESOC_FREEZE_PAYMENTS`
  - `PULSESOC_THROTTLE_MESSAGING`
  - `PULSESOC_DISABLE_UPLOADS`
- Added strict JSON unknown-field rejection for mobile auth and PulseShell validation.
- Added `/api/pulseshell/validate` so native PulseShell calls must be approved by the server before execution.
- Added timestamp and replay nonce protection for PulseShell validation.
- Backed high-risk rate buckets, replay nonces, and device trust cache with the existing Redis-capable `cache_engine`, with in-memory fallback.
- Added mobile access/refresh token issuance and `mobile_security_sessions` tracking bound to a device fingerprint.
- Added refresh-token rotation and logout revocation.
- Added active mobile session revocation on password reset/change helper paths.
- Hardened Live co-host accept/deny against double-action races by requiring the request row to still be `pending` at update time.
- Added observable security headers:
  - `X-PulseSoc-Security: server-authoritative`
  - `X-PulseSoc-Client-Trust: none`
- Hardened the native WebView bridge so `PULSESHELL_NATIVE_CALL` goes through server validation before executing native capabilities.
- Added `scripts/pulsesoc_security_core_audit.py` as a static regression gate.

## Performance Posture

- The guard runs before route handlers and uses short in-memory buckets for fast decisions.
- Normal GET page rendering, feed reads, video reads, and static assets do not run strict schema checks.
- High-risk throttling is limited to mutations and known sensitive prefixes.

## Remaining Hardening

- Confirm `REDIS_URL` is configured in production so rate buckets and replay nonces are shared across replicas.
- Gradually make native clients prefer refresh-token auth over session-only refresh while preserving WebView cookie compatibility.
- Add per-endpoint strict schema definitions for more legacy mutation routes after payload contracts are audited.
- Add broader atomic DB locks for duplicate Live joins and stream publish state transitions.
- Add real-time alerting for kill-switch triggers, payment anomalies, Live spikes, and auth bursts.
