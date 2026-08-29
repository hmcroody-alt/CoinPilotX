"""Executable evidence for the six capabilities added by the action surface expansion.

The expansion added ``feed.posts.hide``, ``messages.mark_read``, ``messages.send``,
``business.campaign.pause``, ``business.campaign.resume`` and
``business.profile.update``. Each one has to be wired through five files that do not
import each other in a single direction — registry, executor table, verifier table,
production tool registry, argument resolution — and the characteristic failure of that
shape is a capability that looks registered, passes every unit test of its own service,
and then raises ``tool_not_registered`` deep inside the gateway at runtime because one
of the five was missed. So the first class here checks the contract itself rather than
any behaviour: it is the test that would have caught the omission.

The behavioural classes then check the two properties the mission brief names as
non-negotiable and that a service-level test cannot see: that UNDX never guesses which
row a write lands on, and that a write which did not happen is never reported as one.
"""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from services import undx_agent_tools  # noqa: E402
from services import undx_verification  # noqa: E402
from services.undx_capability_registry import (  # noqa: E402
    REGISTRY,
    unregistered_tool_names,
)
from services.undx_policy import PRODUCTION_TOOL_REGISTRY  # noqa: E402
from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


#: The capabilities this mission added. Written out rather than derived from the
#: registry: a list computed from the thing under test would shrink silently the day
#: a capability stopped being registered, and pass while doing it.
NEW_CAPABILITIES = (
    "feed.posts.hide",
    "messages.mark_read",
    "messages.send",
    "business.campaign.pause",
    "business.campaign.resume",
    "business.profile.update",
)

#: The subset that cannot be undone by another capability, and therefore must ask
#: every time regardless of how the target was resolved.
ALWAYS_CONFIRM = ("messages.send", "business.profile.update")


