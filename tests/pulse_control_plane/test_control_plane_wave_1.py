"""Wave 1 — the request-path consultation.

The claim wave 1 rests on is that arming the consultation changes nothing. That
claim is cheap to make and the only one worth testing here, so most of this
file is spent proving it cell by cell rather than asserting it once.

The rest guards the two ways a "plumbing" wave stops being plumbing: by
answering for a capability that belongs to a later wave, and by arming against
a database nobody verified.
"""

from __future__ import annotations

import pytest

from services import feature_flag_engine
from services.pulse_control_plane import activation, capabilities, model, runtime, shadow


def rows_matching_registry() -> dict[str, dict]:
    """The ``feature_flags`` content a correctly migrated production would hold."""
    return {
        cap.key: {
            "deployment_state": cap.deployment_state,
            "eligibility_policy": cap.eligibility.key,
        }
        for cap in capabilities.CAPABILITIES
    }


@pytest.fixture(autouse=True)
def _disarmed():
    """Every test starts from a process that has never armed.

    Arming state is module-global by design — it is a boot decision, not a
    request one — so a test that armed and did not clean up would hand the next
    test a consultation it never asked for, and the disarmed-by-default tests
    would pass or fail depending on file order.
    """
    runtime.reset_for_tests()
    yield
    runtime.reset_for_tests()


class TestArmingTakesTwoIndependentFacts:
    def test_a_process_that_never_armed_has_not_decided_anything(self):
        """``None`` is not the same as a disarmed result.

        A process that never called :func:`~runtime.arm` has made no decision;
        one that armed and refused has made one, and the difference is the
        difference between a deploy that skipped the boot check and a deploy
        where the boot check said no.
        """
        assert runtime.arming() is None
        assert runtime.consult("pulse_posts") is None
        assert runtime.permits("pulse_posts") is True

    def test_the_environment_variable_alone_does_not_arm(self):
        """Permission to ask is not evidence that the answer is trustworthy.

        This is the property that stops somebody enabling the consultation
        against a table nobody reconciled. ``live_rows=None`` is the shape of
        "the boot check could not run", which disarms — not because an
        unreadable table is evidence of a problem with the rows, but because it
        is the absence of the evidence arming requires.
        """
        result = runtime.arm(None, environ={runtime.CONSULTATION_ENV: "true"})
        assert result.armed is False
        assert "could not be read" in result.reason
        assert runtime.permits("pulse_posts") is True

    def test_verified_rows_alone_do_not_arm(self):
        """A clean database is not an instruction to start consulting."""
        result = runtime.arm(rows_matching_registry(), environ={})
        assert result.armed is False
        assert runtime.CONSULTATION_ENV in result.reason

    def test_both_together_arm(self):
        result = runtime.arm(
            rows_matching_registry(), environ={runtime.CONSULTATION_ENV: "1"}
        )
        assert result.armed is True
        assert result.disagreements == ()

    @pytest.mark.parametrize("falsy", ["", "0", "false", "no", "off", "maybe", "  "])
    def test_only_the_codebases_truthy_words_arm(self, falsy):
        result = runtime.arm(
            rows_matching_registry(), environ={runtime.CONSULTATION_ENV: falsy}
        )
        assert result.armed is False

    def test_a_single_disagreeing_row_disarms_the_whole_process(self):
        """One wrong row is not a reason to trust the other nine.

        The boot check is not per-capability, and making it so would be the
        tempting refinement to reject. A stored row that disagrees with the
        registry means the migration did not land, or something edited the table
        afterwards; in either case the question "which rows can I still trust?"
        has no answer this module is in a position to give.
        """
        rows = rows_matching_registry()
        rows["pulse_posts"] = {
            "deployment_state": "DISABLED",
            "eligibility_policy": "STANDARD",
        }
        result = runtime.arm(rows, environ={runtime.CONSULTATION_ENV: "true"})
        assert result.armed is False
        assert result.blocked_by_disagreement
        assert any("pulse_posts" in d for d in result.disagreements)
        # ...and the other nine are not consulted either.
        assert runtime.consult("pulse_reels") is None

    def test_a_missing_wave_1_row_disarms(self):
        rows = rows_matching_registry()
        del rows["ai_assistant"]
        result = runtime.arm(rows, environ={runtime.CONSULTATION_ENV: "true"})
        assert result.armed is False
        assert any("ai_assistant" in d for d in result.disagreements)

    def test_the_legacy_state_column_is_not_part_of_the_check(self):
        """Verification reads the migrated columns and ignores ``state``.

        ``state`` is *expected* to disagree — that disagreement is the finding
        two missions were spent establishing, and the migration is forbidden to
        repair it. A boot check that compared it would be unsatisfiable, and an
        unsatisfiable gate does not stop a cutover; it teaches the next operator
        that the entry criteria are decorative.
        """
        rows = rows_matching_registry()
        for key, row in rows.items():
            row["state"] = capabilities.SEEDED_STATES.get(key, "beta")
        result = runtime.arm(rows, environ={runtime.CONSULTATION_ENV: "true"})
        assert result.armed is True

    def test_out_of_wave_rows_are_not_verified(self):
        """Wave 3's row may disagree for as long as wave 3 has not happened.

        ``marketplace_checkout`` is the row the whole package exists because of.
        Letting it block wave 1 would be the same unsatisfiable-gate mistake in
        a different column.
        """
        rows = rows_matching_registry()
        rows["marketplace_checkout"] = {
            "deployment_state": "NONSENSE",
            "eligibility_policy": "NONSENSE",
        }
        assert runtime.verify(rows) == ()
        assert runtime.arm(rows, environ={runtime.CONSULTATION_ENV: "1"}).armed is True


