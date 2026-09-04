"""Stages 36, 37, 40 and 41 — who may reach the stage, and what turns it off.

Two separate properties live here, and they fail in opposite directions.

**Authorization (36, 37).** Every door onto the stage is an authorization
decision, and there are four of them: a viewer asks, a host invites, an invite is
answered, a token is minted. A gap in any one of them is a way onto a broadcast
in front of an audience, so each is asserted individually rather than trusting
that "the invite route checks blocks" covers the token route too.

**The kill switch (40, 41).** ``MULTI_GUEST_LIVE_ENABLED`` used to close the two
entry doors and nothing else. Requests were refused and invites were refused —
but a guest already on stage kept renewing publisher tokens indefinitely, a
pending request was still approvable, and an invite issued a minute earlier was
still acceptable. The flag therefore looked like a kill switch in code review and
behaved like a doorbell in production: turning it off during an incident took
nobody off the air. The tests below assert the flag at all four doors.

The mirror-image property matters just as much and is asserted too: with the flag
off, a **single-host** Live must be untouched. The host branch of the token route
must not consult the flag at all, or switching multi-guest off would end every
broadcast on the platform — a far worse outcome than the one the switch exists to
prevent.

Static guards over ``bot.py`` source text, in the style of
``test_live_moderation_authority.py``: importing ``bot`` costs more than the rest
of the suite put together.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOT = ROOT / "bot.py"
PARTICIPANTS = ROOT / "services" / "live_participants.py"
API = ROOT / "mobile-native" / "src" / "api" / "live.ts"
HOST_SCREEN = ROOT / "mobile-native" / "src" / "screens" / "LiveHostSessionScreen.tsx"


def _extract_function(source: str, name: str) -> str:
    """Return the source of a top-level def by name (indentation-scoped)."""
    match = re.search(rf"^def {name}\(", source, re.M)
    if not match:
        raise AssertionError(f"function {name} not found in bot.py")
    start = match.start()
    rest = source[match.end():]
    nxt = re.search(r"^(?:def |@|# ={3,})", rest, re.M)
    end = match.end() + (nxt.start() if nxt else len(rest))
    return source[start:end]


class TestEveryDoorOntoTheStageIsGuarded(unittest.TestCase):
    """Stages 36 and 37 — forged, borrowed, expired, blocked and self-promoted."""

    @classmethod
    def setUpClass(cls):
        cls.src = BOT.read_text(encoding="utf-8", errors="replace")
        cls.token = _extract_function(cls.src, "api_pulse_live_agora_token")
        cls.invite = _extract_function(cls.src, "api_pulse_live_invite_create")
        cls.answer = _extract_function(cls.src, "api_pulse_live_invite_action")
        cls.request = _extract_function(cls.src, "api_pulse_live_join_request")

    def test_a_client_cannot_promote_itself_through_the_token_body(self):
        # The requested role is a request, not a grant. The role that goes into
        # the token is read off the stored guest row; a viewer asking for
        # "cohost" with no row gets refused, and a guest asking for "cohost"
        # with a guest row gets a guest token rather than moderation authority.
        self.assertIn('token_role = live_participants.normalize_role(guest.get("role")', self.token)
        self.assertIn("TOKEN_MISSING_PUBLISH_PERMISSION", self.token)
        self.assertIn("token_role = live_participants.ROLE_AUDIENCE", self.token)

    def test_only_the_host_may_mint_a_host_token(self):
        self.assertIn('requested_role in {"publisher", "host", "creator"} and not is_host', self.token)

    def test_a_removed_guest_cannot_mint_another_publisher_token(self):
        # pulse_live_active_guest filters on GUEST_ACTIVE_STATUSES, and 'removed'
        # is terminal — so the row stops existing for this purpose the moment a
        # host removes the guest, and the next renewal fails.
        self.assertIn("guest = pulse_live_active_guest(cur, live_id, user_id) if is_guest_request else {}", self.token)
        participants = PARTICIPANTS.read_text(encoding="utf-8", errors="replace")
        self.assertIn('GUEST_TERMINAL_STATUSES = ("left", "removed", "declined", "expired", "rejected")', participants)
        for terminal in ("left", "removed", "declined", "expired", "rejected"):
            self.assertNotIn(f'"{terminal}"', str(_active_statuses(participants)))

    def test_a_blocked_account_cannot_reach_the_stage_by_any_route(self):
        # Four doors, four checks. The token route goes through the shared
        # audience gate (which folds in both Live-level and social blocks); the
        # three guest routes check the Live-level block directly.
        self.assertIn("pulse_live_viewer_authorized(cur, live, user_id)", self.token)
        self.assertIn("pulse_live_user_is_blocked(cur, live_id, user[\"user_id\"])", self.request)
        self.assertIn("pulse_live_user_is_blocked(cur, live_id, target_user_id)", self.invite)
        self.assertIn("pulse_live_user_is_blocked(cur, live_id, target_user_id)", self.answer)

    def test_the_audience_gate_covers_both_directions_of_a_social_block(self):
        gate = _extract_function(self.src, "pulse_live_viewer_authorized")
        self.assertIn("blocker_user_id=? AND blocked_user_id=?) OR (blocker_user_id=? AND blocked_user_id=?", gate)
        self.assertIn('return False, "live_blocked"', gate)

    def test_an_invite_may_only_be_answered_by_its_own_target(self):
        # The check that stops someone walking onto the stage holding an invite
        # id that belongs to another account.
        self.assertIn("if actor_id != target_user_id:", self.answer)
        self.assertIn("This invite belongs to another account.", self.answer)

    def test_a_forged_invite_id_never_reaches_a_query(self):
        self.assertIn("parsed = live_participants.parse_invite_id(invite_id)", self.answer)
        self.assertIn('if not parsed or parsed["live_id"] != int(live_id):', self.answer)

    def test_an_expired_invite_is_refused_and_recorded_as_expired(self):
        self.assertIn("if pulse_live_invite_expired(invite, now_dt):", self.answer)
        self.assertIn("INVITE_EXPIRED", self.answer)

    def test_an_invite_cannot_hand_over_the_broadcast(self):
        # A host may seat a co-host or a guest. There is no path by which an
        # invite makes somebody else the host of a Live they did not start.
        self.assertIn(
            "if requested_role not in {live_participants.ROLE_COHOST, live_participants.ROLE_GUEST}:",
            self.invite,
        )

    def test_only_a_host_or_cohost_may_invite(self):
        self.assertIn("allowed, actor_role = pulse_live_can_invite(cur, live, user)", self.invite)
        can_invite = _extract_function(self.src, "pulse_live_can_invite")
        self.assertIn('role_permissions(role).get("invite_guests")', can_invite)


class TestTheFlagIsAKillSwitchNotADoorbell(unittest.TestCase):
    """Stage 40 — every door consults the flag, including the ones already open."""

    @classmethod
    def setUpClass(cls):
        cls.src = BOT.read_text(encoding="utf-8", errors="replace")
        cls.token = _extract_function(cls.src, "api_pulse_live_agora_token")
        cls.invite = _extract_function(cls.src, "api_pulse_live_invite_create")
        cls.answer = _extract_function(cls.src, "api_pulse_live_invite_action")
        cls.request = _extract_function(cls.src, "api_pulse_live_join_request")
        cls.approve = _extract_function(cls.src, "api_pulse_live_join_request_action")

    def test_the_token_route_refuses_a_guest_publisher_when_multi_guest_is_off(self):
        # The one that actually takes people off the air. Without it the flag
        # only stops new arrivals, and an existing guest publishes until the
        # Live ends.
        self.assertIn("if is_guest_request and not live_participants.multi_guest_enabled():", self.token)
        self.assertIn("MULTI_GUEST_DISABLED", self.token)

    def test_a_pending_request_cannot_be_approved_after_the_flag_is_turned_off(self):
        self.assertIn("if not live_participants.multi_guest_enabled():", self.approve)

    def test_denying_a_request_still_works_with_the_flag_off(self):
        # The deny branch has to be reachable before the flag check, or an
        # operator who closes multi-guest mid-broadcast leaves every queued
        # viewer waiting on a request that can never be answered either way.
        deny_at = self.approve.index('if action == "deny":')
        flag_at = self.approve.index("if not live_participants.multi_guest_enabled():")
        self.assertLess(deny_at, flag_at, "the deny branch must precede the multi-guest flag check")

    def test_a_pending_invite_cannot_be_accepted_after_the_flag_is_turned_off(self):
        self.assertIn("if not live_participants.multi_guest_enabled():", self.answer)

    def test_declining_or_cancelling_an_invite_still_works_with_the_flag_off(self):
        decline_at = self.answer.index('if action in {"decline", "cancel"}:')
        flag_at = self.answer.index("if not live_participants.multi_guest_enabled():")
        self.assertLess(decline_at, flag_at, "decline/cancel must precede the multi-guest flag check")

    def test_new_invites_and_new_requests_are_refused(self):
        self.assertIn("if not live_participants.multi_guest_enabled():", self.invite)
        self.assertIn("if not live_participants.guest_requests_enabled():", self.request)

    def test_the_request_flag_is_subordinate_to_the_master_flag(self):
        # Requests can be closed on their own — an invite-only panel is a real
        # configuration — but they can never be open while multi-guest is off.
        participants = PARTICIPANTS.read_text(encoding="utf-8", errors="replace")
        self.assertIn(
            'return _env_flag("LIVE_GUEST_REQUESTS_ENABLED", True) and multi_guest_enabled()',
            participants,
        )


class TestSingleHostLiveIsUnaffected(unittest.TestCase):
    """Stage 41 — the flag must not be able to end an ordinary broadcast."""

    @classmethod
    def setUpClass(cls):
        cls.src = BOT.read_text(encoding="utf-8", errors="replace")
        cls.token = _extract_function(cls.src, "api_pulse_live_agora_token")

    def test_the_flag_is_only_consulted_on_the_guest_branch(self):
        # Every multi_guest_enabled() call in the token route must be qualified
        # by is_guest_request. An unqualified one would refuse the host their
        # own publisher token the moment multi-guest was switched off, which
        # would take down every Live on the platform rather than just the
        # multi-guest ones.
        for line in self.token.splitlines():
            if "multi_guest_enabled()" in line:
                self.assertIn(
                    "is_guest_request",
                    line,
                    f"unqualified multi-guest flag check would affect single-host Live: {line.strip()}",
                )

    def test_a_viewer_token_does_not_depend_on_the_flag(self):
        # An audience token is minted from the audience role, which is the
        # fallback branch and is reached without consulting the flag at all.
        self.assertIn("token_role = live_participants.ROLE_AUDIENCE", self.token)

    def test_zero_guests_closes_the_stage_without_touching_the_host(self):
        participants = PARTICIPANTS.read_text(encoding="utf-8", errors="replace")
        max_guests = re.search(r"def max_guests\(\)[\s\S]*?return min\(value, LIVE_MAX_GUESTS_HARD_CEILING\)", participants)
        self.assertIsNotNone(max_guests, "max_guests not found")
        self.assertIn("if value < 0:", max_guests.group(0))
        # max_publishers is guests + 1: the host's own seat is not a guest seat
        # and does not disappear when LIVE_MAX_GUESTS is set to 0.
        self.assertIn("return max_guests() + 1", participants)

    def test_configuration_can_lower_the_ceiling_but_never_raise_it(self):
        participants = PARTICIPANTS.read_text(encoding="utf-8", errors="replace")
        self.assertIn("LIVE_MAX_GUESTS_HARD_CEILING = 12", participants)
        self.assertIn("return min(value, LIVE_MAX_GUESTS_HARD_CEILING)", participants)


class TestTheClientReadsCapacityRatherThanInventingIt(unittest.TestCase):
    """Stages 5, 40 and 46 — a truthful STAGE FULL, sourced from the server."""

    @classmethod
    def setUpClass(cls):
        cls.bot_src = BOT.read_text(encoding="utf-8", errors="replace")
        cls.api = API.read_text(encoding="utf-8", errors="replace")
        cls.screen = HOST_SCREEN.read_text(encoding="utf-8", errors="replace")

    def test_the_backstage_endpoint_sends_the_stage_snapshot(self):
        route = _extract_function(self.bot_src, "api_pulse_live_join_requests")
        self.assertIn("stage = pulse_live_stage_snapshot(cur, live_id, guests=guests)", route)
        self.assertIn('"stage": stage', route)

    def test_the_snapshot_carries_both_the_ceiling_and_the_flags(self):
        snapshot = _extract_function(self.bot_src, "pulse_live_stage_snapshot")
        self.assertIn("live_participants.stage_capacity(len(roster))", snapshot)
        self.assertIn("snapshot.update(live_participants.live_feature_flags())", snapshot)

    def test_the_client_parses_the_snapshot_instead_of_hardcoding_a_limit(self):
        self.assertIn("export function normalizeStageCapacity(", self.api)
        self.assertIn("stage: normalizeStageCapacity(data.stage)", self.api)
        # No native constant may stand in for the server's ceiling.
        self.assertNotRegex(self.screen, r"MAX_GUESTS\s*=\s*\d+")

    def test_a_missing_snapshot_does_not_present_as_a_locked_stage(self):
        # An older server, or a failed poll, means "unknown" — which must not
        # render as a full stage and lock the host out of their own backstage.
        self.assertIn("stageFull: false", self.api)
        self.assertIn("if (management.stage) setStage(management.stage);", self.screen)

    def test_accept_all_is_capped_at_the_seats_that_exist(self):
        self.assertIn("const seats = stage ? Math.max(0, stage.slotsAvailable) : requests.length;", self.screen)
        self.assertIn("const pending = requests.slice(0, seats);", self.screen)

    def test_a_full_stage_disables_accept_but_not_deny(self):
        accept = re.search(r"Accept \$\{request\.displayName\}[\s\S]{0,900}?</Pressable>", self.screen)
        self.assertIsNotNone(accept, "accept control not found")
        self.assertIn("stage?.stageFull", accept.group(0))
        deny = re.search(r"Deny \$\{request\.displayName\}[\s\S]{0,900}?</Pressable>", self.screen)
        self.assertIsNotNone(deny, "deny control not found")
        self.assertNotIn("stage?.stageFull", deny.group(0))


def _active_statuses(participants_src: str):
    pending = re.search(r"GUEST_PENDING_STATUSES = \(([^)]*)\)", participants_src)
    live = re.search(r"GUEST_LIVE_STATUSES = \(([^)]*)\)", participants_src)
    assert pending and live, "guest status tuples not found"
    return pending.group(1) + live.group(1)


if __name__ == "__main__":
    unittest.main()
