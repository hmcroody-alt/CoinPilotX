"""Two candidates are held at once, compared, and sometimes refused.

The test carrying the most weight is
:meth:`TheRankingIsTheMatchersOwn.test_the_top_of_the_ranking_is_what_match_capability_returns`,
because it pins the property that makes everything else safe rather than the behaviour
this module adds. A ranking that disagreed with ``match_capability`` would be a second
opinion about what the system is going to do, and a second opinion is worse than none:
the caller would reason about one capability while the gateway ran another. It is
checked across every registered phrasing plus the sentences the other tests use.

The rest divides in four. The *loss* tests show what the current matcher discards —
that on the live registry "stop my alerts" scores a write one point above a read and
the call site cannot tell. The *threshold* tests defend :data:`NEAR_TIE` the way
``_GAP_PENALTY`` is defended in the runtime: by sweeping the alternatives over a corpus
and naming which sentences each one costs, so the number is a measurement and raising it
later means seeing the bill. The *preference* tests run every rule against real
capability pairs drawn from the registry rather than fixtures. And the *refusal* tests
are the point of the module: the pairs of real writes that none of the rules separate,
each one refused because all three rules tie on it rather than because a rule failed to
fire — which is asserted per pair, since the population changes with the registry.
"""

from __future__ import annotations

import os
import sys
import unittest
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import undx_agent_runtime as runtime  # noqa: E402
from services import undx_capability_registry as registry  # noqa: E402
from services.undx_brain import config as brain_config  # noqa: E402
from services.undx_brain import prediction as prediction_module  # noqa: E402
from services.undx_brain import selection as s  # noqa: E402
from services.undx_brain.prediction import Reversal  # noqa: E402

#: The Brain on, selection on, and prediction on — the last because the write-separation
#: rules read predictions, and a test that left it off would be exercising the
#: unavailable-predictor branch while believing it exercised the rules.
ON = {
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_BRAIN_SELECTION_ENABLED": "1",
    "UNDX_BRAIN_PREDICTION_ENABLED": "1",
}

#: Selection on, prediction off. Used to prove the module declines rather than falling
#: back to the score when it has no grounds to prefer one write over another.
ON_WITHOUT_PREDICTION = {
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_BRAIN_SELECTION_ENABLED": "1",
}

#: Sentences that plainly ask for a write. Not ambiguous by any reading: each one names
#: an operation in ordinary words. These are what a widened near-tie band costs, and the
#: threshold sweep below prices each candidate value against this list.
PLAINLY_A_WRITE = (
    "stop my bitcoin alert",
    "silence my alerts",
    "snooze my alert",
    "edit my alert",
    "change my alert",
    "get rid of my alert",
    "turn off alerts",
    "save this post",
    "unsave this post",
    "unfollow user 7",
    "stop following user 7",
    "save post 9",
    "remove alert 3",
    "delete my bitcoin alert",
    "pause my bitcoin alert",
    "update the threshold on my btc alert",
)

#: Sentences genuinely poised between doing something and looking at something. These
#: are what the band has to catch.
GENUINELY_POISED = (
    "stop my alerts",
    "show my alerts",
)


def _all_registered_phrasings() -> list[str]:
    return [phrase for spec in registry.REGISTRY.values() for phrase in spec.intents]


def _write_ids() -> list[str]:
    return sorted(spec.capability_id for spec in registry.REGISTRY.values() if spec.is_write)


#: Verb pairs that name an operation and its exact inverse. Used to find those pairs in
#: the registry by their declared ids instead of listing them, so that registering a new
#: inverse does not silently leave it untested.
_INVERSE_VERBS = {
    "like": "unlike",
    "save": "unsave",
    "follow": "unfollow",
    "block": "unblock",
    "pause": "resume",
    "add": "remove",
    "mute": "unmute",
    "hide": "unhide",
    "subscribe": "unsubscribe",
}


