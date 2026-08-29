"""Executable evidence for the thirteen capabilities added by the service API completion.

The mission this suite belongs to had one rule above the others: *do not build a
second backend inside UNDX*. So the thing that has to be proved here is not "the
agent can block someone" — a test that only showed that would pass just as happily
against a private copy of the block logic living inside ``undx_agent_tools``. What
has to be proved is that the agent reaches the *same* function the web app reaches,
that the function refuses the same people, and that a refusal is reported as a
refusal rather than as a quiet success.

Three kinds of test do that, and they are deliberately different in shape:

*Wiring.* Five files have to agree — registry, executor table, verifier table,
production tool registry, knowledge map — and none of them imports the others, so
drift is silent. A capability missing from ``PRODUCTION_TOOL_REGISTRY`` is not
refused at the edge; it falls through the gateway to the language model, which then
writes prose about an action nobody took. The first class checks the contract
itself, so the test that fails is the one that names the missing row.

*Authority.* Every mutation is driven against a real SQLite database with the real
owner-scoped SQL, from three accounts: the owner, another participant, and an
outsider. A mocked service would confirm that UNDX "deleted" a Reel belonging to a
stranger, because a mock has no ``WHERE user_id=?``. The isolation being claimed is
a property of the SQL, so the SQL has to be present.

*Honesty after the fact.* Idempotency and audit. A second block must converge rather
than error, a second delete must be a no-op rather than a 404, and every completed
mutation must leave exactly one row in ``pulse_mutation_audit`` that says who did
what to which target, from which surface — with no message bodies in it.

Assertions read from the service or straight from the table, never from the
gateway's own receipt. A receipt that agreed with itself would prove nothing.
"""

from __future__ import annotations

import os
import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from services import undx_agent_tools  # noqa: E402
from services import undx_verification  # noqa: E402
from services.undx_agent_contracts import ToolResult, VerificationState  # noqa: E402
from services.undx_capability_registry import (  # noqa: E402
    REGISTRY,
    unregistered_tool_names,
)
from services.undx_policy import PRODUCTION_TOOL_REGISTRY  # noqa: E402
from tests.undx_agent.harness import (  # noqa: E402
    AgentFixture,
    OTHER_ID,
    OUTSIDER_ID,
    OWNER_ID,
)


#: The capabilities this mission added. Written out rather than derived from the
#: registry: a list computed from the thing under test would shrink silently the day
#: a capability stopped being registered, and pass while doing it.
NEW_CAPABILITIES = (
    "profile.block",
    "profile.unblock",
    "profile.bio.update",
    "reels.delete",
    "reels.comment.create",
    "reels.comment.update",
    "reels.comment.delete",
    "feed.report",
    "marketplace.listing.create",
    "marketplace.listing.update",
    "marketplace.listing.pause",
    "marketplace.listing.resume",
    "marketplace.listing.delete",
)

#: The subset the brief names as consequential: content that disappears, text
#: published under the account's name, a moderation case filed against somebody, a
#: commercial offer withdrawn. None of these is undone by another capability in the
#: pack, so none may rely on contextual confirmation — which is skippable whenever
#: the person named the target themselves.
ALWAYS_CONFIRM = (
    "profile.bio.update",
    "reels.delete",
    "reels.comment.create",
    "reels.comment.update",
    "reels.comment.delete",
    "feed.report",
    "marketplace.listing.create",
    "marketplace.listing.delete",
)


# ---------------------------------------------------------------------------
# Fixture extensions
# ---------------------------------------------------------------------------
#
# The shared harness builds the feed tables and a users table holding only what the
# alert engine and the feed's author join read. The consumer mutation services read
# a few more columns and three more tables. They are added here rather than in the
# harness so that a suite which does not exercise these services keeps the narrower
# fixture: the harness note about not inventing columns the engine never reads is
# the reason it is that narrow, and widening it globally would erode that.

#: Columns ``pulse_profile_service`` reads and writes. ``profile_snapshot`` reads
#: every one of them to build the before/after pair the audit trail records.
_PROFILE_COLUMNS = {
    "bio": "TEXT",
    "cover_url": "TEXT",
    "banner_url": "TEXT",
    "profile_visibility": "TEXT DEFAULT 'public'",
    "updated_at": "TEXT",
}

#: ``_comment_authority`` selects ``edited_at``; ``update_comment`` writes both.
_COMMENT_COLUMNS = {"edited_at": "TEXT", "updated_at": "TEXT"}

#: The harness models ``blocked_users`` as the join it reads — two ids, nothing more.
#: ``bot.init_db`` (bot.py:109839) declares the full row, and ``_read_state`` selects
#: ``reason`` and ``created_at`` from it. Widening here rather than in the harness for
#: the usual reason, but this one carries a second lesson: the service's own
#: ``_ensure_blocked_users`` is a ``CREATE TABLE IF NOT EXISTS``, which is silent when
#: the table exists in a narrower shape. That is exactly the shape an older deployment
#: would be in, and the failure there is the same ``OperationalError`` this fixture
#: first produced. Recorded as a finding rather than patched from a test.
_BLOCK_COLUMNS = {"reason": "TEXT", "created_at": "TEXT"}


def _add_columns(fixture, table: str, columns: dict) -> None:
    for name, declaration in columns.items():
        try:
            fixture.cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        except Exception:
            # Already present. SQLite has no ADD COLUMN IF NOT EXISTS, and probing
            # pragma output first would be the same branch written longer.
            pass


def ensure_completion_schema(fixture) -> None:
    """Everything the thirteen operations touch, on top of the shared harness."""
    fixture.ensure_feed_schema()
    _add_columns(fixture, "users", _PROFILE_COLUMNS)
    _add_columns(fixture, "pulse_comments", _COMMENT_COLUMNS)
    _add_columns(fixture, "blocked_users", _BLOCK_COLUMNS)
    # Production declares this pair UNIQUE. ALTER cannot add a constraint, so the
    # index carries it. Deliberately stricter than ``block_user`` needs — that
    # function converges by reading before it writes — so if the read-before-write
    # ever went away this fixture would raise rather than quietly hold two rows.
    fixture.cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_blocked_users_pair "
        "ON blocked_users (blocker_user_id, blocked_user_id)"
    )
    # A Reel is two rows: this one and the ``pulse_posts`` row it renders from.
    # Both are needed or ``delete_owned_reel`` cannot demonstrate that it moves them
    # together, which is the whole point of that function.
    fixture.cur.execute(
        """CREATE TABLE IF NOT EXISTS pulse_reels (
            id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id INTEGER,
            caption TEXT, category TEXT, status TEXT DEFAULT 'active',
            moderation_status TEXT DEFAULT 'approved',
            share_count INTEGER DEFAULT 0, replay_count INTEGER DEFAULT 0,
            completion_rate REAL DEFAULT 0, created_at TEXT, updated_at TEXT)"""
    )
    fixture.cur.execute(
        """CREATE TABLE IF NOT EXISTS pulse_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_user_id INTEGER,
            target_type TEXT, target_id INTEGER, reason TEXT,
            status TEXT DEFAULT 'open', created_at TEXT, updated_at TEXT)"""
    )
    # Present on purpose. ``_write_comm_v2_block`` is a no-op when this table is
    # absent, so a fixture without it would let the "union" half of the canonical
    # block quietly not happen and still pass every assertion about blocked_users.
    fixture.cur.execute(
        """CREATE TABLE IF NOT EXISTS comm_v2_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blocker_user_id INTEGER, blocked_user_id INTEGER, reason TEXT,
            status TEXT, created_at TEXT, updated_at TEXT,
            UNIQUE(blocker_user_id, blocked_user_id))"""
    )
    fixture.commit()


def make_reel(fixture, user_id: int = OWNER_ID, *, caption: str = "Behind the scenes",
              visibility: str = "public") -> tuple:
    """Insert one visible Reel and return ``(reel_id, post_id)``."""
    post_id = fixture.make_post(user_id, body=caption, visibility=visibility)
    fixture.cur.execute(
        """INSERT INTO pulse_reels
           (post_id, user_id, caption, category, status, moderation_status, created_at)
           VALUES (?,?,?, 'general', 'active', 'approved', '2026-08-01T00:00:00')""",
        (post_id, int(user_id), caption),
    )
    reel_id = int(fixture.cur.lastrowid or 0)
    assert reel_id, "fixture could not create a Reel"
    fixture.commit()
    return reel_id, post_id


