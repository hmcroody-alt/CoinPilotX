# Referral fraud controls

The program pays cash for referrals, so it will be attacked. It is also used by
families, roommates, classmates and colleagues, and by everyone behind a
carrier-grade NAT. Both facts have to be true at once.

## Signals are not verdicts

`qualification.set_risk_state` is the only automated path into risk, and note
what it **cannot** do:

* It cannot mark anything `QUALIFIED`. An automated signal may pause a reward;
  it may never award one.
* Its normal effect is `REVIEW_REQUIRED` — reversible, human-resolvable, and
  worth zero in the meantime rather than negative.
* `DISQUALIFIED` from risk requires `risk_state == blocked`, which means a
  confirmed block, not a score crossing a line.

Independently, `_standing()` disqualifies only on confirmed decisions about the
account: deletion, or a suspension/ban an operator or the safety system actually
applied.

## Shared networks are not fraud

A shared IP, a shared device, a household, an office, a school or a CGNAT pool
is **normal** and must never by itself cost someone a reward. This is stated in
product copy too — `progress.howItWorks.fairness` and
`progress.faq.canFamilyParticipate` say so in all eleven languages, because a
rule users cannot see is a rule they will assume we broke.

Correlation may raise a review. It may not decide one.

## What actually resists a farm

Not the risk score — the qualification bar:

* **Two separate UTC posting days.** A farm has to hold accounts alive across a
  day boundary and produce authored content on each, which defeats
  create-thirty-accounts-in-an-hour outright.
* **Reposts excluded.** The cheapest possible fake activity is worth nothing.
* **One referrer per referred user**, enforced by a UNIQUE index, so a signup
  cannot be sold twice.
* **Profile completion and good standing**, read from canonical tables at
  evaluation time, so an account cleaned up later stops counting.
* **Re-evaluation from source on every event**, so a qualification that stops
  being true stops counting without anyone running a script.

A duplicate/scripted-burst detector that mislabels a family is worse than no
detector, because it produces confident wrong answers about people who did
nothing wrong. The structural bar produces the same protection with no false
accusations.

## Sentinel

Sentinel is the canonical abuse system; Progress OS does not build a second one.

* Sentinel (or an operator) calls `set_risk_state` to attach an advisory state.
* Admin decisions mirror **to** Sentinel via `services.sentinel.events.ingest`,
  best-effort. The local audit row is written first and unconditionally, so
  losing the mirror never loses the record.

## Raw signals never reach a client

`progress_api` returns a state and a human summary, nothing more granular. No
risk scores, no IP or device facts, no Sentinel output, no fraud state. A member
under review is told they are under review and that it usually resolves on its
own — which is true, and is all they can act on. Publishing the signal would
both leak the detection method and invite argument about a number that is
advisory by design.

Referred users are identified to the client only by `ref`, an opaque
viewer-bound token. Their Pulse IDs are never sent to the app, so there is no
internal identifier for a screen to leak into a header, a share sheet or a crash
report.

## The event log

`progress_events` carries both the audit trail and the user-facing activity feed,
separated by a `visibility` column. One log means a user-visible claim and the
audit record can never disagree about what happened. Writing an event is
best-effort and never fails the decision that produced it — the log is an
observability aid, not a participant.
