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


class TestTheBanTableIsNoLongerOptional(unittest.TestCase):
    """The guarantee that lets ``is_banned`` treat an absent table as benign.

    ``services/live_moderation.py`` fails closed on a read error but returns
    "not banned" when the table does not exist, and logs that at ERROR. That
    asymmetry is only defensible because the table is created unconditionally
    on every boot: failing closed on an absent table would deny all live access
    in a partial harness while buying nothing in production, where the table
    has existed since it was declared.

    "Unconditionally" is the load-bearing word, and it is not something the
    runtime can check — by the time you could ask, you are already in a
    deployment that either has the table or does not. So it is pinned here. If
    someone moves this DDL behind a feature flag, a try/except, or an optional
    route pack, the fail-open branch silently becomes a way to turn live
    moderation off, and this test is what notices.
    """

    @classmethod
    def setUpClass(cls):
        cls.bot_src = BOT.read_text(encoding="utf-8", errors="replace")

    def test_the_table_is_declared_in_init_db(self):
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS pulse_live_moderation",
            self.bot_src,
            "the live ban table must keep being created at boot",
        )

    def test_the_declaration_is_not_conditional(self):
        """The DDL must sit at the statement level of its function, not inside
        an `if`, a `try`, or a loop. Anything deeper is a condition under which
        production could boot without the table — at which point the fail-open
        missing-table branch becomes a way to switch live moderation off.

        Indentation is the check because it is the thing a refactor changes:
        wrapping the statement in `if FLAG:` or `try:` moves it in by four.
        """
        lines = self.bot_src.splitlines()
        marker = next(
            i for i, line in enumerate(lines)
            if "CREATE TABLE IF NOT EXISTS pulse_live_moderation" in line
        )
        opener = next(
            i for i in range(marker, max(marker - 12, -1), -1)
            if "cur.execute(" in lines[i]
        )
        indent = len(lines[opener]) - len(lines[opener].lstrip())
        self.assertEqual(
            indent, 4,
            f"the pulse_live_moderation DDL is nested {indent} spaces deep "
            f"({lines[opener].strip()!r}); it must run unconditionally at the top "
            "level of its function, because services/live_moderation.py treats an "
            "absent table as benign on the strength of that guarantee",
        )

    def test_the_hot_path_lookup_is_indexed(self):
        """The ban lookup runs on every token mint, join, replay read, co-host
        request and invite decision. Unindexed it is a sequential scan on a
        table that only grows."""
        self.assertIn(
            "ON pulse_live_moderation(live_id, target_user_id, status)",
            self.bot_src,
        )


class TestTheSixEnforcementSitesStillRead(unittest.TestCase):
    """The authority exists to make these six true. They must stay wired.

    This table shipped with six readers and no writer, so every check ran
    against an empty table and said "not banned". Now that a writer exists, the
    opposite failure becomes possible and is just as silent: a refactor drops
    one of the six, that surface stops honouring bans, and nothing crashes. The
    host presses Ban, the UI says it worked, and the banned viewer keeps
    watching.
    """

    @classmethod
    def setUpClass(cls):
        cls.bot_src = BOT.read_text(encoding="utf-8", errors="replace")

    def test_all_six_call_sites_survive(self):
        calls = re.findall(r"[^f]pulse_live_user_is_blocked\(", self.bot_src)
        self.assertGreaterEqual(
            len(calls), 6,
            "one of the six live-ban enforcement sites has gone missing",
        )

    def test_each_named_surface_still_consults_the_ban(self):
        for fn_name in (
            "pulse_live_viewer_authorized",          # canonical audience gate
            "api_pulse_live_viewer_moderation",      # the writer's own guard rails
        ):
            self.assertIn(f"def {fn_name}(", self.bot_src, f"{fn_name} vanished")
        gate = _extract_function(self.bot_src, "pulse_live_viewer_authorized")
        self.assertIn("pulse_live_user_is_blocked(", gate)
        # A live ban and a social block are separate controls with separate
        # reasons. Collapsing them would make "get out of my stream" silently
        # mean "I never want to hear from this person again".
        self.assertIn("PULSE_LIVE_BANNED_REASON", gate)
        self.assertIn('PULSE_LIVE_BANNED_REASON = "live_blocked"', self.bot_src)
        self.assertIn("social_blocked", gate)

    def test_the_two_silent_boundaries_now_name_the_ban(self):
        """``/join`` and the token mint must report a ban as a ban.

        The three co-host boundaries always answered ``BLOCKED_BY_HOST``, so a
        ban there was legible. These two were not: join refused without logging
        anything, and the token mint logged ``reason=NOT_AUTHORIZED``, which is
        also what a followers-only Live says to a non-follower. The token is
        what buys access to the stream, so that was the single most important
        denial in the chain and it left no trace.

        Both events are pinned to the shared reason constant rather than to a
        repeated literal, so the gate cannot start reporting a ban under a name
        these two branches no longer recognise.
        """
        join = _extract_function(self.bot_src, "api_pulse_live_join")
        token = _extract_function(self.bot_src, "api_pulse_live_agora_token")
        for name, src in (("join", join), ("agora token", token)):
            self.assertIn(
                "if viewer_reason == PULSE_LIVE_BANNED_REASON:", src,
                f"the {name} route no longer distinguishes a ban from an audience miss",
            )
        self.assertIn("LIVE_JOIN_DENIED_BANNED", join)
        self.assertIn("LIVE_TOKEN_DENIED_BANNED", token)
        # The private note is trust & safety metadata. Neither event may carry
        # it, however convenient it would be for whoever is reading the logs.
        for src in (join, token):
            self.assertNotIn("reason=%s", src)

    def test_the_single_reader_delegates_to_the_authority_module(self):
        """One module owns reading and writing this table, so the six sites
        cannot drift from the writer's notion of what 'banned' means — and so
        they all inherit the fail-closed read."""
        reader = _extract_function(self.bot_src, "pulse_live_user_is_blocked")
        self.assertIn("live_moderation.is_banned(", reader)
        self.assertNotIn(
            "SELECT", reader,
            "the enforcement reader must not carry its own copy of the query",
        )


