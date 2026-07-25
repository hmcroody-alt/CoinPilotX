# Business OS — Advertising Vertical, Slice 5 (Controlled Campaign Activation & Scheduling)

Status: **PASS** (implemented, tested, byte-compiled; commits owner-side only —
sandbox `.git` is read-only). Flag-gated and dark until `BUSINESS_OS_ADVERTISING`
is enabled. One pre-existing, out-of-scope mobile-Reels protection test is failing
independently of this slice — see §4.

## 1. Scope delivered

Let a **review-approved, funded** campaign enter a controlled *operational*
lifecycle — **without serving a single ad.** This slice adds the third of four
strictly separate concerns:

1. **Review status** → `campaign.status` (`approved`) — slice 3
2. **Funding status** → `funding_status` (`funded`) — slice 4
3. **Operational status** → `operational_status` — **this slice**
4. **Delivery execution** → **not started anywhere**

Operational states have their own vocabulary, never mixed with review/funding:
`inactive → scheduled → active → paused → completed → cancelled`.

Only these transitions are permitted; every other pair is rejected server-side
(409 `illegal_operational_transition`):

```
inactive  → scheduled | active
scheduled → active | paused | cancelled
active    → paused | cancelled | completed
paused    → active | cancelled
```

Advertiser can, through the canonical API: read operational readiness (the three
states side by side + derived `activation_ready`); schedule, activate, pause,
resume, and cancel an owned, eligible campaign; supply an optional UTC start/end
window. Admin can: read operational state + readiness (review + funding +
operational together); pause/cancel when necessary; and mark a run completed
through the authorized admin path — each admin intervention recorded in the
administrative audit trail.

**Activation is gated.** A campaign may become `scheduled` or `active` only when
review status is `approved`, funding status is `funded`, derived
`activation_ready` is true, the advertiser remains approved, the account is active
and access-enabled, the campaign is not archived, and the budget/currency and any
supplied UTC window are valid. **Approval alone cannot activate; funding alone
cannot activate.** Resume re-runs the full gate, so a suspended advertiser or a
campaign whose funding was released cannot resume.

**`active` means operationally authorized for a FUTURE delivery worker — it is NOT
currently delivering.** No route selects an audience, returns a placement, records
an impression/click, deducts spend, moves escrow, computes pacing, runs an
auction, or publishes to the legacy delivery engine. **Cancellation deliberately
does not release funds** — releasing reserved budget stays an explicit, separate
call into the slice-4 funding service, so the two lifecycles never entangle.

Explicitly **out** (not started): delivery, impressions, clicks, spend
consumption, pacing, recurrence, bidding windows, delivery forecasts,
timezone-specific scheduling, analytics, advanced targeting, Marketplace, Crypto,
mobile-cache work.

## 2. Files

| File | Role |
|---|---|
| `services/business_os/advertising/schema.py` | Extended: adds one additive table in `ensure_schema` — `business_os_ad_campaign_operations` (per-campaign operational state, one row per campaign) + its status index. |
| `services/business_os/advertising/operations.py` | **New** operational lifecycle service. Transition state machine (single source of truth), activation-eligibility gate, UTC window normalization/validation, advertiser + admin verbs, reads. Never touches the ledger. |
| `services/business_os/advertising/api.py` | New handlers: `get_operational`, `schedule`, `activate`, `pause`, `resume`, `cancel` (advertiser); `admin_get_operational`, `admin_list_operations`, `admin_pause`, `admin_cancel`, `admin_complete` (admin). |
| `bot.py` | Six flag-gated advertiser routes (operational read / schedule / activate / pause / resume / cancel) + five owner-guarded admin routes (list, view, pause, cancel, complete). The three admin interventions write `log_admin_audit`. No legacy route changed. |
| `migrations/business_os/0007_advertising_operations.sql` / `.down.sql` | Additive `CREATE TABLE IF NOT EXISTS` for the operations table + index (up); drops only that one table + index (down) — never the ledger, funding, campaign, audit, or legacy tables. |
| `tests/business_os/test_advertising_slice5_api.py` | 15-test controller/service decision + integrity matrix (in-process, real SQLite + real ledger). |
| `tests/business_os/test_advertising_slice5_routes.py` | 9-test structural check of the new bot.py operational route wiring via `ast`. |

Legacy `services/pulse_ads_service.py` and `/api/pulse/ads/...` remain untouched.
The canonical surface stays a **separate namespace**
(`/api/business-os/advertising/…`, `/admin/business-os/advertising/…`).

## 3. Design decisions

**One source of truth for legal moves.** `OPERATIONAL_TRANSITIONS` is the single
map the service validates against; `_apply_transition` rejects any pair not listed
(409 `illegal_operational_transition`) before writing. Clients never send a raw
`operational_status` — they send a verb (schedule/activate/pause/resume/cancel),
and the target state is fixed server-side. `completed` and `cancelled` are terminal
(empty allowed-set).