def _inverse_pairs() -> list[tuple[str, str]]:
    writes = set(_write_ids())
    found = set()
    for capability_id in writes:
        segments = capability_id.split(".")
        for index, segment in enumerate(segments):
            opposite = _INVERSE_VERBS.get(segment)
            if opposite is None:
                continue
            twin = ".".join(segments[:index] + [opposite] + segments[index + 1:])
            if twin in writes:
                found.add(tuple(sorted((capability_id, twin))))
    return sorted(found)


def _band(capability_ids) -> list[s.Candidate]:
    """A contested band of real writes, assembled directly.

    No sentence on today's registry produces two contested writes — the sweep in
    :class:`TheWritesTheRegistryDeclaresAreNotCloseToEachOther` establishes that as a
    fact rather than an assumption — so the write-separation rules cannot be reached
    through :func:`~services.undx_brain.selection.select` with a string. They are
    reached here with the band built by hand. The *capabilities* are still the real
    registered ones and the predictions are still derived from them; only the claim
    that these two scored alike is the test's.
    """
    return [
        s.Candidate(
            capability_id=capability_id,
            score=10,
            margin=0,
            is_write=True,
            risk=registry.REGISTRY[capability_id].risk,
            confirmation=registry.REGISTRY[capability_id].confirmation,
            contested=True,
        )
        for capability_id in capability_ids
    ]


def _ranked_for(text: str):
    """The scoring the matcher does, reproduced here only to describe it in assertions."""
    return s.rank(text, env=ON)


class TheMatcherThrowsAwayTheRunnersUp(unittest.TestCase):
    """What is lost today, on the registry as it actually stands."""

    def test_match_capability_returns_one_capability_and_no_indication_of_closeness(self):
        # The premise of the module in one assertion. ``match_capability`` has exactly
        # one return channel, and it carries no score, no runner-up and no margin, so
        # nothing downstream can distinguish a one-point win from a landslide.
        best = runtime.match_capability("stop my alerts")
        self.assertIsNotNone(best)
        self.assertTrue(hasattr(best, "capability_id"))
        for attribute in ("score", "margin", "runner_up", "alternatives", "contested"):
            self.assertFalse(
                hasattr(best, attribute),
                f"CapabilitySpec has grown {attribute!r}; the matcher may now be able to "
                f"report closeness on its own and this module's premise has changed",
            )

    def test_stop_my_alerts_puts_a_write_one_point_above_a_read(self):
        # The concrete cost. One point of scoring separates pausing somebody's alerts
        # from showing them, and today the pause is what runs.
        ranked = _ranked_for("stop my alerts")
        top, second = ranked[0], ranked[1]
        self.assertEqual(top.capability_id, "crypto.alerts.pause")
        self.assertTrue(top.is_write)
        self.assertEqual(second.capability_id, "crypto.alerts.list")
        self.assertFalse(second.is_write)
        self.assertEqual(second.margin, 1)
        self.assertEqual(
            runtime.match_capability("stop my alerts").capability_id,
            "crypto.alerts.pause",
            "the live matcher no longer prefers the write here; this test's example is stale",
        )

    def test_show_my_alerts_is_the_same_shape_between_two_reads(self):
        ranked = _ranked_for("show my alerts")
        self.assertEqual(ranked[0].capability_id, "crypto.alerts.list")
        self.assertEqual(ranked[1].capability_id, "crypto.alerts.get")
        self.assertEqual(ranked[1].margin, 1)


