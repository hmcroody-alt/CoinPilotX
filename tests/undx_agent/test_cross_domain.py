"""What a heterogeneous read is allowed to say about the relationship between its parts.

Batch 2's tests asked whether an analyser reads the fields that decide the answer inside
one domain. These ask the question one level out: when a single authorised read returns
rows from several PulseSoc sources at once, which relationships between those rows are
supported by the evidence, and — much more importantly — which are not.

The module's headline rule is the one most of this file exists to pin down:

    A correlation needs a complete view; a partial read supports observations, not
    relationships.

Direction, concentration and clustering are all claims about *proportion*. On a degraded
read every count is a floor, so every ratio built from those counts is unsound in a
direction nobody can bound. Attention is the exception because it is a claim about rows
that were actually read: a floor on a floor is still true of every row named. That
asymmetry is not a detail of the implementation, it is the module's entire argument, so
it is tested from both sides — that the proportion clauses disappear, and that attention
does not.

The two Batch 2 invariants carry over unchanged and are enforced against every reading
this file produces rather than restated per test: every digit is declared, and absent is
not zero.
"""

from __future__ import annotations

import unittest

from services.undx_agent_contracts import ToolResult
from services import undx_cross_domain as cd
from services import undx_domain_reasoning as dm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _record(kind: str, title: str = "", *, timestamp: str = "2026-07-01T00:00:00Z",
            **data: object) -> dict[str, object]:
    """One canonical record, built by hand for the reason Batch 2's fixtures are.

    Importing the producer would make these tests pass whenever producer and consumer
    agreed with each other about the wrong thing. The field names here are copied from
    ``_activity_daily_summary`` and ``search_global`` by eye, and a drift in either is
    supposed to show up as a failure rather than as an agreement.
    """
    return {
        "kind": kind,
        "title": title,
        "detail": "",
        "source": "pulsesoc",
        "timestamp": timestamp,
        "confidence": "high",
        "data": dict(data),
    }


def _result(capability_id: str, records: list[dict[str, object]] | None = None,
            degraded: list[str] | None = None) -> ToolResult:
    return ToolResult(
        ok=True,
        tool_name=capability_id.replace(".", "_"),
        capability_id=capability_id,
        records=list(records or []),
        degraded_sources=list(degraded or []),
    )


def _activity(unread_count: int = 0, *, day: str = "2026-07-12") -> list[dict[str, object]]:
    """A realistic ``activity.daily_summary`` list: published rows and received rows.

    Shaped after the real composer, which is why the counts are lopsided — a day with
    one post and several notifications is the ordinary case, and the ordinary case is
    where a wrong clause does its damage.
    """
    rows = [
        _record("post_created", "You posted a photo", timestamp=f"{day}T09:00:00Z"),
        _record("notification", "Someone replied", timestamp=f"{day}T10:00:00Z",
                read=unread_count < 1),
        _record("message_received", "New message from Ana",
                timestamp=f"{day}T11:00:00Z", read=unread_count < 2),
        _record("new_follower", "Ben followed you", timestamp=f"{day}T12:00:00Z",
                read=unread_count < 3),
    ]
    return rows


def _strings(reading: dm.DomainReading) -> list[str]:
    return [reading.assessment, *reading.interpretations, *reading.attention,
            *reading.next_steps, *reading.uncertainties]


class _CrossAssertions(unittest.TestCase):

    def assertDeclared(self, reading: dm.DomainReading) -> None:
        """No emitted string carries a digit the reading did not declare.

        ``_strip_undeclared`` drops an offending clause silently, one clause at a time,
        so a violation of this convention does not look like a bug from the outside —
        it looks like a module with less to say than it used to have.
        """
        for text in _strings(reading):
            for token in dm._DIGITS.findall(text):
                self.assertIn(
                    token, reading.numbers,
                    f"undeclared number {token!r} in {text!r} — build_cross_reading "
                    f"would drop this clause silently")

    def build(self, capability_id: str, records: list[dict[str, object]],
              degraded: list[str] | None = None) -> dm.DomainReading:
        """Build through the real entry point, then assert the invariants.

        Going through :func:`build_cross_reading` rather than the private helpers is the
        point: a relationship function can produce a perfect clause and still lose it at
        the declaration pass one frame later, and a test that calls the helper directly
        cannot see that happen.
        """
        reading = cd.build_cross_reading(
            capability_id, _result(capability_id, records, degraded))
        self.assertDeclared(reading)
        return reading


