"""The owner music takedown policy, tested away from Flask and the database.

``services/music_authority.py`` deliberately imports nothing from ``bot``, so
every decision it makes -- who may act, what an action does to a track, what a
player is told about a track it may no longer have -- can be exercised directly.
That matters because the decisions are the security boundary: a route test can
only show that *this* route refused, while these show the rule itself refuses.

The properties worth stating up front, because they are the ones a future
change is most likely to break:

* **Nothing in the request selects an identity.** ``resolve_actor`` takes an
  already-validated session admin and an already-proven account id. A body
  carrying ``role``, ``is_owner`` or ``admin_user_id`` has no way in.
* **Permission is a named grant, not a role name.** ``require_permission``
  delegates to the caller's permission check and never inspects the actor's
  role, email or id itself.
* **Takedown and deletion are different operations.** No single transition goes
  from ``ACTIVE`` to ``PURGED``.
* **Servability is an allowlist.** A state added later is unservable until
  someone deliberately adds it to ``SERVABLE_STATES``.
"""

import unittest

from services import music_authority as ma


def allow_all(actor, permission):
    return True


def deny_all(actor, permission):
    return False


def grants(*permissions):
    held = set(permissions)
    return lambda actor, permission: permission in held


class PermissionVocabularyTests(unittest.TestCase):
    def test_the_six_mission_permissions_exist(self):
        self.assertEqual(
            {
                "music.view_all", "music.moderate", "music.takedown",
                "music.restore", "music.purge", "music.manage_rights",
            },
            set(ma.MUSIC_PERMISSIONS),
        )

    def test_every_action_maps_to_a_real_permission(self):
        for action, permission in ma.ACTION_PERMISSIONS.items():
            self.assertIn(permission, ma.MUSIC_PERMISSIONS, action)

    def test_an_unknown_permission_is_a_server_error_not_a_silent_allow(self):
        """A typo in a route's permission string must not become "allowed"."""
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.require_permission({"id": 1}, "music.typo", allow_all)
        self.assertEqual("music_permission_unknown", caught.exception.error_code)
        self.assertEqual(500, caught.exception.status)

    def test_pulse_moderate_does_not_appear_anywhere_in_this_module(self):
        """The old catalog-review permission must not imply takedown authority."""
        self.assertNotIn("pulse.moderate", ma.MUSIC_PERMISSIONS)
        self.assertNotIn("pulse.moderate", set(ma.ACTION_PERMISSIONS.values()))


class ResolveActorTests(unittest.TestCase):
    """Identity comes from a validated session or a stored link. Nothing else."""

    def test_a_web_admin_session_resolves_to_that_admin(self):
        actor = ma.resolve_actor({"id": 7, "role": "owner"}, 0, lambda _: None)
        self.assertEqual(7, actor["id"])

    def test_an_account_id_resolves_through_the_stored_link(self):
        actor = ma.resolve_actor(
            None, 42, lambda uid: {"id": 3, "role": "owner", "status": "active"} if uid == 42 else None
        )
        self.assertEqual(3, actor["id"])

    def test_an_account_with_no_admin_row_has_no_identity(self):
        """An ordinary user is not merely unpermitted -- they are unknown."""
        self.assertIsNone(ma.resolve_actor(None, 99, lambda _: None))

    def test_a_suspended_admin_row_does_not_resolve(self):
        self.assertIsNone(
            ma.resolve_actor(None, 42, lambda _: {"id": 3, "role": "owner", "status": "suspended"})
        )

    def test_the_web_session_wins_when_both_legs_are_present(self):
        """An admin working in the browser is never re-identified as someone else."""
        actor = ma.resolve_actor(
            {"id": 7, "role": "support"}, 42, lambda _: {"id": 3, "role": "owner", "status": "active"}
        )
        self.assertEqual(7, actor["id"])

    def test_no_credentials_at_all_resolves_to_nothing(self):
        self.assertIsNone(ma.resolve_actor(None, 0, lambda _: None))

    def test_the_lookup_is_called_with_the_proven_id_and_nothing_else(self):
        seen = []

        def record(uid):
            seen.append(uid)
            return None

        ma.resolve_actor(None, 42, record)
        self.assertEqual([42], seen)


