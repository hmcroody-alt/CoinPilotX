"""What a request must open, and — the harder half — what it must leave shut.

Directive §6 states the acceptance test in full: "Why is my account acting strange?"
should activate account health, sessions, devices, notifications, recent settings
changes and support tickets, and should *not* activate Marketplace, music or crypto
unless evidence connects them. Both halves are pinned here, because a router that opens
everything passes the first half perfectly.

The rest of these tests are the defects found while building the module, each written as
the user-visible failure it was rather than as the code path it exercises: a working
capability reported to somebody as a feature that does not exist; a request about a
misbehaving account offered a *write* to follow somebody; "what devices am I signed in
on" reaching the one unbuilt capability and missing the built one; a focus carrying
three ways of reading sessions and no way of reading notifications. Every one of them
scored plausibly and every one of them was wrong, which is why they are regressions a
future change could reintroduce without anything looking broken.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import undx_knowledge_map as kmap  # noqa: E402
from services.undx_brain import attention as a  # noqa: E402
from services.undx_brain import knowledge as k  # noqa: E402
from services.undx_brain import workspace as w  # noqa: E402
from services.undx_brain.bounds import Refusal  # noqa: E402

OWNER = 4242

#: Everything on. Individual tests turn things off; a base of "off" would mean most
#: tests measured the disabled path by accident.
ON = {
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_BRAIN_ATTENTION_ENABLED": "1",
    "UNDX_BRAIN_WORKSPACE_ENABLED": "1",
}

#: The §6 request, quoted exactly.
SIXTH = "Why is my account acting strange?"


def env(**overrides: str) -> dict[str, str]:
    settings = dict(ON)
    settings.update(overrides)
    return settings


def attend(request: str, **overrides: str) -> a.Focus:
    return a.attend(request, env=env(**overrides))


def resources_of(focus: a.Focus) -> set[str]:
    """Every resource type reachable through the focus's *executable* capabilities."""
    return {a._RESOURCE_OF[cid] for cid in focus.capability_ids}


class ItIsOffUntilItIsTurnedOn(unittest.TestCase):
    """An unconfigured deployment must route exactly as it does today: not at all."""

    def test_the_master_switch_closes_it(self):
        focus = a.attend(SIXTH, env={"UNDX_BRAIN_ATTENTION_ENABLED": "1"})
        self.assertFalse(focus)
        self.assertFalse(focus.ok)
        self.assertIn("disabled", focus.reason)

    def test_its_own_switch_closes_it(self):
        focus = a.attend(SIXTH, env={"UNDX_BRAIN_ENABLED": "1"})
        self.assertFalse(focus)
        self.assertFalse(focus.ok)
        self.assertIn("disabled", focus.reason)

    def test_a_closed_focus_carries_nothing_rather_than_a_default(self):
        # The dangerous shape of "disabled" is one that returns a plausible default area,
        # because the caller cannot tell that from a real routing decision.
        focus = a.attend(SIXTH, env={})
        self.assertEqual(focus.areas, ())
        self.assertEqual(focus.capability_ids, ())
        self.assertEqual(focus.deferred, ())
        self.assertEqual(focus.unreachable, ())


