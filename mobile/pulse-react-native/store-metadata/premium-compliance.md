# PulseSoc Premium Mobile Payment Compliance Draft

PulseSoc currently uses Stripe-backed Premium and Founder membership flows on the web.

## Store Risk

Apple and Google may require in-app purchase for digital features, subscriptions, creator tools, badges, or premium content consumed in the app.

## Recommended Launch Strategy

- Do not present Stripe checkout, external billing links, or paid digital purchase prompts inside the iOS native production build.
- Hide or disable mobile Premium purchase surfaces in native iOS context until Apple in-app purchase products are implemented and approved.
- Keep entitlement enforcement server-side, but do not expose externally purchased paid digital benefits inside the iOS app until IAP is ready.
- Do not show web billing management for existing subscribers inside the iOS native build unless Apple policy review explicitly allows it.
- Prepare an in-app purchase implementation plan for PulseSoc Premium and Founder-equivalent benefits if required.

## Required Before Production Submission

- Decide whether Premium benefits are digital-only.
- Decide whether Founder Premium can be sold in native apps.
- Prepare Apple subscription products if needed.
- Prepare Google Play subscription products if needed.
- Verify entitlement syncing from store receipts/webhooks.

## App Store Review 1.0 Fix

- iOS native checkout is blocked server-side for `/upgrade`, checkout session APIs, founder activation APIs, creator/marketplace/course checkout APIs, billing portal APIs, and `/pulse/premium`.
- Premium Intelligence, Premium Portfolio, UNDX Premium, and premium-only appearance APIs return an iOS paid-digital unavailable response inside the native iOS app.
- The iOS native build must not be resubmitted with paid digital content available unless matching Apple IAP products are available in the app.
- If Premium is reintroduced in iOS, it must use StoreKit purchase and restore flows with receipt validation before App Review.