**The activation gate composes existing foundations — it invents nothing.**
`_load_activation_context` runs, in order: advertiser eligibility (rollout flag →
account hold → advertiser approval, 403 `ineligible`) → ownership via
`service.get_campaign` (404, existence not leaked) → not archived (409) →
`status == approved` (409 `not_approved`) → funding `funded` (409 `not_funded`) →
derived `activation_ready` (409 `not_activation_ready`) → valid budget (409
`bad_budget`) → valid currency (409 `bad_currency`). All inputs must hold — proving
approval-alone and funding-alone can never activate. `schedule`, `activate`, and
`resume` all run this gate; `pause` and `cancel` need only ownership (removing
capability never requires the gate).

**UTC-only, range-validated scheduling.** `_normalize_window` parses optional
start/end (ISO-8601 with `Z`/offset, or epoch seconds), assumes naive input is
UTC, normalizes to UTC, and rejects a window whose end is not strictly after its
start (400 `bad_window`) or an unparseable timestamp (400 `bad_timestamp`). No
timezone-specific pacing, recurrence, or forecast was added.

**No money, no delivery — enforced structurally.** `operations.py` imports the
funding module only to *read* funding state (`_get_funding_row`,
`_funding_public`); it never calls `reserve_funds`/`release_funds`/`post_entry`.
The routes structural test asserts no operational route references `post_entry`,
`impression`, `auction`, `reserve_funds`, `release_funds`, or `deduct`. The
operational projection exposes `delivering: False` and carries no
impression/click/spend/audience/placement/pacing field. Cancellation leaves escrow
exactly as it was (`test_cancel_does_not_release_funds`).

**Ownership + audit reuse the proven mechanisms.** Ownership is derived from the
authenticated user (never from the request body); a non-owner gets 404 for every
verb and the read. Every transition writes the existing append-only
`business_os_ad_audit` trail (actor, campaign, previous → new operational state,
reason, timestamp) via `service._audit` — no competing audit framework. The three
admin interventions additionally write `log_admin_audit` at the route (acting
admin, target campaign, resulting state, reason, request ref).

**Dark-when-off is preserved.** Advertiser routes return 404 when the flag is off;
admin routes gate the flag *after* the owner guard (→ 409). Creating the empty
operations table changes zero behaviour until the flag is enabled.

## 4. Validation matrix (observed, not asserted-by-claim)

`python3 tests/business_os/test_advertising_slice5_api.py` → **15/15 PASS**

| Test | What it proves |
|---|---|
| `test_flag_off_dark` | Every new handler (advertiser + admin) returns 404 when the flag is off. |
| `test_approved_and_funded_can_activate` | approved + funded + activation_ready → activate → `active`, `activated_at` stamped, `delivering=false`. |
| `test_approved_but_unfunded_cannot_activate` | Approved but no reservation → activate/schedule 409 `not_funded`; stays `inactive`. |
| `test_funded_but_unapproved_cannot_activate` | Funded but review regressed → activate 409 `not_approved`. |
| `test_suspended_advertiser_cannot_activate_or_resume` | Suspended advertiser / held account → resume + first-activate 403 `ineligible`. |
| `test_invalid_date_ranges_rejected` | end ≤ start → 400 `bad_window`; garbage timestamp → 400 `bad_timestamp`; valid window normalized to UTC (`Z`). |
| `test_scheduled_can_pause_and_resume` | `scheduled → paused → active`; `paused_at` stamped. |
| `test_illegal_transitions_rejected` | pause on `inactive`, and any move after `cancelled` → 409 illegal/`not_paused`. |
| `test_non_owner_cannot_control` | Non-owner gets 404 on read + all five verbs (existence not leaked). |
| `test_admin_interventions_audited` | Admin pause + complete recorded in the ad-audit trail with actor + before/after state; admin view combines all three states. |
| `test_cancel_does_not_release_funds` | Advertiser + admin cancel leave escrow == reservation; funding_status stays `funded`. |
| `test_activation_no_spend_no_delivery` | Activation moves no wallet/escrow money; view leaks no delivery field; no legacy delivery table created. |
| `test_admin_listing_and_combined_read` | Admin combined read + status-filtered cross-owner listing; unknown filter → 400. |
| `test_unknown_fields_rejected` | Raw `operational_status`/`budget_cents` on schedule/activate → 400 `unknown_field`. |
| `test_full_lifecycle_happy_path` | `scheduled → active → paused → active → completed`; review/funding untouched; escrow unmoved. |