class ExpansionWiringContract(unittest.TestCase):
    """The five-file contract, checked as a contract."""

    def test_every_new_capability_is_registered(self) -> None:
        for capability_id in NEW_CAPABILITIES:
            with self.subTest(capability_id=capability_id):
                self.assertIn(capability_id, REGISTRY)

    def test_every_new_capability_has_an_executor(self) -> None:
        for capability_id in NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            with self.subTest(capability_id=capability_id):
                self.assertIn(spec.executor, undx_agent_tools.EXECUTORS)

    def test_every_new_capability_has_a_verifier(self) -> None:
        for capability_id in NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            with self.subTest(capability_id=capability_id):
                self.assertTrue(spec.verifier, f"{capability_id} declares no verifier")
                self.assertIn(spec.verifier, undx_verification.VERIFIERS)

    def test_every_new_tool_name_reaches_the_production_registry(self) -> None:
        """The specific miss that produces ``tool_not_registered`` at runtime.

        A capability absent from ``PRODUCTION_TOOL_REGISTRY`` is not refused at the
        edge — it falls through the gateway to the language model, which then answers
        in prose about an action it did not take. That is the exact shape of "fake
        success" the brief forbids, and it is invisible to every test of the service
        underneath.
        """
        for capability_id in NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            with self.subTest(capability_id=capability_id):
                self.assertIn(spec.tool_name, PRODUCTION_TOOL_REGISTRY)

    def test_no_capability_anywhere_is_missing_from_the_production_registry(self) -> None:
        """Not scoped to the new six on purpose. The failure is a gap in a mapping, and
        a test that only looked at this mission's rows would not notice the next one."""
        self.assertEqual(unregistered_tool_names(), [])

    def test_verification_route_lives_outside_the_writing_module(self) -> None:
        """A write and its read-back must not be the same code path.

        If the verifier calls back into the module that just performed the write, a
        write that silently did nothing verifies against the writer's own opinion of
        what it did. Every new entry names a read in a different module.
        """
        for capability_id in NEW_CAPABILITIES:
            entry = PRODUCTION_TOOL_REGISTRY[REGISTRY[capability_id].tool_name]
            with self.subTest(capability_id=capability_id):
                verification_route = entry.get("verification_route") or ""
                self.assertTrue(verification_route,
                                f"{capability_id} declares no verification route")
                self.assertNotEqual(verification_route, entry.get("route"))

    def test_unrecoverable_writes_confirm_unconditionally(self) -> None:
        """Sending a message and replacing profile text cannot be walked back by
        another capability, so neither may rely on contextual confirmation — which is
        skippable whenever the person named the target themselves."""
        for capability_id in ALWAYS_CONFIRM:
            spec = REGISTRY[capability_id]
            with self.subTest(capability_id=capability_id):
                self.assertEqual(getattr(spec.confirmation, "value", spec.confirmation),
                                 "always")

    def test_reversible_campaign_writes_undo_each_other(self) -> None:
        """Pause and resume are each other's undo, and the mapping has to carry the
        campaign across or the undo would arrive without a target."""
        pause = REGISTRY["business.campaign.pause"]
        resume = REGISTRY["business.campaign.resume"]
        self.assertEqual(pause.undo_capability_id, "business.campaign.resume")
        self.assertEqual(resume.undo_capability_id, "business.campaign.pause")
        for spec in (pause, resume):
            self.assertEqual(dict(spec.undo_argument_map), {"campaign_id": "campaign_id"})

    def test_send_is_not_marked_idempotent(self) -> None:
        """Everything else added here converges on a state; a send accumulates. A retry
        of ``messages.send`` writes a second message, so the flag that authorizes silent
        retries must be off."""
        self.assertFalse(REGISTRY["messages.send"].idempotent)

    def test_business_profile_field_allowlists_agree(self) -> None:
        """The registry enum and the executor allowlist are two hand-written copies of
        one list, kept in step by this test rather than by an import.

        They cannot import each other — the registry is loaded before the tool module —
        so drift is possible in both directions, and both directions are bad: a field
        in the enum but not the allowlist is a capability that offers a change it will
        then refuse, and a field in the allowlist but not the enum is a write the
        registry never described.
        """
        field = next(item for item in REGISTRY["business.profile.update"].fields
                     if item.name == "field")
        self.assertEqual(tuple(field.choices),
                         undx_agent_tools.BUSINESS_PROFILE_WRITABLE_FIELDS)

    def test_no_new_write_leaves_its_changed_field_unverified(self) -> None:
        """``CapabilitySpec`` enforces this at import, so this test is a statement of
        intent as much as a check: it names the property, so a later change that
        weakened the constructor would fail here with a readable reason."""
        for capability_id in NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            mutable = {item.name for item in spec.fields} - {spec.target_field}
            with self.subTest(capability_id=capability_id):
                self.assertFalse(mutable - set(spec.verified_fields))


