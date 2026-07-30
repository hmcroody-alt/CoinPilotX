"""What each domain is allowed to say about its own records.

Batch 1's tests asked whether the response layer described the *shape* of a result
honestly. These ask a narrower and sharper question: given records that a real PulseSoc
domain service really emits, does the analyser read the fields that decide the answer,
and does it stop at the edge of what those fields support?

Two invariants are enforced against every analyser rather than written out once per
domain, because both are the kind of rule a later author breaks by adding one more
clause and never noticing:

* **Every digit is declared.** A clause carrying an undeclared number is dropped by
  :func:`build_reading`, silently, one clause at a time. The module's own convention is
  the only thing preventing that, so the convention is tested on all of them.
* **Absent is not zero.** An empty result yields :meth:`DomainReading.empty`, never an
  all-clear. A lookup that found nothing must not become a reassurance nobody verified.
"""

from __future__ import annotations

import unittest

from services.undx_agent_contracts import ToolResult
from services import undx_domain_reasoning as dm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _record(kind: str, title: str = "", **data: object) -> dict[str, object]:
    """One canonical record. Field names are taken from the real domain services.

    Deliberately built by hand rather than by calling
    ``undx_personal_intelligence_service``: these tests are about what the analyser does
    with a given contract, and importing the producer would make them pass whenever
    producer and consumer agreed with each other about the wrong thing.
    """
    return {
        "kind": kind,
        "title": title,
        "detail": "",
        "source": "pulsesoc",
        "timestamp": "2026-07-01T00:00:00Z",
        "confidence": "high",
        "data": dict(data),
    }


def _result(capability_id: str, records: list[dict[str, object]] | None = None,
            **data: object) -> ToolResult:
    return ToolResult(
        ok=True,
        tool_name=capability_id,
        capability_id=capability_id,
        records=list(records or []),
        data=dict(data),
    )


def _strings(reading: dm.DomainReading) -> list[str]:
    return [reading.assessment, *reading.interpretations, *reading.attention,
            *reading.next_steps, *reading.uncertainties]


class _ReadingAssertions(unittest.TestCase):

    def assertDeclared(self, reading: dm.DomainReading) -> None:
        """No emitted string carries a digit the reading did not declare."""
        for text in _strings(reading):
            for token in dm._DIGITS.findall(text):
                self.assertIn(
                    token, reading.numbers,
                    f"undeclared number {token!r} in {text!r} — build_reading would "
                    f"drop this clause silently")

    def assertSurvives(self, capability_id: str, records: list[dict[str, object]]
                       ) -> dm.DomainReading:
        """The reading built by the real entry point still has everything in it.

        Calling :func:`build_reading` rather than the analyser directly is the whole
        point of this helper: the analyser can produce a perfect clause and still lose
        it at the declaration check one frame later, and a test that only ever calls the
        analyser cannot see that happen.
        """
        direct = dm.ANALYSERS[capability_id](records)
        built = dm.build_reading(capability_id, _result(capability_id, records))
        self.assertEqual(len(direct.interpretations), len(built.interpretations))
        self.assertEqual(len(direct.attention), len(built.attention))
        self.assertEqual(len(direct.next_steps), len(built.next_steps))
        self.assertEqual(len(direct.uncertainties), len(built.uncertainties))
        self.assertEqual(bool(direct.assessment), bool(built.assessment))
        self.assertDeclared(built)
        return built


# ---------------------------------------------------------------------------
# Module-wide invariants
# ---------------------------------------------------------------------------


