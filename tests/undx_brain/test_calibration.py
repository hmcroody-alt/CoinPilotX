"""What the calibration reader must not do, held against it by running it.

The module's whole value is that it declines to say things a count would let it say, so
almost every test here is of a refusal rather than of a result. The four the docstring
names — silence is not approval, ``not_helpful`` is not ``wrong``, a rate without an
interval is not a measurement, and observing is not steering — each get a class.

One test is different in kind: :meth:`TheFloorIsDerivedAndNotChosen.
test_min_judged_is_the_first_n_whose_widest_interval_is_narrow_enough` recomputes the
sweep that produced :data:`~services.undx_brain.calibration.MIN_JUDGED` instead of
asserting that it equals twelve. Asserting the number would pass forever and would tell
nobody why it is that number; recomputing it fails the day the constant and its stated
justification stop agreeing, which is the only failure worth catching about a constant.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.undx_brain import calibration as c  # noqa: E402
from services.undx_brain import learning as L  # noqa: E402

#: Calibration needs its own flag, the Brain flag, the learning reader that supplies the
#: window, and facts ageing to place the result in time. All four, because the module
#: refuses to conclude anything it cannot date.
ON = {
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_BRAIN_CALIBRATION_ENABLED": "1",
    "UNDX_BRAIN_LEARNING_ENABLED": "1",
    "UNDX_BRAIN_FACTS_ENABLED": "1",
}

NOW = "2026-08-01T12:00:00Z"


def _event(seq: int, event_type: str, at: str, **metadata) -> L.Event:
    return L.Event(
        event_id=seq,
        owner_id=7,
        event_type=event_type,
        source="test",
        metadata=metadata,
        created_at=at,
    )


def _window(events) -> L.Window:
    events = list(events)
    stamps = sorted(item.created_at for item in events if item.created_at)
    return L.Window(
        ok=True,
        owner_id=7,
        events=tuple(events),
        first_at=stamps[0] if stamps else "",
        last_at=stamps[-1] if stamps else "",
    )


def _answers(*ratings, capability="cap.a", claim="agent_action", start=0):
    """One claim per rating, each rated as given. ``None`` means nobody rated it.

    Message ids start at ``start + 1`` so two calls can be concatenated into one window
    without their ids colliding — a collision would silently join one capability's
    feedback to another's answer, which is precisely the bug the join rules exist to
    prevent and would be an embarrassing way to introduce it in the tests.
    """
    out = []
    seq = start * 10
    for offset, rating in enumerate(ratings):
        message = start + offset + 1
        seq += 1
        out.append(
            _event(
                seq, claim, f"2026-08-01T10:{offset:02d}:00Z",
                message_id=message, capability_id=capability,
            )
        )
        if rating is not None:
            seq += 1
            out.append(
                _event(
                    seq, "feedback_recorded", f"2026-08-01T10:{offset:02d}:30Z",
                    message_id=message, rating=rating,
                )
            )
    return out


def _repeat(rating, times, **kwargs):
    return _answers(*([rating] * times), **kwargs)


class TheFloorIsDerivedAndNotChosen(unittest.TestCase):
    """MIN_JUDGED has an argument behind it, and the argument is checked."""

    def test_min_judged_is_the_first_n_whose_widest_interval_is_narrow_enough(self):
        def widest(n: int) -> float:
            return max(c.interval(k, n).half_width for k in range(n + 1))

        self.assertLessEqual(
            widest(c.MIN_JUDGED), c.WIDEST_USEFUL_HALF_WIDTH,
            "MIN_JUDGED admits a sample whose worst-case interval is too wide to "
            "distinguish anything",
        )
        self.assertGreater(
            widest(c.MIN_JUDGED - 1), c.WIDEST_USEFUL_HALF_WIDTH,
            "MIN_JUDGED is higher than it needs to be; one fewer would still fit "
            "inside the half-width, so the constant is no longer the first n and its "
            "stated derivation is wrong",
        )

    def test_the_interval_is_wilson_and_not_the_normal_approximation(self):
        # The reason for the choice, stated as a test: twelve out of twelve is not
        # certainty, and the normal approximation says it is. If this ever passes with
        # a zero-width interval, the floor above stops meaning anything.
        perfect = c.interval(12, 12)
        self.assertEqual(perfect.point, 1.0)
        self.assertLess(perfect.low, 1.0)
        self.assertGreater(perfect.half_width, 0.0)
        # And the symmetric case: nought out of twelve is not impossibility.
        none = c.interval(0, 12)
        self.assertEqual(none.point, 0.0)
        self.assertGreater(none.high, 0.0)

    def test_the_interval_never_leaves_the_unit_range_or_divides_by_zero(self):
        for successes, n in ((0, 0), (5, 0), (-3, 10), (99, 10), (0, 1), (1, 1)):
            with self.subTest(successes=successes, n=n):
                band = c.interval(successes, n)
                self.assertGreaterEqual(band.low, 0.0)
                self.assertLessEqual(band.high, 1.0)
                self.assertLessEqual(band.low, band.high)
                self.assertGreaterEqual(band.point, 0.0)
                self.assertLessEqual(band.point, 1.0)

    def test_an_empty_sample_is_the_whole_range_and_is_never_useful(self):
        band = c.interval(0, 0)
        self.assertEqual((band.low, band.high), (0.0, 1.0))
        self.assertFalse(band.useful)

    def test_reaching_the_floor_is_enough_to_make_the_interval_useful(self):
        # The redundancy is deliberate: ``conclusive`` checks both the floor and the
        # width, and this is the assertion that the second check is currently implied
        # by the first. It fails if MIN_JUDGED is ever lowered without the half-width
        # being reconsidered, which is exactly when the second check earns its keep.
        for successes in range(c.MIN_JUDGED + 1):
            with self.subTest(successes=successes):
                self.assertTrue(c.interval(successes, c.MIN_JUDGED).useful)


class SilenceIsNotApproval(unittest.TestCase):
    """The unjudged are the number that decides what the rate is worth."""

    def test_an_unrated_answer_is_unjudged_and_not_approved(self):
        window = _window(_answers("helpful", None, None))
        verdicts = [item.verdict for item in c.pair(window, env=ON)]
        self.assertEqual(
            verdicts,
            [c.Verdict.APPROVED, c.Verdict.UNJUDGED, c.Verdict.UNJUDGED],
        )

    def test_the_unjudged_are_outside_the_denominator_entirely(self):
        judged_only = c.calibrate(
            _window(_repeat("helpful", 9) + _repeat("wrong", 3, start=9)),
            now=NOW, env=ON,
        )
        with_silence = c.calibrate(
            _window(
                _repeat("helpful", 9)
                + _repeat("wrong", 3, start=9)
                + _repeat(None, 200, start=12)
            ),
            now=NOW, env=ON,
        )
        self.assertEqual(judged_only.judged, with_silence.judged)
        self.assertEqual(judged_only.approved, with_silence.approved)
        self.assertEqual(
            judged_only.interval.point, with_silence.interval.point,
            "two hundred unrated answers moved the correctness rate; silence is being "
            "counted as something",
        )
        self.assertEqual(with_silence.unjudged, 200)

    def test_the_sentence_cannot_be_quoted_without_the_unjudged_count(self):
        result = c.calibrate(
            _window(
                _repeat("helpful", 12) + _repeat(None, 88, start=12)
            ),
            now=NOW, env=ON,
        )
        self.assertTrue(result.conclusive)
        self.assertIn("88 more went unjudged", result.answer)
        self.assertIn("12%", result.answer)  # the coverage, stated in the same breath
        self.assertAlmostEqual(result.coverage, 12 / 100)

    def test_coverage_is_never_confused_with_the_correctness_rate(self):
        result = c.calibrate(
            _window(_repeat("wrong", 12) + _repeat(None, 12, start=12)),
            now=NOW, env=ON,
        )
        self.assertAlmostEqual(result.coverage, 0.5)
        self.assertEqual(result.interval.point, 0.0)
        self.assertNotEqual(result.coverage, result.interval.point)


class NotHelpfulIsNotWrong(unittest.TestCase):
    """Three ratings claim the answer was incorrect. One claims it did not help."""

    def test_every_rating_the_service_accepts_has_a_verdict(self):
        service = (ROOT / "services" / "pulse_ai_service.py").read_text(encoding="utf-8")
        self.assertIn(
            'if rating not in {"helpful", "not_helpful", "wrong", "unsafe", "outdated"}:',
            service,
            "the service's rating allowlist has changed shape; RATINGS was copied from "
            "it and is now a guess",
        )
        self.assertEqual(
            set(c.RATINGS),
            {"helpful", "not_helpful", "wrong", "unsafe", "outdated"},
        )

    def test_not_helpful_is_in_neither_column(self):
        window = _window(_answers("not_helpful"))
        item = c.pair(window, env=ON)[0]
        self.assertIs(item.verdict, c.Verdict.UNHELPFUL)
        self.assertTrue(item.judged, "a not_helpful rating is a judgement")
        self.assertFalse(
            item.verdict.counts_toward_correctness,
            "not_helpful is being counted in a rate about correctness",
        )

    def test_a_flood_of_not_helpful_moves_neither_the_rate_nor_the_floor(self):
        clean = c.calibrate(
            _window(_repeat("helpful", 12)), now=NOW, env=ON,
        )
        noisy = c.calibrate(
            _window(_repeat("helpful", 12) + _repeat("not_helpful", 50, start=12)),
            now=NOW, env=ON,
        )
        self.assertEqual(clean.judged, noisy.judged)
        self.assertEqual(clean.approved, noisy.approved)
        self.assertEqual(clean.corrected, noisy.corrected)
        self.assertEqual(clean.interval.point, noisy.interval.point)
        self.assertEqual(noisy.unhelpful, 50)
        self.assertTrue(
            any("not_helpful" in note for note in noisy.notes),
            "fifty complaints were excluded from the denominator without saying so",
        )

    def test_not_helpful_alone_never_reaches_the_floor(self):
        result = c.calibrate(_window(_repeat("not_helpful", 40)), now=NOW, env=ON)
        self.assertTrue(result.ok)
        self.assertFalse(
            result.conclusive,
            "forty complaints produced a correctness rate; none of them was about "
            "correctness",
        )
        self.assertEqual(result.judged, 0)

    def test_unsafe_survives_being_aggregated(self):
        result = c.calibrate(
            _window(_repeat("helpful", 30) + _answers("unsafe", start=30)),
            now=NOW, env=ON,
        )
        self.assertEqual(result.severe, 1)
        self.assertEqual(result.corrected, 1)
        self.assertIn("1 was rated unsafe", result.answer)
        self.assertNotIn("1 were rated unsafe", result.answer)
        self.assertTrue(any("unsafe" in note for note in result.notes))

    def test_the_raw_rating_survives_the_verdict(self):
        window = _window(_answers("unsafe", "outdated", "wrong"))
        pairs = c.pair(window, env=ON)
        self.assertEqual(
            [item.verdict for item in pairs], [c.Verdict.CORRECTED] * 3,
            "the three are one verdict",
        )
        self.assertEqual(
            [item.rating for item in pairs], ["unsafe", "outdated", "wrong"],
            "...and the three are still tellable apart, which is why rating is kept",
        )
        self.assertEqual([item.severe for item in pairs], [True, False, False])

    def test_an_unrecognised_rating_is_not_read_as_approval(self):
        for junk in ("", "great", "HELPFUL!", "1", None, True, ["helpful"]):
            with self.subTest(junk=junk):
                window = _window(_answers("helpful"))
                events = list(window.events)
                events[1] = _event(
                    2, "feedback_recorded", "2026-08-01T10:00:30Z",
                    message_id=1, rating=junk,
                )
                item = c.pair(_window(events), env=ON)[0]
                self.assertIs(
                    item.verdict, c.Verdict.UNJUDGED,
                    f"the rating {junk!r} was read as a judgement",
                )

    def test_the_two_recognised_spellings_of_helpful_are_the_ones_the_service_writes(self):
        # ``record_feedback`` lowercases and replaces spaces before storing, so the
        # stored value is already normalised — but reading is done on rows that may
        # predate that normalisation, so the reader does it too rather than assuming.
        window = _window(_answers("Not Helpful"))
        self.assertIs(c.pair(window, env=ON)[0].verdict, c.Verdict.UNHELPFUL)


class ARateWithoutAnIntervalIsNotAMeasurement(unittest.TestCase):

    def test_nothing_is_concluded_below_the_floor(self):
        for n in range(0, c.MIN_JUDGED):
            with self.subTest(n=n):
                result = c.calibrate(_window(_repeat("helpful", n)), now=NOW, env=ON)
                self.assertTrue(result.ok, "the call itself succeeded")
                self.assertFalse(
                    result.conclusive,
                    f"{n} judged answers produced a conclusion",
                )
                self.assertIn(str(c.MIN_JUDGED), result.reason)

    def test_the_floor_is_the_first_n_at_which_something_is_concluded(self):
        result = c.calibrate(
            _window(_repeat("helpful", c.MIN_JUDGED)), now=NOW, env=ON,
        )
        self.assertTrue(result.conclusive)

    def test_truthiness_is_conclusive_and_not_ok(self):
        result = c.calibrate(_window(_repeat("helpful", 4)), now=NOW, env=ON)
        self.assertTrue(result.ok)
        self.assertFalse(
            bool(result),
            "a Calibration that was computed correctly over four ratings is truthy; a "
            "caller checking it reports a coin flip as a measurement",
        )

    def test_the_answer_always_carries_the_interval_and_never_a_bare_percentage(self):
        result = c.calibrate(
            _window(_repeat("helpful", 9) + _repeat("wrong", 3, start=9)),
            now=NOW, env=ON,
        )
        self.assertTrue(result.conclusive)
        self.assertIn("95% CI", result.answer)
        self.assertIn(f"n={result.judged}", result.answer)

    def test_a_result_that_cannot_be_placed_in_time_is_reported_and_not_concluded(self):
        undated = dict(ON)
        undated["UNDX_BRAIN_FACTS_ENABLED"] = "0"
        result = c.calibrate(_window(_repeat("helpful", 30)), now=NOW, env=undated)
        self.assertTrue(result.ok)
        self.assertFalse(result.conclusive)
        self.assertIn("could not be established", result.answer)

    def test_the_scope_of_a_capability_is_judged_on_its_own_evidence(self):
        window = _window(
            _repeat("helpful", 30, capability="cap.a")
            + _repeat("wrong", 3, capability="cap.b", start=30)
        )
        whole = c.calibrate(window, now=NOW, env=ON)
        self.assertTrue(whole.conclusive)
        narrow = c.calibrate(window, capability_id="cap.b", now=NOW, env=ON)
        self.assertFalse(
            narrow.conclusive,
            "three ratings became conclusive because the account around them had "
            "thirty-three",
        )
        self.assertEqual(narrow.judged, 3)

    def test_by_capability_returns_the_inconclusive_ones_rather_than_hiding_them(self):
        window = _window(
            _repeat("helpful", 30, capability="cap.a")
            + _repeat("wrong", 3, capability="cap.b", start=30)
        )
        results = c.by_capability(window, now=NOW, env=ON)
        self.assertEqual([item.scope for item in results], ["cap.b", "cap.a"])
        self.assertFalse(results[0].conclusive)
        self.assertTrue(results[1].conclusive)

    def test_by_capability_is_ordered_by_corrections_and_not_by_rate(self):
        window = _window(
            # cap.a: one correction out of one — a 0% rate, and one event.
            _answers("wrong", capability="cap.a")
            # cap.b: twelve corrections out of forty — a better rate, more corrections.
            + _repeat("wrong", 12, capability="cap.b", start=10)
            + _repeat("helpful", 28, capability="cap.b", start=30)
        )
        results = c.by_capability(window, now=NOW, env=ON)
        self.assertEqual(
            [item.scope for item in results], ["cap.b", "cap.a"],
            "a capability with one bad rating sorted above one with twelve; the order "
            "is by rate, which puts every tiny sample at the top",
        )


class TheJoinRefusesToGuess(unittest.TestCase):
    """Three rules, each because the obvious version of it is wrong."""

    def test_feedback_that_predates_the_answer_does_not_judge_it(self):
        events = [
            _event(1, "feedback_recorded", "2026-08-01T09:00:00Z",
                   message_id=4, rating="wrong"),
            _event(2, "agent_action", "2026-08-01T10:00:00Z",
                   message_id=4, capability_id="cap.a"),
        ]
        item = c.pair(_window(events), env=ON)[0]
        self.assertIs(item.verdict, c.Verdict.UNJUDGED)
        self.assertTrue(
            any("predate" in note for note in item.notes),
            "the row was dropped without saying so",
        )

    def test_only_the_newest_rating_of_a_message_counts(self):
        events = [
            _event(1, "agent_action", "2026-08-01T10:00:00Z",
                   message_id=1, capability_id="cap.a"),
            _event(2, "feedback_recorded", "2026-08-01T10:01:00Z",
                   message_id=1, rating="wrong"),
            _event(3, "feedback_recorded", "2026-08-01T10:02:00Z",
                   message_id=1, rating="helpful"),
        ]
        pairs = c.pair(_window(events), env=ON)
        self.assertEqual(len(pairs), 1, "one answer became two by being rated twice")
        self.assertIs(pairs[0].verdict, c.Verdict.APPROVED)
        self.assertTrue(
            any("more than once" in note for note in pairs[0].notes),
            "a mind that was changed was counted silently",
        )

    def test_a_repeated_rating_that_agrees_is_not_reported_as_a_disagreement(self):
        events = [
            _event(1, "agent_action", "2026-08-01T10:00:00Z", message_id=1),
            _event(2, "feedback_recorded", "2026-08-01T10:01:00Z",
                   message_id=1, rating="helpful"),
            _event(3, "feedback_recorded", "2026-08-01T10:02:00Z",
                   message_id=1, rating="helpful"),
        ]
        item = c.pair(_window(events), env=ON)[0]
        self.assertFalse(
            any("disagree" in note for note in item.notes),
            "two identical ratings were reported as a change of mind",
        )

    def test_a_missing_message_id_joins_to_nothing_rather_than_to_everything(self):
        events = [
            _event(1, "agent_action", "2026-08-01T10:00:00Z", capability_id="cap.a"),
            _event(2, "agent_action", "2026-08-01T10:01:00Z",
                   message_id=0, capability_id="cap.b"),
            _event(3, "feedback_recorded", "2026-08-01T10:02:00Z",
                   message_id=0, rating="helpful"),
        ]
        pairs = c.pair(_window(events), env=ON)
        self.assertEqual(len(pairs), 2)
        for item in pairs:
            with self.subTest(capability=item.capability_id):
                self.assertIs(
                    item.verdict, c.Verdict.UNJUDGED,
                    "an unlabelled claim was joined to an unlabelled verdict; every "
                    "zero matches every other zero",
                )
                self.assertEqual(item.message_id, "")

    def test_the_same_id_written_as_a_number_and_as_text_is_one_message(self):
        events = [
            _event(1, "agent_action", "2026-08-01T10:00:00Z", message_id=7),
            _event(2, "feedback_recorded", "2026-08-01T10:01:00Z",
                   message_id="7", rating="helpful"),
        ]
        self.assertIs(c.pair(_window(events), env=ON)[0].verdict, c.Verdict.APPROVED)

    def test_a_boolean_message_id_is_refused_rather_than_read_as_one(self):
        # ``True`` is an int in Python and would join to message 1, which belongs to
        # somebody. The same trap ``learning._bounded`` documents.
        events = [
            _event(1, "agent_action", "2026-08-01T10:00:00Z", message_id=1),
            _event(2, "feedback_recorded", "2026-08-01T10:01:00Z",
                   message_id=True, rating="wrong"),
        ]
        self.assertIs(c.pair(_window(events), env=ON)[0].verdict, c.Verdict.UNJUDGED)

    def test_both_kinds_of_claim_are_read_and_only_those_two(self):
        events = [
            _event(1, "agent_action", "2026-08-01T10:00:00Z", message_id=1),
            _event(2, "message_answered", "2026-08-01T10:01:00Z", message_id=2),
            _event(3, "memory_corrected", "2026-08-01T10:02:00Z", memory_id=9),
            _event(4, "provider_failure", "2026-08-01T10:03:00Z", message_id=3),
            _event(5, "settings_updated", "2026-08-01T10:04:00Z", keys=["a"]),
        ]
        pairs = c.pair(_window(events), env=ON)
        self.assertEqual(
            [item.claim_event for item in pairs],
            ["agent_action", "message_answered"],
        )

    def test_a_corrected_memory_is_not_silently_attributed_to_an_answer(self):
        # The stated limitation, pinned. ``memory_corrected`` carries memory_id and no
        # message_id, so it must reach nothing here — and the module must say so rather
        # than let the absence look like an absence of corrections.
        events = [
            _event(1, "agent_action", "2026-08-01T10:00:00Z", message_id=5),
            _event(2, "memory_corrected", "2026-08-01T10:01:00Z", memory_id=5),
        ]
        item = c.pair(_window(events), env=ON)[0]
        self.assertIs(
            item.verdict, c.Verdict.UNJUDGED,
            "a memory id was joined to a message id because both happened to be 5",
        )
        self.assertIn("memory_corrected", c.__doc__ or "")

    def test_the_claim_order_of_the_window_is_preserved(self):
        window = _window(_answers("helpful", "wrong", None))
        self.assertEqual(
            [item.message_id for item in c.pair(window, env=ON)], ["1", "2", "3"],
        )


class ObservingIsNotSteering(unittest.TestCase):
    """The refusal that would be invisible if it were not tested."""

    #: Everything a module that had started acting on what it observed would have to
    #: reach for. ``selection`` is the one the docstring names; the rest are the other
    #: ways the same thing could be done without importing it.
    FORBIDDEN = (
        "selection",
        "prediction",
        "goals",
        "undx_tool_gateway",
        "undx_capability_registry",
        "undx_agent_runtime",
        "undx_architecture",
        "sqlite3",
    )

    def _source(self) -> str:
        return (ROOT / "services" / "undx_brain" / "calibration.py").read_text(
            encoding="utf-8"
        )

    def test_calibration_imports_nothing_it_could_steer_with(self):
        source = self._source()
        lines = [
            line.strip() for line in source.splitlines()
            if line.startswith(("import ", "from "))
        ]
        for name in self.FORBIDDEN:
            with self.subTest(name=name):
                self.assertFalse(
                    [line for line in lines if name in line],
                    f"calibration imports {name}; observing has become steering",
                )

    def test_nothing_imports_calibration_either(self):
        # Nothing on the live path calls it, which is what the flag being off means in
        # practice and what the foundation entry claims.
        import re

        package = ROOT / "services" / "undx_brain"
        relative = re.compile(
            r"^\s*(from\s+\.\s+import\s+.*\bcalibration\b"
            r"|from\s+\.calibration\s+import\b)",
            re.MULTILINE,
        )
        absolute = re.compile(
            r"^\s*(from\s+services\.undx_brain\s+import\s+.*\bcalibration\b"
            r"|import\s+services\.undx_brain\.calibration\b)",
            re.MULTILINE,
        )
        found = []
        for path in (ROOT / "services").rglob("*.py"):
            if path.name == "calibration.py":
                continue
            text = path.read_text(encoding="utf-8")
            if absolute.search(text) or (package in path.parents and relative.search(text)):
                found.append(path.stem)
        self.assertEqual(sorted(found), [])

    def test_it_opens_no_connection_and_reads_through_the_owner_scoped_layer(self):
        source = self._source()
        self.assertNotIn("cur.execute", source)
        self.assertNotIn("SELECT", source)
        self.assertIn("from . import learning", source)

    def test_it_writes_nothing_at_all(self):
        source = self._source()
        for verb in ("INSERT", "UPDATE", "DELETE", "commit()"):
            with self.subTest(verb=verb):
                self.assertNotIn(
                    verb, source,
                    "the reader of the record has started writing to it",
                )

    def test_nothing_it_returns_carries_an_instruction(self):
        # A field named like an action is how "observing is not steering" would erode:
        # not by an import, but by a boolean the caller is expected to obey.
        for shape in (c.Calibration, c.Answer):
            with self.subTest(shape=shape.__name__):
                names = set(shape.__dataclass_fields__)
                for banned in (
                    "disable", "suppress", "penalty", "weight", "rank",
                    "should_retry", "recommend", "action",
                ):
                    self.assertNotIn(banned, names)


class TheFlagGatesEverything(unittest.TestCase):

    def test_off_by_default_and_off_without_the_brain_flag(self):
        window = _window(_repeat("helpful", 30))
        for env in (
            {},
            {"UNDX_BRAIN_CALIBRATION_ENABLED": "1"},
            {"UNDX_BRAIN_ENABLED": "1"},
        ):
            with self.subTest(env=env):
                self.assertEqual(c.pair(window, env=env), ())
                result = c.calibrate(window, now=NOW, env=env)
                self.assertFalse(result.ok)
                self.assertFalse(result.conclusive)
                self.assertIn("disabled", result.reason)
                self.assertEqual(c.by_capability(window, now=NOW, env=env), ())

    def test_a_disabled_reader_is_never_mistaken_for_a_clean_bill_of_health(self):
        result = c.calibrate(_window(_repeat("wrong", 40)), now=NOW, env={})
        self.assertFalse(result.ok)
        self.assertEqual(result.answer, "", "a disabled reader produced a sentence")
        self.assertEqual(result.judged, 0)
        self.assertEqual(result.corrected, 0)
        self.assertIsNone(result.interval)

    def test_the_flag_is_declared_fail_closed_with_an_off_default(self):
        from services.undx_brain import config as brain_config

        flag = next(
            item for item in brain_config.CATALOG
            if item.name == "UNDX_BRAIN_CALIBRATION_ENABLED"
        )
        self.assertEqual(flag.default, "0")
        self.assertEqual(flag.fail, "closed")
        for named in ("message_id", "feedback_recorded", "selection"):
            with self.subTest(named=named):
                self.assertIn(named, flag.purpose)

    def test_a_broken_window_is_refused_rather_than_treated_as_empty(self):
        for bad in (None, "window", 7, L.Window(ok=False, reason="the read failed")):
            with self.subTest(bad=bad):
                result = c.calibrate(bad, now=NOW, env=ON)
                self.assertFalse(result.ok)
                self.assertFalse(result.conclusive)
                self.assertTrue(result.reason)
                self.assertEqual(c.pair(bad, env=ON), ())

    def test_an_empty_window_concludes_nothing_and_says_why(self):
        result = c.calibrate(_window([_event(1, "settings_updated", NOW)]), now=NOW, env=ON)
        self.assertTrue(result.ok)
        self.assertFalse(result.conclusive)
        self.assertEqual(result.answers, 0)
        self.assertEqual(result.coverage, 0.0)


class TheNamesMeanWhatTheyClaim(unittest.TestCase):

    def test_everything_exported_exists(self):
        for name in c.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(c, name), f"calibration.__all__ names {name}")

    def test_the_join_key_is_spelled_once(self):
        source = (ROOT / "services" / "undx_brain" / "calibration.py").read_text(
            encoding="utf-8"
        )
        # Two occurrences: the constant's definition and its single use. A third is a
        # second spelling waiting to drift.
        self.assertEqual(
            source.count('"message_id"'), 1,
            "message_id is spelled as a literal somewhere other than JOIN_KEY",
        )

    def test_the_verdicts_partition_into_correctness_and_not(self):
        counted = {v for v in c.Verdict if v.counts_toward_correctness}
        self.assertEqual(counted, {c.Verdict.APPROVED, c.Verdict.CORRECTED})
        self.assertEqual(
            {v for v in c.Verdict} - counted,
            {c.Verdict.UNHELPFUL, c.Verdict.UNJUDGED},
        )

    def test_every_rating_maps_onto_a_real_verdict(self):
        for rating, name in c.RATINGS.items():
            with self.subTest(rating=rating):
                self.assertIn(name, {v.value for v in c.Verdict})
                self.assertIsNot(
                    c.Verdict(name), c.Verdict.UNJUDGED,
                    "a rating that was given maps to the verdict for not being rated",
                )

    def test_severe_ratings_are_a_subset_of_the_corrections(self):
        for rating in c.SEVERE_RATINGS:
            with self.subTest(rating=rating):
                self.assertEqual(c.RATINGS.get(rating), "corrected")


if __name__ == "__main__":
    unittest.main()