class TestTheWriterIsAuthorizedAndPrivate(unittest.TestCase):
    """What the mutation route must never stop doing."""

    @classmethod
    def setUpClass(cls):
        cls.bot_src = BOT.read_text(encoding="utf-8", errors="replace")
        cls.fn = _extract_function(cls.bot_src, "api_pulse_live_viewer_moderation")
        cls.service = (ROOT / "services" / "live_moderation.py").read_text(
            encoding="utf-8", errors="replace")
        # The module docstring argues at length about what a ban is *not*, and
        # naming `blocked_users` there is the point. Assertions below are about
        # what the module does, so they read the code only.
        cls.service_code = cls.service.split('"""', 2)[-1]

    def test_the_route_delegates_its_authorization(self):
        self.assertIn("live_moderation.authorize(", self.fn)
        self.assertIn("can_moderate=live_participants.can_moderate", self.fn)

    def test_the_actors_role_comes_from_this_live(self):
        """Resolving the role from the actor's participant row on the live
        being moderated is the entire defence against "I co-host Live A,
        therefore I moderate Live B"."""
        resolver = _extract_function(self.bot_src, "pulse_live_moderation_actor_role")
        self.assertIn("pulse_live_active_guest(", resolver)
        self.assertIn("live_participants.normalize_role(", resolver)

    def test_the_route_never_writes_a_social_block(self):
        for forbidden in ("blocked_users", "INSERT INTO users"):
            self.assertNotIn(
                forbidden, self.fn,
                "a live ban must not escalate into a platform-wide social block",
            )
        self.assertNotIn("blocked_users", self.service_code)

    def test_the_write_path_stays_visible_to_the_contract_sentinel(self):
        """The table name must be a literal in the SQL, not threaded through a
        generic helper as a variable. The sentinel's unit of analysis is an
        .execute whose first argument resolves to a readable string; a dynamic
        table name makes this write invisible to it."""
        self.assertIn("INSERT INTO pulse_live_moderation ", self.service)
        self.assertIn("UPDATE pulse_live_moderation SET ", self.service)

    def test_reading_fails_closed(self):
        reader = _extract_function(self.service, "is_banned")
        self.assertIn("LIVE_MODERATION_READ_FAILED", reader)
        self.assertRegex(
            reader,
            r"LIVE_MODERATION_READ_FAILED[\s\S]{0,400}return True",
            "a read failure must deny the protected action, not answer 'not banned'",
        )

    def test_the_private_note_is_bounded_and_never_shown_to_its_subject(self):
        self.assertIn("normalize_reason(", self.service)
        denial = _extract_function(self.service, "public_denial_message")
        for leak in ("reason", "moderator", "{"):
            self.assertNotIn(
                leak, denial.split('"""')[-1],
                "the viewer-facing denial must be a fixed string with no metadata",
            )

    def test_an_unknown_action_cannot_reach_the_table(self):
        """A closed vocabulary. An open-ended action string reaching a security
        table is how a vocabulary becomes an attack surface."""
        self.assertIn("live_moderation.MODERATION_ACTIONS", self.fn)
        self.assertIn('MODERATION_ACTIONS = (ACTION_BAN, ACTION_UNBAN)', self.service)


if __name__ == "__main__":
    unittest.main()
