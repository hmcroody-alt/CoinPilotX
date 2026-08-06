# Advertising — what the backend actually serves

Written before Phase 2 of the Advertising OS mission, by reading `bot.py` and
`services/` rather than by reading the completion reports. The mission's §0 lists
a Campaign/Ad Group/Ad hierarchy, a Policy Center, review and appeal history,
attribution and permissions as missing. Most of that list is wrong in an
expensive direction: the work exists, it is live, it is ungated, and no mobile
code calls it.

The purpose of this document is to stop Phase 2 from building a screen against
an API that answers 404 for every seller. That failure is not hypothetical — it
is exactly what would happen to a hierarchy UI written against the endpoints the
schema files advertise.

## There are two advertising backends, and only one of them is on

**The legacy advertiser portal**, `/api/pulse/ads/*`, 24 routes in `bot.py`
around `:17082–17760`, backed by `services/pulse_ads_service.py` (66 KB) and
`services/pulse_advertiser_portal.py`. **No environment gate.** Every route
checks authentication and CSRF and nothing else. This is what the mobile app
calls today and it is what every seller can reach right now.

**The Business OS canonical surface**, `/api/business-os/advertising/*`, 46
routes in `bot.py` around `:18900–20033`, backed by the 23 modules in
`services/business_os/advertising/`. Every single route opens with

```python
if not _business_os_advertising_enabled():
    return jsonify({"ok": False, "error": "Not found."}), 404
```

and `_business_os_advertising_enabled()` (`bot.py:18843`) reads the server
variable `BUSINESS_OS_ADVERTISING`, which is **blank in `.env.example:416`**.
The header comment at `:18830` states the intent plainly: "When
BUSINESS_OS_ADVERTISING is off the whole surface is dark (404), so no partial
canonical path is exposed." `docs/business_os/SLICE7_ADVERTISING_REPORT.md:296`
confirms the rollout plan was to deploy dark first. Nothing in the repo records
that it was ever turned on.

This is a second, larger flag registry than the mobile one. `.env.example`
carries roughly two dozen `BUSINESS_OS_*` variables — advertising, attribution,
ledger, marketplace, orders, store, insights, verification — every one of them
blank. A mobile flag that is off degrades a surface. A server flag that is off
deletes it.

**Consequence for Phase 2:** ad sets, the `/campaigns/<id>/submit` review
workflow, the appeals endpoint at `:20033` and the assistant endpoints exist
only on the dark surface. Building against them produces a screen that 404s
until someone with Railway access sets a variable. Anything built this phase
must target `/api/pulse/ads/*` or be explicitly labelled as blocked on a
deployment decision.

## What the live surface actually models

Three levels, not four: **account → campaign → creative**. Placements attach to
the *campaign* (`pulse_ad_campaign_placements`, via
`attach_campaign_placements` at `pulse_ads_service.py:660`), which is the job an
ad set would otherwise do.

So §0's "missing Campaign/Ad Group/Ad hierarchy" is precise in a way the mission
document probably did not intend. Campaign and Ad both exist and are reachable.
The **Ad Group tier genuinely does not exist** on the live surface — it exists as
`business_os_ad_sets` (`services/business_os/advertising/schema.py:233`) on the
dark one. That is the one item on §0's list that is a real absence, and it is
blocked on a deployment decision rather than on engineering.

Campaign fields (`create_campaign`, `:601`): `campaign_name`, `objective` (one
of a 15-value allowlist), `status` (created as `draft`), `budget_type`
(`daily` | `lifetime`), `daily_budget_cents`, `lifetime_budget_cents`,
`spent_cents`, `start_at`, `end_at`, and — read but not written by the create
path — `priority` and `pacing_mode`.

Campaign actions (`CAMPAIGN_ACTIONS`, `pulse_advertiser_portal.py:20`):
`pause`, `resume`, `archive`, `duplicate`, `submit`, `complete`. Mapped to
statuses `paused`, `active`, `archived`, `pending_review`, `completed`.
`resume` calls `pulse_ad_payments.reserve_campaign_budget` first, so resuming is
the point where funding is enforced server-side.

Creative actions (`CREATIVE_ACTIONS`): `duplicate`, `archive`, `delete_draft`,
`submit`.

## The single endpoint that answers most of §0

`GET /api/pulse/ads/portal` (`bot.py:17464` → `portal_summary`,
`pulse_advertiser_portal.py:438`) is ungated, authenticated, and returns, in one
response:

| Key | Contents |
| --- | --- |
| `accounts` | each with `role`, `health_score`, `campaign_count`, `active_campaigns`, `pending_reviews`, `total_spend_cents` |
| `campaigns` | full list |
| `creatives` | full list, media attached, with `performance_state`, `media_ready`, `destination_safe` |
| `wallets` | per account: available, reserved, lifetime funded, lifetime spent, spendable, transactions, receipts |
| `analytics` | `advertiser_analytics` |
| `review_board` | `review_status`, `risk_score`, `automated_review_status`, `human_review_status`, `review_reason`, `reviewed_at`, plus the creative's `moderation_status` and `rejection_reason` |
| `notifications` | typed, with `status` and `read_at` |
| `billing` | `enabled`, `mode`, and an explicit `live_charging: false` |
| `metrics` | fourteen precomputed counts and money figures, cents **and** formatted |
| `campaign_status_counts` | status → count |
| `placements` | the full `PLACEMENT_METADATA` map |
| `roles` | `current` and the `allowed` set |

