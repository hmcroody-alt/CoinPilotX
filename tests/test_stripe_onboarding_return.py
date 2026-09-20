"""The Stripe onboarding return decision, over every state rather than the happy one.

The flow this covers is exercised in production exactly once per seller, and
only ever on its success path. Nobody can ask Stripe to hold an account in
`pending_verification` on demand, or to expire a link to order, so the four
non-happy branches of a return page are branches nothing ever runs. Testing the
decision as a pure function is what makes them reachable: each is one dict
literal.

The assertions that matter most here are not about wording. They are about the
`onboarding_status` string that gets written, because three separate consumers
branch on the exact spelling with no default between them — a status outside all
their sets is not read as "unknown", it is read as the absence of the thing it
was meant to record. The production row this mission started from was exactly
that failure in the other direction: Stripe said live, the column said
`onboarding_started`, buyers could pay and the seller could not be paid.
"""

import json
import re
from pathlib import Path

import pytest

from services.stripe_onboarding_return import (
    AUTO_HANDOFF_STATES,
    AUTO_RETURN_NOTE,
    CONSUMED_ONBOARDING_STATUSES,
    MONEY_PATH_REFUSED_STATUSES,
    RETURN_FAILED,
    RETURN_INCOMPLETE,
    RETURN_MORE_INFO,
    RETURN_READY,
    RETURN_STATES,
    RETURN_UNDER_REVIEW,
    classify_return,
    onboarding_status_for,
    presentation_is_consistent,
    return_presentation,
)

REPO = Path(__file__).resolve().parents[1]