class TheRankingIsTheMatchersOwn(unittest.TestCase):
    """The ranking must never become a second opinion about what will run."""

    def test_the_top_of_the_ranking_is_what_match_capability_returns(self):
        texts = _all_registered_phrasings() + list(PLAINLY_A_WRITE) + list(GENUINELY_POISED) + [
            "hello", "do not delete alert 3", "what does delete alert do",
            "delete all my alerts", "find posts from people i follow",
        ]
        self.assertGreater(len(texts), 250, "the phrasing corpus has shrunk unexpectedly")
        for text in texts:
            with self.subTest(text=text):
                best = runtime.match_capability(text)
                ranked = s.rank(text, env=ON)
                expected = best.capability_id if best is not None else None
                actual = ranked[0].capability_id if ranked else None
                self.assertEqual(
                    actual, expected,
                    f"rank() disagrees with match_capability() on {text!r}; the ranking "
                    f"has drifted from the thing that actually runs",
                )

    def test_the_scoring_functions_it_borrows_still_exist(self):
        # ``rank`` calls five private names in the runtime. Private means they can be
        # renamed without ceremony, and a rename would strand this module with an
        # AttributeError raised at request time rather than at test time. Naming them
        # here turns that into a failure now.
        for name in ("_tokens", "_words", "_subsequence_score", "_negation_blocks",
                     "asks_for_the_action"):
            with self.subTest(name=name):
                self.assertTrue(
                    hasattr(runtime, name),
                    f"undx_agent_runtime.{name} is gone; selection.rank borrows it",
                )

    def test_a_negated_message_yields_no_write_candidates(self):
        # The exclusions are applied during ranking rather than filtered afterwards, so
        # an excluded write cannot appear in the contested band and be reported as
        # displaced. It was never a candidate, and calling it displaced would be a false
        # claim about what the system nearly did.
        text = "do not delete alert 3"
        ranked = s.rank(text, env=ON)
        self.assertEqual([item.capability_id for item in ranked if item.is_write], [])
        self.assertIsNone(runtime.match_capability(text))
        self.assertEqual(s.select(text, env=ON).displaced_writes, ())

    def test_a_question_about_what_a_write_does_is_not_excluded_by_the_matcher(self):
        # Recorded because it contradicts what ``match_capability``'s docstring says it
        # does, and the code is what is true. "Writes are excluded when the message is
        # framed as … a question about what the action does" holds for the negation
        # mechanism, but ``asks_for_the_action("what does delete alert do")`` is True and
        # "delete alert" outscores the registered read phrasing "what does alert" — so
        # asking what deleting an alert does deletes the alert.
        #
        # This module does not fix that; changing the matcher would change what the
        # live gateway runs, which is not this batch's business. What it does is make
        # the closeness visible, and one point of closeness is all there is.
        text = "what does delete alert do"
        self.assertTrue(runtime.asks_for_the_action(text))
        self.assertEqual(runtime.match_capability(text).capability_id, "crypto.alerts.delete")

        ranked = s.rank(text, env=ON)
        self.assertEqual(ranked[0].capability_id, "crypto.alerts.delete")
        self.assertEqual(ranked[1].capability_id, "crypto.alerts.get")
        self.assertEqual(ranked[1].margin, 1)

        chosen = s.select(text, env=ON)
        self.assertTrue(chosen.decided)
        self.assertEqual(chosen.capability_id, "crypto.alerts.get")
        self.assertIs(chosen.separator, s.Separator.READ_OVER_WRITE)
        self.assertEqual(chosen.displaced_writes, ("crypto.alerts.delete",))

    def test_truncation_never_cuts_into_the_contested_band(self):
        # ``MAX_CANDIDATES`` exists to keep the tail short, and it is only sound if the
        # tail is never part of a decision. Every decision this module makes is a
        # property of the near-tie band, so the band must fit inside the limit with room
        # to spare on every corpus sentence.
        widest = 0
        for text in _all_registered_phrasings() + list(PLAINLY_A_WRITE) + list(GENUINELY_POISED):
            ranked = s.rank(text, env=ON)
            widest = max(widest, sum(1 for item in ranked if item.contested))
        self.assertLess(
            widest, s.MAX_CANDIDATES,
            f"the widest contested band is {widest} and MAX_CANDIDATES is "
            f"{s.MAX_CANDIDATES}; truncation can now change a decision",
        )