class ModuleInvariantTests(_ReadingAssertions):

    def test_no_analyser_reads_an_empty_domain_as_an_all_clear(self) -> None:
        """Absent is not zero, for all ten registered capabilities.

        The temptation is specific and strong: "no open tickets" reads as good news and
        "no account health items" reads as better news. Neither was verified — an empty
        list is equally consistent with a query that matched nothing and a filter that
        excluded everything — and the shape layer already says "there are none" from
        evidence it can point at.
        """
        for capability_id in dm.ANALYSERS:
            with self.subTest(capability=capability_id):
                reading = dm.build_reading(capability_id, _result(capability_id))
                self.assertFalse(bool(reading))
                self.assertEqual("", reading.assessment)

    def test_an_unregistered_capability_gets_no_reading(self) -> None:
        self.assertEqual("", dm.domain_for("notifications.preference.update"))
        self.assertFalse(bool(dm.build_reading(
            "notifications.preference.update",
            _result("notifications.preference.update", [_record("preference", "Alerts")]))))

    def test_a_raising_analyser_costs_the_reading_and_not_the_turn(self) -> None:
        """``build_reading`` is called past the gateway's point of no return.

        An exception here does not degrade the answer, it deletes it — and the runtime
        reads "the agent did not handle this turn" as licence to fall through to a
        language model that has no evidence at all. So the failure has to be absorbed
        where it happens.
        """
        def explode(_records: list[dict[str, object]]) -> dm.DomainReading:
            raise KeyError("data")

        original = dm.ANALYSERS["groups.list"]
        dm.ANALYSERS["groups.list"] = explode
        try:
            with self.assertLogs("services.undx_domain_reasoning", "ERROR"):
                reading = dm.build_reading(
                    "groups.list",
                    _result("groups.list", [_record("group", "Anything")]))
            self.assertFalse(bool(reading))
        finally:
            dm.ANALYSERS["groups.list"] = original

    def test_non_mapping_rows_are_discarded_rather_than_fatal(self) -> None:
        result = _result("groups.list", [])
        result.records = [None, "oops", 7, _record("group", "Real", viewer_role="owner")]
        reading = dm.build_reading("groups.list", result)
        self.assertTrue(bool(reading))
        self.assertIn("1", reading.numbers)

    def test_an_undeclared_number_is_dropped_and_logged(self) -> None:
        """The check itself, exercised directly, since every analyser passes it."""
        with self.assertLogs("services.undx_domain_reasoning", "WARNING"):
            kept = dm._strip_undeclared(["3 things happened"], frozenset({"5"}), "x")
        self.assertEqual((), kept)


# ---------------------------------------------------------------------------
# Per-domain readings
# ---------------------------------------------------------------------------


class AccountHealthTests(_ReadingAssertions):

    def test_a_restriction_outranks_strikes_and_warnings(self) -> None:
        """One restriction and three warnings is a restricted account, not four items."""
        reading = self.assertSurvives("account.health.summary", [
            _record("account_warning", "Community guidelines", status="open"),
            _record("account_warning", "Spam reminder", status="open"),
            _record("account_restriction", "Marketplace paused", status="active"),
            _record("account_warning", "Tag reminder", status="open"),
        ])
        self.assertIn("limiting your account", reading.assessment)
        self.assertIn("Marketplace paused", reading.assessment)
        self.assertIn("Marketplace paused", reading.attention)

    def test_strikes_without_restrictions_say_so_explicitly(self) -> None:
        reading = self.assertSurvives("account.health.summary", [
            _record("account_strike", "Strike 1", status="active"),
        ])
        self.assertIn("no restrictions", reading.assessment)
        self.assertIn("on record", reading.assessment)

    def test_a_resolved_history_is_not_a_current_state(self) -> None:
        """Rows the service left in for history must not be read as live enforcement."""
        reading = dm.build_reading("account.health.summary", _result(
            "account.health.summary", [
                _record("account_restriction", "Old", status="resolved"),
                _record("account_strike", "Older", status="expired"),
            ]))
        self.assertFalse(bool(reading))

    def test_an_expiry_date_makes_the_state_time_limited(self) -> None:
        reading = self.assertSurvives("account.health.summary", [
            _record("account_restriction", "Marketplace paused",
                    status="active", expires_at="2026-08-14"),
        ])
        self.assertTrue(any("time-limited" in text for text in reading.interpretations))
        self.assertIn("2026", reading.numbers)

    def test_an_unrecognised_kind_declines_instead_of_crashing(self) -> None:
        """The generic ``account_health`` kind, which the service really emits.

        Before the guard, this raised ``IndexError`` from inside the analyser.
        ``build_reading`` absorbed it exactly as designed, so nothing failed and nothing
        was logged where anyone would look — the reading simply vanished. It surfaced
        only when the module was wired into ``build_plan`` and the existing suite began
        printing tracebacks it was not failing on.
        """
        with self.assertNoLogs("services.undx_domain_reasoning", "ERROR"):
            reading = dm.build_reading("account.health.summary", _result(
                "account.health.summary", [
                    _record("account_health", "Something new", status="active"),
                ]))
        self.assertFalse(bool(reading))

    def test_it_never_predicts_a_review_outcome(self) -> None:
        reading = self.assertSurvives("account.health.summary", [
            _record("account_restriction", "Under appeal", status="under_review"),
        ])
        self.assertTrue(any("does not predict" in text
                            for text in reading.uncertainties))


