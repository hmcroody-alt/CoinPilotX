"""Strict parsing. Ambiguity narrows access; it never widens it.

The behaviour this replaces
---------------------------

``feature_flag_engine.normalize_state`` used to fall back to ``beta``, which
grants ``visible`` and ``usable``. So *every* unrecognised input — a typo, an
empty string, ``None``, a value from a future schema, a truncated write —
resolved to full public access. On a page whose only purpose is restricting
public exposure. The most plausible operator slip, ``"internal only"`` with a
space where the hyphen belongs, published the feature the operator was trying
to hide.

That has since been fixed at the source: the legacy engine now falls back to
``disabled`` and refuses an unrecognised word outright on the way *in*, via
``feature_flag_engine.state_for_write``. The two modules agree on polarity for
the first time.

They are still not the same thing, and this one is not redundant. The legacy
engine coerces, because it must answer for a word already sitting in a column;
this module *raises*, because nothing downstream of it has a stored value to
be compatible with. Coercion silently turns a bad input into a decision, which
is the right trade only when there is no alternative — and here there is.

The rule here
-------------

**A value that cannot be resolved exactly denies.** Not "denies unless it looks
close to something permissive" — denies. Normalisation is limited to
differences that cannot change meaning: surrounding whitespace, letter case,
and the choice of ``-``/``_``/space as a word separator. Nothing is spell-
corrected, nothing is prefix-matched, nothing falls back to a default.

Why ``internal-only`` is rejected as a deployment state
-------------------------------------------------------

It is not a deployment state. It is an *eligibility* — and under the two-axis
model in :mod:`services.pulse_control_plane.model` the two are parsed by
different functions against different vocabularies. Feeding ``internal-only``
to :func:`parse_deployment_state` returns ``UNKNOWN`` (which denies), and
feeding it to :func:`parse_eligibility_policy` returns ``INTERNAL_ONLY``.

That is not pedantry about names. The legacy vocabulary mixed both axes into
one column, which is exactly how ``admin_command`` came to be stored as
``enabled`` — the single state meaning "unconditionally visible and usable" —
while sitting behind 199 admin-guarded routes. Splitting the vocabularies makes
that particular sentence impossible to write.

The legacy words are therefore **not** accepted at runtime. ``enabled``,
``beta``, ``premium-only`` and ``owner-only`` all resolve to ``UNKNOWN`` here.
They are translated exactly once, with evidence, by
:mod:`services.pulse_control_plane.migration`, and never again.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from services.pulse_control_plane.model import (
    DEPLOYMENT_STATES,
    ELIGIBILITY_POLICIES,
    EligibilityPolicy,
)

log = logging.getLogger(__name__)

#: A token is letters, in groups separated by a single run of ``-``, ``_`` or
#: whitespace. Digits and punctuation are not accepted: no current state name
#: contains them, and admitting them would mean guessing at what a stray
#: character was meant to be.
_TOKEN = re.compile(r"^[A-Za-z]+(?:[-_\s]+[A-Za-z]+)*$")


@dataclass(frozen=True)
class ParseResult:
    """A parse outcome that carries why it came out that way.

    ``accepted`` is deliberately separate from ``value``: a rejected parse still
    returns a usable (closed) value, and a caller that checked only the value
    would never learn that the stored configuration is broken. Drift detection
    reads ``accepted``; evaluation reads ``value``. Both are needed.
    """

    accepted: bool
    value: Any
    raw: Any
    reason: str


def _canonicalise(raw: Any) -> Optional[str]:
    """Fold only the differences that cannot alter meaning."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or not _TOKEN.match(text):
        return None
    return re.sub(r"[-_\s]+", "_", text).upper()


def parse_deployment_state(raw: Any) -> ParseResult:
    """Resolve a stored deployment state. Unresolvable input yields ``UNKNOWN``.

    ``UNKNOWN`` is not a sentinel to be tidied away later — it is a real state
    that :func:`services.pulse_control_plane.model.evaluate` denies on, with
    reason ``UNRESOLVABLE_STATE``. That is the whole fix.
    """
    canonical = _canonicalise(raw)
    if canonical is None:
        log.warning(
            "control_plane.state_unparseable raw=%r -> UNKNOWN (denies)", raw
        )
        return ParseResult(False, "UNKNOWN", raw, "not a parseable state token")
    if canonical not in DEPLOYMENT_STATES:
        # Named separately from the branch above because this is the case that
        # matters operationally: somebody wrote a real-looking word that this
        # vocabulary does not contain. Legacy values land here too, and should.
        log.warning(
            "control_plane.state_unrecognised raw=%r canonical=%r -> UNKNOWN (denies)",
            raw,
            canonical,
        )
        return ParseResult(
            False, "UNKNOWN", raw, f"{canonical!r} is not a deployment state"
        )
    return ParseResult(True, canonical, raw, "recognised")


