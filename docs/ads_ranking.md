# Ad selection: eligibility, ranking, exploration

Modules: `ads_intelligence/context.py`, `interest.py`, `performance.py`,
`ranking.py`. The canonical decision is still made by
`services/business_os/advertising/service.py::select_ads`.

## Eligibility comes before ranking, always

Nothing is scored until it is allowed. The order is:

1. **Is an ad permitted here at all?** `context.ad_permitted()` refuses private
   surfaces and sensitive content categories outright, with a reason from
   `REFUSAL_REASONS`. `AD_SUPPORTED_SURFACES` is an allowlist —
   `{feed, reels, explore, search}` — so a new surface is un-monetised until
   somebody deliberately adds it.
2. **Is this campaign eligible?** Canonical checks: review state, account
   verification, policy, budget, wallet, schedule, and now account guardrails.
3. **Have we shown this person too much?** Frequency caps and ad-load.
4. *Only then* is the surviving candidate set ranked.

Ranking a candidate that should not have been shown produces a
beautifully-explained bad decision, and the explanation makes it look
considered. The ordering is what makes an ineligible candidate impossible rather
than merely unlikely to win.

## The ranker is deterministic and explainable

`RANKING_MODE = "intelligence_v1"`. Four components, fixed `WEIGHTS`:

| Component | Source | `NEUTRAL` when unknown |
| --- | --- | --- |
| Context match | `context.match_score()` — exact 1.0 / related 0.5 / neutral 0.25 / none 0.0 | 0.5 |
| Affinity | `interest.affinities_for()`, normalised against `_AFFINITY_CEILING` | 0.5 |
| Quality | `performance.summarise()` — CTR, viewability, negative rate | 0.5 |
| Exploration | Boost for candidates with too little data to judge | — |

Then `FATIGUE_MULTIPLIERS` apply the creative's fatigue state.

Every score carries its components, and `explain_score(scored)` renders a
sentence a human can check. This is not a debugging nicety: an unexplainable
ranker cannot be diagnosed when it misbehaves, and "the model decided" is the
answer that ends every investigation into why a campaign stopped delivering.

**There is no model here.** No fit, no predict, no weights learned from data.
The weights are constants, set by people, changed in commits. When
[`ads_data_quality.md`](ads_data_quality.md)'s gates pass, a learned ranker
becomes a candidate for shadow evaluation against this one — and this one is the
baseline it has to beat, which is the point of building it first.

## Unknown means neutral, not zero

`NEUTRAL = 0.5` is used wherever a signal is missing. A new campaign with no
performance history scores neutral on quality, not zero.

Scoring unknowns as zero makes the system unable to learn anything new: a new
creative never wins an auction, so it never accumulates the data that would let
it win. The result is a platform where incumbency is the strongest ranking
signal, which is invisible in aggregate metrics — they look fine, because the
incumbents are genuinely performing — and slowly kills advertiser acquisition.

`safe_rate(..., min_denominator=N)` returns `None` rather than a rate computed
from too few observations, and `None` becomes neutral. `MIN_IMPRESSIONS_FOR_CTR
= 500`, `MIN_CLICKS_FOR_CVR = 50`, `MIN_IMPRESSIONS_FOR_FATIGUE = 1000`. One
click on three impressions is not a 33% CTR.

## Exploration

`EXPLORATION_FRACTION = 0.10`, `EXPLORATION_MAX_BUDGET_SHARE = 0.20`.

Exploration is bounded on both axes — how often it happens and how much of a
budget it can consume — because unbounded exploration is indistinguishable from
a bug that spends money on bad ads. Bounded, it is the mechanism that stops the
neutral-scoring problem above from being merely theoretical.

## Creative fatigue

`performance.assess_fatigue()` returns one of `INSUFFICIENT_DATA`, `HEALTHY`,
`WEARING`, `FATIGUED`. Triggers: CTR down 30% from baseline (`WEARING`), down
50% (`FATIGUED`), or a negative-feedback rate above
`FATIGUE_NEGATIVE_RATE = 0.02`.

Fatigue demotes; it does not stop delivery. Stopping is the advertiser's
decision, and they are told (see
[`ads_advertiser_experience.md`](ads_advertiser_experience.md)). A platform that
silently stops a campaign it judged tired is a platform whose advertisers
believe their budget vanished.

Note the negative-rate trigger is independent of CTR. A creative can hold its
click rate while annoying a large number of people, and CTR alone will never
show it.

## Interest scoring

`interest.score_events()` applies exponential decay with per-window half-lives:
`{7: 3.0, 30: 14.0, 90: 45.0}` days. Scores are clamped to
`[AFFINITY_MIN=-50, AFFINITY_MAX=100]`.

The negative floor is the load-bearing part. Explicit negatives —
"hide this ad", "report" — push affinity *below* zero rather than to it, so a
category a user has actively rejected is not merely unweighted but suppressed,
and a burst of ambient signals cannot wash the rejection out. `SIGNAL_WEIGHTS`
sets the relative strength, with explicit actions dominating inferred ones.

`explain_affinity()` returns why a category is scored as it is, which is what
[`ads_transparency.md`](ads_transparency.md) is built on.

## Shadow comparison

`ranking.compare(conn, candidates, ...)` scores a candidate set without
affecting delivery. This is how ranker v1 was evaluated against canonical
ordering before any traffic moved, and it is how a v2 would be evaluated
against v1. See the staged rollout in
[`ads_safety_and_rollout.md`](ads_safety_and_rollout.md).
