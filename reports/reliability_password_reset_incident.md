# PulseSoc Password Reset Reliability Incident

## Summary

The active password-reset request paths could fail inside the request lifecycle when token creation, email enqueue, or audit logging raised an exception. That made a password-reset feature failure visible as a route failure and could destabilize the web worker under repeated retries.

No production Railway logs were available in this local shell, so this report is based on code-path evidence.

## Root Cause

- `/forgot-password` called `create_password_reset(...)`, `send_password_reset_email(...)`, `log_product_event(...)`, and `log_auth_event(...)` directly.
- `/api/mobile/auth/recover` repeated the same direct flow.
- Reset completion looked up `password_reset_tokens.token` directly, so new reset tokens were stored as raw database values.
- Provider, queue, or audit-log failures were not isolated from the password-reset route.

## Fix

- Added `safe_password_reset_request(...)` as the request-safe reset boundary.
- Password reset email delivery is now queued inside a try/except path and delivery status is recorded as `queued` or `queue_failed`.
- New reset tokens are stored by HMAC hash in `password_reset_tokens.token_hash`; legacy plaintext token lookup remains as a compatibility fallback.
- Web and mobile reset request routes now always return a generic success message without exposing whether an email exists.
- Password-changed email and product-event logging after reset completion are isolated from the successful password update.

## Prevention

- Added `/health/live` for process liveness and `/health/ready` for database readiness.
- Added admin-only `/admin/health/deep` for provider configuration diagnostics without blocking public health.
- Added `scripts/pulsesoc_reliability_audit.py` to verify health endpoints, reset route survival, hashed reset-token storage, and clean invalid-token handling.
