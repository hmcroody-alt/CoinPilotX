"""What the bounded working context must refuse, and why each refusal matters.

Every test here names a user-visible failure. The workspace is a container, and a
container's tests drift toward asserting that things go in and come out again, which
proves nothing: a plain dict passes all of those. What this module is *for* is the
refusals, so that is what is tested — the full slot that does not evict, the second
account's evidence that does not get in, the expired context that does not resume, and
the scratch that does not become durable memory by omission.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.undx_brain import workspace as w  # noqa: E402
from services.undx_brain.bounds import Refusal  # noqa: E402

OWNER = 4242
OTHER = 9317

#: Everything on. Individual tests turn things off; a base of "off" would mean most
#: tests measured the disabled path by accident.
ON = {
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_BRAIN_WORKSPACE_ENABLED": "1",
}


def env(**overrides: str) -> dict[str, str]:
    settings = dict(ON)
    settings.update(overrides)
    return settings


class FakeClock:
    """A clock the tests advance by hand, so expiry is measured rather than waited for."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def opened(owner: int = OWNER, clock=None, **overrides: str) -> w.Workspace:
    return w.open_workspace(owner, env=env(**overrides), clock=clock)


class ItIsOffUntilItIsTurnedOn(unittest.TestCase):
    """An unconfigured deployment must behave exactly as it does today."""

    def test_the_master_switch_closes_it(self):
        space = w.open_workspace(OWNER, env={"UNDX_BRAIN_WORKSPACE_ENABLED": "1"})
        self.assertFalse(space)
        self.assertIn("Brain layer is disabled", space.reason)

    def test_its_own_switch_closes_it(self):
        space = w.open_workspace(OWNER, env={"UNDX_BRAIN_ENABLED": "1"})
        self.assertFalse(space)
        self.assertIn("disabled", space.reason)

    def test_a_closed_workspace_refuses_entries_rather_than_swallowing_them(self):
        # The dangerous version of "disabled" is one that accepts writes and drops them,
        # because the caller believes the constraint it placed is being honoured.
        space = w.open_workspace(OWNER, env={})
        refusal = space.place(w.Slot.GOAL, "goal", "pause the bitcoin alert", source="user")
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "disabled")
        self.assertEqual(len(space), 0)

    def test_an_unreadable_switch_leaves_it_off(self):
        space = w.open_workspace(OWNER, env=env(UNDX_BRAIN_WORKSPACE_ENABLED="yes-please"))
        self.assertFalse(space)


class ItIsScopedToOneAccount(unittest.TestCase):
    """Two accounts in one working context is how one person hears about another."""

    def test_an_unresolvable_owner_opens_nothing(self):
        for bad in (None, 0, -1, True, 3.7, "", "  ", "abc", [4242]):
            with self.subTest(owner=bad):
                space = w.open_workspace(bad, env=env())
                self.assertFalse(space)
                self.assertEqual(space.owner_id, 0)

    def test_it_uses_the_same_resolver_as_memory(self):
        # Not "behaves the same as": is the same function. Two implementations of "whose
        # account is this" is two places for the answer to be different, and the
        # difference shows up as somebody inside a rollout and outside their own scope.
        from services.undx_brain import memory
        self.assertIs(w.memory.owner_id, memory.owner_id)

    def test_a_unicode_digit_owner_is_not_that_account(self):
        # int("٩٩") is 99 and "٩٩".isdigit() is True, so the obvious implementation opens
        # a workspace for a real person through a string that does not spell their id.
        for spelling in ("٩٩", "１００", "𝟵𝟵", "1_0_0"):
            with self.subTest(spelling=spelling):
                self.assertFalse(w.open_workspace(spelling, env=env()))

    def test_evidence_about_another_account_is_refused(self):
        space = opened()
        refusal = space.place(
            w.Slot.EVIDENCE, "alert", "alert 7 is active",
            source="tool:pulse.alerts.list", owner=OTHER,
        )
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "owner")
        self.assertEqual(len(space), 0)

    def test_evidence_that_names_no_account_is_refused(self):
        # The argument has no default precisely so that omitting it is a refusal rather
        # than a quiet attribution to whoever happens to own the workspace.
        space = opened()
        refusal = space.place(
            w.Slot.EVIDENCE, "alert", "alert 7 is active", source="tool:pulse.alerts.list",
        )
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "owner")

    def test_evidence_about_this_account_is_accepted(self):
        space = opened()
        self.assertFalse(space.place(
            w.Slot.EVIDENCE, "alert", "alert 7 is active",
            source="tool:pulse.alerts.list", owner=OWNER,
        ))
        self.assertEqual(len(space), 1)

    def test_the_owner_may_be_named_as_a_string_of_the_same_account(self):
        space = opened()
        self.assertFalse(space.place(
            w.Slot.EVIDENCE, "alert", "active", source="tool:x", owner=str(OWNER),
        ))

    def test_a_non_evidence_entry_may_still_be_checked_and_is_still_refused(self):
        space = opened()
        refusal = space.place(
            w.Slot.RESOURCE, "alert", "alert 7", source="user", owner=OTHER,
        )
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "owner")


