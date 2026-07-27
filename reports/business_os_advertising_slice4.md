# Business OS — Advertising Vertical, Slice 4 (Campaign Funding Readiness & Activation Controls)

Status: **PASS** (implemented, tested, byte-compiled; commits owner-side only —
sandbox `.git` is read-only). Flag-gated and dark until `BUSINESS_OS_ADVERTISING`
is enabled.

## 1. Scope delivered

Give a review-approved campaign the minimum canonical workflow to become
**financially ready for future delivery — without serving a single ad.** Four
concerns stay strictly separate: review approval, funding, activation eligibility,
and live delivery. An approved campaign does **not** auto-fund, auto-activate, or
begin spending.

Advertiser can, through the canonical API: set/update a campaign's budget before
funding; read funding readiness (current funding state + whether it is
activation-ready); reserve funds for an owned, approved campaign; and release
previously reserved funds. Admin can, through the canonical admin API: view a
campaign's funding state with its ledger transaction references, escrow balance,
and append-only operation log; and list funding rows filtered by status (e.g.
`funding_failed`) to inspect failed/inconsistent reservations — all read-only.

**Funding states are their own vocabulary, never mixed with the review lifecycle:**
`unfunded → funding_pending → funded`, with `funding_failed` (insufficient funds or
a failed ledger post) and `released` (reservation returned). Review status stays in
the `status` column; funding status lives on a separate row.

**`activation_ready` is derived, never stored.** It is true only when
`funding_status == funded` **and** `review_status == approved` **and** the campaign
is not archived. It grants no delivery — it is a readiness signal only.

Explicitly **out** (not started): delivery auctions, impressions, click tracking,
analytics/reporting dashboards, advanced targeting, bidding, daily pacing, spend
forecasting, Marketplace, Crypto, mobile-cache work. Approval alone still moves no
money; funding still delivers no ad.

## 2. Files

| File | Role |
|---|---|
| `services/business_os/advertising/schema.py` | Extended: adds two additive tables in `ensure_schema` — `business_os_ad_campaign_funding` (per-campaign funding state) and `business_os_ad_funding_ops` (append-only op log, `UNIQUE(idempotency_key)`) + their indexes. |
| `services/business_os/advertising/funding.py` | **New** funding service. Budget config, funding-readiness validation, reserve/release via the canonical ledger, self-healing multi-step state machine, admin read views. |
| `services/business_os/advertising/api.py` | New handlers: `get_funding`, `set_budget`, `reserve`, `release` (advertiser); `admin_get_funding`, `admin_list_funding` (admin). |
| `bot.py` | Four flag-gated advertiser routes (funding read / budget / reserve / release) + two owner-guarded admin funding routes. No legacy route changed; no new reconcile route. |
| `migrations/business_os/0006_advertising_funding.sql` / `.down.sql` | Additive `CREATE TABLE IF NOT EXISTS` for both funding tables + indexes (up); drops only those two tables (down) — never the ledger or legacy tables. |
| `tests/business_os/test_advertising_slice4_api.py` | 15-test controller/service/ledger decision + integrity matrix (in-process, real SQLite + real ledger). |
| `tests/business_os/test_advertising_slice4_routes.py` | 8-test structural check of the new bot.py funding route wiring via `ast`. |

Legacy `services/pulse_ads_service.py` and `/api/pulse/ads/...` remain untouched.
The canonical surface stays a **separate namespace**
(`/api/business-os/advertising/…`, `/admin/business-os/advertising/…`). The
canonical **ledger** (`ledger_transactions` / `ledger_entries` / `ledger_balances`)
is used through its public `post_entry` API only — never bypassed.

## 3. Design decisions

**Money lives only in the ledger; funding tables hold state, not balances.** No
balance is ever mutated by a bare UPDATE. Every reservation is a double-entry ledger
post `wallet → escrow` (`advertiser:{uid}:wallet` → `ad_campaign_escrow:{cid}`); every
release is the reverse `escrow → wallet`. A campaign's escrow balance is fully
reconstructable from ledger entries (`test_ledger_backed_and_reconstructable`). Both
wallet and escrow accounts are overdraft-guarded (not on the ledger's
`_ALLOW_NEGATIVE_PREFIXES`), so the ledger itself refuses to let available balance go
negative.