class TheNearTieThresholdIsMeasured(unittest.TestCase):
    """One point, priced against the alternatives rather than chosen for feel."""

    def test_it_is_one(self):
        self.assertEqual(s.NEAR_TIE, 1)

    def test_one_catches_both_poised_sentences_and_costs_no_plain_write(self):
        for text in GENUINELY_POISED:
            with self.subTest(poised=text):
                chosen = s.select(text, env=ON)
                self.assertGreaterEqual(
                    len(chosen.contested), 2,
                    f"{text!r} is no longer reported as contested",
                )
        for text in PLAINLY_A_WRITE:
            with self.subTest(write=text):
                chosen = s.select(text, env=ON)
                self.assertEqual(
                    chosen.displaced_writes, (),
                    f"{text!r} plainly asks for a write and the write was displaced",
                )

    def test_widening_the_band_would_cost_named_sentences(self):
        # The sweep that makes the number a measurement. At two, an unmistakable update
        # stops being an update; at three and four, more follow. Recording *which*
        # sentences each value costs means a future widening is a decision about those
        # sentences rather than about a constant.
        cost = {}
        for threshold in (1, 2, 3, 4):
            deferred = []
            for text in PLAINLY_A_WRITE:
                ranked = s.rank(text, env=ON)
                if len(ranked) > 1 and ranked[0].is_write and ranked[1].margin <= threshold:
                    deferred.append(text)
            cost[threshold] = deferred

        self.assertEqual(cost[1], [], "a one-point band now defers a plainly-intended write")
        self.assertIn("update the threshold on my btc alert", cost[2])
        self.assertIn("edit my alert", cost[3])
        self.assertIn("pause my bitcoin alert", cost[4])
        for lower, higher in ((1, 2), (2, 3), (3, 4)):
            self.assertLess(
                len(cost[lower]), len(cost[higher]),
                "the cost of widening the band is no longer monotonic; the sweep needs "
                "re-reading before the threshold is trusted",
            )


class TheWritesTheRegistryDeclaresAreNotCloseToEachOther(unittest.TestCase):
    """A near-tie involving a write is never something the registry authored."""

    def test_no_registered_write_phrasing_lands_inside_the_near_tie_band(self):
        """The class's claim, asserted against the band instead of against a count.

        This used to require that exactly *one* registered write phrasing had a
        runner-up at all, and then that that one's margin was at least 15. Having a
        runner-up is not the thing that matters — being close enough to one to be
        *contested* is, and ``NEAR_TIE`` is where that line sits. The registry has since
        grown from 254 phrasings to 479 and thirteen write phrasings now have a runner-up
        somewhere behind them, none of them nearer than six points. Nothing moved closer
        together; the count was never measuring distance.

        So the assertion is the distance. Every registered write phrasing's nearest rival
        must sit outside the band, which is what makes "a near-tie involving a write only
        comes from a sentence somebody typed" true.
        """
        margins = []
        for text in _all_registered_phrasings():
            ranked = s.rank(text, env=ON)
            if len(ranked) > 1 and ranked[0].is_write:
                margins.append((ranked[1].margin, text, ranked[0].capability_id))
        margins.sort()

        # Without this the sweep passes by matching no write phrasing at all.
        self.assertGreaterEqual(
            len(margins), 10,
            "almost no registered phrasing resolves to a write with a runner-up, so the "
            "band check below is close to vacuous",
        )
        for margin, text, capability_id in margins:
            with self.subTest(text=text):
                self.assertGreater(
                    margin, s.NEAR_TIE,
                    f"{text!r} puts {capability_id} within the near-tie band of its "
                    f"runner-up, so the registry now authors a contested write",
                )
        self.assertGreaterEqual(
            margins[0][0], 6,
            "the designed write vocabulary has moved closer together. Still outside the "
            f"band at {s.NEAR_TIE}, so nothing is contested yet, but the headroom that "
            f"made that robust is shrinking: {margins[:3]}",
        )

    def test_no_registered_phrasing_produces_two_contested_writes(self):
        # Which is why the separation rules are exercised through ``_separate_writes``
        # with a hand-built band rather than through a string. Stated as a test so that
        # the day a phrasing does produce one, this is a failure telling the suite to
        # start driving those rules end to end.
        for text in _all_registered_phrasings() + list(PLAINLY_A_WRITE):
            with self.subTest(text=text):
                ranked = s.rank(text, env=ON)
                contested = [item for item in ranked if item.contested and item.is_write]
                self.assertLessEqual(len(contested), 1, f"{text!r} contests two writes")


