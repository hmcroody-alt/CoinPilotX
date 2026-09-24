"""Pins the facts that make wiring the control plane unsafe today.

These tests exist to stop a future change from *quietly* becoming safe. Every
one of them asserts a property of the code as it actually is, so if somebody
repairs the seeded states or changes ``evaluate_flag``'s polarity, the relevant
test fails and forces the inventory to be updated alongside the fix rather than
after it.
"""

from __future__ import annotations

import pytest

from services import feature_flag_engine
from services.pulse_control_plane import legacy, reconcile
from services.pulse_control_plane.classify import classify_env_gate


class TestEngineBehaviourTheInventoryDependsOn:
    """The claims :mod:`legacy` makes about ``evaluate_flag``, verified."""

    def test_internal_only_hides_the_feature_from_a_normal_user(self):
        """The whole reason ``marketplace_checkout`` is a blocker.

        The seeded row says ``internal-only``. If that state did not actually
        withdraw the feature, the activation gate would be over-cautious and
        this suite should say so.
        """
        verdict = feature_flag_engine.evaluate_flag({"state": "internal-only"}, {"user_id": 7})
        assert verdict["visible"] is False
        assert verdict["usable"] is False

    def test_internal_only_stays_visible_for_an_admin(self):
        verdict = feature_flag_engine.evaluate_flag(
            {"state": "internal-only"}, {"user_id": 1, "is_admin": True}
        )
        assert verdict["usable"] is True

    def test_beta_is_not_a_restriction(self):
        """``beta`` grants full access, which is easy to assume otherwise.

        Six seeded rows carry ``beta``. It would be natural to read that as
        "limited audience" and conclude those rows are harmless; they are
        harmless for the opposite reason — ``beta`` restricts nothing.
        """
        verdict = feature_flag_engine.evaluate_flag({"state": "beta"}, {"user_id": 7})
        assert verdict["visible"] is True
        assert verdict["usable"] is True

    @pytest.mark.parametrize("bogus", ["disabeld", "OFF-ish", "", None, "internal only", "unknown"])
    def test_an_unrecognised_state_fails_open_to_full_access(self, bogus):
        """The database plane's polarity, pinned.

        ``normalize_state`` maps anything it does not recognise to ``beta``, and
        ``beta`` is visible and usable. So a typo in the admin form — including
        ``"internal only"`` with a space instead of a hyphen, which is the most
        plausible one — grants full public access rather than erroring.

        This is the single most important asymmetry in the whole inventory: the
        environment plane fails closed, this one fails open.
        """
        assert feature_flag_engine.normalize_state(bogus) == "beta"
        verdict = feature_flag_engine.evaluate_flag({"state": bogus}, {"user_id": 7})
        assert verdict["usable"] is True

    def test_rollout_percentage_is_not_consulted(self):
        """``rollout_percentage`` is stored, rendered, and ignored.

        A 0% rollout and a 100% rollout produce the same verdict, so the field
        offers an operator a control that does nothing.
        """
        at_zero = feature_flag_engine.evaluate_flag(
            {"state": "enabled", "rollout_percentage": 0}, {"user_id": 7}
        )
        at_full = feature_flag_engine.evaluate_flag(
            {"state": "enabled", "rollout_percentage": 100}, {"user_id": 7}
        )
        assert at_zero == at_full


class TestLegacyReconciliation:
    def test_every_row_is_reconciled(self):
        assert legacy.unreconciled() == ()

    def test_all_fifteen_seeded_keys_are_covered(self):
        """The inventory must not silently drop a feature.

        Derived from the engine's own definitions rather than hardcoded, so a
        sixteenth feature added to ``FEATURE_DEFINITIONS`` fails here until it
        has been reconciled too.
        """
        defined = {item["feature_key"] for item in feature_flag_engine.FEATURE_DEFINITIONS}
        inventoried = {row.feature_key for row in legacy.LEGACY_ROWS}
        assert inventoried == defined

    def test_each_rows_seeded_state_matches_the_engine_seed(self):
        """Guards against the inventory drifting from what is actually seeded."""
        seeded = {i["feature_key"]: i["state"] for i in feature_flag_engine.FEATURE_DEFINITIONS}
        for row in legacy.LEGACY_ROWS:
            assert row.seeded_state == seeded[row.feature_key], row.feature_key

    def test_checkout_is_the_known_regression(self):
        keys = [row.feature_key for row in legacy.regressions_if_wired()]
        assert keys == ["marketplace_checkout"]

    def test_checkout_row_disagrees_with_its_seed(self):
        row = legacy.by_key("marketplace_checkout")
        assert row.seeded_state == "internal-only"
        assert row.verdict == "LIVE_GLOBAL"

    def test_every_row_carries_evidence(self):
        for row in legacy.LEGACY_ROWS:
            assert row.evidence.strip(), row.feature_key


class TestActivationGate:
    def test_the_gate_refuses_today(self):
        with pytest.raises(reconcile.ActivationBlocked) as excinfo:
            reconcile.activation_gate()
        assert "marketplace_checkout" in str(excinfo.value)

    def test_the_gate_has_no_override(self):
        """An override argument would be used during the first incident."""
        import inspect

        assert list(inspect.signature(reconcile.activation_gate).parameters) == []

    def test_summary_reports_blocked(self):
        assert reconcile.summary()["activation_allowed"] is False


class TestClassificationRule:
    def test_a_gate_with_no_reader_is_dead_however_true_it_looks(self):
        """The rule that prevents the inventory's worst failure mode.

        ``ENABLE_TELEGRAM=true`` is set in production and read by nothing.
        Classifying on value alone would call it live.
        """
        verdict = classify_env_gate(
            "ENABLE_TELEGRAM", observed="true", default=None, reader_count=0
        )
        assert verdict.verdict == "DEAD"
        assert verdict.effective is None

    def test_an_unset_gate_is_judged_by_its_default(self):
        closed = classify_env_gate(
            "BUSINESS_OS_ADS_INTELLIGENCE", observed=None, default="", reader_count=2
        )
        assert closed.verdict == "DISABLED"

        opened = classify_env_gate(
            "PUSH_TRACE_ENABLED", observed=None, default="1", reader_count=1, fails_open=True
        )
        assert opened.verdict == "LIVE_GLOBAL"

    def test_the_same_default_can_mean_opposite_things(self):
        """Polarity is carried explicitly because the default cannot imply it."""
        closed = classify_env_gate("A", observed=None, default="", reader_count=1, fails_open=False)
        opened = classify_env_gate("B", observed=None, default="", reader_count=1, fails_open=True)
        assert closed.verdict == "DISABLED"
        assert opened.verdict == "LIVE_GLOBAL"

    def test_an_uninterpretable_value_is_unknown_not_a_guess(self):
        verdict = classify_env_gate(
            "BUSINESS_OS_ENTITLEMENTS", observed="canonical", default="", reader_count=9
        )
        assert verdict.verdict == "UNKNOWN"

    def test_verdicts_must_come_from_the_vocabulary(self):
        from services.pulse_control_plane.classify import Classification

        with pytest.raises(ValueError):
            Classification(key="x", verdict="PROBABLY_FINE", effective=True, evidence="")