**Two-layer idempotency.** (1) The `business_os_ad_funding_ops` table has a DB
`UNIQUE(idempotency_key)` — the app claims the op row before touching the ledger, so
a duplicate key can never start a second reserve. (2) The ledger post uses a
namespaced key `ad_campaign:{reserve|release}:{key}`, so even a re-driven post is a
no-op returning the original transaction. A retry with the same key never
double-reserves (`test_retry_same_key_no_double_reserve`); reusing a key for a
*different* operation/amount/campaign is rejected 409 `idempotency_conflict`
(`test_key_reuse_different_operation_rejected`).

**Self-healing state machine, safe to re-drive.** Reserve runs as: claim op row +
mark `funding_pending` → ledger `post_entry` → finalize `funded` (or `funding_failed`
on `LedgerError`). Each step is its own short transaction, so a crash between steps
leaves a re-runnable state, never a half-funded campaign. Insufficient funds →
`funding_failed` + HTTP 402 `insufficient_funds`; the amount is **never** silently
clamped to zero (`test_insufficient_balance_rejected_atomically`). A failed op leaves
the campaign **unfunded/failed**, not funded (`test_funding_failure_leaves_unfunded`
path within the insufficient-balance test).

**Concurrency cannot overdraw.** Two concurrent reservations that together exceed the
wallet balance resolve to exactly one `200 funded` and one `402`; wallet balance
never goes negative and total escrow equals the single successful reservation
(`test_concurrent_reservations_cannot_overdraw`, driven by `threading.Barrier`). The
guarantee comes from the ledger's atomic `BEGIN IMMEDIATE` overdraft check, not from
app-level locking.

**Release references its reservation and is idempotent.** Release posts
`escrow → wallet` with `provider_reference = reservation_txn_id` and
`metadata.release_of`, restoring the wallet exactly once; a duplicate release is a
no-op returning the released view (`test_release_restores_once_and_is_idempotent`).
Release requires a currently-`funded` campaign (409 `not_funded` otherwise) and is an
**explicit verb** — it is not auto-triggered by archive/withdraw, keeping funding and
review lifecycles independent.

**Validation is server-side and minimal.** Reserve requires: flag on; advertiser
eligible (403 `ineligible`); campaign owned by caller (404 — existence not leaked);
not archived (409); `review_status == approved` (409 `not_approved`); a budget set
(409 `no_budget`); funding amount equals the configured budget (400
`amount_mismatch`); supported currency matching the budget (400). No bidding, pacing,
targeting price, or forecasting was added.

**Admin surface is read-only and cannot fabricate balances.** The two admin routes
only *read* funding state, ledger references, escrow balance, and the op log. There
is **no new reconcile route** — reconciliation stays with the existing protected
mechanism (`test_no_new_reconcile_route`). Admin funding routes gate the flag *after*
the owner guard (→ 409), while advertiser routes are dark (→ 404) when the flag is
off.

## 4. Validation matrix (observed, not asserted-by-claim)

`python3 tests/business_os/test_advertising_slice4_api.py` → **15/15 PASS**

| Test | What it proves |
|---|---|
| `test_flag_off_dark` | Every new handler returns 404 when the flag is off. |
| `test_approved_campaign_can_be_funded` | approved + budget + funded wallet → reserve → `funded`, `activation_ready=true`. |
| `test_non_approved_states_cannot_be_funded` | draft/submitted/rejected/archived cannot fund (409). |
| `test_suspended_advertiser_cannot_fund` | Suspended/held advertiser → 403 ineligible. |
| `test_budget_and_amount_guards` | No budget → 409; amount ≠ budget → 400; unsupported currency → 400. |
| `test_insufficient_balance_rejected_atomically` | Reserve > wallet → 402, campaign `funding_failed`, wallet untouched, escrow zero. |
| `test_retry_same_key_no_double_reserve` | Same key retried → one reservation only; wallet/escrow unchanged. |
| `test_key_reuse_different_operation_rejected` | Key reused for a different op/amount → 409 idempotency_conflict. |
| `test_concurrent_reservations_cannot_overdraw` | Two racing reserves over one wallet → one 200 / one 402; wallet never negative; escrow == one reservation. |
| `test_release_restores_once_and_is_idempotent` | Release returns escrow → wallet exactly once; duplicate release is a no-op. |
| `test_release_requires_funded` | Release on a non-funded campaign → 409 not_funded. |
| `test_approval_alone_no_spend` | Approving a campaign moves no money and sets no escrow. |
| `test_funding_no_delivery` | A funded campaign has no impression/delivery/spend side effect. |
| `test_ledger_backed_and_reconstructable` | Escrow balance recomputed from ledger entries matches the reservation. |
| `test_admin_funding_visibility` | Admin view exposes state, ledger refs, escrow balance, and the op log. |