def make_comment(fixture, post_id: int, author_id: int, body: str = "Nice one.") -> int:
    """Insert one comment directly.

    Not through ``add_comment``: that function runs moderation, attaches media and
    fans out notifications, none of which is under test here, and all of which would
    have to be stubbed to seed a row. Seeding by INSERT keeps the fixture honest
    about what it is — a starting state, not an exercise of the create path. The
    create path has its own tests below.
    """
    fixture.cur.execute(
        """INSERT INTO pulse_comments
           (post_id, user_id, body, moderation_status, created_at)
           VALUES (?,?,?, 'approved', '2026-08-02T00:00:00')""",
        (int(post_id), int(author_id), body),
    )
    comment_id = int(fixture.cur.lastrowid or 0)
    assert comment_id, "fixture could not create a comment"
    fixture.commit()
    return comment_id


def audit_rows(fixture, operation: str, target_id) -> list:
    """Rows from ``pulse_mutation_audit``, read straight from the table."""
    try:
        fixture.cur.execute(
            "SELECT * FROM pulse_mutation_audit WHERE operation=? AND target_id=? "
            "ORDER BY id",
            (operation, str(target_id)),
        )
    except Exception:
        return []
    return [dict(row) for row in fixture.cur.fetchall()]


@contextmanager
def recording_safety_events():
    """Stand in for ``bot.pulse_emit_comms_safety_event`` and record the calls.

    ``_emit_safety_event`` reaches the notification helper through ``import bot``,
    which pulls in the whole Flask monolith — payment SDKs included — and is wrapped
    in a bare ``except`` so a failed import is indistinguishable from a suppressed
    notification. Both halves of that are a problem for a test: the import is far too
    heavy, and swallowing it would let "the safety event never fired" pass silently.
    A stub module makes the call observable, which is what the block decision
    requires evidence for.
    """
    calls: list = []
    module = types.ModuleType("bot")
    module.pulse_emit_comms_safety_event = (
        lambda *args, **kwargs: calls.append({"args": args, "kwargs": kwargs})
    )
    previous = sys.modules.get("bot")
    sys.modules["bot"] = module
    try:
        yield calls
    finally:
        if previous is None:
            sys.modules.pop("bot", None)
        else:
            sys.modules["bot"] = previous


class CompletionCase(unittest.TestCase):
    """Base: one isolated database per test, with the completion schema on it."""

    flags: dict = {}

    def setUp(self) -> None:
        self.fix = AgentFixture(**self.flags).start()
        self.addCleanup(self.fix.stop)
        ensure_completion_schema(self.fix)


# ---------------------------------------------------------------------------
# 1. Wiring
# ---------------------------------------------------------------------------


class CompletionWiringContract(unittest.TestCase):
    """The five-file contract, checked as a contract rather than as behaviour."""

    def test_every_capability_is_registered(self) -> None:
        for capability_id in NEW_CAPABILITIES:
            with self.subTest(capability_id=capability_id):
                self.assertIn(capability_id, REGISTRY)

    def test_every_capability_has_an_executor(self) -> None:
        for capability_id in NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            with self.subTest(capability_id=capability_id):
                self.assertIn(spec.executor, undx_agent_tools.EXECUTORS)

    def test_every_capability_has_a_verifier(self) -> None:
        for capability_id in NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            with self.subTest(capability_id=capability_id):
                self.assertTrue(spec.verifier, f"{capability_id} declares no verifier")
                self.assertIn(spec.verifier, undx_verification.VERIFIERS)

    def test_every_tool_name_reaches_the_production_registry(self) -> None:
        """The specific miss that produces ``tool_not_registered`` at runtime."""
        for capability_id in NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            with self.subTest(capability_id=capability_id):
                self.assertIn(spec.tool_name, PRODUCTION_TOOL_REGISTRY)

    def test_no_capability_anywhere_is_missing_from_the_production_registry(self) -> None:
        """Not scoped to these thirteen on purpose: the failure is a gap in a mapping,
        and a test that only looked at this mission's rows would not notice the next
        one."""
        self.assertEqual(unregistered_tool_names(), [])

    def test_every_route_names_a_shared_service_not_a_route_handler(self) -> None:
        """The mission's central claim, written down as an assertion.

        ``route`` here names a Python function that the HTTP layer also calls. If one
        of these ever pointed at a ``bot`` handler or an ``/api/`` path, UNDX would be
        going somewhere the web app does not, and the single-authority property would
        be gone without any test noticing.
        """
        for capability_id in NEW_CAPABILITIES:
            entry = PRODUCTION_TOOL_REGISTRY[REGISTRY[capability_id].tool_name]
            with self.subTest(capability_id=capability_id):
                route = str(entry.get("route") or "")
                self.assertTrue(route.startswith("services."),
                                f"{capability_id} route {route!r} is not a service function")
                self.assertNotIn("/", route)
                # ``method`` is None because these execute in process. A plausible HTTP
                # verb here reads as a promise that a route exists in bot.py.
                self.assertIsNone(entry.get("method"))

    def test_verification_route_lives_outside_the_writing_call(self) -> None:
        """A write and its read-back must not be the same call.

        If verification re-enters the function that just performed the write, a write
        that silently did nothing verifies against the writer's own opinion of what it
        did.
        """
        for capability_id in NEW_CAPABILITIES:
            entry = PRODUCTION_TOOL_REGISTRY[REGISTRY[capability_id].tool_name]
            with self.subTest(capability_id=capability_id):
                verification_route = entry.get("verification_route") or ""
                self.assertTrue(verification_route,
                                f"{capability_id} declares no verification route")
                self.assertNotEqual(verification_route, entry.get("route"))

    def test_consequential_writes_confirm_unconditionally(self) -> None:
        for capability_id in ALWAYS_CONFIRM:
            spec = REGISTRY[capability_id]
            with self.subTest(capability_id=capability_id):
                self.assertEqual(
                    getattr(spec.confirmation, "value", spec.confirmation), "always")

    def test_the_registry_and_the_policy_agree_about_confirmation(self) -> None:
        """Two hand-written statements of one fact. The registry decides whether the
        card is shown; the policy row is what an operator reads when auditing what the
        agent may do unattended. A capability that confirms in one and not the other
        is a capability whose documentation lies about it."""
        for capability_id in NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            entry = PRODUCTION_TOOL_REGISTRY[spec.tool_name]
            always = getattr(spec.confirmation, "value", spec.confirmation) == "always"
            with self.subTest(capability_id=capability_id):
                self.assertEqual(bool(entry.get("confirmation")), always)

    def test_no_write_leaves_its_changed_field_unverified(self) -> None:
        """``CapabilitySpec`` enforces this at import, so this is a statement of intent
        as much as a check: it names the property, so a later change that weakened the
        constructor fails here with a readable reason."""
        for capability_id in NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            mutable = {item.name for item in spec.fields} - {spec.target_field}
            with self.subTest(capability_id=capability_id):
                self.assertFalse(mutable - set(spec.verified_fields))

    def test_block_and_unblock_are_each_others_undo(self) -> None:
        """Asserted through ``undo_arguments`` rather than against the declared map,
        because the two specs reach the same place by different routes: they take one
        field and their undo takes the same field, so the empty map's pass-through is
        correct and declaring the identity mapping would be noise. What has to hold is
        that the reversing call arrives carrying the same target, however that is
        spelled."""
        block, unblock = REGISTRY["profile.block"], REGISTRY["profile.unblock"]
        self.assertEqual(block.undo_capability_id, "profile.unblock")
        self.assertEqual(unblock.undo_capability_id, "profile.block")
        for spec in (block, unblock):
            with self.subTest(capability_id=spec.capability_id):
                self.assertEqual(spec.undo_arguments(arguments={"target_user_id": 77}),
                                 {"target_user_id": 77})

    def test_pause_and_resume_are_each_others_undo(self) -> None:
        pause, resume = (REGISTRY["marketplace.listing.pause"],
                         REGISTRY["marketplace.listing.resume"])
        self.assertEqual(pause.undo_capability_id, "marketplace.listing.resume")
        self.assertEqual(resume.undo_capability_id, "marketplace.listing.pause")
        for spec in (pause, resume):
            with self.subTest(capability_id=spec.capability_id):
                self.assertEqual(spec.undo_arguments(arguments={"listing_id": "mktp_x"}),
                                 {"listing_id": "mktp_x"})

    def test_creates_are_not_marked_idempotent(self) -> None:
        """Everything else here converges on a state; a create accumulates. A retried
        comment writes a second comment and a retried listing a second listing, so the
        flag that authorizes silent retries must be off on both."""
        for capability_id in ("reels.comment.create", "marketplace.listing.create"):
            with self.subTest(capability_id=capability_id):
                self.assertFalse(REGISTRY[capability_id].idempotent)