class TheContestedBandIsSeparatedOnDeclaredData(unittest.TestCase):
    """Every rule, run against real capability pairs."""

    def test_a_read_is_taken_over_a_write_and_the_write_is_named(self):
        chosen = s.select("stop my alerts", env=ON)
        self.assertTrue(chosen.decided)
        self.assertEqual(chosen.capability_id, "crypto.alerts.list")
        self.assertIs(chosen.separator, s.Separator.READ_OVER_WRITE)
        self.assertEqual(chosen.displaced_writes, ("crypto.alerts.pause",))
        self.assertIn("crypto.alerts.pause", chosen.reason)

    def test_contested_reads_do_not_stop_the_turn(self):
        # Refusing "show my alerts" because ``get`` scored a point behind ``list`` would
        # be pedantry wearing caution's clothes. Neither choice changes anything.
        chosen = s.select("show my alerts", env=ON)
        self.assertTrue(chosen.decided)
        self.assertEqual(chosen.capability_id, "crypto.alerts.list")
        self.assertIs(chosen.separator, s.Separator.UNCONTESTED)
        self.assertEqual(chosen.contested, ("crypto.alerts.list", "crypto.alerts.get"))
        self.assertEqual(chosen.displaced_writes, ())

    def test_the_reversible_write_beats_the_irrecoverable_one(self):
        band = _band(["crypto.alerts.pause", "crypto.alerts.delete"])
        chosen = s._separate_writes(band, tuple(band), ("a", "b"), (), ON)
        self.assertTrue(chosen.decided)
        self.assertEqual(chosen.capability_id, "crypto.alerts.pause")
        self.assertIs(chosen.separator, s.Separator.REVERSIBLE_OVER_NOT)

    def test_the_reason_does_not_overclaim_reversibility(self):
        # ``crypto.alerts.update`` wins over ``delete`` while being only
        # ``requires_pre_read`` — recoverable if and only if its prior values were read
        # first. A constant explanation saying "it can be taken back" would be false
        # here, which is why the explanation is built from the predictions.
        band = _band(["crypto.alerts.update", "crypto.alerts.delete"])
        chosen = s._separate_writes(band, tuple(band), ("a", "b"), (), ON)
        self.assertEqual(chosen.capability_id, "crypto.alerts.update")
        self.assertIn(Reversal.REQUIRES_PRE_READ.value, chosen.reason)
        self.assertIn(Reversal.IRRECOVERABLE.value, chosen.reason)
        self.assertNotIn("can be taken back", chosen.reason)

    def test_the_narrower_blast_radius_wins_when_reversibility_ties(self):
        band = _band(["crypto.alerts.pause", "reels.save"])
        chosen = s._separate_writes(band, tuple(band), ("a", "b"), (), ON)
        self.assertTrue(chosen.decided)
        self.assertEqual(chosen.capability_id, "reels.save")
        self.assertIs(chosen.separator, s.Separator.NARROWER_BLAST_RADIUS)

    def test_the_reversal_order_table_covers_every_member_of_the_enum(self):
        # An unmapped member would fall to the default, and the default has to sort
        # *last*: a new reversal class defaulting to zero would be preferred over an
        # exact inverse, which is the wrong direction to fail in.
        for member in Reversal:
            with self.subTest(member=member):
                self.assertIn(member, s._REVERSAL_ORDER)
        self.assertGreater(
            s._reversal_rank("not-a-reversal-at-all"),
            max(s._REVERSAL_ORDER.values()),
            "an unknown reversal class no longer sorts last",
        )

    def test_the_rules_are_tried_in_the_declared_order(self):
        # Reversibility dominates width, and both dominate undo cost. Checked by finding
        # a pair where the later rules would disagree with the earlier one and showing
        # the earlier one wins: ``saved.post.set`` has a strictly narrower blast radius
        # than ``crypto.alerts.pause`` and still loses, because pause is an exact
        # inverse and set is not.
        band = _band(["saved.post.set", "crypto.alerts.pause"])
        chosen = s._separate_writes(band, tuple(band), ("a", "b"), (), ON)
        self.assertEqual(chosen.capability_id, "crypto.alerts.pause")
        self.assertIs(chosen.separator, s.Separator.REVERSIBLE_OVER_NOT)

        narrower = prediction_module.predict("saved.post.set", {}, env=ON)
        wider = prediction_module.predict("crypto.alerts.pause", {}, env=ON)
        self.assertLess(
            len(narrower.conflicting_writes), len(wider.conflicting_writes),
            "the example no longer has the later rule disagreeing with the earlier one",
        )


