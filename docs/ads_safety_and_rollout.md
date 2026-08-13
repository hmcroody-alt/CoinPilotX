# Safety, guardrails and rollout

Where the money authority lives, what can stop delivery, how autonomy is
bounded, and how any of this reaches production.

Modules: `services/business_os/advertising/guardrails.py` (canonical package —
see below), plus the failure-direction choices throughout `ads_intelligence`.

## The money-authority line

**The existing ledger is the only thing that moves money.** No module in
`ads_intelligence` writes to a wallet, a ledger, a balance or a billing table.
`tests/business_os/test_ad_account_guardrails.py::test_guardrails_never_move_money`
enforces this structurally: it parses the module AST, asserts no ledger import,
then walks every string constant asserting no `INSERT INTO` / `UPDATE ` /
`DELETE FROM` targets a money table.

That test began as a grep for money-related words in the source and failed on
the module's own docstring — the prose explaining *why* the ledger is untouched
matched the pattern. Rewriting it as an AST check made it test the code rather
than the commentary; the reasoning is recorded in its docstring so the next
person does not re-introduce the weaker version.

**Account guardrails live in `services/business_os/advertising/`, not in
`ads_intelligence/`.** Stopping delivery stops charging, so a guardrail is a
money-authority decision and belongs next to the ledger and the audit trail.
Putting it in the intelligence package would have broken the invariant that the
intelligence layer only measures and advises.

## Account spend ceilings and the emergency stop

Table `business_os_ad_account_guardrails`. Operator actions, all audited into
`business_os_ad_audit`:

- `set_daily_ceiling(user_id, cents)` — `NO_LIMIT = 0` means no ceiling, *not*
  "spend nothing".
- `halt_account_delivery(user_id)` — the emergency stop.
- `lift_account_halt(user_id)`

`check()` returns `{allowed, reason, degraded}` with reasons `REASON_OK`,
`REASON_HALTED`, `REASON_DAILY_CEILING`, `REASON_HALT_UNREADABLE`. Spend is
summed from `business_os_ad_billing_events.total_amount_cents` for the UTC day,
excluding `billing_status = 'failed'`.

`advertiser_eligibility` consults it **last** in precedence, and an import or
wiring failure yields `guardrail = None` rather than a refusal — that class of
failure is our bug, not a signal about the account, and refusing every
advertiser because of it would be a self-inflicted outage.

### The two directions, and why they differ

```python
# halt state unreadable  →  fails CLOSED (treated as halted)
# spend total unreadable →  fails OPEN (delivery continues, degraded=True)
```

The **halt fails closed** because a stop that a failed query can lift is not a
stop. Somebody pressed the emergency button; the one outcome that must not
happen is delivery resuming because a read timed out. The blast radius is one
account.

The **ceiling fails open** because the per-campaign budget and the
overdraft-guarded ledger are both still enforcing underneath it. Converting a
transient database blip into a platform-wide advertising outage is the larger
failure, and the ceiling is a convenience limit, not the last line.

Both are pinned by tests using a connection that raises, asserting each
direction specifically. If you change one, change its test and its comment.

## Autonomy levels

| Level | Name | May |
| --- | --- | --- |
| 0 | Observe | Record and report only |
| 1 | Recommend | Produce advice for a human |
| 2 | Act (non-money) | Apply reversible non-financial changes |
| 3 | Act (bounded) | Apply bounded changes within explicit limits |

**Money actions are permanently capped at level 1.** `max_autonomy_for()`
returns `LEVEL_RECOMMEND` for every member of `MONEY_ACTIONS`, and there is no
`apply()` function to enact anything regardless.

The system currently operates at level 1 for everything. Levels 2 and 3 are
defined so that raising autonomy is a deliberate, reviewable change rather than
a discovery that something has been acting all along.

## Staged rollout

Delivery was never big-banged. The stages, in order, each gated on the previous
one being clean:

1. **Event collection only.** The fabric fills. Delivery is untouched. Nothing
   reads the new data for a decision.
2. **Analytics and diagnostics.** Advertisers and operators can see the data.
   Still no delivery change.
3. **Pacing and frequency, canary.** Small traffic share. Both fail in the safe
   direction; both are throttles, not stops.
4. **Ranker: shadow → canary → ramp.** `ranking.compare()` scores without
   affecting delivery, and the shadow disagreement rate is checked before any
   traffic moves.
5. **Recommendations.** Advice to advertisers, still level 1.
6. **Experimentation and fraud tightening.**

Stages 1 and 2 are what is live in code today. The decision recording in
`select_ads` is stage 1 — it records what the canonical path decided and does
not alter it.

### Rollback

Every stage is reversible without a data migration, because everything derived
is rebuildable from the fabric (see
[`ads_data_quality.md`](ads_data_quality.md)). Rolling back a ranking change
does not require restoring a table; it requires not consulting the ranker.

## Operator runbook

| Situation | Action |
| --- | --- |
| One account spending wrongly | `halt_account_delivery(user_id)` — immediate, audited, one account |
| Advertising broadly wrong | Roll back the stage, not the schema |
| Diagnosis says `degraded: true` | A read failed; the diagnosis is incomplete, do not act on it as if complete |
| Anomaly raised | Always `requires_human` — investigate; the detector never acted |
| No-fill spike | `GET /admin/business-os/ads-intel/delivery-health` gives the named breakdown |

## Rules for future work

- Do not add a write path from `ads_intelligence` to any money table.
- Do not add an `apply()` to `recommendations.py`.
- Do not add an autonomy-level parameter to an HTTP endpoint.
- Do not add an override to `ml_readiness.assess()`.
- Do not build a second advertiser, campaign, wallet, ledger, review, delivery
  or audience system. Extend the canonical one.
- Do not touch real-time audio, livestream, or call paths from advertising work.
  Run `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`.
- When you choose a failure direction, write the reason above the `except` and
  pin it with a test that injects a failure.