class CompletionVocabulariesAgree(unittest.TestCase):
    """Hand-copied lists, kept in step by tests rather than by an import.

    The registry is loaded before the service modules, so it cannot import their
    vocabularies, and drift is possible in both directions. Both directions are bad: a
    value the registry offers and the service refuses is a capability that promises
    something it will then reject, and a value the service accepts but the registry
    never described is a write nothing documented.
    """

    def test_report_content_types_match_the_service(self) -> None:
        from services.pulse_feed_engine import REPORT_TARGET_TYPES

        field = next(item for item in REGISTRY["feed.report"].fields
                     if item.name == "content_type")
        self.assertEqual(set(field.choices), set(REPORT_TARGET_TYPES))

    def test_marketplace_updatable_fields_are_a_subset_the_service_allows(self) -> None:
        """The service allows ``currency`` as well. The capability deliberately does
        not offer it: changing the currency of a listing that already has offers
        against it reprices it silently, and there is no confirmation card wording that
        makes that safe. So this asserts containment, not equality — but it asserts
        containment, which is what catches a field the service would refuse."""
        import inspect

        from services.business_os.marketplace import service as marketplace

        allowed = {
            "title", "description", "price_cents", "currency",
            "fulfillment_type", "inventory_qty",
        }
        source = inspect.getsource(marketplace.update_product)
        for name in allowed:
            self.assertIn(f'"{name}"', source,
                          f"the service allowlist no longer mentions {name}")
        field = next(item for item in REGISTRY["marketplace.listing.update"].fields
                     if item.name == "field")
        self.assertTrue(set(field.choices) <= allowed,
                        f"capability offers fields the service refuses: "
                        f"{sorted(set(field.choices) - allowed)}")

    def test_marketplace_lifecycle_verbs_exist_in_the_service(self) -> None:
        """``marketplace.listing.delete`` maps to ``archive``. There is no hard delete
        in the product and this pack did not invent one — orders reference products,
        and a row that vanishes from under a buyer's receipt is a support incident."""
        from services.business_os.marketplace.service import PRODUCT_ACTIONS

        for action in ("pause", "resume", "archive"):
            self.assertIn(action, PRODUCT_ACTIONS)
        self.assertEqual(PRODUCT_ACTIONS["archive"], "archived")

    def test_the_bio_ceiling_matches_the_service(self) -> None:
        """The capability declares a maximum and the service clips to one. If the
        capability's were the larger, a bio inside the declared limit would be stored
        truncated and then read back as a verification failure against text the person
        never asked to shorten."""
        from services.pulse_profile_service import BIO_MAX

        field = next(item for item in REGISTRY["profile.bio.update"].fields
                     if item.name == "bio")
        self.assertLessEqual(int(field.max_length or 0), BIO_MAX)

    def test_the_listing_title_ceiling_matches_the_service(self) -> None:
        from services.business_os.marketplace.service import TITLE_MAX

        field = next(item for item in REGISTRY["marketplace.listing.create"].fields
                     if item.name == "title")
        self.assertLessEqual(int(field.max_length or 0), TITLE_MAX)


# ---------------------------------------------------------------------------
# 2. Block and unblock
# ---------------------------------------------------------------------------