class TheSelectorRefusesRatherThanGuesses(unittest.TestCase):
    """The part that makes this selection and not ranking-then-obeying."""

    def test_an_operation_and_its_own_inverse_are_never_separated(self):
        # Derived from the registry rather than listed. The five that were written out by
        # hand were the five that existed; ``business.campaign.pause/resume``,
        # ``crypto.watchlist.add/remove``, ``marketplace.listing.pause/resume`` and
        # ``profile.block/unblock`` have been registered since and were not covered, which
        # is the failure mode of a hand-kept list — it does not shrink, it just stops
        # keeping up. Deriving them means a newly registered inverse is covered the day
        # it lands.
        inverses = _inverse_pairs()
        self.assertGreaterEqual(len(inverses), 9, f"inverse pairs are no longer found: {inverses}")
        for pair in inverses:
            with self.subTest(pair=pair):
                band = _band(list(pair))
                chosen = s._separate_writes(band, tuple(band), pair, (), ON)
                self.assertFalse(
                    chosen.decided,
                    f"{pair} was separated; a rule is now preferring one of two exact "
                    f"opposites, which the score cannot justify",
                )
                self.assertIs(chosen.separator, s.Separator.NOTHING_SEPARATED_THEM)
                self.assertEqual(chosen.capability_id, "")
                self.assertEqual(chosen.displaced_writes, tuple(sorted(pair)))

    def test_an_undecided_selection_is_falsy_but_not_an_error(self):
        band = _band(["crypto.alerts.pause", "crypto.alerts.resume"])
        chosen = s._separate_writes(band, tuple(band), ("a", "b"), (), ON)
        self.assertTrue(chosen.ok, "declining to choose is a result, not a failure")
        self.assertFalse(chosen.decided)
        self.assertFalse(bool(chosen))

    def test_there_is_no_best_guess_field_to_fall_back_on(self):
        # A caller that wants the best-scoring capability regardless can still call
        # ``match_capability``. What must not exist is a field on an *undecided*
        # selection that quietly offers one, because that is how a refusal becomes
        # advisory.
        band = _band(["crypto.alerts.pause", "crypto.alerts.resume"])
        chosen = s._separate_writes(band, tuple(band), ("a", "b"), (), ON)
        for attribute in ("best_guess", "fallback", "probable", "default_capability_id"):
            self.assertFalse(hasattr(chosen, attribute), f"Selection grew {attribute!r}")
        self.assertEqual(chosen.capability_id, "")

    def test_every_write_pair_in_the_registry_is_separated_or_refused_deliberately(self):
        """The whole write half of the registry, pairwise, without freezing the tally.

        This used to assert three numbers: 136 pairs, 16 of them undecided, and the exact
        16. All three were the same claim about a registry of 17 writes. There are 48 now,
        1,128 pairs and 91 refusals, and the enumerated set had become a snapshot of a
        moment — it would have to be re-typed every time a write is registered, which is
        the kind of maintenance that gets done by pasting in whatever the run printed.
        A list regenerated from the output it is meant to check is not a check.

        What the enumeration was reaching for is in its own old comment: the interesting
        thing is not how many are refused but *why*. So that is asserted directly —
        every refusal must be a genuine tie on all three declared rules, which is the
        durable form of "an operation against its own inverse, or two preference updates
        on different resources". A pair refused while a rule had grounds to separate it
        would be a rule that failed to fire, and no count would show it.
        """
        writes = _write_ids()
        pairs = list(combinations(writes, 2))
        undecided = []
        separators = {}
        for pair in pairs:
            band = _band(list(pair))
            chosen = s._separate_writes(band, tuple(band), pair, (), ON)
            separators[chosen.separator] = separators.get(chosen.separator, 0) + 1
            if not chosen.decided:
                undecided.append(pair)
                self.assertIs(chosen.separator, s.Separator.NOTHING_SEPARATED_THEM)
                self.assertEqual(chosen.capability_id, "")
                self.assertEqual(chosen.displaced_writes, tuple(sorted(pair)))
            else:
                self.assertIsNot(chosen.separator, s.Separator.NOTHING_SEPARATED_THEM)
                self.assertIn(chosen.capability_id, pair)

        # Both floors, because this test is vacuous at either extreme: refusing every
        # pair would pass every assertion above while making the selector useless, and
        # separating every pair would mean the refusal path is never reached at all.
        self.assertGreater(len(pairs), 500, "the registry's write half has shrunk sharply")
        self.assertGreater(len(undecided), 0, "nothing is refused; the refusal path is dead")
        self.assertLess(
            len(undecided), len(pairs) // 2,
            f"{len(undecided)} of {len(pairs)} write pairs are refused. Refusing is the "
            f"safe direction but it is not free, and a majority means the declared data "
            f"has stopped distinguishing capabilities",
        )

        # Every refusal is a tie, not a rule that failed to fire.
        for pair in undecided:
            with self.subTest(pair=pair):
                first, second = (prediction_module.predict(c, {}, env=ON) for c in pair)
                self.assertEqual(
                    s._reversal_rank(first.reversal), s._reversal_rank(second.reversal),
                    "refused while reversibility could have separated them",
                )
                self.assertEqual(
                    len(first.conflicting_writes), len(second.conflicting_writes),
                    "refused while blast radius could have separated them",
                )
                self.assertEqual(
                    first.undo_is_cheap, second.undo_is_cheap,
                    "refused while undo cost could have separated them",
                )

        # Every rule that exists is reached by some real pair. CHEAPER_UNDO used to be
        # unreachable here — reversibility and blast radius always decided first — and
        # the old expectation pinned it at zero with a note saying to update rather than
        # remove it if that changed. It has: the wider registry contains pairs that agree
        # on the first two rules and differ on undo cost.
        for separator in (s.Separator.REVERSIBLE_OVER_NOT, s.Separator.NARROWER_BLAST_RADIUS,
                          s.Separator.CHEAPER_UNDO, s.Separator.NOTHING_SEPARATED_THEM):
            with self.subTest(separator=separator):
                self.assertGreater(
                    separators.get(separator, 0), 0,
                    f"no real write pair reaches {separator}; it is a rule the registry "
                    f"cannot exercise, so nothing here defends it",
                )

    def test_separating_two_writes_does_not_depend_on_which_was_named_first(self):
        # The band arrives in score order and two contested writes score alike, so the
        # order within it is incidental — whichever the matcher happened to emit first.
        # A rule that broke a tie on position would be reading that incidental order as
        # evidence, and it would look perfectly decisive from one side.
        for pair in combinations(_write_ids(), 2):
            forward = _band(list(pair))
            backward = _band(list(pair[::-1]))
            first = s._separate_writes(forward, tuple(forward), pair, (), ON)
            second = s._separate_writes(backward, tuple(backward), pair[::-1], (), ON)
            with self.subTest(pair=pair):
                self.assertEqual(
                    (first.decided, first.capability_id, first.separator),
                    (second.decided, second.capability_id, second.separator),
                    f"{pair} separates differently when reversed",
                )

    def test_without_prediction_two_contested_writes_are_refused_not_scored(self):
        # The rules read predictions. With the predictor unavailable there is no ground
        # to prefer either write, and falling back to the score would be precisely the
        # behaviour this module replaces, wearing a fallback's clothes.
        band = _band(["crypto.alerts.pause", "crypto.alerts.delete"])
        chosen = s._separate_writes(band, tuple(band), ("a", "b"), (), ON_WITHOUT_PREDICTION)
        self.assertTrue(chosen.ok)
        self.assertFalse(chosen.decided)
        self.assertEqual(chosen.capability_id, "")
        self.assertIn("prediction is unavailable", chosen.reason)


