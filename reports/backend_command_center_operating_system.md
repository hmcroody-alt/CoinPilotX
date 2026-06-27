# Backend Command Center Operating System

PulseSoc now uses `services/backend_management_registry.py` as the permanent backend-management source of truth and `/admin/command-center` as the operating-system surface.

## Operating Model

- The registry defines feature key, display name, category, route, role, permission, status, owner, backend service, audit log table, risk, launch-critical state, and backend manageability.
- `/admin/command-center` renders role-filtered live command modules, provider readiness, department rooms, and launch state.
- `/admin/command-center/<module>` renders feature-level inventory, live metrics, module operators, actions, and failure behavior.
- `/admin/launch-readiness` shows strict launch readiness, provider gaps, audit gaps, and backend coverage.
- `/api/admin/backend-management/registry` exposes safe admin-only JSON for diagnostics and never returns secret values.
- Registry rows are synced additively into `backend_feature_registry` when the command center loads.
- Audit events can be stored in `backend_management_audit_events` without changing existing feature ownership.

## Operating Snapshot

- Total feature surfaces: 77
- Registered modules: 12
- Managed features: 77
- Partial features: 27
- External provider gaps: 12

## Modules

| Module | Key | Total | Active | Manageable | Readiness | Risk |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Account Command Center | `account` | 10 | 9 | 10 | 95% | high |
| Network Command Center | `network` | 6 | 3 | 6 | 75% | high |
| Creator Command Center | `creator` | 8 | 6 | 8 | 88% | critical |
| Moderation / Safety Command Center | `moderation` | 4 | 4 | 4 | 100% | low |
| Ads Command Center | `ads` | 6 | 6 | 6 | 100% | low |
| Economy Command Center | `economy` | 6 | 4 | 6 | 83% | critical |
| Media Command Center | `media` | 6 | 3 | 6 | 75% | critical |
| AI Command Center | `ai` | 4 | 2 | 4 | 75% | critical |
| System Command Center | `system` | 18 | 6 | 18 | 67% | critical |
| Launch Readiness Command Center | `launch` | 3 | 2 | 3 | 83% | critical |
| Global Controls Command Center | `controls` | 3 | 1 | 3 | 67% | critical |
| Audit Command Center | `audit` | 3 | 3 | 3 | 100% | low |

## Module Actions

| Module | Key | State | Surface | Operators | Actions |
| --- | --- | --- | --- | --- | --- |
| Account Command Center | `account` | WATCH | /admin/account-command | Owner, admin, security, trust roles | review, revert, restrict, force logout, audit |
| Network Command Center | `network` | WATCH | /admin/notifications, /admin/private-chat-reports, social operations | Admin, moderator, support | inspect, triage, notify, mark safe, escalate |
| Creator Command Center | `creator` | CRITICAL | /admin/pulse-moderation, /admin/pulse-analytics | Moderator, creator ops, admin | review, remove, restore, feature, escalate |
| Moderation / Safety Command Center | `moderation` | ONLINE | /admin/pulse-moderation, /admin/security, /admin/scam-shield | Trust and safety, moderators, owner | approve, reject, block, mark safe, investigate |
| Ads Command Center | `ads` | ONLINE | /admin/pulse-ads-review-board, /admin/pulse-ads-delivery-intelligence | Ads ops, finance, owner | approve, reject, pause, kill switch, audit spend |
| Economy Command Center | `economy` | CRITICAL | /admin/payments-command-center, /admin/pulse-ad-finance | Finance admins and owner | inspect, reconcile, refund prepare, pause, audit |
| Media Command Center | `media` | CRITICAL | /admin/pulse-music-review, /admin/pulse-infrastructure | Media ops, moderator, admin | approve, reject, quarantine, repair, audit |
| AI Command Center | `ai` | CRITICAL | /admin/ai-usage, /admin/scam-shield | AI ops, security admins | inspect, disable, explain risk, audit |
| System Command Center | `system` | CRITICAL | /admin/system, /admin/performance | Engineering, owner | health check, diagnose, restart externally, disable feature, audit |
| Launch Readiness Command Center | `launch` | CRITICAL | /admin/launch-readiness | Owner, launch lead | review blockers, open module, run audit, document risk |
| Global Controls Command Center | `controls` | CRITICAL | /admin/system plus module-specific control rooms | Owner-level admins | disable, pause, require approval, audit |
| Audit Command Center | `audit` | ONLINE | /admin/audit-logs | Owner, audit admins | search, export-ready review, investigate, escalate |

## External Service Readiness

Only environment variable names are listed. Values, tokens, URLs, private keys, and credentials are intentionally excluded.

| Service | State | Configured | Missing Env Names |
| --- | --- | ---: | --- |
| Railway | `missing` | 0/3 | RAILWAY_ENVIRONMENT, RAILWAY_SERVICE_ID, RAILWAY_DEPLOYMENT_ID |
| PostgreSQL | `configured` | 1/1 | none |
| Redis | `missing` | 0/1 | REDIS_URL |
| Cloudflare R2 | `missing` | 0/3 | R2_BUCKET_NAME, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY |
| Stripe | `missing` | 0/2 | STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET |
| Brevo | `missing` | 0/1 | BREVO_API_KEY |
| Firebase / FCM | `missing` | 0/3 | FCM_PROJECT_ID, FCM_CLIENT_EMAIL, FCM_PRIVATE_KEY |
| Apple APNs | `missing` | 0/4 | APNS_BUNDLE_ID, APNS_KEY_ID, APNS_TEAM_ID, APNS_PRIVATE_KEY |
| Expo / EAS | `missing` | 0/1 | EXPO_ACCESS_TOKEN |
| LiveKit | `missing` | 0/3 | LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL |
| Mux | `missing` | 0/2 | MUX_TOKEN_ID, MUX_TOKEN_SECRET |
| App Store Connect | `missing` | 0/2 | APP_STORE_CONNECT_KEY_ID, APP_STORE_CONNECT_ISSUER_ID |
| Google Play | `missing` | 0/1 | GOOGLE_PLAY_SERVICE_ACCOUNT_JSON |

## Developer Standard

- feature registry entry
- backend/admin route or intentional hidden status
- server-side role and permission gate
- audit log table or audit event target
- launch critical flag
- risk level
- owner/service mapping
- QA/audit script coverage

## Launch Blockers For New Features

- auth required
- owner/admin scoping
- no credential value exposure
- clear rollback or moderation action where applicable
- mobile and desktop admin usability