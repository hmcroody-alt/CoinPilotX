# Production Email Delivery Incident

Date: 2026-06-06

## Incident

Production signups are creating user accounts, but users report that confirmation emails do not reach their inboxes. Because email confirmation is enforced before login, affected users remain blocked until the confirmation message is delivered or an owner/admin manually confirms the account after support review.

## Current App-Side Evidence

The application now has explicit instrumentation for the email path:

- Signup calls `send_account_confirmation_email()` after account creation.
- Confirmation links are built with `public_url_for()` and the production base URL helper.
- Login blocks unconfirmed accounts and exposes a resend-confirmation form.
- Admin email logs show provider, status code, provider message ID, safe error reason, trace ID, retry count, and webhook delivery status.
- Brevo webhook routes update delivery status when Brevo sends events.
- Admin tools can send test, confirmation, password reset, and payment emails.

## Fix Applied In This Pass

### Railway Runtime Variable Compatibility

The code now recognizes the production variables requested for this incident:

- `PUBLIC_BASE_URL`
- `DEFAULT_FROM_EMAIL`
- `SUPPORT_EMAIL`
- `SECURITY_EMAIL`

Older variables remain supported:

- `PULSE_APP_URL`
- `APP_BASE_URL`
- `BASE_URL`
- `DOMAIN`
- `BREVO_SENDER_EMAIL`
- `MAIL_FROM_ADDRESS`
- `BREVO_SENDER_NAME`
- `MAIL_FROM_NAME`

### Sender Alignment

Sender selection now supports:

- Transactional/signup confirmation: `DEFAULT_FROM_EMAIL`, falling back to Brevo sender variables, then `noreply@pulsesoc.com`.
- Support email channel: `SUPPORT_EMAIL`, then support-specific legacy variables, then `support@pulsesoc.com`.
- Security email channel: `SECURITY_EMAIL`, then security-specific legacy variables, then `security@pulsesoc.com`.

### Safe Admin Diagnostics

Added admin-only JSON endpoints:

- `GET /api/admin/email/diagnostics`
- `POST /api/admin/email/direct-test`

The diagnostics endpoint returns loaded=true/false style configuration status only. It does not expose API keys or secrets.

The direct-test endpoint sends one Brevo transactional test email and returns/logs:

- trace ID
- provider status code
- provider message ID when Brevo returns one
- sender used
- masked recipient
- safe error reason
- safe provider response body

It also writes an `email_logs` row with the same trace ID.

## Required Production Truth Checks

These items must be completed in production before the incident can be marked resolved.

### 1. Railway Status

Verify in Railway runtime, not just settings UI:

- `BREVO_API_KEY`: loaded=true
- `BREVO_EMAIL_ENABLED=true`: loaded/enabled=true
- `DEFAULT_FROM_EMAIL=noreply@pulsesoc.com`: loaded=true
- `SUPPORT_EMAIL=support@pulsesoc.com`: loaded=true
- `SECURITY_EMAIL=security@pulsesoc.com`: loaded=true
- `PUBLIC_BASE_URL=https://coinpilotx.app`: loaded=true

Use:

- `GET /api/admin/email/diagnostics`
- `/admin/emails` Brevo Configuration panel

### 2. Brevo API Test From Production Runtime

Use:

- `POST /api/admin/email/direct-test`

Expected PASS evidence:

- HTTP 200 from Pulse endpoint
- `ok=true`
- Brevo status code 2xx
- provider message ID present
- `email_logs` row exists with the same trace ID
- real inbox receives the message

If this returns 401, root cause is likely invalid `BREVO_API_KEY` or wrong Railway variable.

If this returns 403, root cause is likely unverified sender/domain or Brevo account restriction.

If this returns 2xx but inbox does not receive, root cause is downstream Brevo delivery, bounce, block, spam placement, or DNS/domain authentication.

### 3. Brevo Dashboard QA

In Brevo Transactional logs, search the test recipient and provider message ID.

Record whether the message is:

- accepted
- delivered
- opened/clicked
- blocked
- bounced
- deferred
- rejected
- missing

Also verify:

- sender `noreply@pulsesoc.com` exists and is verified
- `pulsesoc.com` is authenticated
- transactional sending is enabled
- account is not suspended, limited, or blocked
- API key has transactional email permission

### 4. Cloudflare / DNS QA

Verify propagated records for `pulsesoc.com`:

- SPF includes Brevo
- DKIM records match Brevo
- DMARC exists and is valid
- Brevo domain verification records are present and propagated

### 5. Signup / Confirmation Proof

Required PASS evidence:

- Create a new production test account.
- Admin Dashboard shows the account.
- `/admin/emails?filter=confirmation` shows a confirmation email row.
- The row has provider `brevo`, provider status code, trace ID, and provider message ID.
- Real inbox receives confirmation email.
- Confirmation link uses `https://coinpilotx.app`.
- Confirmation link works.
- User becomes confirmed.
- User can log in.

### 6. Password Reset Proof

Required PASS evidence:

- Request password reset for a production account.
- Real inbox receives password reset email.
- Reset link uses `https://coinpilotx.app`.
- Reset link works.
- Email log shows provider response and trace ID.

## Failure Classification Matrix

Use the following evidence to classify the final root cause:

| Evidence | Root cause |
| --- | --- |
| No email log row | App did not call Brevo or route failed before send |
| `BREVO_API_KEY` loaded=false | Railway variable missing/not loaded |
| Provider code 401 | Brevo API key invalid/missing/wrong permission |
| Provider code 403 | Sender/domain not verified, Brevo account restricted, or transactional permission issue |
| Provider 2xx, message missing in Brevo | Brevo API response/log mismatch or wrong Brevo account/key |
| Provider 2xx, Brevo delivered, inbox missing | Recipient spam/filter/provider issue |
| Provider 2xx, Brevo blocked/bounced/deferred | Brevo delivery/domain/recipient reputation issue |
| Links are localhost/wrong host | Public base URL misconfigured |
| Confirmation link fails | Token route or token persistence problem |

## Files Changed

- `services/email_service.py`
- `bot.py`
- `scripts/email_confirmation_audit.py`
- `scripts/notification_delivery_audit.py`
- `reports/production_email_delivery_incident.md`

## Current Status

App-side diagnostic and sender-configuration fixes are implemented locally.

Email delivery is **not yet proven fixed** until production deployment is complete and a real inbox receives:

- a signup confirmation email
- a password reset email

## Remaining Manual Production Steps

1. Deploy this commit to Railway.
2. Open `/api/admin/email/diagnostics` as admin and save the JSON result.
3. Send a production direct test email through `/api/admin/email/direct-test`.
4. Confirm the provider message ID appears in Brevo Transactional logs.
5. Confirm the message arrives in a real inbox.
6. Test signup confirmation and password reset end to end.
