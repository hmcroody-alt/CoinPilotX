"""The classification vocabulary, and the one rule that does the real work.

A control point is classified by what it *does*, never by what it is named or
what value it stores. The distinction matters because this codebase is full of
variables whose names are confident and whose effect is nil:
``ENABLE_TELEGRAM=true`` and ``ENABLE_SMS=true`` are both set in production and
neither is read anywhere in the source tree. Classifying those as "enabled"
because they say ``true`` would put two lies into the inventory on day one.

Hence the governing rule:

    **A control point with no reader is DEAD, whatever its value.**

Everything else follows from reader count plus observed value plus the default
that applies when the variable is unset.

Why the default matters as much as the value
--------------------------------------------

Of the gate-shaped environment variables the backend reads, 33 are not set in
production at all, so their *default* is the production behaviour. Those
defaults are not uniform and the difference is load-bearing:

* every ``BUSINESS_OS_*`` gate defaults to ``""`` and tests for membership in a
  truthy set, so unset means **off** — it fails closed;
* ``PUSH_BADGE_ENABLED``, ``PG_LOCK_ALERT_ENABLED`` and
  ``EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED`` default to ``"1"`` and test for
  membership in a *falsy* set, so unset means **on** — they fail open.

An inventory that recorded only "unset" for both groups would be describing two
opposite production behaviours with one word.

The contrast with the database plane
------------------------------------

``feature_flag_engine.normalize_state`` *used to* return ``"beta"`` for any value
it did not recognise, and ``evaluate_flag`` grants ``beta`` both ``visible`` and
``usable``. So the database plane failed **open**, on a page whose whole purpose
is restricting public exposure: a state misspelled ``"disabeld"`` in the admin
form silently became fully available rather than erroring. The environment plane
mostly fails closed. Reconciling the two planes therefore could not be a value
copy; it was a polarity change, which is why this package described before it
wired.

That one has since been closed — unrecognised now resolves to ``disabled`` —
but the observation above is kept rather than deleted, because it is the reason
this module exists and because **the planes still do not agree in general**. The
env gates split into fail-closed and fail-open groups (see above), and no single
fix to the database plane changes that. "Both planes fail closed" would be a
tidier sentence and a false one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: The eight buckets every control point resolves to.
#:
#: ``UNKNOWN`` is a real answer and not a placeholder to be tidied away. A
#: control point reaches it when the evidence is contradictory — for example a
#: gate with readers whose production value cannot be observed from outside the
#: container. Forcing such a row into ``DISABLED`` or ``LIVE_GLOBAL`` to make
#: the table look complete is how a control plane starts lying again.
CLASSIFICATIONS = (
    "LIVE_GLOBAL",
    "LIVE_CONDITIONAL",
    "INTERNAL_ONLY",
    "EXPERIMENTAL",
    "DISABLED",
    "LEGACY",
    "DEAD",
    "UNKNOWN",
)

#: Values this codebase treats as true. Collected from the gates themselves
#: rather than invented: ``bot.py`` and ``services/`` consistently test
#: membership in some subset of these.
TRUTHY = frozenset({"1", "true", "yes", "on"})

#: Values this codebase treats as false.
FALSY = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class Classification:
    """A verdict plus the reasoning that produced it.

    ``evidence`` is not decoration. Every row in this inventory exists because
    an earlier inventory asserted a state with no evidence and was believed for
    four months, so a verdict that cannot say why it holds is not an
    improvement on the thing it replaces.
    """

    key: str
    verdict: str
    effective: Optional[bool]
    evidence: str

    def __post_init__(self) -> None:
        if self.verdict not in CLASSIFICATIONS:
            raise ValueError(f"unknown classification {self.verdict!r} for {self.key!r}")


def is_truthy(raw: Optional[str]) -> bool:
    return str(raw or "").strip().lower() in TRUTHY


def is_falsy(raw: Optional[str]) -> bool:
    return str(raw or "").strip().lower() in FALSY


def classify_env_gate(
    key: str,
    *,
    observed: Optional[str],
    default: Optional[str],
    reader_count: int,
    fails_open: bool = False,
) -> Classification:
    """Classify one environment-variable gate.

    ``observed`` is ``None`` when the variable is not set in the environment
    being described, which is different from being set to an empty string — the
    first falls through to ``default``, the second is an explicit empty value
    that most gates here read as false.

    ``fails_open`` records the polarity of the *call site*, not of the value. A
    gate written ``getenv(K, "1") not in FALSY`` is open by default; one written
    ``getenv(K, "") in TRUTHY`` is closed by default. Two gates can share a
    default of ``""`` and still disagree about what that means, so polarity has
    to be carried explicitly rather than inferred from the default string.
    """
    if reader_count <= 0:
        return Classification(
            key=key,
            verdict="DEAD",
            effective=None,
            evidence=(
                f"set to {observed!r} in the environment but read by no code. "
                "A value nothing reads cannot control anything."
                if observed is not None
                else "no readers and not set anywhere."
            ),
        )

    raw = observed if observed is not None else default
    source = "observed" if observed is not None else "default (variable unset)"

    if raw is None:
        return Classification(
            key=key,
            verdict="UNKNOWN",
            effective=None,
            evidence=f"{reader_count} reader(s), no value and no literal default at the call site.",
        )

    if is_truthy(raw):
        effective = True
    elif is_falsy(raw) or str(raw).strip() == "":
        # An empty string is only "off" for a closed-polarity gate. For an
        # open-polarity gate the empty string never reaches the comparison as a
        # negative, because the test is membership in the falsy set.
        effective = bool(fails_open) and str(raw).strip() == ""
    else:
        return Classification(
            key=key,
            verdict="UNKNOWN",
            effective=None,
            evidence=f"{source} value {raw!r} is neither truthy nor falsy for this gate.",
        )

    verdict = "LIVE_GLOBAL" if effective else "DISABLED"
    polarity = "fails open" if fails_open else "fails closed"
    return Classification(
        key=key,
        verdict=verdict,
        effective=effective,
        evidence=f"{reader_count} reader(s); {source} = {raw!r}; {polarity}.",
    )
