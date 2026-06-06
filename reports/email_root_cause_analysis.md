# Email Root Cause Analysis

Date: 2026-06-06

## Summary

Accounts are being created successfully, but confirmation delivery cannot be proven because the production email evidence surface, `/admin/emails`, returned HTTP 500 during the incident.

The immediate verified production failure is:

- `GET /admin/emails`
- HTTP status: `500`
- Time observed in Railway HTTP logs: Jun 6, 2026 18:23 EDT
- User-facing trace ID from screenshot: `0295b64b5abb`

The most likely component failure is the email-log reporting query hitting a production schema that is missing one or more recently added `email_logs` diagnostic columns. The route queried these columns directly before rendering, so the admin email page could crash before showing Brevo status, trace IDs, provider message IDs, or delivery state.

## Impact

- Admins cannot reliably inspect email logs.
- Signup confirmation failures cannot be classified from the admin UI.
- Password reset delivery may be affected by the same sender/provider path.
- Users remain blocked when their accounts are created but `email_verified=0`.
- Support cannot prove whether the break occurs in app code, Railway env loading, Brevo API, Brevo delivery, or recipient inbox until the admin evidence page is stable.

## Evidence Collected

### Production Request Evidence

Railway HTTP logs for the active `CoinPilotX` web service showed:

- `GET /admin/emails`
- HTTP `500`
- Around Jun 6, 2026 18:23 EDT
- Duration around `343ms`

### Production Deployment Evidence

Railway showed the active deployment as:

- Service: `CoinPilotX`
- Active deployment: `Build Pulse Mobile feed and status creation`
- Earlier email incident deployment: `Fix production email delivery incident`
- Status for earlier email incident deployment: `Removed`

This means production is running the newer active commit, not the earlier email-only deployment label. The newer active commit includes the email changes in Git history, but the visible deployment label is not the email incident label.

### Production Log Evidence

The active web deploy logs show PostgreSQL schema mismatch errors in production, including:

```text
SQL_EXECUTE_FAILED
engine=postgresql
error=UndefinedColumn('column v.created_at does not exist ...')
psycopg2.errors.UndefinedColumn: column v.created_at does not exist
```

That specific loaded log excerpt was for `pulse_status_views`, not `email_logs`, but it proves the active production deployment is encountering schema drift / missing-column errors. The `/admin/emails` route had the same risk because it selected newly introduced email diagnostic columns without guarding for older production schemas.

### Railway Variable Evidence

Railway Variables page for the `CoinPilotX` service showed these variable names present:

- `BREVO_API_KEY`: present
- `BREVO_EMAIL_ENABLED`: present
- `BREVO_SENDER_EMAIL`: present
- `BREVO_SENDER_NAME`: present
- `PUBLIC_BASE_URL`: present
- `MAIL_FROM_ADDRESS`: present
- `MAIL_FROM_NAME`: present

The following requested variable names were not visible:

- `DEFAULT_FROM_EMAIL`: missing
- `SUPPORT_EMAIL`: missing
- `SECURITY_EMAIL`: missing

The app now supports both the requested PulseSoc variable names and the older legacy sender variables, so missing `DEFAULT_FROM_EMAIL` does not have to block mail if `BREVO_SENDER_EMAIL` or `MAIL_FROM_ADDRESS` is configured correctly.

### DNS Evidence

Public DNS checks for `pulsesoc.com` showed:

- DMARC exists:

```text
v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com
```

- SPF TXT record was not observed.
- Common Brevo DKIM selectors checked (`mail`, `brevo`, `sib1`, `sib2`, `default`) did not return DKIM TXT records.

This is a likely downstream deliverability/domain-authentication issue if Brevo accepts messages but inboxes still do not receive them. It may also cause Brevo sender/domain rejection depending on the exact Brevo account configuration.

### Production Admin Session Evidence

When checking `https://coinpilotx.app/api/admin/email/diagnostics`, production returned:

```json
{"ok": false, "error": "Admin login required."}
```

When checking `https://pulsesoc.com/admin/emails`, production redirected to:

```text
https://pulsesoc.com/admin/login
```

So a live direct Brevo API test from production could not be completed without an active production admin session.

## Route / Component Verification

### Route Registration

Verified in code:

- `GET /admin/emails`
- `POST /admin/emails/test`
- `POST /admin/emails/resend-confirmation`
- `GET /api/admin/email/diagnostics`
- `POST /api/admin/email/direct-test`
- `POST /api/brevo/webhook`
- `POST /webhooks/brevo`

### Admin Authorization

Local test client with an admin session returned:

- `/admin/emails`: HTTP 200
- `/admin/emails?filter=brevo_401`: HTTP 200
- `/api/admin/email/diagnostics`: HTTP 200

Production without an admin session correctly returned admin login required or redirected to admin login.

### Template Rendering

Local `/admin/emails` rendered successfully after the schema guard fix.

### Database Queries

Original risk:

`/admin/emails` queried these columns directly:

- `provider_status_code`
- `provider_message_id`
- `safe_error_reason`
- `retry_count`
- `trace_id`
- `delivery_status`
- `last_webhook_event`