class BlockIsOneCanonicalOperation(CompletionCase):
    """The block decision, written down.

    Blocking used to mean three different things depending on which screen it was
    pressed from: Settings wrote one row, the feed wrote a row *and filed a moderation
    report*, Messages emitted a notification and filed nothing. The canonical service
    resolves that as the union minus the auto-report — both tables, always the safety
    event, never a report the person did not ask to file. These tests are what stops a
    later change from quietly restoring one of the old three.
    """

    def test_a_block_writes_both_tables(self) -> None:
        from services import pulse_social_graph_service as graph

        with recording_safety_events():
            graph.block_user(OWNER_ID, OTHER_ID, surface="settings")
        self.fix.cur.execute(
            "SELECT reason FROM blocked_users WHERE blocker_user_id=? AND blocked_user_id=?",
            (OWNER_ID, OTHER_ID))
        self.assertIsNotNone(self.fix.cur.fetchone())
        self.fix.cur.execute(
            "SELECT status FROM comm_v2_blocks WHERE blocker_user_id=? AND blocked_user_id=?",
            (OWNER_ID, OTHER_ID))
        row = self.fix.cur.fetchone()
        self.assertIsNotNone(row, "the messaging mirror was not written")
        self.assertEqual(dict(row)["status"], "active")

    def test_a_block_always_emits_the_safety_event(self) -> None:
        from services import pulse_social_graph_service as graph

        with recording_safety_events() as calls:
            graph.block_user(OWNER_ID, OTHER_ID, surface="undx")
        self.assertEqual(len(calls), 1)
        self.assertIn("user_blocked", calls[0]["args"])

    def test_a_block_never_files_a_moderation_report(self) -> None:
        """The half of the old feed-screen behaviour that was deliberately dropped.

        Filing a report is an accusation that reaches a moderator and can end an
        account. Someone muting a stranger has not asked to make one, and an agent
        acting on "block them" certainly has not.
        """
        from services import pulse_social_graph_service as graph

        with recording_safety_events():
            graph.block_user(OWNER_ID, OTHER_ID, surface="undx")
        self.fix.cur.execute("SELECT COUNT(*) AS n FROM pulse_reports")
        self.assertEqual(int(dict(self.fix.cur.fetchone())["n"]), 0)

    def test_blocking_twice_converges_rather_than_erroring(self) -> None:
        from services import pulse_social_graph_service as graph

        with recording_safety_events():
            first = graph.block_user(OWNER_ID, OTHER_ID, surface="undx")
            second = graph.block_user(OWNER_ID, OTHER_ID, surface="undx")
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertTrue(second["ok"])
        self.fix.cur.execute(
            "SELECT COUNT(*) AS n FROM blocked_users WHERE blocker_user_id=? AND blocked_user_id=?",
            (OWNER_ID, OTHER_ID))
        self.assertEqual(int(dict(self.fix.cur.fetchone())["n"]), 1)

    def test_a_repeat_block_does_not_move_the_original_date(self) -> None:
        """A moderator reading the trail needs the date the block was first placed. A
        toggle that retries must not rewrite it into the present."""
        from services import pulse_social_graph_service as graph

        with recording_safety_events():
            graph.block_user(OWNER_ID, OTHER_ID)
            self.fix.cur.execute(
                "SELECT created_at FROM blocked_users WHERE blocker_user_id=? AND blocked_user_id=?",
                (OWNER_ID, OTHER_ID))
            original = dict(self.fix.cur.fetchone())["created_at"]
            graph.block_user(OWNER_ID, OTHER_ID, reason="changed my mind")
        self.fix.cur.execute(
            "SELECT created_at FROM blocked_users WHERE blocker_user_id=? AND blocked_user_id=?",
            (OWNER_ID, OTHER_ID))
        self.assertEqual(dict(self.fix.cur.fetchone())["created_at"], original)

    def test_unblocking_someone_who_is_not_blocked_is_a_terminal_success(self) -> None:
        """Not a 404. A 404 here would also be an oracle: it would distinguish "not
        blocked" from "no such row", which are the same fact to this caller."""
        from services import pulse_social_graph_service as graph

        with recording_safety_events():
            outcome = graph.unblock_user(OWNER_ID, OTHER_ID, surface="undx")
        self.assertTrue(outcome["ok"])
        self.assertFalse(outcome["changed"])

    def test_a_block_cannot_be_placed_on_behalf_of_someone_else(self) -> None:
        """The requester is the row's ``blocker_user_id``, taken from the session and
        never from an argument. The service has no parameter that would let it be
        anyone else — this test is what notices if one is ever added."""
        import inspect

        from services import pulse_social_graph_service as graph

        signature = inspect.signature(graph.block_user)
        self.assertEqual(list(signature.parameters)[:2],
                         ["requester_user_id", "target_user_id"])
        for forbidden in ("on_behalf_of", "as_user", "actor_user_id", "blocker_user_id"):
            self.assertNotIn(forbidden, signature.parameters)

    def test_blocking_yourself_is_refused(self) -> None:
        from services import pulse_social_graph_service as graph

        with self.assertRaises(graph.SocialGraphError) as caught:
            graph.block_user(OWNER_ID, OWNER_ID)
        self.assertEqual(caught.exception.code, "self_target")

    def test_blocking_an_account_that_does_not_exist_is_refused(self) -> None:
        from services import pulse_social_graph_service as graph

        with self.assertRaises(graph.SocialGraphError) as caught:
            graph.block_user(OWNER_ID, 999_999)
        self.assertEqual(caught.exception.http_status, 404)

    def test_one_persons_block_does_not_block_the_other_direction(self) -> None:
        """``blocked_users`` is directed. If the read were undirected, unblocking would
        remove a block somebody else placed."""
        from services import pulse_social_graph_service as graph

        with recording_safety_events():
            graph.block_user(OWNER_ID, OTHER_ID)
        self.assertTrue(graph.block_state(OWNER_ID, OTHER_ID)["blocked"])
        self.assertFalse(graph.block_state(OTHER_ID, OWNER_ID)["blocked"])

    def test_an_unblock_cannot_lift_a_block_somebody_else_placed(self) -> None:
        from services import pulse_social_graph_service as graph

        with recording_safety_events():
            graph.block_user(OTHER_ID, OUTSIDER_ID)
            graph.unblock_user(OWNER_ID, OUTSIDER_ID)
        self.assertTrue(graph.block_state(OTHER_ID, OUTSIDER_ID)["blocked"])

    def test_the_audit_row_records_actor_target_surface_and_transition(self) -> None:
        from services import pulse_social_graph_service as graph

        with recording_safety_events():
            graph.block_user(OWNER_ID, OTHER_ID, surface="undx")
        rows = audit_rows(self.fix, "social_graph.block", OTHER_ID)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(int(row["actor_user_id"]), OWNER_ID)
        self.assertEqual(row["target_type"], "user")
        self.assertEqual(row["actor_surface"], "undx")
        self.assertEqual(row["outcome"], "applied")
        self.assertIn('"blocked": false', row["before_json"])
        self.assertIn('"blocked": true', row["after_json"])
        self.assertTrue(row["correlation_id"])
        self.assertTrue(row["created_at"])

    def test_a_repeat_block_is_audited_as_already_blocked(self) -> None:
        """The trail has to distinguish "this act changed something" from "this act
        found the state already correct", or a reviewer counting blocks counts
        retries."""
        from services import pulse_social_graph_service as graph

        with recording_safety_events():
            graph.block_user(OWNER_ID, OTHER_ID, surface="undx")
            graph.block_user(OWNER_ID, OTHER_ID, surface="undx")
        outcomes = [row["outcome"] for row in audit_rows(self.fix, "social_graph.block", OTHER_ID)]
        self.assertEqual(outcomes, ["applied", "already_blocked"])

    def test_undx_reaches_the_same_service_the_web_app_does(self) -> None:
        """The single-authority property, at the only place it can be observed: the
        executor is asked to block, and the canonical service function is what runs."""
        from services import pulse_social_graph_service as graph

        with patch.object(graph, "block_user", wraps=graph.block_user) as canonical, \
                recording_safety_events():
            result = undx_agent_tools.profile_block(
                OWNER_ID, {"target_user_id": OTHER_ID})
        canonical.assert_called_once()
        self.assertEqual(canonical.call_args.args[:2], (OWNER_ID, OTHER_ID))
        self.assertEqual(canonical.call_args.kwargs.get("surface"), "undx")
        self.assertTrue(result.ok)
        self.assertTrue(graph.block_state(OWNER_ID, OTHER_ID)["blocked"])

    def test_the_executor_carries_a_service_refusal_through_unchanged(self) -> None:
        """The service already decided why the write was refused and phrased it for a
        person. Re-deriving either here would let the two drift."""
        result = undx_agent_tools.profile_block(OWNER_ID, {"target_user_id": OWNER_ID})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "self_target")


# ---------------------------------------------------------------------------
# 3. Bio
# ---------------------------------------------------------------------------


class BioIsSelfOnly(CompletionCase):

    def test_the_bio_service_has_no_target_parameter_at_all(self) -> None:
        """The strongest form of "you cannot edit someone else's bio": there is no
        argument that would express it. Authorization by absence, checked so that a
        later convenience parameter does not slip past review."""
        import inspect

        from services import pulse_profile_service

        signature = inspect.signature(pulse_profile_service.update_profile_bio)
        self.assertEqual(list(signature.parameters)[:2], ["requester_user_id", "bio"])
        for forbidden in ("user_id", "target_user_id", "on_behalf_of", "as_user"):
            self.assertNotIn(forbidden, signature.parameters)

    def test_a_bio_write_lands_only_on_the_caller(self) -> None:
        from services import pulse_profile_service

        pulse_profile_service.update_profile_bio(OWNER_ID, "Building things.", surface="undx")
        self.assertEqual(
            pulse_profile_service.profile_state(OWNER_ID).get("bio"), "Building things.")
        self.assertEqual(pulse_profile_service.profile_state(OTHER_ID).get("bio"), "")

    def test_an_unchanged_bio_reports_that_nothing_moved(self) -> None:
        """A confirmation card that says "your bio was updated" when nothing moved is a
        false receipt."""
        from services import pulse_profile_service

        pulse_profile_service.update_profile_bio(OWNER_ID, "Same text.")
        again = pulse_profile_service.update_profile_bio(OWNER_ID, "Same text.")
        self.assertTrue(again["ok"])
        self.assertFalse(again["changed"])

    def test_a_bio_write_does_not_disturb_the_display_name(self) -> None:
        """The reason the named wrapper exists. Routing a bio edit through the general
        updater with every other field spelled out invites someone to eventually pass a
        sixth by accident, carrying a stale display name back over one the person
        changed elsewhere."""
        from services import pulse_profile_service

        self.fix.cur.execute("UPDATE users SET display_name=? WHERE user_id=?",
                             ("Roody", OWNER_ID))
        self.fix.commit()
        pulse_profile_service.update_profile_bio(OWNER_ID, "New bio.")
        self.assertEqual(
            pulse_profile_service.profile_state(OWNER_ID).get("display_name"), "Roody")

    def test_the_capability_cannot_clear_a_bio(self) -> None:
        """``FieldSpec.coerce`` refuses empty text for a string field, so "delete my
        bio" is not expressible through this capability. Recorded as a known limitation
        rather than worked around: an empty string reaching ``update_profile`` would be
        a real write, and the gap belongs in the report, not hidden behind a special
        case."""
        spec = REGISTRY["profile.bio.update"]
        field = next(item for item in spec.fields if item.name == "bio")
        with self.assertRaises(Exception):
            field.coerce("   ")

    def test_undx_reaches_the_same_service_the_web_app_does(self) -> None:
        from services import pulse_profile_service

        with patch.object(pulse_profile_service, "update_profile_bio",
                          wraps=pulse_profile_service.update_profile_bio) as canonical:
            result = undx_agent_tools.profile_bio_update(OWNER_ID, {"bio": "Shipping."})
        canonical.assert_called_once()
        self.assertEqual(canonical.call_args.args[0], OWNER_ID)
        self.assertTrue(result.ok)
        self.assertEqual(
            pulse_profile_service.profile_state(OWNER_ID).get("bio"), "Shipping.")

    def test_the_bio_is_clipped_by_the_service_not_stored_long(self) -> None:
        from services import pulse_profile_service

        pulse_profile_service.update_profile_bio(OWNER_ID, "x" * 900)
        stored = pulse_profile_service.profile_state(OWNER_ID).get("bio") or ""
        self.assertEqual(len(stored), pulse_profile_service.BIO_MAX)