class TheSixthDirectiveExample(unittest.TestCase):
    """§6's worked example, both halves, on the real map."""

    def setUp(self):
        self.focus = attend(SIXTH)

    def test_it_activates_something(self):
        self.assertTrue(self.focus)
        self.assertTrue(self.focus.ok)

    def test_it_opens_account_health(self):
        # The map spells the summary's resource type ``account_health_fact`` and the
        # standing record's ``account_health``; either answers §6's first requirement.
        self.assertTrue(
            resources_of(self.focus) & {"account_health", "account_health_fact"},
            f"nothing that reads account standing was carried: {self.focus.capability_ids}",
        )

    def test_it_opens_sessions_and_devices(self):
        # §6 names them separately; in the map both live under the security area, and
        # what matters is that a capability able to answer each one is being carried.
        carried = resources_of(self.focus)
        self.assertTrue(
            carried & {"session", "device", "device_session", "security_event"},
            f"nothing that reads sessions or devices was carried: {self.focus.capability_ids}",
        )

    def test_it_opens_notifications(self):
        carried = resources_of(self.focus)
        self.assertTrue(
            carried & {"notification", "notification_preference", "notification_group"},
            f"nothing that reads notifications was carried: {self.focus.capability_ids}",
        )

    def test_it_opens_recent_settings_changes(self):
        carried = resources_of(self.focus)
        self.assertTrue(
            carried & {"setting", "setting_recommendation", "security_setting", "privacy_setting"},
            f"nothing that reads settings was carried: {self.focus.capability_ids}",
        )

    def test_it_opens_support_tickets(self):
        self.assertIn("support_ticket", resources_of(self.focus))

    def test_it_does_not_open_marketplace_music_or_crypto(self):
        # The half that is hard. Each of these is one vocabulary coincidence away from
        # activating, and each would turn an answer about a compromised account into a
        # status page.
        for forbidden in ("Marketplace", "Music", "Crypto alerts"):
            self.assertFalse(
                self.focus.activated(forbidden),
                f"{forbidden} activated on {SIXTH!r}; cues: {self.focus.why(forbidden)}",
            )

    def test_the_concern_frame_is_what_reached_them(self):
        # Recorded so that a future change removing the frame fails here, naming the
        # mechanism, rather than failing six assertions above with no explanation.
        self.assertIn("account_anomaly", self.focus.concerns)

    def test_every_activated_area_can_say_why(self):
        for area in self.focus.areas:
            self.assertTrue(self.focus.why(area.product_area),
                            f"{area.product_area} activated with no cue")


class NothingMatchedActivatesNothing(unittest.TestCase):
    """The absence of a default. A request nobody understood must not be answered."""

    def test_gibberish_activates_nothing(self):
        focus = attend("asdfgh qwerty zxcvbn")
        self.assertFalse(focus)
        self.assertTrue(focus.ok)
        self.assertIn("nothing in the product map matched", focus.reason)

    def test_an_empty_request_activates_nothing(self):
        focus = attend("")
        self.assertFalse(focus)
        self.assertTrue(focus.ok)
        self.assertIn("empty", focus.reason)

    def test_stop_words_alone_activate_nothing(self):
        self.assertFalse(attend("the and for with that this"))

    def test_a_long_request_is_still_routed(self):
        # Refusing to route a long message would turn somebody who explains themselves
        # thoroughly into somebody the system cannot help.
        padding = "please " * 400
        focus = attend(SIXTH + " " + padding)
        self.assertTrue(focus)
        self.assertTrue(any("first" in note for note in focus.notes))


class IncidentalProseNamesNoSubject(unittest.TestCase):
    """The rule that does most of §6's second half.

    Both false positives found while building the module were pure intent-and-description
    matches: "find" appears in the phrasings of ``marketplace.search`` and
    ``music.search``, and the word "account" appears in the prose describing blocking and
    muting. In the second case the workspace would have been offered ``social.follow`` —
    a write — to somebody reporting that their account was misbehaving.
    """

    def test_a_verb_shared_by_every_search_does_not_open_every_area(self):
        focus = attend("Find my Bitcoin alert")
        self.assertTrue(focus)
        for forbidden in ("Marketplace", "Music", "Groups", "Feed posts", "Search"):
            self.assertFalse(focus.activated(forbidden),
                             f"{forbidden} activated on a verb: {focus.why(forbidden)}")

    def test_the_area_the_request_is_actually_about_still_opens(self):
        # The filter must narrow, not silence. A test that only checked the exclusions
        # would pass on a module that returned an empty focus for everything.
        self.assertIn("Crypto alerts", attend("Find my Bitcoin alert").area_names)

    def test_no_activated_area_rests_on_prose_alone(self):
        for request in (SIXTH, "Find my Bitcoin alert", "what devices am I signed in on",
                        "why do i keep getting so many notifications"):
            focus = attend(request)
            for area in focus.areas:
                self.assertTrue(
                    any(cue.source_field in a._STRUCTURAL_FIELDS for cue in area.cues),
                    f"{area.product_area} activated on {request!r} with prose cues only",
                )

    def test_a_write_is_never_reached_by_prose(self):
        focus = attend(SIXTH)
        self.assertNotIn("social.follow", focus.capability_ids)


