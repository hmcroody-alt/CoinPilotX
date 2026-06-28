# System Mission Control Security Review

## Privacy Boundary

The System Mission Control service exposes aggregate counts, readiness flags, scores, and recommendations only.

It does not expose:

- Raw tokens
- Provider credentials
- Database URLs
- Private keys
- Private message bodies
- Filesystem paths
- Internal worker tokens

## Authorization

- User dashboard routes require an authenticated account.
- Admin routes require admin login.
- User-facing system state omits admin routes and sensitive diagnostics.
- Admin module pages intentionally show only redacted operational diagnostics.

## Input Safety

- Dynamic route keys are normalized and matched against registered modules.
- Unknown module keys redirect to the safe Mission Control root.
- Display values are escaped with `clean_html`.

## Operational Safety

- No automatic restarts, cache clears, queue rebalances, or scaling actions were added.
- Admin quick actions link to existing protected tools.
- Missing provider keys produce honest limited states instead of fake green status.
