# PulseSoc Ads Security Review

Date: 2026-06-24

## Controls Added

- All advertiser write endpoints require authenticated PulseSoc account sessions.
- All ad tracking writes require CSRF.
- Admin review, approval, rejection, suspension, and kill switch endpoints require admin permissions.
- Destination URLs are restricted to `http` or `https`.
- Local, `javascript:`, `data:`, `file:`, and malformed destinations are rejected.
- Creative text is sanitized and length-bounded.
- Moderation approval is required before serving.
- Campaign must be active before serving.
- Platform kill switch can stop all ad serving.

## Abuse Resistance

- API route layer uses existing `security_guard` rate limiting where available.
- Frequency caps prevent repeated serving to the same logged-in user/session.
- Policy flags are created from automated review checks.
- Audit logs capture advertiser/admin lifecycle actions without secrets.

## Non-Goals

- No billing secrets were added.
- No payment credentials were added.
- No internal admin data is exposed publicly.
- No advertiser can approve their own creative.
