# PulseSoc Ad Payments Report

## Wallet Model

- `pulse_ad_wallets` stores available, pending, promotional, bonus, refund, lifetime funded, lifetime spent, and reserved budget cents.
- `pulse_ad_wallet_transactions` stores every money mutation with transaction type, cents, currency, status, idempotency key, and metadata.
- `pulse_ad_wallet_funding_sessions` stores server-side funding state and safe checkout URL.
- `pulse_ad_receipts`, `pulse_ad_invoices`, `pulse_ad_refunds`, and `pulse_ad_billing_events` prepare receipts, statements, refunds, and billing audits.

## Stripe Funding

- Web advertisers create a funding session.
- If billing is enabled and Stripe is configured, the server creates a Stripe Checkout Session.
- Stripe webhook processes only sessions with `metadata.purpose=pulse_ad_wallet_funding`.
- Crediting is idempotent by provider event and funding session.
- Provider references are hashed/redacted in summaries.

## Spend Controls

- Campaign resume reserves available budget.
- Delivery checks require wallet spendability unless the account is an internal promotion.
- Impression tracking posts idempotent spend events.
- Wallets cannot go negative.
- Campaigns auto-pause when wallet funds are insufficient.

## iOS Compliance

- Native iOS funding requests are rejected with a safe business-billing notice.
- Consumer Premium remains separated from advertiser business spend.