# ---------------------------------------------------------------------------
# 4. Reel deletion
# ---------------------------------------------------------------------------


class ReelDeletionIsOwnerOnly(CompletionCase):

    def setUp(self) -> None:
        super().setUp()
        self.reel_id, self.post_id = make_reel(self.fix, OWNER_ID)

    def _reel_status(self) -> str:
        self.fix.cur.execute("SELECT status FROM pulse_reels WHERE id=?", (self.reel_id,))
        return str(dict(self.fix.cur.fetchone())["status"])

    def _post_status(self) -> str:
        self.fix.cur.execute("SELECT status FROM pulse_posts WHERE id=?", (self.post_id,))
        return str(dict(self.fix.cur.fetchone())["status"])

    def test_the_owner_can_delete_and_both_rows_move(self) -> None:
        """Deleting only ``pulse_reels`` leaves the post in Home: the Reel disappears
        from Reels and reappears on the profile grid, which reads as "delete didn't
        work" to the person who pressed it."""
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.delete_owned_reel(OWNER_ID, self.reel_id, surface="undx")
        self.assertTrue(outcome["ok"])
        self.assertEqual(self._reel_status(), "deleted")
        self.assertEqual(self._post_status(), "deleted")

    def test_a_stranger_cannot_delete_it_and_nothing_moves(self) -> None:
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.delete_owned_reel(OUTSIDER_ID, self.reel_id)
        self.assertFalse(outcome["ok"])
        self.assertEqual(self._reel_status(), "active")
        self.assertEqual(self._post_status(), "published")

    def test_a_foreign_reel_and_a_missing_reel_refuse_identically(self) -> None:
        """Both read as ``not_found``. If they differed, the refusal would be an oracle
        for which Reel ids exist."""
        from services import pulse_feed_engine

        foreign = pulse_feed_engine.delete_owned_reel(OUTSIDER_ID, self.reel_id)
        missing = pulse_feed_engine.delete_owned_reel(OUTSIDER_ID, 999_999)
        self.assertEqual((foreign["error"], foreign["message"]),
                         (missing["error"], missing["message"]))

    def test_deleting_twice_is_a_terminal_no_op(self) -> None:
        from services import pulse_feed_engine

        first = pulse_feed_engine.delete_owned_reel(OWNER_ID, self.reel_id)
        second = pulse_feed_engine.delete_owned_reel(OWNER_ID, self.reel_id)
        self.assertTrue(first["changed"])
        self.assertTrue(second["ok"])
        self.assertFalse(second["changed"])
        self.assertTrue(second["deleted"])

    def test_the_audit_trail_separates_the_delete_from_the_retry(self) -> None:
        from services import pulse_feed_engine

        pulse_feed_engine.delete_owned_reel(OWNER_ID, self.reel_id, surface="undx")
        pulse_feed_engine.delete_owned_reel(OWNER_ID, self.reel_id, surface="undx")
        rows = audit_rows(self.fix, "reels.delete", self.reel_id)
        self.assertEqual([row["outcome"] for row in rows], ["applied", "already_deleted"])
        self.assertEqual(int(rows[0]["actor_user_id"]), OWNER_ID)
        self.assertEqual(rows[0]["target_type"], "reel")
        self.assertEqual(rows[0]["actor_surface"], "undx")

    def test_a_refused_delete_writes_no_audit_row(self) -> None:
        """An audit trail that recorded attempts as well as acts would report a
        stranger's failed delete as something that happened to the Reel."""
        from services import pulse_feed_engine

        pulse_feed_engine.delete_owned_reel(OUTSIDER_ID, self.reel_id)
        self.assertEqual(audit_rows(self.fix, "reels.delete", self.reel_id), [])

    def test_undx_cannot_delete_a_reel_the_caller_does_not_own(self) -> None:
        result = undx_agent_tools.reels_delete(OUTSIDER_ID, {"reel_id": self.reel_id})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "not_found")
        self.assertEqual(self._reel_status(), "active")

    def test_undx_reaches_the_same_service_the_web_app_does(self) -> None:
        from services import pulse_feed_engine

        with patch.object(pulse_feed_engine, "delete_owned_reel",
                          wraps=pulse_feed_engine.delete_owned_reel) as canonical:
            result = undx_agent_tools.reels_delete(OWNER_ID, {"reel_id": self.reel_id})
        canonical.assert_called_once()
        self.assertEqual(canonical.call_args.args[:2], (OWNER_ID, self.reel_id))
        self.assertTrue(result.ok)
        self.assertEqual(self._reel_status(), "deleted")


# ---------------------------------------------------------------------------
# 5. Reel comments
# ---------------------------------------------------------------------------


