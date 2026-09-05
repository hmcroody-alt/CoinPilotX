"""Tests for the canonical multi-guest Live participant model.

These cover the properties the broadcast architecture depends on: that roles are
normalised to one vocabulary, that co-host is genuinely distinct from both host
and guest, that the stage ceiling is server-owned and cannot be raised past its
hard limit, and that remote identity is resolved by uid lookup rather than by
position.
"""

import os
import unittest
from unittest import mock

from services import live_participants as lp


class RoleNormalizationTests(unittest.TestCase):
    def test_known_roles_pass_through(self):
        for role in lp.LIVE_ROLES:
            self.assertEqual(lp.normalize_role(role), role)

    def test_historical_spellings_collapse(self):
        self.assertEqual(lp.normalize_role("co-host"), lp.ROLE_COHOST)
        self.assertEqual(lp.normalize_role("co_host"), lp.ROLE_COHOST)
        self.assertEqual(lp.normalize_role("moderator"), lp.ROLE_COHOST)
        self.assertEqual(lp.normalize_role("creator"), lp.ROLE_HOST)
        self.assertEqual(lp.normalize_role("publisher"), lp.ROLE_HOST)
        self.assertEqual(lp.normalize_role("panelist"), lp.ROLE_GUEST)
        self.assertEqual(lp.normalize_role("viewer"), lp.ROLE_AUDIENCE)
        self.assertEqual(lp.normalize_role("subscriber"), lp.ROLE_AUDIENCE)

    def test_case_and_whitespace_are_tolerated(self):
        self.assertEqual(lp.normalize_role("  CoHost  "), lp.ROLE_COHOST)
        self.assertEqual(lp.normalize_role("HOST"), lp.ROLE_HOST)

    def test_unknown_roles_degrade_to_audience_rather_than_raising(self):
        # An unrecognised role must cost the caller their publishing rights, not
        # take the request down. "You are a viewer" is a safe failure; a 500 on
        # a live broadcast is not.
        for value in ("nonsense", "", None, 7, "superhost"):
            self.assertEqual(lp.normalize_role(value), lp.ROLE_AUDIENCE)

    def test_wire_role_preserves_the_legacy_audience_spelling(self):
        self.assertEqual(lp.wire_role(lp.ROLE_AUDIENCE), "viewer")
        self.assertEqual(lp.wire_role("audience"), "viewer")
        for role in (lp.ROLE_HOST, lp.ROLE_COHOST, lp.ROLE_GUEST):
            self.assertEqual(lp.wire_role(role), role)


class PermissionTests(unittest.TestCase):
    def test_only_publishing_roles_may_publish(self):
        self.assertTrue(lp.can_publish(lp.ROLE_HOST))
        self.assertTrue(lp.can_publish(lp.ROLE_COHOST))
        self.assertTrue(lp.can_publish(lp.ROLE_GUEST))
        self.assertFalse(lp.can_publish(lp.ROLE_AUDIENCE))

    def test_audience_has_nothing_to_initialise(self):
        # Every capability false is the point: an audience client has no camera
        # to open and no microphone to claim.
        self.assertFalse(any(lp.role_permissions(lp.ROLE_AUDIENCE).values()))
        self.assertEqual(lp.publish_sources(lp.ROLE_AUDIENCE), [])

    def test_cohost_moderates_but_cannot_end_the_broadcast(self):
        self.assertTrue(lp.can_moderate(lp.ROLE_COHOST))
        self.assertFalse(lp.can_end_live(lp.ROLE_COHOST))

    def test_guest_can_leave_but_cannot_moderate(self):
        self.assertFalse(lp.can_moderate(lp.ROLE_GUEST))
        self.assertFalse(lp.can_end_live(lp.ROLE_GUEST))
        self.assertTrue(lp.role_permissions(lp.ROLE_GUEST)["leave_stage"])

    def test_only_the_host_may_end_the_live(self):
        ending = [role for role in lp.LIVE_ROLES if lp.can_end_live(role)]
        self.assertEqual(ending, [lp.ROLE_HOST])

    def test_cohost_is_not_an_alias_for_guest_or_host(self):
        cohost = lp.role_permissions(lp.ROLE_COHOST)
        self.assertNotEqual(cohost, lp.role_permissions(lp.ROLE_GUEST))
        self.assertNotEqual(cohost, lp.role_permissions(lp.ROLE_HOST))

    def test_permissions_are_copies(self):
        mutated = lp.role_permissions(lp.ROLE_GUEST)
        mutated["end_live"] = True
        self.assertFalse(lp.role_permissions(lp.ROLE_GUEST)["end_live"])

    def test_publishers_publish_both_sources(self):
        for role in lp.PUBLISHING_ROLES:
            self.assertEqual(lp.publish_sources(role), ["microphone", "camera"])

    def test_every_role_has_a_label(self):
        for role in lp.LIVE_ROLES:
            self.assertTrue(lp.role_label(role))


