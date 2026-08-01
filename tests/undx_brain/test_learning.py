"""The learning log gets a reader that opens a row, and one that knows when to shut up.

Two tests carry the weight, and neither of them is about arithmetic.

:meth:`WhatTheWriterMakesUnreachable.test_an_event_recorded_for_user_zero_is_lost_to_every_owner`
runs the real :func:`services.pulse_ai_service._record_learning_event` against the real
schema and shows that ``int(user_id or 0) or None`` stores a NULL owner, so a whole
category of event can never be returned by any owner-scoped read. That is the limit the
module reports rather than the gap it closes, and if the writer is ever changed the
claim in :attr:`Window.unattributable` becomes wrong and this goes red.

:meth:`EverySentenceIsGrounded.test_the_sentence_never_borrows_the_events_own_hedge`
pins the one thing most likely to be "simplified" back in later: the assembled answer
carries the *time* from :mod:`facts` and not the *hedge*, because the hedge for a
live-verified event reads "this was confirmed against a running system" and a pattern
counted over records was not confirmed against anything. Using ``reading.citation``
instead of ``reading.qualifier`` is a one-word change that would make every finding
overclaim, and it would pass every test that only checked the numbers.

The rest divides in three: what a row parses into and what it refuses to guess, when a
distribution declines to name a leader, and when a succession declines to call a rate a
pattern.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap  # noqa: E402

#: ``pulse_ai_service`` pulls in the whole feed stack, which reaches werkzeug. The
#: bootstrap is what makes the real writer importable here, and the real writer is the
#: point of :class:`WhatTheWriterMakesUnreachable`.
bootstrap.install()

from services import pulse_ai_service as svc  # noqa: E402
from services.undx_brain import config as brain_config  # noqa: E402
from services.undx_brain import facts  # noqa: E402
from services.undx_brain import learning as L  # noqa: E402
from services.undx_brain import memory  # noqa: E402
from services.undx_brain import truth  # noqa: E402

#: Four flags, and every one of them is load-bearing. The master switch and the learning
#: switch gate this module; the memory switch is what lets a scope be opened at all; the
#: facts switch is what lets a finding be placed in time. A test that set fewer would
#: pass against a module that had forgotten to read one of them.
ON = {
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_BRAIN_MEMORY_ENABLED": "1",
    "UNDX_BRAIN_LEARNING_ENABLED": "1",
    "UNDX_BRAIN_FACTS_ENABLED": "1",
}

#: A fixed instant, so nothing below becomes a test of how long the suite took to run.
NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)

SCHEMA = """
CREATE TABLE pulse_ai_learning_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT UNIQUE,
    user_id INTEGER,
    event_type TEXT NOT NULL,
    source TEXT,
    metadata_json TEXT,
    created_at TEXT
)
"""


def ago(**offset) -> str:
    return (NOW - timedelta(**offset)).isoformat(timespec="seconds")


def event(minutes: int, event_type: str, source: str = "undx_agent", **metadata) -> L.Event:
    return L.Event(
        event_id=10_000 - minutes,
        owner_id=7,
        event_type=event_type,
        source=source,
        metadata=dict(metadata),
        created_at=ago(minutes=minutes),
    )


def window(*events: L.Event, **overrides) -> L.Window:
    """A loaded window, newest first, exactly as :func:`learning.load` returns one."""
    ordered = sorted(events, key=lambda item: item.created_at, reverse=True)
    stamps = sorted(item.created_at for item in ordered if item.created_at)
    fields = dict(
        ok=True,
        owner_id=7,
        events=tuple(ordered),
        first_at=stamps[0] if stamps else "",
        last_at=stamps[-1] if stamps else "",
    )
    fields.update(overrides)
    return L.Window(**fields)


def agent_actions(*capability_ids: str) -> tuple[L.Event, ...]:
    """One ``agent_action`` per capability id, ten minutes apart, oldest first."""
    return tuple(
        event(300 - index * 10, "agent_action", capability_id=capability_id)
        for index, capability_id in enumerate(capability_ids)
    )


class ADatabaseTest(unittest.TestCase):
    """Base for the tests that need the real table rather than a hand-built window."""

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self.cursor.execute(SCHEMA)
        self.addCleanup(self.conn.close)

    def insert(self, user_id, event_type, source="undx_agent", metadata=None, minutes=10):
        self.cursor.execute(
            "INSERT INTO pulse_ai_learning_events "
            "(public_id, user_id, event_type, source, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"evt_{self.cursor.lastrowid}_{minutes}_{event_type}",
                user_id,
                event_type,
                source,
                json.dumps(metadata or {}),
                ago(minutes=minutes),
            ),
        )


# ---------------------------------------------------------------------------------


class TheFlagGovernsWhetherAnythingIsAnswered(unittest.TestCase):
    """A disabled reader must never be mistaken for one that looked and found nothing."""

    def test_the_flag_exists_and_defaults_off(self):
        flag = next(
            item for item in brain_config.CATALOG
            if item.name == "UNDX_BRAIN_LEARNING_ENABLED"
        )
        self.assertEqual(flag.default, "0")
        self.assertEqual(flag.fail, "closed")

    def test_the_master_switch_alone_does_not_turn_this_on(self):
        result = L.distribution(window(*agent_actions("a", "b", "c", "d", "e")),
                                "event_type", env={"UNDX_BRAIN_ENABLED": "1"})
        self.assertFalse(result.ok)

    def test_the_learning_switch_alone_does_not_turn_this_on(self):
        result = L.distribution(window(*agent_actions("a", "b", "c", "d", "e")),
                                "event_type", env={"UNDX_BRAIN_LEARNING_ENABLED": "1"})
        self.assertFalse(result.ok)

    def test_every_entry_point_answers_not_ok_while_it_is_off(self):
        off = dict(ON, UNDX_BRAIN_LEARNING_ENABLED="0")
        loaded = window(*agent_actions("a", "b", "c", "d", "e"))
        outcomes = (
            L.load(memory.open_scope(7, env=off), None, env=off),
            L.distribution(loaded, "event_type", env=off),
            L.succession(loaded, "agent_action", "agent_action", env=off),
        )
        for outcome in outcomes:
            with self.subTest(kind=type(outcome).__name__):
                self.assertFalse(outcome.ok)
                self.assertIn("disabled", outcome.reason)

    def test_a_disabled_finding_is_not_a_conclusive_one(self):
        off = dict(ON, UNDX_BRAIN_LEARNING_ENABLED="0")
        result = L.distribution(window(*agent_actions("a", "b", "c", "d", "e")),
                                "event_type", env=off)
        self.assertFalse(result.conclusive)
        self.assertFalse(bool(result))


class WhatTheWriterMakesUnreachable(ADatabaseTest):
    """The limits that come from how the eleven call sites already write."""

    def test_an_event_recorded_for_user_zero_is_lost_to_every_owner(self):
        svc._record_learning_event(self.cursor, 7, "agent_action", "undx_agent", {})
        svc._record_learning_event(self.cursor, 0, "safety_refusal", "pulse_ai_safety", {})
        svc._record_learning_event(self.cursor, None, "conversation_reset", "pulse_ai_messenger")

        stored = [dict(row) for row in self.cursor.execute(
            "SELECT user_id, event_type FROM pulse_ai_learning_events ORDER BY id")]
        self.assertEqual(
            [row["user_id"] for row in stored], [7, None, None],
            "int(user_id or 0) or None turns owner 0 into an owner-less row",
        )

        # And no scope can reach them: owner 0 is refused outright, and every other
        # owner's WHERE clause cannot match NULL.
        self.assertIsNone(memory.owner_id(0))
        for owner in (1, 7, 99):
            loaded = L.load(memory.open_scope(owner, env=ON), self.cursor, env=ON)
            with self.subTest(owner=owner):
                self.assertTrue(loaded.ok)
                self.assertNotIn(
                    "safety_refusal", {item.event_type for item in loaded.events})

    def test_the_window_says_it_cannot_count_what_it_cannot_reach(self):
        loaded = L.load(memory.open_scope(7, env=ON), self.cursor, env=ON)
        self.assertIn("unknown", loaded.unattributable)
        self.assertNotIn(
            loaded.unattributable, ("0", "", "none"),
            "reporting zero unattributable events would turn a boundary into a "
            "false reassurance",
        )

    def test_this_module_reaches_the_table_only_through_the_memory_layer(self):
        """The isolation guarantee is only worth what the bypasses are worth.

        ``pulse_ai_learning_events`` appears in exactly one place in this module: the
        constant, and the statement handed to :func:`memory.read`. A second path — a
        connection of its own, a bare ``cur.execute``, an import of ``sqlite3`` — would
        be an owner clause written by hand again, which is the thing the memory layer
        exists to make impossible.
        """
        source = Path(L.__file__).read_text(encoding="utf-8")
        for bypass in ("sqlite3", "_open_db", "cur.execute", "cursor.execute", "connect("):
            with self.subTest(bypass=bypass):
                self.assertNotIn(bypass, source)
        self.assertIn("memory.read(", source)
        self.assertIn(memory.OWNER_MARKER, source)


class LoadingIsOwnerScopedAndBounded(ADatabaseTest):
    """The read goes through the memory layer, and comes back with limits attached."""

    def setUp(self) -> None:
        super().setUp()
        for index in range(6):
            self.insert(7, "agent_action", metadata={"capability_id": "c"}, minutes=100 - index)
        self.insert(8, "agent_action", metadata={"capability_id": "other"}, minutes=50)

    def test_one_owner_gets_their_own_events_and_only_their_own(self):
        loaded = L.load(memory.open_scope(7, env=ON), self.cursor, env=ON)
        self.assertTrue(loaded.ok)
        self.assertEqual(len(loaded.events), 6)
        self.assertEqual({item.owner_id for item in loaded.events}, {7})

    def test_a_scope_that_could_not_be_opened_produces_no_events(self):
        loaded = L.load(memory.open_scope(0, env=ON), self.cursor, env=ON)
        self.assertFalse(loaded.ok)
        self.assertEqual(loaded.events, ())
        self.assertFalse(bool(loaded))

    def test_a_limit_beyond_the_ceiling_is_brought_back_to_it(self):
        loaded = L.load(memory.open_scope(7, env=ON), self.cursor,
                        limit=L.MAX_EVENTS * 10, env=ON)
        self.assertTrue(loaded.ok)
        self.assertLessEqual(len(loaded.events), L.MAX_EVENTS)

    def test_a_truncated_read_says_so_rather_than_looking_complete(self):
        loaded = L.load(memory.open_scope(7, env=ON), self.cursor, limit=3, env=ON)
        self.assertTrue(loaded.truncated)
        self.assertEqual(len(loaded.events), 3)
        self.assertTrue(any("older ones exist" in note for note in loaded.notes))

    def test_the_newest_events_are_the_ones_kept_when_the_read_is_bounded(self):
        loaded = L.load(memory.open_scope(7, env=ON), self.cursor, limit=2, env=ON)
        self.assertEqual(
            [item.created_at for item in loaded.events],
            [ago(minutes=95), ago(minutes=96)],
            "a bounded read of history should keep the recent end of it",
        )

    def test_a_since_bound_excludes_older_events(self):
        loaded = L.load(memory.open_scope(7, env=ON), self.cursor,
                        since=ago(minutes=97), env=ON)
        self.assertEqual(len(loaded.events), 3)

    def test_the_span_is_reported_and_is_not_the_same_as_the_count(self):
        loaded = L.load(memory.open_scope(7, env=ON), self.cursor, env=ON)
        self.assertEqual(loaded.span_seconds, 5 * 60)

    def test_a_window_whose_timestamps_are_unreadable_reports_no_span(self):
        self.assertIsNone(L.Window(ok=True, first_at="nonsense", last_at="also").span_seconds)
        self.assertIsNone(L.Window(ok=True).span_seconds)


class ARowIsParsedNotGuessedAt(unittest.TestCase):
    """What :func:`learning.from_row` will and will not infer."""

    def test_a_normal_row_becomes_a_usable_event(self):
        parsed = L.from_row({
            "id": 4, "user_id": 7, "event_type": "agent_action", "source": "undx_agent",
            "metadata_json": '{"capability_id": "crypto.alerts.create"}',
            "created_at": ago(minutes=3),
        })
        self.assertTrue(parsed.usable)
        self.assertEqual(parsed.metadata["capability_id"], "crypto.alerts.create")
        self.assertEqual(parsed.notes, ())

    def test_unreadable_metadata_costs_the_metadata_and_not_the_event(self):
        parsed = L.from_row({
            "id": 4, "user_id": 7, "event_type": "agent_action",
            "metadata_json": "{not json", "created_at": ago(minutes=3),
        })
        self.assertTrue(
            parsed.usable,
            "the event_type and the timestamp are still true, so the row still counts "
            "toward a distribution over those",
        )
        self.assertEqual(parsed.metadata, {})
        self.assertTrue(any("could not be parsed" in note for note in parsed.notes))

    def test_metadata_that_is_not_an_object_is_not_forced_into_one(self):
        parsed = L.from_row({
            "event_type": "x", "metadata_json": "[1, 2]", "created_at": ago(minutes=1),
        })
        self.assertEqual(parsed.metadata, {})
        self.assertTrue(any("rather than an object" in note for note in parsed.notes))

    def test_a_row_with_no_timestamp_is_not_usable(self):
        parsed = L.from_row({"event_type": "x", "created_at": ""})
        self.assertFalse(parsed.usable)
        self.assertTrue(any("cannot be placed in time" in note for note in parsed.notes))

    def test_a_null_owner_is_noted_rather_than_defaulted_to_somebody(self):
        parsed = L.from_row({"event_type": "x", "user_id": None, "created_at": ago(minutes=1)})
        self.assertEqual(parsed.owner_id, 0)
        self.assertTrue(any("names no owner" in note for note in parsed.notes))

    def test_the_owner_rule_is_the_memory_layers_rule(self):
        for raw in (True, 3.7, "٩٩", "1_0", -4, 0):
            with self.subTest(raw=raw):
                self.assertEqual(
                    L.from_row({"event_type": "x", "user_id": raw}).owner_id, 0,
                    "an owner id this module accepted but memory.owner_id refused "
                    "would be a second isolation rule",
                )

    def test_a_missing_metadata_key_and_a_blank_one_are_different_answers(self):
        absent = L.from_row({"event_type": "x", "metadata_json": "{}"})
        blank = L.from_row({"event_type": "x", "metadata_json": '{"capability_id": ""}'})
        self.assertIsNone(L.dimension_of(absent, "metadata:capability_id"))
        self.assertEqual(L.dimension_of(blank, "metadata:capability_id"), "")

    def test_a_metadata_value_that_cannot_be_grouped_by_is_not_stringified(self):
        parsed = L.from_row({
            "event_type": "x", "metadata_json": '{"reasons": ["a", "b"]}',
        })
        self.assertIsNone(
            L.dimension_of(parsed, "metadata:reasons"),
            "grouping by a list would need an ordering or a spelling invented here, "
            "and either makes two equal things unequal",
        )


class ADistributionRefusesToNameANarrowLeader(unittest.TestCase):
    """The "which capability fails most" question, and when it declines to answer."""

    def test_a_clear_leader_is_named_with_its_share(self):
        result = L.distribution(
            window(*agent_actions("create", "create", "create", "create", "update")),
            "metadata:capability_id", now=NOW, env=ON)
        self.assertTrue(result.conclusive)
        self.assertTrue(bool(result))
        self.assertEqual(result.leader, "create")
        self.assertEqual(result.tied, ())
        self.assertEqual(result.buckets[0].count, 4)
        self.assertAlmostEqual(result.buckets[0].share, 0.8)

    def test_a_one_event_margin_is_a_tie_and_not_a_leader(self):
        result = L.distribution(
            window(*agent_actions("create", "create", "create", "update", "update")),
            "metadata:capability_id", now=NOW, env=ON)
        self.assertFalse(result.conclusive)
        self.assertEqual(result.leader, "")
        self.assertEqual(set(result.tied), {"create", "update"})
        self.assertIn("no metadata:capability_id value leads", result.answer)

    def test_the_margin_rule_is_exactly_min_margin_and_not_a_ratio(self):
        """Three to one is a leader; three to two is not, at any window size."""
        clear = L.distribution(
            window(*agent_actions("a", "a", "a", "b", "c", "d", "e")),
            "metadata:capability_id", now=NOW, env=ON)
        narrow = L.distribution(
            window(*agent_actions("a", "a", "a", "b", "b", "c", "d")),
            "metadata:capability_id", now=NOW, env=ON)
        self.assertEqual(clear.leader, "a")
        self.assertEqual(narrow.leader, "")
        self.assertEqual(clear.buckets[0].count - clear.buckets[1].count, L.MIN_MARGIN)

    def test_too_few_events_produce_a_number_needed_rather_than_a_percentage(self):
        result = L.distribution(
            window(*agent_actions("create", "create", "update")),
            "metadata:capability_id", now=NOW, env=ON)
        self.assertTrue(result.ok)
        self.assertFalse(result.conclusive)
        self.assertNotIn("%", result.answer)
        self.assertIn(str(L.MIN_SUPPORT), result.answer)
        self.assertIn(f"{L.MIN_SUPPORT - 3} more", result.reason)

    def test_events_that_could_not_carry_the_dimension_leave_the_denominator(self):
        events = list(agent_actions("create", "create", "create", "create", "update"))
        events += [event(90, "conversation_reset", source="pulse_ai_messenger"),
                   event(80, "conversation_reset", source="pulse_ai_messenger")]
        result = L.distribution(window(*events), "metadata:capability_id",
                                now=NOW, env=ON)
        self.assertEqual(result.considered, 5)
        self.assertEqual(result.absent, 2)
        self.assertAlmostEqual(sum(bucket.share for bucket in result.buckets), 1.0)
        self.assertTrue(any("excluded from the denominator" in n for n in result.notes))

    def test_a_dimension_that_is_only_absent_is_not_reported_as_a_finding(self):
        result = L.distribution(window(*agent_actions("a", "b", "c", "d", "e")),
                                "metadata:nothing_uses_this", now=NOW, env=ON)
        self.assertTrue(result.ok)
        self.assertFalse(result.conclusive)
        self.assertEqual(result.considered, 0)
        self.assertEqual(result.absent, 5)

    def test_a_column_that_identifies_a_row_is_not_a_dimension(self):
        for name in ("id", "public_id", "created_at", "user_id", "", "metadata:"):
            with self.subTest(name=name):
                result = L.distribution(window(*agent_actions("a", "b", "c", "d", "e")),
                                        name, now=NOW, env=ON)
                self.assertFalse(result.ok)
                self.assertIn("is not a dimension", result.reason)

    def test_both_real_columns_work_as_dimensions(self):
        events = list(agent_actions("a", "b", "c", "d")) + [
            event(50, "provider_failure", source="pulse_ai_messenger")]
        for name in (L.Dimension.EVENT_TYPE, L.Dimension.SOURCE, "event_type", "source"):
            with self.subTest(name=name):
                result = L.distribution(window(*events), name, now=NOW, env=ON)
                self.assertTrue(result.ok)
                self.assertEqual(result.considered, 5)

    def test_buckets_are_ordered_by_count_and_ties_broken_by_name(self):
        result = L.distribution(
            window(*agent_actions("b", "a", "c", "c", "c", "a")),
            "metadata:capability_id", now=NOW, env=ON)
        self.assertEqual([bucket.name for bucket in result.buckets], ["c", "a", "b"])


class ASuccessionIsMeasuredAgainstTheBaseRate(unittest.TestCase):
    """A rate on its own is not a pattern, and this is where that is enforced."""

    def repeated_failures(self) -> L.Window:
        """A cluster of six failures inside an otherwise ordinary day.

        The proportions matter more than the shape. Failures are a small share of the
        window overall, so a run of them is genuinely above the background — which is
        exactly the case the margin rule is supposed to let through. Six failures in a
        window that was *mostly* failures would not be, and rightly would not conclude:
        five-in-six against a five-in-nine background is not distinguishable on six
        observations, and no amount of wanting a result makes it so.
        """
        events = [event(200 - index, "provider_failure", source="pulse_ai_messenger")
                  for index in range(6)]
        events += [event(180 - index * 5, "message_answered", source="pulse_ai_messenger")
                   for index in range(19)]
        return window(*events)

    def test_a_real_run_beats_the_base_rate_and_is_reported(self):
        result = L.succession(self.repeated_failures(), "provider_failure",
                              "provider_failure", now=NOW, env=ON)
        self.assertTrue(result.conclusive)
        self.assertGreater(result.lift, 1.0)
        self.assertIn("against", result.answer)

    def test_a_common_event_following_anything_is_not_a_pattern(self):
        """Nine of ten events are the same type, so of course it follows things."""
        events = [event(200, "provider_failure", source="pulse_ai_messenger")]
        events += [event(190 - index * 5, "message_answered", source="pulse_ai_messenger")
                   for index in range(9)]
        events += [event(120 - index, "provider_failure", source="pulse_ai_messenger")
                   for index in range(5)]
        result = L.succession(window(*events), "provider_failure", "message_answered",
                              now=NOW, env=ON)
        self.assertFalse(
            result.conclusive,
            "the consequent is the background; reporting it as a signal would be the "
            "most confident-sounding way to say nothing",
        )
        self.assertIn("runs at anyway", result.answer)

    def test_the_base_rate_is_always_reported_even_when_the_finding_is_not(self):
        result = L.succession(self.repeated_failures(), "provider_failure",
                              "message_answered", now=NOW, env=ON)
        self.assertIsNotNone(result.base_rate)
        self.assertIsNotNone(result.conditional_rate)

    def test_too_few_occurrences_produce_a_number_needed_rather_than_a_rate(self):
        events = [event(200, "provider_failure", source="pulse_ai_messenger"),
                  event(190, "provider_failure", source="pulse_ai_messenger")]
        events += [event(180 - index * 5, "message_answered", source="pulse_ai_messenger")
                   for index in range(5)]
        result = L.succession(window(*events), "provider_failure", "provider_failure",
                              now=NOW, env=ON)
        self.assertTrue(result.ok)
        self.assertFalse(result.conclusive)
        self.assertNotIn("%", result.answer)
        self.assertIn(str(L.MIN_SUPPORT), result.reason)

    def test_only_the_next_event_counts_not_any_event_in_the_window(self):
        """Otherwise the finding measures how busy the account is."""
        events = [event(200 - index * 2, "provider_failure", source="pulse_ai_messenger")
                  for index in range(6)]
        # A single answered message far later. Every failure precedes it in the window,
        # but none of them is immediately followed by it.
        events.append(event(1, "message_answered", source="pulse_ai_messenger"))
        result = L.succession(window(*events), "provider_failure", "message_answered",
                              within_seconds=100_000, now=NOW, env=ON)
        self.assertEqual(result.support, 6)
        self.assertAlmostEqual(result.conditional_rate, 1 / 6)

    def test_a_gap_wider_than_the_window_does_not_count_as_followed(self):
        events = [event(200, "provider_failure", source="pulse_ai_messenger")]
        events += [event(190 - index, "provider_failure", source="pulse_ai_messenger")
                   for index in range(5)]
        near = L.succession(window(*events), "provider_failure", "provider_failure",
                            within_seconds=3600, now=NOW, env=ON)
        far = L.succession(window(*events), "provider_failure", "provider_failure",
                           within_seconds=1, now=NOW, env=ON)
        self.assertGreater(near.conditional_rate, far.conditional_rate)
        self.assertEqual(far.conditional_rate, 0.0)

    def test_a_missing_antecedent_or_consequent_is_refused_rather_than_assumed(self):
        for first, then in (("", "x"), ("x", ""), ("", "")):
            with self.subTest(first=first, then=then):
                result = L.succession(self.repeated_failures(), first, then, env=ON)
                self.assertFalse(result.ok)
                self.assertIn("required", result.reason)

    def test_an_antecedent_that_never_happened_is_not_a_conclusion(self):
        result = L.succession(self.repeated_failures(), "never_recorded",
                              "provider_failure", now=NOW, env=ON)
        self.assertTrue(result.ok)
        self.assertFalse(result.conclusive)
        self.assertEqual(result.support, 0)


class EverySentenceIsGrounded(unittest.TestCase):
    """What the assembled answer may and may not say about itself."""

    def clear_finding(self) -> L.Finding:
        return L.distribution(
            window(*agent_actions("create", "create", "create", "create", "update")),
            "metadata:capability_id", now=NOW, env=ON)

    def test_the_sentence_never_borrows_the_events_own_hedge(self):
        result = self.clear_finding()
        hedge = truth.hedge_for(L.EVENT_TRUST)
        self.assertTrue(hedge, "the fixture is only meaningful if the hedge is non-empty")
        self.assertNotIn(
            hedge, result.answer,
            "the events were confirmed against a running system; a pattern counted "
            "over them was not, and borrowing their hedge would say it was",
        )
        self.assertIn(L.GROUNDING, result.answer)

    def test_the_sentence_carries_the_time_it_is_true_of(self):
        result = self.clear_finding()
        self.assertTrue(result.reading.ok)
        self.assertIn(result.reading.qualifier, result.answer)
        self.assertIn("as of", result.answer)

    def test_no_finding_ever_claims_to_describe_the_present(self):
        result = self.clear_finding()
        self.assertFalse(result.reading.may_cite_as_current)
        self.assertFalse(
            truth.may_claim_live_state(L.EVENT_TRUST),
            "if this ever becomes true, the sentences here need rewriting rather than "
            "silently strengthening",
        )

    def test_a_finding_that_cannot_be_placed_in_time_is_not_concluded(self):
        without_facts = dict(ON, UNDX_BRAIN_FACTS_ENABLED="0")
        result = L.distribution(
            window(*agent_actions("create", "create", "create", "create", "update")),
            "metadata:capability_id", now=NOW, env=without_facts)
        self.assertTrue(result.ok)
        self.assertEqual(result.leader, "create")
        self.assertFalse(
            result.conclusive,
            "the counting was correct and the tense was unavailable; only one of "
            "those is enough to state a finding",
        )
        self.assertIn("could not be established", result.answer)

    def test_the_time_comes_from_the_packages_one_parser(self):
        self.assertIs(facts.parse_moment, facts._parse_time)
        moment, naive = facts.parse_moment(ago(minutes=5))
        self.assertIsNotNone(moment)
        self.assertFalse(naive)

    def test_a_finding_is_truthy_only_when_it_is_conclusive(self):
        clear = self.clear_finding()
        tied = L.distribution(
            window(*agent_actions("create", "create", "create", "update", "update")),
            "metadata:capability_id", now=NOW, env=ON)
        self.assertTrue(bool(clear))
        self.assertFalse(bool(tied))
        self.assertTrue(tied.ok, "the call succeeded; the answer is what is unusable")


class NothingHereRaises(unittest.TestCase):
    """Every entry point is on a response path, and none of them may throw."""

    def test_garbage_in_every_argument_still_returns_an_answer(self):
        junk = (None, 0, "", [], {}, object(), 3.7, True)
        for value in junk:
            with self.subTest(value=type(value).__name__):
                self.assertIsInstance(L.from_row(value), L.Event)
                self.assertIsInstance(
                    L.distribution(value, value, env=ON), L.Finding)
                self.assertIsInstance(
                    L.succession(value, value, value, env=ON), L.Finding)
                # And with a dimension that *is* real, so the window argument is the
                # thing being abused rather than shielded by an early refusal.
                self.assertIsInstance(
                    L.distribution(value, "event_type", env=ON), L.Finding)
                self.assertIsInstance(
                    L.succession(value, "a", "b", env=ON), L.Finding)

    def test_a_cursor_that_explodes_produces_a_reason_and_not_a_traceback(self):
        class Exploding:
            def execute(self, *args, **kwargs):
                raise RuntimeError("the database went away")

        loaded = L.load(memory.open_scope(7, env=ON), Exploding(), env=ON)
        self.assertFalse(loaded.ok)
        self.assertIn("RuntimeError", loaded.reason)

    def test_a_nonsense_limit_falls_back_to_the_ceiling(self):
        for value in (None, 0, -5, "many", [], True, False):
            with self.subTest(value=value):
                self.assertEqual(L._bounded(value), L.MAX_EVENTS)

    def test_a_limit_only_ever_coerces_downwards(self):
        """The one place this module is looser than ``memory.owner_id``, on purpose."""
        self.assertEqual(L._bounded(3.7), 3)
        self.assertEqual(L._bounded("12"), 12)
        self.assertEqual(L._bounded(L.MAX_EVENTS + 1), L.MAX_EVENTS)

    def test_a_nonsense_window_size_does_not_break_a_succession(self):
        loaded = window(*[event(200 - index, "provider_failure") for index in range(6)])
        for value in (None, 0, -1):
            with self.subTest(value=value):
                result = L.succession(loaded, "provider_failure", "provider_failure",
                                      within_seconds=value, now=NOW, env=ON)
                self.assertTrue(result.ok)
                self.assertEqual(result.conditional_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
