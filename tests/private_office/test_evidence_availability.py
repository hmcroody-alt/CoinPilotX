"""Evidence availability — the third axis, and the honesty rules it carries.

Run either way::

    python -m pytest tests/private_office/test_evidence_availability.py
    python -m unittest tests.private_office.test_evidence_availability

What these tests defend
-----------------------
* **Availability is computed, never stored, and never confused with the other
  two axes.** Provenance says where a claim came from; verification says what
  checking happened; availability says whether the cited source is reachable
  now. A deleted document changes only the third.
* **A source that is gone cannot establish a fresh verification.** Exactly one
  availability state — ``AVAILABLE`` — passes :func:`evidence.may_verify`.
  Archived, expired, superseded, deleted, missing and unknown all fail closed.
* **A verification that already happened stays attributable.** Deleting the
  document does not un-verify the fact; it makes the citation unopenable while
  the attribution survives.
* **Cross-owner is indistinguishable from nonexistent on every field**, not
  just on ``exists``. The new fields must not become an existence oracle.
* **"I could not look" is not "there is nothing there."** A missing table
  resolves ``UNKNOWN``, not ``NOT_FOUND``.

The database here is a hand-built SQLite with only the columns the resolver
reads. That is deliberate: these tests are about the resolver's contract, and
building them on the full schema would make them pass or fail for reasons that
belong to other modules.
"""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.private_office import evidence  # noqa: E402


OWNER = 4001
STRANGER = 4002


def _build(conn: sqlite3.Connection) -> None:
    """Only the kinds these tests cite. ``briefing`` is left uncreated on
    purpose — it is the "could not look" case."""
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE private_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE')"""
    )
    cur.execute(
        """CREATE TABLE private_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER NOT NULL,
            fact_type TEXT NOT NULL DEFAULT '',
            lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE')"""
    )
    cur.execute(
        """CREATE TABLE private_shield_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '')"""
    )
    conn.commit()


def _doc(conn, owner: int, title: str, lifecycle: str = "ACTIVE") -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO private_documents (owner_user_id, title, lifecycle_state) "
        "VALUES (?,?,?)",
        (owner, title, lifecycle),
    )
    conn.commit()
    return int(cur.lastrowid)


class AvailabilityDecisionTable(unittest.TestCase):
    """:func:`evidence.availability_for` is pure, so pin it directly."""

    def test_active_is_available(self):
        self.assertEqual(
            evidence.availability_for("document", "ACTIVE", found=True),
            evidence.AVAILABILITY_AVAILABLE,
        )

    def test_retired_states_stay_historically_resolvable(self):
        for lifecycle, expected in (
            ("ARCHIVED", evidence.AVAILABILITY_ARCHIVED),
            ("SUPERSEDED", evidence.AVAILABILITY_SUPERSEDED),
            ("EXPIRED", evidence.AVAILABILITY_EXPIRED),
        ):
            with self.subTest(lifecycle=lifecycle):
                got = evidence.availability_for("document", lifecycle, found=True)
                self.assertEqual(got, expected)
                self.assertTrue(evidence.is_resolvable(got))

    def test_deleted_and_revoked_are_source_unavailable(self):
        for lifecycle in ("DELETED", "REVOKED"):
            with self.subTest(lifecycle=lifecycle):
                self.assertEqual(
                    evidence.availability_for("document", lifecycle, found=True),
                    evidence.AVAILABILITY_SOURCE_UNAVAILABLE,
                )

    def test_not_found_beats_any_lifecycle_value(self):
        # A lifecycle string cannot resurrect a row that was not there. If this
        # ever reads ACTIVE, a caller can forge availability by passing a state.
        self.assertEqual(
            evidence.availability_for("document", "ACTIVE", found=False),
            evidence.AVAILABILITY_NOT_FOUND,
        )

    def test_unrecognised_lifecycle_fails_closed_to_unknown(self):
        for lifecycle in ("SOMETHING_NEW", "active-ish", "?", "0"):
            with self.subTest(lifecycle=lifecycle):
                self.assertEqual(
                    evidence.availability_for("document", lifecycle, found=True),
                    evidence.AVAILABILITY_UNKNOWN,
                )

    def test_empty_lifecycle_on_a_lifecycle_bearing_kind_is_unknown(self):
        self.assertEqual(
            evidence.availability_for("document", "", found=True),
            evidence.AVAILABILITY_UNKNOWN,
        )

    def test_lifecycle_matching_is_case_and_whitespace_tolerant(self):
        self.assertEqual(
            evidence.availability_for("document", "  active ", found=True),
            evidence.AVAILABILITY_AVAILABLE,
        )

    def test_kind_without_lifecycle_column_is_available_when_present(self):
        # Shield findings have a workflow status, not a lifecycle. Existence is
        # the whole of what can be said about them as a source.
        self.assertIsNone(evidence.KIND_LIFECYCLE["finding"])
        self.assertEqual(
            evidence.availability_for("finding", "", found=True),
            evidence.AVAILABILITY_AVAILABLE,
        )

    def test_every_kind_has_a_lifecycle_decision(self):
        # A kind added to KINDS without a KIND_LIFECYCLE entry would silently
        # take the .get() default and be treated as lifecycle-free.
        self.assertEqual(set(evidence.KINDS), set(evidence.KIND_LIFECYCLE))