class StageCapacityTests(unittest.TestCase):
    def test_default_limit(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LIVE_MAX_GUESTS", None)
            self.assertEqual(lp.max_guests(), lp.LIVE_MAX_GUESTS_DEFAULT)
            self.assertEqual(lp.max_publishers(), lp.LIVE_MAX_GUESTS_DEFAULT + 1)

    def test_limit_is_configurable_downward(self):
        with mock.patch.dict(os.environ, {"LIVE_MAX_GUESTS": "3"}):
            self.assertEqual(lp.max_guests(), 3)
            self.assertEqual(lp.max_publishers(), 4)

    def test_configuration_cannot_raise_past_the_hard_ceiling(self):
        # A portrait stage cannot render more faces than this legibly, and an
        # audience device cannot decode more video than this. Config may lower
        # the limit; nothing may raise it.
        with mock.patch.dict(os.environ, {"LIVE_MAX_GUESTS": "500"}):
            self.assertEqual(lp.max_guests(), lp.LIVE_MAX_GUESTS_HARD_CEILING)

    def test_negative_limit_clamps_to_zero(self):
        with mock.patch.dict(os.environ, {"LIVE_MAX_GUESTS": "-4"}):
            self.assertEqual(lp.max_guests(), 0)
            self.assertTrue(lp.stage_is_full(0))

    def test_malformed_limit_falls_back_rather_than_becoming_unbounded(self):
        with mock.patch.dict(os.environ, {"LIVE_MAX_GUESTS": "twelve"}):
            self.assertEqual(lp.max_guests(), lp.LIVE_MAX_GUESTS_DEFAULT)

    def test_capacity_snapshot_is_truthful_at_the_boundary(self):
        with mock.patch.dict(os.environ, {"LIVE_MAX_GUESTS": "6"}):
            self.assertEqual(
                lp.stage_capacity(5),
                {"max_guests": 6, "max_publishers": 7, "guests_active": 5, "slots_available": 1, "stage_full": False},
            )
            full = lp.stage_capacity(6)
            self.assertTrue(full["stage_full"])
            self.assertEqual(full["slots_available"], 0)

    def test_capacity_never_reports_negative_slots_when_over_subscribed(self):
        with mock.patch.dict(os.environ, {"LIVE_MAX_GUESTS": "2"}):
            snapshot = lp.stage_capacity(9)
            self.assertEqual(snapshot["slots_available"], 0)
            self.assertTrue(snapshot["stage_full"])

    def test_stage_is_full_tracks_the_limit(self):
        with mock.patch.dict(os.environ, {"LIVE_MAX_GUESTS": "2"}):
            self.assertFalse(lp.stage_is_full(1))
            self.assertTrue(lp.stage_is_full(2))
            self.assertTrue(lp.stage_is_full(3))


class FeatureFlagTests(unittest.TestCase):
    """The three states of the master switch, pinned separately.

    A single "defaults are on" assertion could not distinguish the two ways a
    flag reads as enabled: because the environment says so, or because nobody
    said anything. That distinction is the whole content of a rollout decision,
    so each state gets its own test and its own name.
    """

    def test_absent_means_off_so_multi_guest_does_not_ship_by_omission(self):
        """The default is the deployment posture for every environment that has
        not been told otherwise — which is all of them until someone acts.

        This is the assertion that changed. It previously read ``assertTrue``,
        and that made a deploy the enabling action for a feature whose audio has
        never been validated on a device. Nothing here is a claim that
        multi-guest is broken; it is a claim about who should have to opt in.
        """
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MULTI_GUEST_LIVE_ENABLED", None)
            os.environ.pop("LIVE_GUEST_REQUESTS_ENABLED", None)
            self.assertFalse(lp.multi_guest_enabled())
            self.assertFalse(
                lp.guest_requests_enabled(),
                "guest requests must follow the master switch when neither is set",
            )

    def test_explicit_true_turns_it_on_without_a_deploy(self):
        """The opposite direction, so the default cannot be mistaken for the
        feature being removed. Setting the variable is the whole activation
        procedure — no code change, no deploy."""
        with mock.patch.dict(os.environ, {"MULTI_GUEST_LIVE_ENABLED": "true"}):
            os.environ.pop("LIVE_GUEST_REQUESTS_ENABLED", None)
            self.assertTrue(lp.multi_guest_enabled())
            self.assertTrue(lp.guest_requests_enabled())

    def test_explicit_false_is_indistinguishable_from_absent(self):
        """An operator who sets it false during an incident and an environment
        that never set it must land in the same state. If these ever diverge,
        the kill switch means something different from the default and the
        rollback instructions in the audio declaration stop being true."""
        with mock.patch.dict(os.environ, {"MULTI_GUEST_LIVE_ENABLED": "false"}):
            explicit_off = (lp.multi_guest_enabled(), lp.guest_requests_enabled())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MULTI_GUEST_LIVE_ENABLED", None)
            absent = (lp.multi_guest_enabled(), lp.guest_requests_enabled())
        self.assertEqual(explicit_off, absent)
        self.assertEqual(explicit_off, (False, False))

    def test_single_host_live_is_not_conditional_on_the_flag(self):
        """The reason defaulting off is safe at all: capacity is reported by a
        separate function, and the host's own publisher slot is not a guest
        slot. If turning multi-guest off ever took the host off the air, this
        default would be a production outage rather than a conservative
        rollout."""
        with mock.patch.dict(os.environ, {"MULTI_GUEST_LIVE_ENABLED": "false", "LIVE_MAX_GUESTS": "4"}):
            self.assertFalse(lp.multi_guest_enabled())
            capacity = lp.stage_capacity(0)
            self.assertGreaterEqual(
                capacity["max_publishers"], 1, "the host must always have a publisher slot"
            )
            self.assertFalse(capacity["stage_full"])

    def test_master_switch_disables_guest_requests_too(self):
        with mock.patch.dict(os.environ, {"MULTI_GUEST_LIVE_ENABLED": "false", "LIVE_GUEST_REQUESTS_ENABLED": "true"}):
            self.assertFalse(lp.multi_guest_enabled())
            self.assertFalse(lp.guest_requests_enabled())

    def test_requests_can_be_disabled_independently_for_invite_only_panels(self):
        with mock.patch.dict(os.environ, {"MULTI_GUEST_LIVE_ENABLED": "true", "LIVE_GUEST_REQUESTS_ENABLED": "0"}):
            self.assertTrue(lp.multi_guest_enabled())
            self.assertFalse(lp.guest_requests_enabled())

    def test_truthy_spellings(self):
        for value in ("1", "true", "TRUE", "yes", "on", "enabled"):
            with mock.patch.dict(os.environ, {"MULTI_GUEST_LIVE_ENABLED": value}):
                self.assertTrue(lp.multi_guest_enabled(), value)
        for value in ("0", "false", "no", "off", "nonsense"):
            with mock.patch.dict(os.environ, {"MULTI_GUEST_LIVE_ENABLED": value}):
                self.assertFalse(lp.multi_guest_enabled(), value)

    def test_flag_snapshot_shape(self):
        snapshot = lp.live_feature_flags()
        self.assertEqual(
            sorted(snapshot),
            ["live_guest_requests_enabled", "live_max_guests", "live_max_publishers", "multi_guest_live_enabled"],
        )


class RtcIdentityTests(unittest.TestCase):
    def test_uid_is_the_user_id(self):
        self.assertEqual(lp.rtc_uid(4242), 4242)

    def test_out_of_range_ids_are_rejected(self):
        for value in (0, -1, lp.AGORA_UID_MAX + 1):
            with self.assertRaises(ValueError):
                lp.rtc_uid(value)

    def test_boundary_uid_is_accepted(self):
        self.assertEqual(lp.rtc_uid(lp.AGORA_UID_MAX), lp.AGORA_UID_MAX)

    def test_safe_uid_degrades_to_zero_for_payloads(self):
        # One unrepresentable id must not fail the whole roster response.
        self.assertEqual(lp.safe_rtc_uid(0), 0)
        self.assertEqual(lp.safe_rtc_uid("not-a-number"), 0)
        self.assertEqual(lp.safe_rtc_uid(None), 0)
        self.assertEqual(lp.safe_rtc_uid(19), 19)

    def test_uid_owner_is_resolved_by_lookup_not_by_position(self):
        roster = [
            {"rtc_uid": 11, "display_name": "Host", "role": lp.ROLE_HOST},
            {"rtc_uid": 22, "display_name": "Ada", "role": lp.ROLE_GUEST},
            {"rtc_uid": 33, "display_name": "Grace", "role": lp.ROLE_COHOST},
        ]
        # Arrival order is irrelevant: the third-listed participant is still
        # found by uid, which is the property that stops a component inferring
        # identity from whoever happened to join first.
        self.assertEqual(lp.resolve_uid_owner(33, roster)["display_name"], "Grace")
        self.assertEqual(lp.resolve_uid_owner("22", roster)["display_name"], "Ada")

    def test_unknown_uid_resolves_to_none(self):
        roster = [{"rtc_uid": 11}]
        self.assertIsNone(lp.resolve_uid_owner(99, roster))
        self.assertIsNone(lp.resolve_uid_owner(0, roster))
        self.assertIsNone(lp.resolve_uid_owner(None, roster))
        self.assertIsNone(lp.resolve_uid_owner("bad", roster))
        self.assertIsNone(lp.resolve_uid_owner(11, []))


class GuestLifecycleTests(unittest.TestCase):
    def test_pending_guests_hold_a_stage_slot(self):
        # Otherwise a host could over-invite and overflow the stage on arrival.
        for status in lp.GUEST_PENDING_STATUSES:
            self.assertTrue(lp.guest_is_on_stage(status), status)
            self.assertFalse(lp.guest_is_publishing(status), status)

    def test_live_guests_are_on_stage_and_publishing(self):
        for status in lp.GUEST_LIVE_STATUSES:
            self.assertTrue(lp.guest_is_on_stage(status), status)
            self.assertTrue(lp.guest_is_publishing(status), status)

    def test_terminal_guests_hold_nothing(self):
        for status in lp.GUEST_TERMINAL_STATUSES:
            self.assertFalse(lp.guest_is_on_stage(status), status)
            self.assertFalse(lp.guest_is_publishing(status), status)

    def test_active_and_terminal_sets_are_disjoint(self):
        self.assertFalse(set(lp.GUEST_ACTIVE_STATUSES) & set(lp.GUEST_TERMINAL_STATUSES))

    def test_unknown_status_is_not_on_stage(self):
        self.assertFalse(lp.guest_is_on_stage("banana"))
        self.assertFalse(lp.guest_is_on_stage(None))

    def test_sql_list_matches_the_active_statuses(self):
        rendered = lp.guest_status_sql_list()
        for status in lp.GUEST_ACTIVE_STATUSES:
            self.assertIn(f"'{status}'", rendered)
        self.assertEqual(rendered.count(","), len(lp.GUEST_ACTIVE_STATUSES) - 1)
        # The roster query interpolates this, so it must never carry anything
        # that could terminate the string literal.
        self.assertNotIn(";", rendered)
        self.assertNotIn("--", rendered)


class InviteIdentityTests(unittest.TestCase):
    """Stage 30 — an invite must be deduplicable, which means a stable id."""

    def test_invite_id_is_stable_for_the_same_invite(self):
        self.assertEqual(lp.build_invite_id(7, 91), lp.build_invite_id(7, 91))

    def test_invite_id_is_distinct_per_invite(self):
        self.assertNotEqual(lp.build_invite_id(7, 91), lp.build_invite_id(7, 92))
        self.assertNotEqual(lp.build_invite_id(7, 91), lp.build_invite_id(8, 91))

    def test_invite_id_round_trips(self):
        parsed = lp.parse_invite_id(lp.build_invite_id(7, 91))
        self.assertEqual(parsed, {"live_id": 7, "request_id": 91})

    def test_invite_id_requires_both_parts(self):
        self.assertEqual(lp.build_invite_id(0, 91), "")
        self.assertEqual(lp.build_invite_id(7, 0), "")

    def test_malformed_invite_ids_are_rejected(self):
        # These must fail at parse time rather than reaching a query.
        for bad in [
            "",
            None,
            "inv-7",
            "inv-7-91-extra",
            "xxx-7-91",
            "inv-abc-91",
            "inv-7-abc",
            "inv--91",
            "inv-7-0",
            "inv-7-91; DROP TABLE users",
            "../../inv-7-91",
        ]:
            self.assertIsNone(lp.parse_invite_id(bad), bad)

    def test_invite_id_parsing_is_case_insensitive(self):
        self.assertEqual(lp.parse_invite_id("INV-7-91"), {"live_id": 7, "request_id": 91})


class InviteLifecycleTests(unittest.TestCase):
    def test_invite_statuses_are_distinct(self):
        statuses = {
            lp.INVITE_STATUS_PENDING,
            lp.INVITE_STATUS_ACCEPTED,
            lp.INVITE_STATUS_DECLINED,
            lp.INVITE_STATUS_EXPIRED,
            lp.INVITE_STATUS_CANCELLED,
        }
        self.assertEqual(len(statuses), 5)

    def test_a_pending_invite_does_not_occupy_a_stage_slot(self):
        # An offer nobody has answered must not consume a seat, or a host who
        # invites six people locks the stage against all of them.
        self.assertFalse(lp.guest_is_on_stage(lp.INVITE_STATUS_PENDING))

    def test_an_accepted_invite_does_occupy_a_stage_slot(self):
        self.assertTrue(lp.guest_is_on_stage(lp.INVITE_STATUS_ACCEPTED))

    def test_declined_and_expired_invites_cannot_publish(self):
        for status in (lp.INVITE_STATUS_DECLINED, lp.INVITE_STATUS_EXPIRED):
            self.assertFalse(lp.guest_is_publishing(status), status)

    def test_origins_are_distinct(self):
        self.assertNotEqual(lp.ORIGIN_INVITE, lp.ORIGIN_REQUEST)

    def test_invite_ttl_is_short_enough_to_be_a_live_gesture(self):
        # A standing offer is the wrong model: a stale accept drops someone on
        # stage long after the host moved on.
        self.assertGreater(lp.LIVE_INVITE_TTL_SECONDS, 0)
        self.assertLessEqual(lp.LIVE_INVITE_TTL_SECONDS, 600)

    def test_request_cooldown_is_positive_but_not_punitive(self):
        self.assertGreater(lp.LIVE_REQUEST_COOLDOWN_SECONDS, 0)
        self.assertLessEqual(lp.LIVE_REQUEST_COOLDOWN_SECONDS, 300)


if __name__ == "__main__":
    unittest.main()