`python3 tests/business_os/test_advertising_slice5_routes.py` → **9/9 PASS**
(bot.py parses; the operational read route wires flag+auth+session-derived owner;
the five write routes wire flag+auth+write-CSRF+session-derived owner; advertiser
routes are dark 404 when off; admin reads wire owner-guard+flag (409) and are
read-only; admin interventions wire owner-guard+flag+CSRF+`log_admin_audit`+request
ref; canonical namespace + legacy route intact; every route delegates to the
controller; no operational route references any delivery/spend symbol.)

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
| `test_advertising_slice5_api` | 15/15 |
| `test_advertising_slice5_routes` | 9/9 |
| `test_entitlements` | 26/26 |
| `test_entitlement_account_hold` | 11/11 |
| `test_entitlement_effective_access` | 11/11 |
| `test_entitlement_identity_effects` | 11/11 |
| `test_premium_visibility_effective_override` | 6/6 |
| `test_ledger_and_webhook_inbox` | 6/6 |
| `test_stripe_ledger_handler` | 7/7 |

Total: **169/169** across all Business-OS + entitlement + payment suites. Byte-compile:
`bot.py`, `advertising/schema.py`, `advertising/operations.py`, `advertising/api.py`,
plus all `ledger/*.py` → all OK.

Protection contracts: `test_core_platform_contract` OK; `test_livestream_contract`
OK. **`test_media_playback_contract` FAILS** on `next two Reels are preloaded`.
This is a **pre-existing drift** unrelated to slice 5: the mobile Reels preload code
in `bot.py` (≈ line 37484) was refactored to key preload flags by mode
(`'reelLightPreloaded'+mode` with `mode ∈ {previous,next1,next2}`), while the
protection test still asserts the older string `'reelLightPreloaded'+(idx+1)`. Slice
5 only added advertising route functions (≈ line 18120–18400) and touched no Reels,
media, or navigation code. This failure should be resolved separately by
reconciling the Reels protection test with the refactored preload implementation;
it is outside the advertising scope and not introduced by this work.

## 5. Owner-side staging guide

The sandbox `.git` is read-only, so these steps are for the owner to run locally.

1. **Review the diff.** Changes are isolated to the advertising module (new
   `operations.py`, extended `schema.py` + `api.py`), eleven new bot.py route
   functions, the two 0007 migration files, and two new test files. No legacy
   `pulse_ads` code, no payments/entitlement/ledger-internal code, and no schema
   outside the one new `business_os_ad_campaign_operations` table is touched.

2. **Apply the migration.** `migrations/business_os/0007_advertising_operations.sql`
   creates the operations table and its index. It is additive and idempotent
   (`CREATE TABLE IF NOT EXISTS`); the same table is also created idempotently by
   `schema.ensure_schema()`. Rollback: `0007_advertising_operations.down.sql` drops
   only that one table + index — it never touches `ledger_*`, the funding tables,
   the campaign/advertiser/audit tables, or any `pulse_ads` table.

3. **Keep the flag OFF in production initially.** With `BUSINESS_OS_ADVERTISING`
   unset/false the entire canonical surface (including the new operational routes)
   returns 404 — verified by `test_flag_off_dark`. Ship dark, then enable per
   environment.

4. **Smoke-test with the flag on (staging).** Take a campaign through
   draft → submit → admin approve → budget → reserve (funded). `GET
   …/campaigns/<id>/operational` → confirm `review_status=approved`,
   `funding_status=funded`, `activation_ready=true`, `operational_status=inactive`,
   `delivering=false`. `POST …/campaigns/<id>/schedule` (optional
   `start_at`/`end_at`) → `scheduled`; `POST …/activate` → `active` with
   `activated_at`; `POST …/pause` → `paused`; `POST …/resume` → `active`. As owner,
   `POST /admin/…/campaigns/<id>/operational/complete` → `completed`; `GET
   /admin/…/campaigns/<id>/operational` → confirm the three states + funding; `GET
   /admin/…/operations?operational_status=active` → confirm the listing. Confirm an
   unfunded or unapproved campaign is refused (409), a bad date range is refused
   (400), a non-owner gets 404, and that **no ad is delivered and no spend occurs**
   at any point — cancel a campaign and confirm its escrow balance is unchanged.

5. **Run the suites** in section 4 locally before merging; they need no pytest
   (`python3 tests/business_os/<name>.py`).

## 6. Completion boundary

Stops exactly at the spec boundary: the canonical workflow
**approved + funded → scheduled or active → paused/resumed → completed or
cancelled** works end-to-end, gated by the composed activation-eligibility check,
audited on every transition, and **no ad is delivered**. No audience selection,
placement, impression/click tracking, spend consumption, escrow movement, pacing,
auction, recurrence, forecast, Marketplace, or Crypto work was started. `active`
is an authorization signal for a future delivery worker only — review approval,
funding, operational status, and live delivery remain four separate concerns.