class VerificationTests(_ReadingAssertions):

    def test_pending_is_stated_as_neither_granted_nor_refused(self) -> None:
        reading = self.assertSurvives("verification.status", [
            _record("verification_request", "Creator badge",
                    status="pending", verification_type="creator"),
        ])
        self.assertIn("under review", reading.assessment)
        self.assertTrue(any("neither been granted nor refused" in text
                            for text in reading.interpretations))

    def test_an_approved_request_names_what_was_approved(self) -> None:
        reading = self.assertSurvives("verification.status", [
            _record("verification_request", "Creator badge",
                    status="approved", verification_type="creator"),
        ])
        self.assertIn("creator", reading.assessment)
        self.assertIn("approved", reading.assessment)

    def test_a_newer_pending_request_supersedes_an_older_rejection(self) -> None:
        reading = self.assertSurvives("verification.status", [
            _record("verification_request", "Retry", status="pending"),
            _record("verification_request", "First try", status="rejected"),
        ])
        self.assertIn("under review", reading.assessment)
        self.assertTrue(any("history rather than the current state" in text
                            for text in reading.interpretations))

    def test_it_refuses_to_estimate_a_review_time(self) -> None:
        reading = self.assertSurvives("verification.status", [
            _record("verification_request", "Creator badge", status="pending"),
        ])
        self.assertTrue(any("does not publish a review time" in text
                            for text in reading.uncertainties))

    def test_an_unrecognised_status_produces_no_reading(self) -> None:
        """Better to say nothing than to sort an unknown state into a known bucket."""
        self.assertFalse(bool(dm.build_reading("verification.status", _result(
            "verification.status",
            [_record("verification_request", "?", status="quantum")]))))


class SupportTests(_ReadingAssertions):

    def test_it_separates_who_the_ticket_is_waiting_on(self) -> None:
        """Two queues with the same row count and opposite meanings."""
        reading = self.assertSurvives("support.tickets.list", [
            _record("support_ticket", "Payout missing",
                    status="awaiting_reply", issue_type="billing", priority="normal"),
            _record("support_ticket", "Login loop",
                    status="in_progress", issue_type="access", priority="normal"),
        ])
        self.assertIn("2 support tickets", reading.assessment)
        self.assertTrue(any("waiting on your" in text
                            for text in reading.interpretations))
        self.assertTrue(any("in your hands" in text
                            for text in reading.interpretations))

    def test_high_priority_is_reported_from_the_field_not_inferred(self) -> None:
        reading = self.assertSurvives("support.tickets.list", [
            _record("support_ticket", "Account locked",
                    status="open", issue_type="access", priority="urgent"),
        ])
        self.assertTrue(any("high priority" in text
                            for text in reading.interpretations))

    def test_a_fully_closed_queue_is_a_statement_about_tickets_only(self) -> None:
        """"None open" is supported by the rows; "you are fine" would not be."""
        reading = dm.build_reading("support.tickets.list", _result(
            "support.tickets.list", [
                _record("support_ticket", "Old", status="closed"),
                _record("support_ticket", "Older", status="resolved"),
            ]))
        self.assertEqual("none of your support tickets are still open",
                         reading.assessment)
        self.assertEqual((), reading.interpretations)

    def test_mixed_issue_types_are_named(self) -> None:
        reading = self.assertSurvives("support.tickets.list", [
            _record("support_ticket", "A", status="open", issue_type="billing"),
            _record("support_ticket", "B", status="open", issue_type="access"),
        ])
        self.assertTrue(any("not all the same issue" in text
                            for text in reading.interpretations))


class CreatorTests(_ReadingAssertions):

    def test_it_reports_the_engagement_figure_and_refuses_to_judge_it(self) -> None:
        """The sharpest boundary in the module.

        An average with no baseline supports exactly one sentence — what the average is.
        A verdict attached to a verified receipt is worse than the same verdict from a
        chat model, because the receipt vouches for it.
        """
        reading = self.assertSurvives("creator.analytics.summary", [
            _record("creator_analytics", "Last 30 days",
                    content_count=12, reel_count=4, status_count=0,
                    average_engagement_score=4.5, average_completion_rate=0.62),
        ])
        self.assertIn("12 posts", reading.assessment)
        self.assertIn("4 Reels", reading.assessment)
        self.assertNotIn("status", reading.assessment)
        joined = " ".join(_strings(reading)).lower()
        for verdict in ("good", "strong", "poor", "low engagement", "improved",
                        "better", "worse", "healthy", "should be"):
            self.assertNotIn(verdict, joined)
        self.assertTrue(any("nothing to compare them against" in text
                            for text in reading.uncertainties))

    def test_an_empty_window_is_a_fact_about_the_window(self) -> None:
        reading = self.assertSurvives("creator.analytics.summary", [
            _record("creator_analytics", "Last 30 days",
                    content_count=0, reel_count=0, status_count=0),
        ])
        self.assertIn("nothing was published", reading.assessment)
        self.assertTrue(any("not about the account" in text
                            for text in reading.interpretations))

    def test_an_undeclared_metric_column_is_not_narrated(self) -> None:
        """A new column must not start being described under a machine-made name."""
        reading = self.assertSurvives("creator.analytics.summary", [
            _record("creator_analytics", "Last 30 days",
                    content_count=3, marketplace_listing_count=9),
        ])
        self.assertNotIn("9", reading.assessment)
        self.assertNotIn("marketplace", " ".join(_strings(reading)).lower())


