# Account Button Intelligence Audit

## Button Standard

Generic Account buttons were replaced with contextual actions. User-facing Dashboard widgets and Account Command cards now prefer subsystem labels such as:

- Manage Profile
- Continue Verification
- View Account Health
- Secure Account
- Manage Settings
- Harden Security
- Protect Identity
- Review Sessions
- Manage Devices
- View Timeline
- View Alerts
- Review Logins

## State Standard

The Account system now uses strict state labels:

- READY
- ACTION
- REVIEW
- WARNING
- LOCKED
- PREMIUM
- BETA
- PARTIAL
- ADMIN

The old ambiguous `ON` state is no longer used by the new Account state model.

## User Dashboard Wiring

Every Account card links to a real Dashboard route. Primary existing flows remain backed by their existing full pages. New intelligence modules render owner-safe subsystem pages with metrics, recommendations, command actions, protection/recovery layers and audit boundaries.

## Backend Wiring

Each visible Account subsystem has a matching admin surface under `/admin/account-command/<section>`. Admin pages expose safe counts, safe searchable records and security boundaries instead of private raw data.

## Privacy Notes

Button and state payloads are generated server-side. The frontend does not receive internal provider tokens, private document paths, password hashes, database URLs or moderation-only notes.