class AFullSlotRefusesRatherThanEvicting(unittest.TestCase):
    """The oldest entry is usually the constraint the person stated first."""

    def test_the_ninth_constraint_is_refused_and_the_first_survives(self):
        space = opened()
        limit = w.BY_SLOT[w.Slot.CONSTRAINT].limit
        self.assertFalse(space.place(
            w.Slot.CONSTRAINT, "c0", "do not touch my portfolio", source="user",
        ))
        for index in range(1, limit):
            self.assertFalse(space.place(
                w.Slot.CONSTRAINT, f"c{index}", f"limit {index}", source="user",
            ))
        refusal = space.place(w.Slot.CONSTRAINT, "overflow", "one more", source="user")
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, f"slot:{w.Slot.CONSTRAINT.value}")
        self.assertEqual(
            space.value(w.Slot.CONSTRAINT, "c0"), "do not touch my portfolio",
            "the constraint stated first was displaced by later noise",
        )

    def test_a_second_goal_is_refused(self):
        space = opened()
        self.assertFalse(space.place(w.Slot.GOAL, "goal", "pause my alert", source="user"))
        refusal = space.place(w.Slot.GOAL, "other", "and mute the chat", source="user")
        self.assertTrue(refusal)
        self.assertEqual(refusal.limit, 1)

    def test_every_slot_enforces_its_declared_ceiling(self):
        for bound in w.SLOTS:
            with self.subTest(slot=bound.slot.value):
                space = opened(**{"UNDX_WORKSPACE_MAX_ITEMS": "200"})
                for index in range(bound.limit):
                    refusal = space.place(
                        bound.slot, f"k{index}", f"v{index}",
                        source="test", owner=OWNER,
                    )
                    self.assertFalse(refusal, refusal.message)
                overflow = space.place(
                    bound.slot, "extra", "one too many", source="test", owner=OWNER,
                )
                self.assertTrue(overflow)
                self.assertEqual(len(space.items(bound.slot)), bound.limit)

    def test_the_refusal_says_which_number_was_exceeded(self):
        space = opened()
        for index in range(w.BY_SLOT[w.Slot.RISK].limit):
            space.place(w.Slot.RISK, f"r{index}", f"risk {index}", source="policy")
        refusal = space.place(w.Slot.RISK, "extra", "another", source="policy")
        self.assertEqual(refusal.limit, w.BY_SLOT[w.Slot.RISK].limit)
        self.assertEqual(refusal.requested, w.BY_SLOT[w.Slot.RISK].limit + 1)


class TheWholeContextIsBounded(unittest.TestCase):
    """The corpus, all of memory and every capability arrive one addition at a time."""

    def test_the_item_budget_stops_the_total_even_when_no_slot_is_full(self):
        space = opened(UNDX_WORKSPACE_MAX_ITEMS="5")
        placed = 0
        for index in range(20):
            if not space.place(w.Slot.EVIDENCE, f"e{index}", f"observed {index}",
                               source="tool:x", owner=OWNER):
                placed += 1
        self.assertEqual(placed, 5)
        self.assertEqual(len(space), 5)

    def test_the_character_budget_refuses_rather_than_trimming(self):
        space = opened(UNDX_WORKSPACE_MAX_CHARS="300")
        self.assertFalse(space.place(w.Slot.GOAL, "goal", "x" * 200, source="user"))
        refusal = space.place(w.Slot.EVIDENCE, "e", "y" * 200, source="tool:x", owner=OWNER)
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "chars")
        # Nothing partial landed. A trimmed observation still reads as a whole one.
        self.assertEqual(space.value(w.Slot.EVIDENCE, "e"), "")

    def test_one_entry_cannot_fill_the_workspace_by_itself(self):
        space = opened(UNDX_WORKSPACE_MAX_CHARS="60000")
        refusal = space.place(
            w.Slot.EVIDENCE, "dump", "z" * (w.MAX_VALUE_CHARS + 1),
            source="tool:x", owner=OWNER,
        )
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "value")

    def test_a_key_long_enough_to_be_content_is_refused(self):
        space = opened()
        refusal = space.place(w.Slot.RESOURCE, "k" * (w.MAX_KEY_CHARS + 1), "v", source="user")
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "key")

    def test_an_empty_value_is_refused_because_it_reads_as_known_and_empty(self):
        space = opened()
        for blank in ("", "   ", None):
            with self.subTest(value=blank):
                refusal = space.place(w.Slot.RESOURCE, "alert", blank, source="user")
                self.assertTrue(refusal)
                self.assertEqual(refusal.bound, "value")

    def test_an_entry_with_no_source_is_refused(self):
        space = opened()
        refusal = space.place(w.Slot.RESOURCE, "alert", "alert 7", source="")
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "source")

    def test_there_is_no_general_purpose_slot(self):
        space = opened()
        for made_up in ("context", "extra", "misc", "Evidence", "EVIDENCE", 7, None):
            with self.subTest(slot=made_up):
                refusal = space.place(made_up, "k", "v", source="user")
                self.assertTrue(refusal)
                self.assertEqual(refusal.bound, "slot")