class ASymptomAloneIsAMood(unittest.TestCase):
    """A concern frame needs both halves: something is wrong, and something it is wrong about."""

    def test_a_symptom_without_an_anchor_fires_nothing(self):
        focus = attend("this is weird and broken and strange")
        self.assertEqual(focus.concerns, ())

    def test_an_anchor_without_a_symptom_fires_nothing(self):
        focus = attend("show me my account")
        self.assertEqual(focus.concerns, ())

    def test_a_symptom_about_something_else_does_not_open_the_account(self):
        # "That marketplace listing looks strange" must stay in Marketplace. The symptom
        # word is the same one §6's example turns on.
        focus = attend("that marketplace listing looks strange")
        self.assertEqual(focus.concerns, ())
        self.assertIn("Marketplace", focus.area_names)
        self.assertNotIn("account_health", resources_of(focus))

    def test_the_volume_frame_reaches_settings_not_just_notifications(self):
        # "Why do I keep getting these" is a question about settings, and the settings
        # capability is the one a vocabulary match on "notifications" would never reach.
        focus = attend("why do i keep getting so many notifications")
        self.assertIn("unwanted_volume", focus.concerns)
        self.assertTrue(
            resources_of(focus) & {"setting", "setting_recommendation",
                                   "notification_preference"},
            f"nothing that changes the volume was carried: {focus.capability_ids}",
        )

    def test_a_frame_naming_an_unknown_resource_type_fails_at_import_time(self):
        # A frame pointing at a resource type that no longer exists contributes nothing,
        # silently, and the symptom of that is somebody asking why nothing was checked.
        with self.assertRaises(ValueError) as caught:
            a.Concern(name="ghost", anchors=frozenset({"x"}), symptoms=frozenset({"y"}),
                      resources=("no_such_resource_type",), rationale="")
        self.assertIn("unknown resource types", str(caught.exception))

    def test_an_incomplete_frame_is_rejected(self):
        with self.assertRaises(ValueError):
            a.Concern(name="hollow", anchors=frozenset(), symptoms=frozenset({"y"}),
                      resources=("session",), rationale="")

    def test_the_shipped_frames_name_only_real_resource_types(self):
        for concern in a.CONCERNS:
            self.assertEqual(set(concern.resources) - a.RESOURCE_TYPES, set(),
                             f"{concern.name} names a resource type the map does not have")


class DeferredIsNotUnreachable(unittest.TestCase):
    """The defect that would have told somebody a working feature does not exist.

    Executable capabilities cut by the budget were once filed alongside genuinely unbuilt
    ones, and :func:`place_into` writes "nothing here is executable today" into the
    workspace for an unreachable area. A capability the person uses every day would have
    been reported as one the product has not built.
    """

    def test_a_budget_cut_is_reported_as_deferred(self):
        focus = attend(SIXTH, UNDX_ATTENTION_MAX_CAPABILITIES="1")
        self.assertEqual(len(focus.capability_ids), 1)
        self.assertTrue(focus.deferred, "areas were emptied by budget and said nothing")

    def test_nothing_deferred_is_also_called_unreachable(self):
        focus = attend(SIXTH, UNDX_ATTENTION_MAX_CAPABILITIES="1")
        self.assertEqual(set(focus.deferred) & set(focus.unreachable), set())

    def test_everything_deferred_is_actually_executable(self):
        focus = attend(SIXTH, UNDX_ATTENTION_MAX_CAPABILITIES="1")
        for cid in focus.deferred:
            self.assertTrue(a._RECORD_OF[cid].is_executable,
                            f"{cid} was deferred but is not executable")

    def test_nothing_unreachable_is_executable(self):
        for cid in attend(SIXTH).unreachable:
            self.assertFalse(a._RECORD_OF[cid].is_executable,
                             f"{cid} was called unreachable but the map says it runs")

    def test_an_area_emptied_by_budget_is_still_reachable(self):
        focus = attend(SIXTH, UNDX_ATTENTION_MAX_CAPABILITIES="1")
        for area in focus.areas:
            if area.deferred:
                self.assertTrue(area.reachable,
                                f"{area.product_area} has {len(area.deferred)} working "
                                "capabilities and was called unreachable")

    def test_a_budget_cut_never_reaches_the_workspace_as_a_gap(self):
        focus = attend(SIXTH, UNDX_ATTENTION_MAX_CAPABILITIES="1")
        space = w.open_workspace(OWNER, env=env())
        a.place_into(focus, space)
        gaps = {item.key for item in space.items(w.Slot.UNKNOWN)}
        for area in focus.areas:
            if area.deferred:
                self.assertNotIn(area.product_area, gaps)


