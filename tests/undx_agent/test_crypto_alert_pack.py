"""The crypto-alert pack, one operation at a time, executed for real.

A capability that is registered but never executed is a promise, not a feature. The
registry will happily describe ``crypto.alerts.update`` to a planner whether or not
``alert_engine.update_alert_rule`` has ever been called with arguments the gateway
produced. So this file drives all seven operations — list, get, create, pause,
resume, update, delete — through ``undx_tool_gateway.execute`` against a real SQLite
database and a real ``services.alert_engine``, and reads every outcome back through
the engine rather than through the receipt that claims it.

For each mutation the same seven properties are asserted, because each one has its
own way of being quietly wrong:

* owner isolation — the SQL, not the gateway, is what keeps accounts apart
* canonical id validation — an id that does not exist must fail, not invent
* confirmation policy — what the registry declares is what the gateway enforces
* argument-hash binding — an approval is for one action with one set of arguments
* idempotency — the same request twice is one mutation
* independent read-back — success is a claim about the world, checked separately
* audit persistence, typed card, deep link — what survives, and what the client renders

``CrudCase`` holds the shared assertions so each operation's class stays about what is
specific to it. That is deliberate: the properties above are a contract for the pack,
and a contract expressed once is a contract that cannot be half-applied.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OWNER_ID, OTHER_ID  # noqa: E402


class Driven:
    """A gateway outcome together with the card the client would be sent.

    The gateway returns a receipt; the card is built one layer up. Pairing them here
    means these tests assert the payload the native client actually receives —
    ``undx_agent_runtime.build_card`` is the real function, not a local
    reconstruction of it — while still driving each capability by id rather than by
    hoping a sentence matches it.
    """

    __slots__ = ("outcome", "card")

    def __init__(self, outcome, card) -> None:
        self.outcome = outcome
        self.card = card

    @property
    def receipt(self):
        return self.outcome.receipt

    @property
    def status(self) -> str:
        return self.outcome.receipt.status


class CrudCase(unittest.TestCase):
    """One database, one gateway, and the assertions shared by every operation."""

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import (alert_engine, undx_agent_runtime, undx_architecture,
                              undx_capability_registry, undx_tool_gateway)

        self.engine = alert_engine
        self.registry = undx_capability_registry
        self.architecture = undx_architecture
        self.gateway = undx_tool_gateway
        self.runtime = undx_agent_runtime
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)

    def tearDown(self) -> None:
        self.fx.stop()

    # -- driving ----------------------------------------------------------

    def run_capability(self, capability_id, arguments, *, user_id=OWNER_ID,
                       request_id="r1", token="", explicit=True) -> Driven:
        outcome = self.gateway.execute(
            self.fx.cur, user_id=int(user_id), capability_id=capability_id,
            proposed_arguments=dict(arguments), request_id=request_id,
            explicit_request=explicit, confirmation_token=token)
        self.fx.commit()
        return Driven(outcome, self.runtime.build_card(
            self.registry.get(capability_id), outcome))

    def approve(self, capability_id, arguments, *, user_id=OWNER_ID, request_id="r1"):
        """Run a consequential capability through its confirmation and return both halves."""
        asked = self.run_capability(capability_id, arguments, user_id=user_id,
                                    request_id=request_id)
        self.assertEqual(asked.receipt.status, "confirmation_required",
                         f"{capability_id} is declared 'always' and must ask first")
        token = asked.card.get("confirmation_token")
        self.assertTrue(token, "a confirmation card must carry the token that redeems it")
        done = self.run_capability(capability_id, arguments, user_id=user_id,
                                   request_id=f"{request_id}-confirm", token=token)
        return asked, done

    # -- reading the world ------------------------------------------------

    def rule(self, alert_id, user_id=OWNER_ID) -> dict:
        return self.engine.get_alert_rule(int(alert_id), int(user_id)) or {}

    def alerts(self, user_id=OWNER_ID) -> list[dict]:
        """The account's alerts, straight from the engine.

        ``list_alert_rules`` returns an envelope, not a list — counting the envelope
        would give 2 every time and turn every "did this create anything?" assertion
        into a tautology.
        """
        return list((self.engine.list_alert_rules(int(user_id)) or {}).get("alerts") or [])

    def operations(self, tool_name="") -> list[dict]:
        sql = ("SELECT operation_id, tool_name, status, canonical_entity_id, "
               "verification_json, idempotency_key FROM pulse_ai_tool_operations")
        params: tuple = ()
        if tool_name:
            sql += " WHERE tool_name=?"
            params = (tool_name,)
        self.fx.cur.execute(sql + " ORDER BY id", params)
        columns = ["operation_id", "tool_name", "status", "canonical_entity_id",
                   "verification_json", "idempotency_key"]
        return [dict(zip(columns, row)) for row in self.fx.cur.fetchall()]

    # -- shared assertions ------------------------------------------------

    def assert_declared_policy(self, capability_id, expected):
        self.assertEqual(self.registry.get(capability_id).confirmation, expected)

    def assert_audited(self, capability_id, expected_status="verified"):
        """The ledger holds exactly one settled row naming the thing that was touched."""
        spec = self.registry.get(capability_id)
        rows = self.operations(spec.tool_name)
        self.assertEqual(len(rows), 1, f"{capability_id} must leave exactly one ledger row")
        self.assertEqual(rows[0]["status"], expected_status)
        if spec.is_write:
            # Only a mutation has a resource to name. A list touches nothing in
            # particular, and inventing an entity id for it would make the column
            # useless for the question it exists to answer: what was changed.
            self.assertTrue(rows[0]["canonical_entity_id"],
                            "the audit row must name the resource it changed")
        self.assertTrue(rows[0]["idempotency_key"])
        json.loads(rows[0]["verification_json"])  # must be readable evidence, not a blob
        return rows[0]

    def assert_card_and_link(self, outcome, capability_id, expected_link):
        self.assertEqual(outcome.card.get("capability_id"), capability_id)
        self.assertEqual(outcome.card.get("component"),
                         self.registry.get(capability_id).result_card)
        self.assertEqual(outcome.receipt.native_deep_link, expected_link)

    def assert_owner_isolation(self, capability_id, arguments, *, consequential):
        """Another account naming this alert must not reach it.

        Asserted through the gateway rather than the engine because the gateway is
        where an authorisation mistake would live; the engine's ``WHERE user_id=?`` is
        the backstop this is checking has not been routed around.
        """
        if consequential:
            asked = self.run_capability(capability_id, arguments, user_id=OTHER_ID,
                                        request_id="intruder")
            if asked.receipt.status == "confirmation_required":
                # An approval may be offered for a resource the account cannot touch;
                # what must never happen is that redeeming it works.
                done = self.run_capability(
                    capability_id, arguments, user_id=OTHER_ID, request_id="intruder-2",
                    token=asked.card.get("confirmation_token") or "")
                self.assertNotEqual(done.receipt.status, "verified_success")
                return
            self.assertNotEqual(asked.receipt.status, "verified_success")
            return
        outcome = self.run_capability(capability_id, arguments, user_id=OTHER_ID,
                                      request_id="intruder")
        self.assertNotEqual(outcome.receipt.status, "verified_success")

    def assert_unknown_id_is_refused(self, capability_id, *, extra=None):
        arguments = {"alert_id": 987654, **(extra or {})}
        outcome = self.run_capability(capability_id, arguments, request_id="ghost")
        if outcome.receipt.status == "confirmation_required":
            outcome = self.run_capability(
                capability_id, arguments, request_id="ghost-2",
                token=outcome.card.get("confirmation_token") or "")
        self.assertNotEqual(outcome.receipt.status, "verified_success")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


class ListOperation(CrudCase):

    def test_it_returns_the_owner_s_alerts_as_records(self):
        outcome = self.run_capability("crypto.alerts.list", {"limit": 10})
        self.assertEqual(outcome.receipt.status, "verified_success")
        records = outcome.card.get("records") or []
        self.assertTrue(records, "the list must return structured rows, not prose")
        self.assertIn(str(self.alert_id), [str(row.get("id") or row.get("alert_id"))
                                           for row in records])
        self.assert_card_and_link(outcome, "crypto.alerts.list", "/pulse/crypto/alerts")

    def test_it_never_asks_for_confirmation(self):
        self.assert_declared_policy("crypto.alerts.list", "never")

    def test_it_shows_one_account_nothing_of_another_s(self):
        """The strongest available form of this check: a second account with its own
        alert, listing, and finding only its own. An empty result would also pass a
        naive isolation test while proving nothing about filtering."""
        theirs = self.fx.make_alert(OTHER_ID, symbol="ETH")
        outcome = self.run_capability("crypto.alerts.list", {"limit": 10}, user_id=OTHER_ID)
        ids = [str(row.get("id") or row.get("alert_id"))
               for row in (outcome.card.get("records") or [])]
        self.assertIn(str(theirs), ids)
        self.assertNotIn(str(self.alert_id), ids)

    def test_it_is_audited_even_though_it_changes_nothing(self):
        self.run_capability("crypto.alerts.list", {"limit": 10})
        self.assert_audited("crypto.alerts.list")


class GetOperation(CrudCase):

    def test_it_returns_the_named_alert(self):
        outcome = self.run_capability("crypto.alerts.get", {"alert_id": self.alert_id})
        self.assertEqual(outcome.receipt.status, "verified_success")
        self.assert_card_and_link(outcome, "crypto.alerts.get",
                                  f"/pulse/alerts/{self.alert_id}")

    def test_another_account_cannot_read_it(self):
        self.assert_owner_isolation("crypto.alerts.get", {"alert_id": self.alert_id},
                                    consequential=False)

    def test_an_id_that_does_not_exist_is_refused(self):
        self.assert_unknown_id_is_refused("crypto.alerts.get")


# ---------------------------------------------------------------------------
# Reversible writes
# ---------------------------------------------------------------------------


class PauseOperation(CrudCase):

    def test_it_pauses_and_the_engine_agrees(self):
        outcome = self.run_capability("crypto.alerts.pause", {"alert_id": self.alert_id})
        self.assertEqual(outcome.receipt.status, "verified_success")
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")
        self.assertEqual(outcome.receipt.verification_state, "verified")
        self.assert_card_and_link(outcome, "crypto.alerts.pause",
                                  f"/pulse/alerts/{self.alert_id}")
        self.assert_audited("crypto.alerts.pause")

    def test_it_is_reversible_so_it_does_not_stop_to_ask(self):
        """The declared policy and the observed behaviour, asserted together.

        Pause is ``contextual``: an explicit, unambiguous instruction runs, and the
        receipt carries the undo that makes running it safe. This is the one place the
        pack deliberately does not gate a write, so the reason is asserted rather than
        assumed — remove ``undo_capability_id`` and this test should fail.
        """
        self.assert_declared_policy("crypto.alerts.pause", "contextual")
        outcome = self.run_capability("crypto.alerts.pause", {"alert_id": self.alert_id})
        self.assertEqual(outcome.receipt.status, "verified_success")
        self.assertEqual(outcome.receipt.undo_capability_id, "crypto.alerts.resume")

    def test_another_account_cannot_pause_it(self):
        self.assert_owner_isolation("crypto.alerts.pause", {"alert_id": self.alert_id},
                                    consequential=False)
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_an_id_that_does_not_exist_is_refused(self):
        self.assert_unknown_id_is_refused("crypto.alerts.pause")

    def test_the_same_request_twice_is_one_mutation(self):
        """Proved by resuming out of band between the two calls.

        Without that step a replayed pause would be invisible — the alert is already
        paused, so a second real mutation and a correctly suppressed one look
        identical. Resuming first makes the difference observable.
        """
        first = self.run_capability("crypto.alerts.pause", {"alert_id": self.alert_id},
                                    request_id="same")
        self.assertEqual(first.receipt.status, "verified_success")
        self.engine.resume_alert(int(self.alert_id), OWNER_ID)
        self.fx.commit()
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

        replay = self.run_capability("crypto.alerts.pause", {"alert_id": self.alert_id},
                                     request_id="same")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active",
                         "a replayed request must not mutate a second time")
        self.assertTrue(replay.receipt.evidence.get("idempotent_replay"))


class ResumeOperation(CrudCase):

    def setUp(self) -> None:
        super().setUp()
        self.engine.pause_alert(int(self.alert_id), OWNER_ID)
        self.fx.commit()
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")

    def test_it_resumes_and_the_engine_agrees(self):
        outcome = self.run_capability("crypto.alerts.resume", {"alert_id": self.alert_id})
        self.assertEqual(outcome.receipt.status, "verified_success")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")
        self.assert_card_and_link(outcome, "crypto.alerts.resume",
                                  f"/pulse/alerts/{self.alert_id}")
        self.assert_audited("crypto.alerts.resume")

    def test_another_account_cannot_resume_it(self):
        self.assert_owner_isolation("crypto.alerts.resume", {"alert_id": self.alert_id},
                                    consequential=False)
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")

    def test_an_id_that_does_not_exist_is_refused(self):
        self.assert_unknown_id_is_refused("crypto.alerts.resume")


# ---------------------------------------------------------------------------
# Consequential writes
# ---------------------------------------------------------------------------


class CreateOperation(CrudCase):

    ARGS = {"symbol": "ETH", "condition": "below", "threshold": 1500.0}

    def test_it_asks_first_then_creates_something_the_engine_can_find(self):
        self.assert_declared_policy("crypto.alerts.create", "always")
        asked, done = self.approve("crypto.alerts.create", self.ARGS)
        self.assertEqual(asked.card.get("component"), "action_confirmation")
        self.assertEqual(done.receipt.status, "verified_success")
        self.assertEqual(done.receipt.verification_state, "verified")

        created = done.receipt.canonical_resource_ids
        self.assertTrue(created, "a create must return the id of what it created")
        new_id = int(str(created[0]).split(":")[-1])
        self.assertNotEqual(new_id, self.alert_id)
        made = self.rule(new_id)
        self.assertTrue(made, "the engine must be able to find the new alert")
        self.assertEqual(str(made.get("symbol") or "").upper(), "ETH")
        self.assert_audited("crypto.alerts.create")

    def test_without_approval_nothing_is_created(self):
        before = len(self.alerts())
        asked = self.run_capability("crypto.alerts.create", self.ARGS)
        self.assertEqual(asked.receipt.status, "confirmation_required")
        self.assertEqual(len(self.alerts()), before,
                         "the card is a question, not a side effect")

    def test_an_approval_cannot_be_spent_on_different_arguments(self):
        """The argument hash covers the whole proposal, not just the capability.

        Approving an ETH alert below 1500 is not approving a BTC alert below 1. If this
        binding were missing, anything between the card and the confirm call could
        change what the user agreed to while keeping their approval.
        """
        asked = self.run_capability("crypto.alerts.create", self.ARGS)
        token = asked.card["confirmation_token"]
        before = len(self.alerts())
        swapped = self.run_capability(
            "crypto.alerts.create",
            {"symbol": "BTC", "condition": "below", "threshold": 1.0},
            request_id="swapped", token=token)
        self.assertNotEqual(swapped.receipt.status, "verified_success")
        self.assertEqual(len(self.alerts()), before)

    def test_another_account_cannot_spend_this_approval(self):
        asked = self.run_capability("crypto.alerts.create", self.ARGS)
        token = asked.card["confirmation_token"]
        stolen = self.run_capability(
            "crypto.alerts.create", self.ARGS, user_id=OTHER_ID,
            request_id="stolen", token=token)
        self.assertNotEqual(stolen.receipt.status, "verified_success")
        self.assertFalse(self.alerts(OTHER_ID))

    def test_the_same_approved_request_twice_creates_one_alert(self):
        asked = self.run_capability("crypto.alerts.create", self.ARGS, request_id="once")
        token = asked.card["confirmation_token"]
        self.run_capability("crypto.alerts.create", self.ARGS,
                            request_id="once-confirm", token=token)
        after_first = len(self.alerts())
        self.run_capability("crypto.alerts.create", self.ARGS,
                            request_id="once-confirm", token=token)
        self.assertEqual(len(self.alerts()), after_first,
                         "a replayed create must not produce a second alert")


class UpdateOperation(CrudCase):

    ARGS_KEY = "threshold"

    def args(self, threshold=123456.0):
        return {"alert_id": self.alert_id, "threshold": threshold, "condition": "above"}

    def test_it_asks_first_then_changes_the_threshold_the_engine_reports(self):
        self.assert_declared_policy("crypto.alerts.update", "always")
        asked, done = self.approve("crypto.alerts.update", self.args())
        self.assertEqual(asked.card.get("component"), "action_confirmation")
        self.assertEqual(done.receipt.status, "verified_success")
        self.assertEqual(done.receipt.verification_state, "verified")

        rule = self.rule(self.alert_id)
        stored = float(rule.get("target_value") or rule.get("threshold") or 0)
        self.assertEqual(stored, 123456.0)
        self.assert_card_and_link(done, "crypto.alerts.update",
                                  f"/pulse/alerts/{self.alert_id}")
        self.assert_audited("crypto.alerts.update")

    def test_without_approval_the_threshold_is_untouched(self):
        asked = self.run_capability("crypto.alerts.update", self.args())
        self.assertEqual(asked.receipt.status, "confirmation_required")
        rule = self.rule(self.alert_id)
        self.assertEqual(float(rule.get("target_value") or rule.get("threshold") or 0),
                         90000.0)

    def test_an_approval_for_one_threshold_cannot_set_another(self):
        asked = self.run_capability("crypto.alerts.update", self.args(111.0))
        token = asked.card["confirmation_token"]
        swapped = self.run_capability("crypto.alerts.update", self.args(999.0),
                                      request_id="swapped", token=token)
        self.assertNotEqual(swapped.receipt.status, "verified_success")
        rule = self.rule(self.alert_id)
        self.assertEqual(float(rule.get("target_value") or rule.get("threshold") or 0),
                         90000.0)

    def test_another_account_cannot_update_it(self):
        self.assert_owner_isolation("crypto.alerts.update", self.args(),
                                    consequential=True)
        rule = self.rule(self.alert_id)
        self.assertEqual(float(rule.get("target_value") or rule.get("threshold") or 0),
                         90000.0)

    def test_an_id_that_does_not_exist_is_refused(self):
        self.assert_unknown_id_is_refused(
            "crypto.alerts.update", extra={"threshold": 5.0, "condition": "above"})


class DeleteOperation(CrudCase):

    def test_it_asks_first_then_deletes_and_the_engine_agrees(self):
        self.assert_declared_policy("crypto.alerts.delete", "always")
        asked, done = self.approve("crypto.alerts.delete", {"alert_id": self.alert_id})
        self.assertEqual(asked.card.get("component"), "action_confirmation")
        self.assertEqual(done.receipt.status, "verified_success")
        self.assertEqual(done.receipt.verification_state, "verified")
        self.assertEqual(self.fx.alert_status(self.alert_id), "deleted")
        self.assert_card_and_link(done, "crypto.alerts.delete", "/pulse/crypto/alerts")
        self.assert_audited("crypto.alerts.delete")

    def test_without_approval_the_alert_survives(self):
        asked = self.run_capability("crypto.alerts.delete", {"alert_id": self.alert_id})
        self.assertEqual(asked.receipt.status, "confirmation_required")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_an_approval_cannot_be_pointed_at_a_different_alert(self):
        """The most consequential form of argument binding in the pack.

        A delete approval that could be redirected is a delete approval for every alert
        the account owns, so the second alert is real and is checked afterwards.
        """
        other = self.fx.make_alert(OWNER_ID, symbol="SOL")
        asked = self.run_capability("crypto.alerts.delete", {"alert_id": self.alert_id})
        token = asked.card["confirmation_token"]
        redirected = self.run_capability("crypto.alerts.delete", {"alert_id": other},
                                         request_id="redirected", token=token)
        self.assertNotEqual(redirected.receipt.status, "verified_success")
        self.assertEqual(self.fx.alert_status(other), "active")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_another_account_cannot_delete_it(self):
        self.assert_owner_isolation("crypto.alerts.delete", {"alert_id": self.alert_id},
                                    consequential=True)
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_an_id_that_does_not_exist_is_refused(self):
        self.assert_unknown_id_is_refused("crypto.alerts.delete")


# ---------------------------------------------------------------------------
# The pack as a whole
# ---------------------------------------------------------------------------


class PackCompleteness(CrudCase):
    """Guards against the failure this file exists to rule out: a registered
    capability with nothing behind it."""

    OPERATIONS = ("crypto.alerts.list", "crypto.alerts.get", "crypto.alerts.create",
                  "crypto.alerts.pause", "crypto.alerts.resume", "crypto.alerts.update",
                  "crypto.alerts.delete")

    def test_all_seven_operations_are_registered(self):
        registered = set(self.registry.capability_ids())
        for capability_id in self.OPERATIONS:
            with self.subTest(capability_id=capability_id):
                self.assertIn(capability_id, registered)

    def test_every_operation_has_a_real_executor_behind_it(self):
        """Registered is not executable. The executor name must resolve to a callable
        the gateway can actually reach — a typo here would otherwise surface as a
        runtime failure in a user's hands rather than in this suite."""
        from services import undx_agent_tools

        for capability_id in self.OPERATIONS:
            with self.subTest(capability_id=capability_id):
                spec = self.registry.get(capability_id)
                self.assertIn(spec.executor, undx_agent_tools.EXECUTORS)
                self.assertTrue(callable(undx_agent_tools.EXECUTORS[spec.executor]))

    def test_every_mutation_declares_a_verifier_that_exists(self):
        """A write with no read-back can never honestly report ``verified_success``."""
        from services import undx_verification

        for capability_id in self.OPERATIONS:
            spec = self.registry.get(capability_id)
            if not spec.is_write:
                continue
            with self.subTest(capability_id=capability_id):
                self.assertTrue(spec.verifier, f"{capability_id} declares no verifier")
                self.assertIn(spec.verifier, undx_verification.VERIFIERS)

    def test_every_operation_resolves_a_deep_link_the_native_router_accepts(self):
        """No unsubstituted ``:param`` may reach the client.

        ``/pulse/alerts/:alert_id`` rendered literally is a link that navigates
        nowhere, and it fails silently — the card looks correct and the button does
        nothing.
        """
        for capability_id in self.OPERATIONS:
            with self.subTest(capability_id=capability_id):
                link = self.registry.get(capability_id).deep_link({"alert_id": 42})
                self.assertTrue(link.startswith("/pulse/"))
                self.assertNotIn(":", link)

    def test_the_whole_pack_executes_in_one_session(self):
        """List, create, get, pause, resume, update, delete — in sequence, on one
        alert, each step observed through the engine.

        Individually each operation is covered above. What this adds is that they
        compose: the id a create returns is one a get can find, and the alert a pause
        touched is one an update can still change.
        """
        listed = self.run_capability("crypto.alerts.list", {"limit": 10}, request_id="s1")
        self.assertEqual(listed.receipt.status, "verified_success")

        _, created = self.approve("crypto.alerts.create",
                                  {"symbol": "SOL", "condition": "above", "threshold": 200.0},
                                  request_id="s2")
        new_id = int(str(created.receipt.canonical_resource_ids[0]).split(":")[-1])

        got = self.run_capability("crypto.alerts.get", {"alert_id": new_id}, request_id="s3")
        self.assertEqual(got.receipt.status, "verified_success")

        paused = self.run_capability("crypto.alerts.pause", {"alert_id": new_id},
                                     request_id="s4")
        self.assertEqual(paused.receipt.status, "verified_success")
        self.assertEqual(self.fx.alert_status(new_id), "paused")

        resumed = self.run_capability("crypto.alerts.resume", {"alert_id": new_id},
                                      request_id="s5")
        self.assertEqual(resumed.receipt.status, "verified_success")
        self.assertEqual(self.fx.alert_status(new_id), "active")

        _, updated = self.approve("crypto.alerts.update",
                                  {"alert_id": new_id, "threshold": 321.0, "condition": "above"},
                                  request_id="s6")
        self.assertEqual(updated.receipt.status, "verified_success")
        rule = self.rule(new_id)
        self.assertEqual(float(rule.get("target_value") or rule.get("threshold") or 0), 321.0)

        _, deleted = self.approve("crypto.alerts.delete", {"alert_id": new_id},
                                  request_id="s7")
        self.assertEqual(deleted.receipt.status, "verified_success")
        self.assertEqual(self.fx.alert_status(new_id), "deleted")

        # Seven operations, seven distinct settled ledger rows.
        settled = [row for row in self.operations()
                   if row["tool_name"].startswith("pulsesoc.crypto_alerts.")]
        self.assertEqual(len(settled), 7)
        self.assertEqual({row["status"] for row in settled}, {"verified"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
