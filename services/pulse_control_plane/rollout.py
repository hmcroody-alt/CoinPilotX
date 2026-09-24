"""Percentage rollout, implemented — the column is no longer decorative.

Mission 1 found ``feature_flags.rollout_percentage`` written by the admin form,
persisted, rendered back to the operator, and indexed by
``idx_feature_flags_state`` — then read by nothing. A 0% rollout and a 100%
rollout produced identical verdicts. The choice was to implement it or delete
it, because a control that looks live and is inert is worse than no control: it
invites an operator to "ease a feature out to 5%" and then ships it to
everyone.

This implements it.

Why not call PulseExperiments
-----------------------------

``services/pulse_experiments/assignment.py`` already buckets subjects by
SHA-256 and would have worked. This module reuses the *technique* and
deliberately not the *module*, because the two answer different questions:

* an experiment asks "which of these arms does this eligible user see?"
* a rollout asks "may this user reach the capability at all?"

Making the second depend on the first would mean a capability could not be
exposed unless the experiment registry were healthy — authorisation coupled to
analytics infrastructure. So the ~8 lines of bucketing are duplicated on
purpose, with no import between the packages in either direction.

The salt is a constant, and that is the point
---------------------------------------------

The experiment assigner draws its salt from configuration so a re-randomised
experiment can re-shuffle its population. A rollout must never re-shuffle: the
same salt change would silently swap which customers can use a feature, taking
it away from people who had it while granting it to people who did not. Nobody
would see an error. So :data:`ROLLOUT_NAMESPACE` is fixed in source and has no
environment override.

Monotonicity
------------

Inclusion is ``bucket < percentage * 100`` over 10,000 fixed buckets, with the
bucket depending only on the capability key and the subject. The consequences
are the ones an operator actually needs:

* **Widening never drops anyone.** Raising 10 → 25 keeps every subject under
  1,000 and adds those in [1,000, 2,500). Nobody loses access to a feature they
  were already using, which is the failure that makes staged rollouts feel
  unsafe.
* **Narrowing is defined and reversible.** Lowering 25 → 10 removes exactly the
  subjects in [1,000, 2,500), and restoring 25 restores exactly those subjects.
* **0 means nobody. 100 means everybody the eligibility policy already
  admitted** — rollout narrows, it never widens, so it cannot grant access that
  an entitlement check refused.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

#: Fixed. See the module docstring: this is an access-control salt, not an
#: experiment salt, and rotating it would silently redistribute access.
ROLLOUT_NAMESPACE = "pulse_control_plane.rollout.v1"

#: 10,000 buckets gives rollout percentages one decimal place of headroom
#: without changing the stored integer column.
BUCKETS = 10_000


def subject_key(subject: Any) -> str:
    """The stable string a subject hashes under, or ``""`` when unusable.

    ``bool`` is rejected before ``int`` because ``True`` is ``1`` in Python: a
    caller passing a flag where a user id belongs would otherwise be bucketed as
    user 1 — and on this platform user 1 is the only seller, so the mistake
    would be invisible in aggregate and wrong in exactly the account that
    matters.
    """
    if subject is None or isinstance(subject, bool):
        return ""
    if isinstance(subject, int):
        return str(subject) if subject > 0 else ""
    return str(subject).strip()


def bucket_of(capability_key: str, subject: Any) -> Optional[int]:
    """The subject's fixed bucket in ``[0, BUCKETS)``, or ``None`` if unusable.

    Depends only on the capability key and the subject, so the same subject
    occupies the same bucket in every process, on every deploy, forever. It does
    *not* depend on the percentage — which is what makes widening additive.
    """
    key = subject_key(subject)
    if not key:
        return None
    raw = f"{ROLLOUT_NAMESPACE}:{capability_key}:{key}"
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % BUCKETS


def in_rollout(capability_key: str, subject: Any, percentage: int) -> bool:
    """Whether this subject falls inside the rollout. Fails closed.

    An unusable subject (``None``, ``0``, ``True``, empty string) is **excluded**
    from any partial rollout rather than being bucketed at zero. Bucketing an
    anonymous or malformed subject at a constant would put every such request in
    the same bucket, so a 1% rollout would either admit all of them or none —
    and "all of them" is the kind of accident that reads as a successful launch.
    """
    try:
        pct = int(percentage)
    except (TypeError, ValueError):
        return False
    if pct <= 0:
        return False
    if pct >= 100:
        return True
    bucket = bucket_of(capability_key, subject)
    if bucket is None:
        return False
    return bucket < pct * (BUCKETS // 100)


class RolloutRefused(ValueError):
    """Raised when a rollout is attempted on a protected capability."""


def assert_rollout_allowed(capability_key: str, protected: tuple[str, ...], percentage: int) -> None:
    """Refuse partial rollout of a protected capability (Stage 29).

    A percentage rollout of a payment, auth or privacy path is not a cautious
    launch — it is a hash function deciding which customers may pay, which
    sessions authenticate, or whose data is covered. The remainder do not see a
    "not yet available" state; they see a broken checkout.

    So the refusal is a raise, not a clamp. Clamping to 100 would silently do
    the right thing and teach the operator that the control works.
    """
    if protected and int(percentage) != 100:
        raise RolloutRefused(
            f"{capability_key!r} touches protected domain(s) {', '.join(protected)} and "
            f"cannot be rolled out to {percentage}%. Deciding by hash which customers may "
            "complete a payment is an outage for the remainder, not a rollout."
        )
