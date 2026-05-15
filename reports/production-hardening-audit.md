# CoinPilotXAI Production Hardening Audit

Date: 2026-05-15

## Fixed In This Pass

- Replaced homepage market navigation links that pointed to `#market-board` with `/quote`, so users land on the real Live Quote Market Center.
- Added premium trust-first market backgrounds for homepage market/prediction sections and quote pages using `.psych-market-bg`, `.intelligence-glow-bg`, `.quote-cta-glow`, `.soft-data-grid`, and `.trust-gradient-panel`.
- Added `/predictions/crypto` as an original CoinPilotXAI crypto predictions intelligence page with probability cards, source labels, logged-out action routing, external trade disclosure, and safety language.
- Added category/status filtering to `/api/predictions`.
- Added additional educational crypto prediction scenarios for BTC, ETH, and altcoin liquidity.
- Added admin operational visibility routes for `/admin/email-health`, `/admin/system/health`, and `/admin/system/errors`.

## Security Notes

- Private and admin pages remain protected by existing account/admin guards.
- New prediction actions route logged-out users to signup before saving watches, alerts, or simulations.
- New pages avoid guaranteed-profit language and include educational-only disclaimers.
- External trade links use configured redirect values and include `rel="noopener sponsored"` where rendered.

## Billing And Email Checklist

- `STRIPE_SECRET_KEY`: must be set in Railway.
- `STRIPE_WEBHOOK_SECRET`: must match the Stripe dashboard webhook endpoint.
- Webhook endpoint: `https://coinpilotx.app/stripe/webhook`.
- Webhook events: `checkout.session.completed`, `invoice.payment_succeeded`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`.
- Payment pipeline must persist `stripe_events`, `payment_records`, `transactions`, `payment_email_logs`, and user Pro status.
- `BREVO_API_KEY`, `MAIL_FROM_ADDRESS`, and `MAIL_FROM_NAME` must be configured for payment confirmation and Pro activation email delivery.

## SEO Notes

- `/quote` now has canonical metadata and OG metadata.
- `/quote/crypto/<symbol>` has canonical metadata and OG metadata.
- `/predictions/crypto` has canonical metadata, OG metadata, and JSON-LD WebPage schema.
- Homepage CTAs now route live-market intent to `/quote`.

## Still Missing Or Environment Dependent

- Live prediction provider requires `PREDICTIONS_PROVIDER` and `PREDICTIONS_API_KEY`; otherwise sample scenarios are clearly labeled educational.
- SMS delivery requires Twilio variables.
- PWA push requires VAPID variables.
- External prediction/trade redirect can be overridden with `PREDICTIONS_EXTERNAL_TRADE_URL` or `GEMINI_AFFILIATE_URL`.
- Full browser/mobile visual QA still needs a live server and browser access.

## Owner Manual Steps

- Verify Railway has Stripe, Brevo, SMTP, OpenAI, Twilio, VAPID, and optional Telegram variables configured.
- Verify Stripe webhook endpoint URL and enabled events.
- Verify Brevo sender/domain authentication for `noreply@coinpilotx.app`.
- Submit updated sitemap in Google Search Console after deployment.
