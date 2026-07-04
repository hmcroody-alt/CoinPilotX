# PulseSoc Universal Growth Engine Foundation

## What Was Built

PulseSoc now has a universal Growth Engine foundation. Every account can be provisioned with the full growth infrastructure automatically, without asking the user to create an advertising account or complete a setup wizard.

User-facing language is Growth-focused:

- Growth Center
- Growth Account
- Campaign Workspace
- Promotion Wallet
- Pulse AI Growth Advisor
- Growth Score

Internal compatibility remains in the existing ads/promotion APIs and tables because feed promotion, campaign delivery, creative review, wallet funding, tracking, and moderation already depend on them.

## Files Changed

- `services/pulsesoc_growth_engine.py`
- `migrations/pulsesoc_growth_engine.sql`
- `scripts/backfill_pulsesoc_growth_engine.py`
- `scripts/pulsesoc_growth_engine_audit.py`
- `templates/pulse_advertiser_portal.html`
- `static/js/pulse_advertiser_portal.js`
- `bot.py`
- `data/pulse_ai/pulsesoc_feature_map.json`
- `data/pulse_ai/pulsesoc_knowledge.json`

## Provisioned Records

For each user, the Growth Engine creates or verifies:

- Growth Account
- Campaign Workspace
- Promotion Wallet
- Promotion Ledger
- Audience Profile
- Audience Learning Model
- Creator Growth Profile
- Promotion History
- Billing Profile, inactive by default
- Promotion Preferences
- AI Growth Assistant Session
- Analytics Container
- Conversion Tracking ID
- Internal Promotion API Key hash
- Growth Score
- Trust Score Link
- Risk Profile
- Existing promotion wallet compatibility records

## New Account Provisioning

`create_account()` now provisions the Growth Engine inside the signup transaction after the user and subscription records are created. If provisioning fails during signup, the transaction rolls back instead of leaving a partially provisioned account.

## Existing User Backfill

Backfill is resumable:

```bash
venv/bin/python scripts/backfill_pulsesoc_growth_engine.py --limit 500 --after-user-id 0 --json
```

The backfill:

- scans users by increasing `user_id`
- creates only missing records
- avoids duplicate Growth Accounts, wallets, ledgers, and API keys
- logs provisioning and failures to `pulse_growth_provisioning_log`
- returns `next_cursor` and `has_more`

## Growth Center

Canonical route:

```text
/pulse/growth
```

Compatibility routes still work:

```text
/pulse/advertise
/pulse/ads
```

The visible UI now uses Growth language and avoids asking users to create an ad account. The existing campaign, creative, wallet, billing, moderation, and analytics controls remain backed by the existing secure APIs.

## Admin Tools

Admin route:

```text
/admin/growth-engine
```

Compatibility route:

```text
/admin/ads-command-center
```

Admin backfill endpoint:

```text
POST /api/admin/growth-engine/backfill
```

The admin view shows Growth Engine provisioning counts, wallet/profile/model/container/key readiness, and links into review, analytics, audit, and registry tools. Admin access is protected by existing `command_center.view` permission checks.

## Pulse AI Knowledge

Pulse AI knowledge now includes Growth Center guidance:

- how to grow reach
- how to promote a business, Reel, music, marketplace listing, event, live stream, or creator profile
- where the promotion wallet and Growth Center live
- safety notes around private targeting data and internal API keys

## Security

- Internal promotion API keys are stored as hashes with a short prefix only.
- Raw targeting data is not exposed in user summaries.
- Billing starts inactive.
- Promotion wallet funding remains gated by existing billing/provider checks.
- Campaign delivery still requires moderation, budget, review, placement, and safety checks.
- Admin backfill controls are permission protected and audit logged.

## Performance

Provisioning is idempotent and lightweight. Existing users can be backfilled in batches. Heavy analytics remain in existing tracking/worker paths; user page loads read provisioned records and summaries.

## QA Results

Prepared and verified by `scripts/pulsesoc_growth_engine_audit.py`:

- new user provisioning creates required Growth records
- repeated provisioning does not duplicate records
- existing user backfill provisions missing records
- backfill can be rerun safely
- internal API key is hashed
- Growth Center routes exist
- admin backfill route is protected
- user UI uses Growth language
- Pulse AI knowledge includes Growth Center

## Known Limitations

- Live billing remains inactive until provider/configuration and policy review are enabled.
- Advanced modules such as A/B testing, retargeting, and conversion optimization are provisioned as infrastructure-ready but should unlock progressively.
- Existing internal API route names still include `/ads` for compatibility with the current promotion delivery system; user-facing copy now uses Growth language.
