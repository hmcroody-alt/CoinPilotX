"""Mission 2's matrix: the two-axis model, strict parsing, rollout, migration.

Mission 1's suite (``test_control_plane_reconciliation.py``) pins the *legacy*
engine's behaviour and stays green; this one pins the replacement. The division
is deliberate — the old tests describe what production does today, these describe
what the new model does, and both have to hold simultaneously until activation.

Most tests here are arranged around a specific way of being wrong rather than
around a function. ``TestTheInvariantThatCaughtMission1`` is the clearest
example: it does not test that ``Capability`` validates its arguments, it tests
that the exact mistake Mission 1 shipped cannot be re-expressed.
"""

from __future__ import annotations

import ast
import datetime
import functools
import importlib
import inspect
import pathlib
import re
import sqlite3
import sys

import pytest

import services.feature_flag_engine as feature_flag_engine
from services.pulse_control_plane import activation
from services.pulse_control_plane import capabilities as caps
from services.pulse_control_plane import drift
from services.pulse_control_plane import env_gates
from services.pulse_control_plane import migration as mig
from services.pulse_control_plane import observations
from services.pulse_control_plane import parsing, reconciler, rollout, shadow
from services.pulse_control_plane import write_security
from services.pulse_control_plane.model import (
    ADMIN_ONLY,
    ANONYMOUS,
    DENYING_STATES,
    DEPLOYMENT_STATES,
    OWNER_ONLY,
    PREMIUM_ENTITLEMENT,
    REACHABLE_STATES,
    STANDARD,
    Capability,
    CapabilityDecision,
    EligibilityPolicy,
    EligibilityVerdict,
    evaluate,
)
from services.pulse_control_plane.observations import PRODUCTION_SIGNALS, STORED_STATES

# The audited production rows, 2026-09-24. Duplicated here rather than imported
# so that a change to the shipped observation data cannot silently rewrite what
# the migration tests believe they are migrating.
AUDITED_ROWS = {
    "pulse_posts": {"state": "enabled", "rollout_percentage": 100, "public_label": "Live"},
    "pulse_comments_reactions": {"state": "enabled", "rollout_percentage": 100, "public_label": "Live"},
    "pulse_messenger": {"state": "enabled", "rollout_percentage": 100, "public_label": "Live"},
    "pulse_spaces": {"state": "enabled", "rollout_percentage": 100, "public_label": "Live"},
    "pulse_groups": {"state": "enabled", "rollout_percentage": 100, "public_label": "Live"},
    "pulse_reels": {"state": "beta", "rollout_percentage": 100, "public_label": "Beta"},
    "pulse_livestream": {"state": "beta", "rollout_percentage": 100, "public_label": "Beta"},
    "marketplace_browse": {"state": "enabled", "rollout_percentage": 100, "public_label": "Live"},
    "merchant_applications": {"state": "enabled", "rollout_percentage": 100, "public_label": "Live"},
    "marketplace_checkout": {"state": "internal-only", "rollout_percentage": 0, "public_label": "Internal"},
    "premium_identity": {"state": "enabled", "rollout_percentage": 100, "public_label": "Live"},
    "premium_advanced_tools": {"state": "beta", "rollout_percentage": 100, "public_label": "Beta"},
    "ai_assistant": {"state": "beta", "rollout_percentage": 100, "public_label": "Beta"},
    "creator_cockpit": {"state": "beta", "rollout_percentage": 100, "public_label": "Beta"},
    "admin_command": {"state": "enabled", "rollout_percentage": 100, "public_label": "Live"},
}


def reconciliations():
    return reconciler.reconcile_all(PRODUCTION_SIGNALS, STORED_STATES)


def imported_modules(module) -> set[str]:
    """Top-level module names a module imports, read from its AST.

    Several properties in this suite are about what a module *depends on*, and
    every one of them is stated at length in that module's docstring. Grepping
    the source therefore matches the explanation as readily as a violation. The
    import graph is the thing actually being claimed, so read that.
    """
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _is_stdlib(name: str) -> bool:
    """Is this module name part of the standard library?

    Asked against :data:`sys.stdlib_module_names` rather than a hand-written
    allowlist, so that adding a stdlib import to the package does not fail a
    test about first-party dependencies — while adding *any* first-party one
    still does.
    """
    return name.split(".")[0] in sys.stdlib_module_names


def code_of(module) -> str:
    """Module source with docstrings and comments stripped.

    Same motivation as :func:`imported_modules`: this package argues for its
    design in prose, so a claim about the code has to be checked against the
    code.
    """
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    return ast.unparse(tree)


# ---------------------------------------------------------------------------
# Stage 1/2 — the model
# ---------------------------------------------------------------------------


class TestTheTwoAxesPartitionCleanly:
    def test_every_deployment_state_either_reaches_or_denies(self):
        assert REACHABLE_STATES | DENYING_STATES == set(DEPLOYMENT_STATES)

    def test_no_state_does_both(self):
        assert not (REACHABLE_STATES & DENYING_STATES)

    def test_unknown_denies(self):
        """The single most important cell in the table.

        The legacy engine mapped anything unrecognised to ``beta`` and granted
        full access. If ``UNKNOWN`` ever migrates into ``REACHABLE_STATES``, the
        entire fail-closed argument collapses silently.
        """
        assert "UNKNOWN" in DENYING_STATES


class TestTheInvariantThatCaughtMission1:
    """The specific wrong sentence, made unwriteable."""

    def test_a_conditional_capability_cannot_name_an_ungated_policy(self):
        with pytest.raises(ValueError, match="must name the authority"):
            Capability(
                key="creator_cockpit",
                deployment_state="LIVE_CONDITIONAL",
                eligibility=STANDARD,
                evidence="19 of 41 users have a growth workspace",
            )

    def test_the_same_holds_for_anonymous(self):
        with pytest.raises(ValueError, match="must name the authority"):
            Capability(
                key="x",
                deployment_state="LIVE_CONDITIONAL",
                eligibility=ANONYMOUS,
                evidence="e",
            )

    def test_a_global_capability_cannot_sit_behind_an_authority(self):
        """The mirror. Catches the opposite lie — ``admin_command`` as global."""
        with pytest.raises(ValueError, match="however widely deployed"):
            Capability(
                key="admin_command",
                deployment_state="LIVE_GLOBAL",
                eligibility=ADMIN_ONLY,
                evidence="199 admin routes",
            )

    def test_mission_1_recorded_the_row_the_new_model_refuses(self):
        """Non-vacuity: the corrected row really did differ from Mission 1's.

        Without this, the invariant above could be guarding against a mistake
        nobody ever made.
        """
        from services.pulse_control_plane import legacy

        old = {r.feature_key: r for r in legacy.LEGACY_ROWS}["creator_cockpit"]
        assert old.verdict == "LIVE_CONDITIONAL"
        # The tell, right there in the shipped row: it claimed a condition and
        # named no gate. Nothing checked, so the claim stood for a whole mission.
        assert old.real_gate is None
        assert caps.by_key("creator_cockpit").deployment_state == "LIVE_GLOBAL"


class TestAPolicyMustSayWhatRestricts:
    def test_a_restricting_policy_without_an_authority_is_refused(self):
        with pytest.raises(ValueError, match="must say what does the restricting"):
            EligibilityPolicy(key="VIP", description="vips only")

    def test_an_ungated_policy_needs_no_authority(self):
        assert STANDARD.authority is None
        assert STANDARD.requires_authority is False

    def test_every_shipped_gated_policy_names_one(self):
        for policy in (PREMIUM_ENTITLEMENT, ADMIN_ONLY, OWNER_ONLY):
            assert policy.authority


class TestProtectedCapabilitiesRefusePartialRollout:
    def test_the_constructor_refuses(self):
        with pytest.raises(ValueError, match="not a rollout"):
            Capability(
                key="marketplace_checkout",
                deployment_state="LIVE_GLOBAL",
                eligibility=STANDARD,
                evidence="32 orders",
                protected=("PAYMENT",),
                rollout_percentage=50,
            )

    def test_the_rollout_module_refuses_too(self):
        with pytest.raises(rollout.RolloutRefused, match="complete a payment"):
            rollout.assert_rollout_allowed("marketplace_checkout", ("PAYMENT",), 50)

    def test_it_raises_rather_than_clamping(self):
        """Clamping would silently do the right thing and teach the wrong lesson."""
        with pytest.raises(rollout.RolloutRefused):
            rollout.assert_rollout_allowed("x", ("PAYMENT",), 99)


# ---------------------------------------------------------------------------
# Stage 3/4 — the decision contract, failing closed
# ---------------------------------------------------------------------------


class TestEvaluateFailsClosed:
    @pytest.mark.parametrize("state", sorted(DENYING_STATES))
    def test_every_denying_state_denies(self, state):
        capability = Capability(
            key="x", deployment_state=state, eligibility=STANDARD, evidence="e"
        )
        decision = evaluate(capability)
        assert decision.visible is False
        assert decision.usable is False

    def test_unknown_reports_why(self):
        capability = Capability(
            key="x", deployment_state="UNKNOWN", eligibility=STANDARD, evidence="e"
        )
        assert evaluate(capability).reason_code == "UNRESOLVABLE_STATE"

    def test_a_gated_capability_with_no_verdict_is_unusable(self):
        capability = caps.by_key("premium_identity")
        decision = evaluate(capability)
        assert decision.usable is False
        assert decision.reason_code == "ELIGIBILITY_NOT_SUPPLIED"

    def test_a_denied_verdict_is_unusable(self):
        capability = caps.by_key("premium_identity")
        decision = evaluate(
            capability,
            eligibility=EligibilityVerdict(False, "business_os.entitlements.premium"),
        )
        assert decision.usable is False
        assert decision.reason_code == "ELIGIBILITY_DENIED"

    def test_a_granted_verdict_is_usable(self):
        capability = caps.by_key("premium_identity")
        decision = evaluate(
            capability,
            eligibility=EligibilityVerdict(True, "business_os.entitlements.premium"),
        )
        assert decision.usable is True
        assert decision.reason_code == "AVAILABLE"


class TestDenialRendersDifferentlyPerPolicy:
    """Same denial, opposite correct rendering. A single boolean would lose this."""

    def test_a_denied_premium_user_still_sees_the_upsell(self):
        decision = evaluate(caps.by_key("premium_identity"))
        assert decision.visible is True
        assert decision.usable is False

    def test_a_denied_non_admin_sees_nothing(self):
        decision = evaluate(caps.by_key("admin_command"))
        assert decision.visible is False
        assert decision.usable is False


class TestTheDecisionContract:
    def test_usable_without_visible_is_refused(self):
        with pytest.raises(ValueError, match="incoherent"):
            CapabilityDecision(
                capability_key="x",
                deployment_state="LIVE_GLOBAL",
                visible=False,
                usable=True,
                eligibility_policy="STANDARD",
                reason_code="AVAILABLE",
                source="t",
                model_version="2.0",
                evaluated_at="now",
            )

    def test_reason_codes_come_from_the_vocabulary(self):
        with pytest.raises(ValueError, match="unknown reason code"):
            CapabilityDecision(
                capability_key="x",
                deployment_state="LIVE_GLOBAL",
                visible=True,
                usable=True,
                eligibility_policy="STANDARD",
                reason_code="PROBABLY_FINE",
                source="t",
                model_version="2.0",
                evaluated_at="now",
            )

    def test_every_decision_carries_a_reason_and_a_version(self):
        decision = evaluate(caps.by_key("pulse_posts"))
        assert decision.reason_code
        assert decision.model_version
        assert decision.evaluated_at