class VerificationGate(unittest.TestCase):

    def test_only_available_may_establish_verification(self):
        for state in evidence.AVAILABILITY_STATES:
            with self.subTest(state=state):
                self.assertEqual(
                    evidence.may_verify(state),
                    state == evidence.AVAILABILITY_AVAILABLE,
                )

    def test_historically_resolvable_is_not_the_same_as_verifiable(self):
        # The two sets must not be allowed to collapse into one another: a
        # citation you can still open is not thereby a citation you may check
        # something new against.
        self.assertTrue(
            evidence.VERIFYING_AVAILABILITY < evidence.RESOLVABLE_AVAILABILITY
        )

    def test_junk_input_does_not_pass_the_gate(self):
        for value in (None, "", 0, "available", "AVAILABLE ", object()):
            with self.subTest(value=repr(value)):
                self.assertFalse(evidence.may_verify(value))


class HistoricalAttribution(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        _build(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_deleting_the_source_does_not_erase_the_attribution(self):
        doc_id = _doc(self.conn, OWNER, "Lease 2024")
        cur = self.conn.cursor()
        ref = evidence.format_ref("document", doc_id)

        before = evidence.resolve_refs(cur, OWNER, [ref])[0]
        self.assertTrue(before["may_verify"])
        checked = evidence.historical_attribution(before)
        self.assertTrue(checked["attributable"])
        self.assertTrue(checked["openable"])

        cur.execute(
            "UPDATE private_documents SET lifecycle_state='DELETED' WHERE id=?",
            (doc_id,),
        )
        self.conn.commit()

        after = evidence.resolve_refs(cur, OWNER, [ref])[0]
        # The row is still there — that is what keeps the citation honest —
        # but it is no longer a source anything new may be checked against.
        self.assertTrue(after["exists"])
        self.assertEqual(
            after["availability"], evidence.AVAILABILITY_SOURCE_UNAVAILABLE
        )
        self.assertFalse(after["may_verify"])
        self.assertFalse(after["resolvable"])

        still = evidence.historical_attribution(after)
        self.assertTrue(still["attributable"], "a past check does not un-happen")
        self.assertFalse(still["openable"])
        self.assertEqual(still["id"], doc_id)

    def test_attribution_of_a_ref_that_never_resolved(self):
        entry = evidence.resolve_refs(
            self.conn.cursor(), OWNER, ["document:999999"]
        )[0]
        checked = evidence.historical_attribution(entry)
        self.assertFalse(checked["openable"])
        # The ref is well formed, so the sentence "this cited document 999999"
        # is still sayable even though nothing answers to it.
        self.assertTrue(checked["attributable"])

    def test_attribution_of_junk_is_refused_rather_than_guessed(self):
        for value in (None, "document:1", 7, [], {}):
            with self.subTest(value=repr(value)):
                out = evidence.historical_attribution(value)
                self.assertFalse(out["attributable"])
                self.assertFalse(out["openable"])
                self.assertEqual(out["availability"], evidence.AVAILABILITY_UNKNOWN)


class ResolverOwnerIsolation(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        _build(self.conn)
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.close()

    def test_cross_owner_is_indistinguishable_from_nonexistent_on_every_field(self):
        doc_id = _doc(self.conn, STRANGER, "Their private will", "ARCHIVED")
        theirs = evidence.resolve_refs(
            self.cur, OWNER, [evidence.format_ref("document", doc_id)]
        )[0]
        nothing = evidence.resolve_refs(self.cur, OWNER, ["document:999999"])[0]

        # Everything except the ref and the id — which the caller supplied and
        # already knows — must match exactly. Comparing only `exists` (as the
        # original Stage 14 check did) would let `lifecycle` or `availability`
        # become the oracle that `exists` was closed against.
        oracle_fields = ("exists", "label", "lifecycle", "availability",
                         "resolvable", "may_verify")
        self.assertEqual(
            {k: theirs[k] for k in oracle_fields},
            {k: nothing[k] for k in oracle_fields},
        )
        self.assertEqual(theirs["availability"], evidence.AVAILABILITY_NOT_FOUND)
        self.assertEqual(theirs["label"], "")
        self.assertEqual(theirs["lifecycle"], "")

    def test_a_deleted_row_of_anothers_still_leaks_nothing(self):
        doc_id = _doc(self.conn, STRANGER, "Their deleted deed", "DELETED")
        theirs = evidence.resolve_refs(
            self.cur, OWNER, [evidence.format_ref("document", doc_id)]
        )[0]
        # SOURCE_UNAVAILABLE would confirm the row exists. NOT_FOUND is the
        # only answer that does not.
        self.assertEqual(theirs["availability"], evidence.AVAILABILITY_NOT_FOUND)

    def test_owner_zero_resolves_nothing_and_claims_nothing(self):
        doc_id = _doc(self.conn, OWNER, "Mine")
        out = evidence.resolve_refs(
            self.cur, 0, [evidence.format_ref("document", doc_id)]
        )[0]
        self.assertFalse(out["exists"])
        # Not NOT_FOUND: no probe was made on anyone's behalf, so "there is no
        # such row" is a conclusion this call never reached.
        self.assertEqual(out["availability"], evidence.AVAILABILITY_UNKNOWN)
        self.assertFalse(out["may_verify"])

    def test_owner_sees_own_row_across_lifecycles(self):
        for lifecycle, expected in (
            ("ACTIVE", evidence.AVAILABILITY_AVAILABLE),
            ("ARCHIVED", evidence.AVAILABILITY_ARCHIVED),
            ("SUPERSEDED", evidence.AVAILABILITY_SUPERSEDED),
            ("DELETED", evidence.AVAILABILITY_SOURCE_UNAVAILABLE),
        ):
            with self.subTest(lifecycle=lifecycle):
                doc_id = _doc(self.conn, OWNER, f"Doc {lifecycle}", lifecycle)
                out = evidence.resolve_refs(
                    self.cur, OWNER, [evidence.format_ref("document", doc_id)]
                )[0]
                self.assertTrue(out["exists"])
                self.assertEqual(out["availability"], expected)
                self.assertEqual(out["label"], f"Doc {lifecycle}")


class ResolverProbeHonesty(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        _build(self.conn)
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.close()

    def test_absent_table_is_unknown_not_not_found(self):
        # `private_office_briefings` was never created here.
        out = evidence.resolve_refs(self.cur, OWNER, ["briefing:1"])[0]
        self.assertFalse(out["exists"])
        self.assertEqual(out["availability"], evidence.AVAILABILITY_UNKNOWN)
        self.assertFalse(out["may_verify"])

    def test_absent_table_does_not_raise(self):
        try:
            evidence.resolve_refs(self.cur, OWNER, ["briefing:1", "briefing:2"])
        except Exception as exc:  # pragma: no cover - the assertion is the point
            self.fail(f"resolver raised on an absent table: {exc!r}")

    def test_mixed_kinds_resolve_independently(self):
        doc_id = _doc(self.conn, OWNER, "Deed")
        cur = self.cur
        cur.execute(
            "INSERT INTO private_facts (owner_user_id, fact_type, lifecycle_state) "
            "VALUES (?,?,?)",
            (OWNER, "preferred_airline", "SUPERSEDED"),
        )
        self.conn.commit()
        fact_id = int(cur.lastrowid)

        out = evidence.resolve_refs(
            cur, OWNER,
            [f"document:{doc_id}", f"fact:{fact_id}", "briefing:1", "document:88888"],
        )
        self.assertEqual(
            [e["availability"] for e in out],
            [
                evidence.AVAILABILITY_AVAILABLE,
                evidence.AVAILABILITY_SUPERSEDED,
                evidence.AVAILABILITY_UNKNOWN,
                evidence.AVAILABILITY_NOT_FOUND,
            ],
        )
        # Order is the caller's order, not the query's.
        self.assertEqual([e["ref"] for e in out][0], f"document:{doc_id}")

    def test_resolution_is_grouped_not_one_query_per_ref(self):
        ids = [_doc(self.conn, OWNER, f"D{n}") for n in range(12)]
        counted = {"n": 0}
        real_execute = self.cur.execute

        class Counting:
            def execute(self, *args, **kwargs):
                counted["n"] += 1
                return real_execute(*args, **kwargs)

            def fetchall(self):
                return self.cur.fetchall()

        counting = Counting()
        counting.cur = self.cur
        evidence.resolve_refs(
            counting, OWNER, [f"document:{i}" for i in ids]
        )
        self.assertEqual(
            counted["n"], 1,
            "twelve document refs must cost one query, not twelve",
        )

    def test_empty_input_costs_no_query(self):
        class Exploding:
            def execute(self, *args, **kwargs):
                raise AssertionError("no query should be issued for zero refs")

        self.assertEqual(evidence.resolve_refs(Exploding(), OWNER, []), [])
        self.assertEqual(evidence.resolve_refs(Exploding(), OWNER, None), [])
        self.assertEqual(evidence.resolve_refs(Exploding(), OWNER, ["nonsense"]), [])


class AvailabilitySummary(unittest.TestCase):

    def test_counts_split_deleted_from_missing(self):
        resolved = [
            {"availability": evidence.AVAILABILITY_AVAILABLE},
            {"availability": evidence.AVAILABILITY_ARCHIVED},
            {"availability": evidence.AVAILABILITY_SOURCE_UNAVAILABLE},
            {"availability": evidence.AVAILABILITY_NOT_FOUND},
            {"availability": evidence.AVAILABILITY_UNKNOWN},
        ]
        summary = evidence.summarize_availability(resolved)
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["resolvable"], 2)
        self.assertEqual(summary["unavailable"], 1)
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(summary["unknown"], 1)

    def test_unrecognised_state_counts_as_unknown_rather_than_vanishing(self):
        summary = evidence.summarize_availability([{"availability": "WHATEVER"}])
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["unknown"], 1)
        self.assertEqual(sum(summary["by_state"].values()), 1)

    def test_junk_entries_are_dropped_not_counted(self):
        summary = evidence.summarize_availability([None, "x", 3, {"a": 1}])
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["unknown"], 1)

    def test_empty(self):
        summary = evidence.summarize_availability([])
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["resolvable"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
