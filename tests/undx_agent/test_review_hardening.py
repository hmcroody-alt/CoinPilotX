"""The properties an architectural review found missing, asserted so they stay fixed.

Every case here corresponds to a defect that existed in ``adf314d9`` and passed all
127 tests, because each one lived in a path that only opens when something else has
already gone wrong: an audit write that fails after the mutation, a listing that
happens to be one page long, a capability whose declared permission nothing reads.
Code that only runs on a bad day is code that is only tested deliberately.

The organising idea is that a guarantee stated in a docstring is not a guarantee.
Several of the fixes reviewed here are of the form "this function must not raise
past this point" or "this field must be enforced" — claims about absence, which the
existing suite could not have caught, and which are asserted here by arranging the
failure and checking what the user is told.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


class RegistryIntegrity(unittest.TestCase):
    """Import-time checks that make a class of mistake impossible to deploy."""

    def setUp(self) -> None:
        from services import undx_capability_registry

        self.registry = undx_capability_registry

    def test_every_capability_tool_is_known_to_the_production_ledger(self) -> None:
        """A tool the ledger has never heard of cannot be recorded, and the failure
        surfaces as a ``ValueError`` deep inside the gateway — which the service layer
        used to swallow into an ordinary chat reply. Catching the mismatch here means
        it is a red build rather than a silent downgrade to conversation."""
        self.assertEqual(self.registry.unregistered_tool_names(), [])

    def test_a_write_must_name_a_verifier_and_a_target(self) -> None:
        for spec in self.registry.REGISTRY.values():
            if not spec.is_write:
                continue
            with self.subTest(capability=spec.capability_id):
                self.assertTrue(spec.verifier)
                self.assertTrue(spec.target_field)

    def test_every_mutable_field_is_read_back(self) -> None:
        """``crypto.alerts.update`` accepted ``condition`` while its verifier checked
        only ``threshold``, so changing the condition and having it silently not
        persist produced a card that said "verified"."""
        for spec in self.registry.REGISTRY.values():
            if not spec.is_write:
                continue
            mutable = {f.name for f in spec.fields} - {spec.target_field}
            with self.subTest(capability=spec.capability_id):
                self.assertEqual(mutable - set(spec.verified_fields), set())

    def test_an_undo_that_cannot_be_invoked_is_rejected_at_import(self) -> None:
        """The graph validator is the whole defence here, so it is exercised directly
        rather than trusted because the module imported."""
        from services.undx_capability_registry import CapabilitySpec, REGISTRY
        from services.undx_agent_contracts import (
            CardType, ConfirmationPolicy, FieldSpec, PermissionScope, RiskLevel,
        )
        from services import undx_capability_registry as module

        broken = CapabilitySpec(
            capability_id="test.broken.undo",
            description="A write whose undo needs an argument it never supplies",
            intents=("x",),
            risk=RiskLevel.REVERSIBLE_WRITE,
            confirmation=ConfirmationPolicy.CONTEXTUAL,
            tool_name="pulsesoc.test.broken",
            permission=PermissionScope.SELF_ACCOUNT_ONLY,
            fields=(FieldSpec("category", "str", required=True),),
            executor="notification_preferences_update",
            verifier="notification_preference_value",
            native_route="/pulse/settings/notifications",
            result_card=CardType.SETTING_CHANGE_RECEIPT,
            audit_category="notification_preferences_write",
            target_field="category",
            # Undoes with a capability that also requires `push`, which this one has no
            # way to produce. Pass-through would send an incomplete call.
            undo_capability_id="notifications.preference.update",
        )
        REGISTRY[broken.capability_id] = broken
        try:
            with self.assertRaises(ValueError) as caught:
                module._validate_undo_graph()
            self.assertIn("push", str(caught.exception))
        finally:
            REGISTRY.pop(broken.capability_id, None)

    def test_a_preference_undo_inverts_rather_than_replays(self) -> None:
        """The defect this fixes: ``notifications.preference.update`` undoes itself, so
        re-sending the stored arguments would re-apply the change the user is trying to
        walk back. The card promised an undo that would have confirmed the setting."""
        spec = self.registry.get("notifications.preference.update")
        self.assertEqual(
            spec.undo_arguments({"category": "reels", "push": False}, []),
            {"category": "reels", "push": True},
        )

    def test_a_creation_offers_no_undo_without_the_row_it_created(self) -> None:
        """Undoing a create means deleting a row whose id lives only in the result. No
        id, no undo — rather than a delete aimed at an empty target."""
        spec = self.registry.get("crypto.alerts.create")
        self.assertEqual(
            spec.undo_arguments({"symbol": "BTC"}, ["alert_rule:42"]), {"alert_id": "42"},
        )
        self.assertIsNone(spec.undo_arguments({"symbol": "BTC"}, []))


class PermissionScopeEnforcement(unittest.TestCase):
    """``permission`` was a decorative string. These are the two ways it now bites."""

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import undx_agent_runtime, undx_capability_registry

        self.registry = undx_capability_registry
        self.runtime = undx_agent_runtime
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)

    def tearDown(self) -> None:
        self.fx.stop()

    def _handle_with(self, **spec_changes):
        """Drive one capability through the runtime with its spec temporarily altered.

        Going through ``handle`` rather than ``execute`` is the point. The gateway
        raises for a pre-execution refusal, and what matters is that the refusal
        reaches the user as a typed card — not that an exception was thrown somewhere
        inside a call the service layer might have caught and turned into chat.
        """
        from dataclasses import replace
        from services.undx_capability_registry import REGISTRY

        real = REGISTRY["crypto.alerts.get"]
        REGISTRY["crypto.alerts.get"] = replace(real, **spec_changes)
        try:
            return self.runtime.handle(
                self.fx.cur, user_id=OWNER_ID, text="Show me that alert",
                capability_id="crypto.alerts.get",
                arguments={"alert_id": self.alert_id}, request_id="r-scope",
            )
        finally:
            REGISTRY["crypto.alerts.get"] = real

    def test_a_self_only_capability_may_not_declare_an_actor_field(self) -> None:
        """A capability scoped to the caller's own account that also accepts
        ``user_id`` is a capability whose scope is a comment. The gateway refuses it
        rather than trusting that whoever wrote the executor remembered to ignore the
        field — which is exactly the assumption an injected argument exploits."""
        from services.undx_agent_contracts import FieldSpec
        from services.undx_capability_registry import REGISTRY

        reply = self._handle_with(
            fields=REGISTRY["crypto.alerts.get"].fields
            + (FieldSpec("user_id", "int", required=False),),
        )
        self.assertTrue(reply.handled)
        self.assertEqual(reply.card["status"], "permission_denied")

    def test_a_scope_with_no_resolver_is_unsupported_rather_than_allowed(self) -> None:
        """``other_user_target`` and ``owned_content_target`` name checks nothing has
        been built for yet. Until they exist, a capability declaring one is refused —
        which is what stops the next pack from shipping the declaration and forgetting
        the enforcement, rather than leaving that to a reviewer noticing."""
        from services.undx_agent_contracts import PermissionScope

        reply = self._handle_with(permission=PermissionScope.OTHER_USER_TARGET)
        self.assertTrue(reply.handled)
        self.assertEqual(reply.card["status"], "unsupported_capability")


class AmbiguityAcrossPageBoundaries(unittest.TestCase):
    """"UNDX does not guess" was true only for users with few alerts."""

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import undx_agent_runtime, undx_agent_tools

        self.runtime = undx_agent_runtime
        self.tools = undx_agent_tools

    def tearDown(self) -> None:
        self.fx.stop()

    def test_a_listing_reports_when_it_is_not_the_whole_set(self) -> None:
        for index in range(4):
            self.fx.make_alert(OWNER_ID, symbol=f"SYM{index}", threshold=100.0 + index)
        result = self.tools.crypto_alerts_list(OWNER_ID, {"limit": 2})
        self.assertEqual(len(result.records), 2)
        self.assertTrue(result.data["truncated"])

    def test_a_reference_is_not_resolved_from_a_truncated_page(self) -> None:
        """The dangerous shape: the user says "my Bitcoin alert", the first page holds
        exactly one BTC alert, and a second one sits on page two. Resolving from the
        page would pause a specific alert with no ambiguity ever reported."""
        original = self.runtime._MAX_REFERENCE_SCAN
        self.runtime._MAX_REFERENCE_SCAN = 2
        try:
            for index in range(4):
                self.fx.make_alert(OWNER_ID, symbol=f"SYM{index}", threshold=100.0 + index)
            reference = self.runtime.resolve_alert_reference(OWNER_ID, "SYM0")
            self.assertEqual(reference.count, 2)
            self.assertEqual(reference.resource_id, 0)
            self.assertIn("more alerts", reference.detail)
        finally:
            self.runtime._MAX_REFERENCE_SCAN = original

    def test_one_match_on_a_partial_page_is_still_not_a_resolution(self) -> None:
        """The shape the test above describes and does not build.

        ``test_a_reference_is_not_resolved_from_a_truncated_page`` opens by naming the
        dangerous case exactly — "the first page holds exactly one BTC alert, and a
        second one sits on page two" — and then constructs an account where the named
        symbol is on neither page, so the page holds *zero* matches and the guard is
        never asked the question the docstring poses. Two other truncation tests have
        two matches and fifty. None of them has one.

        The gap was found by ``mutate14.py``'s ``guardafterunique`` mode, which moves the
        truncation check below the single-match return so it fires everywhere except
        here, and which the whole suite passed. By this harness's own contract a guard
        nothing objects to removing is decorative, so it gets a test rather than an
        argument. That is the second time this has fired for real; ``mutate12.py``'s
        ``zerorow`` was the first.

        The ordering is load-bearing and is why the matching row is created last: the
        listing returns newest first, so ``SYM0`` lands on the page that is read while a
        fourth alert stays past the edge. Whether that fourth row is another ``SYM0`` is
        precisely what the runtime cannot know, and precisely why one match on a partial
        page is not one match on an account.
        """
        original = self.runtime._MAX_REFERENCE_SCAN
        self.runtime._MAX_REFERENCE_SCAN = 2
        try:
            for index in range(3):
                self.fx.make_alert(OWNER_ID, symbol=f"ZZZ{index}", threshold=100.0 + index)
            self.fx.make_alert(OWNER_ID, symbol="SYM0", threshold=200.0)
            listing = self.tools.crypto_alerts_list(OWNER_ID, {"limit": 2})
            self.assertTrue(listing.data["truncated"])
            self.assertIn("SYM0", [str(row.get("symbol")) for row in listing.records])

            reference = self.runtime.resolve_alert_reference(OWNER_ID, "pause my SYM0 alert")
            self.assertEqual(0, reference.resource_id)
            self.assertIn("more alerts", reference.detail)
        finally:
            self.runtime._MAX_REFERENCE_SCAN = original

    def test_a_coin_named_by_a_person_is_a_question_for_the_store(self) -> None:
        """The defect Batch 14 was built for: a refusal about a comparison never made.

        Fifty-one alerts, exactly one of them Bitcoin. "Pause my bitcoin alert" names
        one row and one row only, and the account is not ambiguous about it. Before this
        batch the person was told UNDX had more alerts than it could compare at once —
        which was not what happened. The scan stopped one block *above* the filter that
        would have found the answer, so nothing was ever compared. The same sentence on
        the same account with one alert fewer worked, and the thing that broke it was
        the size of a list it did not need to read.

        The scan limit is lowered rather than fifty rows created, because the property
        has nothing to do with the number fifty. It is that the narrowing happens before
        the cap, not after it.
        """
        original = self.runtime._MAX_REFERENCE_SCAN
        self.runtime._MAX_REFERENCE_SCAN = 2
        try:
            wanted = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
            for index in range(4):
                self.fx.make_alert(OWNER_ID, symbol="ETH", threshold=3000.0 + index)
            reference = self.runtime.resolve_alert_reference(OWNER_ID, "pause my bitcoin alert")
            self.assertEqual(1, reference.count)
            self.assertEqual(wanted, reference.resource_id)
        finally:
            self.runtime._MAX_REFERENCE_SCAN = original

    def test_narrowing_does_not_weaken_the_guard_it_moved_past(self) -> None:
        """The other half, and the one that would make this batch dangerous if it broke.

        Same lowered scan, but now three Bitcoin alerts against a limit of two. The
        narrowed question is still bigger than the page, so uniqueness still cannot be
        established and the runtime still refuses without naming a row. Pushing the
        filter into the store was meant to stop the guard firing where there was nothing
        to guard — not to stop it firing.
        """
        original = self.runtime._MAX_REFERENCE_SCAN
        self.runtime._MAX_REFERENCE_SCAN = 2
        try:
            for index in range(3):
                self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0 + index)
            reference = self.runtime.resolve_alert_reference(OWNER_ID, "pause my bitcoin alert")
            self.assertEqual(0, reference.resource_id)
            self.assertEqual([], reference.candidates)
            self.assertIn("more alerts", reference.detail)
        finally:
            self.runtime._MAX_REFERENCE_SCAN = original

    def test_a_narrowed_listing_reports_truncation_about_what_was_asked_for(self) -> None:
        """``truncated`` has to describe the narrowed set or the guard reads the wrong one.

        Four alerts, one Bitcoin, a limit of two. Unnarrowed the page is truncated —
        there are more alerts than fit. Narrowed to BTC it is not, because there is one
        Bitcoin alert and room for two. Both facts are true of the same account at the
        same moment, which is exactly why the flag has to travel with the question that
        produced it.
        """
        self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
        for index in range(3):
            self.fx.make_alert(OWNER_ID, symbol="ETH", threshold=3000.0 + index)
        wide = self.tools.crypto_alerts_list(OWNER_ID, {"limit": 2})
        self.assertTrue(wide.data["truncated"])
        narrow = self.tools.crypto_alerts_list(OWNER_ID, {"limit": 2, "symbol": "BTC"})
        self.assertFalse(narrow.data["truncated"])
        self.assertEqual(["BTC"], [str(row.get("symbol")) for row in narrow.records])

    def test_two_coins_at_once_are_not_pushed_into_the_store(self) -> None:
        """The boundary on the narrowing, stated so it is not widened by accident.

        The store takes one symbol, so a sentence naming two cannot be asked as one
        query. Rather than pick one of them — which would silently drop half the
        sentence and could resolve to a single row the person never meant — the scan
        stays wide and the in-memory filter handles both. Asserted through the resolver
        rather than by inspecting the query, because the property that matters is that
        both coins survive to the card.
        """
        btc = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
        eth = self.fx.make_alert(OWNER_ID, symbol="ETH", threshold=3000.0)
        self.fx.make_alert(OWNER_ID, symbol="SOL", threshold=150.0)
        reference = self.runtime.resolve_alert_reference(
            OWNER_ID, "pause my bitcoin or ethereum alert")
        self.assertEqual(0, reference.resource_id)
        self.assertEqual({btc, eth},
                         {int(row.get("alert_id") or 0) for row in reference.candidates})


class SupportingReadsObeyTheSameSwitches(unittest.TestCase):
    """Reference resolution and the confirmation preview call executors directly.

    That is deliberate — neither is an action the user asked for, and routing them
    through the gateway would write a receipt and an audit row for a lookup nobody
    requested. But it meant they skipped the flags entirely, so a capability an
    operator had switched off still had its data read and rendered into a card.
    """

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import undx_agent_runtime, undx_capability_registry

        self.runtime = undx_agent_runtime
        self.registry = undx_capability_registry
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)

    def tearDown(self) -> None:
        self.fx.stop()

    def test_resolution_stops_when_the_read_capability_is_withdrawn(self) -> None:
        self.assertTrue(self.runtime.resolve_alert_reference(OWNER_ID, "my bitcoin alert").unique)
        self.fx.set_flags(UNDX_AGENT_DISABLED_CAPABILITIES="crypto.alerts.get")
        reference = self.runtime.resolve_alert_reference(OWNER_ID, "my bitcoin alert")
        self.assertFalse(reference.unique)
        self.assertEqual(reference.resource_id, 0)

    def test_resolution_stops_when_reads_are_switched_off(self) -> None:
        self.fx.set_flags(UNDX_AGENT_READS_ENABLED="")
        self.assertFalse(self.runtime.resolve_alert_reference(OWNER_ID, "my bitcoin alert").unique)

    def test_a_preview_declines_rather_than_inventing_a_current_value(self) -> None:
        """The confirmation card still renders — refusing to show one would either
        block an action the user may take or push it through unconfirmed. What it must
        not do is fill the gap with a guess."""
        spec = self.registry.get("crypto.alerts.pause")
        before, after, label = self.runtime.preview(OWNER_ID, spec, {"alert_id": self.alert_id})
        self.assertEqual(before, "active")
        self.assertTrue(label)

        self.fx.set_flags(UNDX_AGENT_DISABLED_CAPABILITIES="crypto.alerts.get")
        before, after, label = self.runtime.preview(OWNER_ID, spec, {"alert_id": self.alert_id})
        self.assertIsNone(before)
        # The same declining. A read that did not happen names nothing, and the label
        # goes blank alongside the value rather than outliving it.
        self.assertEqual("", label)

    def test_the_gate_refuses_to_treat_a_write_as_a_supporting_read(self) -> None:
        """A supporting read is read-only by construction. Naming a write here would
        run a mutation with no receipt, no audit row and no confirmation."""
        self.assertFalse(self.runtime._read_permitted(OWNER_ID, "crypto.alerts.delete"))
        self.assertFalse(self.runtime._read_permitted(OWNER_ID, "no.such.capability"))
        self.assertTrue(self.runtime._read_permitted(OWNER_ID, "crypto.alerts.get"))


class ThePointOfNoReturn(unittest.TestCase):
    """Once a mutation has run, no later failure may be reported as "nothing happened".

    Both cases below arrange a failure *after* the write has already landed. The
    assertion in each is the same in spirit: the database changed, and the user is
    told something that does not contradict that.
    """

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import undx_agent_runtime, undx_capability_registry, undx_tool_gateway

        self.gateway = undx_tool_gateway
        self.registry = undx_capability_registry
        self.runtime = undx_agent_runtime
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)

    def tearDown(self) -> None:
        self.fx.stop()

    def test_a_failure_while_settling_still_returns_a_receipt(self) -> None:
        """``_settle`` does verification, audit and ledger bookkeeping — all after the
        alert is already paused. If it raises, ``execute`` must still return, and the
        receipt must say the change may have happened and needs reconciling."""
        original = self.gateway._settle
        self.gateway._settle = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("settle exploded"))
        try:
            outcome = self.gateway.execute(
                self.fx.cur, user_id=OWNER_ID, capability_id="crypto.alerts.pause",
                proposed_arguments={"alert_id": self.alert_id}, request_id="r-settle",
                explicit_request=True,
            )
        finally:
            self.gateway._settle = original
        self.fx.commit()

        self.assertEqual(outcome.receipt.status, "accepted_unverified")
        self.assertTrue(outcome.receipt.evidence.get("needs_reconciliation"))
        # The world really did change, which is why the receipt may not claim otherwise.
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")
        self.assertFalse(outcome.receipt.may_claim_completed)

    def test_a_card_that_cannot_be_built_does_not_lose_the_receipt(self) -> None:
        """``build_card`` is presentation. A rendering bug must not turn a completed
        action into an exception that the service layer reads as "the agent declined"."""
        original = self.runtime.build_card
        self.runtime.build_card = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad card"))
        try:
            reply = self.runtime.handle(
                self.fx.cur, user_id=OWNER_ID, text="Pause that alert",
                capability_id="crypto.alerts.pause",
                arguments={"alert_id": self.alert_id}, request_id="r-card",
            )
        finally:
            self.runtime.build_card = original
        self.fx.commit()

        self.assertTrue(reply.handled)
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")

    def test_an_executor_refusal_becomes_a_result_not_an_exception(self) -> None:
        """An ``AgentError`` raised inside an executor used to propagate out of
        ``execute``, past every caller that assumed it returns. It is now a failed
        ``ToolResult``, so the ledger and audit rows are still written."""
        outcome = self.gateway.execute(
            self.fx.cur, user_id=OWNER_ID, capability_id="crypto.alerts.pause",
            proposed_arguments={"alert_id": 999_999}, request_id="r-missing",
            explicit_request=True,
        )
        self.fx.commit()
        self.assertIn(outcome.receipt.status, {"recoverable_failure", "terminal_failure"})
        self.assertFalse(outcome.receipt.may_claim_completed)


class UndoOnTheWire(unittest.TestCase):
    """What the client is actually sent, since that is what an Undo button reads."""

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import undx_agent_runtime, undx_capability_registry, undx_tool_gateway

        self.gateway = undx_tool_gateway
        self.registry = undx_capability_registry
        self.runtime = undx_agent_runtime
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)

    def tearDown(self) -> None:
        self.fx.stop()

    def _card(self, capability_id, arguments, token=""):
        outcome = self.gateway.execute(
            self.fx.cur, user_id=OWNER_ID, capability_id=capability_id,
            proposed_arguments=dict(arguments), request_id="r-undo",
            explicit_request=True, confirmation_token=token)
        self.fx.commit()
        return self.registry.get(capability_id), outcome, self.runtime.build_card(
            self.registry.get(capability_id), outcome)

    def test_a_pause_receipt_carries_the_arguments_that_resume_it(self) -> None:
        _, outcome, card = self._card("crypto.alerts.pause", {"alert_id": self.alert_id})
        self.assertEqual(outcome.receipt.status, "verified_success")
        self.assertTrue(card["can_undo"])
        self.assertEqual(card["undo_capability_id"], "crypto.alerts.resume")
        self.assertEqual(card["undo_arguments"], {"alert_id": self.alert_id})

    def test_the_undo_arguments_actually_reverse_the_change(self) -> None:
        """The end the review was worried about: not that the fields are populated, but
        that sending them back restores the state the user started in."""
        _, _, card = self._card("crypto.alerts.pause", {"alert_id": self.alert_id})
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")

        undone = self.gateway.execute(
            self.fx.cur, user_id=OWNER_ID, capability_id=card["undo_capability_id"],
            proposed_arguments=dict(card["undo_arguments"]), request_id="r-undo-2",
            explicit_request=True)
        self.fx.commit()
        self.assertEqual(undone.receipt.status, "verified_success")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_no_undo_is_offered_on_an_action_that_was_not_verified(self) -> None:
        _, outcome, card = self._card("crypto.alerts.pause", {"alert_id": 999_999})
        self.assertNotEqual(outcome.receipt.status, "verified_success")
        self.assertFalse(card["can_undo"])
        self.assertEqual(card["undo_capability_id"], "")
        self.assertEqual(card["undo_arguments"], {})


class AnOrdinalMeansOneThing(unittest.TestCase):
    """A chooser that answers "the first one" and ignores "the sixth one".

    ``read_choice`` is a pure function of the rows and the reply, so these are written
    against it directly rather than through a turn. That is deliberate: the whole point
    of this class is the *reading*, and a fixture would add three tables and a commit to
    every assertion about a regular expression. One end-to-end journey lives in
    ``test_question_shape`` to prove the reading reaches a card.

    Three properties, and they are not equally important. Coverage — sixth, final, bottom
    — is a lost turn. Correctness is a wrong write: "the one before last" resolved to the
    *last* row, because ``\\blast\\b`` matches inside the phrase that excludes it. And
    negation was worse than either, because a rule that collects readings by searching
    for words anywhere in the reply cannot tell naming a row from ruling it out, so "not
    the first one" resolved to the first one and put it on a confirmation card.
    """

    def rows(self, ids: list[int], symbol: str = "BTC") -> list[dict]:
        """A chooser shaped the way ``_numbered`` publishes one, in drawn order."""
        return [{"alert_id": value, "symbol": symbol, "condition": "above",
                 "threshold": 90000 + index, "choice_index": index + 1}
                for index, value in enumerate(ids)]

    def read(self, ids: list[int], reply: str):
        from services import undx_agent_runtime

        return undx_agent_runtime.read_choice(self.rows(ids), reply)

    # ---- correctness: the readings that pointed at the wrong row ----------------

    def test_the_row_before_last_is_not_the_last_row(self) -> None:
        """The wrong-row reading, and the one that cost a write rather than a turn.

        "The one before last" contains "last", and the reading searched for "last"
        anywhere in the reply — so a person naming row N-1 was handed row N, on a
        confirmation card whose message is the generic "I need you to confirm this
        before I make the change". Nothing on that card contradicts the misreading
        loudly enough to be caught, which is why this is the first assertion here.

        Asserted at both list lengths because the phrase is positional from the far end
        and a three-row list is where an off-by-one is easiest to write and hardest to
        see.
        """
        for phrase in ("the one before last", "second to last", "the second-to-last one",
                       "next to last", "penultimate"):
            with self.subTest(phrase=phrase):
                self.assertEqual(12, self.read([11, 12, 13], phrase).chosen)
                self.assertEqual(25, self.read([21, 22, 23, 24, 25, 26], phrase).chosen)

    def test_second_to_last_is_not_also_the_second(self) -> None:
        """The phrase carries "second", and testing for both would refuse it.

        The fix removes the phrase from the text before the ordinal search runs rather
        than testing the two side by side. Tested side by side, "second to last" names
        row 2 and row N-1 at once and comes back ambiguous — which is a different wrong
        answer rather than a fix, and is the shape this would most plausibly regress to.
        """
        reading = self.read([11, 12, 13], "second to last")
        self.assertEqual(12, reading.chosen)
        self.assertEqual("", reading.miss)

    # ---- negation: the reading that answered the opposite of the question --------

    def test_a_row_ruled_out_is_never_the_row_chosen(self) -> None:
        """The worst input to a rule that reads by presence alone.

        Every one of these named a row in order to exclude it, and every one of them
        resolved *to* that row before this batch. Against three rows the exclusion
        leaves two, so the honest answer is to say so and ask again — which is a
        reported miss rather than silence, because a reply that names a row on the card
        is proof the reply was aimed at the question.
        """
        from services import undx_agent_runtime

        for reply in ("not the first one", "anything but the first one",
                      "don't touch the first one", "no, not the first one",
                      "everything but the first", "other than the first one"):
            with self.subTest(reply=reply):
                reading = self.read([11, 12, 13], reply)
                self.assertEqual(0, reading.chosen)
                self.assertEqual(undx_agent_runtime.CHOICE_MISS_EXCLUDED, reading.miss)

    def test_an_exclusion_that_leaves_one_row_is_a_reading_not_a_guess(self) -> None:
        """Set subtraction over a list the person was shown is not a guess.

        The published chooser is the complete set by construction, so "not the first
        one" against two rows names the second as surely as saying so, and "neither the
        first nor the second" against three names the third. Declining these would be
        the runtime being pedantic about a reply with exactly one possible meaning.

        The boundary is the count, not the confidence: two rows left is re-asked, and
        that is asserted in the test above rather than argued here.
        """
        self.assertEqual(12, self.read([11, 12], "not the first one").chosen)
        self.assertEqual(11, self.read([11, 12], "not the last one").chosen)
        self.assertEqual(13, self.read([11, 12, 13],
                                       "neither the first nor the second").chosen)

    def test_a_correction_still_names_the_row_it_corrects_to(self) -> None:
        """The reason "no" is not a negation cue, and the reason the comma is the scope.

        "No, the third one" is the commonest correction there is: a person rejecting
        what was offered and naming what they meant, in one breath. A blanket rule on
        "no" would turn it into a re-ask, which is the negation fix overshooting into a
        second defect — so the cue set omits the bare marker and the scope stops at the
        punctuation the person typed.

        The pair is what makes it a test. One comma apart, "no, the third one" and "no,
        not the third one" mean opposite things, and both have to come out right from
        the same rule.
        """
        from services import undx_agent_runtime

        self.assertEqual(13, self.read([11, 12, 13], "no, the third one").chosen)
        self.assertEqual(13, self.read([11, 12, 13], "no the third one").chosen)
        self.assertEqual(12, self.read([11, 12, 13], "nope, the second one").chosen)
        self.assertEqual(12, self.read([11, 12, 13], "the second one, not the first").chosen)
        self.assertEqual(undx_agent_runtime.CHOICE_MISS_EXCLUDED,
                         self.read([11, 12, 13], "no, not the third one").miss)

    def test_a_negation_that_names_no_row_stays_silent(self) -> None:
        """The exclusion path must not start swallowing unrelated sentences.

        "Do not pause it" carries a cue and names nothing, and is not an answer to
        "which one". It was silence before this batch and is silence after it: the
        runtime has no evidence it was aimed at the question, and the discipline this
        reading is built on is that a message it has no evidence about is a message
        about something else.

        "I'm not sure" was in this list too, on the same reasoning, and Batch 17 took
        it out — see ``test_saying_so_is_an_answer_to_which_one``. The reasoning was
        wrong about that one phrase and right about the rest, so the rest stay here.
        """
        for reply in ("do not pause it", "never mind", "neither of them",
                      "not that one either"):
            with self.subTest(reply=reply):
                reading = self.read([11, 12, 13], reply)
                self.assertEqual(0, reading.chosen)
                self.assertEqual("", reading.miss)

    def test_a_negation_still_means_no_without_its_apostrophe(self) -> None:
        """One character of punctuation was carrying a wrong write.

        Batch 15 closed "not the first one" and left the contraction matching only its
        punctuated form. So the same sentence off a keyboard that had not autocorrected
        — "dont pause the first one" — missed the exclusion path entirely and resolved
        *to* row one, on a confirmation card, from a reply whose whole purpose was to
        say no to it. That is not a smaller version of the Batch 15 defect; it is the
        Batch 15 defect, still live, reachable by dropping one character.

        Asserted as pairs so the property is the one that matters: the two spellings
        have to read identically. A test on the bare forms alone would still pass if
        someone "fixed" this by making both of them silent.

        The last three pairs are here because ``mutate18.py``'s ``fullstems`` mode
        reported zero failures without them. English contractions do not all clip at
        the word boundary — can't is "ca" plus "n't", won't is "wo", ain't is "ai" —
        and the natural way to write the alternation is with the words, which look
        right and match nothing. Every pair above uses a stem that happens to be its
        own word, so the whole class where the two differ went untested.
        """
        from services import undx_agent_runtime

        for bare, typed in (("dont pause the first one", "don't pause the first one"),
                            ("wasnt the first one", "wasn't the first one"),
                            ("isnt the second one", "isn't the second one"),
                            ("didnt mean the first one", "didn't mean the first one"),
                            ("couldnt have been the first one",
                             "couldn't have been the first one"),
                            ("cant be the first one", "can't be the first one"),
                            ("wont be the first one", "won't be the first one"),
                            ("aint the first one", "ain't the first one")):
            with self.subTest(reply=bare):
                reading = self.read([11, 12, 13], bare)
                self.assertEqual(0, reading.chosen)
                self.assertEqual(undx_agent_runtime.CHOICE_MISS_EXCLUDED, reading.miss)
                self.assertEqual(self.read([11, 12, 13], typed).miss, reading.miss)

    def test_a_word_that_merely_ends_in_nt_is_not_a_negation(self) -> None:
        """The overshoot the fix above is one careless character away from.

        The obvious pattern is ``n'?t\\b``, and without the apostrophe it matches the
        last two letters of any word ending in "nt". "I want the first one" becomes an
        exclusion of the row it names, and so do "the recent one" and "the second
        component" — turning a fix for a wrong write into a refusal to read the most
        ordinary replies there are.

        So the contractions are enumerated by stem instead, and this is the test that
        makes that a requirement rather than a preference. Every reply here names a row
        and must keep resolving to it.
        """
        for reply, expected in (("i want the first one", 11),
                                ("i meant the second one", 12),
                                ("the one i sent, the third", 13),
                                ("point at the first one", 11),
                                ("im confident its the second one", 12),
                                ("the important one is the third", 13),
                                ("i am certain its the first one", 11)):
            with self.subTest(reply=reply):
                reading = self.read([11, 12, 13], reply)
                self.assertEqual(expected, reading.chosen)
                self.assertEqual("", reading.miss)

    # ---- the two answers that name no row and are still answers -----------------

    def test_saying_so_is_an_answer_to_which_one(self) -> None:
        """"I don't know" is a reply to the question, not a change of subject.

        Every other miss in this file is proved from the reply and the rows together:
        a number out of range, a word that fits two rows, an ordering the list does not
        have. These two cannot be proved that way, because what makes them answers is
        what they mean rather than what they point at — which is why they are the only
        two readings here that are vocabularies, and why they are consulted last, after
        every reading that could name a row has already declined.

        Before Batch 17 they were filed with "what is my account health": the turn
        declined, the pending question burned, and the numbered rows stayed on screen
        above a number that would no longer do anything. The person had answered
        honestly and been punished for it.

        The two are kept apart because they need different sentences. Someone who
        cannot decide is not asking for anything; someone who says "all of them" is
        asking for something UNDX has no capability to do.
        """
        from services import undx_agent_runtime

        for reply in ("i don't know", "i dont know", "not sure", "i'm not sure",
                      "no idea", "dunno", "i can't tell", "no clue"):
            with self.subTest(reply=reply):
                reading = self.read([11, 12, 13], reply)
                self.assertEqual(0, reading.chosen)
                self.assertEqual(undx_agent_runtime.CHOICE_MISS_UNDECIDED, reading.miss)

        for reply in ("all of them", "all", "both", "every one", "each of them",
                      "just do all of them"):
            with self.subTest(reply=reply):
                reading = self.read([11, 12, 13], reply)
                self.assertEqual(0, reading.chosen)
                self.assertEqual(undx_agent_runtime.CHOICE_MISS_EVERY_ROW, reading.miss)

    def test_all_but_one_is_an_exclusion_and_not_a_request_for_all(self) -> None:
        """The word "all" opens the commonest exclusion there is.

        "All but the third one" starts with the word the every-row reading is built on
        and means very nearly its opposite. Reading it as a request for all of them
        would tell a person who had just narrowed the list that the runtime cannot
        narrow lists, and the two-row case is sharper still: there the exclusion
        resolves, one row left and named as surely as if it had been pointed at, so an
        every-row reading running first would replace a correct write with a refusal.

        Neither of these actually reaches the negation guard, and that is worth writing
        down rather than leaving as a comfortable ambiguity. They are safe because
        ``_read_excluding`` finishes with them — a reply that names a row and rules it
        out never falls through to ``_unread`` at all. The guard exists for the replies
        below, and this test only covers the top half; the mutation that removes the
        guard walks straight past it.
        """
        from services import undx_agent_runtime

        reading = self.read([11, 12, 13], "all but the third one")
        self.assertEqual(0, reading.chosen)
        self.assertEqual(undx_agent_runtime.CHOICE_MISS_EXCLUDED, reading.miss)
        self.assertEqual(11, self.read([11, 12], "all but the second one").chosen)

    def test_a_refusal_of_all_of_them_is_not_a_request_for_all_of_them(self) -> None:
        """What the negation guard is actually for, found by the mutation that removed it.

        ``mutate17.py``'s ``everyrowignoresnegation`` reported zero failures against the
        test above, which appeared to be exactly about this. It was not: "all but the
        third one" names a row and rules it out, so ``_read_excluding`` returns an
        exclusion miss and ``_unread`` — where the every-row vocabulary lives — is never
        consulted. The guard is unreachable on that route and the assertion could not
        break.

        These are the replies that do reach it: a negation cue, the word "all", and no
        row named anywhere. Without the guard a person who typed "not all of them" is
        answered "UNDX changes these one at a time, so it needs one of them" — the
        sentence for the opposite request, and one that reads plausibly enough to
        survive a review. With it they get silence, which is right: ruling out the whole
        list rules out the question too, and the runtime has no row left to offer and no
        business pretending the reply was a request.
        """
        for reply in ("not all of them", "not every one", "not both of them",
                      "none of them"):
            with self.subTest(reply=reply):
                reading = self.read([11, 12, 13], reply)
                self.assertEqual(0, reading.chosen)
                self.assertEqual("", reading.miss)

    def test_being_undecided_is_read_before_being_told_the_list_is_unordered(self) -> None:
        """"I'm not sure which is the newest" is one person, not two readings.

        The sentence carries a recency word and an undecided one, and only one of them
        is what the person is telling the runtime. Answering the recency reading would
        say "these are not listed by date, so I cannot tell which is newest" — a true
        sentence, addressed to a question they did not ask, in place of the one that
        helps: nothing has changed, go and look.

        This is the whole reason the undecided reading sits above the recency one in
        ``_unread`` rather than below it, and the ordering is asserted here because it
        is invisible at the call site.
        """
        from services import undx_agent_runtime

        for reply in ("i'm not sure which is the newest",
                      "no idea which one is the latest"):
            with self.subTest(reply=reply):
                reading = self.read([11, 12, 13], reply)
                self.assertEqual(0, reading.chosen)
                self.assertEqual(undx_agent_runtime.CHOICE_MISS_UNDECIDED, reading.miss)

    def test_neither_new_reading_ever_outranks_a_named_row(self) -> None:
        """Both are consulted only where the function was about to return nothing.

        This is the same guarantee the recency reading has and it matters more here,
        because these two are vocabularies: a bare "all" is a word that turns up inside
        replies that do name a row. Checking either before the row readings would turn
        "number 2, that's all" into a refusal to act on any of them.
        """
        self.assertEqual(12, self.read([11, 12, 13], "number 2, that's all").chosen)
        self.assertEqual(11, self.read([11, 12, 13],
                                       "the first one, i think, not sure").chosen)

    # ---- coverage: the readings that were nothing at all ------------------------

    def test_the_far_end_of_the_list_answers_to_all_of_its_names(self) -> None:
        """"Final" and "bottom" said what "last" says and were read as nothing."""
        for phrase in ("the last one", "the final one", "the bottom one"):
            with self.subTest(phrase=phrase):
                self.assertEqual(13, self.read([11, 12, 13], phrase).chosen)
                self.assertEqual(26, self.read([21, 22, 23, 24, 25, 26], phrase).chosen)

    def test_a_chooser_longer_than_five_rows_can_be_answered(self) -> None:
        """The ordinal table stopped at "fifth" while the scan cap is fifty.

        Six rows is the first length where the two disagree, and the disagreement was
        silence: the runtime drew a sixth row, was answered about it, and ended the turn
        with no card. A list the runtime itself published has to be answerable at every
        position it published.
        """
        drawn = [21, 22, 23, 24, 25, 26]
        for position, phrase in enumerate(
                ("the first one", "the second one", "the third one",
                 "the fourth one", "the fifth one", "the sixth one"), 0):
            with self.subTest(phrase=phrase):
                self.assertEqual(drawn[position], self.read(drawn, phrase).chosen)

    def test_a_position_past_the_end_is_reported_rather_than_ignored(self) -> None:
        """The position analogue of "alert 8" against three rows.

        "The fourth one" against a three-row chooser names the very kind of thing the
        question is about and gets the number wrong, which is the same evidence "alert
        8" carries and has been reported as a miss since Batch 13. As a position it was
        silence — so the same mistake, made in words instead of digits, ended the turn.
        """
        from services import undx_agent_runtime

        for phrase in ("the fourth one", "the fifth one", "the seventh one"):
            with self.subTest(phrase=phrase):
                reading = self.read([11, 12, 13], phrase)
                self.assertEqual(0, reading.chosen)
                self.assertEqual(undx_agent_runtime.CHOICE_MISS_NO_SUCH_ROW, reading.miss)

    # ---- recency: the words that must not be read as positions ------------------

    def test_recency_is_reported_and_never_read_as_a_position(self) -> None:
        """The tempting fix, and why it is refused.

        Mapping "the newest one" onto row 1 would work most of the time and be quietly
        wrong exactly when the account is interesting: the rows are drawn ``active
        first, then updated_at DESC``, so a paused alert edited a minute ago sorts below
        an active one untouched for a month. Reading it from ``created_at`` was measured
        and rejected too — the column is stored to the second, and three alerts made in
        one sitting come back identical, so that reading would resolve or fall silent
        depending on how fast the person had been typing.

        So it is reported. The person is told the list is not in date order and asked
        for the number, which is a sentence they can act on, and the assertion that
        matters is the pair: a miss, and *not* a chosen row.
        """
        from services import undx_agent_runtime

        for phrase in ("the newest one", "the oldest one", "the latest one",
                       "the most recent one", "the earliest one"):
            with self.subTest(phrase=phrase):
                reading = self.read([11, 12, 13], phrase)
                self.assertEqual(0, reading.chosen)
                self.assertEqual(undx_agent_runtime.CHOICE_MISS_UNORDERED, reading.miss)

    def test_a_recency_word_never_outranks_a_reading(self) -> None:
        """The report only ever replaces silence, which is what keeps it safe.

        A recency word can appear in a reply that already names a row — "the latest one,
        number 2" — and in a sentence that is not about the chooser at all. Checking it
        first would turn both into a re-ask; checking it only where the function was
        about to return nothing means it can add a sentence and can never remove a
        resolution.
        """
        self.assertEqual(12, self.read([11, 12, 13], "the latest one, number 2").chosen)
        self.assertEqual(12, self.read([11, 12, 13], "2, the most recent").chosen)
        self.assertEqual(11, self.read([11, 12, 13],
                                       "the first one, which is the newest").chosen)

    def test_every_reported_miss_has_a_sentence_to_say(self) -> None:
        """A miss code with no entry in ``_REASK_DETAIL`` falls back to the wrong words.

        ``_reask_response`` looks the code up with a default, so a new code added
        without its sentence does not crash — it silently says "that is not one of
        these" to a person who ruled a row out. That is the failure mode this batch is
        about, one layer up, so the mapping is asserted rather than trusted.

        The codes are collected off the module rather than listed here, which is the
        only version of this test that keeps working. Batch 17 added two and the listed
        version passed all the way through the change: it enumerated the four it knew
        and never noticed the two it did not, which is precisely the regression it
        exists to catch.

        What is asserted about each sentence changed with them. "Ends in a question
        mark" was the old rule and ``CHOICE_MISS_UNDECIDED`` deliberately breaks it —
        re-asking "which number?" is the least useful thing to say to someone who has
        just said they cannot answer it. The rule underneath was never punctuation: a
        re-ask has to leave the person holding an action they can take. So each
        sentence must either put the question again or say what to do instead, and all
        of them must differ, because two codes sharing a sentence is a distinction the
        runtime draws and the person never sees.
        """
        from services import undx_agent_runtime as runtime

        codes = {name: getattr(runtime, name) for name in dir(runtime)
                 if name.startswith("CHOICE_MISS_")}
        self.assertGreaterEqual(len(codes), 6)
        for name, code in sorted(codes.items()):
            with self.subTest(code=name):
                self.assertIn(code, runtime._REASK_DETAIL)
                sentence = runtime._REASK_DETAIL[code].strip()
                self.assertTrue(sentence.endswith("?") or "number" in sentence.lower(),
                                f"{name} leaves the person with nothing to do next")
        self.assertEqual(len(codes), len(set(runtime._REASK_DETAIL.values())))


class ACardNamesWhatItChanges(unittest.TestCase):
    """An approval must be about one identifiable thing, not about an id.

    Every batch since the twelfth has leaned on the same defence: a misreading is
    shown to the person on a confirmation card and refused before anything is
    written. That defence assumed the card said which row it was about. It did not.
    Two cards staging pauses of two different coins — one BTC, one DOGE, both
    produced by the sentence "pause my bitcoin alert" — were identical in every
    field except ``target``, which is a row id this app displays nowhere. So the
    refusal the defence depends on was not available: there was nothing on the card
    to notice.

    The tests below are about the label and about where it comes from, which matter
    equally. A label composed from the request would render the same words on both
    of those cards and would look like a fix while removing the only signal.
    """

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import undx_agent_runtime, undx_capability_registry

        self.runtime = undx_agent_runtime
        self.registry = undx_capability_registry

    def tearDown(self) -> None:
        self.fx.stop()

    def label(self, alert_id: int, capability: str = "crypto.alerts.pause") -> str:
        spec = self.registry.get(capability)
        return self.runtime.preview(OWNER_ID, spec, {"alert_id": alert_id})[2]

    def card(self, *messages: str) -> dict:
        """Whole turns in order, returning the card the last one drew."""
        answered = None
        for message in messages:
            answered = self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text=message)
            self.fx.commit()
        return (answered.card or {}) if answered else {}

    def crowd(self, other: str = "DOGE") -> int:
        """More bitcoin alerts than resolution will compare, plus one of another coin.

        This is the shape that produced the defect and it cannot be simplified away.
        Past ``_MAX_REFERENCE_SCAN`` the runtime declines to draw a chooser and asks
        the person to open their alerts and name one, which is the only route on
        which a raw id is a reasonable thing for them to type — and therefore the
        only route on which the id can disagree with the sentence that preceded it.
        """
        odd_one_out = self.fx.make_alert(OWNER_ID, symbol=other, threshold=0.5)
        for step in range(self.runtime._MAX_REFERENCE_SCAN + 1):
            self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=float(90000 + step))
        return odd_one_out

    # -- the label ---------------------------------------------------------

    def test_two_alerts_do_not_produce_the_same_card(self) -> None:
        """The defect itself. Distinctness is the property, so it is asserted as
        distinctness rather than by matching either card against a fixed string."""
        btc = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
        doge = self.fx.make_alert(OWNER_ID, symbol="DOGE", threshold=0.5)
        first, second = self.label(btc), self.label(doge)
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertNotEqual(first, second)

    def test_the_label_says_the_coin_the_row_holds_not_the_one_that_was_asked_for(self) -> None:
        """The whole point. The person said "bitcoin" and the id they then gave
        belongs to a DOGE alert; the card has to say DOGE, because a card that
        agreed with the request would be silent exactly when it is needed."""
        doge = self.crowd()
        card = self.card("pause my bitcoin alert", str(doge))
        self.assertEqual("confirmation_required", card.get("status"))
        self.assertIn("DOGE", str(card.get("resource_label") or ""))
        self.assertNotIn("BTC", str(card.get("resource_label") or ""))

    def test_the_coin_word_and_the_id_can_disagree_inside_one_sentence(self) -> None:
        """The same disagreement, arriving all at once, which is where it can lie.

        The two-turn version above cannot actually be got wrong, and that is why it
        needs this one beside it. By the time the confirming turn runs its text is
        the bare id and the word "bitcoin" is gone — the stored continuation carries
        a capability id and a missing field name and no request text at all. A label
        wrongly composed from the request would, on that route, find no coin word and
        fall through to the correct one. The assertion is right and the route cannot
        break it.

        Here the id and the coin word arrive in the same sentence, which is exactly
        what the truncated refusal invites: "open your alerts and tell me which one"
        comes back as a sentence at least as often as a bare number. The id wins the
        resolution, correctly, and the capability is one that always confirms. So the
        label has to follow the id and not the word sitting next to it — one that
        agreed with the word would read "BTC alert" over a DOGE row, turning the card
        from the thing that catches this into the thing that hides it.

        Found by ``mutate16.py``'s ``labelfromrequest`` mode reporting zero failures.
        """
        doge = self.crowd()
        for said in (f"delete my bitcoin alert {doge}",
                     f"change my bitcoin alert {doge} to 120000"):
            with self.subTest(said=said):
                card = self.card(said)
                self.assertEqual("confirmation_required", card.get("status"))
                label = str(card.get("resource_label") or "")
                self.assertIn("DOGE", label)
                self.assertNotIn("BTC", label)
                # The price is still in it, so this is the row read back rather than
                # a symbol swapped in for one.
                self.assertIn("0.5", label)
                self.assertEqual(str(doge), str(card.get("target")))

    def test_the_label_carries_the_numbers_that_tell_two_alerts_apart(self) -> None:
        """Same coin, same direction, different price is the ordinary case — an
        alert set twice at two levels. A label that stopped at the symbol would put
        the two cards back to being indistinguishable."""
        low = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
        high = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=120000.0)
        self.assertIn("90,000", self.label(low))
        self.assertIn("120,000", self.label(high))

    def test_a_price_reads_the_way_a_person_writes_one(self) -> None:
        """``90000.0`` is the column, not the language. Small, and the reason the
        card is worth reading at all."""
        self.assertEqual("90,000", self.runtime._amount(90000.0))
        self.assertEqual("0.5", self.runtime._amount(0.5))
        self.assertEqual("", self.runtime._amount(None))
        self.assertEqual("", self.runtime._amount("90000"))

    # -- where the label comes from ---------------------------------------

    def test_the_label_is_built_from_the_row_and_from_nothing_else(self) -> None:
        """``describe_alert`` takes a record and no request text, so there is no
        parameter through which the person's words could reach it. Asserted on the
        signature because it is a structural guarantee: a later caller cannot pass
        what the function cannot accept."""
        import inspect

        parameters = list(inspect.signature(self.runtime.describe_alert).parameters)
        self.assertEqual(["record"], parameters)

    def test_no_row_means_no_label_rather_than_an_invented_one(self) -> None:
        """A read that failed must leave the field empty. Filling it from the
        arguments would produce a confident description of a row nobody read."""
        self.assertEqual("", self.runtime.describe_alert({}))
        self.assertEqual("", self.runtime.describe_alert(None))
        alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
        self.fx.set_flags(UNDX_AGENT_DISABLED_CAPABILITIES="crypto.alerts.get")
        self.assertEqual("", self.label(alert_id))

    def test_an_unlabelled_card_still_carries_its_identifier(self) -> None:
        """The label is presentation and the target is identity. Losing the second
        to gain the first would unbind the approval from the row it approves.

        Built at the card layer rather than driven through a turn, because the state
        being asserted — an approval that exists with no label on it — is one the
        runtime as a whole declines to reach. Switching the supporting read off does
        not produce an unlabelled confirmation; it produces no confirmation, since
        the same read is what proves the id is real. That is the right behaviour and
        it means the fallback has to be tested where it lives.
        """
        from services import undx_agent_contracts, undx_tool_gateway

        spec = self.registry.get("crypto.alerts.pause")
        grant = undx_agent_contracts.ConfirmationRequest(
            confirmation_id="undx_confirm_test", confirmation_token="token",
            capability_id=spec.capability_id, action_name=spec.description,
            target="7", current_value="active", proposed_value="paused",
            risk_summary=spec.description, expires_at="2026-07-30T00:00:00+00:00",
        )
        receipt = self.runtime._bare_receipt(
            spec, user_id=OWNER_ID, request_id="undx_req_test",
            status="confirmation_required", explanation="Confirm?")
        card = self.runtime.build_card(
            spec, undx_tool_gateway.GatewayOutcome(receipt, confirmation=grant))
        self.assertEqual("", card.get("resource_label"))
        self.assertEqual("7", card.get("target"))

    def test_the_label_cannot_move_what_the_approval_is_bound_to(self) -> None:
        """The token is bound to the hash of the validated arguments. The label is
        copied onto the grant after that hash is computed and is not part of it, so
        a wrong label is a wrong sentence and never a wrong write."""
        from services import undx_agent_contracts

        fields = undx_agent_contracts.ConfirmationRequest.__dataclass_fields__
        self.assertIn("resource_label", fields)
        self.assertEqual("", fields["resource_label"].default)

    # -- every write, not just this one ------------------------------------

    def test_every_confirmable_alert_write_names_its_row(self) -> None:
        """Pause is not a special case. Delete is the one that cannot be taken back,
        and update is the one where the before value is a price rather than a status
        — so an unnamed card there says "90000 → 95000" about no particular alert."""
        alert_id = self.fx.make_alert(OWNER_ID, symbol="ETH", threshold=3000.0)
        for capability in ("crypto.alerts.pause", "crypto.alerts.resume",
                           "crypto.alerts.delete", "crypto.alerts.update"):
            with self.subTest(capability=capability):
                self.assertIn("ETH", self.label(alert_id, capability))

    def test_the_label_comes_out_of_the_same_read_as_the_values(self) -> None:
        """One read, three values, and that is the point rather than an economy.

        The first cut of this batch left a two-value ``preview`` wrapping a private
        three-value one, so that existing tests would not have to change their
        unpacking. Nothing in the runtime called the wrapper — it was a function kept
        alive for the convenience of its own tests, and it cost a real seam: the
        fault-injection list in ``test_point_of_no_return`` still named the wrapper
        after the turn had stopped calling it. So the wrapper is gone and the values
        arrive together, which is also the guarantee the card depends on: the label
        cannot describe a different row from the one the before-value was read from,
        because there is only ever one read to disagree with.
        """
        spec = self.registry.get("crypto.alerts.pause")
        alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
        before, after, label = self.runtime.preview(OWNER_ID, spec, {"alert_id": alert_id})
        self.assertEqual("active", before)
        self.assertEqual("paused", after)
        self.assertEqual("BTC alert · above · 90,000", label)


class WithdrawalReachesOnlyTheRightGrants(unittest.TestCase):
    """Batch 19's two primitives, tested where their guards are actually reachable.

    ``pending_approvals`` and ``revoke_approval`` are the first pair in this codebase
    that address a grant by *account* rather than by the bearer token, because a person
    who types "never mind" is talking rather than echoing a credential. That is the
    whole reason the batch works, and it is also the reason these two need tests of
    their own rather than only journey tests.

    Both carry the same two guards — owner scope, and the ``undx.continuation:``
    namespace exclusion — and both are defended a second time by the runtime's ordering
    (a live question wins over a withdrawal, and a withdrawal only reaches ``revoke``
    for grants ``pending_approvals`` already returned). That redundancy is deliberate
    and it is also what makes the guards invisible to a journey test: mutate any one of
    the four in isolation and every end-to-end assertion still passes, because the other
    three catch it. A property no test can see is a property that will be deleted by a
    future refactor with a green suite, so each one is asserted here directly against
    the primitive, with the layer above it removed.

    Written after ``mutate19.py`` walked past four modes. That is the same rule that has
    now fired six times across this programme: a mutation mode that catches nothing has
    named a decorative test, and the answer is the missing test, never a weaker mode.
    """

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import undx_architecture

        self.arch = undx_architecture

    def tearDown(self) -> None:
        self.fx.stop()

    def grant(self, user_id: int = OWNER_ID, action_id: str = "crypto.alerts.delete") -> dict:
        made = self.arch.create_confirmation(
            self.fx.cur, int(user_id),
            {"action_id": action_id, "action_version": "agent.1",
             "target_id": "42", "arguments": {"alert_id": 42}},
        )
        self.fx.commit()
        return made

    # -- owner scope ---------------------------------------------------------

    def test_the_read_does_not_see_another_accounts_grant(self) -> None:
        """The boundary, with nothing else standing in front of it.

        End to end this is masked twice over: the withdrawal path returns early on an
        empty list, and ``revoke_approval`` re-scopes anyway. Asserted here so that
        removing ``WHERE user_id=?`` from the read is a failure rather than a no-op.
        """
        self.grant(OTHER_ID)
        self.assertEqual([], self.arch.pending_approvals(self.fx.cur, OWNER_ID))

    def test_the_revoke_refuses_another_accounts_grant(self) -> None:
        """Same boundary on the write, addressed by an id the caller should not have.

        The id is handed in directly, which is exactly the situation the guard exists
        for: a caller that filtered correctly would never produce this call, and a
        write whose safety depends on its caller having filtered first is one refactor
        away from being wrong.
        """
        made = self.grant(OTHER_ID)
        self.assertFalse(
            self.arch.revoke_approval(self.fx.cur, OWNER_ID, made["confirmation_id"]))
        self.fx.commit()
        # And the grant is untouched, not merely un-reported.
        self.assertEqual(1, len(self.arch.pending_approvals(self.fx.cur, OTHER_ID)))

    # -- the continuation namespace ------------------------------------------

    def test_a_remembered_question_is_not_a_pending_approval(self) -> None:
        """The one place the two meanings could be confused, since they share a table.

        A continuation and an approval are both ``status='pending'`` rows in
        ``pulse_ai_confirmations``; only the ``action_id`` prefix separates them. A
        remembered question is not permission to do anything, so returning one here
        would let "never mind" report "that is cancelled" about a write nobody staged.
        """
        self.arch.create_continuation(
            self.fx.cur, OWNER_ID, capability_id="crypto.alerts.pause",
            arguments={}, missing=["alert_id"], choices=[],
        )
        self.fx.commit()
        self.assertEqual([], self.arch.pending_approvals(self.fx.cur, OWNER_ID))

    def test_the_revoke_will_not_cancel_a_remembered_question(self) -> None:
        """The guard repeated on the write, and the reason it is repeated.

        ``revoke_approval`` could trust its caller to have filtered — it is only ever
        handed ids that ``pending_approvals`` returned. It does not, because the day
        someone adds a second caller is the day a "never mind" starts silently eating
        open questions, and nothing about that failure is visible.
        """
        continuation_id = self.arch.create_continuation(
            self.fx.cur, OWNER_ID, capability_id="crypto.alerts.pause",
            arguments={}, missing=["alert_id"], choices=[],
        )
        self.fx.commit()
        self.assertTrue(continuation_id)
        self.assertFalse(
            self.arch.revoke_approval(self.fx.cur, OWNER_ID, continuation_id))
        self.fx.commit()
        self.assertIsNotNone(self.arch.pending_continuation(self.fx.cur, OWNER_ID))

    # -- single use ----------------------------------------------------------

    def test_a_spent_grant_cannot_be_revoked_twice(self) -> None:
        """The ``status='pending'`` guard, which is also the race resolver.

        If Confirm is tapped at the same moment "cancel" is typed, exactly one of the
        two ``UPDATE``s finds a pending row. That is what makes the pair safe without a
        lock, and it is why a second revoke must report ``False`` rather than shrug: the
        runtime reads that boolean to decide whether it is entitled to say "cancelled".
        """
        made = self.grant()
        self.assertTrue(
            self.arch.revoke_approval(self.fx.cur, OWNER_ID, made["confirmation_id"]))
        self.assertFalse(
            self.arch.revoke_approval(self.fx.cur, OWNER_ID, made["confirmation_id"]))


if __name__ == "__main__":
    unittest.main()