class TheFlagGatesEverything(unittest.TestCase):
    """Off is the default, and off means the module does nothing at all."""

    def test_the_flag_exists_defaults_off_and_fails_closed(self):
        flag = next(item for item in brain_config.CATALOG
                    if item.name == "UNDX_BRAIN_SELECTION_ENABLED")
        self.assertEqual(flag.default, "0")
        self.assertEqual(flag.fail, "closed")

    def test_with_no_environment_at_all_nothing_ranks_and_nothing_is_selected(self):
        self.assertEqual(s.rank("stop my alerts"), ())
        chosen = s.select("stop my alerts")
        self.assertFalse(chosen.ok)
        self.assertFalse(chosen.decided)
        self.assertEqual(chosen.capability_id, "")

    def test_the_package_flag_alone_does_not_turn_it_on(self):
        for env in ({"UNDX_BRAIN_ENABLED": "1"}, {"UNDX_BRAIN_SELECTION_ENABLED": "1"}):
            with self.subTest(env=tuple(sorted(env))):
                self.assertEqual(s.rank("stop my alerts", env=env), ())
                self.assertFalse(s.select("stop my alerts", env=env).ok)

    def test_rank_is_gated_independently_of_select(self):
        # A caller holding a ranking taken while the flag was on must not be able to
        # keep ranking after it goes off, and vice versa. Checked separately because a
        # single shared guard read once at import would pass a test that only exercised
        # one of them.
        self.assertNotEqual(s.rank("stop my alerts", env=ON), ())
        self.assertEqual(s.rank("stop my alerts", env={}), ())
        self.assertTrue(s.select("stop my alerts", env=ON).ok)
        self.assertFalse(s.select("stop my alerts", env={}).ok)