class ReachableAreasRankFirst(unittest.TestCase):
    """An area where nothing can run must never displace one where something can.

    Twenty-six of the map's records are ``service_missing``, and several sit in areas
    named so closely to the built ones ("Privacy settings" beside "Privacy") that they
    score identically on the same words. Pure salience ordering withheld Support from
    §6's example, which the directive names outright.
    """

    def test_unreachable_areas_sort_behind_reachable_ones(self):
        focus = attend(SIXTH)
        seen_unreachable = False
        for area in focus.areas:
            if not area.reachable:
                seen_unreachable = True
            elif seen_unreachable:
                self.fail(f"{area.product_area} can run and ranked behind an area that cannot")

    def test_the_directives_support_requirement_survives_a_higher_scoring_dead_area(self):
        focus = attend(SIXTH)
        self.assertIn("support_ticket", resources_of(focus))


class TheBudgetIsSpentEvenly(unittest.TestCase):
    """Greedy filling produced three ways of reading sessions and no way of reading
    notifications — a narrower answer than the request asked for, arrived at by an
    implementation detail."""

    def test_every_activated_area_gets_its_first_capability_before_any_gets_a_second(self):
        focus = attend(SIXTH)
        counts = [len(area.capability_ids) for area in focus.areas if area.reachable]
        self.assertTrue(counts)
        self.assertLessEqual(max(counts) - min(counts), 1)

    def test_the_capability_ceiling_holds(self):
        focus = attend(SIXTH)
        self.assertLessEqual(len(focus.capability_ids), a.MAX_CAPABILITIES)

    def test_the_area_ceiling_holds(self):
        focus = attend(SIXTH)
        self.assertLessEqual(len(focus.areas), a.MAX_AREAS)


class ConfigurationMayNarrowAndMayNotWiden(unittest.TestCase):
    """A mistyped environment variable must not turn the router into one that opens
    everything."""

    def test_a_lower_area_ceiling_is_obeyed(self):
        focus = attend(SIXTH, UNDX_ATTENTION_MAX_AREAS="2")
        self.assertLessEqual(len(focus.areas), 2)

    def test_a_higher_area_ceiling_is_ignored(self):
        focus = attend(SIXTH, UNDX_ATTENTION_MAX_AREAS="500")
        self.assertLessEqual(len(focus.areas), a.MAX_AREAS)

    def test_a_higher_capability_ceiling_is_ignored(self):
        focus = attend(SIXTH, UNDX_ATTENTION_MAX_CAPABILITIES="500")
        self.assertLessEqual(len(focus.capability_ids), a.MAX_CAPABILITIES)

    def test_nonsense_falls_back_to_the_compiled_ceiling(self):
        focus = attend(SIXTH, UNDX_ATTENTION_MAX_AREAS="not a number")
        self.assertLessEqual(len(focus.areas), a.MAX_AREAS)

    def test_there_is_no_switch_that_activates_everything(self):
        names = {flag.name for flag in a.brain_config.CATALOG}
        for name in names:
            self.assertNotIn("ACTIVATE_EVERYTHING", name)
            self.assertNotIn("DISABLE_LIMITS", name)

    def test_the_capability_ceiling_cannot_exceed_what_the_workspace_accepts(self):
        # Offering the workspace more capabilities than it will hold means the surplus is
        # dropped by whichever of the two happened to arrive last.
        self.assertLessEqual(a.MAX_CAPABILITIES, w.BY_SLOT[w.Slot.SKILL].limit)