class RequirePermissionTests(unittest.TestCase):
    def test_no_identity_is_401_not_403(self):
        """401 says "authenticate as someone who could act"; 403 says "you may not"."""
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.require_permission(None, "music.takedown", allow_all)
        self.assertEqual(401, caught.exception.status)
        self.assertEqual("music_authority_required", caught.exception.error_code)

    def test_an_identity_without_the_grant_is_403(self):
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.require_permission({"id": 3, "role": "owner"}, "music.takedown", deny_all)
        self.assertEqual(403, caught.exception.status)
        self.assertEqual("music_permission_denied", caught.exception.error_code)

    def test_the_decision_comes_from_the_permission_check_not_the_role(self):
        """A row claiming role=owner is refused when the grant check says no.

        This is the property that stops `admin_users.role` from becoming the
        security rule by accident.
        """
        owner_shaped = {"id": 3, "role": "owner", "email": "owner@example.com"}
        with self.assertRaises(ma.AuthorityError):
            ma.require_permission(owner_shaped, "music.purge", deny_all)
        self.assertEqual(owner_shaped, ma.require_permission(owner_shaped, "music.purge", allow_all))

    def test_permissions_are_checked_one_at_a_time(self):
        """Holding takedown does not confer purge."""
        actor = {"id": 3, "role": "moderator"}
        check = grants("music.takedown")
        ma.require_permission(actor, "music.takedown", check)
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.require_permission(actor, "music.purge", check)
        self.assertEqual(403, caught.exception.status)

    def test_the_checked_permission_is_the_one_passed_in(self):
        asked = []
        ma.require_permission({"id": 1}, "music.restore", lambda a, p: asked.append(p) or True)
        self.assertEqual(["music.restore"], asked)


class StateMachineTests(unittest.TestCase):
    def test_takedown_is_not_deletion(self):
        """No transition leads from ACTIVE straight to PURGED."""
        for (action, source), destination in ma.TRANSITIONS.items():
            if source == ma.STATE_ACTIVE:
                self.assertNotEqual(ma.STATE_PURGED, destination, action)

    def test_purging_requires_passing_through_purge_pending(self):
        sources = [src for (act, src) in ma.TRANSITIONS if act == ma.ACTION_PURGE]
        self.assertEqual([ma.STATE_PURGE_PENDING], sources)

    def test_purged_is_terminal(self):
        for (action, source) in ma.TRANSITIONS:
            self.assertNotEqual(ma.STATE_PURGED, source, action)

    def test_takedown_then_restore_round_trips(self):
        state, changed = ma.plan_transition(ma.ACTION_TAKEDOWN, ma.STATE_ACTIVE)
        self.assertEqual((ma.STATE_TAKEN_DOWN, True), (state, changed))
        state, changed = ma.plan_transition(ma.ACTION_RESTORE, state)
        self.assertEqual((ma.STATE_ACTIVE, True), (state, changed))

    def test_repeating_an_applied_action_is_a_no_op_not_an_error(self):
        """A client retrying a request whose response it never saw must not fail."""
        state, changed = ma.plan_transition(ma.ACTION_TAKEDOWN, ma.STATE_TAKEN_DOWN)
        self.assertEqual((ma.STATE_TAKEN_DOWN, False), (state, changed))

    def test_restoring_a_purged_track_is_refused(self):
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.plan_transition(ma.ACTION_RESTORE, ma.STATE_PURGED)
        self.assertEqual(409, caught.exception.status)
        self.assertEqual("music_transition_refused", caught.exception.error_code)

    def test_purging_a_track_that_was_never_scheduled_is_refused(self):
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.plan_transition(ma.ACTION_PURGE, ma.STATE_TAKEN_DOWN)
        self.assertEqual(409, caught.exception.status)

    def test_a_quarantine_may_follow_a_takedown(self):
        """Escalation must not require restoring the track first."""
        state, changed = ma.plan_transition(ma.ACTION_QUARANTINE, ma.STATE_TAKEN_DOWN)
        self.assertEqual((ma.STATE_QUARANTINED, True), (state, changed))

    def test_cancel_purge_returns_to_taken_down(self):
        state, changed = ma.plan_transition(ma.ACTION_CANCEL_PURGE, ma.STATE_PURGE_PENDING)
        self.assertEqual((ma.STATE_TAKEN_DOWN, True), (state, changed))


