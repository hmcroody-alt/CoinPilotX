"""Stages 20 and 22 — who may end a Live, and who may only run the room.

Static architecture guards over ``bot.py``, in the same style as
``test_live_end_nonblocking.py``: the source text is read rather than imported,
because importing ``bot`` costs more than the whole rest of the suite.

The two properties asserted here are the ones whose failure is silent. Nothing
crashes when a co-host gains the ability to end somebody else's broadcast, and
nothing crashes when a guest coming on stage is stored as a moderator — the
build is green, the tests pass, and the first anyone hears about it is a host
whose Live was ended by a panellist.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOT = ROOT / "bot.py"

# The protection runner executes each suite as a script (`python3 <file>`), not
# through pytest, so there is no rootdir conftest to put the repository on the
# path. Without this the four tests that import `services.live_participants`
# error under CI while passing locally under pytest — the worst possible split,
# because the local run is the one people trust.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _extract_function(source: str, name: str) -> str:
    """Return the source of a top-level def by name (indentation-scoped)."""
    match = re.search(rf"^def {name}\(", source, re.M)
    if not match:
        raise AssertionError(f"function {name} not found")
    start = match.start()
    rest = source[match.end():]
    nxt = re.search(r"^(?:def |@|# ={3,})", rest, re.M)
    end = match.end() + (nxt.start() if nxt else len(rest))
    return source[start:end]


class TestGuestActionAuthority(unittest.TestCase):
    """The moderation endpoint must defer to the role table, not re-decide."""

    @classmethod
    def setUpClass(cls):
        cls.bot_src = BOT.read_text(encoding="utf-8", errors="replace")
        cls.fn = _extract_function(cls.bot_src, "api_pulse_live_guest_action")

    def test_moderation_is_gated_on_the_canonical_role_table(self):
        # If this check were inlined here instead of delegated, the client's
        # menu and the server's answer would drift, and the product would offer
        # a co-host a Remove button and then refuse the request.
        self.assertIn("live_participants.can_moderate(", self.fn)

    def test_the_actors_role_is_resolved_from_their_own_participant_row(self):
        self.assertIn("pulse_live_active_guest(", self.fn)
        self.assertIn("live_participants.normalize_role(", self.fn)

    def test_a_co_host_cannot_mute_or_remove_the_host(self):
        # A co-host who could silence the host could take the broadcast.
        self.assertRegex(
            self.fn,
            r"not is_host[\s\S]{0,200}live\.get\(\"user_id\"\)",
            "the endpoint must refuse a non-host acting on the host's own seat",
        )

    def test_leave_remains_self_only(self):
        # Stage 20: a guest walking off stage and a host removing them are
        # different events, and a moderation log exists to tell them apart.
        self.assertIn("is_self_guest", self.fn)
        self.assertIn("Only the guest can leave their guest slot.", self.fn)

    def test_leave_and_remove_write_different_statuses(self):
        self.assertIn('"left" if action == "leave" else "removed"', self.fn)

    def test_the_endpoint_never_ends_the_broadcast(self):
        # Stage 20's central rule: a guest leaving must never end the Live.
        for forbidden in ("status='ended'", "pulse_live_publish_replay_reel(", "livestream_ended"):
            self.assertNotIn(
                forbidden,
                self.fn,
                "a guest action must never terminate the broadcast",
            )


class TestEndLiveRemainsOwnerOnly(unittest.TestCase):
    """Stage 22: ending is the one authority a co-host never receives."""

    @classmethod
    def setUpClass(cls):
        cls.bot_src = BOT.read_text(encoding="utf-8", errors="replace")
        cls.fn = _extract_function(cls.bot_src, "api_pulse_live_end")

    def test_only_the_session_owner_or_an_admin_may_end(self):
        self.assertRegex(
            self.fn,
            r"live\.get\(\"user_id\"\)[\s\S]{0,160}admin_current_user\(\)",
        )
        self.assertIn("Only the host or an admin can end this stream.", self.fn)

    def test_ending_does_not_consult_the_guest_roster(self):
        # If ending ever asked "is this actor on stage", a co-host would be one
        # permission-table edit away from ending someone else's broadcast.
        self.assertNotIn("pulse_live_active_guest(", self.fn)
        self.assertNotIn("can_moderate", self.fn)


class TestStagePromotionIsDeliberate(unittest.TestCase):
    """Coming on stage makes you a guest. Co-host is a promotion."""

    @classmethod
    def setUpClass(cls):
        cls.bot_src = BOT.read_text(encoding="utf-8", errors="replace")

    def test_the_single_writer_of_guest_rows_defaults_to_guest(self):
        # The previous default stored every approved viewer as a co-host, which
        # would have handed moderation to the whole stage the moment the role
        # table became load-bearing.
        window = self.bot_src[self.bot_src.index("guest_role = live_participants.normalize_role(role or") :][:600]
        self.assertIn("live_participants.ROLE_GUEST)", window)
        self.assertNotIn("role or live_participants.ROLE_COHOST", window)

    def test_approval_can_never_mint_a_host(self):
        window = self.bot_src[self.bot_src.index("guest_role = live_participants.normalize_role(role or") :][:600]
        self.assertIn("guest_role = live_participants.ROLE_GUEST", window)
        self.assertNotIn("ROLE_HOST", window)


class TestClientAndServerAgreeOnPermissions(unittest.TestCase):
    """The mobile table must never grant more than the server enforces.

    ``mobile-native/src/live/liveSessionLifecycle.ts`` carries a copy of the
    role/permission matrix so the app can avoid drawing buttons the server will
    refuse. A copy is a liability: the day the two disagree, the app either
    hides a capability the user has or offers one they do not. This test is the
    thing that makes the copy safe.
    """

    LIFECYCLE = ROOT / "mobile-native" / "src" / "live" / "liveSessionLifecycle.ts"

    #: Client permission name -> server permission flag.
    MAPPING = {
        "publish": "publish",
        "moderateGuests": "remove_guests",
        "inviteGuests": "invite_guests",
        "approveRequests": "approve_requests",
        "endBroadcast": "end_live",
    }

    @classmethod
    def setUpClass(cls):
        source = cls.LIFECYCLE.read_text(encoding="utf-8", errors="replace")
        block = re.search(r"ROLE_PERMISSIONS[^=]*=\s*\{(.*?)\n\};", source, re.S)
        if not block:
            raise AssertionError("ROLE_PERMISSIONS table not found in liveSessionLifecycle.ts")
        cls.client = {}
        for role, body in re.findall(r"(\w+):\s*\[(.*?)\]", block.group(1), re.S):
            cls.client[role] = set(re.findall(r'"(\w+)"', body))

    def test_every_role_is_present_on_both_sides(self):
        from services import live_participants as lp

        self.assertEqual(set(self.client), set(lp.LIVE_ROLES))

    def test_the_client_never_grants_more_than_the_server(self):
        from services import live_participants as lp

        for role, granted in self.client.items():
            server = lp.role_permissions(role)
            for client_name, server_flag in self.MAPPING.items():
                if client_name in granted:
                    self.assertTrue(
                        server[server_flag],
                        f"client grants {role}.{client_name} but the server refuses {server_flag}",
                    )

    def test_the_client_does_not_silently_withhold_a_capability(self):
        from services import live_participants as lp

        for role, granted in self.client.items():
            server = lp.role_permissions(role)
            for client_name, server_flag in self.MAPPING.items():
                if server[server_flag]:
                    self.assertIn(
                        client_name,
                        granted,
                        f"the server grants {role}.{server_flag} but the client hides it",
                    )

    def test_the_co_host_is_not_host_equivalent_on_either_side(self):
        from services import live_participants as lp

        self.assertNotEqual(self.client["cohost"], self.client["host"])
        self.assertFalse(lp.can_end_live(lp.ROLE_COHOST))
        self.assertTrue(lp.can_moderate(lp.ROLE_COHOST))


if __name__ == "__main__":
    unittest.main()