class ReelCommentAuthority(CompletionCase):
    """Edit is author-only; delete is author *or* the owner of the Reel.

    The asymmetry is intentional and pre-existing: a creator may remove a comment from
    their Reel, but may not rewrite what somebody else said on it. Moderation is
    deletion, not authorship. Both halves are pinned here because either one drifting
    is a different kind of harm.
    """

    def setUp(self) -> None:
        super().setUp()
        self.reel_id, self.post_id = make_reel(self.fix, OWNER_ID)
        self.comment_id = make_comment(self.fix, self.post_id, OTHER_ID, "Great work.")

    def _body(self) -> str:
        self.fix.cur.execute("SELECT body FROM pulse_comments WHERE id=?", (self.comment_id,))
        return str(dict(self.fix.cur.fetchone())["body"])

    def _deleted_at(self):
        self.fix.cur.execute("SELECT deleted_at FROM pulse_comments WHERE id=?",
                             (self.comment_id,))
        return dict(self.fix.cur.fetchone())["deleted_at"]

    # -- edit -------------------------------------------------------------

    def test_the_author_can_edit_their_own_comment(self) -> None:
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.update_comment(
            OTHER_ID, self.comment_id, "Great work, seriously.", surface="undx")
        self.assertTrue(outcome["ok"])
        self.assertEqual(self._body(), "Great work, seriously.")

    def test_the_reel_owner_cannot_rewrite_somebody_elses_comment(self) -> None:
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.update_comment(
            OWNER_ID, self.comment_id, "I actually loved it.")
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["error"], "forbidden")
        self.assertEqual(self._body(), "Great work.")

    def test_a_stranger_cannot_edit_it(self) -> None:
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.update_comment(OUTSIDER_ID, self.comment_id, "spam")
        self.assertFalse(outcome["ok"])
        self.assertEqual(self._body(), "Great work.")

    def test_an_empty_edit_is_refused_rather_than_blanking_the_comment(self) -> None:
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.update_comment(OTHER_ID, self.comment_id, "   ")
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["error"], "empty_body")
        self.assertEqual(self._body(), "Great work.")

    def test_editing_a_deleted_comment_is_not_found(self) -> None:
        from services import pulse_feed_engine

        pulse_feed_engine.delete_comment(OTHER_ID, self.comment_id)
        outcome = pulse_feed_engine.update_comment(OTHER_ID, self.comment_id, "back again")
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["error"], "not_found")

    def test_an_identical_edit_reports_that_nothing_changed(self) -> None:
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.update_comment(OTHER_ID, self.comment_id, "Great work.")
        self.assertTrue(outcome["ok"])
        self.assertFalse(outcome["changed"])

    def test_the_edit_audit_row_records_lengths_and_not_the_prose(self) -> None:
        """The trail records that the comment changed and by how much. The text still
        lives in ``pulse_comments``; copying it into the audit table would put user
        speech in a second place nobody is watching."""
        from services import pulse_feed_engine

        pulse_feed_engine.update_comment(
            OTHER_ID, self.comment_id, "A much longer remark entirely.", surface="undx")
        rows = audit_rows(self.fix, "reels.comment.update", self.comment_id)
        self.assertEqual(len(rows), 1)
        self.assertIn("body_length", rows[0]["after_json"])
        self.assertNotIn("A much longer remark", rows[0]["after_json"])
        self.assertNotIn("Great work", rows[0]["before_json"])

    # -- delete -----------------------------------------------------------

    def test_the_author_can_delete_their_own_comment(self) -> None:
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.delete_comment(OTHER_ID, self.comment_id, surface="undx")
        self.assertTrue(outcome["ok"])
        self.assertFalse(outcome["moderated_by_owner"])
        self.assertIsNotNone(self._deleted_at())

    def test_the_reel_owner_may_moderate_a_comment_on_their_reel(self) -> None:
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.delete_comment(OWNER_ID, self.comment_id, surface="undx")
        self.assertTrue(outcome["ok"])
        self.assertTrue(outcome["moderated_by_owner"])
        self.assertIsNotNone(self._deleted_at())

    def test_a_stranger_can_neither_delete_nor_moderate(self) -> None:
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.delete_comment(OUTSIDER_ID, self.comment_id)
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["error"], "forbidden")
        self.assertIsNone(self._deleted_at())

    def test_deleting_twice_is_a_terminal_no_op(self) -> None:
        from services import pulse_feed_engine

        first = pulse_feed_engine.delete_comment(OTHER_ID, self.comment_id)
        second = pulse_feed_engine.delete_comment(OTHER_ID, self.comment_id)
        self.assertTrue(first["changed"])
        self.assertTrue(second["ok"])
        self.assertFalse(second["changed"])

    def test_a_repeat_delete_does_not_move_the_deletion_timestamp(self) -> None:
        from services import pulse_feed_engine

        pulse_feed_engine.delete_comment(OTHER_ID, self.comment_id)
        first_stamp = self._deleted_at()
        pulse_feed_engine.delete_comment(OTHER_ID, self.comment_id)
        self.assertEqual(self._deleted_at(), first_stamp)

    def test_the_audit_trail_distinguishes_withdrawal_from_moderation(self) -> None:
        """"The author took their remark back" and "a creator removed somebody else's"
        are two different acts, and a moderator reviewing the history has to be able to
        tell them apart."""
        from services import pulse_feed_engine

        pulse_feed_engine.delete_comment(OWNER_ID, self.comment_id, surface="undx")
        rows = audit_rows(self.fix, "reels.comment.delete", self.comment_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["actor_user_id"]), OWNER_ID)
        self.assertIn('"moderated_by_owner": true', rows[0]["after_json"])
        self.assertIn(f'"author_user_id": {OTHER_ID}', rows[0]["before_json"])

    # -- create -----------------------------------------------------------

    def test_a_comment_cannot_be_left_on_a_reel_the_caller_cannot_see(self) -> None:
        """The Reel is resolved through the viewer-scoped read, not by joining the
        tables in the executor. That read is what decides whether this account may see
        the Reel at all, and a Reel the person cannot see is one they cannot comment
        on."""
        from services import pulse_feed_engine

        with patch("services.content_graph_intelligence_service.get_reel",
                   return_value=None) as reel_read, \
                patch.object(pulse_feed_engine, "add_comment") as writer:
            result = undx_agent_tools.reels_comment_create(
                OUTSIDER_ID, {"reel_id": self.reel_id, "body": "hello"})
        reel_read.assert_called_once()
        writer.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "not_found")

    def test_a_private_reel_is_invisible_to_a_stranger(self) -> None:
        """Driven against the real read rather than a patch, because the claim is about
        that query's WHERE clause."""
        from services import content_graph_intelligence_service as graph

        private_reel, _ = make_reel(self.fix, OWNER_ID, visibility="private")
        self.assertIsNotNone(graph.get_reel(OWNER_ID, private_reel))
        self.assertIsNone(graph.get_reel(OUTSIDER_ID, private_reel))

    def test_a_moderation_rejection_is_a_failure_not_a_success(self) -> None:
        """``add_comment`` answers 400 for a body moderation blocked. Reporting that as
        posted would claim a comment the Reel does not have."""
        from services import pulse_feed_engine

        with patch("services.content_graph_intelligence_service.get_reel",
                   return_value={"post_id": self.post_id, "creator_id": OWNER_ID}), \
                patch.object(pulse_feed_engine, "add_comment",
                             return_value=({"ok": False, "message": "Needs changes."}, 400)):
            result = undx_agent_tools.reels_comment_create(
                OWNER_ID, {"reel_id": self.reel_id, "body": "..."})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "moderation_rejected")

    def test_undx_reaches_the_same_services_the_web_app_does(self) -> None:
        from services import pulse_feed_engine

        for name, executor, arguments in (
            ("update_comment", undx_agent_tools.reels_comment_update,
             {"comment_id": self.comment_id, "body": "Edited."}),
            ("delete_comment", undx_agent_tools.reels_comment_delete,
             {"comment_id": self.comment_id}),
        ):
            with self.subTest(service=name):
                canonical = getattr(pulse_feed_engine, name)
                with patch.object(pulse_feed_engine, name, wraps=canonical) as spy:
                    executor(OTHER_ID, arguments)
                spy.assert_called_once()
                self.assertEqual(spy.call_args.kwargs.get("surface"), "undx")


# ---------------------------------------------------------------------------
# 6. Reporting
# ---------------------------------------------------------------------------


class ReportingIsScopedToTheReporter(CompletionCase):

    def setUp(self) -> None:
        super().setUp()
        self.post_id = self.fix.make_post(OTHER_ID, body="Questionable claim.")

    def _reports(self) -> list:
        self.fix.cur.execute("SELECT * FROM pulse_reports ORDER BY id")
        return [dict(row) for row in self.fix.cur.fetchall()]

    def test_a_report_files_one_open_case(self) -> None:
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.report_content(
            OWNER_ID, "post", self.post_id, "Misleading.", surface="undx")
        self.assertTrue(outcome["ok"])
        rows = self._reports()
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["reporter_user_id"]), OWNER_ID)
        self.assertEqual(rows[0]["status"], "open")

    def test_reporting_twice_updates_the_open_case_rather_than_filing_a_second(self) -> None:
        """There is no uniqueness constraint on ``pulse_reports``, so pressing Report
        twice — or an agent retrying a timed-out call — used to file two cases about
        one grievance and inflate the moderation queue."""
        from services import pulse_feed_engine

        first = pulse_feed_engine.report_content(OWNER_ID, "post", self.post_id, "Misleading.")
        second = pulse_feed_engine.report_content(OWNER_ID, "post", self.post_id, "Still misleading.")
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(len(self._reports()), 1)
        self.assertEqual(first["report_id"], second["report_id"])

    def test_two_different_reporters_file_two_cases(self) -> None:
        """Idempotency is per reporter. Collapsing across reporters would silently
        discard the second person's complaint."""
        from services import pulse_feed_engine

        pulse_feed_engine.report_content(OWNER_ID, "post", self.post_id, "Misleading.")
        pulse_feed_engine.report_content(OUTSIDER_ID, "post", self.post_id, "Misleading.")
        self.assertEqual(len(self._reports()), 2)

    def test_an_unrecognised_type_is_refused_rather_than_retyped_as_a_post(self) -> None:
        """The defect this replaced: an unknown ``target_type`` was coerced to
        ``"post"``, so a mistyped report against user 91 was filed against *post* 91 —
        a different and innocent piece of content."""
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.report_content(OWNER_ID, "profile", 91, "spam")
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["error"], "invalid_target_type")
        self.assertEqual(self._reports(), [])

    def test_a_target_that_does_not_exist_is_refused(self) -> None:
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.report_content(OWNER_ID, "post", 999_999, "spam")
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["error"], "not_found")

    def test_reporting_yourself_is_refused(self) -> None:
        from services import pulse_feed_engine

        outcome = pulse_feed_engine.report_content(OWNER_ID, "user", OWNER_ID, "spam")
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["error"], "self_target")

    def test_a_report_nudges_an_approved_post_into_review_but_never_softens_a_decision(self) -> None:
        """A report must not be able to move a post a moderator already held or
        rejected back into a weaker state."""
        from services import pulse_feed_engine

        held = self.fix.make_post(OTHER_ID, body="Already held.")
        self.fix.cur.execute("UPDATE pulse_posts SET moderation_status='rejected' WHERE id=?",
                             (held,))
        self.fix.commit()
        pulse_feed_engine.report_content(OWNER_ID, "post", self.post_id, "Misleading.")
        pulse_feed_engine.report_content(OWNER_ID, "post", held, "Misleading.")
        self.fix.cur.execute("SELECT id, moderation_status FROM pulse_posts WHERE id IN (?,?)",
                             (self.post_id, held))
        statuses = {int(dict(r)["id"]): dict(r)["moderation_status"]
                    for r in self.fix.cur.fetchall()}
        self.assertEqual(statuses[self.post_id], "needs_review")
        self.assertEqual(statuses[held], "rejected")

    def test_the_read_back_cannot_see_a_stranger_s_report(self) -> None:
        """Whether strangers have complained about a post is a moderation fact, and not
        the reporter's to know. A read that answered "is this content reported" would
        hand it to anyone."""
        from services import pulse_feed_engine

        pulse_feed_engine.report_content(OUTSIDER_ID, "post", self.post_id, "Misleading.")
        mine = pulse_feed_engine.get_report_state(OWNER_ID, "post", self.post_id)
        theirs = pulse_feed_engine.get_report_state(OUTSIDER_ID, "post", self.post_id)
        self.assertFalse(mine["reported"])
        self.assertTrue(theirs["reported"])

    def test_the_audit_row_names_the_target_type_and_the_reporter(self) -> None:
        from services import pulse_feed_engine

        pulse_feed_engine.report_content(
            OWNER_ID, "post", self.post_id, "Misleading.", surface="undx")
        rows = audit_rows(self.fix, "feed.report", self.post_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["actor_user_id"]), OWNER_ID)
        self.assertEqual(rows[0]["target_type"], "post")
        self.assertEqual(rows[0]["actor_surface"], "undx")

    def test_undx_reaches_the_same_service_the_web_app_does(self) -> None:
        from services import pulse_feed_engine

        with patch.object(pulse_feed_engine, "report_content",
                          wraps=pulse_feed_engine.report_content) as canonical:
            result = undx_agent_tools.feed_report(
                OWNER_ID,
                {"content_type": "post", "content_id": self.post_id, "reason": "Misleading."})
        canonical.assert_called_once()
        self.assertEqual(canonical.call_args.kwargs.get("surface"), "undx")
        self.assertTrue(result.ok)
        self.assertEqual(len(self._reports()), 1)