class StaleStateTests(unittest.TestCase):
    def test_a_matching_expected_state_proceeds(self):
        state, changed = ma.plan_transition(
            ma.ACTION_TAKEDOWN, ma.STATE_ACTIVE, expected_state=ma.STATE_ACTIVE
        )
        self.assertEqual((ma.STATE_TAKEN_DOWN, True), (state, changed))

    def test_a_stale_expected_state_is_409(self):
        """Two moderators on stale screens must not overwrite each other."""
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.plan_transition(
                ma.ACTION_RESTORE, ma.STATE_PURGE_PENDING, expected_state=ma.STATE_TAKEN_DOWN
            )
        self.assertEqual(409, caught.exception.status)
        self.assertEqual("music_state_conflict", caught.exception.error_code)

    def test_omitting_expected_state_does_not_enforce_it(self):
        state, _ = ma.plan_transition(ma.ACTION_RESTORE, ma.STATE_PURGE_PENDING)
        self.assertEqual(ma.STATE_ACTIVE, state)

    def test_the_conflict_is_raised_before_the_legal_hold_check(self):
        """A stale caller is told its view is stale, not that a hold exists."""
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.plan_transition(
                ma.ACTION_PURGE, ma.STATE_PURGE_PENDING,
                expected_state=ma.STATE_ACTIVE, legal_hold=True,
            )
        self.assertEqual("music_state_conflict", caught.exception.error_code)


class LegalHoldTests(unittest.TestCase):
    def test_a_hold_blocks_purge_with_its_own_code(self):
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.plan_transition(ma.ACTION_PURGE, ma.STATE_PURGE_PENDING, legal_hold=True)
        self.assertEqual("music_legal_hold", caught.exception.error_code)
        self.assertEqual(409, caught.exception.status)

    def test_a_hold_does_not_block_a_takedown(self):
        """A hold preserves evidence; it must not force the track to stay public."""
        state, changed = ma.plan_transition(ma.ACTION_TAKEDOWN, ma.STATE_ACTIVE, legal_hold=True)
        self.assertEqual((ma.STATE_TAKEN_DOWN, True), (state, changed))

    def test_a_hold_does_not_block_scheduling(self):
        state, _ = ma.plan_transition(
            ma.ACTION_SCHEDULE_PURGE, ma.STATE_TAKEN_DOWN, legal_hold=True
        )
        self.assertEqual(ma.STATE_PURGE_PENDING, state)

    def test_only_purge_is_treated_as_destructive(self):
        self.assertEqual({ma.ACTION_PURGE}, set(ma.DESTRUCTIVE_ACTIONS))