def parse_eligibility_policy(raw: Any) -> ParseResult:
    """Resolve a stored eligibility policy.

    An unresolvable policy does **not** fall back to ``STANDARD``. Defaulting an
    unreadable restriction to "everyone" is the same mistake as defaulting an
    unreadable state to ``beta``, one axis over. The caller receives
    ``accepted=False`` and :func:`parse_stored_row` turns the whole capability
    ``UNKNOWN``, which denies.
    """
    canonical = _canonicalise(raw)
    if canonical is None:
        return ParseResult(False, None, raw, "not a parseable policy token")
    policy = ELIGIBILITY_POLICIES.get(canonical)
    if policy is None:
        log.warning(
            "control_plane.policy_unrecognised raw=%r canonical=%r -> unresolved (denies)",
            raw,
            canonical,
        )
        return ParseResult(False, None, raw, f"{canonical!r} is not an eligibility policy")
    return ParseResult(True, policy, raw, "recognised")


def parse_rollout(raw: Any) -> ParseResult:
    """Resolve a rollout percentage. Anything uninterpretable means **0**.

    Zero, not 100. A rollout value that cannot be read is a configuration fault,
    and the safe reading of a faulty exposure control is "expose nobody" — the
    same polarity as every other decision in this module. Clamping silently to
    100 would turn a corrupt integer into a full public launch.
    """
    if isinstance(raw, bool) or raw is None:
        return ParseResult(False, 0, raw, "missing or non-numeric rollout")
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        # ``OverflowError`` is the interesting one: ``int(float("inf"))`` raises
        # it, and an exception escaping a parser whose whole contract is "fails
        # closed" is the single outcome that is neither open nor closed — the
        # caller gets a traceback instead of a denial. ``nan`` already landed in
        # ``ValueError`` and denied correctly, which is exactly why the gap was
        # easy to miss: the two adjacent float edge cases behaved differently.
        log.warning("control_plane.rollout_unparseable raw=%r -> 0 (denies)", raw)
        return ParseResult(False, 0, raw, "non-numeric rollout")
    if not 0 <= value <= 100:
        log.warning("control_plane.rollout_out_of_range raw=%r -> 0 (denies)", raw)
        return ParseResult(False, 0, raw, "rollout outside 0-100")
    return ParseResult(True, value, raw, "in range")


@dataclass(frozen=True)
class StoredRow:
    """A row from ``feature_flags`` resolved against the canonical model.

    ``faults`` is the list of parse failures. A row with faults is still
    usable — it evaluates to a denial — but the drift detector reports it, so a
    broken row is loud rather than quietly restrictive.
    """

    capability_key: str
    deployment_state: str
    eligibility: Optional[EligibilityPolicy]
    rollout_percentage: int
    faults: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.faults


def parse_stored_row(row: dict) -> StoredRow:
    """Resolve one stored row, failing closed on any unreadable part.

    A row whose eligibility cannot be resolved is forced to ``UNKNOWN`` rather
    than kept at its stated deployment state. Otherwise a row reading
    ``LIVE_GLOBAL`` with a corrupt policy column would stay globally live — the
    corruption would have removed the restriction rather than triggering one.
    """
    key = str(row.get("feature_key") or row.get("capability_key") or "").strip()
    state_result = parse_deployment_state(row.get("deployment_state") or row.get("state"))
    policy_result = parse_eligibility_policy(row.get("eligibility_policy"))
    rollout_result = parse_rollout(row.get("rollout_percentage"))

    faults: list[str] = []
    if not key:
        faults.append("missing capability key")
    if not state_result.accepted:
        faults.append(f"state: {state_result.reason}")
    if not policy_result.accepted:
        faults.append(f"eligibility: {policy_result.reason}")
    if not rollout_result.accepted:
        faults.append(f"rollout: {rollout_result.reason}")

    state = state_result.value
    if not policy_result.accepted or not key:
        state = "UNKNOWN"

    return StoredRow(
        capability_key=key,
        deployment_state=state,
        eligibility=policy_result.value,
        rollout_percentage=rollout_result.value,
        faults=tuple(faults),
    )
