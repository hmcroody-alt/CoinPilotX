# Command Center Redis Integration Layer

Generated: 2026-06-19

## Goal

Service 3 introduces Redis as an optional realtime/cache layer for PulseSoc Command Center. PostgreSQL remains the source of truth for durable data. Redis accelerates operational state only.

## Architecture

```text
Main App
  -> Command Center Worker
      -> Redis speed layer
      -> PostgreSQL durable source of truth
```

## Redis Responsibilities

- presence state
- typing indicators
- unread counter cache
- realtime connection registry
- rate limiting windows
- event fanout/replay cache
- recent notification cache

## PostgreSQL Responsibilities

- permanent messages
- conversations
- receipts
- reactions
- notifications
- audit logs
- security events
- AI events

Redis never replaces PostgreSQL persistence.

## Integration Module

Added:

- `services/command_center_worker/redis_manager.py`

Public helpers:

- `get_redis()`
- `redis_health()`
- `safe_get()`
- `safe_set()`
- `safe_delete()`
- `safe_publish()`
- `safe_subscribe()`

Additional safe helpers:

- `safe_scan()`
- `safe_rate_limit()`
- `rate_limit_login()`
- `rate_limit_messages()`
- `rate_limit_comments()`
- `rate_limit_dm_spam()`
- `rate_limit_typing()`

If `REDIS_URL` is missing, all Redis helpers fail gracefully and the worker continues with PostgreSQL/in-process fallback behavior.

## Worker Health

`GET /internal/command-center/health` now includes:

```json
{
  "redis_enabled": false,
  "redis_ok": false,
  "redis_latency_ms": null
}
```

When Redis is configured and healthy, `redis_enabled=true`, `redis_ok=true`, and latency is reported in milliseconds. No Redis URL or credential is exposed.

## Key Strategy

| Area | Key | TTL |
| --- | --- | --- |
| Presence | `presence:user:<id>` | 20 minutes |
| Typing | `typing:<conversation_id>:<user_id>` | 5 seconds |
| Unread cache | `unread:user:<id>` | 10 minutes |
| Realtime connection | `connection:<user_id>:<session_id>` | 3 minutes |
| Realtime event | `realtime:user:<id>:event:<event_id>` | 10 minutes |
| Realtime channel | `realtime:user:<id>` publish channel | Pub/sub only |
| Conversation channel | `realtime:conversation:<id>` publish channel | Pub/sub only |
| Notification recent cache | `notifications:user:<id>` | 10 minutes |
| Notification unread cache | `notifications:unread:user:<id>` | 10 minutes |
| Rate limits | `rate:<scope>` | scope-specific |

## Presence Strategy

Presence writes cache to Redis first, then persist to PostgreSQL/SQLite. Reads check Redis first and fall back to persistent storage. Stale cleanup updates or clears Redis state and preserves the database cleanup path.

## Typing Strategy

Typing state is stored in Redis using a 5 second TTL. The existing durable message-event record is still accepted for compatibility and auditability, but live typing lookups prefer Redis keys so typing expires automatically.

## Unread Counter Cache

Unread counts read from Redis when present. Cache misses read from PostgreSQL participant state and then populate `unread:user:<id>`. Message-created and message-read events invalidate the relevant unread cache.

## Realtime Registry And Fanout

Realtime connections are written to Redis under `connection:<user_id>:<session_id>` and kept in the in-process registry as fallback. Published events are written to per-user Redis event keys and published to per-user/conversation channels. If Redis is unavailable, the in-process replay buffer still serves poll/SSE responses.

## Rate Limiting

Redis-backed helpers exist for:

- login
- messages
- comments
- DM spam
- typing spam

If Redis is unavailable, rate limits fail open to avoid locking out legitimate users due to cache outages. Typing spam uses Redis rate limits when enabled and falls back to local in-process throttling.

## Notification Cache

Recent notifications and unread counts are cached per user. Notification creation and read actions invalidate the relevant Redis caches. PostgreSQL remains authoritative.

## Security

Redis payload sanitization avoids storing:

- passwords
- secrets
- tokens
- credentials
- session secrets

Redis stores only operational state. Health responses and reports do not expose Redis URLs, database URLs, tokens, or filesystem paths.

## Fallback Strategy

Redis unavailable:

- worker starts
- health reports Redis disabled or unhealthy
- presence still persists in PostgreSQL
- typing falls back to durable event lookup
- unread counts read from PostgreSQL
- notifications read from PostgreSQL
- realtime uses in-process replay
- rate limits fail open

Redis available:

- operational state uses Redis first
- PostgreSQL continues to persist durable records

## Validation

Added audit:

- `scripts/command_center_redis_audit.py`

Audit covers:

- Redis health
- missing Redis fallback
- memory-backed Redis behavior
- presence cache
- typing TTL
- unread cache
- notification cache
- realtime registry
- event fanout
- rate limit storage
- secret/path redaction

## Remaining Notes

Production should attach a real Railway Redis service and set `REDIS_URL` on the Command Center Worker. The local audit uses `memory://` only to validate behavior without requiring a live Redis daemon.
