# Crypto Command Center Security and Privacy Review

## Security Model

Crypto user data is owner-scoped. Alerts, watchlists, favorites, recent assets, and AI query metadata require authenticated user context.

Admin diagnostics are role-gated behind the existing admin page permission flow.

## Protected Data

The implementation does not expose:

- private keys
- seed phrases
- raw wallet secrets
- provider API keys
- database URLs
- raw push tokens
- Stripe customer IDs
- Stripe subscription IDs
- full private AI prompt bodies in admin diagnostics

## Validation

Input validation is applied to:

- asset symbols
- alert condition types
- alert target values
- alert status changes
- watchlist names
- token scanner contract string length

Crypto symbol validation accepts only normalized alphanumeric symbols such as `BTC`, `ETH`, and `SOL`.

## Owner Scoping

Alerts and watchlist assets are modified with `WHERE user_id=?` filters. The audit script verifies another authenticated user cannot delete another user’s alert or watchlist asset.

## Market Data Boundary

Market data uses configured public market providers where available. When unavailable, PulseSoc displays truthful fallback text instead of fake prices.

## AI Safety Boundary

Crypto AI returns a safe disabled/partial response when `PULSE_AI_ENABLED=false`. Responses include an educational-only disclaimer and do not promise returns or provide guaranteed buy/sell advice.

## Admin Boundary

The backend Crypto Command Center exposes aggregate diagnostics and provider readiness only. It does not expose user wallet secrets, raw provider tokens, or cross-user portfolio details.

## Remaining Risks

- Live whale/news/calendar providers are not connected yet and remain `PARTIAL`.
- Token scanner is `BETA` until full contract, liquidity, holder, and source-provider integrations are connected.
- Portfolio and wallet intelligence should remain `PARTIAL` until a secure wallet/portfolio provider is fully integrated.
