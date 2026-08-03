# ADR-0007 — Reconciliation and background-job standard

Status: Accepted
Owner: Platform services
Date: 2026-08-01

## Context

The Business OS is acquiring work that happens away from the user: the
`conversation_domain` backfill, the entity-graph migration under ADR-0001,
payout settlement, advertising spend reconciliation, and whatever the review's
deferred risk platform eventually attaches.

None of these has a shared shape today. A job that fails has no agreed way to be
noticed, no agreed way to be retried, and no agreed way for anyone to find out
it failed other than a seller reporting that a number looks wrong. For anything
touching money that is not acceptable, and the ADR-0001 migration is explicitly
expected to produce imperfect results that need a durable correction path rather
than a one-shot script.

## Decision

Every background job carries the same seven properties.

**A job ID**, stable and quotable, so a failure can be named in a support
conversation and traced without a database query.

**Idempotency**, so that running a job twice produces the same result as running
it once. This is the property that makes retry safe, and without it retry is a
way to double-charge someone.

**A retry policy** with a bounded number of attempts and a backoff, declared by
the job rather than assumed by the runner.

**A dead-letter destination** for work that exhausted its retries. Exhausted
work is not discarded and not silently dropped; it lands somewhere a human can
see it, with enough context to decide what to do.

**An audit event** naming what changed, when, and — per ADR-0006 — on whose
behalf, so that a business with more than one operator has a record of who
caused what.

**Admin visibility.** Job state is inspectable without a deployment. If the only
way to learn that the backfill stalled is to read logs on a server, the backfill
will stall unnoticed.

**A reconciliation pass** for any job whose result is a number a seller sees.
The pass re-derives the number independently and reports disagreement rather
than silently correcting it, because a silent correction destroys the evidence
that something was wrong.

Money jobs inherit the Payments mission's rules on top of these and remain
stricter. Nothing in this ADR permits a money figure to be computed
client-side, cached, or displayed without its source.

## Consequences

The `conversation_domain` backfill (Tier 0.4, Phase 1) is the first job built to
this standard, which is deliberate — it is a large, resumable, idempotent
migration over a table the whole messaging separation depends on, and it is a
good forcing function for the runner.

The ADR-0001 entity migration follows and needs the reconciliation pass most,
since it is expected to produce imperfect product de-duplication that will be
corrected over time rather than at once.

Existing ad-hoc background work should be inventoried and migrated, but that is
a follow-up rather than a precondition; the standard applies to new jobs
immediately and to existing ones as they are touched.

## Open question

Where admin visibility lives. An internal dashboard is the obvious answer and
does not exist; surfacing job state through the existing admin surfaces is
cheaper but couples job infrastructure to a product surface. The decision is
deferred to whoever owns platform services, with the constraint recorded here
that "visible in logs" does not satisfy this ADR.
