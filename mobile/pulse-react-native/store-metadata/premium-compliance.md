# Pulse Premium Mobile Payment Compliance Draft

Pulse currently uses Stripe-backed Premium and Founder membership flows on the web.

## Store Risk

Apple and Google may require in-app purchase for digital features, subscriptions, creator tools, badges, or premium content consumed in the app.

## Recommended Launch Strategy

- Keep mobile Premium status visible.
- Do not present Stripe checkout as the primary in-app purchase path in production store builds until Apple/Google rules are reviewed.
- Use web billing management for existing subscribers only if policy allows.
- Prepare an in-app purchase implementation plan for Pulse Premium and Founder-equivalent benefits if required.

## Required Before Production Submission

- Decide whether Premium benefits are digital-only.
- Decide whether Founder Premium can be sold in native apps.
- Prepare Apple subscription products if needed.
- Prepare Google Play subscription products if needed.
- Verify entitlement syncing from store receipts/webhooks.
