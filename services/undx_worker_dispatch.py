"""Decide, deterministically, whether an approved action runs in the request or the worker.

This module answers one question — *does this action need to outlive the HTTP request
that asked for it?* — and it answers it from the capability descriptor and the resolved
plan shape. Never from prose.

That constraint is the whole point, so it is worth saying why rather than only stating
it. The alternative design is obvious and tempting: let the planner say
``{"background": true}`` when the request sounds long-running. It would work most of the
time. But "run this in the worker" is not a stylistic preference, it is a decision to
execute an action *after the person has stopped watching*, and a model that can set that
flag is a model that can move any action out of the person's sight by describing it
persuasively. The queue is the one place where nobody is looking, which makes it the one
place a model must not be able to route work into.

So the inputs here are: the registry entry, the number of targets the deterministic
resolver pinned, whether an approval exists, and the flag surface. All four are facts
about the system rather than claims about intent.

**The default is synchronous, and it is a real default rather than a fallback.** A fast
read, a single like, a single watchlist mutation, a preference change — these complete in
milliseconds inside the request. Queueing them would trade a correct immediate answer for
a run row, a poll interval, and a second round trip to learn what already happened. Worse,
it would make the honest status model (``QUEUED`` → ``CLAIMED`` → …) into theatre laid
over work that was never deferred. Deferral has a cost and only earns it when the work
genuinely cannot finish in the request.

**Eligibility is an explicit, server-owned set.** The registry declares risk, confirmation
policy, ownership and verification, but it does not declare duration or step count — and
inventing that metadata for 120 capabilities would mean guessing at 120 latencies. An
enumerated set is auditable in one screenful, grows by deliberate review, and fails in the
safe direction: a capability nobody has considered is synchronous, which is the behaviour
that exists today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from services import undx_agent_policy
from services import undx_agent_runs
from services.undx_agent_contracts import ConfirmationPolicy


#: Capabilities that may be executed by the worker, enumerated rather than inferred.
#:
#: The bar for entry is *this action can take long enough that a person may reasonably
#: close the app before it finishes* — aggregation across an unbounded number of rows,
#: or work whose cost scales with the size of an account rather than with the size of the
#: request. Everything currently listed is read-only, because this mission lands behind a
#: global write stop and adding a write here would enumerate a path that cannot be
#: exercised and therefore cannot be proven.
#:
#: Adding to this set is a deliberate act with a test attached. Removing from it is always
#: safe: the capability reverts to executing in the request, exactly as it does today.
WORKER_ELIGIBLE_CAPABILITIES = frozenset({
    "account.health.summary",
    "activity.daily_summary",
    "ads.performance.summary",
    "creator.analytics.summary",
    "feed.post.performance.summary",
    "notifications.group_summary",
    "profile.activity.summary",
    "reels.performance.summary",
    "search.global",
    "security.activity.summary",
})

#: Reasons, as stable codes. They are logged, surfaced on the run row and asserted on in
#: tests, so they are part of the contract rather than debug strings.
NOT_WORKER_ELIGIBLE = "capability_not_worker_eligible"
RUNS_DISABLED = "agent_runs_unavailable"
WRITES_STOPPED = "writes_stopped_answer_in_request"
AWAITING_CONFIRMATION = "confirmation_required_before_queueing"
LONG_RUNNING_CAPABILITY = "long_running_capability"
BATCH_OVER_MANY_TARGETS = "batch_over_many_targets"


@dataclass(frozen=True)
class Dispatch:
    """Where this action runs, and the stable code saying why."""

    worker_backed: bool
    reason: str

    @property
    def synchronous(self) -> bool:
        return not self.worker_backed


def _sync(reason: str) -> Dispatch:
    return Dispatch(worker_backed=False, reason=reason)


def eligible(capability_id: str) -> bool:
    return str(capability_id or "") in WORKER_ELIGIBLE_CAPABILITIES


def decide(spec, *, resolved_count: int = 1, has_confirmation: bool = False,
           env: Mapping[str, str] | None = None) -> Dispatch:
    """Return where this call should execute.

    ``spec`` is the :class:`~services.undx_capability_registry.CapabilitySpec`.
    ``resolved_count`` is how many targets the *deterministic* resolver pinned from the
    person's own words — not how many the planner suggested. ``has_confirmation`` says
    whether an approval already exists for this exact action.

    Note the argument this function does not take: the person's text. It could not use it
    if it had it, and not receiving it is the cheapest way to keep that true.
    """
    capability_id = str(getattr(spec, "capability_id", "") or "")

    # 1. Deferral requires somewhere to defer to. With runs disabled, a queued action is
    #    a row nothing will ever claim — which reads to the person as an action that was
    #    accepted and then silently never happened. Executing in the request is both the
    #    honest and the working answer.
    limits = undx_agent_runs.surface(env)
    if not limits.enabled:
        return _sync(limits.reason or RUNS_DISABLED)

    # 2. The allowlist, before anything else about this particular call. Order matters:
    #    checking eligibility first means the reasons below are only ever reported for
    #    capabilities somebody deliberately opted in, so a surprising reason code points
    #    at a decision that was made rather than at a default that leaked.
    if not eligible(capability_id):
        return _sync(NOT_WORKER_ELIGIBLE)

    # 3. A write while writes are stopped will be refused by the gateway, and refusing it
    #    here instead means the person is told now, to their face, rather than being told
    #    a job was queued and having to come back to read a failure that was certain at
    #    the moment they asked.
    if getattr(spec, "is_write", False) and not undx_agent_policy.writes_available():
        return _sync(WRITES_STOPPED)

    # 4. Confirm before queueing. This is the ordering the whole envelope depends on: the
    #    request resolves the target, mints the approval and shows the card; only an
    #    action a person has already confirmed becomes worker-managed. An unconfirmed
    #    write returned synchronously reaches the gateway, which mints the card — so this
    #    branch does not lose the action, it routes it back to the human first.
    if (getattr(spec, "is_write", False)
            or getattr(spec, "confirmation", "") == ConfirmationPolicy.ALWAYS):
        if not has_confirmation:
            return _sync(AWAITING_CONFIRMATION)

    # 5. Fan-out. A capability that is worth deferring for one target is more worth
    #    deferring for forty. Escalation is scoped to the eligible set on purpose: a
    #    resolver that pins many targets for a *fast* capability has produced a fast batch,
    #    not a slow one, and forty likes are still forty milliseconds.
    if int(resolved_count or 1) > 1:
        return Dispatch(worker_backed=True, reason=BATCH_OVER_MANY_TARGETS)

    return Dispatch(worker_backed=True, reason=LONG_RUNNING_CAPABILITY)


def describe() -> dict[str, Any]:
    """The dispatch surface, for the health endpoint. Contains no secrets and no user data."""
    return {
        "worker_eligible_count": len(WORKER_ELIGIBLE_CAPABILITIES),
        "worker_eligible": sorted(WORKER_ELIGIBLE_CAPABILITIES),
    }


__all__ = [
    "AWAITING_CONFIRMATION",
    "BATCH_OVER_MANY_TARGETS",
    "Dispatch",
    "LONG_RUNNING_CAPABILITY",
    "NOT_WORKER_ELIGIBLE",
    "RUNS_DISABLED",
    "WORKER_ELIGIBLE_CAPABILITIES",
    "WRITES_STOPPED",
    "decide",
    "describe",
    "eligible",
]
