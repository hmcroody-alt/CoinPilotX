# Brevo IP Block Diagnosis

Date: 2026-06-13

## Confirmed Incident

PulseSoc user creation succeeds. The database creates users correctly.

Email delivery fails at Brevo with:

- Provider: Brevo
- HTTP code: 401
- Status: IP Blocked
- Safe app error: `Brevo rejected the request because the Railway server IP is not authorized in Brevo.`

This means the active bottleneck is Brevo API security, not signup persistence.

## Brevo Configuration Audit

The app now recognizes these Brevo sender variables:

- `BREVO_API_KEY`
- `BREVO_SENDER`
- `BREVO_FROM_EMAIL`
- `BREVO_SENDER_EMAIL`
- `BREVO_SENDER_NAME`
- `DEFAULT_FROM_EMAIL`
- `MAIL_FROM_ADDRESS`

Secrets are not printed in diagnostics. Admin diagnostics expose only loaded/missing booleans and masked sender values.

## Railway Outbound IP

The local machine IP is not the Railway service outbound IP and must not be used for Brevo allowlisting.

Current local workspace could not read linked Railway production variables or static outbound IPs through Railway CLI, so the exact production Railway outbound IP remains pending live production runtime verification.

Implemented admin-only runtime check:

- `GET /api/admin/email/outbound-ip`

Run this endpoint in production after deployment while logged in as an authorized admin. The returned `outbound_ip` is the IP Brevo will see for API requests from the PulseSoc runtime.

Railway official remediation reference: Railway Static Outbound IPs can be enabled per service on Pro plan from service Settings > Networking > Enable Static IPs. Railway notes traffic may be balanced over multiple static outbound IPs and the IPs are used after the next deploy.

Reference: https://docs.railway.com/networking/static-outbound-ips

## Brevo Restriction Causing Block

Brevo IP security blocks API requests from unknown or unauthorized IPs when API-key IP blocking is active.

Brevo official remediation path:

1. Open Brevo.
2. Go to Settings > Security > Authorized IPs.
3. Check blocking status for API keys.
4. If blocking unknown IPs is enabled, authorize the Railway outbound IP or static outbound IP range.
5. If the current Railway IP is dynamic, enable Railway Static Outbound IPs first, redeploy, then authorize the static IPs in Brevo.
6. Alternatively, deactivate Brevo API IP blocking if the account security policy allows it.

Brevo notes that API requests from unauthorized IPs are blocked even when the API key is valid.

References:

- https://help.brevo.com/hc/en-us/articles/5740111683858-Authorize-and-block-IP-addresses-for-API-and-SMTP-security
- https://developers.brevo.com/docs/ip-security

## Code Fixes Implemented

- Broadened Brevo 401 IP-block detection.
- Added `BREVO_SENDER` and `BREVO_FROM_EMAIL` support.
- Signup no longer fails account creation when Brevo blocks the verification email.
- Duplicate unverified signup no longer shows “Account already exists.”
- Resend confirmation now keeps the user in a recoverable pending-delivery state for known Brevo IP blocks instead of surfacing a hard server failure.
- Welcome-email retry skips the immediate retry sleep when the provider error is the known Brevo IP block.
- User-facing pending verification flow now shows:
  - `Account created successfully but verification email could not be delivered.`
  - `Resend Email`
  - `Change Email Address`
- Change-email flow requires the current account password and only works for unverified accounts.
- Mobile pending confirmation screen supports password-protected email change.
- Admin email tools now include:
  - Resend Verification Email
  - Resend Welcome Email
  - Retry Failed Queue
  - Check Railway Outbound IP
- Admin dashboard now shows:
  - Blocked Emails
  - Failed Emails
  - Pending Verification Emails
  - Successful Deliveries
  - Queued Retries

## Queue Retry System

Failed Brevo sends are already written into `failed_email_queue`.

Admin retry action:

- `POST /admin/emails/retry-failed`

The retry worker attempts pending/failed/retry-ready rows whose `next_retry_at` is due, updates retry count, and backs off failed rows.

## Exact Remediation

1. Deploy the code changes.
2. Log in to PulseSoc admin.
3. Open `/api/admin/email/outbound-ip`.
4. Copy the returned production outbound IP.
5. In Railway, if Static Outbound IPs are not enabled:
   - Open the PulseSoc service.
   - Settings > Networking.
   - Enable Static IPs.
   - Redeploy.
   - Re-run `/api/admin/email/outbound-ip`.
6. In Brevo:
   - Security > Authorized IPs.
   - Add the Railway static outbound IPs.
   - Confirm API-key blocking allows those IPs.
7. Go to `/admin/emails`.
8. Click Retry Failed Queue.
9. Use Resend Verification Email for unverified users.
10. Verify new logs show successful Brevo delivery.

## Validation

Local validation added:

- `scripts/brevo_ip_block_delivery_audit.py`

This audit verifies:

- Mobile signup returns success when Brevo blocks email delivery.
- Duplicate unverified signup does not return account-already-exists copy.
- Change email requires password.
- Unverified email change updates the account and keeps it unverified.
- Admin resend verification/welcome actions exist.
- Admin metrics exist.
- Runtime outbound IP endpoint exists.

Validation run on 2026-06-13:

- PASS: `.venv/bin/python -m py_compile bot.py services/email_service.py scripts/brevo_ip_block_delivery_audit.py`
- PASS: `.venv/bin/python scripts/brevo_ip_block_delivery_audit.py`
- PASS: `.venv/bin/python scripts/production_email_delivery_incident_audit.py`
- PASS: `npm run typecheck` in `mobile/pulse-react-native`
- PASS: `git diff --check` for Brevo/email files
- ENVIRONMENT BLOCKED: `npm run typecheck` in the duplicate `mobile` tree cannot run because that tree does not have `tsc` installed in its local environment. The canonical `mobile/pulse-react-native` tree passed typecheck.

## Remaining Manual Check

Brevo dashboard itself must be checked by an authenticated Brevo admin:

- Security > Authorized IPs
- API-key blocking status
- Unauthorized IP list
- Sender/domain restrictions
- Sender identity verification

This cannot be proven from local code without Brevo dashboard access.
