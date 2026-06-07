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
- Local workspace environment does not contain Brevo credentials. Local email logs show Brevo-not-configured failures, but those are local-only and are not enough to classify the production root cause.

## Code Changes

- Email service now treats the built-in Pulse sender fallback as an actual sender candidate instead of blocking Brevo before the provider can respond.
- Brevo diagnostics now include safe sender source, sender domain, masked sender email, and fallback-sender state.
- Admin direct-test API now returns masked sender diagnostics instead of raw sender email.
- Admin email page now shows sender source/domain and warns when the built-in fallback is being used.
- `.env.example` now includes `DEFAULT_FROM_EMAIL=noreply@pulsesoc.com`.
- Added audit guard for the production email incident diagnostics.

## Current Classification

Production root cause at provider-submission level is classified as A: Brevo API key/configuration failure. Evidence: production email logs recorded `failed_brevo_401` for confirmation/welcome attempts, followed later by `sent_brevo` with status `201` for password reset/password changed after configuration was corrected.

Inbox delivery is not proven yet because a real recipient inbox was not accessible in this session.

Remaining buckets to verify next:

- D: Brevo accepted email but final inbox status shows blocked, bounced, spam, deferred, or routed to spam.
- J: Recipient inbox/quarantine/provider filtering.

## Required Production Proof Before Calling Fixed

Use the production admin session or Brevo/Railway dashboards and verify:

1. `/api/admin/email/diagnostics` returns `ready=true`.
2. Direct production email test arrives in a real inbox.
3. New signup confirmation email arrives in a real inbox.
4. Confirmation link opens and marks the account verified.
5. Password reset email arrives in a real inbox.
6. Brevo transactional logs show accepted/delivered status for the same trace IDs.

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
