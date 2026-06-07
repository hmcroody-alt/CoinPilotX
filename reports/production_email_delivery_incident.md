# Production Email Delivery Incident

Date: 2026-06-06

## Incident

Users can create accounts, but production confirmation emails are not confirmed as reaching inboxes. Password reset and payment-related emails may share the same transactional delivery path.

## What Was Verified

- Local code path uses Brevo transactional email for signup confirmation, password reset, and payment notifications.
- Production admin page `/admin/emails` exists, but QA browser access redirected to `/admin/login`, so production email logs and direct-test execution could not be verified from the current browser session.
- Railway browser access was available for the production `CoinPilotX` service.
- Railway production variables show these keys present: `BREVO_API_KEY`, `BREVO_EMAIL_ENABLED`, `BREVO_SENDER_EMAIL`, `BREVO_SENDER_NAME`, `MAIL_FROM_ADDRESS`, `MAIL_FROM_NAME`, `APP_BASE_URL`, and `PULSE_APP_URL`.
- Railway production variables do not show `DEFAULT_FROM_EMAIL`, `SUPPORT_EMAIL`, `SECURITY_EMAIL`, or `BREVO_WEBHOOK_SECRET`.
- A safe production container diagnostic reports provider readiness as true.
- Production email logs show earlier signup/welcome attempts failed with Brevo `401`.
- Production email logs show newer password reset/password-changed messages accepted by Brevo with `201`.
- A real production route-level QA after the direct inbox test showed fresh signup confirmation, password reset, welcome, and payment confirmation attempts still returning Brevo `401` from the normal transactional sender path.
- The direct inbox test proved that at least one production diagnostic path could reach Brevo/Gmail, but it did not prove the user-facing signup/reset/payment wrappers were healthy.
- Local workspace environment does not contain Brevo credentials. Local email logs show Brevo-not-configured failures, but those are local-only and are not enough to classify the production root cause.

## Code Changes

- Email service now treats the built-in Pulse sender fallback as an actual sender candidate instead of blocking Brevo before the provider can respond.
- Brevo diagnostics now include safe sender source, sender domain, masked sender email, and fallback-sender state.
- Admin direct-test API now returns masked sender diagnostics instead of raw sender email.
- Admin email page now shows sender source/domain and warns when the built-in fallback is being used.
- Fixed the production PostgreSQL compatibility layer so SQL literals like `LIKE 'sent%'` are escaped before they reach the driver. This prevents `/admin/emails` from crashing with `IndexError: tuple index out of range`.
- Fixed `public_url_for()` so confirmation links can be generated safely outside an active request, including admin/console/background resend contexts.
- The Brevo API key is now stripped before provider requests. This protects production from hidden leading/trailing whitespace in Railway environment values, which can make the key appear configured while Brevo still returns `401`.
- Safe diagnostics now expose only whether API-key whitespace was detected/cleaned, never the key.
- Added a shared failed-email queue retry helper and an admin `/admin/emails/retry-failed` action so signup confirmations, password resets, welcome emails, and payment-related queued failures can be retried after provider configuration is corrected.
- `.env.example` now includes `DEFAULT_FROM_EMAIL=noreply@pulsesoc.com`.
- Added audit guards for the production email incident diagnostics and PostgreSQL percent placeholder handling.

## Current Classification

Production root cause at provider-submission level is classified as A: Brevo API key/configuration failure, with a likely hidden environment-value normalization issue. Evidence: production route-level QA recorded fresh `failed_brevo_401` rows for signup confirmation, password reset, welcome, and premium confirmation even after the direct email test reached Gmail. The sender now trims the configured key before all Brevo SMTP API requests.

Production admin visibility root cause is classified as H: App/admin diagnostics route failure. Evidence: `/admin/emails` crashed in the production database wrapper while running email dashboard queries containing literal `%` patterns.

Production resend/ops helper root cause is classified as H: request-context dependent link generation. Evidence: console/admin-style confirmation sends could not build verification links outside an active request until `public_url_for()` was made context-safe.

Inbox delivery proof is mixed: the direct production test reached Gmail, but user-facing transactional flows still failed before the key-normalization fix. Fresh signup/reset/payment flow proof must be repeated after deploy.

Remaining buckets to verify next:

- D: Brevo accepted email but final inbox status shows blocked, bounced, spam, deferred, or routed to spam.
- J: Recipient inbox/quarantine/provider filtering.

## Required Production Proof Before Calling Fixed

Use the production admin session or Brevo/Railway dashboards and verify:

1. `/api/admin/email/diagnostics` returns `ready=true` and only safe booleans for key whitespace.
2. Direct production email test arrives in a real inbox.
3. New signup confirmation email arrives in a real inbox.
4. Confirmation link opens and marks the account verified.
5. Password reset email arrives in a real inbox.
6. Premium/payment confirmation email arrives in a real inbox.
7. Brevo transactional logs show accepted/delivered status for the same trace IDs.
8. Failed-email queue retry sends queued rows successfully after the provider path is healthy.

## Safe Production Checks

Do not print or screenshot secrets. Only record:

- `brevo_api_key_configured=true/false`
- `brevo_email_enabled=true/false`
- `default_from_email_configured=true/false`
- `brevo_sender_email_configured=true/false`
- `sender_domain`
- `sender_email_source`
- Brevo response status code
- Brevo provider message ID presence
- Delivery status: accepted, delivered, bounced, blocked, spam, deferred

## Follow-Up If Production Still Fails

- If diagnostics show missing config, set the missing Railway env and redeploy.
- If Brevo returns 401, replace the API key in Railway.
- If Brevo returns 403, verify sender/domain authentication in Brevo and Cloudflare DNS.
- If Brevo accepts but inbox does not receive, inspect Brevo delivery logs for bounce/block/spam/deferred and check recipient spam/quarantine.
- If confirmation links arrive but fail, verify `PUBLIC_BASE_URL` or `APP_BASE_URL` resolves to `https://coinpilotx.app`.

## Current Blocker

To finish the proof, use a real inbox that Codex can observe or provide live confirmation for. The current session can prove production Brevo acceptance, but cannot prove inbox arrival, confirmation-link click-through, or password-reset inbox delivery without a recipient inbox.
