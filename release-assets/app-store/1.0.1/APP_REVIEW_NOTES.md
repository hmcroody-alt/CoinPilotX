# App Review Notes — PulseSoc 1.0.1 (17)

Draft for the **App Review Information → Notes** field in App Store Connect.
Every claim below was verified on device or in the codebase this session. Do not paste any
sentence that stops being true.

---

## Paste-ready text

```
Thank you for the review of build 16.

Build 16 was rejected under Guideline 2.1(b) because the app referenced subscriptions
that had not been submitted for review. Both subscription products are now submitted for
review together with this build:

  Monthly — com.pulsesoc.premium.monthly
  Annual  — com.pulsesoc.premium.annual

Both belong to the "PulseSoc Premium" subscription group, and an App Review screenshot
showing both plans and both prices is attached to each product.

WHERE TO FIND THE SUBSCRIPTIONS
Sign in, then open the navigation menu at the top left and choose Premium (under
Commerce). The paywall lists both plans with prices supplied by StoreKit, a Continue
button, and Restore Purchases.

WHAT PREMIUM UNLOCKS
Premium is an account-level entitlement. The purchase is verified server-side and the
entitlement is granted to the signed-in PulseSoc account, so it is available on any
device where that account is signed in. Restore Purchases re-queries StoreKit for
existing transactions.

NOTE ON BUILD 16
In build 16 the paywall could not display plans because the products had not yet been
created in App Store Connect. That is resolved: the products now exist, and the app
displays the prices StoreKit returns rather than any hardcoded value. If StoreKit
returns nothing, the app shows a specific explanation and a Retry control instead of a
purchase button.

Please contact us through the address on record if a demo account or any further
information would help.
```

---

## Notes on the draft — read before pasting

**Verified true.** Both product IDs and the group name match App Store Connect. The
navigation path was walked this session. Prices come from StoreKit — `purchasePremium()`
in `mobile-native/src/payments/appleIapPremium.ts` takes the product ID from the
server-issued payment instruction, and the plan cards render Apple's own `displayPrice`
strings. The distinct failure states (`empty`, `unavailable`, `failed`, `timeout`) each
render their own message with a Retry control, covered by
`src/screens/__tests__/PremiumCenterScreen.planLoading.test.tsx`.

**Deliberately omitted.** No claim about what Premium features do, beyond it being an
account-level entitlement. The Premium screen currently carries BETA labels and
"aren't switched on yet" copy on parts of the offering, so a feature list in the review
notes would not survive contact with the screen itself.

**Needs a decision before submitting.**

1. **Demo account.** The notes offer one on request rather than supplying credentials,
   because no reviewer account has been prepared. If PulseSoc requires sign-in to reach
   the paywall — it does — Apple will normally expect credentials up front. Preparing a
   reviewer account is likely the single highest-value remaining task, and it also solves
   most of the screenshot content problem.

2. **Sandbox purchase is unverified end to end.** A sandbox Apple Account was never signed
   into the capture simulator, so the flow was confirmed as far as StoreKit presenting the
   purchase sheet, not through to a completed purchase and entitlement grant. Both paths
   did reach `StoreKit/Purchase_SK2`, cancellation renders "Purchase cancelled." rather
   than an error, and Restore issued a real `StoreKit/TransactionQuery`. Worth closing
   before submission: sign a sandbox tester in under Settings → App Store → Sandbox
   Account and complete one purchase of each plan.

3. **Subscription level ordering.** Monthly is Level 1 and Annual is Level 2, so StoreKit
   treats Monthly → Annual as a downgrade that defers to the next renewal date. This is
   not a rejection risk, but it is cheap to fix now with no subscribers and expensive
   later.