class ExpansionNeverGuessesTheTarget(unittest.TestCase):
    """Natural language resolves a target or asks. It does not pick one."""

    def setUp(self) -> None:
        from services.undx_agent_runtime import resolve_arguments

        self.resolve = resolve_arguments

    def run_turn(self, capability_id: str, text: str):
        return self.resolve(OWNER_ID, REGISTRY[capability_id], text, {})

    def test_named_conversation_resolves(self) -> None:
        result = self.run_turn("messages.send",
                               "send a message to conversation 12 saying running late")
        self.assertEqual(result.arguments.get("conversation_id"), 12)
        self.assertEqual(result.arguments.get("body"), "running late")

    def test_unnamed_conversation_asks_rather_than_choosing_the_latest(self) -> None:
        """There is no messaging equivalent of ``resolve_recent_post`` on this path,
        deliberately. The most recent thread is a plausible guess, and a send is the
        one write in this pack where a plausible guess reaches another person."""
        result = self.run_turn("messages.send", "send a message saying hello")
        self.assertNotIn("conversation_id", result.arguments)
        self.assertIsNotNone(result.unresolved)
        self.assertFalse(result.agent_chose_target)

    def test_mark_read_also_declines_to_guess(self) -> None:
        result = self.run_turn("messages.mark_read", "mark my messages as read")
        self.assertNotIn("conversation_id", result.arguments)
        self.assertIsNotNone(result.unresolved)

    def test_hide_resolves_a_named_post(self) -> None:
        result = self.run_turn("feed.posts.hide", "hide post 2245 from my home feed")
        self.assertEqual(result.arguments.get("post_id"), 2245)

    def test_hide_never_falls_back_to_the_callers_own_recent_post(self) -> None:
        """``feed.posts.hide`` is kept out of the shared post_id branch on purpose.

        That branch falls through to ``resolve_recent_post``, which returns the
        caller's *own* most recent post — and ``hide_post`` refuses own posts with a
        400. The fallback would therefore turn every vague "hide that post" into a
        guaranteed rejection against a row the person never named.
        """
        with patch("services.undx_agent_runtime.resolve_recent_post") as recent:
            result = self.run_turn("feed.posts.hide", "hide that post")
        recent.assert_not_called()
        self.assertNotIn("post_id", result.arguments)
        self.assertFalse(result.agent_chose_target)

    def test_a_single_campaign_name_match_is_marked_as_agent_chosen(self) -> None:
        """Resolving a campaign by name is a proposal, not an instruction, so the
        policy has to see that the runtime picked the target. Step 6a upgrades exactly
        this case to a confirmation even though pause is contextually confirmed."""
        campaigns = [{"campaign_id": "c0ffee", "name": "Summer Campaign"},
                     {"campaign_id": "beaded", "name": "Winter Teaser"}]
        with patch("services.business_os.advertising.service.list_campaigns_for_owner",
                   return_value=campaigns):
            result = self.run_turn("business.campaign.pause",
                                   "pause the summer campaign")
        self.assertEqual(result.arguments.get("campaign_id"), "c0ffee")
        self.assertTrue(result.agent_chose_target)

    def test_an_ambiguous_campaign_name_resolves_to_nothing(self) -> None:
        campaigns = [{"campaign_id": "c0ffee", "name": "Summer Campaign"},
                     {"campaign_id": "beaded", "name": "Summer Campaign v2"}]
        with patch("services.business_os.advertising.service.list_campaigns_for_owner",
                   return_value=campaigns):
            result = self.run_turn("business.campaign.pause",
                                   "pause the summer campaign")
        self.assertNotIn("campaign_id", result.arguments)
        self.assertFalse(result.agent_chose_target)