# ---------------------------------------------------------------------------
# The degradation rule
# ---------------------------------------------------------------------------


class DegradationTests(_CrossAssertions):
    """The module's headline rule, from both sides."""

    def test_a_degraded_read_states_no_proportion(self) -> None:
        """Direction, concentration and clustering all vanish when a source drops out.

        Each of the three is checked by its own vocabulary rather than by counting
        clauses, because a future clause that happens to be safe should not make this
        test fail and a future clause that is *not* safe should not be able to slip in
        under a count that still adds up.
        """
        reading = self.build("activity.daily_summary", _activity(unread_count=2),
                             degraded=["pulse_follows"])
        body = " ".join(_strings(reading))
        self.assertNotIn("published", body)
        self.assertNotIn("mostly one thing", body)
        self.assertNotIn("lands on one day", body)

    def test_a_degraded_read_still_names_what_is_waiting(self) -> None:
        """Attention survives, because it is a claim about rows that were read.

        Dropping this too would be the safe-looking mistake: a module that says nothing
        on a partial read is easy to defend and useless to the person holding two unread
        messages. The floor is genuinely true of every row it names.
        """
        reading = self.build("activity.daily_summary", _activity(unread_count=2),
                             degraded=["pulse_follows"])
        self.assertIn("unread", reading.assessment)
        self.assertIn("Someone replied", reading.attention)
        self.assertTrue(any("dropped out" in text for text in reading.uncertainties))

    def test_a_degraded_read_with_nothing_waiting_says_nothing(self) -> None:
        """No attention and no proportions leaves an empty reading, not a hedge.

        The shape layer already narrates the dropped source. A second sentence here
        would be this module restating a fact it did not establish.
        """
        reading = self.build("activity.daily_summary", _activity(unread_count=0),
                             degraded=["pulse_follows"])
        self.assertFalse(bool(reading))

    def test_the_same_records_undegraded_do_state_relationships(self) -> None:
        """The control for the three tests above.

        Without this, a module that had simply stopped emitting relationship clauses
        entirely would pass every degradation test in this file.
        """
        reading = self.build("activity.daily_summary", _activity(unread_count=2))
        self.assertIn("published", " ".join(_strings(reading)))


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------


class DirectionTests(_CrossAssertions):

    def test_both_sides_present_is_stated_as_two_questions(self) -> None:
        reading = self.build("activity.daily_summary", _activity())
        body = " ".join(_strings(reading))
        self.assertIn("one thing you published", body)
        self.assertIn("three things that came back to you", body)

    def test_nothing_published_is_not_reported_as_a_zero(self) -> None:
        """Absent is not zero, in the one place it is most tempting to round.

        "Nothing you published came back" is an absence claim, and the read is bounded
        per source — the row that would have made the count non-zero may simply be the
        twenty-first.
        """
        reading = self.build("activity.daily_summary", [
            _record("notification", "Someone replied"),
            _record("message_received", "New message"),
            _record("new_follower", "Ben followed you"),
        ])
        self.assertNotIn("published", " ".join(_strings(reading)))

    def test_nothing_received_is_not_reported_as_a_zero(self) -> None:
        reading = self.build("activity.daily_summary", [
            _record("post_created", "You posted"),
            _record("reel_activity", "You posted a reel"),
            _record("status_activity", "You updated your status"),
        ])
        self.assertNotIn("came back to you", " ".join(_strings(reading)))

    def test_a_searched_post_is_not_something_you_published(self) -> None:
        """The buckets are about how the row was produced, not what it is called.

        ``search.global`` returns a ``post`` kind for a post *matched by a query*, which
        is a search hit and not an act of publication. Sharing a noun with an activity
        kind is exactly the coincidence that would make this clause confidently wrong,
        so the search kinds are in neither bucket.
        """
        reading = self.build("search.global", [
            _record("post", "Coffee in Lisbon"),
            _record("post", "Coffee grinders"),
            _record("message", "about coffee"),
            _record("profile", "Coffee Club"),
        ])
        self.assertNotIn("published", " ".join(_strings(reading)))
        self.assertNotIn("came back to you", " ".join(_strings(reading)))

    def test_a_price_alert_lands_in_neither_bucket(self) -> None:
        """``crypto_alert`` is a rule the person configured, not a thing that arrived.

        Forcing it into either side would put a false number on both, and a number in
        this clause is the whole clause.
        """
        published = cd._PUBLISHED_KINDS
        received = cd._RECEIVED_KINDS
        self.assertNotIn("crypto_alert", published)
        self.assertNotIn("crypto_alert", received)
        self.assertEqual([], cd._direction({"crypto_alert": 4, "post_created": 2}))