Read against §0's missing list:

**Policy Center and review/appeal history** — `review_board` is exactly this. It
carries the reason, the risk score, and the separation between the automated and
the human decision. §37 forbids "no inaccessible policy reason or appeal path";
the reason is one unmade HTTP call away.

**Permissions** — `roles` is this, and it is enforced server-side, not
advisory. `ACCOUNT_ROLES` is `owner`, `campaign_manager`, `marketing_manager`,
`analyst`, `viewer`; `WRITE_ROLES` excludes the last two; `_require_account_role`
(`:85`) raises 403. `pulse_ad_team_members` is the membership table.

**Wallet authority** — `wallets` is server-computed in cents. §37's "no
client-authoritative wallet balance" is already satisfiable.

**Creative Library** — `creatives` is populated and has a full write surface
behind it.

## What mobile reaches, and what it does not

Reached today (`api/businessOs.ts`, `api/ads.ts`): `accounts` GET/POST,
`campaigns` GET/POST, `campaigns/<id>` GET/PATCH, `campaigns/<id>/action`,
`analytics`, `accounts/<id>/wallet`, `accounts/<id>/billing-summary`, and the
delivery-side `placements`, `impression`, `viewability`, `click`, `event`.

Never called from anywhere in `mobile-native/src`:

| Endpoint | What it would unlock |
| --- | --- |
| `GET /api/pulse/ads/portal` | everything in the table above, in one request |
| `GET/POST /api/pulse/ads/creatives` | the Creative Library, which Phase 1 could only describe |
| `POST /api/pulse/ads/creatives/submit` | submitting a creative for review |
| `POST /api/pulse/ads/creatives/<id>/action` | duplicate, archive, delete draft, submit |
| `POST /api/pulse/ads/creatives/<id>/replace` | the "edits are versioned" claim Phase 1's preview page makes |
| `GET/POST /api/pulse/ads/accounts/<id>/profile` | account details beyond the number — industry, website, contact, masked tax id |
| `POST /api/pulse/ads/accounts/<id>/media/upload` | creative media, with a delete counterpart |
| `POST /api/pulse/ads/accounts/<id>/wallet/funding-session` | funding, subject to `PULSE_ADS_BILLING_ENABLED` |
| `POST /api/pulse/ads/campaigns/<id>/reserve-budget` | explicit reservation |
| `GET /api/pulse/ads/placement-metadata` | real placement names instead of a hardcoded list |

The gap is a client gap. Nine of these ten need no backend work at all.

## Two things that are genuinely not there

**Attribution.** No attribution model exists on either surface. The Events flag
registry already records this (`EXPO_PUBLIC_EVENTS_ATTRIBUTION` withholds
attributed sales "until one does"). §37's rule about refund-adjusted attributed
revenue cannot be satisfied by a client change; it needs a model first.

**Per-day spend.** `advertiser_analytics` returns totals. There is no daily
series anywhere on the live surface, which is why Phase 1 removed the "last 7
days" claim from the spend card rather than fixing its data source, and why
`buildSpendSeries` returns `windowed: false` unconditionally. A seven-day chart
requires a new endpoint, not a new component.

`PULSE_ADS_BILLING_ENABLED` is also blank in `.env.example:389`, and
`portal_summary` reports it honestly as `live_charging: false` with the note
that "no live advertiser charging occurs here." Wallet top-up is therefore
correctly absent rather than broken, and `adFundingIsLive` agrees.

## What Phase 2 should be

The evidence orders the work differently from §0's list.

First, replace the four-call fan-out (`accounts`, `campaigns`, `analytics`,
`wallet`, `billing-summary`) with one `GET /api/pulse/ads/portal`. It is fewer
requests, it is the same authentication, and it returns five things the app
currently cannot see — review board, notifications, roles, creatives, placement
metadata. Every subsequent item on this list becomes a rendering job rather than
a plumbing job.

Then the Policy Center, because `review_board` already carries the reason and
§37 names an inaccessible policy reason as a completion blocker.

Then the Creative Library, replacing Phase 1's preview page with the real list,
submit, replace and archive actions.

Then permissions, gating the write controls on `roles.current` against
`WRITE_ROLES` so the client stops offering an action the server will refuse with
a 403.

The Ad Group tier and the appeals workflow come last and are **blocked**, not
unstarted: they need `BUSINESS_OS_ADVERTISING` set on the deployment. That is an
owner decision about a dark vertical, and it should be asked as a question
rather than answered by writing a client for a surface that returns 404.

## Confidence

Every route line number, service function and env-var name above was read in
this repo during this audit. The claim that the canonical surface is dark rests
on the guard clause being present in each route body and on `.env.example:416`
being blank; whether the Railway deployment sets it is not knowable from the
repo and is the one open question in this document.
