"""The authorization boundary must be readable, agreed, and hard to move quietly.

Three files record where each capability's authority ends: the capability
registry, ``undx_policy.PRODUCTION_TOOL_REGISTRY``, and the product knowledge
map. Until now nothing read more than one of them at a time, and one of them —
the policy ledger's confirmation boolean — is what
``undx_architecture.HIGH_IMPACT_TOOLS`` is built from. A capability the registry
classes as ``consequential_write, always`` becomes reachable by the planner
without confirmation if someone flips a boolean in a different file. That edit
would pass every test in this suite as it stood.

These tests do two separate things, and the distinction matters:

  * ``AuthorizationRecordsAgree`` checks the records against *each other*, right
    now. It fails on the day they diverge, with no baseline involved.
  * ``AuthorizationSurfaceDoesNotWidenQuietly`` checks today's agreed boundary
    against a recorded one. It fails only on *widening*, because narrowing
    cannot increase what UNDX may reach, and a test that fires on every change
    trains reviewers to regenerate the baseline without reading it.
"""

from __future__ import annotations

import dataclasses
import unittest

from services import undx_capability_registry as registry
from services.undx_capability_registry import AuthorizationRecordConflict

from tests.undx_agent.authorization_surface_baseline import AUTHORIZATION_SURFACE


class AuthorizationRecordsAgree(unittest.TestCase):
    """The three records of the boundary must not contradict one another."""

    def test_every_registered_capability_yields_one_agreed_boundary(self):
        surface = registry.authorization_surface()
        self.assertEqual(
            sorted(surface), registry.capability_ids(),
            "every registered capability must have a boundary derived from all three records",
        )

    def test_a_conflict_raises_rather_than_picking_a_winner(self):
        """Disagreement is not resolved. Any resolution rule is a fourth opinion.

        Simulated on the most dangerous field: the policy ledger saying no
        confirmation is needed for a capability the registry marks ``always``.
        That single boolean is what removes a name from ``HIGH_IMPACT_TOOLS``.
        """
        from services import undx_policy

        always = [
            spec for spec in registry.REGISTRY.values()
            if spec.confirmation == "always"
        ]
        self.assertTrue(always, "expected at least one always-confirm capability to test with")
        spec = always[0]

        ledger = undx_policy.PRODUCTION_TOOL_REGISTRY
        original = dict(ledger[spec.tool_name])
        ledger[spec.tool_name] = {**original, "confirmation": False}
        try:
            with self.assertRaises(AuthorizationRecordConflict) as caught:
                registry.authorization_surface()
        finally:
            ledger[spec.tool_name] = original

        self.assertEqual(caught.exception.capability_id, spec.capability_id)
        self.assertEqual(caught.exception.field_name, "confirmation")
        self.assertIn("HIGH_IMPACT_TOOLS", caught.exception.detail)

        # And the records agree again once the edit is undone, so the test above
        # is detecting the edit rather than a pre-existing conflict.
        registry.authorization_surface()

    def test_a_scope_the_permission_does_not_admit_is_a_conflict(self):
        """A map record drifting to a defect scope must not be absorbed silently."""
        from services import undx_knowledge_map

        record = undx_knowledge_map.BY_ID["crypto.alerts.create"]
        original = record.authorization_scope
        object.__setattr__(record, "authorization_scope", "unscoped_defect")
        try:
            with self.assertRaises(AuthorizationRecordConflict) as caught:
                registry.authorization_surface()
        finally:
            object.__setattr__(record, "authorization_scope", original)
        self.assertEqual(caught.exception.field_name, "authorization_scope")

    def test_the_ledger_cannot_express_contextual_confirmation(self):
        """A recorded flattening, not an aspiration.

        The policy ledger holds a boolean. The registry holds three values. Every
        capability marked ``contextual`` is therefore recorded downstream as
        needing no confirmation, and is offered to the planner unguarded. This
        test states the count so that it is a number someone chose rather than a
        consequence nobody noticed.
        """
        surface = registry.authorization_surface()
        contextual = sorted(
            cid for cid, boundary in surface.items()
            if boundary.confirmation == "contextual"
        )
        self.assertTrue(
            all(not surface[cid].policy_confirms for cid in contextual),
            "a contextual capability that does confirm downstream would be new behaviour",
        )
        # 22 as of the sweep that gave every registered capability a knowledge-map
        # record: the business campaign verbs, watchlist add/remove, feed hide,
        # both localization writes, messages/notifications mark-read and the theme
        # write joined the original seven. All were already contextual in the
        # registry; what changed is that the surface can now be computed at all.
        self.assertEqual(
            len(contextual), 22,
            "the set of capabilities whose situational confirmation is flattened away "
            "downstream has changed; decide whether that is intended:\n  "
            + "\n  ".join(contextual),
        )


class AuthorizationSurfaceDoesNotWidenQuietly(unittest.TestCase):
    """Moving the boundary outward must require moving the marker, in a diff."""

    def _baseline(self):
        return {row[0]: row for row in AUTHORIZATION_SURFACE}

    def test_no_capability_reaches_further_than_the_recorded_surface(self):
        findings = registry.surface_widenings(self._baseline())
        self.assertEqual(
            findings, [],
            "the authorization surface has widened since it was last reviewed. This is "
            "not a formatting failure: each line below is somewhere UNDX may now reach "
            "that it could not before. If the change is intended, regenerate "
            "tests/undx_agent/authorization_surface_baseline.py as the last step of "
            "that decision.\n  " + "\n  ".join(findings),
        )

    def test_narrowing_and_removal_do_not_fail(self):
        """The asymmetry is the design, so it is pinned rather than assumed."""
        surface = registry.authorization_surface()
        baseline = self._baseline()

        narrowed = dict(surface)
        narrowed["crypto.alerts.pause"] = dataclasses.replace(
            surface["crypto.alerts.pause"],
            risk="consequential_write", confirmation="always", policy_confirms=True,
        )
        self.assertEqual(registry.surface_widenings(baseline, narrowed), [])

        removed = {k: v for k, v in surface.items() if k != "crypto.alerts.pause"}
        self.assertEqual(registry.surface_widenings(baseline, removed), [])

    def test_each_kind_of_widening_is_reported_distinctly(self):
        """A finding has to say what got wider, not that something changed."""
        surface = registry.authorization_surface()
        baseline = self._baseline()
        cid = "crypto.alerts.create"
        before = surface[cid]

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
                mutated = dict(surface)
                mutated[cid] = dataclasses.replace(before, **change)
                findings = registry.surface_widenings(baseline, mutated)
                self.assertTrue(
                    any(phrase in line for line in findings),
                    f"{phrase!r} was not reported; got {findings}",
                )

    def test_a_capability_absent_from_the_baseline_is_reported(self):
        surface = registry.authorization_surface()
        trimmed = {k: v for k, v in self._baseline().items() if k != "crypto.alerts.create"}
        findings = registry.surface_widenings(trimmed, surface)
        self.assertTrue(
            any("newly reachable" in line for line in findings),
            f"a capability missing from the recorded surface must be reported; got {findings}",
        )


if __name__ == "__main__":
    unittest.main()