class MusicTests(_ReadingAssertions):

    def test_licensing_is_the_answer_not_the_result_count(self) -> None:
        reading = self.assertSurvives("music.search", [
            _record("music_track", "Sunrise", is_creator_safe=True,
                    commercial_use_allowed=True, attribution_required=True),
            _record("music_track", "Nightfall", is_creator_safe=True,
                    commercial_use_allowed=False, attribution_required=False),
            _record("music_track", "Drift", is_creator_safe=False,
                    commercial_use_allowed=False, attribution_required=False),
        ])
        self.assertIn("1 track of 3", reading.assessment)
        self.assertIn("commercial", reading.assessment)
        self.assertTrue(any("attribution" in text
                            for text in reading.interpretations))

    def test_a_missing_licence_field_is_unknown_and_not_permission(self) -> None:
        """The distinction that keeps a creator out of a copyright claim."""
        reading = self.assertSurvives("music.search", [
            _record("music_track", "Mystery"),
            _record("music_track", "Known", is_creator_safe=True,
                    commercial_use_allowed=True),
        ])
        self.assertTrue(any("did not say rather than that the answer is no" in text
                            for text in reading.uncertainties))

    def test_nothing_cleared_is_said_plainly(self) -> None:
        """An explicit no from the catalogue is information, not the absence of it.

        The regression: the decline guard tested whether any *positive* licence list was
        non-empty, so a catalogue that described every track and refused every track
        looked identical to a catalogue that described none. The reading was dropped
        entirely, and the creator was handed a list of tracks they may not use with no
        indication that they may not use them.
        """
        reading = self.assertSurvives("music.search", [
            _record("music_track", "A", is_creator_safe=False,
                    commercial_use_allowed=False),
            _record("music_track", "B", is_creator_safe=False,
                    commercial_use_allowed=False),
        ])
        self.assertIn("none of these 2 tracks", reading.assessment)

    def test_a_catalogue_that_described_nothing_produces_no_reading(self) -> None:
        """The case the decline guard is actually for, kept working alongside the fix."""
        self.assertFalse(bool(dm.build_reading("music.search", _result(
            "music.search",
            [_record("music_track", "A"), _record("music_track", "B")]))))

    def test_creator_safe_and_commercial_are_not_treated_as_the_same_permission(self) -> None:
        reading = self.assertSurvives("music.search", [
            _record("music_track", "A", is_creator_safe=True,
                    commercial_use_allowed=True),
            _record("music_track", "B", is_creator_safe=True,
                    commercial_use_allowed=False),
        ])
        self.assertTrue(any("separate permissions" in text
                            for text in reading.interpretations))


class GroupTests(_ReadingAssertions):

    def test_standing_is_read_not_the_row_count(self) -> None:
        reading = self.assertSurvives("groups.list", [
            _record("group", "Alpha", viewer_role="owner"),
            _record("group", "Beta", viewer_role="member"),
            _record("group", "Gamma", viewer_role="member"),
        ])
        self.assertIn("you help run 1 group of the 3", reading.assessment)
        self.assertIn("Alpha", reading.attention)

    def test_a_group_name_containing_a_digit_survives(self) -> None:
        """The regression that motivated declaring ``attention``.

        Group names are user-authored, and user-authored text contains numbers far more
        often than schema fields do. Before ``attention`` was declared, "Web3 Builders"
        was dropped by the declaration check — silently, so the person asking which
        groups they run got the count and never got the names.
        """
        reading = self.assertSurvives("groups.list", [
            _record("group", "Web3 Builders", viewer_role="admin"),
            _record("group", "Cohort 4", viewer_role="moderator"),
        ])
        self.assertIn("Web3 Builders", reading.attention)
        self.assertIn("Cohort 4", reading.attention)

    def test_mixed_roles_are_broken_out(self) -> None:
        reading = self.assertSurvives("groups.search", [
            _record("group", "A", viewer_role="owner"),
            _record("group", "B", viewer_role="member"),
        ])
        self.assertTrue(any("not the same in each" in text
                            for text in reading.interpretations))

    def test_groups_without_a_role_field_produce_no_reading(self) -> None:
        """A search result the viewer does not belong to has no standing to report."""
        self.assertFalse(bool(dm.build_reading("groups.search", _result(
            "groups.search", [_record("group", "Public group")]))))


