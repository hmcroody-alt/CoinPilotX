# CoinPlotXAI Inc. Scale Readiness Report

## Current Strengths

- Website account is the source of truth for Pro access and Telegram linking.
- Stripe checkout is routed through authenticated website sessions.
- Brevo transactional email infrastructure is centralized enough to support signup, password reset, and upgrade emails.
- Private dashboard, API routes, Telegram account lookup, portfolio, watchlist, alerts, SEO, and PWA systems already exist.
- Admin visibility now includes users, billing records, unmatched payments, email logs, system status, and audit logs.

## Current Bottlenecks

- `bot.py` is still the dominant application file and carries web routes, Telegram handlers, Stripe webhook code, and business logic.
- SQLite is risky for concurrent SaaS workloads and background workers.
- Background jobs are present in concept, but a separate worker process would be more reliable for alert checks, trial emails, and Telegram notifications.
- API provider outages currently fall back honestly, but production monitoring should alert the owner when providers fail.

## PostgreSQL Recommendation

Move production to PostgreSQL before significant paid-user growth. Keep the safe migration style, but use a migration tool or a dedicated migration module so schema changes are auditable and reversible.

## Bot/Web/Worker Split Recommendation

Recommended future Railway services:

- `web`: Flask website, account dashboard, admin dashboard, Stripe webhooks, SEO/PWA routes.
- `bot`: Telegram polling or webhook worker.
- `worker`: alert checks, trial expiration, email sequences, portfolio snapshots, market cache refresh.

## Caching Recommendation

- Cache public market and sports API responses for 45-60 seconds.
- Keep account, dashboard, admin, API, and auth responses uncached.
- Preserve the current service worker rule: static assets only, never private pages or APIs.

## Monitoring Recommendation

- Add external uptime checks for `/health`.
- Add Stripe webhook delivery alerts.
- Add Brevo failed email monitoring.
- Add OpenAI/API failure counters to `/admin/system`.

