# Stripe Tax — Hooks Only

**Status: NOT ENABLED. Nothing in this document was activated.**

The owner's instruction for this mission was explicit: *do not enable Stripe Tax
and do not invent marketplace tax behavior; document the technical hooks only.*
So this is an inventory of where tax *would* attach, and of the decisions that
are owed before any of it may be switched on. It deliberately does not
recommend a tax treatment, because which treatment is correct is not an
engineering question.

## 1. Stripe Tax is absent, and the absence is pinned

There is **no** `automatic_tax`, `tax_behavior`, `tax_code`, `tax_rates` or
`tax_id_collection` anywhere in the production code. The single repository-wide
match is a test that asserts the absence:

```
tests/test_marketplace_cart_lifecycle.py:224
    assert "automatic_tax" not in code
```

That matters more than it looks. The absence is a **guarded** property, not an
accident of never having got round to it. Anyone enabling Stripe Tax will turn
that test red, which is the intended alarm — it should be treated as a decision
point, not as a test to update.

Checkout builds its line items inline with no tax fields at all
(`services/payment_provider.py:244-259`).

## 2. The hooks that already exist

The money plumbing has a tax lane threaded through it end to end. It is fully
wired and permanently carries zero.

| Hook | Location | Today |
|---|---|---|
| Quote input | `services/marketplace_quote_service.py:27` `tax_minor: int = 0` | Always defaulted |
| Quote → policy | `marketplace_quote_service.py:47` `tax_cents=_money(tax_minor, …)` | Passes 0 |
| Quote snapshot | `marketplace_quote_service.py:100` `tax_snapshot` | `{"source": "not_calculated", "amount_minor": 0}` |
| Settlement read | `services/marketplace_settlement_service.py:59` | `tax = int(quote.get("tax_minor", 0))` |
| Settlement column | `marketplace_settlement_service.py:116` `tax_minor INTEGER NOT NULL` | Written as 0 |
| Ledger entry | `marketplace_settlement_service.py:310-316` | **Unreachable** — gated on `if snap["tax_minor"]` |
| Ledger account | `marketplace_settlement_service.py:315` `liability:marketplace_tax` | Declared, never posted to |
| Ledger ref column | `marketplace_settlement_service.py:319` `tax_ledger_ref` | Always null |
| Refund input | `marketplace_settlement_service.py:325-327` `tax_refund_minor` | Always 0 |
| Refund cap | `marketplace_settlement_service.py:350` | Caps tax refunds at the settled tax |
| Refund allocator | `bot.py:57912` `available_tax = max(0, snap["tax_minor"] - prior["tax"])` | Always 0 |

### Why the ledger entry is unreachable

`liability:marketplace_tax` is a real declared account — `tests/marketplace/
test_funds_segregation.py:114` and `:442` pin it into the chart of accounts — but
the entry at `marketplace_settlement_service.py:310` only posts when
`snap["tax_minor"]` is truthy, and nothing ever makes it truthy.

There are exactly **two** production callers of `create_quote`:

- `bot.py:97062` — buy-now
- `services/marketplace_cart_routes.py:809` — cart

**Neither passes `tax_minor` and neither passes `tax=`.** Every quote therefore
carries `tax_minor = 0` and a `tax_snapshot` of `not_calculated`. That single
fact is what makes the whole column of the table above inert, and it is the one
thing to re-check before believing any future claim that tax is flowing.

## 3. Seller tax documents are separately absent

A different question from transaction tax, and also unimplemented:

- `services/seller_money.py:406` returns `"tax_documents": "unsupported"`, pinned
  by `tests/test_seller_money_read.py:467`.
- `mobile-native/src/launch/readiness.ts:280` declares
  `"business:payments.taxDocuments": "COMING_SOON"`.
- `mobile-native/src/api/paymentsHub.ts:213-217` gates the tax-document centre
  behind `taxDocumentsAreLive()`, whose own comment records that no 1099-K or
  equivalent is ever issued.
- `mobile-native/src/screens/BusinessOsPaymentsScreen.tsx:25` says the same in
  its header comment; the gate is applied at `:1181`.

**Caveat on that gate:** it reads `EXPO_PUBLIC_PAYMENTS_TAX_DOCUMENTS` through
`envFlagOn`, and `EXPO_PUBLIC_` flags read that way are **dead in Release
builds** — the computed `process.env` lookup is not inlined by Expo's babel
plugin. So the flag is not a usable production switch. It is off in production
for a reason that is not the reason it appears to be off, and shipping a tax
document centre would require solving that first.

Note also that Stripe Express connected accounts have their own tax-document
behaviour on Stripe's side. Whether that discharges any PulseSoc obligation is
part of §4, not an engineering conclusion.

## 4. What is owed before any of this may be enabled

These are owner/advisor decisions. Engineering cannot answer them and must not
assume them.

1. **Who is the merchant of record per transaction?** PulseSoc currently charges
   the buyer on the platform account and pays the seller by separate transfer,
   which is a *facilitator* shape. Whether that makes PulseSoc liable for
   collecting and remitting is jurisdiction-specific.
2. **Marketplace facilitator obligations.** Many US states place the collection
   duty on the marketplace rather than the seller once thresholds are crossed.
   Which states, and whether PulseSoc is over threshold, is not derivable from
   this codebase.
3. **Nexus.** Where COINPLOTXAI INC has it, and where sellers create it.
4. **Seller reporting.** Whether PulseSoc must issue 1099-K or equivalent, or
   whether Stripe's Express reporting covers it.
5. **Digital vs physical goods**, and cross-border treatment, since listings are
   not restricted to one jurisdiction.
6. **Pricing display** — tax-inclusive or tax-exclusive — which is a product
   decision with legal constraints in some markets.

Until 1–6 are answered, enabling Stripe Tax would produce *a* number, and there
would be no basis for believing it is the right one. A wrong tax number that
settles is worse than no tax number at all, because it creates a liability the
ledger then treats as discharged.

## 5. If it is ever enabled, this is where it attaches

Recorded so the work is scoped, **not** as a recommendation to do it.

1. Compute tax at quote time and pass `tax_minor` (and a real `tax=` snapshot
   naming its source) from **both** `bot.py:97062` and
   `marketplace_cart_routes.py:809`. Missing either one silently taxes half the
   product.
2. The settlement path then needs no change — `marketplace_settlement_service.py:59`
   already reads it and `:310-316` already posts the liability entry. This is the
   part that is genuinely finished.
3. Decide what `liability:marketplace_tax` means operationally: it accrues, but
   nothing remits from it, and nothing reconciles it. A growing liability
   account with no remittance process is a reporting problem, not a solution.
4. The refund path at `marketplace_settlement_service.py:350` and the allocator
   at `bot.py:57912` already handle proportional tax refunds and are already
   capped. Also finished.
5. Update `tests/test_marketplace_cart_lifecycle.py:224`, which is the guard in
   §1 — and only as the *last* step, once 1–4 are real.

## 6. What must not be inferred from this document

- That enabling Stripe Tax is planned. It is not, and no decision has been made.
- That the hooks being present means the feature is nearly done. The plumbing is
  done; the determination of what to compute is entirely absent, and that is the
  hard half.
- That zero tax on every settled order to date is a bug. There have been **zero
  marketplace orders**, so the value has never been exercised either way.
