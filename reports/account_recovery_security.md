# Account Recovery Security

## Recovery Options
- Recovery codes
- Recovery email
- Recovery phone
- Trusted devices
- Support review as last resort

## Privacy Guardrails
- Emails are masked.
- Phone numbers are masked.
- Recovery codes are shown only once at generation.
- Raw IP addresses and raw device fingerprints are not exposed.

## Sensitive Actions
Sensitive routes can use `session["recent_reauth_at"]` as the foundation for requiring recent reauthentication.
