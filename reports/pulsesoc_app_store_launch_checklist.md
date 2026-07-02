# PulseSoc App Store Launch Checklist

This checklist is for the manual release window after local launch gates pass.

## Required Green Gates

- `venv/bin/python scripts/pulsesoc_launch_readiness_audit.py`
- `venv/bin/python scripts/pulsesoc_launch_load_smoke.py`
- `venv/bin/python scripts/signup_reset_reliability_audit.py`
- `venv/bin/python scripts/push_delivery_queue_audit.py`
- `venv/bin/python scripts/pulseshell_app_review_audit.py`
- `venv/bin/python scripts/pulse_store_submission_readiness_audit.py`
- `git diff --check`

## Production Verification

- `/health` returns 200.
- `/health/database` returns 200.
- `/pulse`, `/pulse/reels`, `/pulse/messages`, `/pulse/notifications`, and `/pulse/live/studio` do not return 500.
- New signup, login, and password reset stay responsive.
- Railway shows no restart loop, DB lock storm, or traceback flood.
- Push/email queues drain or hold safely with bounded retries.

## Kill Switches

- `PULSESOC_DISABLE_SIGNUP=1`
- `PULSESOC_DISABLE_LIVE=1`
- `PULSESOC_DISABLE_COHOST=1`
- `PULSESOC_FREEZE_PAYMENTS=1`
- `PULSESOC_THROTTLE_MESSAGING=1`
- `PULSESOC_DISABLE_UPLOADS=1`
- `BREVO_EMAIL_ENABLED=0`
- `BREVO_SMS_ENABLED=0`
- `PUSH_ASYNC_DELIVERY_ENABLED=0`
- `EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED=0`
- `PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED=0`
- `PULSE_AI_ENABLED=false`
- `PULSE_CRYPTO_AI_ENABLED=false`
- `PULSE_ADS_BILLING_ENABLED=false`
- `PULSE_PREMIUM_DISABLED=true`

## Monitoring Windows

- 0-15 minutes: app availability, HTTP 5xx, restarts, signup/login/reset.
- 15-60 minutes: queues, DB locks, provider errors, Live starts.
- 1-6 hours: memory/CPU, upload volume, Stripe retries, Cloudflare/Railway limits.
- First 24 hours: crash reports, support tickets, stale cache symptoms.

## App Store Connect

Release only after the readiness report says `RELEASE`.

Manual release path:

App Store Connect -> PulseSoc -> approved version -> Release This Version -> Confirm.
