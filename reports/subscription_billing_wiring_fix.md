# Subscription Billing Wiring Fix

## Scope

Fixed the subscription dashboard wiring so visible billing controls no longer act as local-only UI state.

## Changes

- Added `/billing/portal` so the previous 404 now redirects to Stripe Billing Portal when configured.
- Added `/billing/portal-session` for frontend billing handoff.
- Added subscription APIs:
  - `GET /api/subscriptions/status`
  - `POST /api/subscriptions/checklist`
  - `POST /api/subscriptions/upgrade`
  - `POST /api/subscriptions/downgrade`
  - `POST /api/subscriptions/cancel`
  - `POST /api/subscriptions/resume`
- Rewired Dashboard Economy subscription buttons to real backend endpoints.
- Persisted subscription checklist state through backend dashboard preferences.
- Added explicit unavailable responses when Stripe is not configured instead of fake success.
- Preserved iOS App Store compliance by blocking paid digital billing endpoints for native iOS requests.
- Redacted Stripe customer and subscription identifiers from subscription status responses.

## Security Notes

- Write endpoints require CSRF.
- Billing actions require authentication.
- Native iOS receives core-only subscription status and cannot start Stripe checkout or billing portal flows.
- No raw provider identifiers or secrets are returned to clients.

## QA

Ran:

```bash
venv/bin/python -m py_compile bot.py services/pulsesoc_dashboard_centers.py scripts/subscription_billing_wiring_audit.py
venv/bin/python scripts/subscription_billing_wiring_audit.py
git diff --check
```

Result:

```text
PASS: subscription billing wiring audit passed
```

## Remaining Notes

If Stripe Billing Portal is not configured or the user has no Stripe customer, the UI now disables the action or returns a clear backend error. That is intentional and safer than pretending a portal exists.