class ACutIsReported(unittest.TestCase):
    """"It never considered my orders" and "it considered them and ranked them ninth" are
    different answers, and the caller must be able to tell them apart."""

    def test_a_crowded_focus_says_so(self):
        focus = attend(SIXTH, UNDX_ATTENTION_MAX_AREAS="1")
        self.assertTrue(focus.crowded)
        self.assertTrue(focus.withheld)

    def test_withheld_areas_are_named(self):
        focus = attend(SIXTH, UNDX_ATTENTION_MAX_AREAS="1")
        for name in focus.withheld:
            self.assertIn(name, a.PRODUCT_AREAS)

    def test_nothing_is_both_activated_and_withheld(self):
        focus = attend(SIXTH)
        self.assertEqual(set(focus.area_names) & set(focus.withheld), set())

    def test_considered_counts_more_than_was_carried(self):
        focus = attend(SIXTH)
        self.assertGreaterEqual(focus.considered, len(focus.areas))

    def test_inspection_carries_no_request_text(self):
        focus = attend("my account is acting strange and my password is hunter2")
        blob = repr(focus.inspect())
        self.assertNotIn("hunter2", blob)
        self.assertIsInstance(focus.inspect()["terms"], int)


class SpellingsMeet(unittest.TestCase):
    """The map is written in singulars and people ask in plurals.

    ``devices`` once folded to ``devic`` because the ``es`` rule fired before the ``s``
    rule, so "what devices am I signed in on" reached only ``security.devices.list``,
    which is not built, and never ``security.device.list``, which is.
    """

    def test_a_plural_finds_its_singular(self):
        for word, expected in (("devices", "device"), ("notifications", "notification"),
                               ("alerts", "alert"), ("sessions", "session")):
            self.assertIn(expected, a._variants(word), f"{word} never reaches {expected}")

    def test_short_words_are_not_folded_into_nothing(self):
        self.assertEqual(a._variants("ads"), ("ads",))

    def test_the_device_question_carries_a_capability_that_runs(self):
        focus = attend("what devices am I signed in on")
        self.assertTrue(focus.capability_ids,
                        f"the device question activated {focus.area_names} and carried nothing")
        self.assertTrue(
            resources_of(focus) & {"device", "device_session", "session"},
            f"nothing device-shaped was carried: {focus.capability_ids}",
        )


class ThereIsNoSecondCatalogue(unittest.TestCase):
    """A capability removed from the registry must stop being attendable immediately,
    not linger in a hand-kept copy nobody remembers to prune."""

    def test_every_product_area_comes_from_the_map(self):
        self.assertEqual(a.PRODUCT_AREAS, kmap.PRODUCT_AREAS)

    def test_every_resource_type_comes_from_the_map(self):
        self.assertEqual(a.RESOURCE_TYPES,
                         frozenset(r.resource_type for r in kmap.RECORDS))

    def test_every_capability_ever_returned_is_in_the_map(self):
        known = {r.capability_id for r in kmap.RECORDS}
        for request in (SIXTH, "Find my Bitcoin alert", "what devices am I signed in on",
                        "why do i keep getting so many notifications",
                        "show me my saved posts", "who is following me"):
            focus = attend(request)
            for cid in focus.capability_ids + focus.deferred + focus.unreachable:
                self.assertIn(cid, known)

    def test_the_stop_list_is_shared_with_retrieval(self):
        # Two stop lists is two places to forget a word.
        self.assertIs(a.STOP_WORDS, k.STOP_WORDS)
        self.assertIs(a.MAX_TERMS, k.MAX_TERMS)

    def test_refusals_and_slots_are_the_existing_ones(self):
        self.assertIs(a.Refusal, Refusal)
        self.assertIs(a.Slot, w.Slot)


class ItTakesNoOwner(unittest.TestCase):
    """"What is this about?" is answerable before knowing whose account it is. An owner
    parameter here would be the signal that routing had started doing retrieval's job."""

    def test_attend_accepts_only_a_request_and_an_environment(self):
        import inspect

        parameters = list(inspect.signature(a.attend).parameters)
        self.assertEqual(parameters, ["request", "env"])

    def test_the_same_request_routes_the_same_way_twice(self):
        self.assertEqual(attend(SIXTH).area_names, attend(SIXTH).area_names)


