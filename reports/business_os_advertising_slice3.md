# Business OS — Advertising Vertical, Slice 3 (Campaign Review Lifecycle)

Status: **PASS** (implemented, tested, byte-compiled; commits owner-side only —
sandbox `.git` is read-only). Flag-gated and dark until `BUSINESS_OS_ADVERTISING`
is enabled.

## 1. Scope delivered

Extend the canonical advertising service with the minimum lifecycle needed to move
a campaign beyond `draft` — a real review workflow — while stopping strictly short
of funding and delivery.

Advertiser can, through the canonical API: submit an owned, valid draft for review;
withdraw an owned submitted campaign back to draft; reopen an owned rejected
campaign for revision; and see the review status plus any rejection reason on their
own campaign.

Admin can, through the canonical admin API: list campaigns awaiting review (existing
`status=submitted` filter); approve a submitted campaign; reject a submitted
campaign with a required reason. Every decision is written to the existing
administrative audit trail (`log_admin_audit`).

Explicit lifecycle: `draft → submitted → approved | rejected → archived`, with
withdrawal (`submitted → draft`), reopen (`rejected → draft`), and archive/restore
preserved from slice 1. Clients never set `status` directly — they call verbs, and
the server maps each verb to a fixed target state.

**`approved` means review-approved only.** It funds nothing, activates no delivery,
consumes no quota, and moves no money. Advertiser eligibility, campaign review
approval, funding readiness, and delivery activation remain four separate concerns.

Explicitly **out** (not started): wallet funding, spend deduction, delivery
auctions, impressions, reporting, advanced targeting/creative/billing, Marketplace,
mobile-cache work. No balance/billing/spend/impression or legacy ad table is touched.

## 2. Files

| File | Role |
|---|---|
| `services/business_os/advertising/service.py` | Extended: new lifecycle vocabulary + `ALLOWED_TRANSITIONS`; shared `_apply_transition`; `submit_campaign`, `withdraw_campaign`, `reopen_campaign`, `admin_review_campaign`; `transition_campaign` restricted to archive/restore only. |
| `services/business_os/advertising/schema.py` | Cross-engine idempotent column helpers; adds nullable `review_reason` to `business_os_ad_campaigns`. |
| `services/business_os/advertising/api.py` | New handlers: `submit`, `withdraw`, `reopen` (advertiser); `admin_review` (admin). |
| `bot.py` | Three flag-gated advertiser routes (submit/withdraw/reopen) + one admin review route. No legacy route changed. |
| `migrations/business_os/0005_advertising_review.sql` / `.down.sql` | Additive `review_reason TEXT` column (up) and its rollback (down). |
| `tests/business_os/test_advertising_slice3_api.py` | 13-test controller/service decision matrix (in-process). |
| `tests/business_os/test_advertising_slice3_routes.py` | 6-test structural check of the new bot.py route wiring via `ast`. |

Legacy `services/pulse_ads_service.py` and `/api/pulse/ads/...` remain untouched.
The canonical surface stays a **separate namespace** (`/api/business-os/advertising/…`,
`/admin/business-os/advertising/…`).

## 3. Design decisions

**One additive column, no reviewer identity leak.** Review *state* reuses the
existing `status` column. The only new persisted field is a nullable
`review_reason TEXT` so a rejection reason can be surfaced to the campaign owner.
The reviewing admin's identity is deliberately **not** stored on the campaign row
(owners read the full row via `get_campaign`); it is captured in the admin audit
trail instead. `review_reason` is cleared on every return to `draft` (withdraw,
reopen, restore) so a stale reason never lingers on an editable draft.

**Verbs, never raw status.** The generic `transition_campaign` is restricted to
`ARCHIVE_RESTORE_STATES = {draft, archived}`; `submitted`/`approved`/`rejected`
are only reachable through the gated functions (`submit_campaign`,
`admin_review_campaign`) that enforce eligibility, validation, and authority. This
makes it structurally impossible for a client to reach a review state by supplying
a status.

**Server-side transition table.** `_apply_transition` validates every move against
`ALLOWED_TRANSITIONS` (illegal → 409 `illegal_transition`) inside the open
transaction, builds the UPDATE from fixed-literal column keys, and writes the audit
record. Each entry point passes a distinct audit action label
(`campaign_submit`, `campaign_withdraw`, `campaign_reopen`, `campaign_review`),
while `transition_campaign` keeps its slice-1 label `campaign_transition` to avoid
regressing the slice-1 audit test.

**Eligibility precedence preserved.** `submit_campaign` runs the same three-input
eligibility composition as create (rollout flag → account hold → advertiser
approval). A held account or an unapproved/suspended advertiser cannot submit even
if they own a valid draft (403 `ineligible`). Admin review does not re-check
advertiser eligibility — it acts on the campaign state — but is owner-guarded at the
route.

