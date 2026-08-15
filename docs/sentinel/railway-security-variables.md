# Sentinel Railway Security Variables

Production variables are service-scoped. Never copy a production credential to
staging; use a staging-specific credential or leave the provider disabled.
Values are intentionally not recorded here.

| Variable | Service | Secret | Required now | Purpose |
| --- | --- | ---: | ---: | --- |
| `SENTINEL_EXTERNAL_INTEL_ENABLED` | `python alert_worker.py` | No | No | Master gate; default OFF. |
| `SENTINEL_KEV_ENABLED`, `SENTINEL_OSV_ENABLED`, `SENTINEL_NVD_ENABLED` | `python alert_worker.py` | No | No | Per-provider public-source gates. |
| `SENTINEL_GITHUB_SECURITY_ENABLED`, `SENTINEL_CLOUDFLARE_INTEL_ENABLED` | `python alert_worker.py` | No | No | Read-only provider gates. |
| `SENTINEL_SENTRY_ENABLED`, `SENTINEL_STRIPE_ENABLED` | owning backend service | No | No | Evidence-consumption gates. |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ZONE_ID` | `python alert_worker.py` | No | No | Narrow Cloudflare scope. |
| `CLOUDFLARE_API_TOKEN` | `python alert_worker.py` | Yes | No | Dedicated read-only Sentinel token; rotate at 90 days. |
| `SENTRY_AUTH_TOKEN` | web / worker if polling is enabled | Yes | No | Least-privilege Sentry API token. |
| `SENTRY_ORG`, `SENTRY_PROJECT` | worker | No | No | Sentry read scope. |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | `CoinPilotX` web service | Yes | Existing | Server-only payment API and verified webhook handling. |
| `SENTINEL_GITHUB_APP_TOKEN` or `SENTINEL_GITHUB_FINE_GRAINED_TOKEN` | `python alert_worker.py` | Yes | No | Read-only GitHub security alerts; prefer a GitHub App. |
| `GITHUB_REPOSITORY` | `python alert_worker.py` | No | No | Exact `owner/repository` scope. |
| `NVD_API_KEY` | `python alert_worker.py` | Yes | Optional | Raises NVD quota; keyless mode is rate-limited. |
| `OSV_API_BASE_URL`, `NVD_API_BASE_URL`, `CISA_KEV_FEED_URL`, `GITHUB_API_BASE_URL` | `python alert_worker.py` | No | No | Optional official HTTPS endpoint overrides. |

The web service owns Stripe webhook verification; the alert worker owns
bounded scheduled security ingestion. No public-prefixed (`VITE_`,
`NEXT_PUBLIC_`, `EXPO_PUBLIC_`, or `REACT_APP_`) variable may contain any
credential above. Do not promote vendor secrets to project-shared variables.