# ---------------------------------------------------------------------------
# 7. Marketplace
# ---------------------------------------------------------------------------


class MarketplaceListingAuthority(CompletionCase):
    """The marketplace service is reused exactly as it stands.

    Nothing in this pack re-decides the feature flag, the approved-seller requirement,
    the account hold, product ownership or the legal status transitions. So these tests
    are about the wiring: that the eligibility gates still bite when the caller is
    UNDX, that ownership is enforced on every verb, and that "delete" means archive.
    """

    flags = {"BUSINESS_OS_MARKETPLACE": "1"}

    def setUp(self) -> None:
        super().setUp()
        from services.business_os.marketplace import schema
        from services.business_os.marketplace import service as marketplace

        schema.ensure_schema()
        self.marketplace = marketplace
        marketplace.set_seller_status(OWNER_ID, "approved", actor="test")
        marketplace.set_seller_status(OTHER_ID, "approved", actor="test")

    def _create(self, user_id: int = OWNER_ID, **overrides):
        arguments = {"title": "Handmade lamp", "price_cents": 4500,
                     "fulfillment_type": "digital", **overrides}
        return undx_agent_tools.marketplace_listing_create(user_id, arguments)

    def _status(self, listing_id: str, user_id: int = OWNER_ID) -> str:
        product = self.marketplace.get_product(listing_id, requester_user_id=user_id)
        return "" if not product else str(product.get("status") or "")

    def test_an_unapproved_seller_cannot_create_a_listing(self) -> None:
        result = self._create(OUTSIDER_ID)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "seller_not_approved")

    def test_a_created_listing_is_a_draft_and_says_so(self) -> None:
        """A seller told the listing was created and not told it is unpublished will
        reasonably assume it is on sale."""
        result = self._create()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["status"], "draft")
        self.assertEqual(self._status(result.data["listing_id"]), "draft")

    def test_a_stranger_cannot_edit_a_listing(self) -> None:
        listing_id = self._create().data["listing_id"]
        result = undx_agent_tools.marketplace_listing_update(
            OTHER_ID, {"listing_id": listing_id, "field": "title", "value": "Mine now"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "not_found")
        product = self.marketplace.get_product(listing_id, requester_user_id=OWNER_ID)
        self.assertEqual(product["title"], "Handmade lamp")

    def test_a_foreign_listing_reads_as_missing_rather_than_forbidden(self) -> None:
        """Another approved seller and a nonexistent id refuse identically, so the
        refusal cannot be used to enumerate which product ids exist."""
        listing_id = self._create().data["listing_id"]
        foreign = undx_agent_tools.marketplace_listing_pause(
            OTHER_ID, {"listing_id": listing_id})
        missing = undx_agent_tools.marketplace_listing_pause(
            OTHER_ID, {"listing_id": "mktp_does_not_exist"})
        self.assertEqual((foreign.ok, foreign.error_code),
                         (missing.ok, missing.error_code))

    def test_a_price_edit_is_stored_as_a_number(self) -> None:
        """``price_cents`` arrives as text from a language model. Storing "4900" in an
        INTEGER column would compare unequal to 4900 in the verifier and read back as a
        failed edit against the seller's own listing."""
        listing_id = self._create().data["listing_id"]
        result = undx_agent_tools.marketplace_listing_update(
            OWNER_ID, {"listing_id": listing_id, "field": "price_cents", "value": "4900"})
        self.assertTrue(result.ok)
        product = self.marketplace.get_product(listing_id, requester_user_id=OWNER_ID)
        self.assertEqual(product["price_cents"], 4900)

    def test_a_price_that_is_not_a_number_is_refused_before_the_service(self) -> None:
        listing_id = self._create().data["listing_id"]
        with patch.object(self.marketplace, "update_product") as writer:
            result = undx_agent_tools.marketplace_listing_update(
                OWNER_ID, {"listing_id": listing_id, "field": "price_cents",
                           "value": "about fifty dollars"})
        writer.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "invalid_value")

    def test_status_is_not_settable_through_the_update_capability(self) -> None:
        """Lifecycle is expressed as verbs, never as a raw status. A capability that
        could write ``status`` directly would be a second lifecycle engine sitting
        beside the transition table."""
        listing_id = self._create().data["listing_id"]
        result = undx_agent_tools.marketplace_listing_update(
            OWNER_ID, {"listing_id": listing_id, "field": "status", "value": "active"})
        self.assertFalse(result.ok)
        self.assertEqual(self._status(listing_id), "draft")

    def test_pause_and_resume_move_a_live_listing_back_and_forth(self) -> None:
        listing_id = self._create().data["listing_id"]
        self.marketplace.transition_product(OWNER_ID, listing_id, "publish")
        self.assertEqual(self._status(listing_id), "active")

        paused = undx_agent_tools.marketplace_listing_pause(
            OWNER_ID, {"listing_id": listing_id})
        self.assertTrue(paused.ok)
        self.assertEqual(self._status(listing_id), "paused")

        resumed = undx_agent_tools.marketplace_listing_resume(
            OWNER_ID, {"listing_id": listing_id})
        self.assertTrue(resumed.ok)
        self.assertEqual(self._status(listing_id), "active")

    def test_pausing_an_already_paused_listing_is_refused_not_duplicated(self) -> None:
        """The service refuses the illegal transition rather than writing ``paused``
        twice, so the state cannot be reached by two different paths."""
        listing_id = self._create().data["listing_id"]
        self.marketplace.transition_product(OWNER_ID, listing_id, "publish")
        undx_agent_tools.marketplace_listing_pause(OWNER_ID, {"listing_id": listing_id})
        again = undx_agent_tools.marketplace_listing_pause(
            OWNER_ID, {"listing_id": listing_id})
        self.assertFalse(again.ok)
        self.assertEqual(again.error_code, "illegal_transition")
        self.assertEqual(self._status(listing_id), "paused")

    def test_delete_archives_rather_than_destroying_the_row(self) -> None:
        """Orders reference products. A row that vanishes from under a buyer's receipt
        is a support incident, not a feature."""
        listing_id = self._create().data["listing_id"]
        with patch.object(self.marketplace, "transition_product",
                          wraps=self.marketplace.transition_product) as spy:
            result = undx_agent_tools.marketplace_listing_delete(
                OWNER_ID, {"listing_id": listing_id})
        self.assertEqual(spy.call_args.args[2], "archive")
        self.assertTrue(result.ok)
        self.assertEqual(self._status(listing_id), "archived")
        self.assertIsNotNone(
            self.marketplace.get_product(listing_id, requester_user_id=OWNER_ID))

    def test_the_marketplace_writes_its_own_audit_trail(self) -> None:
        """``business_os_mkt_audit``, not ``pulse_mutation_audit``. Two trails because
        two verticals, and this pack did not merge them: the marketplace one already
        records the seller-side history a support agent reads."""
        listing_id = self._create().data["listing_id"]
        undx_agent_tools.marketplace_listing_delete(OWNER_ID, {"listing_id": listing_id})
        self.fix.cur.execute(
            "SELECT action, actor FROM business_os_mkt_audit "
            "WHERE subject_type='product' AND subject_ref=? ORDER BY id", (listing_id,))
        rows = [dict(row) for row in self.fix.cur.fetchall()]
        self.assertEqual([row["action"] for row in rows],
                         ["product_create", "product_archive"])
        self.assertEqual(rows[-1]["actor"], str(OWNER_ID))

    def test_the_flag_being_off_stops_every_verb(self) -> None:
        """The rollout flag is read on every call, so this genuinely exercises a live
        kill switch rather than a cached one."""
        listing_id = self._create().data["listing_id"]
        self.fix.set_flags(BUSINESS_OS_MARKETPLACE="")
        try:
            for executor, arguments in (
                (undx_agent_tools.marketplace_listing_create,
                 {"title": "x", "price_cents": 1}),
                (undx_agent_tools.marketplace_listing_update,
                 {"listing_id": listing_id, "field": "title", "value": "x"}),
                (undx_agent_tools.marketplace_listing_pause, {"listing_id": listing_id}),
                (undx_agent_tools.marketplace_listing_delete, {"listing_id": listing_id}),
            ):
                with self.subTest(executor=executor.__name__):
                    result = executor(OWNER_ID, arguments)
                    self.assertFalse(result.ok)
                    self.assertEqual(result.error_code, "disabled")
        finally:
            self.fix.set_flags(BUSINESS_OS_MARKETPLACE="1")
        self.assertEqual(self._status(listing_id), "draft")