## 4. Validation matrix (observed, not asserted-by-claim)

`python3 tests/business_os/test_advertising_slice3_api.py` → **13/13 PASS**

| Test | What it proves |
|---|---|
| `test_flag_off_dark` | Every new handler returns 404 when the flag is off. |
| `test_submit_valid` | Owner submits a valid owned draft → `submitted`. |
| `test_invalid_draft_cannot_submit` | Draft failing field validation → 400 structured error, stays draft. |
| `test_pending_or_suspended_cannot_submit` | Unapproved/suspended advertiser → 403 ineligible. |
| `test_nonowner_cannot_submit_or_withdraw` | Non-owner → 404 (existence not leaked). |
| `test_submitted_not_editable` | A submitted campaign rejects draft edits (409). |
| `test_withdraw` | Owner withdraws submitted → `draft`, reason cleared. |
| `test_admin_approve` | Admin approves submitted → `approved`. |
| `test_admin_reject_with_reason` | Reject requires reason → `rejected` + reason surfaced. |
| `test_reopen_rejected` | Owner reopens rejected → `draft`, reason cleared. |
| `test_illegal_transitions_rejected` | e.g. approve a draft, withdraw an approved → 409 illegal_transition. |
| `test_approved_has_no_spend_or_delivery` | Approve touches no balance/spend/delivery field. |
| `test_audit_records_written` | submit/withdraw/approve/reject/reopen each emit an audit row (actor, before, after, reason). |

`python3 tests/business_os/test_advertising_slice3_routes.py` → **6/6 PASS**
(bot.py parses; advertiser routes wire flag+auth+write-CSRF+session-derived owner;
admin review route wires owner-guard+flag+CSRF+`log_admin_audit`; audit fields
present; canonical namespace + legacy route intact; all delegate to the controller.)

### Regression (all observed green this run)

| Suite | Result |
|---|---|
| `test_advertising_slice1` | 11/11 |
| `test_advertising_slice2_api` | 8/8 |
| `test_advertising_slice2_routes` | 6/6 |
| `test_advertising_slice3_api` | 13/13 |
| `test_advertising_slice3_routes` | 6/6 |
| `test_entitlements` | 26/26 |
| `test_entitlement_account_hold` | 11/11 |
| `test_entitlement_effective_access` | 11/11 |
| `test_entitlement_identity_effects` | 11/11 |
| `test_premium_visibility_effective_override` | 6/6 |
| `test_ledger_and_webhook_inbox` | 6/6 |
| `test_stripe_ledger_handler` | 7/7 |

Byte-compile: `bot.py`, `advertising/schema.py`, `advertising/service.py`,
`advertising/api.py`, `entitlements/facade.py` → all OK.

## 5. Owner-side staging guide

The sandbox `.git` is read-only, so these steps are for the owner to run locally.

1. **Review the diff.** Changes are isolated to the advertising module, four new
   bot.py route functions, the two 0005 migration files, and two new test files.
   No legacy `pulse_ads` code, no payments/entitlement code, no schema outside
   `business_os_ad_campaigns` is touched.

2. **Apply the migration.** `migrations/business_os/0005_advertising_review.sql`
   adds a single nullable `review_reason TEXT` column. It is additive and idempotent:
   on PostgreSQL `services.db` rewrites `ADD COLUMN` to `ADD COLUMN IF NOT EXISTS`;
   in dev/SQLite the same column is added idempotently by `schema.ensure_schema()`
   via PRAGMA introspection. Rollback: `0005_advertising_review.down.sql`.

3. **Keep the flag OFF in production initially.** With `BUSINESS_OS_ADVERTISING`
   unset/false the entire canonical surface (including the new routes) returns 404 —
   verified by `test_flag_off_dark`. Ship dark, then enable per environment.

4. **Smoke-test with the flag on (staging):**
   create draft → `POST …/campaigns/<id>/submit` → confirm `submitted`;
   admin `POST /admin/business-os/advertising/campaigns/<id>/review` with
   `decision=approve` → confirm `approved`; repeat with `decision=reject&reason=…`
   → confirm `rejected` and that the reason appears on the owner's `GET …/campaigns/<id>`.
   Confirm each decision produced a `log_admin_audit` row.

5. **Run the suites** in section 4 locally before merging; they need no pytest
   (`python3 tests/business_os/<name>.py`).

## 6. Completion boundary

Stops exactly at the spec boundary: the canonical workflow
**advertiser draft → submission → admin review → approved/rejected** works
end-to-end. No funding, spend, delivery, targeting, reporting, Marketplace, or
mobile-cache work was started. `approved` remains review-approved only.
