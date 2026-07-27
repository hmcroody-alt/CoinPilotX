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

from tests.undx_agent.harness import AgentFixture, OWNER_ID  # noqa: E402


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
        before, after = self.runtime.preview(OWNER_ID, spec, {"alert_id": self.alert_id})
        self.assertEqual(before, "active")

        self.fx.set_flags(UNDX_AGENT_DISABLED_CAPABILITIES="crypto.alerts.get")
        before, after = self.runtime.preview(OWNER_ID, spec, {"alert_id": self.alert_id})
        self.assertIsNone(before)

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


if __name__ == "__main__":
    unittest.main()
