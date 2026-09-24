"""Which arm a subject is in, computed rather than remembered.

Assignment is a pure function of ``(salt, experiment_key, subject)``. Nothing is
written down, and that is the design rather than an optimisation.

Production already holds 111 event and audit tables, 62 of them empty, and the
problem PulseAnalytics was built to address is that none of them joins anything
to anything. An ``experiment_assignments`` table would have been the 112th, and
it would have carried a row per subject per experiment saying what this module
can recompute exactly from three inputs. A stored assignment can also drift from
the code that produced it — the table says ``treatment`` while the current
weights say ``control`` — and then neither is authoritative.

So the arm is derived on every read. The cost is that changing the salt or the
weights reshuffles history, which is a real cost and is why
:func:`config.assignment_salt` is a deployment setting rather than a form field.

Two independent hashes, not one
-------------------------------

A subject's eligibility bucket and their variant bucket are hashed under
different namespaces. Using one bucket for both is the classic error: the arm
boundaries would sit inside the rollout range, so widening a rollout from 10% to
20% would slide every boundary and **reassign subjects already in the
experiment** — silently invalidating the comparison it was widened to
strengthen. With two hashes, widening a rollout only admits new subjects and
everyone already inside keeps the arm they had.

Failure is always toward control
--------------------------------

An unusable subject, an unknown experiment, a malformed definition and the
global kill switch all resolve to ``control``. Control is the product's
behaviour before anyone ran an experiment, so every failure mode in this module
returns the user to the path that was already shipped and tested.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Optional

from services.pulse_experiments import config

#: Bucket resolution. Ten thousand rather than a hundred so a rollout can be
#: expressed to a hundredth of a percent — at a population where 1% is a
#: fraction of one person, the coarser space would round every small rollout to
#: nothing or to everything.
BUCKETS = 10000

CONTROL = "control"

#: Namespaces keeping the two hashes independent. See the module docstring.
_ROLLOUT_NS = "rollout"
_VARIANT_NS = "variant"


@dataclass(frozen=True)
class Assignment:
    """One subject's arm, and why.

    ``reason`` exists so a support question — "why did this user see the old
    checkout?" — has an answer that does not require re-deriving the hash by
    hand. It is the field that separates "assigned to control by the weights"
    from "sent to control because the kill switch is off", which are identical
    in ``variant`` and completely different in meaning.
    """

    experiment_key: str
    variant: str
    in_experiment: bool
    reason: str
    bucket: Optional[int] = None


def _subject_key(subject: Any) -> str:
    """The stable string a subject hashes under, or ``""`` if unusable.

    ``bool`` is rejected before ``int`` because ``True`` is ``1``: a caller
    passing a flag where an id belongs would otherwise be bucketed as user 1,
    quietly, and user 1 is this platform's only seller.
    """
    if subject is None or isinstance(subject, bool):
        return ""
    if isinstance(subject, int):
        return str(subject) if subject > 0 else ""
    text = str(subject).strip()
    return text


def _bucket(namespace: str, experiment_key: str, subject_key: str) -> int:
    raw = f"{config.assignment_salt()}:{namespace}:{experiment_key}:{subject_key}"
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % BUCKETS


def _variant_for(bucket: int, variants: list[tuple[str, int]]) -> str:
    """Walk the cumulative weight space. Weights are percent, scaled to buckets.

    The walk is over a sorted, explicit list rather than a dict ordering so the
    same weights always produce the same boundaries. A dict that happened to
    iterate differently between two processes would put one subject in two arms
    depending on which worker served them.
    """
    ceiling = 0
    for name, weight in variants:
        ceiling += weight * (BUCKETS // 100)
        if bucket < ceiling:
            return name
    return CONTROL


def assign(definition: Any, subject: Any) -> Assignment:
    """The arm for one subject under one experiment definition.

    Deterministic: the same three inputs give the same answer in every process,
    on every host, for as long as the salt holds.
    """
    key = getattr(definition, "key", "") if definition is not None else ""
    if not key:
        return Assignment("", CONTROL, False, "no_definition")

    if not config.enabled():
        return Assignment(key, CONTROL, False, "globally_disabled")

    if not getattr(definition, "enabled", False):
        return Assignment(key, CONTROL, False, "experiment_disabled")

    subject_key = _subject_key(subject)
    if not subject_key:
        # Anonymous or malformed. Not bucketed at all: hashing ``""`` would put
        # every such caller in one arm together, which looks like a cohort and
        # is really a bug reported as a result.
        return Assignment(key, CONTROL, False, "no_subject")

    rollout = getattr(definition, "rollout_percent", 0)
    rollout_bucket = _bucket(_ROLLOUT_NS, key, subject_key)
    if rollout_bucket >= rollout * (BUCKETS // 100):
        return Assignment(key, CONTROL, False, "not_in_rollout", rollout_bucket)

    variant_bucket = _bucket(_VARIANT_NS, key, subject_key)
    variant = _variant_for(variant_bucket, list(getattr(definition, "variants", ())))
    return Assignment(key, variant, True, "assigned", variant_bucket)
