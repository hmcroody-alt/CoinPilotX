# Transparency: "why am I seeing this ad?"

Module: `services/business_os/ads_intelligence/transparency.py`.
Endpoints: `GET /api/business-os/ads-intel/why/<decision_id>` and
`GET /api/business-os/ads-intel/my-interests`.

## Explanations are read, never reconstructed

`transparency.explain()` loads the recorded row from
`ads_intel_delivery_decisions`. It does not re-run the ranker.

A reconstructed explanation is a fresh computation over today's data describing
a decision made with yesterday's. It will usually agree, and when it disagrees
it produces a confident, plausible, false account of why somebody saw an ad —
which is worse than no explanation, because it is quotable. Recording the
decision at the time is the only version that is actually an explanation.

This is also why `decisions.py` is the sole writer of that table and records
no-fills as well as fills: the explanation surface can only be as good as what
was captured.

## One viewer cannot read another's explanation

The lookup is:

```sql
WHERE decision_id = ? AND subject_ref = ?
```

`subject_ref` is derived server-side from the authenticated viewer, and there is
no parameter for it. A decision belonging to somebody else returns a
byte-identical response to a decision that never existed — the same `_not_found()`
object, not two different errors.

Distinguishing "not yours" from "does not exist" leaks the existence of a
decision, and with a guessable id that is a probe. Making the two responses
identical is a stronger property than an authorisation check, because there is
no branch to get wrong. A test asserts `theirs == never_issued` directly.

## What an explanation contains

Components above `MIN_COMPONENT_SHARE = 0.15` of the score, in plain language,
plus the controls that act on them.

Small components are omitted deliberately. Listing every input that contributed
2% produces a page that is technically complete and practically unreadable, and
an explanation nobody finishes reading has failed at its only job. The three
things that actually decided it is the honest summary.

## Interest disclosure

`GET /api/business-os/ads-intel/my-interests` returns **categories only** —
never scores, never signal counts, never the events behind them.

A score is a lever: it lets somebody test what moves it, which turns the
transparency surface into an instrument for probing the interest model. A
category is an answer to the question actually being asked. `known_category()`
echoes only members of `taxonomy.INTEREST_CATEGORIES`, so no free-text value can
be reflected back through this endpoint.

Because the category list is closed and excludes sensitive verticals (see
[`ads_privacy.md`](ads_privacy.md)), this endpoint cannot disclose a health,
political or sexuality inference — there is no such inference to disclose.

## Controls that do something

The `CONTROLS` tuple maps each explanation component to a control, and each
control writes an **explicit negative event**.

Explicit negatives are weighted decisively in `interest.py` and can push
affinity below zero (`AFFINITY_MIN = -50`). So "show me fewer ads about this"
suppresses the category rather than nudging a weight that ambient browsing
signals restore within a day.

A transparency surface whose controls have no measurable effect is worse than
none: it converts a user's genuine objection into a click that made them feel
heard and changed nothing, and they find out by continuing to see the ad.

## Working on this

- Do not add a `subject_ref` or `user_id` parameter to these endpoints. The
  tests assert its absence via `inspect.signature`.
- Do not differentiate "not found" from "not yours".
- Do not return raw scores.
- Do not reconstruct an explanation when the recorded row is missing. Say it is
  unavailable — the missing row is itself a bug worth seeing.
- If you add a ranking component, add its control at the same time. A component
  that influences delivery but has no user control is a component the user
  cannot object to.
