# Measurement: attribution, funnel, invalid traffic

Modules: `ads_intelligence/attribution.py`, `ads_intelligence/invalid_traffic.py`,
plus the funnel query in `performance.py`.

Measurement is where advertising platforms are most tempted to flatter
themselves, because every optimistic choice makes the product look better and
none of them are checkable by the customer. The choices below are deliberately
the conservative ones.

## Last click, and nothing else

```python
ATTRIBUTION_MODEL = "last_click"
VIEW_THROUGH_SUPPORTED = False
```

`CLICK_ATTRIBUTION_WINDOW_HOURS = 168` (7 days).

**View-through attribution is not supported.** This is a constant that other
code reads, not a feature awaiting implementation.

The reason is that view-through credit is unfalsifiable in the direction that
benefits us. If we claim an impression caused a purchase, the advertiser cannot
disprove it, and the metric that results is one we control the size of. Every
platform that has enabled it has grown its reported conversions without growing
anybody's revenue. Last-click is a weaker claim, and it is a claim we can
defend.

`attribute()` credits the most recent click within the window. Multi-touch is
not modelled, because a multi-touch model is a set of assumptions about
causality that we would be choosing, and choosing them to our own advantage is
the failure mode.

## No ROAS we cannot substantiate

Return on ad spend requires knowing revenue attributable to the ad. We know
conversions the advertiser reported to us and clicks we observed. We do not
report a ROAS figure computed from an assumed order value, and we do not
extrapolate.

`explain_attribution()` and `explain_funnel()` state the model in words next to
the number, so the number is never read without its assumptions.

## Funnel

`FUNNEL_STEPS` runs opportunity → served → rendered → viewable → click →
conversion, and `funnel()` returns the count at each step with drop-off between
them. `STEP_DIAGNOSIS` attaches a plain-language cause to each drop.

The value is locating the problem. "Your CTR is low" is ambiguous between six
different failures; "78% of your served ads never became viewable" points at a
placement or a creative weight problem and nothing else. That specificity comes
entirely from having recorded the opportunity, which is why the fabric starts
one step earlier than delivery does.

## Invalid traffic is never billed

The sequence is: **screen, then bill.** Not bill, then credit back.

`invalid_traffic.screen(conn, payload)` runs before an event is billable and
assigns a validity state:

| Rule | Threshold | Catches |
| --- | --- | --- |
| Rapid repeat | > 3 in 60 s | Click spamming |
| Velocity | > 120 in 1 h | Automation |
| Implausible delay | click < 300 ms after render | A click nobody made |

`RULE_STATES` maps each rule to `valid` / `suspect` / `under_review` /
`invalid`, and `is_more_severe()` ensures a later benign rule cannot downgrade
an earlier severe finding.

Credit-back is the industry norm and it is worse in two ways. It requires the
advertiser to trust an adjustment they cannot audit, and it means the money was
briefly ours — which creates a quiet institutional interest in screening
loosely. Screening first removes both.

`sweep()` catches patterns only visible across a window; findings there feed
`credit_candidates()` for the residue that has already been billed, which is the
fallback, not the mechanism.

`summarise()` gives per-campaign invalid rates, surfaced to advertisers.
Invalid traffic is a fact about their campaign — often about a placement or a
source they chose — and hiding it makes the platform look cleaner while leaving
them unable to act.

## Reconciliation

Daily reconciliation compares billable events against ledger entries. A
disagreement is an alert, not an automatic correction: automatic correction
means an accounting bug can quietly rewrite its own evidence. The ledger stays
the money authority in every case (see
[`ads_safety_and_rollout.md`](ads_safety_and_rollout.md)).

## Rates are refused, not approximated

Every rate helper in `performance.py` takes `min_denominator` and returns `None`
below it. `MIN_IMPRESSIONS_FOR_CTR = 500`, `MIN_CLICKS_FOR_CVR = 50`,
`MIN_CONVERSIONS_FOR_OPTIMISATION = 30`, `MIN_SAMPLE_FOR_EXPERIMENT = 200`.

`None` renders as "not enough data yet", never as `0.0%` and never as a
confident-looking number derived from four events. The second is worse than the
first: a wrong number gets acted on, and an advertiser who paused a campaign
because of a 0% CTR computed from nine impressions was actively misled by us.

## Adding a metric

1. Compute it from `ads_intel_events` — never from a derived table, which may
   itself embed an assumption.
2. Give it a minimum denominator and return `None` below it.
3. Write the `explain_*` text at the same time as the number. If it cannot be
   explained in a sentence, it is not ready to be shown.
4. State the attribution model wherever the metric is displayed.
5. If it could ever be read as a return-on-spend claim, do not ship it without
   the data that substantiates it.
