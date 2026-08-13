# Data quality and ML readiness

The mission principle is **data before AI**. This document is where that
principle is enforced: what "good enough" means, how it is measured, and what is
refused when it is not met.

Modules: `ads_intelligence/events.py` (grading),
`ads_intelligence/ml_readiness.py` (gates).

## Grading, not fixing

Every ingested event gets a `quality_status`. `assess_quality()` may repair
recoverable problems — a missing derived field, a normalisable category — and
when it does, it says so. It never repairs a problem by inventing a value.

The distinction: filling in `event_family` from `event_name` is arithmetic, and
the result is exactly as trustworthy as the input. Filling in a missing
`duration_ms` with a median is a guess wearing the costume of a measurement, and
once stored, nothing downstream can tell the two apart.

`validity` is separate from `quality_status` and answers a different question:
`valid`, `suspect`, `invalid`, `under_review` describe whether we believe a
human caused the event (see [`ads_measurement.md`](ads_measurement.md)).
A perfectly-formed bot click is high quality and invalid.

## The repaired share is a metric

`MAX_REPAIRED_SHARE = 0.10`. Above that, the dataset describes our ingest
problems as much as it describes advertising, and it is not fit to learn from.

This is the check that catches the failure nobody looks for. Totals stay
healthy during a partial outage — the same number of rows arrive, and each one
is individually repairable — so every aggregate dashboard stays green while the
data quietly stops meaning what it used to.

## ML readiness

`ml_readiness.assess(conn, window_days=90)` returns
`{ready, gates, blocking, reason}`. `ready` is True only when **every** gate
passes. There is no partial credit.

| Gate | Threshold | Catches |
| --- | --- | --- |
| `volume` | 50 000 labelled examples | Fitting noise |
| `positive_examples` | 500 clicks | A positive class too small to learn |
| `class_balance` | positive rate ≥ 0.05% | A trainer that learns "always predict no" |
| `time_coverage` | 28 distinct days | Missing weekly seasonality |
| `continuity` | no gap > 2 days | **A logging outage inside the window** |
| `campaign_diversity` | 20 campaigns | Learning a few advertisers, not advertising |
| `data_integrity` | ≤ 10% repaired | Learning our ingest bugs |
| `label_stability` | exactly 1 `processing_version` | **Learning a mid-window rule change** |

The two in bold are the ones normally skipped and the ones most often fatal.

**Continuity.** A two-week logging gap is invisible in every aggregate: totals
look plausible, rates look plausible, and the model learns that a fortnight of
the year has no advertising in it. `_largest_gap_days` looks at the distinct
delivery days directly because no summary statistic will show this.

**Label stability.** If click attribution changed halfway through the window,
the first half of the labels and the second half are different quantities. A
model trained on both learns the rule change, performs beautifully in
backtest, and degrades on contact with a third rule. The check is one query
against `processing_version`, and it is the cheapest expensive-mistake
insurance in the system.

## There is no override

`assess()` takes no `force`, no `skip_gates`, no `min_examples` argument. This
was a deliberate refusal, and the reason is recorded in the module docstring: an
override parameter is how a gate that is inconvenient once becomes a gate that
is always overridden. Six months later nobody remembers which gates are real.

If a threshold is genuinely wrong, change the constant, in a commit, with a
reviewer. That is a slower path on purpose.

Failure to *read* the dataset returns `ready: False, degraded: True` — unknown
is not ready. `explain()` says so in those words, because "we could not check"
and "we checked and it is fine" render identically on a dashboard otherwise.

## Current status

**Nothing is trained. Nothing is predicted.** `assess()` returns
`trains_anything: False`, and there is no `fit`, no `predict`, no model artefact
anywhere in the package. `ranking.py` is a deterministic weighted score with a
human-readable explanation, not a model.

This is the honest status, and `ml_readiness` is what makes it a measured
statement rather than an opinion. The intended sequence is unchanged: gates pass
first, then a training pipeline is written, then it refuses to start when
`ready` is False, then a model is shadowed before it is trusted.

## Rebuilding

Everything derived from the fabric is rebuildable:
`performance.rebuild_creative_day`, `rebuild_campaign_day`,
`interest.rebuild_subject`. If a computation was wrong, fix it and rebuild —
there is no state that has to be migrated in place, which is what makes it safe
to correct a metric rather than living with it.