class TheAnswersToNonQuestionsAreStillAnswers(unittest.TestCase):
    """Declining is returned, never raised."""

    def test_an_empty_message_returns_a_refusal_rather_than_raising(self):
        for text in ("", "   ", "\n"):
            with self.subTest(text=repr(text)):
                chosen = s.select(text, env=ON)
                self.assertTrue(chosen.ok)
                self.assertFalse(chosen.decided)
                self.assertIs(chosen.separator, s.Separator.NO_CANDIDATE)

    def test_small_talk_names_no_capability(self):
        chosen = s.select("hello there", env=ON)
        self.assertTrue(chosen.ok)
        self.assertFalse(chosen.decided)
        self.assertEqual(chosen.candidates, ())
        self.assertIs(chosen.separator, s.Separator.NO_CANDIDATE)

    def test_an_uncontested_message_is_decided_and_says_by_how_much(self):
        chosen = s.select("delete my bitcoin alert", env=ON)
        self.assertTrue(chosen.decided)
        self.assertTrue(bool(chosen))
        self.assertEqual(chosen.capability_id, "crypto.alerts.delete")
        self.assertIs(chosen.separator, s.Separator.UNCONTESTED)
        self.assertEqual(chosen.contested, ())
        self.assertIn("near-tie band", chosen.reason)


if __name__ == "__main__":
    unittest.main()