def snap(**over):
    """A live, fully-onboarded account. Every case is this minus something."""
    base = {
        "ok": True,
        "provider_account_id": "acct_test",
        "details_submitted": True,
        "charges_enabled": True,
        "payouts_enabled": True,
        "disabled_reason": None,
        "currently_due": [],
        "past_due": [],
        "capabilities": {"card_payments": "active", "transfers": "active"},
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# The five states
# ---------------------------------------------------------------------------

class TestClassification:
    def test_live_account_is_ready(self):
        assert classify_return(snap()) == RETURN_READY

    def test_submitted_but_not_yet_enabled_is_under_review(self):
        # Nothing outstanding, nothing for the seller to do. Distinct from
        # INCOMPLETE because telling this seller to "finish setting up" sends
        # them back through a flow they already completed.
        assert classify_return(
            snap(charges_enabled=False, payouts_enabled=False)
        ) == RETURN_UNDER_REVIEW

    def test_half_enabled_is_still_under_review(self):
        assert classify_return(snap(payouts_enabled=False)) == RETURN_UNDER_REVIEW

    def test_a_held_transfers_capability_is_not_ready_despite_green_flags(self):
        # The bug this guards: `charges_enabled` and `payouts_enabled` are
        # Stripe's summary flags and neither of them covers `transfers`, which
        # is the capability PulseSoc actually pays sellers through. An account
        # in this state can take a buyer's card and cannot receive its own
        # share, so READY — whose copy says there is nothing left to wait for —
        # would be a false promise.
        assert classify_return(
            snap(capabilities={"card_payments": "active", "transfers": "pending"})
        ) == RETURN_UNDER_REVIEW

    def test_a_held_card_capability_is_not_ready_either(self):
        assert classify_return(
            snap(capabilities={"card_payments": "inactive", "transfers": "active"})
        ) == RETURN_UNDER_REVIEW

    def test_silence_about_capabilities_is_not_refusal(self):
        # An older stored payload, or a caller that built the snapshot by hand,
        # carries no `capabilities` block at all. Reading that absence as "not
        # granted" would demote every live seller whose snapshot predates this
        # field — a missing key is not a Stripe decision.
        no_block = snap()
        no_block.pop("capabilities")
        assert classify_return(no_block) == RETURN_READY
        assert classify_return(snap(capabilities={})) == RETURN_READY
        assert classify_return(snap(capabilities="not-a-mapping")) == RETURN_READY

    def test_capabilities_cannot_rescue_an_account_with_something_due(self):
        # Ordering control: MORE_INFO is decided before the flags are read, so
        # two active capabilities must not talk over an outstanding requirement.
        assert classify_return(
            snap(currently_due=["individual.id_number"])
        ) == RETURN_MORE_INFO

    def test_left_the_flow_early_is_incomplete(self):
        assert classify_return(
            snap(details_submitted=False, charges_enabled=False, payouts_enabled=False)
        ) == RETURN_INCOMPLETE

    def test_currently_due_is_more_info(self):
        assert classify_return(
            snap(currently_due=["individual.verification.document"])
        ) == RETURN_MORE_INFO

    def test_past_due_is_more_info(self):
        assert classify_return(snap(past_due=["individual.id_number"])) == RETURN_MORE_INFO

    def test_disabled_reason_is_more_info_even_with_everything_submitted(self):
        # The ordering that the old nesting got wrong: an account Stripe is
        # *holding* is not described by whether the seller finished typing.
        assert classify_return(
            snap(disabled_reason="requirements.pending_verification")
        ) == RETURN_MORE_INFO

    def test_disabled_reason_outranks_incomplete(self):
        state = classify_return(
            snap(
                details_submitted=False,
                charges_enabled=False,
                payouts_enabled=False,
                disabled_reason="rejected.fraud",
            )
        )
        assert state == RETURN_MORE_INFO, "a held account must not be reported as the seller's unfinished work"

    @pytest.mark.parametrize(
        "bad",
        [None, {}, {"ok": False}, {"ok": False, "error": "No such account"}],
    )
    def test_no_answer_is_failed(self, bad):
        assert classify_return(bad) == RETURN_FAILED

    def test_a_failed_retrieve_is_not_rescued_by_stale_flags(self):
        # `ok: False` with a body is what a partial/cached response looks like.
        # Reading the flags anyway would present last week's state as the
        # outcome of what the seller just did.
        assert classify_return(
            {"ok": False, "charges_enabled": True, "payouts_enabled": True, "details_submitted": True}
        ) == RETURN_FAILED


# ---------------------------------------------------------------------------
# The persisted status — the half that moves money
# ---------------------------------------------------------------------------

class TestPersistedStatus:
    def test_ready_writes_the_word_the_money_path_accepts(self):
        status = onboarding_status_for(snap())
        assert status == "complete"
        assert status not in MONEY_PATH_REFUSED_STATUSES

    def test_ready_no_longer_writes_the_status_that_caused_the_bug(self):
        # The production row: Stripe live, column saying the seller had merely
        # begun, every sale booked `ledger_pending_onboarding`.
        assert onboarding_status_for(snap()) != "onboarding_started"

    def test_under_review_writes_complete_not_a_new_word(self):
        # "Stripe is deciding" is not something this column says. It is
        # `complete` + flags off, which is exactly how
        # `card_payment_status` derives CARD_UNDER_REVIEW. A literal
        # "under_review" would fall outside every consumer's set and be read as
        # SETUP_IN_PROGRESS.
        status = onboarding_status_for(snap(charges_enabled=False, payouts_enabled=False))
        assert status == "complete"
        assert status in CONSUMED_ONBOARDING_STATUSES

    def test_under_review_is_still_refused_a_transfer_by_the_flags(self):
        # `complete` clears the status check, so the refusal has to come from
        # somewhere. It comes from the capability flags, which is the whole
        # reason writing a truthful status is safe.
        assert onboarding_status_for(snap(payouts_enabled=False)) not in MONEY_PATH_REFUSED_STATUSES
        assert snap(payouts_enabled=False)["payouts_enabled"] is False

    def test_a_held_account_writes_a_status_the_money_path_refuses(self):
        status = onboarding_status_for(snap(disabled_reason="rejected.fraud"))
        assert status == "restricted"
        assert status in MONEY_PATH_REFUSED_STATUSES

    def test_merely_due_does_not_restrict_an_account_stripe_left_working(self):
        # `currently_due` with a future deadline is Stripe asking, not Stripe
        # stopping. Writing `restricted` here would freeze payouts for a seller
        # Stripe is still paying.
        status = onboarding_status_for(snap(currently_due=["company.tax_id"]))
        assert status == "requirements_due"
        assert status not in MONEY_PATH_REFUSED_STATUSES

    def test_incomplete_writes_the_in_progress_status(self):
        status = onboarding_status_for(
            snap(details_submitted=False, charges_enabled=False, payouts_enabled=False)
        )
        assert status == "onboarding_started"
        assert status in MONEY_PATH_REFUSED_STATUSES

    def test_a_failed_retrieve_writes_nothing_at_all(self):
        # Not "unknown", not a guess, not a demotion. A transient Stripe
        # timeout must not be able to take a live seller off the rail.
        assert onboarding_status_for({"ok": False}) == ""
        assert onboarding_status_for(None) == ""

    def test_every_status_this_module_writes_is_one_a_consumer_reads(self):
        for state_snapshot in ALL_SNAPSHOTS:
            status = onboarding_status_for(state_snapshot)
            if not status:
                continue
            assert status in CONSUMED_ONBOARDING_STATUSES, (
                f"{status!r} is outside every consumer's branch set; it would be "
                "read as none of them rather than as unknown"
            )


# ---------------------------------------------------------------------------
# Transcribed sets must match the code they were transcribed from
# ---------------------------------------------------------------------------

class TestTranscriptionsStayTrue:
    def test_money_path_refusal_set_matches_bot_py(self):
        # This module cannot import `bot` (Flask, 111k lines), so the set is
        # copied. A copy that silently drifts is worse than no copy: the
        # decisions above would be reasoning about a rule that changed.
        source = (REPO / "bot.py").read_text(encoding="utf-8", errors="replace")
        match = re.search(
            r'def seller_destination_account_id\(payout\):.*?'
            r'onboarding_status[^\n]*?\.lower\(\) in \{([^}]*)\}',
            source,
            re.S,
        )
        assert match, "seller_destination_account_id no longer refuses on onboarding_status"
        live = {token.strip().strip("\"'") for token in match.group(1).split(",") if token.strip()}
        assert live == set(MONEY_PATH_REFUSED_STATUSES)

    def test_consumed_set_covers_card_payment_status_branches(self):
        source = (REPO / "services" / "seller_access_state.py").read_text(encoding="utf-8")
        body = source.split("def card_payment_status", 1)[1].split("\ndef ", 1)[0]
        for group in re.findall(r'onboarding in \{([^}]*)\}', body):
            for token in group.split(","):
                word = token.strip().strip("\"'")
                if word:
                    assert word in CONSUMED_ONBOARDING_STATUSES, (
                        f"card_payment_status branches on {word!r}, which this module "
                        "does not know is read"
                    )


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

ALL_SNAPSHOTS = [
    snap(),
    snap(charges_enabled=False, payouts_enabled=False),
    snap(payouts_enabled=False),
    snap(details_submitted=False, charges_enabled=False, payouts_enabled=False),
    snap(currently_due=["individual.verification.document"]),
    snap(past_due=["individual.id_number"]),
    snap(disabled_reason="requirements.pending_verification"),
    {"ok": False},
    None,
]


class TestPresentation:
    def test_ready_uses_the_copy_the_brief_specified(self):
        page = return_presentation(snap())
        assert page["headline"] == "Stripe setup complete"
        assert "connected successfully" in page["body"]
        assert "finish setting up your store and payments" in page["body"]
        assert page["cta"] == "Open PulseSoc"

    def test_every_state_produces_a_page(self):
        seen = set()
        for snapshot in ALL_SNAPSHOTS:
            page = return_presentation(snapshot)
            assert page["headline"] and page["body"] and page["cta"]
            seen.add(page["state"])
        assert seen == set(RETURN_STATES), f"never exercised: {set(RETURN_STATES) - seen}"

    def test_the_four_knowable_states_hand_off_and_the_unknowable_one_does_not(self):
        for snapshot in ALL_SNAPSHOTS:
            page = return_presentation(snapshot)
            expected = page["state"] != RETURN_FAILED
            assert page["auto_handoff"] is expected
            assert bool(page["auto_note"]) is expected

    def test_the_failure_page_does_not_promise_an_automatic_return(self):
        # The specific thing that must not happen: a page saying "Opening
        # PulseSoc automatically…" above a state that opens nothing.
        page = return_presentation({"ok": False})
        assert page["auto_note"] == ""
        assert AUTO_RETURN_NOTE not in page["body"]

    def test_no_state_tells_an_unfinished_seller_they_are_done(self):
        for snapshot in ALL_SNAPSHOTS:
            page = return_presentation(snapshot)
            if page["state"] == RETURN_READY:
                continue
            assert "connected successfully" not in page["body"]
            assert "complete" not in page["headline"].lower()

    def test_no_state_leaks_a_stripe_identifier_into_the_copy(self):
        for snapshot in ALL_SNAPSHOTS:
            page = return_presentation(snapshot)
            blob = json.dumps({k: v for k, v in page.items() if k != "onboarding_status"})
            assert "acct_" not in blob
            assert "sk_" not in blob and "rk_" not in blob


class TestConsistency:
    def test_every_state_satisfies_the_rule(self):
        for snapshot in ALL_SNAPSHOTS:
            page = return_presentation(snapshot)
            assert presentation_is_consistent(page), page

    def test_the_predicate_can_actually_fail(self):
        # Negative control. Without this, the assertion above passes for a
        # predicate that returns True unconditionally.
        good = return_presentation({"ok": False})
        assert presentation_is_consistent(good)
        assert not presentation_is_consistent({**good, "auto_handoff": True})
        assert not presentation_is_consistent({**good, "onboarding_status": "complete"})

    def test_the_predicate_rejects_a_word_no_consumer_reads(self):
        page = return_presentation(snap(charges_enabled=False, payouts_enabled=False))
        assert presentation_is_consistent(page)
        assert not presentation_is_consistent({**page, "onboarding_status": "under_review"}), (
            "a status outside every consumer's set must not pass as valid"
        )

    def test_the_predicate_rejects_an_invented_state(self):
        assert not presentation_is_consistent(
            {"state": "almost", "auto_handoff": True, "auto_note": AUTO_RETURN_NOTE,
             "onboarding_status": "complete"}
        )

    def test_auto_handoff_states_are_exactly_the_knowable_ones(self):
        assert AUTO_HANDOFF_STATES == set(RETURN_STATES) - {RETURN_FAILED}