# ---------------------------------------------------------------------------
# Concentration and clustering
# ---------------------------------------------------------------------------


class ProportionTests(_CrossAssertions):

    def test_a_dominant_kind_is_stated_instead_of_the_breakdown(self) -> None:
        reading = self.build("notifications.group_summary", [
            _record("notification", f"Reply {i}") for i in range(8)
        ] + [_record("new_follower", "Ben followed you")])
        self.assertIn("mostly one thing", " ".join(_strings(reading)))

    def test_a_three_fifths_majority_is_not_rounded_up_to_a_claim(self) -> None:
        """``security.activity.summary`` at 3/5 sits just under the threshold.

        Recorded as a test because the threshold is the judgement in this clause. A
        majority is not a concentration, and a module that narrated 60% as "mostly one
        thing" would be describing a mixed list as a uniform one.
        """
        reading = self.build("security.activity.summary", [
            _record("security_session", "iPhone 17 Pro Max"),
            _record("security_session", "MacBook Pro"),
            _record("security_session", "iPad"),
            _record("security_event", "Password changed"),
            _record("security_event", "New sign-in"),
        ])
        self.assertNotIn("mostly one thing", " ".join(_strings(reading)))

    def test_a_single_kind_result_is_not_a_cross_domain_reading(self) -> None:
        """One kind is one domain, whatever capability produced it.

        The allowlist says this read *can* be heterogeneous, not that it is. A quiet day
        that returned only notifications is a notification list, and the shape layer
        already describes it.
        """
        reading = self.build("activity.daily_summary", [
            _record("notification", f"Reply {i}") for i in range(6)
        ])
        self.assertFalse(bool(reading))

    def test_two_records_are_two_records_and_not_a_pattern(self) -> None:
        reading = self.build("activity.daily_summary", [
            _record("post_created", "You posted"),
            _record("notification", "Someone replied"),
        ])
        self.assertFalse(bool(reading))

    def test_a_shared_day_is_stated_only_across_kinds(self) -> None:
        reading = self.build("activity.daily_summary", _activity())
        self.assertIn("lands on one day, 2026-07-12", " ".join(_strings(reading)))

    def test_one_kind_on_one_day_is_what_that_list_looks_like(self) -> None:
        """Ten notifications on a Tuesday is not a Tuesday that happened.

        The cross-kind condition is what makes clustering a relationship rather than an
        observation about how a single feed is generated.
        """
        self.assertEqual([], cd._clustering([
            _record("notification", f"Reply {i}", timestamp="2026-07-12T10:00:00Z")
            for i in range(6)
        ]))

    def test_an_unparseable_timestamp_removes_only_that_row(self) -> None:
        """``_date`` slices rather than parses, and refuses rather than guesses."""
        self.assertEqual("", cd._date(_record("notification", "x", timestamp="yesterday")))
        self.assertEqual("", cd._date(_record("notification", "x", timestamp="")))
        self.assertEqual("2026-07-12",
                         cd._date(_record("notification", "x",
                                          timestamp="2026-07-12T10:00:00Z")))


# ---------------------------------------------------------------------------
# Attention
# ---------------------------------------------------------------------------


class AttentionTests(_CrossAssertions):

    def test_only_the_read_field_decides_what_needs_the_person(self) -> None:
        """A status that sounds urgent is not an unread flag.

        An ``active`` alert is a rule working correctly and needs nobody. Widening this
        to any field that reads as urgent is how a summary starts telling people to act
        on things that are fine.
        """
        reading = self.build("activity.daily_summary", [
            _record("crypto_alert", "BTC above 100k", status="active"),
            _record("crypto_alert", "ETH below 2k", status="triggered"),
            _record("post_created", "You posted", status="pending"),
            _record("notification", "Someone replied", status="urgent"),
        ])
        self.assertNotIn("unread", " ".join(_strings(reading)))

    def test_unread_across_kinds_earns_the_more_than_one_screen_step(self) -> None:
        """The next step is itself a cross-domain claim.

        It is true only because the unread rows span kinds — one screen would be one
        visit — so it is gated on the same condition that makes the reading
        cross-domain at all.
        """
        reading = self.build("activity.daily_summary", _activity(unread_count=3))
        self.assertTrue(any("more than one place" in text
                            for text in reading.next_steps))

    def test_unread_within_one_kind_does_not(self) -> None:
        reading = self.build("activity.daily_summary", [
            _record("post_created", "You posted"),
            _record("notification", "Reply one", read=False),
            _record("notification", "Reply two", read=False),
            _record("new_follower", "Ben followed you", read=True),
        ])
        self.assertIn("unread", reading.assessment)
        self.assertEqual((), reading.next_steps)

    def test_attention_leads_the_reading_when_there_is_any(self) -> None:
        """"What needs me" beats "what was it made of".

        The assessment slot holds exactly one sentence and the response layer renders it
        first, so this ordering is the difference between an answer and a preamble.
        """
        reading = self.build("activity.daily_summary", _activity(unread_count=2))
        self.assertIn("unread", reading.assessment)

    def test_a_relationship_leads_when_nothing_is_waiting(self) -> None:
        reading = self.build("activity.daily_summary", _activity(unread_count=0))
        self.assertTrue(bool(reading.assessment))
        self.assertNotIn("unread", reading.assessment)