class TestArmingChangesNothing:
    """The claim wave 1 rests on, proved cell by cell.

    Ten capabilities against every subject class either engine can distinguish.
    If any cell moved, the wave would be a semantic change wearing the word
    "plumbing", which is the failure this whole ordering exists to prevent.
    """

    @pytest.mark.parametrize("key", sorted(runtime.WAVE_1_KEYS))
    @pytest.mark.parametrize("subject", shadow.SUBJECTS, ids=lambda s: s.key)
    def test_every_wave_1_cell_is_unchanged_by_arming(self, key, subject):
        seeded = capabilities.SEEDED_STATES[key]
        legacy = feature_flag_engine.evaluate_flag({"state": seeded}, subject.legacy_user())

        before = runtime.permits(key, subject_id=str(subject.key))
        runtime.arm(rows_matching_registry(), environ={runtime.CONSULTATION_ENV: "1"})
        after = runtime.permits(key, subject_id=str(subject.key))

        assert legacy["usable"] is True, f"{key}/{subject.key}: wave 1 assumed a no-op"
        assert before is True
        assert after is True, f"{key}/{subject.key} moved when the consultation armed"

    @pytest.mark.parametrize("key", sorted(runtime.WAVE_1_KEYS))
    def test_every_wave_1_capability_is_available_for_the_stated_reason(self, key):
        """Not merely allowed — allowed *because* it is deployed and ungated.

        A cell can be ``usable=True`` for the wrong reason, and ``AVAILABLE`` is
        the only reason that makes wave 1 a no-op rather than a coincidence.
        """
        runtime.arm(rows_matching_registry(), environ={runtime.CONSULTATION_ENV: "1"})
        decision = runtime.consult(key, subject_id="7")
        assert decision is not None
        assert decision.reason_code == "AVAILABLE"
        assert decision.deployment_state == "LIVE_GLOBAL"
        assert decision.eligibility_policy == "STANDARD"
        assert decision.source == "wave_1"

    def test_the_equivalence_check_can_actually_fail(self):
        """Non-vacuity. A no-op proof that cannot detect a change proves nothing.

        Arming against a registry where a wave-1 capability is ``DISABLED``
        must flip :func:`~runtime.permits`. If this passes while the assertion
        above also passes, the assertion above is measuring something else.
        """
        broken = tuple(
            model.Capability(
                key=c.key,
                deployment_state="DISABLED" if c.key == "pulse_posts" else c.deployment_state,
                eligibility=c.eligibility,
                evidence=c.evidence,
                runtime_authority=c.runtime_authority,
                protected=c.protected,
                rollout_percentage=c.rollout_percentage,
                confidence=c.confidence,
                notes=c.notes,
            )
            for c in capabilities.CAPABILITIES
        )
        original = capabilities.CAPABILITIES
        try:
            capabilities.CAPABILITIES = broken
            rows = {
                c.key: {
                    "deployment_state": c.deployment_state,
                    "eligibility_policy": c.eligibility.key,
                }
                for c in broken
            }
            assert runtime.arm(rows, environ={runtime.CONSULTATION_ENV: "1"}).armed is True
            assert runtime.permits("pulse_posts") is False
            decision = runtime.consult("pulse_posts")
            assert decision is not None
            assert decision.reason_code == "DEPLOYMENT_DISABLED"
        finally:
            capabilities.CAPABILITIES = original


