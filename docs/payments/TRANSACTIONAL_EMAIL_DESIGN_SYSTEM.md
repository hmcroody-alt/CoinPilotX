# Payments — Transactional Email Design System

`services/payments_email_templates.py`. Sixteen templates, one renderer, no
dependencies beyond `html`, `typing` and `services.app_links`.

```python
render(key, context) -> {"subject": str, "html": str, "text": str}
```

Every constraint below exists because an email client breaks without it, not
because of taste. Email HTML is not web HTML and the differences are not
stylistic.

## 1. Why these rules

| Rule | The client that forces it |
|---|---|
| Table layout, not flexbox or grid | Outlook on Windows renders through Word, which has no support for either |
| Inline CSS on every element | Gmail strips `<style>` blocks in several contexts, including forwarded mail |
| Solid hex colours, never `rgba()` | Word drops `rgba()` outright — the element loses its background rather than degrading to something readable |
| Explicit `background-color` on every container | Dark-mode clients invert what they can't see declared, producing black text on black |
| No `<script>`, no `<link>`, no `@import` | Stripped or blocked everywhere; a template that needs them renders as nothing |
| No web fonts | Not loaded by most clients; the fallback stack is what actually renders |
| A plain-text alternative for every template | Spam filters score multipart/alternative higher, and some clients render only text |

`tests/marketplace/test_payments_notifications.py::EmailClientSafety` asserts
the last four against every template's rendered output. The plain-text check
asserts no `<` survives, which catches markup leaking into the text part.

## 2. Brand

Tokens match `branded_email_html`, so a payment email and a platform email look
like the same product.

```
PAGE_BG   #070b14     CARD_BG   #0d1627
ACCENT    #36e58f     HEADING   (light, on dark)
```

Dark surface, green accent. The accent is used for the primary button and for
emphasis inside notices, never for body text.

## 3. Structure

A template returns blocks, which the renderer wraps in the shared shell:

- `_paragraph(html)` — body copy, already escaped by the caller
- `_facts([(label, value), ...])` — the money table
- `_notice(html, tone=...)` — a bordered callout; `tone="action"` is the one
  that means the reader has something to do
- `_button(label, url, primary=True)` — a bulletproof table-based button

### `_facts` drops empty rows

A row whose value is `None` or `""` is not rendered. This is load-bearing.
Stripe does not always supply an arrival date, a payout reference or a masked
destination, and a fact block that printed `Arrives: ` with nothing after it
would be worse than one that does not mention arrival. Callers therefore pass
`""` for absent data rather than a placeholder, and
`bot.stripe_timestamp_date` returns `""` rather than a formatted zero.

## 4. Links

Every path is a module constant, and every constant points at a route that
exists:

```python
SELLER_PAYMENTS_PATH  = "/pulse/merchant/payouts"
SELLER_DASHBOARD_PATH = "/pulse/merchant/dashboard"
SELLER_APPLY_PATH     = "/pulse/merchant/apply"
SELLER_ORDERS_PATH    = "/pulse/seller-store?mode=orders"
BUYER_ORDERS_PATH     = "/pulse/orders"
PAYMENTS_HELP_PATH    = "/pulse/help"
TERMS_PATH            = "/terms"
PRIVACY_PATH          = "/privacy"
```

An earlier draft linked to `/pulse/seller/payments`, `/pulse/seller/dashboard`
and `/pulse/seller/payments/help`. None of the three is a route — every one
would have 404'd a seller arriving from an email about their money.
`test_links_resolve_to_routes_that_exist` now walks every `href` in every
rendered template and fails on any `pulsesoc.com` path not in the known set.

Templates that receive a caller-supplied URL prefer it and fall back to the
constant: `ctx.get("order_url") or SELLER_ORDERS_PATH`.

## 5. Transparency copy

Two rules, both tested.

**Attribution is required.** Payment emails say payments are powered by Stripe.
`test_payments_are_attributed_to_stripe` pins it on the approval email, which
is the first one a seller reads.

**The custody claim is forbidden.** No template says PulseSoc "never touches",
"never holds", "never hold your money" or "does not hold" funds.
`test_no_claim_that_pulsesoc_never_holds_money` checks all four phrasings
against every template.

The reason is architectural, not legal caution. PulseSoc uses **separate
charges and transfers** (`STRIPE_CONNECT_ARCHITECTURE.md` §2): the buyer pays
the platform, and the platform transfers to the seller afterwards. Between
those two steps the money is in PulseSoc's balance. Publishing the opposite
would be a false statement, not an imprecise one. If the charge model is ever
changed to destination charges, that test is the thing to revisit — not the
copy.

## 6. Anti-phishing

The approval email tells the seller what PulseSoc will never ask for.
`test_security_notice_tells_sellers_what_pulsesoc_will_never_ask_for` requires
"card number", "password" or "full card" to appear in it.

A seller who has just been approved is about to receive a run of legitimate
emails about payouts. That is exactly the window a spoofed payout email is
designed for, and this notice is the only defence the seller has that does not
depend on them inspecting headers.

## 7. Adding a template

1. Write the builder and register it in `TEMPLATES`.
2. Add the matching event to `payments_notifications.SPECS` — a template with
   no event is dead copy and a test fails on it, in both directions.
3. Add any new context field to `SAFE_CONTEXT_KEYS`, or it will be stripped and
   render blank. The allowlist test catches this, which is how the twelve
   already-drifted fields were found.
4. Point links at a constant, or add the new route to the known set in
   `test_links_resolve_to_routes_that_exist`.
5. Add the field to `SAMPLE_CONTEXT` in the test file so the render check
   actually exercises it.