# ---------------------------------------------------------------------------
# 8. Verification
# ---------------------------------------------------------------------------


def _result(capability_id: str, tool_name: str, data: dict) -> ToolResult:
    return ToolResult(ok=True, tool_name=tool_name, capability_id=capability_id,
                      canonical_resource_id="x", data=data, latency_ms=1)


class VerifiersGoAndLook(CompletionCase):
    """Verification reads the world back. It does not read the result."""

    def test_the_block_verifier_takes_its_expectation_from_the_capability(self) -> None:
        """Not from the arguments. Block and unblock share one argument shape and one
        verifier, so a verifier reading an argument would let an unblock that quietly
        did nothing verify against the still-present block it was supposed to remove."""
        from services import pulse_social_graph_service as graph

        with recording_safety_events():
            graph.block_user(OWNER_ID, OTHER_ID)
        verified = undx_verification.verify(
            "profile_block_value", OWNER_ID, {"target_user_id": OTHER_ID},
            _result("profile.unblock", "pulsesoc.profile.unblock",
                    {"target_user_id": OTHER_ID}))
        self.assertEqual(verified.state, VerificationState.FAILED)

    def test_the_block_verifier_reads_both_tables(self) -> None:
        """A write that landed in one table and not the other reads back as a mismatch
        rather than as success."""
        from services import pulse_social_graph_service as graph

        with recording_safety_events():
            graph.block_user(OWNER_ID, OTHER_ID)
        verified = undx_verification.verify(
            "profile_block_value", OWNER_ID, {"target_user_id": OTHER_ID},
            _result("profile.block", "pulsesoc.profile.block",
                    {"target_user_id": OTHER_ID}))
        self.assertEqual(verified.state, VerificationState.VERIFIED)
        self.assertEqual(verified.evidence["read_back"]["messaging_block_status"], "active")

    def test_the_reel_verifier_reports_pending_when_the_row_is_unreadable(self) -> None:
        """``None`` from the owner-scoped read means "not yours, or not there". After a
        delete this account performed, that is unreadable rather than failed, and
        collapsing the two would report a missing row as a failed delete."""
        from services import pulse_feed_engine

        with patch.object(pulse_feed_engine, "get_owned_reel_deletion_state",
                          return_value=None):
            verified = undx_verification.verify(
                "reel_deleted", OWNER_ID, {"reel_id": 4242},
                _result("reels.delete", "pulsesoc.reels.delete", {"reel_id": 4242}))
        self.assertEqual(verified.state, VerificationState.PENDING)

    def test_the_comment_delete_verifier_can_tell_deleted_from_never_existed(self) -> None:
        """``get_comment`` filters deleted rows out, so it answers ``None`` for both.
        The verifier uses ``get_comment_state``, which reports the distinction."""
        from services import pulse_feed_engine

        reel_id, post_id = make_reel(self.fix, OWNER_ID)
        comment_id = make_comment(self.fix, post_id, OWNER_ID)
        pulse_feed_engine.delete_comment(OWNER_ID, comment_id)
        self.assertIsNone(pulse_feed_engine.get_comment(comment_id))
        state = pulse_feed_engine.get_comment_state(OWNER_ID, comment_id)
        self.assertTrue(state["exists"])
        self.assertTrue(state["deleted"])
        verified = undx_verification.verify(
            "reel_comment_deleted", OWNER_ID, {"comment_id": comment_id},
            _result("reels.comment.delete", "pulsesoc.reels.comment.delete",
                    {"comment_id": comment_id}))
        self.assertEqual(verified.state, VerificationState.VERIFIED)

    def test_an_undeleted_comment_does_not_verify_a_deletion(self) -> None:
        _reel_id, post_id = make_reel(self.fix, OWNER_ID)
        comment_id = make_comment(self.fix, post_id, OWNER_ID)
        verified = undx_verification.verify(
            "reel_comment_deleted", OWNER_ID, {"comment_id": comment_id},
            _result("reels.comment.delete", "pulsesoc.reels.comment.delete",
                    {"comment_id": comment_id}))
        self.assertNotEqual(verified.state, VerificationState.VERIFIED)

    def test_a_duplicate_report_verifies_without_requiring_the_new_reason(self) -> None:
        """An already-open report deliberately keeps its original text on the read-back
        path the verifier uses. Requiring the reason to match unconditionally would turn
        correct idempotency into a reported failure."""
        from services import pulse_feed_engine

        post_id = self.fix.make_post(OTHER_ID, body="Questionable.")
        pulse_feed_engine.report_content(OWNER_ID, "post", post_id, "First reason.")
        verified = undx_verification.verify(
            "content_reported", OWNER_ID,
            {"content_type": "post", "content_id": post_id, "reason": "Second reason."},
            _result("feed.report", "pulsesoc.feed.report",
                    {"content_type": "post", "content_id": post_id, "changed": False}))
        self.assertEqual(verified.state, VerificationState.VERIFIED)

    def test_a_report_that_was_never_filed_does_not_verify(self) -> None:
        post_id = self.fix.make_post(OTHER_ID, body="Questionable.")
        verified = undx_verification.verify(
            "content_reported", OWNER_ID,
            {"content_type": "post", "content_id": post_id, "reason": "Misleading."},
            _result("feed.report", "pulsesoc.feed.report",
                    {"content_type": "post", "content_id": post_id, "changed": True}))
        self.assertNotEqual(verified.state, VerificationState.VERIFIED)

    def test_a_verifier_never_raises_out_of_verify(self) -> None:
        """An escaping exception would leave the user's data changed with no receipt.
        Driven with an argument shape no verifier expects, against every verifier this
        pack added."""
        for capability_id in NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            with self.subTest(capability_id=capability_id):
                verified = undx_verification.verify(
                    spec.verifier, OWNER_ID, {},
                    _result(capability_id, spec.tool_name, {}))
                self.assertIsNotNone(verified.state)


if __name__ == "__main__":
    unittest.main()