class ExpansionRefusalsAreRefusals(unittest.TestCase):
    """A write that did not happen never returns ``ok``."""

    def test_send_refuses_an_empty_body(self) -> None:
        result = undx_agent_tools.messages_send(
            OWNER_ID, {"conversation_id": 1, "body": "   "})
        self.assertFalse(result.ok)

    def test_send_never_reaches_the_writer_without_membership(self) -> None:
        """The specific hazard: ``comm_v2.send_message`` passes ``join_public=True``,
        so for a public room the caller is not in the service does not refuse — it
        adds them as a participant and then sends. The send would therefore change
        who is in a conversation, which is not what the confirmation card described.
        The membership pre-check has to stop it before ``send_message`` is entered,
        because once entered the join has already happened."""
        with patch("services.messenger_intelligence_service.get_conversation_read_state",
                   return_value=None), \
                patch.object(undx_agent_tools, "_comm_v2") as comm:
            result = undx_agent_tools.messages_send(
                OWNER_ID, {"conversation_id": 4242, "body": "hello"})
        comm.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "not_found")

    def test_send_refuses_identically_for_foreign_and_missing_conversations(self) -> None:
        """Both read as ``None`` from the membership-scoped query, so the refusal
        cannot be used to learn which conversation ids exist."""
        codes = set()
        for conversation_id in (4242, 4243):
            with patch("services.messenger_intelligence_service.get_conversation_read_state",
                       return_value=None):
                result = undx_agent_tools.messages_send(
                    OWNER_ID, {"conversation_id": conversation_id, "body": "hello"})
            codes.add((result.error_code, result.error_message))
        self.assertEqual(len(codes), 1)

    def test_send_reaches_the_writer_when_membership_is_real(self) -> None:
        """The guard refuses non-members; it must not refuse members."""
        state = {"conversation_id": 12, "unread_count": 0,
                 "last_read_message_id": 0, "last_read_at": ""}
        comm = SimpleNamespace(
            send_message=lambda *_a, **_k: {"ok": True, "message_id": 99})
        with patch("services.messenger_intelligence_service.get_conversation_read_state",
                   return_value=state), \
                patch.object(undx_agent_tools, "_comm_v2", return_value=comm):
            result = undx_agent_tools.messages_send(
                OWNER_ID, {"conversation_id": 12, "body": "running late"})
        self.assertTrue(result.ok)
        self.assertEqual(result.canonical_resource_id, "message:99")

    def test_profile_update_refuses_a_field_outside_the_allowlist(self) -> None:
        """The allowlist omits contact details, visibility and public location. Those
        are not writable by an agent at any confirmation level, so the refusal happens
        before the service is reached at all."""
        with patch("services.business_os.profile.api.update_profile") as writer:
            result = undx_agent_tools.business_profile_update(
                OWNER_ID, {"field": "phone_number", "value": "555"})
        writer.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "field_not_writable")

    def test_a_field_held_for_review_is_a_failure_not_a_success(self) -> None:
        """``update_profile`` returns 200 for a field it queued for verification
        review. Reporting that as done would claim an outcome the account cannot see."""
        envelope = (200, {"ok": True, "saved": {}, "rejected": {},
                          "queued_for_review": ["about"]})
        with patch("services.business_os.profile.api.update_profile",
                   return_value=envelope):
            result = undx_agent_tools.business_profile_update(
                OWNER_ID, {"field": "about", "value": "We fix bikes."})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "queued_for_review")

    def test_a_rejected_field_is_a_failure(self) -> None:
        envelope = (200, {"ok": True, "saved": {}, "rejected": {"about": "Too long."},
                          "queued_for_review": []})
        with patch("services.business_os.profile.api.update_profile",
                   return_value=envelope):
            result = undx_agent_tools.business_profile_update(
                OWNER_ID, {"field": "about", "value": "x" * 5000})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "field_rejected")

    def test_an_unchanged_value_is_a_success_that_says_it_changed_nothing(self) -> None:
        """The service skips a no-op write, so the field lands in none of the three
        buckets. The requested state does hold, so this is a success — but one that
        reports ``changed`` false rather than implying an edit that never happened."""
        envelope = (200, {"ok": True, "saved": {}, "rejected": {},
                          "queued_for_review": []})
        with patch("services.business_os.profile.api.update_profile",
                   return_value=envelope):
            result = undx_agent_tools.business_profile_update(
                OWNER_ID, {"field": "about", "value": "unchanged"})
        self.assertTrue(result.ok)
        self.assertFalse(result.data["changed"])

    def test_a_campaign_the_caller_does_not_own_never_reaches_the_writer(self) -> None:
        """``pause_campaign`` reads ``campaign.get("advertiser_user_id")`` with no
        ``None`` guard, so a foreign campaign raises ``AttributeError`` and surfaces as
        a 500 instead of the 404 the module documents. The executor pre-checks through
        the ownership-enforcing view so the refusal is clean — and, more importantly,
        so the write is never attempted."""
        from services.business_os.advertising.service import AdvertisingError

        with patch("services.business_os.advertising.operations.get_operational_view",
                   side_effect=AdvertisingError("Campaign not found.", 404, "not_found")), \
                patch("services.business_os.advertising.operations.pause_campaign") as writer:
            result = undx_agent_tools.business_campaign_pause(
                OWNER_ID, {"campaign_id": "someone-elses"})
        writer.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "not_found")


