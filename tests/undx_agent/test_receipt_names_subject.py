"""The completed-write receipt must name the thing it changed.

Found live, not by reading. On an iPhone 17 Pro Max simulator against a local backend,
a confirmation card correctly said

    Pause one crypto alert so it stops triggering
    BTC alert · above · 999,999: active → paused

and, one tap later, the receipt for that same write said

    Done — the current value is paused, and I read it back from PulseSoc to confirm it.

Both sentences are true. Only one of them is checkable. A person holding four alerts
reads the second and cannot tell which of the four moved, which makes the receipt
unable to do the one job a receipt has. Batch 16 taught the confirmation card to name
its subject; the sentence on the next screen never learned it.

The evidence for that session is in ``reports/live_simulator_batch16_18.md``.

Three properties are asserted here, and the third is the one that would have been
missed by inspection:

* the naming has exactly one definition, shared by the card and the receipt, so the
  two screens cannot word the same row differently;
* the subject travels on the *verification evidence*, published by the read-back,
  never composed from the request;
* naming the subject does not get the whole answer thrown away. ``validate_consistency``
  discards any sentence containing a digit that does not appear in the evidence, and
  it discards it *silently and totally*. A subject like "BTC alert · above · 999,999"
  carries digits. Had the label been composed in the prose layer instead of published
  into the evidence, every named receipt would have failed that check and fallen
  through to the last-resort line — a worse outcome than the defect being fixed.
"""

from __future__ import annotations

import unittest

from tests.undx_agent import bootstrap as _bootstrap  # noqa: F401
from services import undx_agent_runtime as runtime
from services import undx_response_intelligence as ri
from services import undx_verification as verify
from services.undx_agent_contracts import (
    AgentOutcome,
    ToolResult,
    VerificationResult,
    VerificationState,
    describe_alert,
)
from services.undx_capability_registry import get


PAUSE = "crypto.alerts.pause"

#: The shape ``alert_engine.get_alert_rule`` returns through ``_public_rule``: the
#: symbol, the normalised condition, and ``threshold`` mirrored from the stored value.
#: Copied from a row that was actually paused live — alert_rules id 29.
LIVE_ROW = {
    "id": 29,
    "symbol": "BTC",
    "condition": "above",
    "threshold": 999999.0,
    "status": "paused",
    "active": 0,
}

LIVE_LABEL = "BTC alert · above · 999,999"


def _result() -> ToolResult:
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.crypto_alerts.pause",
        capability_id=PAUSE,
        canonical_resource_id="alert_rule:29",
        data={"alert_id": 29, "requested_status": "paused"},
    )


def _verified(subject: str = LIVE_LABEL) -> VerificationResult:
    evidence = {
        "canonical_resource_id": "alert_rule:29",
        "read_back": {"status": "paused", "active": 0},
        "source": "alert_engine.get_alert_rule",
    }
    if subject:
        evidence["subject"] = subject
    return VerificationResult(
        state=VerificationState.VERIFIED,
        expected="paused",
        observed="paused",
        evidence=evidence,
    )


class OneDefinitionTests(unittest.TestCase):
    """The card and the receipt must be reading from the same function."""

    def test_the_runtime_name_is_the_contracts_name(self) -> None:
        """Not "produces the same string" — literally the same object.

        Equal output today is what two copies also have, right up until one of them
        is edited. Identity is the property that cannot rot.
        """
        self.assertIs(describe_alert, runtime.describe_alert)

    def test_the_label_is_the_one_seen_on_the_live_confirmation_card(self) -> None:
        self.assertEqual(LIVE_LABEL, describe_alert(LIVE_ROW))

    def test_a_record_that_names_nothing_yields_nothing(self) -> None:
        """No subject is not a licence to invent one."""
        for empty in ({}, None, {"status": "paused"}):
            self.assertEqual("", describe_alert(empty))