`python3 tests/business_os/test_advertising_slice4_routes.py` → **8/8 PASS**
(bot.py parses; the funding read route wires flag+auth+session-derived owner; the
three write routes wire flag+auth+write-CSRF+session-derived owner; advertiser routes
are dark 404 when off; admin routes wire owner-guard+flag (409) and are read-only —
no `log_admin_audit`, no new reconcile route; canonical namespace + legacy route
intact; all delegate to the controller.)

### Regression (all observed green this run)

| Suite | Result |
|---|---|
| `test_advertising_slice1` | 11/11 |
| `test_advertising_slice2_api` | 8/8 |
| `test_advertising_slice2_routes` | 6/6 |
| `test_advertising_slice3_api` | 13/13 |
| `test_advertising_slice3_routes` | 6/6 |
| `test_advertising_slice4_api` | 15/15 |
| `test_advertising_slice4_routes` | 8/8 |
| `test_entitlements` | 26/26 |
| `test_entitlement_account_hold` | 11/11 |
| `test_entitlement_effective_access` | 11/11 |
| `test_entitlement_identity_effects` | 11/11 |
| `test_premium_visibility_effective_override` | 6/6 |
| `test_ledger_and_webhook_inbox` | 6/6 |
| `test_stripe_ledger_handler` | 7/7 |

Total: **145/145** across all suites. Byte-compile: `bot.py`,
`advertising/schema.py`, `advertising/funding.py`, `advertising/api.py` → all OK.

## 5. Owner-side staging guide

The sandbox `.git` is read-only, so these steps are for the owner to run locally.

1. **Review the diff.** Changes are isolated to the advertising module (new
   `funding.py`, extended `schema.py` + `api.py`), six new bot.py route functions +
   one payload helper, the two 0006 migration files, and two new test files. No
   legacy `pulse_ads` code, no payments/entitlement code, no ledger-internal code, and
   no schema outside the two new `business_os_ad_*` funding tables is touched.

2. **Apply the migration.** `migrations/business_os/0006_advertising_funding.sql`
   creates the two funding tables and their indexes. It is additive and idempotent
   (`CREATE TABLE IF NOT EXISTS`); the same tables are also created idempotently by
   `schema.ensure_schema()`. Rollback: `0006_advertising_funding.down.sql` drops only
   those two tables — it never touches `ledger_*` or any `pulse_ads` table.

3. **Keep the flag OFF in production initially.** With `BUSINESS_OS_ADVERTISING`
   unset/false the entire canonical surface (including the new funding routes) returns
   404 — verified by `test_flag_off_dark`. Ship dark, then enable per environment.

4. **Smoke-test with the flag on (staging).** Take a campaign through
   draft → submit → admin approve; `POST …/campaigns/<id>/budget` with
   `budget_cents`/`currency`; confirm `GET …/campaigns/<id>/funding` shows `unfunded`
   and `activation_ready=false`. Fund the advertiser wallet via the ledger, then
   `POST …/campaigns/<id>/reserve` (send an `Idempotency-Key` header or
   `idempotency_key` field) → confirm `funded` and `activation_ready=true`; re-POST the
   same key → confirm no second reservation. `POST …/campaigns/<id>/release` → confirm
   `released` and the wallet is restored once. As owner, `GET
   /admin/business-os/advertising/campaigns/<id>/funding` → confirm the ledger refs,
   escrow balance, and op log; `GET /admin/business-os/advertising/funding?funding_status=funding_failed`
   → confirm failed reservations are inspectable. Verify **no** ad is delivered and no
   spend occurs at any point.

5. **Run the suites** in section 4 locally before merging; they need no pytest
   (`python3 tests/business_os/<name>.py`).

## 6. Completion boundary

Stops exactly at the spec boundary: the canonical workflow
**approved campaign → budget configured → funds reserved once → campaign marked
activation-ready** works end-to-end, backed entirely by the canonical ledger, and
**no ad is delivered**. No delivery auction, impression, click tracking, analytics
dashboard, advanced targeting, Marketplace, or Crypto work was started. `funded` and
`activation_ready` are financial-readiness signals only — review approval, funding,
activation eligibility, and live delivery remain four separate concerns.
