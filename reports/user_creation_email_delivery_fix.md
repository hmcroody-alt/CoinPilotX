# User Creation Email Delivery Fix

## Issue

The admin email dashboard showed signup verification, welcome, and password reset emails stuck in `queued`. User creation could report success when the email was only inserted into the outbox, not when Brevo accepted delivery.

## Root Cause

- `send_account_confirmation_email()` treated `enqueue_platform_email()` success as delivery success.
- `send_password_reset_email()` also returned success for queue insertion only.
- The outbox processor updated `failed_email_queue`, but did not update the original `email_logs` row that admins see, so rows could remain visibly `queued` even after processing.
- Signup welcome emails created a default `support@pulsesoc.com` copy, which made one signup look like duplicate welcome delivery.

## Fix Applied

- Verification emails now send through the direct Brevo provider path and return success only when Brevo accepts the message.
- Password reset emails now use the direct provider path and preserve retry queue fallback on failure.
- Outbox processing now finalizes the original `email_logs` row by trace ID with `sent_brevo`, `failed_brevo_*`, provider status, provider message ID, retry count, and delivery status.
- Signup support-copy welcome email is disabled by default and can only be re-enabled with `PULSESOC_SIGNUP_SUPPORT_COPY=1`.
- `/admin/emails` now includes a `Queued` filter, a queued-log metric, and clearer “Process Pending Queue” wording.

## Security / Privacy

- Provider secrets remain hidden.
- Trace IDs remain safe for support diagnostics.
- Failed provider sends still create retry jobs without exposing raw secret values.

## QA

- Static audit added: `scripts/user_creation_email_delivery_audit.py`.
- Existing Brevo incident audits remain compatible.

## Remaining Limitations

- If Brevo rejects the sender/domain or Railway outbound IP, signup correctly reports a delivery failure and keeps the account unconfirmed. Admins must fix the provider-side issue or use the existing owner-only emergency confirmation flow after identity review.