class AContradictionIsRefusedNotOverwritten(unittest.TestCase):
    """A value that changes between understanding and acting changes the wrong thing."""

    def test_a_different_value_under_the_same_key_is_refused(self):
        space = opened()
        space.place(w.Slot.RESOURCE, "alert", "alert 7 (Bitcoin)", source="user")
        refusal = space.place(w.Slot.RESOURCE, "alert", "alert 9 (Ethereum)", source="retrieval")
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "conflict")
        self.assertEqual(space.value(w.Slot.RESOURCE, "alert"), "alert 7 (Bitcoin)")

    def test_placing_the_same_value_twice_is_not_an_error_and_costs_nothing(self):
        space = opened()
        space.place(w.Slot.CONSTRAINT, "scope", "only the bitcoin one", source="user")
        before = space.chars
        self.assertFalse(space.place(w.Slot.CONSTRAINT, "scope", "only the bitcoin one",
                                     source="user"))
        self.assertEqual(len(space), 1)
        self.assertEqual(space.chars, before)

    def test_a_correction_goes_through_revise_and_is_recorded(self):
        space = opened()
        space.place(w.Slot.RESOURCE, "alert", "alert 7 (Bitcoin)", source="user")
        self.assertFalse(space.revise(w.Slot.RESOURCE, "alert", "alert 9 (Ethereum)",
                                      source="user"))
        self.assertEqual(space.value(w.Slot.RESOURCE, "alert"), "alert 9 (Ethereum)")
        self.assertEqual(space.inspect()["revised"], 1)
        self.assertTrue(space.items(w.Slot.RESOURCE)[0].revised)

    def test_revising_does_not_consume_a_second_slot(self):
        space = opened()
        space.place(w.Slot.GOAL, "goal", "pause my alert", source="user")
        self.assertFalse(space.revise(w.Slot.GOAL, "goal", "delete my alert", source="user"))
        self.assertEqual(len(space.items(w.Slot.GOAL)), 1)

    def test_revising_something_that_was_never_established_is_refused(self):
        space = opened()
        refusal = space.revise(w.Slot.GOAL, "goal", "pause my alert", source="user")
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "conflict")

    def test_a_key_that_keeps_being_re_resolved_is_refused_as_a_loop(self):
        # Revision was the last unbounded dimension: it replaces rather than adds, so it
        # costs no memory. It costs something else — a caller re-resolving the same key
        # forever is a loop that would otherwise surface as a request that quietly runs
        # out of time and reports abandonment with no cause attached.
        space = opened()
        space.place(w.Slot.RESOURCE, "alert", "v0", source="user")
        for index in range(1, w.MAX_REVISIONS + 1):
            self.assertFalse(space.revise(w.Slot.RESOURCE, "alert", f"v{index}",
                                          source="user"))
        refusal = space.revise(w.Slot.RESOURCE, "alert", "v99", source="user")
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "revisions")
        self.assertEqual(space.value(w.Slot.RESOURCE, "alert"),
                         f"v{w.MAX_REVISIONS}")

    def test_the_revision_count_is_kept_per_key_not_per_workspace(self):
        space = opened()
        for key in ("a", "b"):
            space.place(w.Slot.RESOURCE, key, "v0", source="user")
            for index in range(w.MAX_REVISIONS):
                self.assertFalse(
                    space.revise(w.Slot.RESOURCE, key, f"v{index + 1}", source="user"),
                    f"{key} ran out of revisions because another key used them",
                )

    def test_a_revision_still_respects_the_character_budget(self):
        space = opened(UNDX_WORKSPACE_MAX_CHARS="300")
        space.place(w.Slot.GOAL, "goal", "short", source="user")
        refusal = space.revise(w.Slot.GOAL, "goal", "x" * 400, source="user")
        self.assertTrue(refusal)
        self.assertEqual(space.value(w.Slot.GOAL, "goal"), "short")