class ReasonTests(unittest.TestCase):
    def test_the_ten_mission_reason_codes_exist(self):
        self.assertEqual(
            [
                "COPYRIGHT", "LICENSING_EXPIRED", "POLICY_VIOLATION",
                "UNAUTHORIZED_UPLOAD", "DUPLICATE", "MALWARE_OR_UNSAFE_FILE",
                "PRIVACY_REQUEST", "UPLOADER_REQUEST", "OWNER_DECISION", "OTHER",
            ],
            list(ma.REASON_CODES),
        )

    def test_a_reason_code_is_required(self):
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.validate_reason(ma.ACTION_TAKEDOWN, "", "")
        self.assertEqual("music_reason_code_invalid", caught.exception.error_code)

    def test_an_invented_reason_code_is_refused(self):
        with self.assertRaises(ma.AuthorityError):
            ma.validate_reason(ma.ACTION_TAKEDOWN, "BECAUSE_I_SAID_SO", "note")

    def test_a_reason_code_is_case_insensitive(self):
        code, _ = ma.validate_reason(ma.ACTION_TAKEDOWN, "copyright", "")
        self.assertEqual("COPYRIGHT", code)

    def test_other_requires_a_note(self):
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.validate_reason(ma.ACTION_TAKEDOWN, "OTHER", "   ")
        self.assertEqual("music_reason_note_required", caught.exception.error_code)

    def test_a_purge_requires_a_note_whatever_the_code(self):
        with self.assertRaises(ma.AuthorityError) as caught:
            ma.validate_reason(ma.ACTION_PURGE, "COPYRIGHT", "")
        self.assertEqual("music_reason_note_required", caught.exception.error_code)

    def test_a_takedown_with_a_specific_code_needs_no_note(self):
        code, note = ma.validate_reason(ma.ACTION_TAKEDOWN, "COPYRIGHT", "")
        self.assertEqual(("COPYRIGHT", ""), (code, note))

    def test_a_note_is_bounded(self):
        _, note = ma.validate_reason(ma.ACTION_TAKEDOWN, "COPYRIGHT", "x" * 5000)
        self.assertEqual(2000, len(note))


class ServabilityTests(unittest.TestCase):
    def test_only_active_is_servable(self):
        for state in ma.LIFECYCLE_STATES:
            expected = state == ma.STATE_ACTIVE
            self.assertEqual(expected, ma.is_servable({"lifecycle_state": state}), state)

    def test_servable_states_is_an_allowlist(self):
        """A state added later must be unservable until someone decides otherwise."""
        self.assertEqual({ma.STATE_ACTIVE}, set(ma.SERVABLE_STATES))

    def test_a_row_predating_the_column_is_servable(self):
        """An empty lifecycle_state means "written before this existed", not removed."""
        self.assertTrue(ma.is_servable({}))
        self.assertTrue(ma.is_servable({"lifecycle_state": None}))

    def test_a_legacy_removal_is_honoured_without_a_lifecycle_state(self):
        """The old admin route stamps only these; both writers must agree."""
        self.assertFalse(ma.is_servable({"removed_at": "2026-09-17T00:00:00"}))
        self.assertFalse(ma.is_servable({"safety_status": "removed"}))
        self.assertFalse(ma.is_servable({"safety_status": "BLOCKED"}))

    def test_an_active_approved_row_is_servable(self):
        self.assertTrue(
            ma.is_servable(
                {"lifecycle_state": "ACTIVE", "safety_status": "approved", "removed_at": ""}
            )
        )

    def test_an_unrecognised_state_does_not_resurrect_a_removed_track(self):
        """normalize_state falls back to ACTIVE, so the legacy trio must still bite."""
        self.assertFalse(
            ma.is_servable({"lifecycle_state": "WHO_KNOWS", "safety_status": "removed"})
        )


