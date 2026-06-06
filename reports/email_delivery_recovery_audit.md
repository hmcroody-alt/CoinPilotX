# Email Delivery Recovery Audit

Generated: 2026-06-06

## Root Cause

Pulse account confirmation emails were generated, but the signup and login experience did not give users a reliable recovery path when Brevo rejected or could not send the message. Confirmation and password reset links were also built with request-relative `_external=True`, which can produce the wrong host in preview/local/proxy contexts if the request host is not the production domain.

The active sender is Brevo through `services/email_service.py` and the legacy helper in `bot.py` logs to `email_logs`. The platform already had basic Brevo diagnostics, but confirmation sends were fire-and-forget and admin resend tooling sent generic account-update emails instead of fresh confirmation links.

## Fixed Files

- `bot.py`
  - Added production-safe public URL helper using `APP_BASE_URL`/`PULSE_APP_URL`/`BASE_URL`/`DOMAIN`, defaulting to `https://coinpilotx.app`.
  - Added reusable account confirmation email sender and resend helper.
  - Signup now sends confirmation email, signs the user out, and asks them to confirm before login.
  - Login and mobile login now block unconfirmed email accounts with clear guidance.
  - Password reset links now use the public URL helper.
  - Admin user resend endpoint can send confirmation, password reset, and payment confirmation emails.
  - Added owner-only emergency manual email confirmation endpoint.
  - Added admin email QA tools.
  - Added Brevo delivery webhook endpoints with optional `BREVO_WEBHOOK_SECRET`.
  - Extended email logs with trace ID, retry count, delivery status, and webhook event fields.
- `services/email_service.py`
  - Added safer Brevo enabled detection.
  - Added clear 401, 403, and 429 Brevo error classification.
- `templates/account.html`
  - Added login-page resend confirmation form and clear confirmation copy.
- `scripts/email_confirmation_audit.py`
- `scripts/password_reset_email_audit.py`
- `scripts/payment_email_audit.py`
- `scripts/notification_delivery_audit.py`

## Brevo Configuration Status

The code now safely reports:

- Brevo API key configured: yes/no
- Sender email configured: yes/no
- Sender name configured: yes/no
- Missing fields

Secrets are not printed or exposed. Production QA still needs Railway/Brevo evidence that:

- `BREVO_API_KEY` exists and is active.
- `BREVO_EMAIL_ENABLED=true`.
- `BREVO_SENDER_EMAIL` is verified in Brevo.
- The sending domain has valid SPF/DKIM/DMARC in Cloudflare.

## Email Flows Covered

- Signup confirmation
- Resend confirmation from login
- Resend confirmation from admin email tools
- Resend confirmation from admin user detail
- Password reset
- Admin password reset test
- Payment/Premium confirmation test
- Pulse notification email service audit
- Brevo delivery webhook event updates

## Failed Flows

No local code-path failures were found after the patch. Real delivery can only be marked PASS after an authenticated production test confirms Brevo accepted and delivered the email.

## Remaining Manual Production QA

1. Confirm Railway has:
   - `APP_BASE_URL=https://coinpilotx.app`
   - `BREVO_EMAIL_ENABLED=true`
   - `BREVO_API_KEY`
   - `BREVO_SENDER_EMAIL`
   - `BREVO_SENDER_NAME`
2. In Brevo, verify sender and domain authentication.
3. In Cloudflare DNS, verify SPF/DKIM/DMARC records for the sending domain.
4. Create a new production test user with a real inbox.
5. Confirm the account remains signed out and the login page asks for confirmation.
6. Confirm the email arrives.
7. Click the confirmation link and verify `email_verified=1`.
8. Log in successfully.
9. Trigger password reset and verify the reset email arrives.
10. Trigger an admin QA test email and verify `/admin/emails` shows provider status code, message ID, trace ID, and delivery status.

## Notes

If Brevo returns:

- `401`: Check `BREVO_API_KEY` in Railway.
- `403`: Verify the sender email and domain authentication in Brevo/Cloudflare.
- `429`: Retry later or review Brevo rate limits.
- `failed_brevo_not_configured`: Add missing Railway variables shown in `/admin/emails`.