# ---------------------------------------------------------------------------
# Module-wide invariants
# ---------------------------------------------------------------------------


class ModuleInvariantTests(_CrossAssertions):

    def test_an_empty_result_is_not_an_all_clear(self) -> None:
        for capability_id in sorted(cd.CROSS_DOMAIN_CAPABILITIES):
            with self.subTest(capability=capability_id):
                reading = self.build(capability_id, [])
                self.assertFalse(bool(reading))
                self.assertEqual("", reading.assessment)

    def test_a_capability_outside_the_allowlist_gets_no_reading(self) -> None:
        """Heterogeneity is a property of the executor, not of a given result.

        A single-domain read that happens to return two kinds is a schema wrinkle, and
        narrating it as a relationship would be inventing significance out of an
        implementation detail.
        """
        self.assertFalse(cd.is_cross_domain("groups.list"))
        self.assertFalse(bool(self.build("groups.list", _activity())))

    def test_every_allowlisted_capability_is_a_real_registry_entry(self) -> None:
        """The allowlist is written by hand, so it can name a capability that is gone.

        A stale entry fails silently — the reading simply never fires — which is the
        same signature as the whole class of bug this workstream keeps chasing.
        """
        from services.undx_capability_registry import REGISTRY
        for capability_id in sorted(cd.CROSS_DOMAIN_CAPABILITIES):
            with self.subTest(capability=capability_id):
                self.assertIn(capability_id, REGISTRY)

    def test_a_raising_relationship_costs_the_reading_and_not_the_turn(self) -> None:
        """``build_cross_reading`` runs past the gateway's point of no return.

        An exception here does not degrade the answer, it deletes it — the runtime reads
        "the agent did not handle this turn" as licence to fall through to a language
        model holding no evidence at all. So the failure is absorbed where it happens.
        """
        def explode(_counts: dict[str, int]) -> list[str]:
            raise KeyError("counts")

        original = cd._direction
        cd._direction = explode
        try:
            with self.assertLogs("services.undx_cross_domain", "ERROR"):
                reading = cd.build_cross_reading(
                    "activity.daily_summary",
                    _result("activity.daily_summary", _activity()))
            self.assertFalse(bool(reading))
        finally:
            cd._direction = original

    def test_a_non_dict_row_is_skipped_rather_than_fatal(self) -> None:
        result = _result("activity.daily_summary", [])
        result.records = ["not a record", None, *_activity()]  # type: ignore[list-item]
        reading = cd.build_cross_reading("activity.daily_summary", result)
        self.assertDeclared(reading)
        self.assertTrue(bool(reading))

    def test_every_named_kind_has_a_plural(self) -> None:
        """A missing plural renders "three post createds", which is how the response
        layer's own gap was found. Both directional buckets are covered here."""
        for kind in sorted(cd._PUBLISHED_KINDS | cd._RECEIVED_KINDS):
            with self.subTest(kind=kind):
                self.assertIn(kind, cd._ACTIVITY_NOUNS)

    def test_counts_above_twelve_still_declare_their_digits(self) -> None:
        """The spelled-number convention runs out at twelve, and the clause has to
        survive the boundary rather than quietly disappear one row later."""
        records = [_record("notification", f"Reply {i}", read=False)
                   for i in range(14)] + [_record("post_created", "You posted")]
        reading = self.build("activity.daily_summary", records)
        self.assertIn("14", reading.assessment)
        self.assertIn("14", reading.numbers)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