class LegacyColumnTests(unittest.TestCase):
    """The trio the ~10 existing filtered read paths already consult."""

    def test_a_removed_state_writes_the_removal_trio(self):
        for state in (ma.STATE_TAKEN_DOWN, ma.STATE_QUARANTINED,
                      ma.STATE_PURGE_PENDING, ma.STATE_PURGED):
            columns = ma.legacy_columns_for_state(state, now="NOW", actor_admin_id=9)
            self.assertEqual(0, columns["active"], state)
            self.assertEqual(0, columns["approved_by_admin"], state)
            self.assertEqual("removed", columns["safety_status"], state)
            self.assertEqual("NOW", columns["removed_at"], state)
            self.assertEqual(9, columns["removed_by_admin"], state)

    def test_active_clears_the_removal_trio(self):
        columns = ma.legacy_columns_for_state(ma.STATE_ACTIVE, now="NOW", actor_admin_id=9)
        self.assertEqual(1, columns["active"])
        self.assertEqual(1, columns["approved_by_admin"])
        self.assertEqual("approved", columns["safety_status"])
        self.assertEqual("", columns["removed_at"])
        self.assertIsNone(columns["removed_by_admin"])

    def test_the_trio_agrees_with_is_servable_in_every_state(self):
        """The two definitions of "removed" must not be able to disagree."""
        for state in ma.LIFECYCLE_STATES:
            columns = ma.legacy_columns_for_state(state, now="NOW", actor_admin_id=1)
            row = {"lifecycle_state": state, **columns}
            self.assertEqual(
                state == ma.STATE_ACTIVE, ma.is_servable(row), state
            )


class UnavailablePayloadTests(unittest.TestCase):
    def test_every_url_is_blank_and_the_flag_is_set(self):
        payload = ma.unavailable_audio_payload(ma.STATE_TAKEN_DOWN)
        self.assertTrue(payload["audio_unavailable"])
        for field in ("audio_url", "attached_audio_url", "preview_url"):
            self.assertEqual("", payload[field], field)

    def test_the_tracks_identity_goes_with_it(self):
        """A copyright or privacy takedown removes the metadata too."""
        payload = ma.unavailable_audio_payload()
        self.assertEqual("", payload["title"])
        self.assertEqual("", payload["artist"])
        self.assertEqual(0, payload["track_id"])
        self.assertEqual("", payload["waveform"])

    def test_it_does_not_touch_the_creators_mute_decision(self):
        """Unmuting would publish audio the creator chose to silence."""
        self.assertNotIn("original_audio_muted", ma.unavailable_audio_payload())

    def test_it_does_not_substitute_another_track(self):
        payload = ma.unavailable_audio_payload()
        self.assertEqual(0, payload["id"])
        self.assertEqual(0, payload["track_id"])

    def test_the_state_is_reported_so_the_client_can_explain(self):
        self.assertEqual(
            ma.STATE_QUARANTINED,
            ma.unavailable_audio_payload(ma.STATE_QUARANTINED)["audio_unavailable_state"],
        )

    def test_it_defaults_to_taken_down_rather_than_active(self):
        """A blank reason must never read as "this track is fine"."""
        self.assertEqual(
            ma.STATE_TAKEN_DOWN, ma.unavailable_audio_payload()["audio_unavailable_state"]
        )


class ErrorShapeTests(unittest.TestCase):
    def test_every_refusal_carries_a_machine_readable_code(self):
        """`pulseApi` in the native app reads `error_code`; a blank one is generic."""
        raised = []
        for call in (
            lambda: ma.require_permission(None, "music.takedown", allow_all),
            lambda: ma.require_permission({"id": 1}, "music.takedown", deny_all),
            lambda: ma.plan_transition(ma.ACTION_PURGE, ma.STATE_TAKEN_DOWN),
            lambda: ma.plan_transition(ma.ACTION_PURGE, ma.STATE_PURGE_PENDING, legal_hold=True),
            lambda: ma.validate_reason(ma.ACTION_TAKEDOWN, "NOPE", ""),
            lambda: ma.validate_reason(ma.ACTION_PURGE, "COPYRIGHT", ""),
        ):
            with self.assertRaises(ma.AuthorityError) as caught:
                call()
            raised.append(caught.exception.error_code)
            self.assertTrue(caught.exception.message)
            self.assertIn(caught.exception.status, (400, 401, 403, 409, 500))
        self.assertEqual(len(raised), len(set(raised)), "codes must be distinguishable")


if __name__ == "__main__":
    unittest.main()
