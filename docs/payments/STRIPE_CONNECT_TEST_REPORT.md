# Stripe Connect — Test Report

Date: 2026-09-17
Commit range: `47428c43` … `78d027fd`

This report records what was **executed**, with results. Where something was not
executed, it says so and why. No test result here is inferred from the existence
of code.

---

## 1. Summary

| Layer | Result |
| --- | --- |
| Marketplace unit + integration suites (32 files) | **all green** except 3 pre-existing failures unrelated to this work |
| Webhook wiring guards (source-level) | 19 passed |
| Post-settlement finance (behaviour) | 15 passed |
| Adjacent payout/ledger suites (4 files) | 59 passed |
| `bot.py` import + route registration | clean, 2118 routes |
| `scripts/stripe_webhook_recovery_audit.py` | 0 failures, 0 warnings |
| Mutation verification of new wiring guards | 2/2 mutations detected |
| **Test-mode E2E against Stripe** | **NOT RUN — blocked, no test key exists** |
| **Physical iPhone 16 Pro E2E** | **NOT RUN — blocked on the same** |

## 2. How these suites must be run

`tests/marketplace/` files **cannot share a pytest process.** Each sets its own
temporary `DATABASE_URL` at import, so batching two produces spurious failures
reading `no such table: marketplace_listings` in another file's `setUp`. Every
run below is one file per process:

```
for f in tests/marketplace/*.py; do .venv/bin/python -m pytest "$f" -q; done
```

The `.venv` is mandatory — system `python3` lacks `requests` and `pytest` and
fakes a suite failure.

## 3. Full marketplace suite (one file per process)

| File | Result |
| --- | --- |
| test_admin_review_batch_route.py | 38 passed |
| test_admin_review_detail_page.py | 15 passed |
| test_admin_review_queue_page.py | 43 passed |
| test_cash_settlement.py | 7 passed |
| test_commercial_operations.py | 6 passed |
| test_goods_policy.py | 4 passed |
| test_listing_rereview_reset.py | 10 passed |
| test_marketplace_approval_visibility.py | 20 passed |
| test_marketplace_moderation_reachability.py | 7 passed |
| test_payment_error_classification.py | 7 passed |
| **test_post_settlement_finance.py** | **15 passed** |
| test_product_media_attach.py | 12 passed |
| test_quote_authority.py | 5 passed |
| test_reservation_lifecycle.py | 33 passed |
| test_reservation_schema_bootstrap.py | 26 passed |
| test_reservation_settlement.py | 39 passed |
| test_reservation_sweep_schema_contract.py | 4 passed |
| test_reservation_sweep_worker_wiring.py | 54 passed |
| test_reservation_sweeper.py | 44 passed |
| **test_reservation_webhook_wiring.py** | **19 passed** |
| test_review_mutation_guards.py | 20 passed |
| test_review_pipeline_end_to_end.py | 11 passed |
| test_seller_listing_batch_category_route.py | 36 passed |
| test_seller_listing_batch_price_route.py | 26 passed |
| test_seller_listing_batch_route.py | 20 passed |
| test_seller_listing_edit.py | 47 passed |
| test_seller_listing_readiness_route.py | **3 failed**, 18 passed |
| test_seller_review_verdict.py | 28 passed |
| test_supplier_checkout_gate.py | 59 passed |
| test_supplier_checkout_wiring.py | 20 passed |
| test_supplier_ledger_authority.py | 28 passed |
| test_supplier_variants.py | 57 passed |

### The 3 failures

`test_seller_listing_readiness_route.py` — pre-existing, from another session's
in-flight `resubmittable` work on the same shared checkout. Not touched by this
mission and not caused by it. Confirmed by the fact that no file changed here
imports or affects that route.

## 4. Adjacent suites

| File | Result |
| --- | --- |
| tests/business_os_finance/test_seller_payout_routes.py | 13 passed |
| tests/test_seller_money_read.py | 25 passed |
| tests/test_stripe_webhook_verification.py | 21 passed |
| tests/protection/test_environment_contract.py | 9 passed, **1 failed** |

