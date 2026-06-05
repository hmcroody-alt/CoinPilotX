# Profile Avatar Persistence Root Cause

## Root Cause
The avatar UI could display a temporary preview while the backend response was trusted before verifying the database row. This made a successful-looking upload vulnerable to stale cached images or failed persistence.

## Fix
- Backend verifies the `users.avatar_url` row after update.
- Success is returned only after persistence is confirmed.
- API returns `avatar_url_cache_busted`.
- Profile edit UI replaces previews with the durable saved URL.
- Header/nav/avatar images update from the same persisted field.

## Guardrail
`scripts/profile_avatar_persistence_audit.py` checks for persistence verification and cache-busted return values.