class TestEvaluateHasNoEscapeHatch:
    def test_there_is_no_force_or_bypass_argument(self):
        """The affordance reached for during an incident, that then stays."""
        params = set(inspect.signature(evaluate).parameters)
        assert params == {"capability", "subject_id", "eligibility", "source"}

    def test_the_control_plane_cannot_resolve_entitlements_itself(self):
        """It must have no path to the entitlement tables.

        Checked over the import graph rather than over the file's text: the
        docstrings in this package necessarily *name* ``business_os``, because
        naming the authority is the whole design. A substring search cannot tell
        "documents the authority" from "calls the authority"; the AST can.

        The closure is walked rather than the one file's import list, because
        ``evaluate`` defers ``rollout`` to a function-local import. A one-level
        check would have missed anything that module reached in turn, and a
        deferred import is exactly the shape an escape hatch takes.
        """
        import services.pulse_control_plane.model as model_module

        seen: set[str] = set()
        pending = [model_module]
        while pending:
            current = pending.pop()
            for name in imported_modules(current):
                if name in seen:
                    continue
                seen.add(name)
                if name.startswith("services.pulse_control_plane."):
                    pending.append(importlib.import_module(name))

        # Reachable first-party modules: the pure-arithmetic bucketer, nothing
        # else. No ``services.db``, no ``bot``, no ``business_os`` anything.
        assert {n for n in seen if not _is_stdlib(n)} == {
            "services.pulse_control_plane.rollout"
        }


# ---------------------------------------------------------------------------
# Stage 5 — strict parsing
# ---------------------------------------------------------------------------


class TestStrictParsing:
    @pytest.mark.parametrize(
        "raw",
        ["internal-only", "internal_only", "internal only", "Internal-Only", "  INTERNAL  ONLY  "],
    )
    def test_every_spelling_of_internal_only_resolves_to_one_policy(self, raw):
        result = parsing.parse_eligibility_policy(raw)
        assert result.accepted is True
        assert result.value.key == "INTERNAL_ONLY"

    @pytest.mark.parametrize(
        "raw", ["internal-only", "internal_only", "internal only", "Internal-Only"]
    )
    def test_internal_only_is_not_a_deployment_state(self, raw):
        """It names an eligibility. Feeding it to the wrong axis must deny."""
        result = parsing.parse_deployment_state(raw)
        assert result.accepted is False
        assert result.value == "UNKNOWN"

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", None, "enabled!", "live-global-2", "LIVE__GLOBAL!", 42, [], {}, True],
    )
    def test_garbage_denies(self, raw):
        assert parsing.parse_deployment_state(raw).value == "UNKNOWN"

    @pytest.mark.parametrize("raw", ["live_globl", "LIVE_GLOBL", "liveglobal", "global"])
    def test_typos_are_not_spell_corrected(self, raw):
        result = parsing.parse_deployment_state(raw)
        assert result.accepted is False
        assert result.value == "UNKNOWN"

    @pytest.mark.parametrize("raw", ["enabled", "beta", "premium-only", "owner-only"])
    def test_legacy_words_are_refused_at_runtime(self, raw):
        """Translated exactly once, by the migration. Never at runtime."""
        assert parsing.parse_deployment_state(raw).value == "UNKNOWN"

    def test_disabled_is_the_one_word_the_two_vocabularies_share(self):
        """And it means the same thing in both, so accepting it is correct.

        Worth pinning rather than leaving implicit: every other legacy word
        either conflates the axes (``internal-only``, ``premium-only``) or means
        "everyone" (``enabled``, ``beta``). ``disabled`` is the lone survivor —
        a genuine deployment state under either model — and the fail-closed
        argument does not need it refused, because it denies either way.
        """
        result = parsing.parse_deployment_state("disabled")
        assert result.accepted is True
        assert result.value == "DISABLED"
        assert result.value in DENYING_STATES

    @pytest.mark.parametrize(
        "raw,expected", [("live-global", "LIVE_GLOBAL"), ("LIVE GLOBAL", "LIVE_GLOBAL"),
                         ("  live_global ", "LIVE_GLOBAL"), ("Dead", "DEAD")]
    )
    def test_only_meaning_preserving_differences_are_folded(self, raw, expected):
        result = parsing.parse_deployment_state(raw)
        assert result.accepted is True
        assert result.value == expected


class TestParsingNeverWidens:
    def test_an_unresolvable_policy_does_not_become_standard(self):
        result = parsing.parse_eligibility_policy("vip-only")
        assert result.accepted is False
        assert result.value is None

    @pytest.mark.parametrize("raw", [None, "", "abc", -1, 101, 1e400, True, [], {}])
    def test_an_unreadable_rollout_is_zero_not_a_hundred(self, raw):
        result = parsing.parse_rollout(raw)
        assert result.accepted is False
        assert result.value == 0

    def test_a_corrupt_policy_forces_the_whole_row_unknown(self):
        """Corruption must not be able to *remove* a restriction."""
        row = parsing.parse_stored_row(
            {
                "feature_key": "premium_identity",
                "deployment_state": "LIVE_GLOBAL",
                "eligibility_policy": "\x00garbage",
                "rollout_percentage": 100,
            }
        )
        assert row.deployment_state == "UNKNOWN"
        assert row.clean is False

    def test_a_missing_key_forces_the_row_unknown(self):
        row = parsing.parse_stored_row(
            {"deployment_state": "LIVE_GLOBAL", "eligibility_policy": "STANDARD"}
        )
        assert row.deployment_state == "UNKNOWN"

    def test_faults_are_reported_even_though_the_row_is_usable(self):
        row = parsing.parse_stored_row(
            {"feature_key": "x", "deployment_state": "nonsense",
             "eligibility_policy": "STANDARD", "rollout_percentage": "many"}
        )
        assert row.faults
        assert row.deployment_state == "UNKNOWN"


# ---------------------------------------------------------------------------
# Stage 13/14 — rollout
# ---------------------------------------------------------------------------


SUBJECTS = [str(i) for i in range(1, 2001)]


class TestRolloutIsMonotone:
    def test_widening_never_drops_anyone(self):
        """Raising a rollout must be purely additive, at every step."""
        previous = set()
        for pct in range(0, 101, 5):
            current = {s for s in SUBJECTS if rollout.in_rollout("cap", s, pct)}
            assert previous <= current, f"widening to {pct}% dropped subjects"
            previous = current

    def test_narrowing_is_exactly_reversible(self):
        at_25 = {s for s in SUBJECTS if rollout.in_rollout("cap", s, 25)}
        at_10 = {s for s in SUBJECTS if rollout.in_rollout("cap", s, 10)}
        back_at_25 = {s for s in SUBJECTS if rollout.in_rollout("cap", s, 25)}
        assert at_10 <= at_25
        assert back_at_25 == at_25

    def test_zero_means_nobody_and_hundred_means_everybody(self):
        assert not any(rollout.in_rollout("cap", s, 0) for s in SUBJECTS)
        assert all(rollout.in_rollout("cap", s, 100) for s in SUBJECTS)

    def test_the_bucket_does_not_depend_on_the_percentage(self):
        """What makes widening additive in the first place."""
        assert rollout.bucket_of("cap", "1234") == rollout.bucket_of("cap", "1234")

    def test_different_capabilities_bucket_independently(self):
        a = {s for s in SUBJECTS if rollout.in_rollout("cap_a", s, 20)}
        b = {s for s in SUBJECTS if rollout.in_rollout("cap_b", s, 20)}
        assert a != b


class TestRolloutFailsClosed:
    @pytest.mark.parametrize("subject", [None, 0, -1, True, False, "", "   "])
    def test_an_unusable_subject_is_excluded_from_a_partial_rollout(self, subject):
        assert rollout.in_rollout("cap", subject, 50) is False

    def test_bool_is_rejected_before_int(self):
        """``True`` is ``1``, and user 1 is this platform's only seller."""
        assert rollout.subject_key(True) == ""
        assert rollout.subject_key(1) == "1"

    @pytest.mark.parametrize("pct", [None, "abc", [], {}])
    def test_an_unparseable_percentage_admits_nobody(self, pct):
        assert rollout.in_rollout("cap", "7", pct) is False


