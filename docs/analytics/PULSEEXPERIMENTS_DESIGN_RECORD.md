# PulseExperiments — assignment without a store, and the readout that was not built

Code: `services/pulse_experiments/`
Tests: `tests/pulse_experiments/` — 128 tests, 20 mutations written, 20 caught.

Two findings shaped this package more than the brief did. The first is that
PulseSoc already has a feature-flag control panel that controls nothing. The
second is that no experiment defined today could reach a conclusion, because
the population is 41 users.

---

## 1. The capability matrix is a mirror, not a control

`/admin/capability-matrix` (`bot.py:103315`) is live. It is owner-gated
(`admin_is_owner_level`), it writes to a real `feature_flags` table, and it
audit-logs every change as `feature_flag_updated`. An owner can set
`pulse_livestream` to `disabled`, save, and watch the page render the new state
back.

Nothing reads it.

The `feature_flags` table is read in exactly two places: `load_feature_flags`
(`bot.py:103249`), which feeds the admin page that just wrote it, and a
row-count in an admin stats list (`bot.py:107309`). `evaluate_flag`
(`services/feature_flag_engine.py:259`) — the function that would decide whether
a given user may see a given feature — has **one occurrence in the entire
repository, its own definition**. `rollout_percentage` is written by the form,
persisted, and rendered back, and is never read by any gating logic anywhere.
There is even an index, `idx_feature_flags_state` (`bot.py:122072`), built to
serve queries nobody wrote.

So the control is a mirror: it faithfully reflects whatever an operator typed
into it, and the product ignores all of it.

### The landmine underneath

The obvious fix — wire `evaluate_flag` into the surfaces — would **disable
marketplace checkout in production**. Verified against production on
2026-09-22, all 15 rows carry `updated_at = 2026-05-22T11:51:57`, identical to
the row they were seeded with, which is the proof nobody has ever used the
page. And the seed says:

```
marketplace_checkout    internal-only    rollout=0
```

Checkout is live. `seller_transactions` holds 32 rows. The seeded state is a
description of the product as it stood in May, and the product moved without
it. Three more (`pulse_livestream`, `pulse_reels`, `ai_assistant`) are seeded
`beta`, which `evaluate_flag` treats as admin-only.

This is why the package below does not touch the capability matrix. Making a
dead control live is not a refactor; it is switching on fifteen gates whose
stored positions were last true four months ago, and the first one to fire
takes payments down. **Recommended separately, with the states re-derived from
what is actually shipped, and not bundled into an analytics change.**

---

## 2. What was built

A deterministic assignment layer. No table, no admin surface, no ingest.

| Decision | Why | Enforced by |
|---|---|---|
| Arms are computed, never stored | An `experiment_assignments` table would be the 112th event table, written at ~1 row/week, and a stored arm can drift from the weights that produced it | `test_the_same_subject_gets_the_same_arm_every_time` |
| Eligibility and variant hash under **separate** namespaces | With one bucket, every enrolled subject sits below the rollout ceiling and therefore inside the first arm — a 10% rollout of a 50/50 test puts 100% of its population in control and reports a null result forever while looking healthy | `test_a_partial_rollout_is_still_split_by_the_weights` |
| Widening a rollout admits, never reshuffles | Otherwise the act of strengthening a comparison invalidates it | `test_widening_a_rollout_does_not_move_anyone_already_inside` |
| The experiment key is mixed into the hash | Without it a subject unlucky once is unlucky in every experiment at once, and the second test measures the first | `test_two_experiments_do_not_assign_the_same_subject_the_same_way` |
| Its own salt, not commerce's | A shared salt would let an exported assignment be joined onto the behavioural profile `commerce_discovery`'s salt exists to protect | `test_the_token_changes_with_the_salt` |
| Every failure resolves to `control` | Control is the behaviour the product shipped and tested before any experiment existed | `test_an_unusable_subject_is_control_and_is_not_bucketed` |
| `reason` distinguishes the four routes to control | `variant=control` alone cannot separate a running experiment from a switched-off one | `test_the_reason_distinguishes_the_four_ways_to_reach_control` |