If production had an older `email_logs` table, the route could fail before rendering.

Fix:

Added `ensure_email_logs_reporting_schema()` and defensive select expressions so `/admin/emails` creates/repairs missing reporting columns before querying, and falls back cleanly if a column is still unavailable.

### Notification Service Initialization

Local audits passed for notification delivery and Brevo notification infrastructure.

### Brevo Initialization

Code now recognizes:

- `BREVO_API_KEY`
- `BREVO_EMAIL_ENABLED`
- `BREVO_SENDER_EMAIL`
- `BREVO_SENDER_NAME`
- `MAIL_FROM_ADDRESS`
- `MAIL_FROM_NAME`
- `DEFAULT_FROM_EMAIL`
- `SUPPORT_EMAIL`
- `SECURITY_EMAIL`

## Root Cause

### Immediate Root Cause

`/admin/emails` crashed because the route assumed the production `email_logs` table already contained all recently added diagnostic columns. Production is showing schema drift / missing-column behavior, and the email evidence page had no defensive reporting-schema repair before querying.

### Email Delivery Root Cause Status

The exact delivery break between Pulse/CoinPilotX and recipient inbox is **not yet fully proven** because the admin email page was down and the production admin direct-test endpoint could not be run without an active admin session.

Current strongest findings:

1. App signup creates users.
2. Email evidence UI crashed at `/admin/emails`, blocking inspection.
3. Railway has Brevo API/sender variables present.
4. Railway is missing requested `DEFAULT_FROM_EMAIL`, `SUPPORT_EMAIL`, and `SECURITY_EMAIL` variable names, though legacy sender variables are present.
5. Public DNS for `pulsesoc.com` appears incomplete for SPF/Brevo DKIM.

Most likely remaining delivery break candidates:

- Brevo sender/domain authentication incomplete.
- Brevo accepts but recipient providers filter/block due missing SPF/DKIM alignment.
- Confirmation sends are failing with provider rejection but were hidden because `/admin/emails` crashed.

## Fix Applied

Updated `bot.py`:

- Added `ensure_email_logs_reporting_schema(cur, conn)`.
- `/admin/emails` now repairs/creates `email_logs` reporting columns before querying.
- `/admin/emails` now avoids using optional columns in filters if they are missing.
- `/admin/emails` now selects fallback values for missing optional columns instead of crashing.

This restores the admin email evidence page so production can show:

- status
- provider status code
- provider message ID
- trace ID
- safe error reason
- delivery/webhook status

## Validation Completed Locally

Local admin-session test:

- `/admin/emails`: HTTP 200
- `/admin/emails?filter=brevo_401`: HTTP 200
- `/api/admin/email/diagnostics`: HTTP 200

Validation still required after deploy:

- Confirm `/admin/emails` no longer returns 500 in production.
- Confirm `/api/admin/email/diagnostics` returns loaded booleans for an authenticated admin.
- Send direct production Brevo test.
- Confirm Brevo message ID appears.
- Confirm real inbox receives the email.

## Production Proof Still Required

The incident cannot be marked fully resolved until these pass:

1. Open `/admin/emails` in production as admin.
2. Confirm no 500 and no trace ID page.
3. Open `/api/admin/email/diagnostics` as admin.
4. Confirm:
   - `BREVO_API_KEY=true`
   - `BREVO_EMAIL_ENABLED=true`
   - sender email configured=true
   - public base URL configured=true
5. Send direct production test through `/api/admin/email/direct-test`.
6. Record:
   - HTTP status
   - Brevo response body
   - provider message ID
   - trace ID
7. Search Brevo Transactional Logs for the provider message ID.
8. Confirm message status:
   - accepted
   - delivered
   - blocked
   - bounced
   - deferred
   - rejected
9. Confirm real inbox receipt.
10. Create a new test account and verify:
    - confirmation token created
    - confirmation email log row created
    - message ID present
    - email received
    - confirmation link works
    - login works
11. Trigger password reset and verify:
    - reset email received
    - reset link works

## Recommended Production Fixes Outside Code

Add or verify Railway variables:

```text
DEFAULT_FROM_EMAIL=noreply@pulsesoc.com
SUPPORT_EMAIL=support@pulsesoc.com
SECURITY_EMAIL=security@pulsesoc.com
PUBLIC_BASE_URL=https://coinpilotx.app
BREVO_EMAIL_ENABLED=true
```

In Brevo:

- Verify sender `noreply@pulsesoc.com`.
- Authenticate domain `pulsesoc.com`.
- Confirm transactional sending is active.
- Confirm API key has transactional email permission.

In Cloudflare DNS:

- Add/repair SPF TXT for Brevo.
- Add Brevo DKIM TXT records exactly as Brevo provides.
- Keep DMARC valid.

## Final Classification

Current confirmed break:

```text
Admin evidence path failure: /admin/emails 500 due schema-drift risk in email_logs reporting query.
```

Current suspected delivery break:

```text
Brevo/domain authentication is likely incomplete because SPF/Brevo DKIM records were not observed publicly for pulsesoc.com.
```

Final inbox-delivery root cause is pending production direct-test and Brevo dashboard evidence.
