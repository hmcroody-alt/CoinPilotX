# PulseSoc Command Center Railway Deployment

Generated: 2026-06-19T20:37:15Z

## Scope

This report documents the Railway deployment and connection verification for Service 2 Command Center Worker. No product features were added in this task. The work was limited to Railway service setup, environment wiring, protected endpoint checks, Main App dispatch verification, and production smoke checks.

## Railway Services

| Item | Result |
| --- | --- |
| Worker service name | PulseSoc Command Center Worker |
| Main App service | CoinPilotX |
| Worker source | GitHub repository deployment from the current PulseSoc app repository |
| Worker public exposure | Not exposed publicly in Railway |
| Private networking | Used for Main App to Worker dispatch |
| Worker deployment status | Online |
| Main App deployment status | Online |

## Worker Runtime Configuration

| Variable | Configured |
| --- | --- |
| PULSESOC_SERVICE_NAME | Yes |
| PULSESOC_SERVICE_ROLE | Yes |
| COMMAND_CENTER_WORKER_ENABLED | Yes |
| COMMAND_CENTER_INTERNAL_TOKEN | Yes, secret value not recorded |
| DATABASE_URL | Yes, existing production PostgreSQL reference used |
| PULSE_AI_ENABLED | Yes, false |

Worker start command:

```text
gunicorn services.command_center_worker.app:app --bind 0.0.0.0:$PORT
```

## Main App Runtime Configuration

| Variable | Configured |
| --- | --- |
| COMMAND_CENTER_ENABLED | Yes |
| COMMAND_CENTER_INTERNAL_URL | Yes, private Railway hostname with runtime port |
| COMMAND_CENTER_INTERNAL_TOKEN | Yes, same secret as worker; value not recorded |

During verification, the first Main App private URL value resolved to a Railway private hostname but no port. Main App dispatch failed with `ConnectionError`. The Worker runtime port was verified as `8080`, so the Main App private URL was updated to include port `8080`. After redeploy, the private URL resolved with host and port, DNS succeeded, and the Worker health endpoint returned HTTP 200 from the Main App container.

## Protected Endpoint Verification

Verified from the Worker Railway console using the runtime token from the environment. The token was not printed or copied into this report.

| Endpoint | Missing token | Valid token |
| --- | ---: | ---: |
| GET /internal/command-center/health | Public health | 200 |
| POST /internal/command-center/events/test | 401 | 200 |
| POST /internal/command-center/presence/update | 401 | 200 |
| POST /internal/command-center/messages/event | 401 | 200 |
| POST /internal/command-center/notifications/event | 401 | 200 |
| POST /internal/command-center/security/event | 401 | 200 |
| POST /internal/command-center/ai/summary | 401 | 200 |

## Main App Dispatch Verification

Verified from the Main App Railway console with sanitized smoke payloads and high-numbered non-user test identifiers.

| Dispatch path | Result |
| --- | --- |
| Worker private DNS from Main App | OK |
| Worker health from Main App | HTTP 200 |
| Presence event dispatch | Sent, HTTP 200 |
| Message event dispatch | Sent, HTTP 200 |
| Notification event dispatch | Sent, HTTP 200 |
| Security event dispatch | Sent, HTTP 200 |
| AI summary request | Safe disabled response; AI remains disabled |

Main App service status from `/api/service/health`:

| Field | Result |
| --- | --- |
| service_name | main-app |
| service_role | web |
| command_center_enabled | true |
| database_ok | true |
| commit | d9fe251dc5233b31580f74473bf4f87e59962d58 |

## Production Smoke Checks

Unauthenticated production HTTP smoke checks confirmed the Main App serves expected protected-route redirects instead of crashing.

| Route | Result |
| --- | --- |
| / | HTTP 200 |
| /pulse | HTTP 200 via /login?next=/pulse |
| /pulse/messages | HTTP 200 via /login?next=/pulse/messages |
| /pulse/reels | HTTP 200 via /login?next=/pulse/reels |
| /pulse/videos | HTTP 200 via /login?next=/pulse/videos |
| /pulse/status | HTTP 200 via /login?next=/pulse/status |
| /pulse/profile | HTTP 200 via /login?next=/pulse/profile |
| /admin/security | HTTP 200 via /admin/login |
| /admin/system | HTTP 200 via /admin/login |

## Security Notes

- No secrets, tokens, database URLs, or private Railway hostnames are recorded in this repository.
- Internal Worker endpoints require `X-Command-Center-Token` except the health endpoint.
- Main App dispatch uses Railway private networking and a short client timeout.
- `PULSE_AI_ENABLED=false`; AI endpoints return safe disabled responses and do not call an external AI provider.
- The Worker is not publicly exposed through a Railway public domain.

## Remaining Notes

- The initial dispatch blocker was configuration-related: Main App had a private Worker hostname without a port. It was corrected in Railway configuration and verified after redeploy.
- No code changes were required for this deployment task.
- Authenticated browser QA for signed-in user surfaces was not repeated in this final report pass; protected route smoke checks verified that production routes serve correctly and redirect as expected for unauthenticated sessions.
