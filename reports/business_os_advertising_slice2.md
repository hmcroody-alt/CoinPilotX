# Business OS — Advertising Vertical, Slice 2 (HTTP Exposure + RBAC)

Status: **PASS** (implemented, tested, byte-compiled; commits owner-side only —
sandbox `.git` is read-only). Flag-gated and dark until `BUSINESS_OS_ADVERTISING`
is enabled.

## 1. Scope delivered

Wire the completed canonical advertising service (slice 1) into narrow, flag-gated
server routes so a real, authenticated advertiser can drive the first end-to-end
workflow over HTTP, and an authorized admin can manage advertiser approval over a
real admin API.

Advertiser can, through the canonical API: read eligibility/approval state;
register an advertiser profile; create a draft campaign; list their own campaigns;
read one owned campaign; update an owned draft; archive and restore an owned draft.

Admin can, through the canonical admin API: list advertiser profiles; read one
advertiser; approve/reject/suspend/restore an advertiser (audited); list canonical
draft campaigns; read one campaign.

Explicitly **out** (unchanged from slice 1, not started): wallet funding, paid
campaign activation, quota consumption, ad delivery/auction, targeting, reporting,
campaign approval/moderation, Marketplace. No balances/billing/spend/impression or
legacy ad table is touched.

## 2. Files

| File | Role |
|---|---|
| `services/business_os/advertising/api.py` | **New** framework-agnostic HTTP controller. Pure `(status_code, body)` functions; unit-testable without Flask. |
| `services/business_os/advertising/service.py` | Extended: `update_campaign_draft`, `eligibility_public_view`. |
| `bot.py` | **One added hunk** (`@@ -17649,+17704,390`): 13 flag-gated routes + 5 helpers, inserted before `admin_admins_page`. No legacy route changed. |
| `tests/business_os/test_advertising_slice2_api.py` | 8-test controller decision matrix (runs in-process). |
| `tests/business_os/test_advertising_slice2_routes.py` | 6-test structural check of bot.py route wiring (auth/CSRF/owner-guard/audit) via `ast`. |

Legacy `services/pulse_ads_service.py` and `/api/pulse/ads/...` remain untouched.
The canonical surface is a **separate namespace** (`/api/business-os/advertising/…`,
`/admin/business-os/advertising/…`), never a redirect or replacement.

## 3. Why a controller layer + a structural route test

`bot.py` is not importable in the hermetic sandbox (needs stripe/flask/telegram; no
PyPI). So the substantive decision logic every route delegates to lives in the
importable `api.py` controller and is tested **live** in-process. The thin bot.py
adapters — authentication, CSRF, the owner RBAC guard, the admin audit call, and
flag-off darkness — are verified by parsing the bot.py source and asserting each
canonical route wires the required guard. This honestly covers "unauthenticated
denied" and "non-admin rejected" (enforced by the bot.py guards, verified by
inspection) while every branch of the decision logic is exercised at runtime.

## 4. Routes

Advertiser (all: flag-off ⇒ 404 dark first, then `pulse_ads_api_user_required()`;
writes add `pulse_ads_verify_write()`; owner identity always from
`user.get("user_id")`, never the request body):

- `GET  /api/business-os/advertising/eligibility`
- `POST /api/business-os/advertising/advertiser`
- `POST /api/business-os/advertising/campaigns`
- `GET  /api/business-os/advertising/campaigns`
- `GET  /api/business-os/advertising/campaigns/<campaign_id>`
- `POST /api/business-os/advertising/campaigns/<campaign_id>/update`
- `POST /api/business-os/advertising/campaigns/<campaign_id>/archive`
- `POST /api/business-os/advertising/campaigns/<campaign_id>/restore`

Admin (all: `require_owner_api()` first, then flag gate; the status writer also
requires `_business_os_ent_csrf_ok()` and calls `log_admin_audit`):

- `GET  /admin/business-os/advertising/advertisers`
- `GET  /admin/business-os/advertising/advertisers/<user_id>`
- `POST /admin/business-os/advertising/advertisers/<user_id>/status`
- `GET  /admin/business-os/advertising/campaigns`
- `GET  /admin/business-os/advertising/campaigns/<campaign_id>`

## 5. Key invariants

**Owner identity is server-derived.** Every advertiser route reads
`user.get("user_id")` from the authenticated session/token; the request body cannot
name an owner.

**Clients can never set lifecycle status.** `status` is not on any client
allowlist (`CREATE_FIELDS`/`UPDATE_FIELDS`), so it is rejected `400 unknown_field`.
Create forces `status='draft'`; lifecycle changes go only through the `archive` /
`restore` verbs, which map to fixed server-side target states
(`{"archive":"archived","restore":"draft"}`).

**Ownership does not leak existence.** A campaign not owned by the caller returns
`404 not_found`, identical to a nonexistent id.

**Explainable eligibility, un-merged.** `eligibility_public_view` exposes
`eligible`, `rollout_enabled`, `account_hold`, `advertiser_status`,
`denial_reason` as separate fields — account hold, advertiser approval, and rollout
are never folded into a single boolean.

**Dark when off.** With the flag off, every canonical handler returns 404 and no
partial canonical write path is exposed; legacy behavior is unchanged.

**No internal exception leakage.** Only curated `AdvertisingError` messages/codes
are surfaced; unexpected exceptions are never returned to the client.