class SecretsAreRefusedAtTheDoor(unittest.TestCase):
    """Not printed carefully afterwards. Refused before they are held at all."""

    def test_credential_shaped_values_never_enter(self):
        space = opened()
        for value in (
            "sk_live_abcdefghijklmnop1234",
            "xoxb-1234567890-abcdefghijkl",
            "AKIAIOSFODNN7EXAMPLE",
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "api_key = 0123456789abcdefghij",
        ):
            with self.subTest(value=value[:12]):
                refusal = space.place(w.Slot.EVIDENCE, "token", value,
                                      source="tool:x", owner=OWNER)
                self.assertTrue(refusal)
                self.assertEqual(refusal.bound, "secret")
                self.assertEqual(len(space), 0)

    def test_the_refusal_does_not_repeat_the_secret(self):
        # A refusal message that echoes what it refused has moved the credential into
        # the log rather than out of it.
        space = opened()
        secret = "sk_live_abcdefghijklmnop1234"
        refusal = space.place(w.Slot.EVIDENCE, "token", secret, source="tool:x", owner=OWNER)
        self.assertNotIn(secret, refusal.message)
        self.assertNotIn(secret[:16], refusal.message)

    def test_it_reuses_the_corpus_filter_rather_than_a_second_list(self):
        from services.undx_brain import corpus
        self.assertIs(w.SECRET_PATTERNS, corpus.SECRET_PATTERNS)

    def test_ordinary_text_about_credentials_still_gets_in(self):
        # A filter that fires on the word is a filter somebody turns off.
        space = opened()
        self.assertFalse(space.place(
            w.Slot.UNKNOWN, "auth", "it is not known whether their session token expired",
            source="user",
        ))


class ExpiryIsAbandonmentNotResumption(unittest.TestCase):
    """What it observed before the pause is no longer evidence of anything current."""

    def test_an_expired_context_refuses_everything(self):
        clock = FakeClock()
        space = opened(clock=clock, UNDX_WORKSPACE_TTL_SECONDS="60")
        self.assertFalse(space.place(w.Slot.GOAL, "goal", "pause my alert", source="user"))
        clock.advance(61)
        refusal = space.place(w.Slot.EVIDENCE, "e", "active", source="tool:x", owner=OWNER)
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "lifetime")
        self.assertFalse(space)

    def test_an_expired_context_carries_nothing_forward_even_if_marked_to_retain(self):
        clock = FakeClock()
        space = opened(clock=clock, UNDX_WORKSPACE_TTL_SECONDS="60")
        space.place(w.Slot.EVIDENCE, "balance", "portfolio is up 3%",
                    source="tool:x", owner=OWNER, retain=True)
        clock.advance(61)
        summary = space.close(completed=True)
        self.assertTrue(summary.abandoned)
        self.assertFalse(summary.completed)
        self.assertEqual(summary.retainable, ())

    def test_it_cannot_be_reopened_by_asking_again(self):
        # There is no cache on purpose: a function that handed back an existing context
        # for a returning user would make the timeout resumable.
        first = opened()
        second = opened()
        self.assertIsNot(first, second)
        first.place(w.Slot.GOAL, "goal", "pause my alert", source="user")
        self.assertEqual(len(second), 0)

    def test_a_disabled_context_is_never_reported_as_expired(self):
        space = w.open_workspace(OWNER, env={})
        self.assertFalse(space.expired())


