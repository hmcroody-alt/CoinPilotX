# Delivery controls: pacing, frequency, ad load

Three controls sit between "this ad could be shown" and "this ad is shown".
Each limits a different thing, and each fails in a direction chosen on purpose.

Modules: `ads_intelligence/pacing.py`, `ads_intelligence/frequency.py`.

## Budget pacing

`pacing.assess(daily_budget_cents, observed_spend_cents)` compares spend against
the fraction of the day elapsed and returns a state:

| State | Ratio | Meaning |
| --- | --- | --- |
| `UNDERPACING` | < 0.85 | Will underspend at this rate |
| `ON_TARGET` | 0.85 – 1.15 | Fine |
| `OVERPACING` | > 1.15 | Ahead of schedule; throttle |
| `LIMITED` | > 1.50 | Far ahead; throttle hard |
| `EXHAUSTED` | budget spent | Stop admitting |

`MIN_ELAPSED_FRACTION = 0.05` suppresses judgement in the first ~72 minutes of
the day. Early in a window the ratio is dominated by noise, and a campaign
throttled at 00:03 for a burst of three impressions never recovers its day.

`MIN_THROTTLE = 0.10` — throttling never goes below 10%. A campaign is never
fully silenced by pacing, so an overcorrection is recoverable within the same
day and an advertiser never sees a total blackout produced by our smoothing.

### Pacing does not touch money

`admits(campaign_id, opportunity_key, throttle)` decides whether a campaign
**enters** the auction. It does not reduce a bid, adjust a budget, or write to a
wallet or ledger.

The distinction matters because it bounds the worst case. A pacing bug produces
under-delivery — recoverable, visible, and the advertiser keeps their money. A
pacing bug with write access to budgets produces incorrect charges, which are
recoverable only through refunds, apologies and an audit.

`admits()` is deterministic in `opportunity_key`, not random, so the same
opportunity yields the same answer on retry and the throttle is stable rather
than jittering per request.

## Frequency capping

`frequency.check()` enforces caps across three scopes × three windows:

- Scopes: `advertiser`, `campaign`, `creative`
- Windows: `session`, `day`, `week`
- Defaults: `DEFAULT_FREQUENCY_CAPS` in `taxonomy.py`

Three scopes are needed because a user who has seen nine different creatives
from one advertiser has been shown one advertiser nine times, and a
creative-only cap reports that as nine healthy first impressions. The
advertiser-level cap is the one users actually feel.

Sliding windows via `WINDOW_SECONDS`, not calendar buckets. Calendar buckets let
a "3 per day" cap deliver six impressions across a midnight boundary, which is
precisely when a user is least receptive to seeing the sixth.

`explain_cap()` and `explain_for_advertiser()` render the same fact for two
audiences: the operator needs the scope and count, the advertiser needs to know
their reach is capped and why their spend stopped.

## Ad load

`frequency.ad_load_permits()` governs how much advertising a session contains,
independent of any advertiser:

| Constant | Value | Meaning |
| --- | --- | --- |
| `MAX_ADS_PER_SESSION` | 12 | Ceiling per session |
| `MIN_ORGANIC_ITEMS_BETWEEN_ADS` | 3 | Spacing |
| `MAX_CONSECUTIVE_ADS` | 1 | Never back-to-back |

This is the control with no advertiser advocate. Every other limit here has
somebody who benefits from raising it; ad load only has users, who express
themselves by leaving — slowly, in a way that shows up as a gradual engagement
decline nobody attributes to ad density.

The constants are in `taxonomy.py` alongside the rest, so raising them is a
reviewable diff rather than a config change.

## Failure directions

| Control | Fails | Because |
| --- | --- | --- |
| Pacing | open (delivers) | Ledger and per-campaign budget still enforce; blocking all delivery on a read error is the larger failure |
| Frequency | closed (suppresses) | An unreadable exposure count means we do not know how many we have shown; showing another is the irreversible option |
| Ad load | closed | Same |
| Account guardrail — ceiling | open | See [`ads_safety_and_rollout.md`](ads_safety_and_rollout.md) |
| Account guardrail — halt | **closed** | A stop a failed query can lift is not a stop |

These are not defaults that happened; each is an `except` clause with the
reasoning written above it, and each is pinned by a test that injects a raising
connection and asserts the direction. Read the comments before changing one —
the reasoning is where the direction came from.

## Interaction

Order at delivery time: eligibility → guardrails → pacing admission → frequency
→ ad load → ranking. Each stage only narrows.

A campaign therefore has several distinct ways to be dark, and
[`ads_advertiser_experience.md`](ads_advertiser_experience.md) covers how the
advertiser is told which one applies. "Your ad is not showing" with no cause is
the single most common advertiser support ticket on every platform, and it is
entirely self-inflicted.
