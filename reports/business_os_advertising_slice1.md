# Business OS — Advertising Vertical, Slice 1 (Draft Campaigns)

Status: **PASS** (implemented, tested, byte-compiled; commits owner-side only —
sandbox `.git` is read-only). Flag-gated and inert until enabled.

## 1. Scope delivered

The smallest end-to-end advertiser capability: an eligible advertiser can create,
own, list, and archive/unarchive a **draft** campaign; admins can see everything.

Covered (as requested): (1) advertiser eligibility + account status,
(2) campaign draft creation, (3) campaign ownership + lifecycle state,
(4) server-side validation, (5) admin visibility, (6) tests + feature-flagged
rollout.

Explicitly **out** (deferred, not required to persist a draft): wallet spending,
billing, auction/delivery, advanced targeting, reporting. The metered commercial
quota `advertising.campaign.create` exists in the entitlement catalog but is **not
consumed** in slice 1 — draft creation is not a billable action here.

## 2. Files

| File | Role |
|---|---|
| `services/business_os/advertising/__init__.py` | Package; imports `schema`, `service`. |
| `services/business_os/advertising/schema.py` | Idempotent `ensure_schema()` — additive `business_os_ad_*` tables. |
| `services/business_os/advertising/service.py` | Flag-gated canonical service (eligibility, draft CRUD, lifecycle, admin reads). |
| `migrations/business_os/0004_advertising.sql` / `.down.sql` | Prod migration + rollback, byte-parity with `ensure_schema`. |
| `tests/business_os/test_advertising_slice1.py` | 11-test standalone matrix. |

No legacy file was modified. `services/pulse_ads_service.py` and its
`pulse_ad_campaigns` table are untouched — this is a strangler-pattern parallel
surface, not a change to delivery.

## 3. Reversibility / rollout

Gated behind env `BUSINESS_OS_ADVERTISING` (accepts `1/true/on/yes/enabled/canonical`;
anything else, including unset, is **off**). With the flag off, every
write/eligibility entrypoint raises `AdvertisingError(http_status=503, code="disabled")`
and touches nothing. Creating the empty tables changes zero behaviour. Rollback is
`0004_advertising.down.sql` (drops only the `business_os_ad_*` namespace).

## 4. Eligibility composes THREE separate inputs (never merged)

`advertiser_eligibility(user_id, context)` returns
`{eligible, reason, flag_enabled, account_hold, advertiser_status}` and evaluates,
in precedence order:

1. **Feature rollout** — `BUSINESS_OS_ADVERTISING` off ⇒ `advertising_disabled`.
2. **Account hold / suspension** — shared authority `facade.account_hold(user_id,
   context)`; a hold overrides an approved advertiser (`reason` = e.g.
   `account_suspended`, `account_access_disabled`).
3. **Advertiser approval** — `business_os_ad_advertisers.status == 'approved'`;
   otherwise `advertiser_not_registered` / `advertiser_pending` / `advertiser_rejected`.

Commercial entitlement (grant/quota) and usage allowance are intentionally **not**
folded into this record — consistent with the shared-foundation checkpoint §8. Self
-registration (`upsert_advertiser`) only ever creates a `pending` row; approval is a
separate admin action (`set_advertiser_status`, the role/administrative authority).

## 5. Server-side validation (all server-enforced, never client-trusted)

`name` required, ≤120 chars; `objective` ∈ {awareness, traffic, engagement, leads,
conversions}; optional `destination_url` must be http/https and ≤2048 chars. Status
is **forced** to `draft` on create regardless of caller input. Objective/name are
normalized (trim; objective lowercased).

## 6. Ownership + lifecycle

`get_campaign`/`transition_campaign`/`list_campaigns_for_owner` are owner-scoped: a
non-owner receives `AdvertisingError(404)` and existence is not leaked. Lifecycle in
slice 1 is `draft ⇄ archived` only (`ALLOWED_TRANSITIONS`); same-state is an
idempotent no-op; anything else raises `409 illegal_transition`. Admin reads
(`admin_list_campaigns`, `admin_list_advertisers`, `admin_get_campaign`) are
trusted-caller (bot.py route enforces RBAC before calling) and are **not**
owner-scoped by design. Every state change writes a `business_os_ad_audit` row.

## 7. Validation matrix (observed)

| Check | Result |
|---|---|
| `test_advertising_slice1.py` (11 tests) | **11/11 PASS** |
| flag-off inert (503 on every write/eligibility entrypoint) | PASS |
| account hold overrides approved advertiser | PASS |
| validation (name/objective/url) | PASS |
| draft forced status + ownership recorded | PASS |
| ownership read/transition enforcement (404 to non-owner) | PASS |
| lifecycle draft⇄archived, illegal 409, idempotent no-op | PASS |
| admin cross-owner visibility | PASS |
| owner isolation | PASS |
| audit rows written | PASS |
| byte-compile: module + `bot.py` + facade + service + premium engine | PASS |
| regression `test_entitlement_effective_access.py` (R3.2) | **11/11 PASS** |
| regression `test_premium_visibility_effective_override.py` (R3.3) | **6/6 PASS** |
| migration↔`ensure_schema` parity (SQL-built DB round-trip) + down rollback | PASS |

## 8. Owner-side staging guide (commits happen on your machine)

The sandbox `.git` is read-only, so stage these on the owner workstation:

```
git add services/business_os/advertising/__init__.py \
        services/business_os/advertising/schema.py \
        services/business_os/advertising/service.py \
        migrations/business_os/0004_advertising.sql \
        migrations/business_os/0004_advertising.down.sql \
        tests/business_os/test_advertising_slice1.py \
        reports/business_os_advertising_slice1.md \
        reports/business_os_shared_foundation_readiness_checkpoint.md
```

`bot.py` and `services/premium_visibility_engine.py` in the working tree also carry
the earlier R3.x Premium edits plus ~20 unrelated pre-existing hunks; review with
`git diff` and stage only the intended Premium hunks — this slice adds **no** new
`bot.py` change. Suggested message: `Business OS: Advertising slice 1 — flag-gated
draft campaigns (additive, reversible)`.

Rollout: apply `0004_advertising.sql` (or let `ensure_schema()` run in dev), then
flip `BUSINESS_OS_ADVERTISING=on` only when ready. No bot.py route is wired yet — the
service is callable but not yet exposed on an HTTP surface; wiring a flag-gated
advertiser/admin route is the next slice.

## 9. Known limitations deferred

- No bot.py HTTP route yet (service-layer only); route + RBAC wiring is next.
- No commercial-quota consumption or usage metering on create (deferred slice).
- No delivery/auction/billing/targeting/reporting (out of scope by design).
- Commits owner-side only; bot.py not importable in the hermetic sandbox, so the
  service is proven directly rather than through a live route.
