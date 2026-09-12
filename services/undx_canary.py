"""Who gets the new routing first, and how to take it away from them again.

What a canary is here, and what it is not
-----------------------------------------
A canary decides **which people** reach behaviour this mission added. It does
not decide **which provider wins**. That distinction is §1 of the brief in
operational form: no provider is promoted to global default, and a canary that
quietly reordered the lane table for a cohort would be exactly such a promotion
with a smaller blast radius and no benchmark behind it.

So :func:`cohort` answers one question — canary or control — and
:func:`routing_mode` maps that to one of two names. Nothing in this module
knows a provider's name, and `tests/test_undx_canary.py` asserts that by
reading the source: a module that cannot name a provider cannot prefer one.

Internal means named, not sampled
---------------------------------
``UNDX_CANARY_USER_IDS`` is an explicit list. There is no percentage rollout
and no "1% of users" knob, because the brief asks for an *internal* canary and
a random one percent of production is not internal — it is a hundredth of the
customer base finding out about an experiment by being in it. Staff can be
listed; strangers cannot be volunteered.

The consequence is that an empty list means an inert canary, which is the same
shape as `undx_shadow`'s default and is correct for the same reason.

Assignment is stable, and stability is the feature
---------------------------------------------------
:func:`cohort` is a pure function of the user id and the configured list. It
does not sample, does not consult a clock, and does not remember. A user who
is in the canary is in it for every request, including the second turn of a
conversation whose first turn set an expectation.

The alternative — a per-request roll — produces a user who gets agentic
routing for one message and the old path for the next, and the resulting bug
report describes behaviour that no single code path produces. That failure is
extremely expensive to diagnose and trivially avoidable, so the roll is not
offered even as an option.

Fail closed, in the direction that means "less new behaviour"
-------------------------------------------------------------
Every ambiguous input lands on control: no user id, an unparseable id, a
malformed list, the kill switch off. "Fail closed" for a privacy ceiling means
refuse; here it means *do not enrol*, because the risk being managed is
unproven behaviour reaching someone who did not agree to it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: The two routing modes, named so a log line or a dashboard cell is readable
#: without a legend. `control` is the routing that ran before this mission.
MODE_CONTROL = "control"
MODE_CANARY = "canary"
MODES: tuple[str, ...] = (MODE_CONTROL, MODE_CANARY)

#: Why somebody is in control. Typed for the same reason
#: `undx_shadow.SKIP_REASONS` is: "the kill switch is off" and "this user is
#: not on the list" are different operational situations and must not be
#: aggregated into one "not enrolled" count.
CONTROL_REASONS: tuple[str, ...] = (
    "omni_disabled",     # UNDX_OMNI_ROUTER_ENABLED is off; nobody is enrolled
    "canary_disabled",   # UNDX_CANARY_ENABLED is off
    "empty_cohort",      # enabled, but nobody is listed
    "no_user",           # anonymous or unidentified request
    "bad_user",          # a user id that is not an integer
    "not_enrolled",      # the ordinary case: a real user, not on the list
)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _flag(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _flag("UNDX_CANARY_ENABLED", False)


def cohort_ids() -> frozenset[int]:
    """The enrolled user ids, parsed leniently and reported strictly.

    A malformed entry is dropped with a warning rather than taking the whole
    list down. The alternative — refusing the entire cohort because of one
    stray comma — turns a typo into "the canary silently stopped", and a
    canary that is quietly off looks exactly like a canary that is on and
    finding no problems.
    """
    raw = _env("UNDX_CANARY_USER_IDS")
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError:
            log.warning("UNDX canary: ignoring non-numeric user id in "
                        "UNDX_CANARY_USER_IDS")
    return frozenset(ids)


@dataclass(frozen=True)
class Assignment:
    """One user's cohort, and the reason when it is control.

    `reason` is empty exactly when `mode` is canary. A caller logging this can
    therefore always answer "why is this person not in the experiment?"
    without re-deriving it from four environment variables.
    """

    mode: str = MODE_CONTROL
    reason: str = "omni_disabled"

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown canary mode {self.mode!r}")
        if self.mode == MODE_CANARY and self.reason:
            raise ValueError("a canary assignment carries no control reason")
        if self.mode == MODE_CONTROL and self.reason not in CONTROL_REASONS:
            raise ValueError(f"unknown canary control reason {self.reason!r}")

    @property
    def is_canary(self) -> bool:
        return self.mode == MODE_CANARY


def cohort(router: Any, user_id: Any) -> Assignment:
    """Canary or control, for this user, deterministically.

    Pure: same router configuration and same user id, same answer, every time
    and in every one of the nine worker processes. Nothing here samples and
    nothing here reads a clock, so two workers cannot disagree about which
    experiment a user is in — which is a failure mode that produces bug reports
    describing behaviour no single code path produces.
    """
    if not getattr(router, "omni_router_enabled", lambda: False)():
        return Assignment(reason="omni_disabled")
    if not enabled():
        return Assignment(reason="canary_disabled")
    ids = cohort_ids()
    if not ids:
        return Assignment(reason="empty_cohort")
    if user_id is None or (isinstance(user_id, str) and not user_id.strip()):
        return Assignment(reason="no_user")
    try:
        resolved = int(user_id)
    except (TypeError, ValueError):
        return Assignment(reason="bad_user")
    if resolved not in ids:
        return Assignment(reason="not_enrolled")
    return Assignment(mode=MODE_CANARY, reason="")


def routing_mode(router: Any, user_id: Any) -> str:
    """The mode name alone, for callers that do not need the reason."""
    return cohort(router, user_id).mode


def state(router: Any) -> dict[str, Any]:
    """What the canary is doing, without naming who is in it.

    The cohort *size* is published; the ids are not. An operator needs to know
    whether the experiment has anybody in it — an empty canary and a healthy
    one produce identical silence otherwise — and does not need a list of
    colleagues' user ids rendered onto a dashboard that outlives the
    experiment.
    """
    ids = cohort_ids()
    return {
        "omni_router_enabled": bool(
            getattr(router, "omni_router_enabled", lambda: False)()),
        "canary_enabled": enabled(),
        "cohort_size": len(ids),
        # False when the switches are on but nobody is listed, which is the
        # state most easily mistaken for "running fine".
        "has_cohort": bool(ids),
        "modes": list(MODES),
    }