class SubjectComesFromTheReadBackTests(unittest.TestCase):
    """Published by the verifier, from the row it read, and from nowhere else."""

    def test_the_verifier_publishes_the_subject_it_read(self) -> None:
        captured: dict[str, object] = {}

        class _Engine:
            @staticmethod
            def get_alert_rule(alert_id: int, user_id: int) -> dict:
                captured["alert_id"] = alert_id
                captured["user_id"] = user_id
                return dict(LIVE_ROW)

        original = verify._alert_engine
        verify._alert_engine = lambda: _Engine  # type: ignore[assignment]
        try:
            outcome = verify.crypto_alert_status(7, {"alert_id": 29}, _result())
        finally:
            verify._alert_engine = original  # type: ignore[assignment]

        self.assertEqual(VerificationState.VERIFIED, outcome.state)
        self.assertEqual(LIVE_LABEL, outcome.evidence.get("subject"))
        self.assertEqual(29, captured["alert_id"])
        # Scoped to the caller, not to the id. A subject naming somebody else's row
        # would be a disclosure wearing the costume of a courtesy.
        self.assertEqual(7, captured["user_id"])

    def test_a_row_that_cannot_be_read_back_publishes_no_subject(self) -> None:
        class _Engine:
            @staticmethod
            def get_alert_rule(alert_id: int, user_id: int) -> None:
                return None

        original = verify._alert_engine
        verify._alert_engine = lambda: _Engine  # type: ignore[assignment]
        try:
            outcome = verify.crypto_alert_status(7, {"alert_id": 29}, _result())
        finally:
            verify._alert_engine = original  # type: ignore[assignment]

        self.assertEqual(VerificationState.FAILED, outcome.state)
        self.assertNotIn("subject", outcome.evidence or {})


class ReceiptNamesTheSubjectTests(unittest.TestCase):
    """The sentence the person actually reads."""

    def _sentence(self, verification: VerificationResult) -> str:
        return ri._write_state_sentence(get(PAUSE), _result(), verification)

    def test_the_named_row_appears_in_the_state_sentence(self) -> None:
        sentence = self._sentence(_verified())
        self.assertIn(LIVE_LABEL, sentence)
        self.assertNotIn("the current value", sentence)

    def test_without_a_subject_the_old_wording_survives(self) -> None:
        """A verifier that reads no record has withheld nothing, so nothing is guessed."""
        sentence = self._sentence(_verified(subject=""))
        self.assertEqual("the current value is paused", sentence)

    def test_the_state_is_still_the_state_that_was_read_back(self) -> None:
        """Naming the subject must not quietly become naming the request."""
        self.assertIn("paused", self._sentence(_verified()))


class TheAnswerSurvivesTheValidatorTests(unittest.TestCase):
    """The regression that naming a subject invites, asserted rather than assumed."""

    def _compose(self, verification: VerificationResult) -> tuple[str, object]:
        return ri.compose(get(PAUSE), AgentOutcome.VERIFIED_SUCCESS,
                          _result(), verification)

    def test_the_rendered_answer_names_the_alert(self) -> None:
        text, _plan = self._compose(_verified())
        self.assertIn("BTC alert", text)

    def test_the_subjects_digits_are_permitted_numbers(self) -> None:
        """"999,999" reaches the prose only because it is in the evidence.

        ``_allowed_numbers`` scrapes ``verification.evidence``; publishing the label
        there is what makes its digits supportable. Composing the same label in the
        renderer would have put an unsupported number into every named receipt.
        """
        _text, plan = self._compose(_verified())
        self.assertIn("999", plan.allowed_numbers)

    def test_the_answer_is_not_discarded_by_the_consistency_guard(self) -> None:
        text, plan = self._compose(_verified())
        self.assertEqual([], ri.validate_consistency(plan, text))

    def test_the_last_resort_line_is_not_what_the_person_gets(self) -> None:
        """The failure mode this whole class exists to catch, named directly."""
        text, plan = self._compose(_verified())
        self.assertNotEqual(ri._last_resort(plan), text.rstrip("."))

    def test_an_unnamed_write_still_renders(self) -> None:
        text, plan = self._compose(_verified(subject=""))
        self.assertTrue(text)
        self.assertEqual([], ri.validate_consistency(plan, text))


class TwoScreensAgreeTests(unittest.TestCase):
    """"Is this the one I approved?" has to be answerable by comparing words."""

    def test_the_confirmation_label_and_the_receipt_use_the_same_words(self) -> None:
        card_label = runtime.describe_alert(LIVE_ROW)
        receipt = ri._write_state_sentence(get(PAUSE), _result(), _verified(card_label))
        self.assertIn(card_label, receipt)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