`True` is rejected as a subject before `int` is considered, because `True == 1`
and user 1 is this platform's only seller — the same refusal-over-coercion rule
`read._seller_id` follows.

### Protected domains

An experiment may not be **defined** in `auth`, `authz`, `payment(s)`,
`payout(s)`, `order(s)`, `privacy`, `fraud`, `seller_authz` or
`entitlement(s)`. Refusal is at construction, so no definition object exists
that a later change could begin evaluating.

That is a rule about strings, and saying more would overclaim. Nothing stops an
author naming something `checkout.button_copy` and branching on it to skip a
fraud check. What closes the gap is
`tests/pulse_experiments/test_protected_domains.py`, which asserts that **no
module owning one of these concerns imports the package at all** — 32 modules
matched today, including `auth_service`, `stripe_service` and
`payment_provider`. A gate unreachable from the payment path cannot alter it,
whatever it is called. The matching is per dotted segment, so `repayment_ui`
and `authoring.toolbar` are allowed; a substring rule would refuse innocent
keys and train authors to rename around it.

### Exposure

A log line, `PULSE_EXPERIMENT_EXPOSURE`, in the same shape as
`PULSE_ANALYTICS_FUNNEL_SERVED`. The subject is a 16-hex salted token, never a
`user_id`. Emission is once per subject per experiment per process — three
lookups while rendering one page is one exposure, or an arm's denominator
measures how many times the template asked rather than how many people saw it.

### Kill switches, two of them

`PULSE_EXPERIMENTS_ENABLED=false` sends every subject to control in one move,
so an operator in an incident does not have to work out which experiments were
running. Per-experiment, `enabled=False` or deleting the definition does the
same for one. Both are tested in the direction that matters: unset must not
mean off, or an unprovisioned environment disables silently.

---

## 3. The readout was not built, and the registry ships empty

`registry.ACTIVE` is `build(())` — no experiments defined. This is asserted by
a test so that adding the first one is a decision someone makes knowingly.

An A/B test needs enough subjects for a difference to outrun noise. Production
holds **41 registered users**, and the commerce event log — the only place an
outcome could be observed — holds **24 impressions from 1 distinct viewer** and
**3 engagements**.

Using the standard approximation `n ≈ 16·p(1−p)/δ²` per arm (80% power, 5%
two-sided), against a 10% baseline:

| Effect to detect | Needed per arm | Needed total | Available |
|---|---|---|---|
| 10% → 12% (a strong result) | ~3,600 | ~7,200 | 41 |
| 10% → 20% (a doubling) | ~144 | ~288 | 41 |

Detecting even a *doubling* needs seven times the entire user base. So a
readout built today would not produce weak evidence, it would produce noise
formatted as evidence — and a dashboard reporting "compact wins, +14%" over
eleven subjects is the same defect PulseAnalytics was written to stop, wearing
a third hat. `PULSEANALYTICS_DESIGN_RECORD.md` §1 states the rule this follows:
a number that is not wrong about anything in particular is worse than being
wrong.

The machinery is built and tested because assignment, kill switches and
protected-domain refusal are worth having at any population size — a kill
switch for 41 users works exactly as well as one for 41 million. The
statistical half waits.

### What would change it

Around **1,000 monthly active users** makes a doubling detectable in a week and
a 20% lift detectable in a quarter. Below a few hundred, the honest use of this
package is feature gating and staged rollout, not measurement.

---

## What was deliberately not built

* **No assignment table.** See §2. It would have been the 112th.
* **No admin form for definitions.** An experiment changes product behaviour
  and goes through the review that changes to product behaviour go through.
  The capability matrix is the cautionary example in the other direction.
* **No statistical readout.** See §3.
* **No change to the capability matrix.** See §1 — that is a separate,
  higher-blast-radius piece of work, and it needs its seeded states re-derived
  from what is actually shipped before anything reads them.
