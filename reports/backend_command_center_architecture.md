# Backend Command Center Architecture

PulseSoc now uses `services/backend_management_registry.py` as the permanent backend-management source of truth.

## Architecture

- The registry defines feature key, display name, category, route, role, permission, status, owner, backend service, audit log table, risk, launch-critical state, and backend manageability.
- `/admin/command-center` renders role-filtered management modules and department rooms.
- `/admin/command-center/<module>` renders feature-level management inventory.
- `/admin/launch-readiness` shows launch readiness and gaps.
- `/api/admin/backend-management/registry` exposes safe admin-only JSON for diagnostics.
- Registry rows are synced additively into `backend_feature_registry` when the command center loads.
- Audit events can be stored in `backend_management_audit_events` without changing existing feature ownership.

## Modules

| Module | Key | Total | Active | Manageable | Readiness | Risk |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Account Command Center | `account` | 10 | 9 | 10 | 95% | high |
| Network Command Center | `network` | 4 | 3 | 4 | 88% | medium |
| Creator Command Center | `creator` | 5 | 4 | 5 | 90% | critical |
| Moderation / Safety Command Center | `moderation` | 3 | 3 | 3 | 100% | low |
| Ads Command Center | `ads` | 3 | 3 | 3 | 100% | low |
| Economy Command Center | `economy` | 3 | 2 | 3 | 83% | high |
| Media Command Center | `media` | 3 | 2 | 3 | 83% | critical |
| AI Command Center | `ai` | 2 | 1 | 2 | 75% | critical |
| System Command Center | `system` | 3 | 3 | 3 | 100% | low |

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
- no secret exposure
- clear rollback or moderation action where applicable
- mobile and desktop admin usability