class ExpansionVerifiersGoAndLook(unittest.TestCase):
    """Verification reads the world back. It does not read the result."""

    def test_read_state_verifier_reports_pending_when_membership_is_gone(self) -> None:
        """``None`` from the read-back means the membership row is unreadable, which is
        not the same claim as "the write failed". Collapsing the two would let a lost
        membership be reported as a failed mark-read, and a failed mark-read as a lost
        membership."""
        from services.undx_agent_contracts import ToolResult, VerificationState

        result = ToolResult(ok=True, tool_name="pulsesoc.messages.mark_read",
                            capability_id="messages.mark_read",
                            canonical_resource_id="conversation:5",
                            data={"conversation_id": 5, "unread_count": 0},
                            latency_ms=1)
        with patch("services.messenger_intelligence_service.get_conversation_read_state",
                   return_value=None):
            verified = undx_verification.verify(
                "conversation_read_state", OWNER_ID, {"conversation_id": 5}, result)
        self.assertEqual(verified.state, VerificationState.PENDING)

    def test_message_verifier_requires_the_row_to_belong_to_the_sender(self) -> None:
        """The verifier takes the message id from the result — the narrowest concession
        in the pack, because only the writer knows which row was created — but it does
        not take the result's word for anything else. A row whose sender is somebody
        else does not verify a send by this account."""
        from services.undx_agent_contracts import ToolResult, VerificationState

        result = ToolResult(ok=True, tool_name="pulsesoc.messages.send",
                            capability_id="messages.send",
                            canonical_resource_id="message:99",
                            data={"message_id": 99, "body": "hello"}, latency_ms=1)
        foreign = [{"message_id": 99, "sender_user_id": OTHER_ID, "body": "hello"}]
        with patch("services.messenger_intelligence_service.list_conversation_messages",
                   return_value=foreign):
            verified = undx_verification.verify(
                "message_exists", OWNER_ID,
                {"conversation_id": 5, "body": "hello"}, result)
        self.assertNotEqual(verified.state, VerificationState.VERIFIED)

    def test_campaign_verifier_takes_its_expectation_from_the_capability(self) -> None:
        """Not from the arguments. An argument can disagree with the capability that
        actually ran, and then a resume that quietly did nothing would verify happily
        against a paused campaign."""
        from services.undx_agent_contracts import ToolResult, VerificationState

        result = ToolResult(ok=True, tool_name="pulsesoc.business.campaign.resume",
                            capability_id="business.campaign.resume",
                            canonical_resource_id="campaign:c0ffee",
                            data={"campaign_id": "c0ffee"}, latency_ms=1)
        view = {"operational_status": "paused", "funding_status": "funded",
                "delivering": False}
        with patch("services.business_os.advertising.operations.get_operational_view",
                   return_value=view):
            verified = undx_verification.verify(
                "campaign_operational_status", OWNER_ID,
                {"campaign_id": "c0ffee"}, result)
        self.assertEqual(verified.state, VerificationState.FAILED)
        self.assertEqual(verified.expected, "active")

    def test_campaign_evidence_keeps_delivery_and_money_apart(self) -> None:
        """"Paused" invites the reading that money stopped moving. It did not: this
        slice authorizes future delivery and touches neither funding nor review, so the
        evidence carries all three states side by side rather than one that could be
        mistaken for the others."""
        from services.undx_agent_contracts import ToolResult

        result = ToolResult(ok=True, tool_name="pulsesoc.business.campaign.pause",
                            capability_id="business.campaign.pause",
                            canonical_resource_id="campaign:c0ffee",
                            data={"campaign_id": "c0ffee"}, latency_ms=1)
        view = {"operational_status": "paused", "funding_status": "funded",
                "delivering": False}
        with patch("services.business_os.advertising.operations.get_operational_view",
                   return_value=view):
            verified = undx_verification.verify(
                "campaign_operational_status", OWNER_ID,
                {"campaign_id": "c0ffee"}, result)
        read_back = verified.evidence["read_back"]
        self.assertEqual(read_back["funding_status"], "funded")
        self.assertIn("delivering", read_back)


