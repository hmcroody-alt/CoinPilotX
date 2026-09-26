"""``surface_widenings`` must name what got wider, and must not fire on narrowing.

Split out of ``test_authorization_surface.py``, which is quarantined. Every test
in that file calls ``registry.authorization_surface()``, and on ``main`` that
raises ``AuthorizationRecordConflict``: 26 registered capabilities have no record
in ``undx_knowledge_map.BY_ID``. So all 8 of its tests die on the same line,
including the ones whose subject has nothing to do with the missing records —
and the widening detector, which is the half that actually guards something, has
been dark the whole time. See #38 for why the records are missing (they were
written and reviewed in ``bd6333f69``, then dropped by merge ``c1623b2fe``) and
#32 for the quarantine.

``surface_widenings(baseline, current)`` only computes the surface itself when
``current`` is omitted. Passing it explicitly makes the function what it already
is — a pure comparison of two dicts — and every assertion here is then about the
comparison, not about whether the three records of the boundary currently agree.
That second question is real and stays in the quarantined file, because it is a
statement about production data that is false today.

The fixture is the recorded baseline itself, reconstructed into boundary objects.
Using the reviewed surface rather than a hand-built one keeps these tests honest
about the shape the detector meets in production, and gives the anti-vacuity
anchor below something meaningful to assert.
"""

from __future__ import annotations

import dataclasses
import unittest

from services import undx_capability_registry as registry
from services.undx_capability_registry import RiskLevel

from tests.undx_agent.authorization_surface_baseline import AUTHORIZATION_SURFACE

# The order ``boundary_tuple`` flattens to, and the order a baseline row records.
_FIELDS = (
    "capability_id", "risk", "confirmation", "permission", "authorization_scope",
    "is_write", "requires_authentication", "policy_confirms", "verifier",
    "verified_fields", "feature_flag",
)


def _boundary(row: tuple) -> registry.AuthorizationBoundary:
    return registry.AuthorizationBoundary(**{
        name: (tuple(value) if name == "verified_fields" else value)
        for name, value in zip(_FIELDS, row)
    })


class WideningDetector(unittest.TestCase):
    """The detector's own behaviour, independent of whether the records agree."""

    def setUp(self):
        self.baseline = {row[0]: row for row in AUTHORIZATION_SURFACE}
        self.current = {row[0]: _boundary(row) for row in AUTHORIZATION_SURFACE}
        self.subject = "crypto.alerts.create"

    def test_the_recorded_baseline_matches_the_boundary_shape(self):
        """A baseline row and an ``AuthorizationBoundary`` must not drift apart.

        ``surface_widenings`` reports "recorded boundary has the wrong shape" and
        moves on when a row is the wrong length, so a baseline written against an
        older field list would degrade to silence rather than failing. This states
        the round trip instead.
        """
        self.assertTrue(self.baseline, "the recorded surface must not be empty")
        for capability_id, row in sorted(self.baseline.items()):
            with self.subTest(capability=capability_id):
                self.assertEqual(len(row), len(_FIELDS))
                self.assertEqual(
                    registry.boundary_tuple(_boundary(row)), tuple(row),
                    "a recorded row no longer round-trips through the boundary shape",
                )

    def test_the_recorded_surface_does_not_widen_against_itself(self):
        """The anchor that makes every case below non-vacuous.

        If this fixture already produced findings, a test asserting that a
        mutation is reported would pass without the mutation doing anything.
        """
        self.assertEqual(registry.surface_widenings(self.baseline, self.current), [])

    def test_each_kind_of_widening_is_reported_distinctly(self):
        """A finding has to say what got wider, not that something changed."""
        before = self.current[self.subject]
        cases = {
            "risk lowered": dict(risk="reversible_write"),
            "confirmation weakened": dict(confirmation="never"),
            "dropped out of HIGH_IMPACT_TOOLS": dict(policy_confirms=False),
            "permission scope changed": dict(permission="other_user_target"),
            "authorization scope changed": dict(authorization_scope="public"),
            "authentication no longer required": dict(requires_authentication=False),
            "verifier dropped": dict(verifier=""),
            "verified fields dropped": dict(verified_fields=()),
            "feature gate removed": dict(feature_flag=""),
        }
        for phrase, change in cases.items():
            with self.subTest(widening=phrase):
                mutated = dict(self.current)
                mutated[self.subject] = dataclasses.replace(before, **change)
                findings = registry.surface_widenings(self.baseline, mutated)
                self.assertTrue(
                    any(phrase in line for line in findings),
                    f"{phrase!r} was not reported; got {findings}",
                )

    def test_narrowing_and_removal_do_not_fail(self):
        """The asymmetry is the design, so it is pinned rather than assumed.

        A check that fires on every change teaches people to regenerate the
        baseline without reading it, which costs more than it buys.
        """
        narrowed = dict(self.current)
        narrowed["crypto.alerts.pause"] = dataclasses.replace(
            self.current["crypto.alerts.pause"],
            risk="consequential_write", confirmation="always", policy_confirms=True,
        )
        self.assertEqual(registry.surface_widenings(self.baseline, narrowed), [])

        removed = {k: v for k, v in self.current.items() if k != "crypto.alerts.pause"}
        self.assertEqual(registry.surface_widenings(self.baseline, removed), [])

    def test_a_capability_absent_from_the_baseline_is_reported(self):
        trimmed = {k: v for k, v in self.baseline.items() if k != self.subject}
        findings = registry.surface_widenings(trimmed, self.current)
        self.assertTrue(
            any("newly reachable" in line for line in findings),
            f"a capability missing from the recorded surface must be reported; got {findings}",
        )

    def test_is_write_is_not_compared_because_risk_already_covers_it(self):
        """Why one of the eleven recorded fields has no widening rule.

        ``is_write`` is in ``boundary_tuple`` but absent from
        ``widenings_against``, which reads like an oversight. It is not:
        ``CapabilitySpec.is_write`` is ``RiskLevel.is_write(self.risk)``, so it
        cannot move on its own, and every transition that clears it is also a
        risk lowering — which is reported. Asserted over the whole lattice rather
        than argued, so a new risk word that breaks the implication fails here.
        """
        levels = list(RiskLevel.ORDER)
        self.assertTrue(levels, "expected a non-empty risk ordering")
        clearing = [
            (before, after)
            for before in levels for after in levels
            if RiskLevel.is_write(before) and not RiskLevel.is_write(after)
        ]
        self.assertTrue(clearing, "expected at least one write -> non-write transition")
        unreported = [
            (before, after) for before, after in clearing
            if RiskLevel.ORDER.get(after, 0) >= RiskLevel.ORDER.get(before, 0)
        ]
        self.assertEqual(
            unreported, [],
            "a risk transition clears is_write without ranking lower, so it would "
            "widen the boundary with nothing reporting it: " + repr(unreported),
        )


if __name__ == "__main__":
    unittest.main()
