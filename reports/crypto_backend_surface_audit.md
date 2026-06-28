# Crypto Backend Surface Audit

## Backend Surface

Primary backend route:

- `/admin/command-center/crypto`

Section routes:

- `/admin/command-center/crypto/alerts`
- `/admin/command-center/crypto/watchlists`
- `/admin/command-center/crypto/market-data`
- `/admin/command-center/crypto/ai-usage`
- `/admin/command-center/crypto/token-scanner`
- `/admin/command-center/crypto/whale-provider`
- `/admin/command-center/crypto/news-provider`
- `/admin/command-center/crypto/portfolio`
- `/admin/command-center/crypto/wallet-safety`
- `/admin/command-center/crypto/audit`

## Backend Modules

- Crypto Alerts Manager
- Watchlist Manager
- Market Data Health
- Crypto AI Usage
- Token Scanner Logs
- Whale Signal Provider
- Crypto News Provider
- Portfolio Intelligence
- Wallet Safety
- Crypto Audit Logs

## Access Control

All backend surfaces use the existing `require_admin_page("command_center.view")` path.

Non-admin users are blocked by redirect/unauthorized/forbidden response.

## Data Exposure

Backend diagnostics use aggregate counts and provider readiness signals. Private crypto user data remains owner-scoped in user-facing APIs.

## Persistence

Additive tables:

- `crypto_alerts`
- `crypto_watchlists`
- `crypto_watchlist_assets`
- `crypto_ai_queries`
- `crypto_recent_assets`
- `crypto_favorite_assets`
- `crypto_audit_logs`

No destructive migration or production identifier rename was introduced.

## Audit Coverage

`scripts/crypto_command_center_audit.py` validates:

- route registration
- backend surface access
- contextual labels
- strict state labels
- no fake `ACTIVE` state
- owner-scoped alert/watchlist writes
- admin route blocking for normal users
- sensitive-data redaction