**Admin audit trail.** The status writer records acting admin, target advertiser,
action, before_status, after_status, reason (when supplied), and request_ref via
`log_admin_audit(admin["id"], "business_os_advertiser_status", "advertiser",
str(user_id), {...})`. Actions map server-side through
`_BO_AD_ADMIN_ACTION_TO_STATUS`; clients send an action, never a raw status.

## 6. Validation matrix — results

| # | Requirement | Where verified | Result |
|---|---|---|---|
| 1 | Unauthenticated denied | routes test: `pulse_ads_api_user_required` on every advertiser route | PASS |
| 2 | Flag off ⇒ dark 404 (all handlers) | api test `test_flag_off_dark` | PASS |
| 3 | Active approved advertiser allowed | api test `test_create_draft` success path | PASS |
| 4 | Active pending advertiser denied | api test `test_create_draft` (403 ineligible) | PASS |
| 5 | Suspended account denied despite approval | api test `test_create_draft` hold precedence | PASS |
| 6 | Advertiser cannot read another's campaign | api test `test_ownership_read` (404) | PASS |
| 7 | Advertiser cannot set status directly | api tests create/update (`unknown_field`) | PASS |
| 8 | Invalid fields rejected | api tests `bad_objective`, `unknown_field`, `no_fields` | PASS |
| 9 | Draft create/update/archive/restore lifecycle | api tests `test_update_draft`, `test_lifecycle` | PASS |
| 10 | Admin approval + suspension carry before/after | api test `test_admin` | PASS |
| 11 | Non-admin rejected from admin routes | routes test: `require_owner_api` on every admin route | PASS |
| 12 | Administrative audit record created | routes test `test_admin_status_audit_fields` + `log_admin_audit` wired | PASS |
| 13 | Legacy advertising behavior unchanged | routes test `test_separate_from_legacy` (legacy route present; no `pulse_ads_service` in canonical funcs) | PASS |
| 14 | Slice 1 tests still green | `test_advertising_slice1.py` 11/11 | PASS |
| 15 | Entitlement + Premium regressions | see §7 | PASS |
| 16 | Payments regressions | `test_stripe_ledger_handler.py` 7/7 | PASS |
| 17 | Python byte compilation | bot.py + advertising + facade + premium engines | PASS |

## 7. Full regression run (observed)

```
test_advertising_slice1.py .................... 11/11
test_advertising_slice2_api.py ................  8/8
test_advertising_slice2_routes.py .............  6/6
test_entitlement_effective_access.py .......... 11/11
test_entitlement_account_hold.py .............. 11/11
test_entitlement_identity_effects.py .......... 11/11
test_entitlements.py .......................... 26/26
test_premium_visibility_effective_override.py .  6/6
test_stripe_ledger_handler.py .................  7/7
py_compile: bot.py, services/business_os/advertising/*.py,
            services/business_os/entitlements/facade.py,
            services/premium_capability_engine.py,
            services/premium_visibility_engine.py,
            services/premium_identity_engine.py,
            services/premium_entitlement_service.py  → all OK
```

## 8. Reversibility / rollout

Unchanged gate: env `BUSINESS_OS_ADVERTISING` (`1/true/on/yes/enabled/canonical`;
anything else, including unset, is **off**). Routes exist in code but are fully dark
when off — advertiser routes 404, admin routes 404 behind the owner guard. No new
tables or migrations in this slice; the slice-1 `0004_advertising` DDL already
provisions storage. Removing the routes is deleting the single bot.py hunk.

## 9. Owner-side staging guide

Sandbox `.git` is read-only, so commit on your machine. Slice-2 artifacts:

```
git add \
  services/business_os/advertising/api.py \
  services/business_os/advertising/service.py \
  tests/business_os/test_advertising_slice2_api.py \
  tests/business_os/test_advertising_slice2_routes.py \
  reports/business_os_advertising_slice2.md
# bot.py: stage ONLY the advertising route hunk (approx @@ -17649 +17704,390,
# the block before admin_admins_page). Use `git add -p bot.py` and accept only
# that hunk — the file also carries ~28 unrelated pre-existing hunks from prior
# Premium/R3 work that are NOT part of this slice.
git add -p bot.py
```

Verify before commit:

```
python3 -m py_compile bot.py
python3 tests/business_os/test_advertising_slice2_api.py
python3 tests/business_os/test_advertising_slice2_routes.py
python3 tests/business_os/test_advertising_slice1.py
```

Staging enablement (canonical surface stays dark until this is set):

```
export BUSINESS_OS_ADVERTISING=on   # gunicorn bot:app
```

Smoke path once enabled (authenticated advertiser session/token):

```
GET  /api/business-os/advertising/eligibility        # {eligible, denial_reason, ...}
POST /api/business-os/advertising/advertiser         # self-register (pending)
# owner-side admin approves:
POST /admin/business-os/advertising/advertisers/<id>/status   {action:"approve", reason:"..."}
POST /api/business-os/advertising/campaigns          # {name, objective, destination_url} -> 201 draft
GET  /api/business-os/advertising/campaigns          # lists only the caller's campaigns
POST /api/business-os/advertising/campaigns/<cid>/archive
POST /api/business-os/advertising/campaigns/<cid>/restore
```

Rollback: unset `BUSINESS_OS_ADVERTISING` (all canonical routes go dark) and/or
revert the bot.py hunk. No data migration to undo.

## 10. Completion boundary honored

Stops here: route implementation, focused tests, regression validation, this
report, and the owner-side staging guide. Not started: wallet funding, paid
campaign activation, ad delivery, targeting, reporting, Marketplace.