class TestScopeIsAnAllowlist:
    @pytest.mark.parametrize(
        "key",
        [
            "marketplace_checkout",
            "premium_identity",
            "premium_advanced_tools",
            "pulse_livestream",
            "admin_command",
        ],
    )
    def test_an_out_of_wave_capability_gets_no_opinion_even_when_armed(self, key):
        """Being passed a key is not authority to answer about it.

        A module that evaluates whatever it is handed has no waves, only an
        ordering convention — and the two look identical right up until
        somebody wires a checkout route to the same helper.
        """
        runtime.arm(rows_matching_registry(), environ={runtime.CONSULTATION_ENV: "1"})
        assert runtime.consult(key) is None

    def test_an_unknown_key_gets_no_opinion_rather_than_a_guess(self):
        runtime.arm(rows_matching_registry(), environ={runtime.CONSULTATION_ENV: "1"})
        assert runtime.consult("pulse_telepathy") is None

    def test_wave_1_keys_match_the_derived_cutover_plan(self):
        """The written allowlist and the computed plan must agree.

        :func:`~services.pulse_control_plane.activation.waves` derives wave 1
        from the shadow matrix so the plan cannot drift out of no-op-first
        order. This module writes the same set out by hand so the *scope of a
        live wave* cannot widen without a diff. Both properties are worth
        having; this test is what stops the pair from silently separating.
        """
        assert runtime.WAVE_1_KEYS == frozenset(activation.waves()[0].capability_keys)

    def test_no_never_wire_capability_is_in_scope(self):
        assert runtime.WAVE_1_KEYS & set(activation.NEVER_WIRE) == set()

    def test_the_payment_row_is_not_in_scope(self):
        """Pinned separately from the derived check, because it is the one that matters.

        ``marketplace_checkout`` is seeded ``internal-only`` against 32 real
        orders. If a future edit ever made the derived plan include it in wave 1,
        the test above would follow the plan and stay green.
        """
        assert "marketplace_checkout" not in runtime.WAVE_1_KEYS


class TestNoOpinionIsNotDenial:
    def test_consult_expresses_denial_as_a_decision_not_as_none(self):
        """``None`` must never be reachable as a way of saying no.

        The two meanings share a return type only if this is violated, and a
        caller that read ``None`` as denial would withdraw ten live features
        every time the process was disarmed.
        """
        runtime.arm(rows_matching_registry(), environ={runtime.CONSULTATION_ENV: "1"})
        for key in sorted(runtime.WAVE_1_KEYS):
            assert runtime.consult(key) is not None

    def test_disarming_restores_pre_wave_behaviour_without_a_deploy(self):
        runtime.arm(rows_matching_registry(), environ={runtime.CONSULTATION_ENV: "1"})
        assert runtime.consult("pulse_posts") is not None
        result = runtime.disarm("incident 4471")
        assert result.armed is False
        assert result.reason == "incident 4471"
        assert runtime.consult("pulse_posts") is None
        assert runtime.permits("pulse_posts") is True

    def test_disarming_cannot_turn_a_denial_into_an_allow_it_can_only_stop_asking(self):
        """The distinction that makes the switch not an override.

        :func:`~services.pulse_control_plane.model.evaluate` refuses a ``force``
        argument. This switch looks like one and is not: with it off no decision
        is produced, so there is no decision it could have overridden. The proof
        is that the set of reachable outcomes is {the plane's answer, no answer}
        and never {an answer the plane did not give}.
        """
        runtime.arm(rows_matching_registry(), environ={runtime.CONSULTATION_ENV: "1"})
        armed = runtime.consult("pulse_posts")
        runtime.disarm()
        assert runtime.consult("pulse_posts") is None
        runtime.arm(rows_matching_registry(), environ={runtime.CONSULTATION_ENV: "1"})
        rearmed = runtime.consult("pulse_posts")
        assert armed is not None and rearmed is not None
        assert (armed.visible, armed.usable, armed.reason_code) == (
            rearmed.visible,
            rearmed.usable,
            rearmed.reason_code,
        )


class TestWave1SuppliesNoEligibilityVerdict:
    def test_no_wave_1_capability_requires_an_authority(self):
        """Why passing no verdict is correct rather than merely convenient.

        All ten carry ``STANDARD``, which requires no authority, so there is
        nobody to ask. If a future wave-1 member needed one, this fails here
        rather than in production as ``ELIGIBILITY_NOT_SUPPLIED`` on a live
        feed — which is what a silently-denied capability looks like from the
        outside.
        """
        registry = {c.key: c for c in capabilities.CAPABILITIES}
        for key in sorted(runtime.WAVE_1_KEYS):
            policy = registry[key].eligibility
            assert policy.requires_authority is False, key

    def test_runtime_does_not_fabricate_an_eligibility_verdict(self):
        """This module must not become the authority it is describing.

        Every other module in the package refuses to decide entitlement. The
        one on the request path is where that refusal is easiest to abandon,
        because a fabricated ``allowed=True`` would make an awkward denial go
        away.
        """
        source = (runtime.__file__ or "").replace(".pyc", ".py")
        with open(source, "r", encoding="utf-8") as handle:
            text = handle.read()
        assert "EligibilityVerdict(" not in text
