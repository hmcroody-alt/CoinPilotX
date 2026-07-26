# Production Readiness Audit

- Routes audited: 30
- Issues found: 1
- Deployment blockers: 0
- Issues fixed: PostgreSQL SELECT alias in `/admin/security` suspicious-domain HAVING clause is guarded by `scripts/postgres_compatibility_audit.py`.
- Remaining risks: local test-client smoke checks do not exercise third-party providers, real browser media playback, or production-only data volumes.

| Surface | Route | Result | HTTP | Detail |
| --- | --- | --- | --- | --- |
| Admin routes | `/admin/dashboard` | PASS | 302 | HTTP 302 |
| Admin routes | `/admin/security` | PASS | 302 | HTTP 302 |
| Admin routes | `/admin/audit-logs` | PASS | 302 | HTTP 302 |
| Admin routes | `/admin/performance` | PASS | 302 | HTTP 302 |
| Admin routes | `/admin/system-audit` | PASS | 302 | HTTP 302 |
| Admin routes | `/admin/messages-health` | PASS | 302 | HTTP 302 |
| Admin routes | `/admin/notifications` | PASS | 302 | HTTP 302 |
| Messaging | `/pulse/messages` | PASS | 200 | HTTP 200 |
| Messaging | `/pulse/messages-v2` | PASS | 200 | HTTP 200 |
| Messaging | `/api/pulse/communications/v2/conversations` | PASS | 200 | HTTP 200 |
| Messaging | `/api/pulse/communications/v2/realtime?after_id=0&limit=20` | PASS | 200 | HTTP 200 |
| Live streaming | `/pulse/live` | PASS | 200 | HTTP 200 |
| Live streaming | `/api/pulse/live/stream` | PASS | 204 | HTTP 204 |
| Notifications | `/pulse/notifications` | PASS | 200 | HTTP 200 |
| Notifications | `/api/pulse/notifications/unread-count` | PASS | 200 | HTTP 200 |
| Notifications | `/api/pulse/badge-counts` | PASS | 200 | HTTP 200 |
| Composer | `/pulse/create` | PASS | 302 | HTTP 302 |
| Composer | `/pulse/camera` | PASS | 200 | HTTP 200 |
| Videos | `/pulse/videos` | PASS | 200 | HTTP 200 |
| Videos | `/api/pulse/videos` | PASS | 200 | HTTP 200 |
| Reels | `/pulse/reels` | PASS | 200 | HTTP 200 |
| Reels | `/api/pulse/reels/feed` | PASS | 200 | HTTP 200 |
| Premium | `/pulse/premium` | PASS | 200 | HTTP 200 |
| Premium | `/pulse/premium/undx` | WARN | 403 | HTTP 403 |
| Premium | `/pulse/creator/dashboard` | PASS | 200 | HTTP 200 |
| Premium | `/admin/premium-command` | PASS | 302 | HTTP 302 |
| Marketplace | `/pulse/marketplace` | PASS | 200 | HTTP 200 |
| Marketplace | `/pulse/merchant/dashboard` | PASS | 200 | HTTP 200 |
| Marketplace | `/admin/marketplace-command` | PASS | 302 | HTTP 302 |
| Marketplace | `/admin/merchant-applications` | PASS | 302 | HTTP 302 |
