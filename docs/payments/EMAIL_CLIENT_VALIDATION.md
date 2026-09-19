# Payments Email — Client Validation

**Status: STATIC CHECKS PASS. REAL VISUAL VALIDATION NOT DONE — BLOCKED on owner
mailboxes.**

Sixteen templates render, and every rule that can be asserted from a string is
asserted. Nobody has looked at one of these emails in Outlook. Those are
different claims and this document keeps them apart.

Authority: `services/payments_email_templates.py`,
`tests/marketplace/test_payments_notifications.py::EmailClientSafety`.
Design rules: `TRANSACTIONAL_EMAIL_DESIGN_SYSTEM.md`.

## 1. The templates

| Key | Recipient |
|---|---|
| `seller_application_received` | seller |
| `seller_more_info_required` | seller |
| `seller_approved` | seller |
| `seller_declined` | seller |
| `stripe_verification_required` | seller |
| `stripe_ready` / `card_payments_enabled` | seller |
| `seller_account_restricted` | seller |
| `new_paid_order` | seller |
| `payout_paid` | seller |
| `payout_failed` | seller |
| `dispute_opened` / `dispute_action_required` | seller |
| `payment_succeeded` | buyer |
| `order_shipped` | buyer |
| `refund_completed` | buyer |

Sixteen keys, fourteen distinct renderers — `card_payments_enabled` and
`dispute_action_required` are deliberate aliases, not duplicates.

## 2. What the static checks actually assert

Run on **every** template's rendered output, not a sample:

| Check | Assertion |
|---|---|
| No `<script>`, `<link>`, `@import` | Stripped or blocked everywhere; a template needing them renders as nothing |
| No `rgba()` | Outlook renders through Word, which drops `rgba()` outright — the element loses its background rather than degrading to something readable |
| Plain-text alternative exists | And contains no `<` at all, which catches markup leaking into the text part |
| Payments attributed to Stripe | Required by the charge model |
| No funds-custody claim | No template may claim PulseSoc never holds money — under separate charges and transfers, it does, between the charge and the transfer |
| Seller security notice | Tells sellers what PulseSoc will never ask them for |

The last two are content assertions, not rendering ones, and they are the two
most likely to be quietly falsified by a well-meaning copy edit.

## 3. What static checks cannot see

This is the reason the status line at the top says what it says.

| Failure | Why no string assertion finds it |
|---|---|
| Outlook/Word table collapse | Valid markup that lays out wrong. The HTML passes every check and the email looks broken |
| Dark-mode inversion | Clients invert what they cannot see declared. A container *with* a declared background can still invert if a nested element lacks one — the check proves declaration, not sufficiency |
| Gmail clipping at ~102KB | A rendered size problem, not a markup problem. Gmail shows "[Message clipped]" and hides the footer |
| Image blocking | Every client blocks by default. Whether the email still makes sense with no images loaded is a judgement, not an assertion |
| Mobile reflow | 320pt-wide rendering of a table layout |
| Link rewriting | Corporate gateways rewrite hrefs; whether the rewritten link still resolves is only observable in the client |
| Spam placement | Unobservable from inside the process that sent it |

A test suite that is green against all seven of these is a test suite that is
not looking at them.

## 4. What is needed to close this

Owner-supplied, not code:

| Needed | Why |
|---|---|
| A real inbox in **Outlook on Windows** | The Word rendering engine. No other client shares its failure modes, and it is the one most likely to break |
| A real **Gmail** inbox, web and Android | Style-block stripping and the clipping threshold |
| **Apple Mail**, macOS and iOS | The most common client for this audience |
| A **dark-mode** pass in each of the above | Inversion is client-specific |
| A sending path from a non-production environment | So validation mail is not sent from the live domain, and so a template mistake cannot reach a real seller |

The last row matters most. Without a non-production sender, validating these
means sending sixteen real emails from the production identity, which is a worse
outcome than leaving them unvalidated.

A commercial preview service (Litmus, Email on Acid) substitutes for the first
four rows and is the cheaper path. It does not substitute for the fifth.

## 5. The procedure, when the mailboxes exist

1. Render all sixteen with realistic context — real amounts, long seller names,
   a long listing title. The default fixtures are short and hide wrapping bugs.
2. Send one of each to every mailbox in §4.
3. Screenshot each, light and dark, at desktop and 320pt.
4. Record per template and per client: **layout intact / degraded / broken**,
   and whether it still makes sense with images blocked.
5. File anything that is not "intact" against the template key, not against a
   screenshot.
6. Replace the status line at the top of this document with the result.

Step 4's third column is the one that usually fails. An email that renders
perfectly and says nothing useful without its images is a broken email that
passes a rendering review.

## 6. Interaction with the rest of the mission

Blocked, but **not** on the activation critical path in the way the Stripe
credentials are. A card payment can be taken, fulfilled, settled and paid out
with an email that renders badly in Outlook. Nothing is lost and no money moves
wrongly; a seller reads an ugly email.

It is therefore listed in `PRODUCTION_ACTIVATION_READINESS.md` as a *should*
rather than a *must*, and that is a judgement the owner may overrule in either
direction. The one thing that should not happen is it being recorded as done
because the test suite is green.
