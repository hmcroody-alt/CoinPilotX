# App Store Optional Phone Signup Fix

## Apple Finding

App Review reported that account creation required a phone number even though the interface described phone as optional. The review used an iPad Air 11-inch (M3) running iPadOS 26.5 with Build 27.

## Root Cause

The website signup screen exposed an email-or-phone registration choice while PulseSoc's native account confirmation flow is email-based. Although the server accepted an email with an empty phone value, presenting both identifiers during initial registration created an ambiguous path and allowed phone/SMS validation to participate in signup.

## Correction

- Initial website registration now requires email and does not collect phone or SMS consent.
- Server-side website signup ignores injected phone/SMS fields.
- Phone remains optional in Account Settings after registration.
- The native registration API continues to accept requests with the phone field omitted.
- The signup page explicitly explains that a phone number is not required.

## Review Regression Protection

`scripts/app_store_optional_phone_signup_audit.py` verifies:

- Native iOS signup renders without phone or SMS fields.
- Email is the explicit required identifier.
- CSRF remains present.
- Web signup completes without phone.
- Native API signup completes without phone.
- Both flows reach email confirmation.

Existing App Store audits continue to cover UGC reporting/blocking, account deletion, iOS paid-digital isolation, and iPhone-only metadata.