class FeedHideWritePack(unittest.TestCase):
    """The one new capability whose whole stack runs against a real database here."""

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        self.fx.ensure_feed_schema()
        self.foreign_post = self.fx.make_post(OTHER_ID, body="Someone else's post.")
        self.own_post = self.fx.make_post(OWNER_ID, body="My own post.")

    def tearDown(self) -> None:
        self.fx.stop()

    def hidden_rows(self, post_id: int, user_id: int = OWNER_ID) -> int:
        """Counted from the table, not from the service. The service function is the
        one the verifier calls, so asserting against it would let one read vouch for
        itself."""
        self.fx.cur.execute(
            "SELECT COUNT(*) AS total FROM pulse_post_hides WHERE user_id=? AND post_id=?",
            (int(user_id), int(post_id)),
        )
        row = self.fx.cur.fetchone()
        return int(dict(row)["total"] if hasattr(row, "keys") else row[0])

    def test_hiding_another_persons_post_lands(self) -> None:
        result = undx_agent_tools.feed_post_hide(
            OWNER_ID, {"post_id": self.foreign_post})
        self.fx.commit()
        self.assertTrue(result.ok, result)
        self.assertEqual(self.hidden_rows(self.foreign_post), 1)

    def test_hiding_is_idempotent(self) -> None:
        undx_agent_tools.feed_post_hide(OWNER_ID, {"post_id": self.foreign_post})
        undx_agent_tools.feed_post_hide(OWNER_ID, {"post_id": self.foreign_post})
        self.fx.commit()
        self.assertEqual(self.hidden_rows(self.foreign_post), 1)

    def test_hiding_is_viewer_scoped_and_never_global(self) -> None:
        """Hiding is a preference held by the person who cannot stand to see the post.
        It must not remove anything from anyone else's feed, which is what makes it a
        REVERSIBLE_WRITE rather than a moderation action."""
        undx_agent_tools.feed_post_hide(OWNER_ID, {"post_id": self.foreign_post})
        self.fx.commit()
        self.assertEqual(self.hidden_rows(self.foreign_post, OTHER_ID), 0)
        self.fx.cur.execute(
            "SELECT deleted_at, status FROM pulse_posts WHERE id=?", (self.foreign_post,))
        row = dict(self.fx.cur.fetchone())
        self.assertIsNone(row["deleted_at"])
        self.assertEqual(row["status"], "published")

    def test_hiding_your_own_post_is_a_clean_rejection(self) -> None:
        result = undx_agent_tools.feed_post_hide(OWNER_ID, {"post_id": self.own_post})
        self.fx.commit()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "write_rejected")
        self.assertEqual(self.hidden_rows(self.own_post), 0)

    def test_a_missing_post_is_not_found_rather_than_rejected(self) -> None:
        result = undx_agent_tools.feed_post_hide(OWNER_ID, {"post_id": 999999})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "not_found")

    def test_the_verifier_reads_the_hide_back_independently(self) -> None:
        from services.undx_agent_contracts import VerificationState

        result = undx_agent_tools.feed_post_hide(
            OWNER_ID, {"post_id": self.foreign_post})
        self.fx.commit()
        verified = undx_verification.verify(
            "feed_post_hidden_value", OWNER_ID,
            {"post_id": self.foreign_post}, result)
        self.assertEqual(verified.state, VerificationState.VERIFIED)


if __name__ == "__main__":
    unittest.main()