class NothingBecomesDurableByOmission(unittest.TestCase):
    """A task's scratch must not turn into knowledge because nobody said not to."""

    def test_close_carries_forward_only_what_was_explicitly_marked(self):
        space = opened()
        space.place(w.Slot.EVIDENCE, "a", "alert 7 is active", source="tool:x", owner=OWNER)
        space.place(w.Slot.CONSTRAINT, "pref", "always ask before deleting",
                    source="user", retain=True)
        summary = space.close(completed=True)
        self.assertEqual([item.key for item in summary.retainable], ["pref"])

    def test_retain_is_off_unless_the_call_says_so(self):
        space = opened()
        space.place(w.Slot.EVIDENCE, "a", "active", source="tool:x", owner=OWNER)
        self.assertFalse(space.items(w.Slot.EVIDENCE)[0].retain)

    def test_there_is_no_flag_that_makes_everything_durable(self):
        # A setting that can be switched to "keep everything" is a setting that will be.
        from services.undx_brain import config
        names = {flag.name for flag in config.CATALOG}
        self.assertNotIn("UNDX_WORKSPACE_RETAIN_BY_DEFAULT", names)

    def test_the_module_persists_nothing_itself(self):
        # close() returns a value. Handing it to memory is the caller's separate act,
        # which is the point at which a flag and an owner scope are in the way.
        with open(w.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("memory.write(", "memory.read(", "cur.execute", "INSERT INTO"):
            self.assertNotIn(forbidden, source)

    def test_closing_empties_it_and_refuses_further_entries(self):
        space = opened()
        space.place(w.Slot.GOAL, "goal", "pause my alert", source="user")
        space.close(completed=True)
        self.assertEqual(len(space), 0)
        refusal = space.place(w.Slot.GOAL, "goal", "something else", source="user")
        self.assertEqual(refusal.bound, "closed")

    def test_closing_twice_returns_the_same_summary(self):
        space = opened()
        space.place(w.Slot.CONSTRAINT, "pref", "ask first", source="user", retain=True)
        first = space.close(completed=True)
        second = space.close(completed=False)
        self.assertIs(first, second)


class CompletionIsStatedNotDefaulted(unittest.TestCase):
    """A workspace that reports success nobody claimed is a false success claim."""

    def test_completed_has_no_default(self):
        space = opened()
        with self.assertRaises(TypeError):
            space.close()

    def test_a_workspace_that_was_never_opened_cannot_report_completion(self):
        # It held nothing, observed nothing and bounded nothing. A summary of it saying
        # "completed" is a success claim about work this module has no evidence of.
        space = w.open_workspace(OWNER, env={})
        summary = space.close(completed=True)
        self.assertFalse(summary.completed)
        self.assertTrue(summary.reason)

    def test_an_incomplete_task_summarises_as_incomplete(self):
        space = opened()
        space.place(w.Slot.UNKNOWN, "which", "which of their three alerts they mean",
                    source="planner")
        summary = space.close(completed=False)
        self.assertFalse(summary)
        self.assertEqual(summary.unknowns, ("which of their three alerts they mean",))

    def test_the_summary_carries_the_goal_whatever_the_key_was_called(self):
        space = opened()
        space.place(w.Slot.GOAL, "objective", "pause the bitcoin alert", source="user")
        self.assertEqual(space.close(completed=True).goal, "pause the bitcoin alert")

    def test_risks_and_unknowns_survive_into_the_summary(self):
        space = opened()
        space.place(w.Slot.RISK, "r", "this deletes history that cannot be restored",
                    source="policy")
        space.place(w.Slot.UNKNOWN, "u", "whether the alert already fired", source="planner")
        summary = space.close(completed=False)
        self.assertEqual(len(summary.risks), 1)
        self.assertEqual(len(summary.unknowns), 1)

    def test_the_counts_are_honest_about_refusals(self):
        space = opened()
        space.place(w.Slot.GOAL, "goal", "pause my alert", source="user")
        space.place(w.Slot.GOAL, "second", "and mute the chat", source="user")
        summary = space.close(completed=True)
        self.assertEqual(summary.placed, 1)
        self.assertEqual(summary.refused, 1)


class InspectionShowsShapeNotContent(unittest.TestCase):
    """This is the surface that ends up in a log line outliving the request."""

    def test_it_contains_no_values_and_no_keys(self):
        # Keys here are deliberately not slot names: the slot vocabulary is structural
        # and appears in the report on purpose, so a key called "goal" would make this
        # test pass or fail for the wrong reason.
        space = opened()
        space.place(w.Slot.GOAL, "objective", "pause the bitcoin alert", source="user")
        space.place(w.Slot.EVIDENCE, "holdings", "portfolio is up 3%",
                    source="tool:pulse.portfolio", owner=OWNER)
        blob = repr(space.inspect())
        for private in ("pause the bitcoin alert", "portfolio is up 3%",
                        "objective", "holdings"):
            self.assertNotIn(private, blob, f"inspect() leaked {private!r}")

    def test_it_reports_the_owner_without_naming_the_account(self):
        space = opened()
        self.assertTrue(space.inspect()["owner_scoped"])
        self.assertNotIn(str(OWNER), repr(space.inspect()))

    def test_it_names_where_entries_came_from(self):
        space = opened()
        space.place(w.Slot.EVIDENCE, "a", "active", source="tool:pulse.alerts.list",
                    owner=OWNER)
        self.assertEqual(space.inspect()["sources"], ["tool:pulse.alerts.list"])

    def test_it_is_stable_between_two_identical_workspaces(self):
        # So that a diff between two requests means something rather than reflecting
        # set iteration order.
        def build() -> dict:
            space = opened()
            space.place(w.Slot.EVIDENCE, "a", "active", source="tool:b", owner=OWNER)
            space.place(w.Slot.EVIDENCE, "b", "paused", source="tool:a", owner=OWNER)
            report = space.inspect()
            report.pop("elapsed_seconds")
            return report

        self.assertEqual(build(), build())

    def test_it_explains_why_a_closed_workspace_is_closed(self):
        space = w.open_workspace(OWNER, env={})
        self.assertIn("disabled", space.inspect()["reason"])


class ItReusesRatherThanRedeclares(unittest.TestCase):
    """Duplicated safety machinery is machinery that drifts apart."""

    def test_a_refusal_is_the_bounds_refusal(self):
        space = opened()
        refusal = space.place("nonsense", "k", "v", source="user")
        self.assertIsInstance(refusal, Refusal)

    def test_an_accepted_entry_returns_a_falsy_refusal(self):
        space = opened()
        self.assertFalse(space.place(w.Slot.GOAL, "goal", "pause my alert", source="user"))

    def test_the_flags_are_declared_in_the_catalog(self):
        from services.undx_brain import config
        names = {flag.name for flag in config.CATALOG}
        for declared in (
            "UNDX_BRAIN_WORKSPACE_ENABLED", "UNDX_WORKSPACE_MAX_ITEMS",
            "UNDX_WORKSPACE_MAX_CHARS", "UNDX_WORKSPACE_TTL_SECONDS",
        ):
            self.assertIn(declared, names)

    def test_the_switch_fails_closed(self):
        from services.undx_brain import config
        by_name = {flag.name: flag for flag in config.CATALOG}
        self.assertEqual(by_name["UNDX_BRAIN_WORKSPACE_ENABLED"].fail, "closed")


class ConfidenceFallsToZeroWhenUnreadable(unittest.TestCase):
    """A malformed number must never be the reason UNDX sounded certain."""

    def test_an_unparseable_confidence_is_zero(self):
        space = opened()
        for raw in ("high", None, object(), float("nan"), True):
            with self.subTest(raw=raw):
                space = opened()
                space.place(w.Slot.EVIDENCE, "a", "active", source="tool:x",
                            owner=OWNER, confidence=raw)
                self.assertEqual(space.items(w.Slot.EVIDENCE)[0].confidence, 0.0)

    def test_it_is_clamped_to_the_unit_interval(self):
        space = opened()
        space.place(w.Slot.EVIDENCE, "a", "active", source="t", owner=OWNER, confidence=9.0)
        space.place(w.Slot.EVIDENCE, "b", "paused", source="t", owner=OWNER, confidence=-9.0)
        self.assertEqual(space.items(w.Slot.EVIDENCE)[0].confidence, 1.0)
        self.assertEqual(space.items(w.Slot.EVIDENCE)[1].confidence, 0.0)


class EntriesCannotBeEditedAfterTheFact(unittest.TestCase):
    """A frozen entry is one whose provenance still means something later."""

    def test_an_item_is_immutable(self):
        space = opened()
        space.place(w.Slot.GOAL, "goal", "pause my alert", source="user")
        item = space.items(w.Slot.GOAL)[0]
        with self.assertRaises(Exception):
            item.value = "delete my alert"  # type: ignore[misc]

    def test_items_returns_a_copy(self):
        space = opened()
        space.place(w.Slot.GOAL, "goal", "pause my alert", source="user")
        got = space.items()
        self.assertIsInstance(got, tuple)
        self.assertEqual(len(space), 1)

    def test_a_summary_is_immutable(self):
        summary = opened().close(completed=False)
        with self.assertRaises(Exception):
            summary.completed = True  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
