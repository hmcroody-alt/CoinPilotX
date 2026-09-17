"""The "…" menu on an admin music row, checked against the thing it must agree with.

`available_actions()` decides what the per-track menu offers. It has no authority
of its own -- every endpoint re-resolves the actor and re-plans the transition --
so the failure it can cause is not a privilege escalation but a credibility one:

* an action offered that the server refuses teaches the operator that refusals
  are noise, which is exactly the habit you do not want in the person holding the
  permanent-delete button;
* an action withheld that the server would allow is indistinguishable, from the
  admin page, from the feature not existing.

So the guarantee is agreement, and the way to pin it is to ask `plan_transition`
rather than to restate the table: a test that hardcodes "ACTIVE offers takedown
and quarantine" passes just as happily when someone edits `TRANSITIONS` and
forgets the menu. Every case here drives the real planner and the real
permission resolver.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import music_authority as ma  # noqa: E402

ALL_STATES = (
    ma.STATE_ACTIVE,
    ma.STATE_TAKEN_DOWN,
    ma.STATE_QUARANTINED,
    ma.STATE_PURGE_PENDING,
    ma.STATE_PURGED,
)

# The two non-transition entries the admin row adds around the state changes.
# They are reads, so they carry no state precondition and are not in TRANSITIONS.
READ_ONLY_ACTIONS = ("impact", "audit")


def _all_permissions(granted=True):
    return {name: granted for name in ma.MUSIC_PERMISSIONS}


class MenuAgreesWithThePlannerTests(unittest.TestCase):
    def test_every_offered_action_is_one_the_planner_accepts(self):
        """No menu item can produce a 409.

        Driven through `plan_transition` for every state, so a future edit to
        `TRANSITIONS` that the menu does not follow fails here.
        """
        for state in ALL_STATES:
            for action in ma.available_actions(state):
                with self.subTest(state=state, action=action):
                    try:
                        ma.plan_transition(action, state)
                    except ma.AuthorityError as exc:
                        self.fail(
                            "menu offers %s on a %s track but the server refuses "
                            "it: %s" % (action, state, exc)
                        )

    def test_every_action_the_planner_would_change_is_offered(self):
        """The other direction: nothing reachable is hidden.

        Without this the suite would pass on a menu that offers nothing at all.
        """
        for state in ALL_STATES:
            for action in ma.ACTION_ORDER:
                try:
                    _, changed = ma.plan_transition(action, state)
                except ma.AuthorityError:
                    continue
                if not changed:
                    continue
                with self.subTest(state=state, action=action):
                    self.assertIn(
                        action,
                        ma.available_actions(state),
                        "%s would change a %s track but the menu hides it" % (action, state),
                    )

    def test_no_offered_action_is_a_no_op(self):
        """`changed: false` is a retry answer, not a menu item.

        `TRANSITIONS` deliberately maps five self-transitions -- restore on an
        ACTIVE track and friends -- so a client retrying a timed-out request gets
        200 instead of 409. Surfacing those as menu entries would offer "Restore"
        on a track that is already active, which reads as a broken state column.
        """
        for state in ALL_STATES:
            for action in ma.available_actions(state):
                with self.subTest(state=state, action=action):
                    new_state, changed = ma.plan_transition(action, state)
                    self.assertTrue(
                        changed,
                        "menu offers %s on a %s track, which would do nothing "
                        "(stays %s)" % (action, state, new_state),
                    )

    def test_a_purged_track_offers_no_state_change(self):
        """PURGED is terminal: the bytes are gone, there is nothing left to do."""
        self.assertEqual(ma.available_actions(ma.STATE_PURGED), [])

    def test_an_unknown_state_is_treated_as_active(self):
        """Rows predating `lifecycle_state` have NULL, not a state.

        `normalize_state` maps those to ACTIVE, and the menu must follow: the
        alternative is a catalogue of legacy tracks with an empty "…" menu, i.e.
        exactly the tracks an owner is most likely to need to remove.
        """
        self.assertEqual(ma.available_actions(None), ma.available_actions(ma.STATE_ACTIVE))
        self.assertEqual(ma.available_actions(""), ma.available_actions(ma.STATE_ACTIVE))


class LegalHoldTests(unittest.TestCase):
    def test_legal_hold_removes_purge_from_the_menu(self):
        offered = ma.available_actions(ma.STATE_PURGE_PENDING, legal_hold=True)
        self.assertNotIn(ma.ACTION_PURGE, offered)

    def test_without_the_hold_purge_is_offered_on_the_same_state(self):
        """Positive control: the exclusion above is the hold, not the state."""
        offered = ma.available_actions(ma.STATE_PURGE_PENDING, legal_hold=False)
        self.assertIn(ma.ACTION_PURGE, offered)

    def test_a_held_track_can_still_be_restricted_and_restored(self):
        """A hold blocks destruction, not moderation.

        Reading it as "freeze the row" would mean a track under legal hold could
        not be taken down while the claim it is held for is being argued.
        """
        offered = ma.available_actions(ma.STATE_ACTIVE, legal_hold=True)
        self.assertIn(ma.ACTION_TAKEDOWN, offered)
        self.assertIn(ma.ACTION_QUARANTINE, offered)
        self.assertIn(ma.ACTION_RESTORE, ma.available_actions(ma.STATE_TAKEN_DOWN, legal_hold=True))

    def test_the_planner_refuses_the_purge_the_menu_withheld(self):
        """The menu is not the enforcement. Prove the server refuses it too."""
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.plan_transition(ma.ACTION_PURGE, ma.STATE_PURGE_PENDING, legal_hold=True)
        self.assertEqual(caught.exception.error_code, "music_legal_hold")


class PermissionFilterTests(unittest.TestCase):
    def test_no_permissions_means_an_empty_menu(self):
        for state in ALL_STATES:
            with self.subTest(state=state):
                self.assertEqual(
                    ma.available_actions(state, permissions=_all_permissions(False)), []
                )

    def test_takedown_only_admin_cannot_see_restore_or_purge(self):
        """`music.takedown` is not a general music grant.

        Split deliberately: the person who can pull a track during an incident is
        not automatically the person who can put it back or destroy it.
        """
        perms = _all_permissions(False)
        perms["music.takedown"] = True
        offered = ma.available_actions(ma.STATE_TAKEN_DOWN, permissions=perms)
        self.assertIn(ma.ACTION_QUARANTINE, offered)
        self.assertNotIn(ma.ACTION_RESTORE, offered)
        self.assertNotIn(ma.ACTION_SCHEDULE_PURGE, offered)
        self.assertNotIn(ma.ACTION_PURGE, offered)

    def test_restore_only_admin_sees_exactly_restore(self):
        perms = _all_permissions(False)
        perms["music.restore"] = True
        self.assertEqual(
            ma.available_actions(ma.STATE_TAKEN_DOWN, permissions=perms), [ma.ACTION_RESTORE]
        )

    def test_the_filter_matches_action_permissions_for_every_action(self):
        """Each action is gated by its own entry in `ACTION_PERMISSIONS`.

        Granting one permission at a time and asserting nothing else appears
        catches a mis-keyed entry -- e.g. purge gated on `music.takedown` --
        which no single-actor test would show.
        """
        for action, required in ma.ACTION_PERMISSIONS.items():
            perms = _all_permissions(False)
            perms[required] = True
            for state in ALL_STATES:
                for offered in ma.available_actions(state, permissions=perms):
                    with self.subTest(granted=required, state=state, offered=offered):
                        self.assertEqual(
                            ma.ACTION_PERMISSIONS[offered],
                            required,
                            "holding only %s surfaced %s, which requires %s"
                            % (required, offered, ma.ACTION_PERMISSIONS[offered]),
                        )

    def test_permissions_none_answers_the_pure_state_question(self):
        """`None` is not "no permissions" -- the state-machine tests rely on it."""
        self.assertNotEqual(ma.available_actions(ma.STATE_ACTIVE, permissions=None), [])


class MenuOrderTests(unittest.TestCase):
    def test_delete_permanently_is_never_above_a_recoverable_action(self):
        """Ordering is a safety property, not decoration.

        Purge sits last in `ACTION_ORDER` so the furthest item from the cursor is
        the irreversible one; a reordering that floats it up is the kind of edit
        that looks cosmetic in review.
        """
        for state in ALL_STATES:
            offered = ma.available_actions(state)
            if ma.ACTION_PURGE in offered:
                with self.subTest(state=state):
                    self.assertEqual(offered[-1], ma.ACTION_PURGE)

    def test_the_menu_follows_action_order(self):
        for state in ALL_STATES:
            offered = ma.available_actions(state)
            with self.subTest(state=state):
                self.assertEqual(
                    offered, [a for a in ma.ACTION_ORDER if a in offered], "menu came out unordered"
                )

    def test_action_order_covers_every_action_in_the_state_machine(self):
        """A new action added to `TRANSITIONS` but not to `ACTION_ORDER` would be
        unreachable from the admin page and silently so."""
        in_transitions = {action for action, _ in ma.TRANSITIONS}
        self.assertEqual(set(ma.ACTION_ORDER), in_transitions)

    def test_every_ordered_action_has_a_permission(self):
        for action in ma.ACTION_ORDER:
            self.assertIn(action, ma.ACTION_PERMISSIONS)


class RenderedRowTests(unittest.TestCase):
    """The row markup carries the action list. That is the part the browser reads."""

    @classmethod
    def setUpClass(cls):
        import tempfile

        handle, path = tempfile.mkstemp(suffix=".db", prefix="music_menu_")
        os.close(handle)
        os.environ.setdefault("DATABASE_URL", "sqlite:///%s" % path)
        import bot  # noqa: E402

        cls.bot = bot

    def _row(self, state, *, legal_hold=0, permissions=None):
        track = {
            "id": 4242,
            "title": "A Song",
            "artist": "An Artist",
            "uploader_user_id": 77,
            "lifecycle_state": state,
            "legal_hold": legal_hold,
            "active": 1,
            "approved_by_admin": 1,
            "safety_status": "approved",
            "updated_at": "2026-09-17T00:00:00",
        }
        return self.bot.admin_music_library_row(
            track, _all_permissions(True) if permissions is None else permissions
        )

    def _actions_in(self, html):
        import re

        match = re.search(r"data-actions='([^']*)'", html)
        return match.group(1).split(",") if match and match.group(1) else []

    def test_the_rendered_row_offers_exactly_what_available_actions_returned(self):
        """The markup is generated from the function, not alongside it."""
        for state in ALL_STATES:
            with self.subTest(state=state):
                expected = list(READ_ONLY_ACTIONS[:1])
                expected += ma.available_actions(state, permissions=_all_permissions(True))
                expected += list(READ_ONLY_ACTIONS[1:])
                self.assertEqual(self._actions_in(self._row(state)), expected)

    def test_a_held_track_renders_without_the_purge_entry(self):
        html = self._row(ma.STATE_PURGE_PENDING, legal_hold=1)
        self.assertNotIn(ma.ACTION_PURGE, self._actions_in(html))
        self.assertIn(ma.ACTION_PURGE, self._actions_in(self._row(ma.STATE_PURGE_PENDING)))

    def test_an_actor_with_no_music_permissions_gets_no_control_at_all(self):
        """Not a disabled button: an admin who cannot act on music should not be
        shown a control that only refuses, and `music.view_all` gates the two
        read actions as well."""
        html = self._row(ma.STATE_ACTIVE, permissions=_all_permissions(False))
        self.assertNotIn("music-dots", html)
        self.assertIn("&mdash;", html)

    def test_the_track_id_and_state_travel_with_the_button(self):
        """`expected_state` is sent from this attribute, so a row that renders a
        stale or missing state would turn the 409 guard off for that row."""
        html = self._row(ma.STATE_TAKEN_DOWN)
        self.assertIn("data-track='4242'", html)
        self.assertIn("data-state='TAKEN_DOWN'", html)


if __name__ == "__main__":
    unittest.main()