The environment-contract failure is `APNS_ALLOWED_BUNDLE_IDS`, read by
`services/pulsesoc_voip_push.py` (another session's VoIP work) and not declared
in `.env.example`. This work added **zero** new `os.getenv` calls — verified by
`git diff … | grep -c "os.getenv"` returning 0 across every file changed.

## 5. What the new behaviour tests actually prove

`tests/marketplace/test_post_settlement_finance.py`, 15 tests. The ones added by
this mission and the specific defect each guards:

| Test | Defect it prevents |
| --- | --- |
| `test_a_chargeback_freezes_the_payout_before_it_can_be_transferred` | a disputed settlement reaching `eligible` → `scheduled` → `paid` while Stripe takes the money back |
| `test_a_won_dispute_releases_to_the_state_the_hold_interrupted` | releasing to `pending_fulfillment`, which needs a second delivery confirmation that `mark_delivered` dedupes away — money stranded permanently |
| `test_a_lost_dispute_reverses_the_seller_ledger_rather_than_releasing_it` | paying out money the platform no longer has |
| `test_a_dispute_on_a_cart_charge_freezes_every_seller_on_it` | a handler stopping at the first row, leaving the rest of a multi-seller cart transferable |
| `test_a_seller_who_onboards_after_selling_stops_being_unpayable` | `pending_onboarding` settlements never revisited — permanently frozen while the ledger looks healthy |
| `test_onboarding_reconciliation_does_not_lift_a_hold` | conflating "finished onboarding" with "no longer blocked" |
| `test_a_fraud_warning_freezes_the_payout_while_the_money_is_still_recoverable` | missing the only signal that arrives before the funds leave |
| `test_the_webhook_actually_delivers_a_fraud_warning_to_the_hold` | a correct handler that nothing calls |
| `test_a_deauthorized_connect_account_stops_being_a_transfer_destination` | routing transfers at an account the seller has revoked |

### Fixture design

`_dispute()` and `_early_fraud_warning()` **deliberately carry no seller
metadata**, because Stripe does not copy a Charge's metadata onto a Dispute or a
fraud warning. A fixture that supplied it would prove nothing — that absence is
precisely what made the original handler inert.

`test_a_deauthorized_connect_account_…` gives `data.object` a *different* id
(`ca_pulsesoc_platform_app`) from the account in `event["account"]`. A handler
reading `data.object["id"]` therefore updates zero rows and the test fails.

### Positive controls

Two tests assert a negative ("not eligible"). Both establish that the assertion
is meaningful by running an identical, undisturbed twin settlement through
`evaluate_eligibility` first and confirming it *is* eligible. The twin is
necessary because `evaluate_eligibility` **transitions** rather than merely
reporting — running it on the subject would move it.

## 6. Mutation verification

Source-level wiring guards can pass vacuously. The two new end-to-end webhook
tests were verified by mutating the dispatch and confirming failure:

| Mutation | Result |
| --- | --- |
| `if event_type == "radar.early_fraud_warning.created"` → `"MUTANT.never.matches"` | `test_the_webhook_actually_delivers_a_fraud_warning_to_the_hold` **FAILED** ✓ |
| `event.get("account")` → `event["data"]["object"]["id"]` | `test_a_deauthorized_connect_account_…` **FAILED** ✓ |

`bot.py` was restored and verified byte-identical afterwards by SHA-256
(`9963203c…b7fe` before and after).

All new source-literal guards were additionally checked for vacuity with
`git show HEAD:<file> | grep -c <token>`, each returning **0** — the token did
not exist before the commit, so the guard cannot have been passing already.

## 7. Webhook recovery audit

```
STRIPE WEBHOOK RECOVERY AUDIT
routes checked: 5
events checked: 18
local signed fixture status: 200
invalid signature fixture status: 400
warnings: 0
failures: 0
```

Prints no key material. The audit previously reported one failure — a missing
`stripe.Webhook.construct_event` token — which was **false**: verification moved
into `services/stripe_webhook_verification.py` and the audit only grepped
`bot.py`. Fixed, because a false failure in the one webhook health summary a
human reads is worse than no check at all.

## 8. What was NOT tested, and why

| Not run | Reason |
| --- | --- |
| Live or test-mode charge against Stripe | **No test key exists.** The only key in the environment is live, and no unauthorized live money movement is permitted |
| Connect Express onboarding end to end | same |
| `stripe.Transfer.create` / `stripe.Payout.create` | same, plus `run_once` has no production caller |
| Physical iPhone 16 Pro checkout E2E | requires a working test-mode checkout to be meaningful |
| Dispute simulation via Stripe's test cards | requires test mode |
| Postgres dialect verification | the local Postgres VM does not boot; SQLite tests structurally cannot see text-vs-integer and reserved-word crashes |

The first five unblock together the moment a `sk_test_…` key is provisioned.
Nothing in this mission's code depends on that key to be *correct* — the
settlement machine, the allocator and the webhook handlers are exercised against
locally-signed fixtures and a real database — but none of it has been proved
against Stripe's own behaviour.
