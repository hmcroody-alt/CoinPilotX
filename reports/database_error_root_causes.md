# Database Error Root Causes

Generated: 2026-06-01

Scope: Phase 2 final blocker investigation for production database errors observed in Railway logs.

## Summary

Production database connectivity is operational through the running app, but Railway logs show several PostgreSQL compatibility and schema-drift errors. The errors are not caused by R2, CDN, or media upload storage. They are application SQL issues that appear only against the production PostgreSQL database.

## Root Causes

### 1. Trial email event upsert used SQLite syntax

Status: Resolved in code

Evidence:

- Railway log error: `psycopg2.errors.SyntaxError: syntax error at or near "OR"`
- Failing SQL: `INSERT OR REPLACE INTO trial_email_events (...)`
- Source: `record_trial_email_event` in `bot.py`

Cause:

`INSERT OR REPLACE` is SQLite-specific. Production uses PostgreSQL, which requires `INSERT ... ON CONFLICT (...) DO UPDATE`.

Fix:

Changed the insert to PostgreSQL-compatible `ON CONFLICT(user_id, event_type) DO UPDATE`, which is also supported by modern SQLite.

### 2. Live discovery queried a non-existent users.trust_score column

Status: Resolved in code

Evidence:

- Railway log error: `psycopg2.errors.UndefinedColumn: column u.trust_score does not exist`
- Failing SQL selected `COALESCE(u.trust_score,72) AS creator_trust`
- Production schema stores creator trust in `user_privilege_profiles.trust_score`, not `users.trust_score`.

Cause:

The live-now card query assumed a trust score column on `users`. The existing schema separates profile/auth fields from privilege and trust fields.

Fix:

The live-now query now joins `user_privilege_profiles` and reads `COALESCE(upp.trust_score,72)`.

### 3. Feed intelligence compared text timestamps to PostgreSQL date values

Status: Resolved in code

Evidence:

- Railway log error: `psycopg2.errors.UndefinedFunction: operator does not exist: text >= date`
- Failing SQL: `SELECT COUNT(*) AS total FROM pulse_posts WHERE created_at>=date('now') AND deleted_at IS NULL`

Cause:

`pulse_posts.created_at` is stored as text ISO timestamps. PostgreSQL rejected direct comparison between text and `date('now')`.

Fix:

The code now computes a UTC ISO date cutoff in Python and passes it as a query parameter.

### 4. Pulse Rooms/Groups message filter used a literal percent in a parameterized PostgreSQL query

Status: Resolved in code

Evidence:

- Railway log error: `IndexError('tuple index out of range')`
- Failing endpoint examples:
  - `/api/pulse/communications/conversations/71/messages?limit=80`
  - `/api/pulse/communications/conversations/72/messages?limit=80`
- Failing SQL included `LIKE '% joined'` while also using `%s` parameters after translation.

Cause:

The database compatibility layer translates `?` placeholders to `%s` for PostgreSQL. Psycopg2 treats literal `%` characters in parameterized SQL as formatting tokens unless they are escaped.

Fix:

Escaped the filter pattern as `LIKE '%% joined'`. This remains equivalent for SQL matching while avoiding PostgreSQL parameter interpolation failures.

## Validation Needed After Deployment

- Trigger production signup/trial email path and confirm no `trial_email_events` SQL error appears.
- Open Pulse live discovery and confirm no `u.trust_score` SQL error appears.
- Load Pulse feed and confirm no `created_at>=date('now')` SQL error appears.
- Open Direct, Rooms, and Groups conversations and confirm no message-history `IndexError` appears.

## Phase 2 Impact

These fixes remove production database errors that affect feed intelligence, live discovery, trial email logging, and Rooms/Groups message loading. They do not add features or change product behavior.