class ItFillsAWorkspace(unittest.TestCase):
    """The join §5 left open: the workspace could bound a context, and nothing filled it.

    This is the guarantee — do not load all capabilities into every request — enforced
    rather than described.
    """

    def setUp(self):
        self.focus = attend(SIXTH)
        self.space = w.open_workspace(OWNER, env=env())
        self.refusals = a.place_into(self.focus, self.space)

    def test_it_carries_a_handful_of_the_registry_not_all_of_it(self):
        placed = {item.key for item in self.space.items(w.Slot.SKILL)}
        self.assertTrue(placed)
        self.assertLessEqual(len(placed), a.MAX_CAPABILITIES)
        self.assertLess(len(placed), len(kmap.RECORDS) // 4)

    def test_a_selected_focus_fits_without_refusal(self):
        self.assertEqual(self.refusals, (),
                         f"attention offered the workspace more than it accepts: "
                         f"{[r.bound for r in self.refusals]}")

    def test_every_entry_names_attention_as_its_source(self):
        for item in self.space.items():
            self.assertEqual(item.source, "attention:knowledge_map")

    def test_a_closed_workspace_is_not_written_to(self):
        closed = w.open_workspace(OWNER, env={"UNDX_BRAIN_ENABLED": "1"})
        self.assertEqual(a.place_into(self.focus, closed), ())
        self.assertEqual(len(closed), 0)

    def test_a_disabled_focus_writes_nothing(self):
        off = a.attend(SIXTH, env={"UNDX_BRAIN_ENABLED": "1"})
        space = w.open_workspace(OWNER, env=env())
        self.assertEqual(a.place_into(off, space), ())
        self.assertEqual(len(space), 0)

    def test_a_relevant_area_with_nothing_built_is_recorded_as_a_gap(self):
        # Said here rather than discovered at dispatch time as a failure.
        for area in self.focus.areas:
            if not area.reachable:
                self.assertEqual(self.space.value(w.Slot.UNKNOWN, area.product_area).strip()[:8],
                                 "relevant")


class ThePlannerActuallyConsultsAttention(unittest.TestCase):
    """The wiring, not the module. Everything above this class passes with nothing
    calling ``attend`` on a real request, which was the whole of the Foundation gap.
    """

    SIXTH_REQUEST = "Why is my account acting strange?"

    def setUp(self):
        from tests.undx_agent import bootstrap
        bootstrap.install()
        from services import undx_architecture

        self.arch = undx_architecture
        self.context = {"tool_names": [], "requires_confirmation": False}

    def _plan(self):
        return self.arch.build_plan(7, self.SIXTH_REQUEST, self.context, "r1")

    def _retrieve(self, plan):
        return next(n for n in plan["nodes"] if n["node_type"] == "retrieve")

    # -- the gate ------------------------------------------------------------------

    def test_off_the_plan_has_no_attention_key_at_all(self):
        """Absent, not ``ok=False``: that is how a reader tells off from found-nothing."""
        plan = self._plan()
        self.assertNotIn("attention", plan)

    def test_off_the_retrieval_objective_is_the_one_it_has_always_been(self):
        """§28. A default-off flag that still edits the plan is not default-off."""
        self.assertEqual(self._retrieve(self._plan())["objective"],
                         self.arch.RETRIEVAL_OBJECTIVE)

    def test_the_brain_flag_alone_is_not_enough(self):
        plan = self.arch.apply_attention(self._plan(), self.SIXTH_REQUEST,
                                         {"UNDX_BRAIN_ENABLED": "1"})
        self.assertNotIn("attention", plan)

    # -- what it is allowed to do --------------------------------------------------

    def test_on_the_sixth_directive_example_narrows_the_retrieval_node(self):
        plan = self.arch.apply_attention(self._plan(), self.SIXTH_REQUEST, ON)
        objective = self._retrieve(plan)["objective"]
        self.assertNotEqual(objective, self.arch.RETRIEVAL_OBJECTIVE)
        self.assertIn("Account health", objective)
        self.assertEqual(self._retrieve(plan)["attention_areas"], plan["attention"]["areas"])

    def test_on_it_leaves_marketplace_music_and_crypto_shut(self):
        """§6's second clause, asserted on the plan rather than on the Focus."""
        plan = self.arch.apply_attention(self._plan(), self.SIXTH_REQUEST, ON)
        for closed in ("Marketplace", "Music", "Crypto"):
            with self.subTest(closed=closed):
                self.assertNotIn(closed, plan["attention"]["areas"])

    def test_a_cut_tail_is_reported_on_the_plan_not_silently_dropped(self):
        plan = self.arch.apply_attention(self._plan(), self.SIXTH_REQUEST, ON)
        self.assertTrue(plan["attention"]["crowded"])
        self.assertTrue(plan["attention"]["withheld"])
        self.assertIn("considered, not overlooked", self._retrieve(plan)["objective"])

    # -- what it is not allowed to do ----------------------------------------------

    def test_attention_never_adds_a_skill(self):
        """The load-bearing boundary: routing is not authorisation.

        ``attend`` matches on the words in the request. If those words could put a
        skill on the plan, a phrase a user typed would be granting a capability, and
        the text router and the permission decision would have become one mechanism.
        """
        before = self._plan()
        after = self.arch.apply_attention(self._plan(), self.SIXTH_REQUEST, ON)
        self.assertEqual(after["skills"], before["skills"])

    def test_a_request_naming_a_high_impact_area_still_gets_no_extra_skill(self):
        plan = self.arch.build_plan(7, "publish a Reel right now", self.context, "r2")
        routed = self.arch.apply_attention(
            self.arch.build_plan(7, "publish a Reel right now", self.context, "r2"),
            "publish a Reel right now", ON,
        )
        self.assertIn("Reels", routed["attention"]["areas"])
        self.assertEqual(routed["skills"], plan["skills"],
                         "attention widened the skill list from the request text")

    def test_it_changes_no_node_other_than_retrieve(self):
        before = self._plan()
        after = self.arch.apply_attention(self._plan(), self.SIXTH_REQUEST, ON)
        for old, new in zip(before["nodes"], after["nodes"]):
            if new["node_type"] == "retrieve":
                continue
            with self.subTest(node=new["node_type"]):
                self.assertEqual(old, new)

    # -- the empty focus -----------------------------------------------------------

    def test_an_unmatched_request_narrows_nothing_and_asks_instead(self):
        """"We did not understand this" must not be recorded as "there is nothing"."""
        plan = self.arch.apply_attention(self._plan(), "zxqw fjjd plfh", ON)
        self.assertTrue(plan["attention"]["needs_clarification"])
        self.assertEqual(plan["attention"]["areas"], [])
        self.assertEqual(self._retrieve(plan)["objective"], self.arch.RETRIEVAL_OBJECTIVE,
                         "an empty focus narrowed retrieval to nothing")
        self.assertIn("nothing in the product map matched", plan["attention"]["reason"])

    def test_a_matched_request_does_not_ask_for_clarification(self):
        plan = self.arch.apply_attention(self._plan(), self.SIXTH_REQUEST, ON)
        self.assertFalse(plan["attention"]["needs_clarification"])

    def test_an_empty_message_is_recorded_rather_than_treated_as_a_failure(self):
        plan = self.arch.apply_attention(self._plan(), "", ON)
        self.assertTrue(plan["attention"]["needs_clarification"])
        self.assertIn("empty", plan["attention"]["reason"])

    # -- it must never take the turn down ------------------------------------------

    def test_a_plan_still_builds_when_attention_raises(self):
        """``attend`` is documented never to raise; the caller does not rely on that."""
        import services.undx_brain.attention as module

        original = module.attend
        module.attend = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            plan = self.arch.apply_attention(self._plan(), self.SIXTH_REQUEST, ON)
        finally:
            module.attend = original
        self.assertNotIn("attention", plan)
        self.assertEqual(self._retrieve(plan)["objective"], self.arch.RETRIEVAL_OBJECTIVE)

    def test_the_recorded_focus_is_json_serialisable(self):
        """``persist_plan`` writes the plan out; a tuple or a dataclass would break it."""
        import json

        plan = self.arch.apply_attention(self._plan(), self.SIXTH_REQUEST, ON)
        json.dumps(plan)

    def test_bounds_still_run_after_attention(self):
        """Order matters: ``build_plan`` composes them, so neither may shadow the other."""
        plan = self._plan()
        self.assertIn("bounds", plan)


if __name__ == "__main__":
    unittest.main()