class TestRolloutIsSeparateFromExperiments:
    def test_the_salt_is_fixed_in_source(self):
        """Rotating an access-control salt silently redistributes access.

        Asserted over the code rather than the prose, which explains at length
        why there is no environment override and would fail a naive grep for
        saying so.
        """
        assert "os" not in imported_modules(rollout)
        assert code_of(rollout).count("ROLLOUT_NAMESPACE = ") == 1

    def test_neither_package_imports_the_other(self):
        """Authorisation must not depend on analytics infrastructure."""
        from services.pulse_experiments import assignment

        assert not any("experiments" in m for m in imported_modules(rollout))
        assert not any("control_plane" in m for m in imported_modules(assignment))

    def test_the_two_bucketings_disagree(self):
        """Same technique, different namespaces — so they are genuinely separate."""
        from services.pulse_experiments import assignment

        control = {s for s in SUBJECTS if rollout.in_rollout("checkout", s, 30)}
        experiment = {
            s
            for s in SUBJECTS
            if assignment._bucket("rollout", "checkout", s) < 30 * (assignment.BUCKETS // 100)
        }
        assert control != experiment


# ---------------------------------------------------------------------------
# Stage 16 — bounded evidence aggregation
# ---------------------------------------------------------------------------


def sig(kind, n=1):
    return tuple(reconciler.Signal(kind, f"source-{i}") for i in range(n))


class TestEvidenceAggregationIsBounded:
    def test_no_amount_of_weak_evidence_declares_something_live(self):
        """Stage 16, and Mission 1's actual mistake."""
        observed = reconciler.aggregate(sig("OPERATOR_CLAIM", 50), gated=False)
        assert observed.deployment_state == "UNKNOWN"
        assert observed.score <= reconciler.WEAK_CONTRIBUTION_CAP

    def test_mixed_weak_evidence_is_capped_as_a_whole(self):
        signals = sig("OPERATOR_CLAIM", 10) + sig("CLIENT_SHIPPED", 10)
        assert reconciler.aggregate(signals, gated=False).deployment_state == "UNKNOWN"

    def test_configuration_cannot_be_evidence_for_itself(self):
        """``pulse_premium_feature_flags`` all-enabled, restated as signals."""
        seven_enabled_rows = sig("OPERATOR_CLAIM", 7)
        assert reconciler.aggregate(seven_enabled_rows, gated=True).deployment_state == "UNKNOWN"

    def test_one_strong_signal_is_enough(self):
        assert reconciler.aggregate(sig("ROUTE_AUTH_GATED"), gated=False).reachable

    def test_one_moderate_signal_is_not(self):
        assert not reconciler.aggregate(sig("SOURCE_GUARD"), gated=False).reachable

    def test_two_moderate_signals_are(self):
        signals = sig("SOURCE_GUARD") + sig("DATA_PRESENT")
        assert reconciler.aggregate(signals, gated=False).reachable

    def test_the_cap_is_below_the_threshold(self):
        assert reconciler.WEAK_CONTRIBUTION_CAP < reconciler.DEPLOYED_THRESHOLD


class TestAbsenceBeatsPresence:
    def test_a_missing_route_vetoes_any_quantity_of_weak_support(self):
        signals = sig("ROUTE_MISSING") + sig("OPERATOR_CLAIM", 30)
        assert reconciler.aggregate(signals, gated=False).deployment_state == "ABSENT"

    def test_a_falsy_env_gate_means_disabled_not_absent(self):
        signals = sig("ENV_GATE_OFF") + sig("DATA_PRESENT")
        assert reconciler.aggregate(signals, gated=False).deployment_state == "DISABLED"

    def test_contradiction_resolves_to_unknown_not_a_tie_break(self):
        signals = sig("ROUTE_OK") + sig("ENV_GATE_OFF")
        observed = reconciler.aggregate(signals, gated=False)
        assert observed.deployment_state == "UNKNOWN"
        assert "contradictory" in observed.rationale

    def test_an_empty_table_does_not_veto(self):
        """It proves nobody used it, not that it is absent."""
        signals = sig("ROUTE_AUTH_GATED") + sig("DATA_EMPTY")
        assert reconciler.aggregate(signals, gated=False).reachable


class TestSignalsMustBeCitable:
    def test_a_signal_without_a_source_is_refused(self):
        with pytest.raises(ValueError, match="citable"):
            reconciler.Signal("ROUTE_OK", "  ")

    def test_an_unknown_signal_kind_is_refused(self):
        with pytest.raises(ValueError, match="unknown signal kind"):
            reconciler.Signal("VIBES", "somewhere")

    def test_every_production_signal_carries_a_source(self):
        for signals in PRODUCTION_SIGNALS.values():
            for signal in signals:
                assert signal.source.strip()


# ---------------------------------------------------------------------------
# Stage 8/9 — reconciliation against production
# ---------------------------------------------------------------------------


class TestReconciliationOfProduction:
    def test_every_capability_is_reconciled(self):
        assert len(reconciliations()) == len(caps.CAPABILITIES) == 15

    def test_measured_reality_never_contradicts_the_inventory(self):
        """If this fails, this package is wrong about production — not the config."""
        bad = [r.capability_key for r in reconciliations() if r.inventory_contradicted]
        assert bad == []

    def test_checkout_is_understated(self):
        r = {x.capability_key: x for x in reconciliations()}["marketplace_checkout"]
        assert r.verdict == "UNDERSTATED"
        assert r.severity == "HIGH"
        assert "withdraw a working feature" in r.rationale

    @pytest.mark.parametrize(
        "key", ["premium_identity", "premium_advanced_tools", "admin_command"]
    )
    def test_the_gated_capabilities_are_overstated(self, key):
        r = {x.capability_key: x for x in reconciliations()}[key]
        assert r.verdict == "OVERSTATED"
        assert r.severity == "CRITICAL"

    def test_overstated_outranks_understated(self):
        """One is a potential access grant; the other a potential outage."""
        order = ["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
        assert order.index(reconciler.VERDICT_SEVERITY["OVERSTATED"]) > order.index(
            reconciler.VERDICT_SEVERITY["UNDERSTATED"]
        )

    def test_the_remaining_eleven_match(self):
        verdicts = [r.verdict for r in reconciliations()]
        assert verdicts.count("MATCH") == 11

    def test_a_row_with_no_config_reports_live_without_config(self):
        r = reconciler.reconcile(
            caps.by_key("pulse_posts"), None, PRODUCTION_SIGNALS["pulse_posts"]
        )
        assert r.verdict == "LIVE_WITHOUT_CONFIG"

    def test_config_with_nothing_deployed_is_dead_config(self):
        r = reconciler.reconcile(caps.by_key("pulse_posts"), "enabled", sig("ROUTE_MISSING"))
        assert r.verdict == "DEAD_CONFIG"

    def test_an_unreadable_stored_value_is_not_guessed_at(self):
        r = reconciler.reconcile(
            caps.by_key("pulse_posts"), "enabeld", PRODUCTION_SIGNALS["pulse_posts"]
        )
        assert r.verdict == "UNKNOWN"
        assert "refusing to infer" in r.rationale

    def test_reconciliation_is_deterministic(self):
        first = reconcile_keys()
        second = reconcile_keys()
        assert first == second

    def test_severity_must_match_the_verdict(self):
        with pytest.raises(ValueError, match="does not match"):
            reconciler.Reconciliation(
                capability_key="x", verdict="MATCH", severity="CRITICAL",
                configured_state="enabled", configured_exposure=4,
                observed_state="LIVE_GLOBAL", observed_exposure=4,
                observed_confidence="MEASURED", rationale="",
            )


def reconcile_keys():
    return [(r.capability_key, r.verdict, r.severity) for r in reconciliations()]


class TestTheLegacyVocabularyIsGivenMeaningExactlyOnce:
    def test_beta_and_enabled_express_the_same_exposure(self):
        """Which is why seven ``beta`` rows are fully public."""
        assert caps.LEGACY_EXPOSURE["beta"] == caps.LEGACY_EXPOSURE["enabled"]

    def test_every_seeded_value_is_translatable(self):
        for state in set(caps.SEEDED_STATES.values()):
            assert state in caps.LEGACY_EXPOSURE

    def test_the_translation_matches_the_engines_actual_behaviour(self):
        from services import feature_flag_engine

        for word, (state, policy) in caps.LEGACY_EXPOSURE.items():
            verdict = feature_flag_engine.evaluate_flag({"state": word}, {"user_id": 7})
            ungated = policy.key in ("STANDARD", "ANONYMOUS")
            expected_usable = state not in DENYING_STATES and ungated
            assert verdict["usable"] is expected_usable, word


# ---------------------------------------------------------------------------
# Stage 10/11 — the migration
# ---------------------------------------------------------------------------


def manifest():
    return mig.build_manifest(reconciliations(), AUDITED_ROWS)


class TestTheManifestIsDecidedBeforeAnythingIsOpened:
    def test_it_is_deterministic(self):
        assert mig.manifest_summary(manifest()) == mig.manifest_summary(manifest())

    def test_it_is_ordered_by_the_inventory_not_by_severity(self):
        keys = [e.capability_key for e in manifest()]
        assert keys == [c.key for c in caps.CAPABILITIES]

    def test_no_action_writes_the_legacy_state_column(self):
        """The core safety property of the mission.

        Parsed into assigned column names rather than matched as a substring,
        because ``deployment_state = ?`` contains ``state = ?`` and a naive
        search would pass while the column was being overwritten.
        """
        set_clause, where_clause = mig._UPDATE.split("WHERE")
        assigned = {
            part.split("=")[0].strip()
            for part in set_clause.split("SET", 1)[1].split(",")
        }
        assert "state" not in assigned
        assert "deployment_state" in assigned
        assert "state = ?" in where_clause  # present only as the guard

    def test_every_write_is_guarded_on_the_audited_value(self):
        for entry in manifest():
            if entry.writes:
                assert entry.expected_state == AUDITED_ROWS[entry.capability_key]["state"]

    def test_all_fifteen_rows_are_writable_today(self):
        assert sum(1 for e in manifest() if e.writes) == 15

    def test_the_columns_are_purely_additive(self):
        existing = {"feature_key", "label", "state", "rollout_percentage", "premium_required",
                    "owner_only", "internal_only", "public_label", "notes", "updated_at"}
        for name, _ in mig.ADDITIVE_COLUMNS:
            assert name not in existing


class TestTheMigrationDeclinesToGuess:
    def test_an_unknown_verdict_is_skipped(self):
        broken = reconciler.reconcile_all({"pulse_posts": sig("DATA_EMPTY")}, STORED_STATES)
        entry = {e.capability_key: e for e in mig.build_manifest(broken, AUDITED_ROWS)}["pulse_posts"]
        assert entry.action == "SKIP_UNVERIFIED"
        assert entry.writes is False

    def test_an_inferred_row_is_not_written_as_truth(self):
        inferred = reconciler.reconcile_all({"pulse_posts": sig("ROUTE_OK")}, STORED_STATES)
        entry = {e.capability_key: e for e in mig.build_manifest(inferred, AUDITED_ROWS)}["pulse_posts"]
        assert entry.action == "SKIP_UNVERIFIED"
        assert "INFERRED" in entry.reason

    def test_a_missing_row_is_not_created(self):
        rows = {k: v for k, v in AUDITED_ROWS.items() if k != "pulse_posts"}
        entry = {e.capability_key: e for e in mig.build_manifest(reconciliations(), rows)}["pulse_posts"]
        assert entry.action == "SKIP_NO_ROW"


class TestPublicLabelOwnership:
    def test_checkout_is_relabelled_away_from_internal(self):
        entry = {e.capability_key: e for e in manifest()}["marketplace_checkout"]
        assert entry.current_public_label == "Internal"
        assert entry.target_public_label == "Live"

    def test_admin_command_is_relabelled_to_internal(self):
        entry = {e.capability_key: e for e in manifest()}["admin_command"]
        assert entry.target_public_label == "Internal"

    def test_premium_rows_are_labelled_premium(self):
        entries = {e.capability_key: e for e in manifest()}
        for key in ("premium_identity", "premium_advanced_tools"):
            assert entries[key].target_public_label == "Premium"

    def test_the_label_is_derived_from_both_axes(self):
        assert mig.public_label_for(caps.by_key("admin_command")) == "Internal"
        assert mig.public_label_for(caps.by_key("pulse_posts")) == "Live"


# ---------------------------------------------------------------------------
# Stage 11 — compare-and-set, against a real table
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE feature_flags (
            feature_key TEXT PRIMARY KEY, label TEXT, state TEXT DEFAULT 'beta',
            rollout_percentage INTEGER DEFAULT 100, premium_required INTEGER DEFAULT 0,
            owner_only INTEGER DEFAULT 0, internal_only INTEGER DEFAULT 0,
            public_label TEXT, notes TEXT, updated_at TEXT)"""
    )
    for key, row in AUDITED_ROWS.items():
        conn.execute(
            "INSERT INTO feature_flags (feature_key, label, state, rollout_percentage, "
            "public_label, updated_at) VALUES (?,?,?,?,?,?)",
            (key, key, row["state"], row["rollout_percentage"], row["public_label"],
             "2026-05-22T11:51:57"),
        )
    conn.commit()
    return conn


def states_of(conn):
    return dict(conn.execute("SELECT feature_key, state FROM feature_flags").fetchall())


class TestMigrationAgainstARealTable:
    def test_a_dry_run_writes_nothing(self, seeded_db):
        before = seeded_db.execute("SELECT * FROM feature_flags").fetchall()
        result = mig.apply_manifest(seeded_db, manifest(), reconciled_at="t", dry_run=True)
        assert result.committed is False
        assert seeded_db.execute("SELECT * FROM feature_flags").fetchall() == before

    def test_applying_writes_the_new_columns(self, seeded_db):
        result = mig.apply_manifest(seeded_db, manifest(), reconciled_at="t", dry_run=False)
        assert result.committed is True
        assert result.written == 15
        rows = dict(
            seeded_db.execute(
                "SELECT feature_key, deployment_state FROM feature_flags"
            ).fetchall()
        )
        assert rows["marketplace_checkout"] == "LIVE_GLOBAL"
        assert rows["admin_command"] == "LIVE_CONDITIONAL"

    def test_the_legacy_state_column_is_untouched(self, seeded_db):
        before = states_of(seeded_db)
        mig.apply_manifest(seeded_db, manifest(), reconciled_at="t", dry_run=False)
        assert states_of(seeded_db) == before

    def test_the_legacy_engine_behaves_identically_afterwards(self, seeded_db):
        """The mission's primary rule, asserted end to end."""
        from services import feature_flag_engine

        seeded_db.row_factory = sqlite3.Row
        def verdicts():
            return {
                r["feature_key"]: feature_flag_engine.evaluate_flag(dict(r), {"user_id": 7})
                for r in seeded_db.execute("SELECT * FROM feature_flags")
            }

        before = verdicts()
        mig.apply_manifest(seeded_db, manifest(), reconciled_at="t", dry_run=False)
        assert verdicts() == before

    def test_it_is_idempotent(self, seeded_db):
        mig.apply_manifest(seeded_db, manifest(), reconciled_at="t", dry_run=False)
        snapshot = seeded_db.execute("SELECT * FROM feature_flags ORDER BY feature_key").fetchall()
        second = mig.apply_manifest(seeded_db, manifest(), reconciled_at="t", dry_run=False)
        assert second.written == 15
        assert seeded_db.execute(
            "SELECT * FROM feature_flags ORDER BY feature_key"
        ).fetchall() == snapshot


class TestCompareAndSetStopsAStaleAudit:
    def test_a_drifted_row_aborts(self, seeded_db):
        seeded_db.execute(
            "UPDATE feature_flags SET state='disabled' WHERE feature_key='pulse_posts'"
        )
        seeded_db.commit()
        result = mig.apply_manifest(seeded_db, manifest(), reconciled_at="t", dry_run=False)
        assert [r.capability_key for r in result.aborted] == ["pulse_posts"]

    def test_one_drifted_row_rolls_the_whole_transaction_back(self, seeded_db):
        """A half-migrated table has no owner."""
        seeded_db.execute(
            "UPDATE feature_flags SET state='disabled' WHERE feature_key='pulse_posts'"
        )
        seeded_db.commit()
        result = mig.apply_manifest(seeded_db, manifest(), reconciled_at="t", dry_run=False)
        assert result.committed is False
        assert "pulse_posts" in result.rolled_back_because
        written = seeded_db.execute(
            "SELECT COUNT(*) FROM feature_flags WHERE deployment_state IS NOT NULL"
        ).fetchone()[0]
        assert written == 0

    def test_the_guard_is_the_audited_value_not_the_target(self, seeded_db):
        """Guarding on the target would make the write unconditional on re-run."""
        entry = {e.capability_key: e for e in manifest()}["marketplace_checkout"]
        assert entry.expected_state == "internal-only"
        assert entry.target_deployment_state == "LIVE_GLOBAL"


# ---------------------------------------------------------------------------
# Stage 9/29 — the inventory itself
# ---------------------------------------------------------------------------


class TestTheInventory:
    def test_it_covers_every_seeded_key(self):
        assert {c.key for c in caps.CAPABILITIES} == set(caps.SEEDED_STATES)

    def test_every_row_carries_evidence(self):
        for capability in caps.CAPABILITIES:
            assert len(capability.evidence) > 40

    def test_every_conditional_row_names_its_authority(self):
        for capability in caps.conditional_capabilities():
            assert capability.runtime_authority
            assert capability.eligibility.authority

    def test_nothing_is_unreconciled(self):
        assert caps.unreconciled() == ()

    def test_payment_and_authorization_are_protected(self):
        protected = {c.key: c.protected for c in caps.protected_capabilities()}
        assert "PAYMENT" in protected["marketplace_checkout"]
        assert "AUTHORIZATION" in protected["admin_command"]

    def test_no_protected_capability_is_partially_rolled_out(self):
        for capability in caps.protected_capabilities():
            assert capability.rollout_percentage == 100


# ---------------------------------------------------------------------------
# Stage 12/26 — init_db() must not overwrite operator-controlled values
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def bot_source() -> str:
    """``bot.py`` as text.

    Read rather than imported. Importing the monolith to inspect two functions
    would drag in the whole Flask app and its optional route packs; these
    properties are about what the source says, so the source is what to read.
    """
    return (pathlib.Path(__file__).resolve().parents[2] / "bot.py").read_text()


@functools.lru_cache(maxsize=1)
def bot_tree() -> ast.Module:
    """``bot.py``'s AST. Four seconds to parse, so done once and only on demand."""
    return ast.parse(bot_source())


def bot_function_source(name: str) -> str:
    for node in ast.walk(bot_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(bot_source(), node) or ""
    raise AssertionError(f"bot.py has no function named {name!r}")


def _feature_flag_updates() -> list[tuple[int, str]]:
    """Every ``UPDATE feature_flags`` in ``bot.py``, as (line, SET clause).

    Read as text rather than through the AST because these statements are SQL
    string literals: the AST would hand back the same string with more
    ceremony. ``bot.py`` does not argue for its design in prose the way the
    control-plane package does, so the usual objection to grepping does not
    apply here.
    """
    source = bot_source()
    updates = []
    for match in re.finditer(r"UPDATE\s+feature_flags\s+SET\s+(.*?)\s+WHERE", source, re.S | re.I):
        line = source.count("\n", 0, match.start()) + 1
        updates.append((line, match.group(1)))
    return updates


def _assigned_columns(set_clause: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"(\w+)\s*=", set_clause)}


class TestPublicLabelHasOnlyOneWriter:
    """``public_label`` had a writer that ran on every request and won.

    ``init_db()`` is called per request, and it unconditionally re-asserted the
    seeded ``public_label``. So the admin page's value was restored within
    milliseconds of any edit — the column was writable in schema and unwritable
    in practice. Production has carried ``marketplace_checkout = "Internal"``
    since May, on a capability that has taken 32 real orders, and no operator
    could have corrected it.
    """

    def test_the_seeder_no_longer_writes_it(self):
        seeder = [u for u in _feature_flag_updates() if "label" in u[1] and "state" not in u[1]]
        assert len(seeder) == 1, seeder
        assert _assigned_columns(seeder[0][1]) == {"label"}

    def test_exactly_one_writer_remains(self):
        writers = [u for u in _feature_flag_updates() if "public_label" in _assigned_columns(u[1])]
        assert len(writers) == 1, writers

    def test_that_writer_is_owner_gated(self):
        """The one remaining writer sits inside the owner-level branch.

        Checked by locating the enclosing route rather than by proximity: an
        ``UPDATE`` that drifted out of the guarded block would still be within
        a few lines of it.
        """
        writer_line = [u for u in _feature_flag_updates() if "public_label" in _assigned_columns(u[1])][0][0]
        enclosing = [
            node
            for node in ast.walk(bot_tree())
            if isinstance(node, ast.FunctionDef)
            and node.lineno <= writer_line <= (node.end_lineno or node.lineno)
        ]
        assert enclosing, "the writer is not inside any function"
        body = ast.get_source_segment(bot_source(), enclosing[-1]) or ""
        assert "admin_is_owner_level" in body
        assert "require_admin_page" in body

    def test_an_omitted_field_does_not_blank_the_stored_value(self):
        """The update must coalesce, not assign.

        With the seeder gone there is no longer anything to restore a blanked
        label, so a form that omits the field has to leave it alone. A plain
        ``public_label=?`` would have turned every save of an unrelated field
        into a silent erasure.
        """
        writer = [u for u in _feature_flag_updates() if "public_label" in _assigned_columns(u[1])][0][1]
        assert re.search(r"public_label\s*=\s*COALESCE\(", writer, re.I), writer

    def test_the_matrix_row_carries_the_stored_label(self):
        """Otherwise the form renders blank and the save erases it."""
        rows = feature_flag_engine.capability_matrix({}, [
            {"feature_key": "marketplace_checkout", "public_label": "Live"}
        ])
        row = next(r for r in rows if r["feature_key"] == "marketplace_checkout")
        assert row["public_label"] == "Live"


class TestAdminAuditRecordsAPreImage:
    def test_before_json_is_no_longer_a_literal_empty_string(self):
        """``admin_activity_logs.before_json`` was hard-coded ``""``.

        The column existed, so the schema read as though it recorded what
        changed, while it only ever recorded what it changed *to*. Stage 25
        wants before/after; this is the half that was missing.
        """
        source = bot_function_source("log_admin_audit")
        assert "before_text" in source
        assert re.search(r"def log_admin_audit\([^)]*\bbefore=None", source, re.S), source[:200]

    def test_the_capability_matrix_post_supplies_one(self):
        source = bot_function_source("admin_capability_matrix_page")
        assert "SELECT * FROM feature_flags WHERE feature_key=?" in source
        assert "before=before" in source


class TestThisSuiteCannotSilentlyShrink:
    def test_no_top_level_name_is_defined_twice(self):
        """A duplicate class name deletes the earlier class's tests.

        This is not hypothetical: ``TestPublicLabelOwnership`` was defined
        twice while the stage above was being written, and the second
        definition shadowed the first. The suite went from 150 collected to
        153 after seven tests were *added* — a net gain, which is exactly why
        watching the total pass is not enough to notice. Four assertions about
        the migration's label decisions stopped running and nothing was red.
        """
        tree = ast.parse(pathlib.Path(__file__).read_text())
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        ]
        duplicates = {n for n in names if names.count(n) > 1}
        assert not duplicates, f"shadowed, so the earlier definition never runs: {duplicates}"


# ---------------------------------------------------------------------------
# Stages 15-18 — drift detection, severity, and the CI gate's two halves
# ---------------------------------------------------------------------------


MEASURED_DAY = datetime.date(2026, 9, 24)


class TestDriftFindingRefusesToBeUseless:
    def test_a_finding_must_say_what_would_fix_it(self):
        with pytest.raises(ValueError, match="what would fix it"):
            drift.DriftFinding("k", "KIND", "STATIC_CONTRACT", "HIGH", "detail", "   ")

    def test_severity_and_class_are_closed_vocabularies(self):
        with pytest.raises(ValueError, match="drift class"):
            drift.DriftFinding("k", "KIND", "SOMEWHAT_STATIC", "HIGH", "d", "r")
        with pytest.raises(ValueError, match="severity"):
            drift.DriftFinding("k", "KIND", "STATIC_CONTRACT", "URGENT", "d", "r")


class TestOnlyStaticDriftBlocks:
    """The split is the whole design, so it is asserted directly."""

    def test_a_critical_production_finding_does_not_block(self):
        finding = drift.DriftFinding(
            "marketplace_checkout", "CONFIG_OVERSTATED", "PRODUCTION_OBSERVED",
            "CRITICAL", "d", "r",
        )
        assert not finding.blocking
        assert drift.gate((finding,)).passed

    def test_a_high_static_finding_blocks(self):
        finding = drift.DriftFinding(
            "x", "SEEDED_KEY_NOT_IN_INVENTORY", "STATIC_CONTRACT", "HIGH", "d", "r",
        )
        assert finding.blocking
        assert not drift.gate((finding,)).passed

    def test_a_low_static_finding_does_not_block(self):
        finding = drift.DriftFinding("x", "K", "STATIC_CONTRACT", "LOW", "d", "r")
        assert not finding.blocking

    def test_passing_does_not_mean_clean(self):
        """``passed`` answers "may this build proceed", not "is everything fine".

        Today's tree passes the gate with three CRITICAL findings outstanding.
        If those two questions were ever collapsed into one answer, either the
        static half stops blocking or the observed half starts to — and both
        are failures this suite is here to prevent.
        """
        result = drift.detect(today=MEASURED_DAY)
        assert result.passed
        assert any(f.severity == "CRITICAL" for f in result.reported)


class TestTheDriftDetectorFindsTodaysRealDrift:
    def test_it_reproduces_the_four_known_findings(self):
        result = drift.detect(today=MEASURED_DAY)
        observed = {
            f.capability_key: f.kind
            for f in result.reported
            if f.drift_class == "PRODUCTION_OBSERVED"
        }
        assert observed == {
            "admin_command": "CONFIG_OVERSTATED",
            "premium_identity": "CONFIG_OVERSTATED",
            "premium_advanced_tools": "CONFIG_OVERSTATED",
            "marketplace_checkout": "CONFIG_UNDERSTATED",
        }

    def test_the_tree_has_no_static_drift(self):
        assert drift.static_findings(today=MEASURED_DAY) == ()

    def test_checkout_is_understated_not_overstated(self):
        """Direction matters more than presence.

        Understated means production is *more* exposed than the row says, so
        wiring the row up withdraws a live capability. Overstated means the
        reverse. A detector that reported "drift" without the direction would
        have been satisfied by either, and the remedies are opposites.
        """
        finding = next(
            f for f in drift.detect(today=MEASURED_DAY).reported
            if f.capability_key == "marketplace_checkout"
        )
        assert finding.kind == "CONFIG_UNDERSTATED"
        assert "withdraw" in finding.remedy

    def test_the_report_is_byte_stable(self):
        first = drift.report(drift.detect(today=MEASURED_DAY))
        second = drift.report(drift.detect(today=MEASURED_DAY))
        assert first == second
        assert "STATIC CONTRACT DRIFT" in first
        assert "PRODUCTION OBSERVED DRIFT" in first


class TestStaticChecksCanActuallyFire:
    """Stage 28 non-vacuity: every static check, shown going red.

    ``test_the_tree_has_no_static_drift`` above passes on an empty tuple, which
    is exactly what a detector that checks nothing also returns. These are the
    tests that tell the two apart.
    """

    def test_a_seeded_key_missing_from_the_inventory_is_caught(self, monkeypatch):
        monkeypatch.setattr(
            caps, "SEEDED_STATES", {**caps.SEEDED_STATES, "ghost_feature": "enabled"}
        )
        kinds = {f.kind for f in drift.static_findings(today=MEASURED_DAY)}
        assert "SEEDED_KEY_NOT_IN_INVENTORY" in kinds

    def test_an_inventory_key_with_no_seeded_row_is_caught(self, monkeypatch):
        trimmed = {k: v for k, v in caps.SEEDED_STATES.items() if k != "pulse_posts"}
        monkeypatch.setattr(caps, "SEEDED_STATES", trimmed)
        findings = {f.kind: f for f in drift.static_findings(today=MEASURED_DAY)}
        assert "INVENTORY_KEY_NOT_SEEDED" in findings
        assert findings["INVENTORY_KEY_NOT_SEEDED"].capability_key == "pulse_posts"

    def test_a_capability_without_evidence_is_caught(self, monkeypatch):
        thin = Capability(
            key="pulse_posts",
            deployment_state="LIVE_GLOBAL",
            eligibility=STANDARD,
            evidence="it works",
        )
        others = tuple(c for c in caps.CAPABILITIES if c.key != "pulse_posts")
        monkeypatch.setattr(caps, "CAPABILITIES", (thin,) + others)
        findings = {f.kind for f in drift.static_findings(today=MEASURED_DAY)}
        assert "CAPABILITY_WITHOUT_EVIDENCE" in findings

    def test_a_legacy_word_becoming_parseable_is_caught(self, monkeypatch):
        """The vocabularies re-merging is the road back to Mission 1's bug.

        Simulated by adding ``ENABLED`` to the runtime vocabulary — which is
        precisely the change that would let ``admin_command`` be stored as the
        one state meaning "unconditionally visible and usable" again, on 199
        admin-guarded routes.
        """
        import services.pulse_control_plane.parsing as parsing_module

        monkeypatch.setattr(
            parsing_module, "DEPLOYMENT_STATES", set(DEPLOYMENT_STATES) | {"ENABLED"}
        )
        findings = {f.kind: f for f in drift.static_findings(today=MEASURED_DAY)}
        assert "LEGACY_WORD_PARSES_AT_RUNTIME" in findings
        assert findings["LEGACY_WORD_PARSES_AT_RUNTIME"].severity == "CRITICAL"

    def test_the_migration_writing_the_legacy_column_is_caught(self, monkeypatch):
        """The mission's primary rule, proven detectable rather than assumed."""
        monkeypatch.setattr(
            mig,
            "_UPDATE",
            "UPDATE feature_flags SET state = ?, deployment_state = ? WHERE feature_key = ?",
        )
        findings = {f.kind: f for f in drift.static_findings(today=MEASURED_DAY)}
        assert "MIGRATION_WRITES_LEGACY_STATE" in findings
        assert findings["MIGRATION_WRITES_LEGACY_STATE"].severity == "CRITICAL"
        assert not drift.gate(tuple(findings.values())).passed

    def test_deployment_state_alone_does_not_trip_the_legacy_column_check(self):
        """``deployment_state = ?`` contains ``state = ?``.

        The substring version of this check passes while the legacy column is
        being overwritten and fails while it is not — wrong in both directions
        at once. Asserted here because the real statement happens to contain
        both columns, so a regression would be invisible in the test above.
        """
        assert "state" not in drift._migration_written_columns()
        assert "deployment_state" in drift._migration_written_columns()

    def test_a_stale_measurement_is_caught_but_does_not_block(self, monkeypatch):
        late = MEASURED_DAY + datetime.timedelta(days=drift.MEASUREMENT_MAX_AGE_DAYS + 1)
        findings = {f.kind: f for f in drift.static_findings(today=late)}
        assert "MEASUREMENT_STALE" in findings
        assert findings["MEASUREMENT_STALE"].severity == "MEDIUM"
        # Below the blocking line on purpose: this is the one static finding
        # that depends on the calendar rather than the tree, so it must not be
        # able to turn a build red on a day nobody changed anything.
        assert not findings["MEASUREMENT_STALE"].blocking

    def test_a_fresh_measurement_is_not_flagged(self):
        """The other direction. Without it, the staleness rule could be
        unconditional and the test above would still pass."""
        kinds = {f.kind for f in drift.static_findings(today=MEASURED_DAY)}
        assert "MEASUREMENT_STALE" not in kinds


class TestObservedDetectorIsNotHardWiredToOneRecording:
    def test_it_reads_the_signals_it_is_given(self):
        """A detector that can only run against the shipped recording cannot be
        shown to detect anything."""
        findings = drift.observed_findings(
            signals={"pulse_posts": PRODUCTION_SIGNALS["pulse_posts"]},
            stored={"pulse_posts": "disabled"},
        )
        kinds = {f.kind for f in findings}
        assert "CONFIG_UNDERSTATED" in kinds

    def test_an_orphaned_row_is_low_and_named(self):
        findings = drift.observed_findings(signals={}, stored={"deleted_feature": "enabled"})
        orphan = next(f for f in findings if f.kind == "ORPHANED_CONFIG_ROW")
        assert orphan.severity == "LOW"
        assert orphan.capability_key == "deleted_feature"


# ---------------------------------------------------------------------------
# Stage 19/20 — the dead environment gates
# ---------------------------------------------------------------------------


class TestDeadGateCatalog:
    def test_all_fourteen_are_still_dead(self):
        """Re-audited 2026-09-24 against 297 live Railway variables."""
        assert len(env_gates.DEAD_GATES) == 14
        assert len({g.name for g in env_gates.DEAD_GATES}) == 14

    def test_the_catalog_records_that_the_removal_happened(self):
        """The fourteen are gone from Railway, and this file is the only record.

        Pinned because the failure it guards against is the file drifting back
        into sounding like a proposal. A reader who takes it as one re-runs the
        removal — harmless — or, much worse, reads ``production_value`` as
        current state and reasons about production from fourteen values that
        have not existed since :data:`env_gates.RETIRED_AT`.
        """
        assert env_gates.RETIRED_AT == "2026-09-24"
        assert "284" in env_gates.RETIRED_FROM, "the post-removal count is the claim"
        assert env_gates.RETIREMENT_DEPLOYMENT, (
            "the deployment id is the only durable handle on the boot that "
            "tested the removal; Railway's log retention has already dropped it"
        )

    def test_the_two_deceptive_switches_are_singled_out(self):
        """Fourteen dead variables are clutter; two of them are dead kill
        switches for live integrations, and that is a different problem."""
        assert {g.name for g in env_gates.deceptive_gates()} == {"ENABLE_SMS", "ENABLE_TELEGRAM"}
        for gate in env_gates.deceptive_gates():
            assert gate.real_gate, "a deceptive switch must name what really decides"

    def test_a_migrate_disposition_must_name_a_destination(self):
        with pytest.raises(ValueError, match="where the behaviour is going"):
            env_gates.DeadGate("X", "true", "MIGRATE", "", "because", "LOW")

    def test_the_removal_plan_includes_its_own_rollback(self):
        plan = env_gates.removal_plan()
        for gate in env_gates.DEAD_GATES:
            assert f"delete {gate.name}" in plan
            assert f"set {gate.name}={gate.production_value}" in plan

    def test_every_command_in_the_plan_is_one_the_cli_accepts(self):
        """The catalogued command was not a command, and running it was the
        only thing that would have said so.

        It read ``railway variables --service CoinPilotX --remove NAME``.
        ``--remove`` is not a flag the Railway CLI has. Nobody noticed for two
        missions because the block was written to be printed, and a printed
        command is never wrong until somebody is depending on it.

        This asserts the shape against the CLI's real grammar — subcommand
        first, then flags — which is weaker than running it and stronger than
        the nothing that was there before. The rollback half additionally has to
        carry ``--skip-deploys``, or restoring fourteen variables costs fourteen
        restarts.
        """
        lines = [
            line
            for line in env_gates.removal_plan().splitlines()
            if line.startswith("railway variable ")
        ]
        assert len(lines) == 28, "fourteen removals and fourteen restores"
        for line in lines:
            verb = line.split()[2]
            assert verb in ("delete", "set"), f"not a CLI subcommand: {line}"
            assert "--remove" not in line, "the flag that never existed is back"
            if verb == "set":
                assert "--skip-deploys" in line, (
                    f"a restore without --skip-deploys redeploys per variable: {line}"
                )

    def test_the_catalog_going_stale_is_a_blocking_finding(self, monkeypatch):
        """A reader appearing turns "delete this" into "delete a live gate".

        Simulated with ``BREVO_SMS_ENABLED``, which is real and *is* read — so
        this also proves the reader scan looks at the files it claims to.
        """
        stale = env_gates.DeadGate(
            "BREVO_SMS_ENABLED", "true", "DELETE", "", "pretend nothing reads it", "LOW"
        )
        monkeypatch.setattr(env_gates, "DEAD_GATES", (stale,))
        findings = {f.kind: f for f in drift.static_findings(today=MEASURED_DAY)}
        assert "DEAD_GATE_GAINED_A_READER" in findings
        assert findings["DEAD_GATE_GAINED_A_READER"].blocking

    def test_the_describer_is_not_counted_as_a_reader(self):
        """``env_gates.py`` names all fourteen. If the scan read itself, every
        one would report a reader on a healthy tree — the same mistake as
        counting an audit script, one level up."""
        assert drift._readers_of("UNDX_METRICS_ENABLED") == ()


# ---------------------------------------------------------------------------
# Stage 22 — shadow evaluation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cells():
    return shadow.compare_all()


class TestShadowCoversEveryCell:
    def test_the_matrix_is_exhaustive(self, cells):
        """Fifteen capabilities by five subject classes, with nothing sampled.

        The mission asks for evaluation alongside the legacy engine on the
        request path. There is no request path — ``feature_flags`` gates
        nothing — so the alternative has to be strictly stronger rather than a
        convenient substitute. It is: both engines can see only four facts
        about a subject, so five classes exhaust them, and seventy-five cells
        is small enough to enumerate. Live shadowing would have covered
        whichever cells that week's traffic happened to touch.
        """
        assert len(cells) == 75
        assert len({c.capability_key for c in cells}) == 15
        assert len({c.subject_key for c in cells}) == 5
        assert len({(c.capability_key, c.subject_key) for c in cells}) == 75

    def test_subject_classes_are_the_ones_either_engine_can_distinguish(self):
        """A sixth class would be describing a distinction neither side makes."""
        facts = set(shadow._POLICY_FACT.values())
        assert facts == {"is_premium", "is_admin", "is_owner"}
        assert {s.key for s in shadow.SUBJECTS} == {
            "anonymous", "member", "premium", "admin", "owner",
        }

    def test_an_owner_who_is_not_an_admin_is_not_a_subject(self):
        with pytest.raises(ValueError, match="strictly narrower than admin"):
            shadow.Subject("bad", "owner without admin", is_owner=True)


class TestShadowCannotBeUsedToDecide:
    def test_the_comparison_exposes_no_answer(self, cells):
        """There is no ``visible``/``usable`` to read off the result.

        A caller has to reach into ``.legacy`` or ``.candidate`` and thereby
        name which engine it trusts. Removing the convenient shape is a
        stronger guarantee than a docstring asking people not to use it.
        """
        fields = set(shadow.ShadowComparison.__dataclass_fields__)
        assert not fields & {"visible", "usable", "decision", "allowed"}
        assert not hasattr(cells[0], "visible")
        assert not hasattr(cells[0], "usable")

    def test_nothing_on_the_request_path_imports_shadow(self):
        """Shadow evaluation something can reach is not shadow evaluation."""
        importers = []
        for path, text in drift._searchable_sources():
            if "pulse_control_plane.shadow" in text or "import shadow" in text:
                importers.append(path)
        assert importers == [], f"shadow reached from the request path: {importers}"

    def test_shadow_never_fabricates_a_verdict_for_an_ungated_policy(self):
        """``None`` means nobody was asked, and must not become ``allowed=True``.

        Handing :func:`evaluate` a manufactured permission would make this
        module an authority in a simulation, which is the one thing the whole
        package is arranged to prevent — simulations get promoted.
        """
        for subject in shadow.SUBJECTS:
            assert shadow.verdict_for(STANDARD, subject) is None
            assert shadow.verdict_for(ANONYMOUS, subject) is None


class TestBothEnginesAreFedTheSameFacts:
    def test_the_policy_fact_map_matches_what_evaluate_flag_branches_on(self):
        """If shadow fed the new model better answers it would look better for free.

        ``evaluate_flag`` branches on exactly three user keys. The mapping used
        to simulate each authority must draw on those same three and nothing
        else, or the comparison measures the mapping rather than the models.
        """
        source = inspect.getsource(feature_flag_engine.evaluate_flag)
        consulted = set(re.findall(r'user\.get\("(\w+)"\)', source))
        assert consulted == {"is_admin", "is_owner", "is_premium"}
        assert set(shadow._POLICY_FACT.values()) <= consulted

    def test_the_legacy_side_is_given_everything_it_reads(self):
        """``compare`` passes only ``state``. That is not a simplification."""
        source = inspect.getsource(feature_flag_engine.evaluate_flag)
        assert set(re.findall(r'flag\.get\("(\w+)"\)', source)) == {"state"}

    def test_four_stored_columns_are_read_by_no_evaluator(self):
        """``premium_required``, ``owner_only``, ``internal_only`` and
        ``rollout_percentage`` look like access controls and gate nothing.

        They are written by the seeder and rendered by the admin page, and the
        one function that decides access never opens them. Worth an assertion
        because a reader glancing at the schema would reasonably assume the
        opposite.
        """
        source = inspect.getsource(feature_flag_engine.evaluate_flag)
        for column in ("premium_required", "owner_only", "internal_only", "rollout_percentage"):
            assert column not in source


class TestTheCutoverDiffIsWhatWeThinkItIs:
    def test_exactly_three_widenings_and_they_are_all_checkout(self, cells):
        """The only capability whose access grows is the one that took money.

        A cutover rule of "refuse every widening" sounds prudent and would
        preserve the single row that is lying about a live payment path. The
        direction of a change is not its justification.
        """
        wider = shadow.widenings(cells)
        assert len(wider) == 3
        assert {c.capability_key for c in wider} == {"marketplace_checkout"}
        assert {c.subject_key for c in wider} == {"anonymous", "member", "premium"}
        for c in wider:
            assert c.stored_state == "internal-only"
            assert c.reconciliation_verdict == "UNDERSTATED"

    def test_every_narrowing_belongs_to_an_overstated_row(self, cells):
        """A withdrawal is only defensible when the grant was an accident."""
        narrower = shadow.narrowings(cells)
        assert len(narrower) == 11
        assert {c.capability_key for c in narrower} == {
            "premium_identity", "premium_advanced_tools", "admin_command",
        }
        for c in narrower:
            assert c.reconciliation_verdict == "OVERSTATED"

    def test_admin_command_is_hidden_from_non_admins_not_merely_locked(self, cells):
        """Same denial, opposite correct rendering.

        A non-premium user should *see* a Premium feature — that is the upsell.
        A member should not see the admin surface at all. The two narrowings
        land on different levels and that difference is the reason
        ``denied_visibility`` exists as a field rather than a convention.
        """
        by_cell = {(c.capability_key, c.subject_key): c for c in cells}
        assert by_cell[("admin_command", "member")].candidate_level == "HIDDEN"
        assert by_cell[("premium_identity", "member")].candidate_level == "VISIBLE_ONLY"

    def test_owners_lose_premium_features_at_cutover(self, cells):
        """An intended narrowing, and one nobody would predict from the words.

        ``evaluate_flag`` treats an owner as premium for ``premium-only`` rows
        (``is_premium or is_owner``) — an implicit superuser rule with no
        authority behind it. The canonical entitlement service does not grant
        premium by role, so the new model denies. It surfaces here because both
        premium rows are stored as words meaning "everyone", not because any
        row is stored ``premium-only``.
        """
        by_cell = {(c.capability_key, c.subject_key): c for c in cells}
        for key in ("premium_identity", "premium_advanced_tools"):
            cell = by_cell[(key, "owner")]
            assert cell.divergence == "CANDIDATE_NARROWER"
            assert cell.candidate_reason_code == "ELIGIBILITY_DENIED"

    def test_a_matching_row_moves_nobody(self, cells):
        """The combination that would mean this package is wrong.

        If the stored row already agreed with measured production, switching
        engines must not change anybody's access. A cell here differs from the
        old engine *and* from the production it claims to describe.
        """
        assert shadow.unexplained(cells) == ()

    def test_eleven_of_fifteen_capabilities_do_not_move_at_all(self, cells):
        moving = {c.capability_key for c in cells if c.changes_behaviour}
        assert len(moving) == 4
        assert len({c.capability_key for c in cells} - moving) == 11


class TestShadowDoesNotGateAuthentication:
    def test_anonymous_resolves_exactly_like_member(self, cells):
        """The invariant behind the scariest-looking line in the report.

        ``marketplace_checkout`` shows ``anonymous: HIDDEN -> USABLE``, which
        reads as "activation lets logged-out people check out". It does not:
        the member cell says the same thing, because this package never asks
        whether anyone is logged in. ``require_account()`` on the route does,
        and it probed 401.

        Asserting the emptiness matters more than asserting any single cell. A
        capability that started resolving anonymous differently would invite
        somebody to delete a ``require_account()`` because "the capability
        model handles it".
        """
        assert shadow.authentication_gated_cells(cells) == ()

    def test_the_legacy_engine_has_the_same_blind_spot(self, cells):
        """Activation neither creates this property nor fixes it."""
        by_cell = {(c.capability_key, c.subject_key): c for c in cells}
        for key in {c.capability_key for c in cells}:
            assert (
                by_cell[(key, "anonymous")].legacy_level
                == by_cell[(key, "member")].legacy_level
            )

    def test_the_report_says_so_rather_than_leaving_it_to_be_inferred(self, cells):
        text = shadow.report(cells)
        assert "require_account()" in text
        assert "does not deny this" in text


class TestShadowIsNotHardWiredToTodaysTree:
    def test_a_stored_row_changing_moves_the_diff(self, cells):
        """Drive it with a synthetic production state and the answer must change.

        A module that could only ever describe today's tree could not be shown
        to notice anything, and every count above would be a coincidence of the
        fixture rather than a measurement.
        """
        repaired = dict(observations.STORED_STATES)
        repaired["marketplace_checkout"] = "enabled"
        moved = shadow.compare_all(stored=repaired)
        assert shadow.widenings(moved) == ()
        assert len(shadow.narrowings(moved)) == 11

    def test_an_inverted_row_turns_a_narrowing_into_a_widening(self):
        """``admin_command`` stored ``owner-only`` instead of ``enabled``."""
        inverted = dict(observations.STORED_STATES)
        inverted["admin_command"] = "owner-only"
        moved = shadow.compare_all(stored=inverted)
        cells = {
            (c.capability_key, c.subject_key): c
            for c in moved
            if c.capability_key == "admin_command"
        }
        assert cells[("admin_command", "admin")].divergence == "CANDIDATE_WIDER"
        assert cells[("admin_command", "member")].divergence == "AGREE"

    def test_a_divergence_class_that_does_not_follow_is_refused(self):
        """The classification is derived, so a hand-set one must be rejected."""
        with pytest.raises(ValueError, match="does not follow from"):
            shadow.ShadowComparison(
                capability_key="x",
                subject_key="member",
                stored_state="enabled",
                legacy_level="USABLE",
                candidate_level="HIDDEN",
                divergence="AGREE",
                reconciliation_verdict="MATCH",
                legacy_reason="",
                candidate_reason_code="AVAILABLE",
            )

    def test_the_report_is_byte_stable(self, cells):
        assert shadow.report(cells) == shadow.report(cells)


# ---------------------------------------------------------------------------
# Stages 21 + 23 — cutover order and the activation gate
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_everything():
    """The state of production after the Stage 11 migration has been applied."""
    return {c.key: c.deployment_state for c in caps.CAPABILITIES}


class TestTheGateBlocksToday:
    def test_activation_is_refused_on_the_current_tree(self):
        """Four known production findings, and the gate must say so."""
        result = activation.readiness()
        assert result.ready is False
        assert len(result.blockers) == 4
        assert all("OBSERVED" in b for b in result.blockers)

    def test_ready_with_blockers_is_not_a_representable_state(self):
        with pytest.raises(ValueError, match="ready with blockers"):
            activation.ActivationReadiness(ready=True, blockers=("something",))

    def test_the_script_exits_one_when_blocked(self):
        import subprocess

        proc = subprocess.run(
            [sys.executable, "scripts/capability_activation_check.py"],
            cwd=str(pathlib.Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1
        assert "READY: NO" in proc.stdout


class TestTheGateCanAlsoPass:
    def test_applying_the_migration_clears_every_blocker(self, migrated_everything):
        """A gate that cannot pass is the same failure as one that cannot fail.

        This is the check that proves the Stage 23 requirement is reachable.
        It very nearly was not: the migration writes new columns and never
        touches ``feature_flags.state``, so a zero-drift gate measured against
        the legacy word would have stayed red permanently, and the only way to
        ship would have been to delete the gate.
        """
        result = activation.readiness(migrated=migrated_everything)
        assert result.ready is True
        assert result.blockers == ()

    def test_a_partially_migrated_table_still_blocks(self, migrated_everything):
        """Incremental means per-wave, not per-row-left-half-done."""
        partial = dict(migrated_everything)
        partial.pop("marketplace_checkout")
        result = activation.readiness(migrated=partial)
        assert result.ready is False
        assert any("marketplace_checkout" in b for b in result.blockers)

    def test_every_drift_blocker_names_the_row_it_is_about(self):
        """A blocker that does not say which capability it concerns is a
        worse blocker, and this one was.

        The first version formatted drift findings as
        ``f"OBSERVED {severity} {kind}: {detail}"`` and dropped
        ``capability_key`` entirely, so today's production findings
        rendered as near-identical lines naming no row. The operator
        reading them would have to re-derive which of fifteen capabilities
        each one concerns — exactly the work the gate exists to have
        already done. Pinned separately from the partial-migration test
        above, which would still pass if some *other* blocker happened to
        mention the key.
        """
        result = activation.readiness()
        assert result.blockers, "expected today's tree to block"
        known = {c.key for c in caps.CAPABILITIES}
        checked = 0
        for blocker in result.blockers:
            if not blocker.startswith(("STATIC ", "OBSERVED ")):
                continue
            checked += 1
            assert any(key in blocker for key in known), (
                f"blocker names no capability: {blocker}"
            )
        assert checked, "no drift-derived blockers were examined"

    def test_an_edited_migrated_row_blocks(self, migrated_everything):
        """The stored column disagreeing with this package is not drift, it is
        somebody having changed one of them since."""
        tampered = dict(migrated_everything)
        tampered["premium_identity"] = "LIVE_GLOBAL"
        result = activation.readiness(migrated=tampered)
        assert any("MIGRATED ROW DIVERGED premium_identity" in b for b in result.blockers)


class TestTheStaleLegacyColumnIsOnlySafeBecauseNothingReadsIt:
    def test_evaluate_flag_has_no_call_sites(self):
        assert activation.legacy_engine_call_sites() == ()

    def test_a_definition_is_not_a_call_site(self):
        """The scan must not find ``def evaluate_flag(`` in the engine itself.

        The first version did, and concluded the legacy engine was live on a
        tree where it is called by nothing. Third instance of one mistake in
        this package: an auditor naming a variable is not a reader, a catalog
        describing a gate is not a gate, a definition is not a call site.
        """
        sources = dict(drift._searchable_sources())
        assert "def evaluate_flag(" in sources["services/feature_flag_engine.py"]
        assert "services/feature_flag_engine.py" not in activation.legacy_engine_call_sites()

    def test_a_real_call_site_blocks_activation(self, migrated_everything, monkeypatch):
        """Adding the first caller makes the stale column live again."""
        monkeypatch.setattr(
            activation, "legacy_engine_call_sites", lambda: ("services/somewhere.py",)
        )
        result = activation.readiness(migrated=migrated_everything)
        assert result.ready is False
        assert any("internal-only" in b and "payment path" in b for b in result.blockers)

    def test_the_legacy_column_cannot_be_neutralised_by_writing_to_it(self):
        """There is still no value meaning "this no longer decides anything".

        This conclusion survived the fail-closed fix, and the reason inverted —
        which is worth more than the conclusion, because the obvious reading of
        that fix is that it made the column safe to write. It did not. It
        changed which rows a careless write destroys.

        Before, every unrecognised word normalized to ``beta``, the most
        permissive state the engine has, so writing ``deprecated`` to retire a
        row would have *widened* all fifteen at once — ``admin_command`` among
        them, which fronts 199 admin routes.

        Now every unrecognised word normalizes to ``disabled``, so the same
        edit *withdraws* all fifteen — ``marketplace_checkout`` among them,
        which has taken 32 real orders. That is an outage rather than a breach,
        which is a different incident, not a smaller one.

        The column can be left wrong or dropped. It cannot be made inert.
        """
        for word in ("deprecated", "retired", "migrated", "", "   "):
            assert feature_flag_engine.normalize_state(word) == "disabled"
            assert feature_flag_engine.evaluate_flag({"state": word}, {}) == {
                "visible": False, "usable": False, "state": "disabled",
                "reason": "Feature disabled.",
            }

    def test_no_subject_can_see_through_an_unrecognised_word(self):
        """Fail-closed must hold for the privileged subjects too.

        ``internal-only`` stays usable for an admin and ``premium-only`` stays
        visible for everyone, so "restrictive" is not a single axis in this
        engine. A fallback that merely *looked* restrictive — landing on
        ``internal-only``, say — would still have handed every admin full
        access to a word nobody can interpret.
        """
        for subject in (
            {},
            {"user_id": 7},
            {"user_id": 1, "is_admin": True},
            {"user_id": 1, "is_owner": True},
            {"user_id": 1, "is_premium": True},
            {"user_id": 1, "is_admin": True, "is_owner": True, "is_premium": True},
        ):
            verdict = feature_flag_engine.evaluate_flag({"state": "nonsense"}, subject)
            assert verdict["visible"] is False, subject
            assert verdict["usable"] is False, subject

    def test_the_write_path_refuses_the_retirement_words_outright(self):
        """The one guard that makes the paragraph above hard to trip over.

        Reading an unparseable word has to answer something. Writing one does
        not, and ``state_for_write`` refuses — so an operator who reaches for
        ``deprecated`` gets an error instead of fifteen withdrawn capabilities.
        """
        for word in ("deprecated", "retired", "migrated", "", "   "):
            with pytest.raises(ValueError):
                feature_flag_engine.state_for_write(word)


class TestTheCutoverOrderIsDerivedNotDeclared:
    def test_wave_one_changes_nobody(self):
        """Any breakage in wave 1 is the plumbing, unambiguously.

        Wiring a capability is two changes at once — the request path starts
        consulting the control plane, and the answer it gets differs from the
        old one. If the first capability wired moves somebody, a failure cannot
        be attributed to either half.
        """
        cells = shadow.compare_all()
        moving = {c.capability_key for c in cells if c.changes_behaviour}
        wave_one = activation.waves()[0]
        assert wave_one.capability_keys
        assert not set(wave_one.capability_keys) & moving

    def test_the_order_follows_the_shadow_matrix_rather_than_a_hand_written_list(self):
        """A declared list would stop being no-op-first and still look right."""
        source = inspect.getsource(activation.waves)
        assert "_no_op_capabilities()" in source

    def test_the_widening_goes_last(self):
        waves = activation.waves()
        assert waves[-1].capability_keys == ("marketplace_checkout",)
        assert "PAYMENT" in caps.by_key("marketplace_checkout").protected

    def test_every_wave_has_a_rollback(self):
        for wave in activation.waves():
            assert wave.rollback

    def test_a_wave_without_a_rollback_is_refused(self):
        with pytest.raises(ValueError, match="not a wave, it is a launch"):
            activation.Wave(9, "x", ("pulse_posts",), "p", ("c",), "")

    def test_rolling_back_wave_three_means_unwiring_not_reverting(self):
        """The asymmetry that would cause the incident.

        Reverting the stored value restores ``internal-only``, which hides
        checkout from every non-admin — the rollback would cause the outage it
        was performed to avoid. The only safe undo is removing the consultation.
        """
        rollback = activation.waves()[-1].rollback
        assert "NOT reverting the stored value" in rollback


class TestCapabilitiesRefusedOutright:
    def test_admin_command_may_never_be_wired(self):
        """It governs the surface that would be needed to un-break it.

        ``/admin/capability-matrix`` is inside ``/admin/*``. A row wrongly
        denying admins removes the page that edits the row, and since the
        seeder no longer re-asserts stored values nothing restores it either.
        """
        assert "admin_command" in activation.NEVER_WIRE
        assert "capability-matrix" in activation.NEVER_WIRE["admin_command"]

    def test_livestream_is_refused_under_the_audio_hard_lock(self):
        assert "pulse_livestream" in activation.NEVER_WIRE
        assert "realtime_audio_change_policy" in activation.NEVER_WIRE["pulse_livestream"]

    def test_no_wave_can_schedule_a_never_wire_capability(self):
        with pytest.raises(ValueError, match="must never be wired"):
            activation.Wave(9, "x", ("admin_command",), "p", ("c",), "undo it")

    def test_the_refused_two_are_absent_from_every_wave(self):
        scheduled = {k for w in activation.waves() for k in w.capability_keys}
        assert not scheduled & set(activation.NEVER_WIRE)
        assert len(scheduled) == 13


class TestWideningsMustBeWrittenDownIndividually:
    def test_todays_three_are_all_accepted(self):
        cells = shadow.widenings(shadow.compare_all())
        for cell in cells:
            assert (cell.capability_key, cell.subject_key) in activation.ACCEPTED_WIDENINGS

    def test_acceptance_is_per_cell_not_per_capability(self, migrated_everything):
        """"Checkout widens" is a summary, and a summary hides the fourth cell."""
        assert all(
            isinstance(k, tuple) and len(k) == 2 for k in activation.ACCEPTED_WIDENINGS
        )
        trimmed = dict(activation.ACCEPTED_WIDENINGS)
        trimmed.pop(("marketplace_checkout", "premium"))
        with pytest.MonkeyPatch.context() as m:
            m.setattr(activation, "ACCEPTED_WIDENINGS", trimmed)
            result = activation.readiness(migrated=migrated_everything)
        assert result.ready is False
        assert any("UNREVIEWED WIDENING marketplace_checkout/premium" in b for b in result.blockers)

    def test_a_correct_but_unreviewed_widening_still_blocks(self, migrated_everything, monkeypatch):
        """Correct and reviewed are different properties; this checks the second.

        ``pulse_reels`` stored ``owner-only`` would be plainly wrong about a
        shipped App Store tab, so the model widening it is right — and it blocks
        anyway, because nobody wrote the grant down.
        """
        stored = dict(observations.STORED_STATES)
        stored["pulse_reels"] = "owner-only"
        result = activation.readiness(stored=stored, migrated=migrated_everything)
        assert result.ready is False
        assert any("UNREVIEWED WIDENING pulse_reels" in b for b in result.blockers)


class TestActivationPlanIsReadable:
    def test_the_plan_names_its_blockers_and_its_refusals(self):
        text = activation.plan()
        assert "READY: NO" in text
        assert "REFUSED OUTRIGHT" in text
        assert "WAVE 1" in text and "WAVE 3" in text

    def test_the_plan_is_byte_stable(self):
        result = activation.readiness()
        assert activation.plan(result) == activation.plan(result)


# ---------------------------------------------------------------------------
# Stage 24/25 — the guards in front of the capability write.
#
# ``bot_source()`` and ``bot_function_source()`` from the Stage 12/26 block
# above are reused rather than reimplemented: this file already pays for one
# AST parse of bot.py and a second copy of the same helper is how the suite
# grew a duplicate class name once before.
# ---------------------------------------------------------------------------


class TestTheGuardsInFrontOfTheWriteAreStillThere:
    """Re-verify the documented stack against the tree, not against memory.

    ``write_security.py`` states in prose what protects the capability write.
    Prose goes stale silently. Each guard carries a literal that must still be
    present in bot.py, and this re-greps them — a deliberately weak check that
    catches the realistic failure (a refactor dropping a line) and would miss
    the unrealistic one (somebody rewriting admin authentication correctly but
    differently).
    """

    def test_every_documented_guard_is_present(self):
        missing = write_security.unverified_predicates(bot_source())
        assert missing == (), (
            f"guards documented but no longer found in bot.py: {missing}. "
            "write_security.py is describing a tree that no longer exists."
        )

    def test_the_owner_check_is_inside_the_post_branch(self):
        """Narrower on write than on read, and only on write.

        ``system.view`` gates the page; owner level gates the change. If the
        owner check moved up to cover the whole handler it would hide the
        matrix from the very admins expected to audit it, and if it vanished
        every admin who can read the matrix could change production exposure.
        """
        handler = bot_function_source(write_security.WRITE_HANDLER)
        assert 'require_admin_page("system.view")' in handler
        # Assert presence before indexing: str.index raises ValueError, which
        # fails the test with a stack trace instead of saying that the owner
        # guard is gone. A mutation run surfaced exactly that.
        assert "admin_is_owner_level" in handler, (
            "the capability write is no longer owner-gated; any admin holding "
            "system.view could change production exposure"
        )
        post_branch = handler.index('if request.method == "POST":')
        assert handler.index("admin_is_owner_level") > post_branch

    def test_only_one_http_path_writes_the_table(self):
        """Three writes exist in bot.py; two are seeding, with no request path.

        Broader than ``_feature_flag_updates`` above, which sees ``UPDATE``
        only. A new ``INSERT`` or ``DELETE`` reachable from a route would be a
        second way to change production exposure and would not show up there.
        """
        writes = [
            # Strip the surrounding quotes: these are SQL string literals, and
            # the one-line ones arrive with a leading '"' that would defeat a
            # startswith check on the statement itself.
            line.strip().strip("\"'")
            for line in bot_source().splitlines()
            if re.search(
                r"(UPDATE|INSERT\s+(OR\s+IGNORE\s+)?INTO|DELETE\s+FROM)\s+feature_flags",
                line,
            )
        ]
        assert len(writes) == 3, f"new write path to feature_flags: {writes}"
        # The two seeding writes, neither reachable from a request.
        assert sum(1 for w in writes if w.startswith("INSERT OR IGNORE INTO feature_flags")) == 1
        assert sum(1 for w in writes if w.startswith("UPDATE feature_flags SET label=?")) == 1
        # And the one that is: the owner-gated admin form.
        assert sum(
            1 for w in writes if w.startswith("UPDATE feature_flags") and "label=?" not in w
        ) == 1

    def test_no_guard_claims_to_stop_everything(self):
        """Each entry has to say what it misses.

        A stack documented only by what it prevents reads as more complete
        than it is, and this one has two real gaps.
        """
        for guard in write_security.GUARDS:
            assert guard.does_not_stop.strip()

    def test_the_audit_record_is_not_counted_as_a_guard(self):
        """It is detection, not prevention, and the list says so."""
        names = {g.name for g in write_security.preventive_guards()}
        assert "audit record" not in names
        assert len(names) == 4


class TestCsrfCoverageAndAdminAuthReadOneSessionKey:
    """The finding: two defences that look independent are keyed on one value.

    ``enforce_admin_form_csrf`` skips enforcement when ``admin_user_id`` is
    absent from the session. That is safe today only because
    ``admin_current_user`` returns ``None`` for exactly those requests, so a
    request the hook declines to check is one the route declines to serve.

    Add a second admin auth leg — a bearer, an API key, an SSO header — and the
    auth check starts passing on requests where the CSRF hook does not run.
    Silently, and for all 79 admin form POSTs at once, not just this one.
    """

    def test_both_functions_read_the_same_session_key(self):
        key = write_security.SHARED_SESSION_KEY
        assert f'session.get("{key}")' in bot_function_source("enforce_admin_form_csrf")
        assert f'session.get("{key}")' in bot_function_source("admin_current_user")

    def test_the_csrf_hook_early_returns_on_a_missing_session(self):
        """Pin the early return itself, so the coupling stays visible.

        Deleting these two lines would make the hook strictly more protective
        and would fail this test. That is the right outcome: it should be a
        deliberate change made with this docstring in view, not a silent one.
        """
        hook = bot_function_source("enforce_admin_form_csrf")
        assert (
            f'if not session.get("{write_security.SHARED_SESSION_KEY}"):\n        return None'
            in hook
        )

    def test_admin_auth_has_exactly_one_leg(self):
        """The guard on the finding above.

        ``admin_current_user`` resolving an admin from anything but the session
        cookie is the event that turns the coupling into a hole. It has not
        happened; this is what notices if it does.
        """
        auth = bot_function_source("admin_current_user")
        for second_leg in ("Authorization", "request.headers", "bearer", "Bearer", "api_key"):
            assert second_leg not in auth, (
                f"admin_current_user now reads {second_leg!r}. If an admin can "
                "authenticate without session['admin_user_id'], "
                "enforce_admin_form_csrf stops running for those requests — for "
                "every admin form POST, not just the capability matrix. See "
                "write_security.py finding 1."
            )

    def test_verify_csrf_still_refuses_bearer(self):
        """Somebody has already stood at this spot and said no."""
        assert "allow_bearer=False" in bot_function_source("verify_csrf")

    def test_the_csrf_exempt_set_is_only_login_and_logout(self):
        """An addition here is an admin POST a foreign page can forge."""
        line = next(
            l for l in bot_source().splitlines() if l.startswith("CSRF_EXEMPT_ADMIN_PATHS")
        )
        for path in write_security.CSRF_EXEMPT:
            assert path in line
        assert line.count('"') == len(write_security.CSRF_EXEMPT) * 2

    def test_the_capability_route_is_covered_by_the_hook(self):
        """It is under /admin and not exempt, so the hook reaches it."""
        assert write_security.WRITE_ROUTE.startswith("/admin")
        assert write_security.WRITE_ROUTE not in write_security.CSRF_EXEMPT


class TestTheAuditLogIsAppendOnlyByConventionOnly:
    """Stage 25. The qualifier in the name is the whole finding.

    "The audit log is append-only" and "no code currently mutates the audit
    log" sound alike, and only the second is true. There is no trigger, no
    revoked grant, no constraint — the property holds because nothing violates
    it, which is a fact about this tree rather than about the table.
    """

    def test_no_route_mutates_either_audit_table(self):
        source = bot_source()
        for table in ("admin_audit_logs", "admin_activity_logs"):
            for verb in ("UPDATE", "DELETE FROM"):
                assert f"{verb} {table}" not in source, (
                    f"bot.py now issues {verb} against {table}. The capability "
                    "write's only after-the-fact record stops being append-only."
                )

    def test_the_only_mutation_in_the_tree_is_the_fixture_script(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        offenders = set()
        for path in list((root / "services").rglob("*.py")) + list(
            (root / "scripts").rglob("*.py")
        ):
            text = path.read_text(encoding="utf-8", errors="replace")
            for table in ("admin_audit_logs", "admin_activity_logs"):
                if f"DELETE FROM {table}" in text or f"UPDATE {table}" in text:
                    offenders.add(str(path.relative_to(root)))
        assert offenders == {"scripts/prelaunch_user_restriction_audit.py"}, (
            f"unexpected audit-log mutation: {sorted(offenders)}"
        )

    def test_the_fixture_script_scopes_its_deletes_to_rows_it_created(self):
        """Unscoped, those two lines would erase the audit trail of whatever
        database the script happened to be pointed at."""
        root = pathlib.Path(__file__).resolve().parents[2]
        text = (root / "scripts" / "prelaunch_user_restriction_audit.py").read_text()
        for table in ("admin_audit_logs", "admin_activity_logs"):
            line = next(l for l in text.splitlines() if f"DELETE FROM {table}" in l)
            assert "WHERE" in line
            assert "action LIKE 'prelaunch_%'" in line


# ---------------------------------------------------------------------------
# Stage 11 execution — the scripts that carry the migration to production.
#
# The control-plane package opens nothing. These two scripts are the only place
# it meets a database, which makes them the only place the package's guarantees
# can be lost, so they are tested here rather than left to the run that applies
# them.
# ---------------------------------------------------------------------------


def _load_script(name: str):
    """Import a file from ``scripts/`` without making it importable by accident.

    ``scripts/`` is not a package and must not become one — adding
    ``__init__.py`` would put a hundred-odd one-off audit scripts on the import
    path of every test in the repository.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        f"_script_{name}", root / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheMigrationRefusesAStaleAudit:
    """The manifest is only as good as the rows it was measured against.

    A compare-and-set catches a row that changes between this process's SELECT
    and its UPDATE. It does not catch a row that changed last week, because the
    guard value itself comes from the audit — a stale audit produces a manifest
    that is internally consistent and wrong. So the recording is compared to
    live production before a single write is proposed.
    """

    @pytest.fixture
    def script(self):
        return _load_script("capability_migration")

    def _live(self, **overrides):
        rows = {
            key: {"feature_key": key, "state": state, "rollout_percentage": 100,
                  "public_label": "Live"}
            for key, state in STORED_STATES.items()
        }
        rows.update(overrides)
        return rows

    def test_an_unchanged_production_is_not_stale(self, script):
        assert script._audit_is_still_current(self._live(), STORED_STATES) == []

    def test_an_edited_state_is_caught(self, script):
        live = self._live()
        live["marketplace_checkout"]["state"] = "enabled"
        stale = script._audit_is_still_current(live, STORED_STATES)
        assert len(stale) == 1
        assert "marketplace_checkout" in stale[0]
        assert "internal-only" in stale[0] and "enabled" in stale[0]

    def test_a_deleted_row_is_caught(self, script):
        live = self._live()
        del live["pulse_posts"]
        stale = script._audit_is_still_current(live, STORED_STATES)
        assert any("pulse_posts" in s and "no row in production" in s for s in stale)

    def test_a_row_the_audit_never_saw_is_caught(self, script):
        """A new capability config row is not harmless.

        It has no measured reality behind it, so nothing here can say whether
        it overstates or understates. Migrating the other fourteen and leaving
        it unmentioned would report a complete migration of an incomplete
        table.
        """
        live = self._live()
        live["pulse_events"] = {
            "feature_key": "pulse_events", "state": "beta",
            "rollout_percentage": 100, "public_label": "Beta",
        }
        stale = script._audit_is_still_current(live, STORED_STATES)
        assert any("pulse_events" in s and "never saw" in s for s in stale)

    def test_the_legacy_state_column_is_not_in_the_update(self):
        """The one column the migration must never write.

        ``state`` appears in the statement exactly once, in the WHERE clause,
        as the compare-and-set guard. Writing it is unsafe in a way that is not
        obvious from reading the statement: ``normalize_state`` maps every
        unrecognised word to ``beta``, the legacy engine's most permissive
        state, so even an edit intended to retire a row would widen it.
        """
        set_clause, where_clause = mig._UPDATE.split("WHERE")
        assert "state" not in set_clause.replace("deployment_state", "").replace(
            "eligibility_policy", ""
        )
        assert "state = ?" in where_clause


class TestTheActivationCheckNeedsEvidenceNotOptimism:
    @pytest.fixture
    def script(self):
        return _load_script("capability_activation_check")

    def test_without_a_database_the_check_blocks(self, script, monkeypatch, capsys):
        """"Could not confirm the migration" and "the migration did not happen"
        deserve the same answer, and this is the one that fails closed."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_PUBLIC_URL", raising=False)
        monkeypatch.setattr(sys, "argv", ["capability_activation_check.py"])
        assert script.main() == 1

    def test_wave_one_entry_does_not_name_an_unreachable_gate(self):
        """``--strict`` measures the legacy word the migration may not repair.

        It therefore reports four findings permanently, and naming it as an
        entry criterion would have made wave 1 unreachable — which does not
        stop a cutover, it just teaches the operator that the entry criteria
        are decorative. The criterion must name a check that can actually go
        green.
        """
        wave_one = activation.waves()[0]
        assert wave_one.number == 1
        assert not any("--strict" in c for c in wave_one.entry_criteria), (
            "wave 1 entry names a gate that can never pass"
        )
        assert any(
            "capability_activation_check" in c for c in wave_one.entry_criteria
        ), "wave 1 must name the check that reads the migrated columns"

    def test_the_gate_reads_only_migrated_rows(self, script):
        """A NULL ``deployment_state`` is an unmigrated row, not a migrated one
        holding nothing. Averaging the two would hide a partial migration."""
        source = inspect.getsource(script.read_migrated)
        assert "deployment_state IS NOT NULL" in source
        assert "readonly=True" in source
