"""Experiment definitions, and the domains that may never hold one.

The governing constraint on this package is that an experiment must never alter
payment correctness, authentication security, seller authorisation, order
integrity, privacy enforcement, payout correctness or fraud protection. Those
are not properties a variant is allowed to have an opinion about: there is no
acceptable outcome to a test where one arm charges the wrong amount.

How that is enforced, and how far the enforcement reaches
---------------------------------------------------------

Refusal happens at **definition** time, not at evaluation time. A key naming a
protected domain raises rather than returning a safe default, so there is never
a definition object in memory that a later change could start evaluating. A
check at the call site would have to be repeated at every call site, and the
one that gets forgotten is the one that matters.

That is a real guarantee about naming and nothing more, and saying otherwise
would be the defect this codebase keeps producing. Nothing here stops an author
calling an experiment ``checkout_button_colour`` and then branching on the
result to skip a fraud check. What closes that gap is not a string rule but
``tests/pulse_experiments/test_protected_domains.py``, which asserts that no
module owning one of these concerns imports this package at all. A gate that
cannot be reached from the payment path cannot alter it, whatever the
experiment is called.

Definitions live in code
------------------------

There is deliberately no table and no admin form. An experiment is a change to
product behaviour, so it goes through the same review as any other change to
product behaviour. The existing ``feature_flags`` table is a cautionary example
in the opposite direction: it is owner-gated, audit-logged, editable from
``/admin/capability-matrix`` — and nothing reads it for gating, so an operator
can set a feature to ``disabled``, watch the page confirm it, and change
nothing at all. See ``docs/analytics/PULSEEXPERIMENTS_DESIGN_RECORD.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from services.pulse_experiments.assignment import CONTROL

#: Key prefixes an experiment may never occupy. Matched on the dotted segment,
#: so ``payment`` blocks ``payment.retry_copy`` and leaves ``repayment_ui``
#: alone — a substring match would refuse keys that have nothing to do with
#: money and train authors to work around the rule.
PROTECTED_DOMAINS = frozenset(
    {
        "auth",
        "authz",
        "payment",
        "payments",
        "payout",
        "payouts",
        "order",
        "orders",
        "privacy",
        "fraud",
        "seller_authz",
        "entitlement",
        "entitlements",
    }
)

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")

#: A key must be this short to stay readable in a log line, and long enough to
#: describe what is being tested rather than which ticket asked for it.
MAX_KEY_LENGTH = 64


class ExperimentDefinitionError(ValueError):
    """Raised when a definition is malformed or names a protected domain."""


@dataclass(frozen=True)
class ExperimentDefinition:
    """One experiment. Immutable, because assignment must not change mid-request.

    A frozen dataclass rather than a dict so that a definition cannot be edited
    by the code evaluating it. Two reads inside one request returning different
    arms would show a user both sides of the experiment on one page.
    """

    key: str
    description: str
    variants: tuple[tuple[str, int], ...]
    rollout_percent: int = 0
    enabled: bool = False

    def __post_init__(self) -> None:
        _validate_key(self.key)
        if not str(self.description or "").strip():
            raise ExperimentDefinitionError(
                f"{self.key}: a description is required — an experiment whose purpose "
                "is not written down cannot be concluded by anyone but its author"
            )
        _validate_rollout(self.key, self.rollout_percent)
        _validate_variants(self.key, self.variants)

    def variant_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.variants)


def _validate_key(key: str) -> None:
    text = str(key or "").strip()
    if not text:
        raise ExperimentDefinitionError("an experiment key is required")
    if len(text) > MAX_KEY_LENGTH:
        raise ExperimentDefinitionError(
            f"{text[:32]}…: key exceeds {MAX_KEY_LENGTH} characters"
        )
    if not _KEY_RE.match(text):
        raise ExperimentDefinitionError(
            f"{text}: key must be lowercase dotted snake_case, so it is safe in a "
            "log line and cannot collide by case"
        )
    for segment in text.split("."):
        if segment in PROTECTED_DOMAINS:
            raise ExperimentDefinitionError(
                f"{text}: '{segment}' is a protected domain. Payment, auth, "
                "authorisation, order, payout, privacy and fraud behaviour may not "
                "be varied by experiment — there is no acceptable outcome to an arm "
                "that gets these wrong."
            )


def _validate_rollout(key: str, rollout: object) -> None:
    if isinstance(rollout, bool) or not isinstance(rollout, int):
        raise ExperimentDefinitionError(
            f"{key}: rollout_percent must be an int, got {type(rollout).__name__}"
        )
    if not 0 <= rollout <= 100:
        raise ExperimentDefinitionError(
            f"{key}: rollout_percent must be between 0 and 100, got {rollout}"
        )


def _validate_variants(key: str, variants: object) -> None:
    if not isinstance(variants, tuple) or not variants:
        raise ExperimentDefinitionError(f"{key}: at least one variant is required")

    names: list[str] = []
    total = 0
    for entry in variants:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise ExperimentDefinitionError(
                f"{key}: each variant must be a (name, weight) pair"
            )
        name, weight = entry
        name = str(name or "").strip()
        if not name or not _KEY_RE.match(name) or "." in name:
            raise ExperimentDefinitionError(
                f"{key}: variant name {entry[0]!r} must be lowercase snake_case"
            )
        if isinstance(weight, bool) or not isinstance(weight, int):
            raise ExperimentDefinitionError(
                f"{key}: weight for '{name}' must be an int, "
                f"got {type(weight).__name__}"
            )
        if weight < 0:
            raise ExperimentDefinitionError(
                f"{key}: weight for '{name}' must not be negative"
            )
        names.append(name)
        total += weight

    if len(set(names)) != len(names):
        raise ExperimentDefinitionError(f"{key}: variant names must be unique")
    if CONTROL not in names:
        raise ExperimentDefinitionError(
            f"{key}: a '{CONTROL}' variant is required — it is the arm every "
            "failure path falls back to, so it has to be a real member of the set"
        )
    # Integer percentages summing to exactly 100. Floats were not used because
    # three arms of a third each cannot sum to 1.0 in binary, and the shortfall
    # lands on whichever arm happens to be walked last.
    if total != 100:
        raise ExperimentDefinitionError(
            f"{key}: variant weights must sum to 100, got {total}"
        )


@dataclass(frozen=True)
class Registry:
    """The experiments this deployment knows about.

    Lookup of an unknown key returns ``None`` rather than raising. A caller
    asking about an experiment that has been removed should get the product's
    default behaviour, not a 500 — deleting a definition is the intended way to
    end an experiment, and it must not take the surface down with it.
    """

    definitions: dict[str, ExperimentDefinition] = field(default_factory=dict)

    def get(self, key: str) -> Optional[ExperimentDefinition]:
        return self.definitions.get(str(key or "").strip())

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.definitions))


def build(definitions: Iterable[ExperimentDefinition]) -> Registry:
    """A registry, refusing duplicates.

    Two definitions sharing a key is not a merge to resolve: the second would
    silently win depending on import order, so the same subject could be in
    either arm depending on which module was imported first.
    """
    out: dict[str, ExperimentDefinition] = {}
    for definition in definitions:
        if definition.key in out:
            raise ExperimentDefinitionError(
                f"{definition.key}: defined twice"
            )
        out[definition.key] = definition
    return Registry(out)


#: Shipped empty, on purpose. See the design record: at 41 registered users and
#: one distinct viewer in the commerce event log, no experiment defined today
#: could reach a conclusion, so defining one would produce a readout that looks
#: like evidence and is noise. The machinery is here and tested; the first real
#: definition waits for a population that can answer it.
ACTIVE = build(())
