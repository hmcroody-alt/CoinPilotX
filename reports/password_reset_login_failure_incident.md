# Password Reset Login Failure Incident

Date: 2026-06-07

## Root Cause

The reset flow wrote to the same `users.password_hash` field that login verifies, but the flow did not prove the saved hash before reporting success. Login also checked the password before checking email confirmation, so an unconfirmed account could be shown the wrong failure message instead of clear confirmation guidance.

The account lookup also depended on whichever matching email row the database returned first. That is fragile if duplicate or legacy user rows exist. Reset and login now use deterministic active-user resolution.

## Affected Routes and Files

- `POST /reset-password/<token>`
- `POST /api/mobile/auth/reset-password`
- `POST /login`
- `POST /api/mobile/auth/login`
- `GET /api/admin/auth/password-reset-diagnostic`
- `bot.py`
- `scripts/password_reset_login_audit.py`
- `scripts/auth_flow_audit.py`

## Fix

- Login now checks account existence, account status, and email confirmation before password verification.
- Unconfirmed accounts now receive: `Please confirm your email before logging in.`
- Password reset now saves the new hash, immediately reads it back, and verifies the submitted password against the stored hash before returning success.
- Password reset now rolls back and returns a reset-specific failure if the hash cannot be verified.
- Successful reset now closes all active reset tokens for that user, preventing stale token reuse.
- Account lookup by email/username now prefers non-deleted, active, newest matching users.
- Added owner/admin-safe diagnostics showing user existence, hash presence, password last changed timestamp, confirmation status, reset token status, and duplicate email count without exposing passwords or hashes.

## Validation

- Test account requested reset token.
- Reset link set a new password.
- Login with new password succeeded through the mobile auth API.
- Login with old password failed.
- Used reset token could not be reused.
- Web reset form set a new password accepted by mobile login.
- Web login accepted the reset password.
- Expired reset token failed.
- Unconfirmed account with correct password showed the confirmation message, not a password error.

## Affected User Recovery Steps

1. Use the admin user detail page or email command center to send the affected user a fresh password reset link.
2. If diagnostics show `account_confirmed=false`, resend confirmation or confirm after support review.
3. Ask the user to set a new password from the fresh link.
4. Confirm she can log in with the new password.
5. Confirm old reset links are invalid.