class EventTests(_ReadingAssertions):

    def test_a_cancelled_event_is_not_something_to_plan_around(self) -> None:
        reading = self.assertSurvives("events.upcoming", [
            _record("event", "Launch party", status="cancelled",
                    starts_at="2026-08-02T18:00:00Z"),
            _record("event", "Standup", status="scheduled",
                    starts_at="2026-08-05T09:00:00Z"),
        ])
        self.assertIn("Standup", reading.assessment)
        self.assertTrue(any("larger than the number you can attend" in text
                            for text in reading.interpretations))
        self.assertIn("Launch party", reading.attention)

    def test_the_soonest_live_event_is_the_lead(self) -> None:
        reading = self.assertSurvives("events.upcoming", [
            _record("event", "Later", status="scheduled",
                    starts_at="2026-09-01T10:00:00Z"),
            _record("event", "Sooner", status="scheduled",
                    starts_at="2026-08-01T10:00:00Z"),
        ])
        self.assertIn("Sooner", reading.assessment)
        self.assertNotIn("Later", reading.assessment)

    def test_an_entirely_cancelled_list_says_so(self) -> None:
        reading = self.assertSurvives("events.upcoming", [
            _record("event", "One", status="cancelled"),
            _record("event", "Two", status="postponed"),
        ])
        self.assertIn("cancelled or postponed", reading.assessment)


class LocalizationTests(_ReadingAssertions):

    def test_unset_is_distinguished_from_set(self) -> None:
        """"Wrong language" is nearly always an unset preference, not a wrong one."""
        reading = self.assertSurvives("localization.preferences", [
            _record("region_preference", "Region", locale="en_gb"),
        ])
        self.assertIn("en_gb", reading.assessment)
        self.assertTrue(any("still on the PulseSoc default" in text
                            for text in reading.interpretations))

    def test_nothing_configured_is_reported_as_defaults(self) -> None:
        reading = self.assertSurvives("localization.preferences", [
            _record("region_preference", "Region"),
        ])
        self.assertIn("using its defaults", reading.assessment)
        self.assertTrue(reading.next_steps)

    def test_automatic_source_detection_is_named(self) -> None:
        reading = self.assertSurvives("localization.preferences", [
            _record("region_preference", "Region", locale="fr_fr",
                    time_zone="europe/paris", currency="eur"),
            _record("translation_preference", "Translation",
                    target_language="fr", source_language="auto"),
        ])
        self.assertTrue(any("detected automatically" in text
                            for text in reading.interpretations))
        self.assertEqual((), tuple(t for t in reading.interpretations
                                   if "still on the PulseSoc default" in t))


class PresenceTests(_ReadingAssertions):

    def test_invisible_mode_overrides_the_audience_setting(self) -> None:
        """Three booleans answer one question, and they do not answer it independently."""
        reading = self.assertSurvives("presence.privacy.status", [
            _record("presence_privacy", "Presence", invisible_mode=True,
                    hide_last_seen=False, presence_privacy="friends"),
        ])
        self.assertIn("nobody sees you as online", reading.assessment)
        self.assertTrue(any("overrides" in text for text in reading.interpretations))

    def test_last_seen_is_named_as_a_separate_setting(self) -> None:
        reading = self.assertSurvives("presence.privacy.status", [
            _record("presence_privacy", "Presence", invisible_mode=False,
                    hide_last_seen=False, presence_privacy="everyone"),
        ])
        self.assertIn("everyone", reading.assessment)
        self.assertTrue(any("separate setting" in text
                            for text in reading.interpretations))

    def test_a_record_with_no_presence_fields_produces_no_reading(self) -> None:
        self.assertFalse(bool(dm.build_reading("presence.privacy.status", _result(
            "presence.privacy.status", [_record("presence_privacy", "Presence")]))))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
