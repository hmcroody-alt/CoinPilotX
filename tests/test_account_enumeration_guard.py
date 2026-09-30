"""Three account-identity holes that a request-per-path limiter could not close.

All three were live in production when this was written, and all three sat a few
thousand lines below a working rate limiter in the same file -- which is the
interesting part. `basic_abuse_guard` was not missing. It was the wrong shape,
and the wrongness ran in opposite directions at the same time:

1. `/api/mobile/auth/confirmation-status` answered "does this address have a
   confirmed PulseSoc account" to anyone, unauthenticated, at any rate. It could
   not simply be added to `ABUSE_GUARD_PROTECTED`: the only shipped client polls
   it every 4 seconds (`VerifyEmailStep.tsx: POLL_INTERVAL_MS = 4000`), so any
   limit low enough to stop an enumerator refuses every real signup. It also
   answers GET, and `basic_abuse_guard` is POST/PUT only, so a prober never had
   to send a method either limiter watched. It leaked `exists` on top of
   `confirmed` -- a strictly larger answer, covering unconfirmed accounts too --
   through a field no client read.

2. The resend endpoints returned three distinguishable messages on three
   different statuses, with a `trace_id` present in only one of them. Four
   independent signals, each answering the same question as (1).

3. Nothing bounded how much confirmation mail one address could be made to
   receive. Bounding that per client is the wrong key, because the attacker is
   not the victim: rotating source addresses defeats a per-client cap entirely
   while the mail still piles up in one mailbox.

Open Commerce §16 is why the first two matter beyond hygiene: a guest checking
out with an address that already has an account must complete the purchase
without the flow revealing that the account exists. An oracle anywhere else in
the product makes that promise unkeepable no matter how carefully checkout is
written.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="enum_guard_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import auth_subject_guard  # noqa: E402
from services import cache_engine, db as db_service, pulse_security_core  # noqa: E402

STATUS = "/api/mobile/auth/confirmation-status"
PULSE_STATUS = "/api/pulse/mobile/auth/confirmation-status"
RESEND = "/api/mobile/auth/resend-confirmation"


def _reset_every_limiter():
    """Four independent stores can refuse a request on these paths.

    Leaving any one of them dirty makes a test pass or fail for a reason that is
    not the one it is named after -- and the failure is silent, because a 429 is
    a 429 whichever store produced it. Each assertion below that expects a
    refusal also pins *which* store refused.
    """
    auth_subject_guard.reset()
    bot.RATE_LIMIT_BUCKETS.clear()
    pulse_security_core._RATE_BUCKETS.clear()
    cache_engine._MEMORY.clear()


# =========================================================================
# 1. The primitive: repetition is free, variety is not
# =========================================================================

class DistinctSubjectLimitCase(unittest.TestCase):
    def setUp(self):
        auth_subject_guard.reset()

    def test_one_subject_may_be_named_forever(self):
        """The property the 4-second poll depends on. 500 is well past any
        request count that would stop an enumerator, which is the point."""
        for attempt in range(500):
            refused = auth_subject_guard.distinct_subject_refused(
                "ip-a", "same@example.com", 8, 900)
            self.assertIsNone(refused, f"honest poll refused at attempt {attempt}")

    def test_the_ninth_different_subject_is_refused(self):
        verdicts = [
            auth_subject_guard.distinct_subject_refused("ip-a", f"u{n}@example.com", 8, 900)
            for n in range(12)
        ]
        self.assertEqual([v is None for v in verdicts], [True] * 8 + [False] * 4, verdicts)
        self.assertEqual(verdicts[8].count, 8)
        self.assertEqual(verdicts[8].limit, 8)

    def test_repetition_after_the_cap_still_works(self):
        """An enumerator being refused must not take the real user down with it.
        A shared NAT means one address can carry both, and the honest client's
        subject was admitted before the cap was reached."""
        for n in range(8):
            auth_subject_guard.distinct_subject_refused("ip-a", f"u{n}@example.com", 8, 900)
        self.assertIsNotNone(
            auth_subject_guard.distinct_subject_refused("ip-a", "new@example.com", 8, 900))
        self.assertIsNone(
            auth_subject_guard.distinct_subject_refused("ip-a", "u3@example.com", 8, 900))

    def test_a_refused_subject_does_not_extend_the_window(self):
        """If the refused subject were recorded, a prober that kept retrying
        would keep its own oldest entry alive and the window would never drain --
        a permanent self-inflicted block on whatever address it shares."""
        clock = 1_000.0
        for n in range(8):
            auth_subject_guard.distinct_subject_refused(
                "ip-a", f"u{n}@example.com", 8, 900, now=clock)
        for step in range(1, 60):
            auth_subject_guard.distinct_subject_refused(
                "ip-a", f"probe{step}@example.com", 8, 900, now=clock + step)
        # The window is measured from the 8 admitted subjects at t=1000, so it
        # drains at t=1900 regardless of how much refused traffic arrived after.
        self.assertIsNone(auth_subject_guard.distinct_subject_refused(
            "ip-a", "later@example.com", 8, 900, now=clock + 901))

    def test_two_actors_do_not_share_a_budget(self):
        for n in range(8):
            auth_subject_guard.distinct_subject_refused("ip-a", f"u{n}@example.com", 8, 900)
        self.assertIsNone(
            auth_subject_guard.distinct_subject_refused("ip-b", "fresh@example.com", 8, 900))

    def test_an_absent_subject_is_not_a_shared_bucket(self):
        """A route that failed to parse a subject must not file every such
        request under one key: eight junk requests would then refuse the ninth
        honest one, and the junk is free to send.

        Asserted on the *consequence* -- the honest ninth still gets through --
        not just on the junk returning None. A version that counted the junk
        under one shared key would also return None fifty times."""
        for _ in range(50):
            self.assertIsNone(auth_subject_guard.distinct_subject_refused("ip-a", "", 8, 900))
        for whitespace in ("  ", "\t", "\n"):
            self.assertIsNone(
                auth_subject_guard.distinct_subject_refused("ip-a", whitespace, 8, 900))
        self.assertEqual(auth_subject_guard._SUBJECTS_BY_ACTOR.get("ip-a", {}), {})
        self.assertIsNone(
            auth_subject_guard.distinct_subject_refused("ip-a", "real@example.com", 8, 900))

    def test_an_absent_actor_is_not_a_shared_bucket_either(self):
        """`client_ip_hash()` returns "" when it cannot resolve a source -- which
        is not hypothetical, it is what happens inside a bare test_request_context
        and what a misconfigured proxy produces. Filing those under one empty key
        means eight of them refuse the ninth real request."""
        for _ in range(50):
            self.assertIsNone(
                auth_subject_guard.distinct_subject_refused("", f"u{_}@example.com", 8, 900))
        self.assertEqual(auth_subject_guard._SUBJECTS_BY_ACTOR, {})

    def test_the_table_holds_no_readable_address(self):
        import hashlib
        email = "victim@example.com"
        auth_subject_guard.distinct_subject_refused("ip-a", email, 8, 900)
        keys = list(auth_subject_guard._SUBJECTS_BY_ACTOR["ip-a"])
        self.assertNotIn(email, keys)
        self.assertNotIn("victim", "".join(keys))
        # Salted, not a bare digest -- an unsalted hash of an email address is
        # reversible by anyone with a wordlist, which is the whole reason
        # ANALYTICS_SALT being unset makes client_ip_hash reversible today.
        self.assertNotIn(hashlib.sha256(email.encode()).hexdigest(), keys)

    def test_case_and_whitespace_are_the_same_subject(self):
        """Otherwise the cap is decorative: eight spellings of one address are
        eight budget slots, and `Victim@Example.com ` is a different probe."""
        auth_subject_guard.distinct_subject_refused("ip-a", "a@example.com", 1, 900)
        self.assertIsNone(
            auth_subject_guard.distinct_subject_refused("ip-a", "  A@Example.COM ", 1, 900))


# =========================================================================
# 2. The primitive: a per-subject cap that ignores who is asking
# =========================================================================

class SubjectEventLimitCase(unittest.TestCase):
    def setUp(self):
        auth_subject_guard.reset()

    def test_the_sixth_send_to_one_address_is_refused(self):
        verdicts = [auth_subject_guard.subject_event_refused("v@example.com", 5, 900)
                    for _ in range(7)]
        self.assertEqual([v is None for v in verdicts], [True] * 5 + [False] * 2, verdicts)

    def test_rotating_the_caller_does_not_reset_it(self):
        """The property that makes this control worth having. A per-client cap
        looks identical in a single-client test and does nothing in production,
        where filling one inbox from 500 source addresses is cheap."""
        for _ in range(5):
            auth_subject_guard.subject_event_refused("v@example.com", 5, 900)
        # No actor argument exists to rotate -- that is the design. Assert the
        # signature cannot grow one by accident.
        import inspect
        params = list(inspect.signature(auth_subject_guard.subject_event_refused).parameters)
        self.assertEqual(params[0], "subject")
        self.assertNotIn("actor", params)
        self.assertIsNotNone(auth_subject_guard.subject_event_refused("v@example.com", 5, 900))

    def test_a_different_address_is_unaffected(self):
        for _ in range(5):
            auth_subject_guard.subject_event_refused("v@example.com", 5, 900)
        self.assertIsNone(auth_subject_guard.subject_event_refused("other@example.com", 5, 900))

    def test_the_window_drains(self):
        for n in range(5):
            auth_subject_guard.subject_event_refused("v@example.com", 5, 900, now=1_000.0 + n)
        self.assertIsNotNone(
            auth_subject_guard.subject_event_refused("v@example.com", 5, 900, now=1_500.0))
        self.assertIsNone(
            auth_subject_guard.subject_event_refused("v@example.com", 5, 900, now=1_902.0))

    def test_memory_comes_back(self):
        """Both tables are keyed by attacker-supplied values, so a table that
        only ever grew would be a memory exhaustion vector handed to the same
        traffic the guard is refusing."""
        for n in range(400):
            auth_subject_guard.subject_event_refused(f"u{n}@example.com", 5, 900, now=1_000.0)
        self.assertEqual(len(auth_subject_guard._EVENTS_BY_SUBJECT), 400)
        auth_subject_guard.subject_event_refused("late@example.com", 5, 900, now=2_500.0)
        self.assertLessEqual(len(auth_subject_guard._EVENTS_BY_SUBJECT), 2,
                             "stale subjects were never reclaimed")


# =========================================================================
# 3. The route: the honest poll survives, the enumerator does not
# =========================================================================

class ConfirmationStatusRouteCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
        bot.INIT_DB_COMPLETED = False
        bot.init_db()
        bot.app.config["TESTING"] = True
        self.client = bot.app.test_client()
        _reset_every_limiter()

    def test_the_route_is_in_the_guard_table_under_both_url_families(self):
        # Asserted on the mapping, not on the source text: a path that appeared
        # only in a comment would satisfy a grep.
        self.assertIn(STATUS, bot.ENUMERATION_GUARD_PROTECTED)
        self.assertIn(PULSE_STATUS, bot.ENUMERATION_GUARD_PROTECTED)
        self.assertIn(RESEND, bot.ENUMERATION_GUARD_PROTECTED)
        self.assertIn("/resend-confirmation", bot.ENUMERATION_GUARD_PROTECTED)

    def test_one_hundred_polls_of_one_address_all_succeed(self):
        email = f"poll-{secrets.token_hex(5)}@example.com"
        statuses = {self.client.post(STATUS, json={"email": email}).status_code
                    for _ in range(100)}
        self.assertEqual(statuses, {200}, statuses)

    def test_a_request_count_limit_could_not_have_been_used(self):
        """Anti-vacuity for the test above, and the reason this guard exists at
        all. 100 polls is under seven minutes of one real signup screen. Every
        limit in the deployed request-per-path table is an order of magnitude
        below it, so "just add the path to ABUSE_GUARD_PROTECTED" was never an
        option -- it would have refused every legitimate signup."""
        polls_per_window = 300 // 4  # POLL_INTERVAL_MS = 4000
        for path, (limit, window) in bot.ABUSE_GUARD_PROTECTED.items():
            if window != 300:
                continue
            self.assertLess(limit, polls_per_window,
                            f"{path} would have to allow {polls_per_window} to host this poll")

    def test_the_ninth_distinct_address_is_refused(self):
        seen = [self.client.post(STATUS, json={"email": f"probe-{n}@example.com"}).status_code
                for n in range(12)]
        self.assertEqual(seen, [200] * 8 + [429] * 4, seen)

    def test_the_get_form_is_guarded_too(self):
        """The method `basic_abuse_guard` never watches. This endpoint answers
        GET, so a prober that sent GET met no limiter at all: not this one, not
        the POST/PUT guard, and not pulse_security_core, whose rate_rule_for
        returns None for GET."""
        self.assertIsNone(pulse_security_core.rate_rule_for(STATUS, "GET"),
                          "premise: GET is unrated by pulse_security_core")
        seen = [self.client.get(f"{STATUS}?email=g-{n}@example.com").status_code
                for n in range(12)]
        self.assertEqual(seen, [200] * 8 + [429] * 4, seen)

    def test_the_new_guard_is_what_refused_it(self):
        """A 429 from one of the three pre-existing limiters would make every
        assertion above pass without this guard existing."""
        for n in range(12):
            self.client.post(STATUS, json={"email": f"who-{n}@example.com"})
        local = [stamps for key, stamps in bot.RATE_LIMIT_BUCKETS.items() if STATUS in key]
        self.assertEqual(local, [], "basic_abuse_guard counted this path")
        for key, stamps in pulse_security_core._RATE_BUCKETS.items():
            if STATUS in key:
                self.assertLess(len(stamps), 180, f"{key} reached its own limit")
        self.assertTrue(auth_subject_guard._SUBJECTS_BY_ACTOR,
                        "the distinct-subject table was never written")

    def test_the_two_url_families_share_one_budget(self):
        """`/api/mobile/...` and `/api/pulse/mobile/...` are one handler mounted
        twice, so they are one scope. Alternating the prefix must not buy a second
        budget -- that would be a bypass spelled into the URL space.

        The probe uses an address the first family never named. An earlier version
        of this test reused `fam-0`, which is admitted under *either* design -- a
        subject already inside the window is always allowed -- so it passed
        whether the scopes were shared or not, and it asserted the wrong thing
        besides. The mutation matrix caught the vacuity
        (`both-url-families-share-one-scope` survived), and fixing the test turned
        up that the shipped key was the actor alone, which shared far too much.
        """
        for n in range(12):
            self.client.post(STATUS, json={"email": f"fam-{n}@example.com"})
        self.assertEqual(
            self.client.post(PULSE_STATUS,
                             json={"email": "never-asked@example.com"}).status_code, 429,
            "alternating the /api/pulse prefix bought a second enumeration budget")

    def test_two_different_endpoints_do_not_share_a_budget(self):
        """The mirror of the test above, and the reason `scope` is a column rather
        than the path.

        These two entries carry deliberately different limits (8 and 5) because
        they carry different risks: one answers a question, the other sends mail.
        Keyed by actor alone -- which is what shipped first -- a client that had
        legitimately named 8 addresses at `/confirmation-status` would arrive at
        `/resend-confirmation` already past its limit of 5 and be refused a resend
        it had not asked for even once.

        The resend probe names an address the status polling never did. Reusing
        one of the eight would be admitted under *either* keying -- a subject
        already inside the window is always allowed -- which is the same vacuity
        the matrix caught in the alias test above. It caught it here too:
        `every-endpoint-shares-one-scope` survived the first version of this test.
        """
        for n in range(8):
            self.assertEqual(
                self.client.post(STATUS, json={"email": f"typo-{n}@example.com"}).status_code,
                200)
        self.assertEqual(
            self.client.post(RESEND, json={"email": "finally-right@example.com"}).status_code,
            200,
            "status polling spent the resend endpoint's budget")

    def test_the_refusal_names_no_address(self):
        for n in range(9):
            response = self.client.post(STATUS, json={"email": f"secret-{n}@example.com"})
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("secret-", body)
        self.assertIn("Retry-After", response.headers)

    def test_the_refusal_does_not_touch_the_database_every_time(self):
        """The pool is 8+8 with a 3s checkout timeout. A security_events write
        per refused request would make this guard an amplifier for exactly the
        traffic it refuses: reaching the cap would make every further probe cost
        a connection instead of costing nothing."""
        calls = []
        with patch.object(bot.security_monitor, "record",
                          side_effect=lambda *a, **k: calls.append(a[0])):
            for n in range(40):
                self.client.post(STATUS, json={"email": f"amp-{n}@example.com"})
        alarms = [name for name in calls if name == "account_enumeration_guard_refused"]
        self.assertEqual(len(alarms), 1, f"{len(alarms)} rows for one actor in one window")

    def test_exists_and_email_verified_are_gone(self):
        body = self.client.post(STATUS, json={"email": "nobody@example.com"}).get_json()
        self.assertNotIn("exists", body)
        self.assertNotIn("email_verified", body)

    def test_confirmed_still_reaches_the_client(self):
        """Do not close a leak by breaking the feature. The signup screen exists
        to wait for this field; the residual signal is bounded by the guard
        rather than removed."""
        email = f"conf-{secrets.token_hex(5)}@example.com"
        self.assertFalse(self.client.post(STATUS, json={"email": email}).get_json()["confirmed"])
        _seed_user(email, verified=1)
        _reset_every_limiter()
        self.assertTrue(self.client.post(STATUS, json={"email": email}).get_json()["confirmed"])


# =========================================================================
# 4. The resend path: one answer for four different truths
# =========================================================================

def _seed_user(email, verified=0):
    now = bot.datetime.now().isoformat()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, display_name, full_name, email, password_hash, "
        "email_verified, account_status, login_enabled, access_enabled, signup_time, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'active', 1, 1, ?, ?, ?)",
        (f"eg_{secrets.token_hex(5)}", "Enum", "Enum", email,
         bot.generate_password_hash("Enum!123456789"), int(verified), now, now, now),
    )
    conn.commit()
    cur.execute("SELECT user_id FROM users WHERE email=?", (email,))
    user_id = cur.fetchone()[0]
    conn.close()
    return user_id


class ResendConfirmationCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
        bot.INIT_DB_COMPLETED = False
        bot.init_db()
        bot.app.config["TESTING"] = True
        self.client = bot.app.test_client()
        _reset_every_limiter()

    def _resend(self, email):
        with patch.object(bot, "send_email_verification", return_value=True):
            response = self.client.post(RESEND, json={"email": email})
        return response.status_code, response.get_json()

    def test_three_account_states_are_indistinguishable(self):
        """The finding, stated as an assertion. Unknown address, verified
        account and unverified account produced three different messages on
        three different statuses -- any one of which answers "does this person
        have a PulseSoc account"."""
        unknown = f"ghost-{secrets.token_hex(5)}@example.com"
        verified = f"verified-{secrets.token_hex(5)}@example.com"
        unverified = f"pending-{secrets.token_hex(5)}@example.com"
        _seed_user(verified, verified=1)
        _seed_user(unverified, verified=0)

        answers = []
        for email in (unknown, verified, unverified):
            _reset_every_limiter()
            answers.append(self._resend(email))
        self.assertEqual(answers[0], answers[1])
        self.assertEqual(answers[1], answers[2])
        self.assertEqual(answers[0][0], 200)

    def test_a_delivery_failure_is_indistinguishable_too(self):
        """Only an existing unverified account can produce a send failure, so a
        502 was itself an oracle. The failure is still recorded server-side --
        `send_account_confirmation_email` writes `verification_email_failed` --
        which is who needs to know."""
        unverified = f"blocked-{secrets.token_hex(5)}@example.com"
        _seed_user(unverified, verified=0)
        with patch.object(bot, "send_email_verification", return_value=False):
            failed = self.client.post(RESEND, json={"email": unverified})
        _reset_every_limiter()
        unknown = self.client.post(RESEND, json={"email": "nobody-here@example.com"})
        self.assertEqual(failed.status_code, unknown.status_code)
        self.assertEqual(failed.get_json(), unknown.get_json())

    def test_the_trace_id_does_not_leak_by_its_presence(self):
        """It used to appear only when a send was attempted, which answered the
        same question as the message it accompanied -- through a field no client
        reads."""
        unverified = f"trace-{secrets.token_hex(5)}@example.com"
        _seed_user(unverified, verified=0)
        _, sent = self._resend(unverified)
        _reset_every_limiter()
        _, missing = self._resend("no-such-account@example.com")
        self.assertEqual(sent["trace_id"], missing["trace_id"])
        self.assertEqual(sent["trace_id"], "")

    def test_a_malformed_address_may_still_be_refused(self):
        """Deliberately not collapsed. 400 here is a statement about the syntax
        of the input, not about the contents of the user table --
        `api_mobile_auth_recover` draws the line in the same place and says so."""
        response = self.client.post(RESEND, json={"email": "not-an-email"})
        self.assertEqual(response.status_code, 400)

    def test_the_email_bomb_is_capped(self):
        sent_to = []
        unverified = f"bomb-{secrets.token_hex(5)}@example.com"
        _seed_user(unverified, verified=0)
        limit, _window = bot.CONFIRMATION_EMAIL_PER_ADDRESS
        with patch.object(bot, "send_email_verification",
                          side_effect=lambda *a, **k: sent_to.append(a) or True):
            for _ in range(limit + 6):
                # Fresh actor each time: the cap must not depend on the caller.
                auth_subject_guard._SUBJECTS_BY_ACTOR.clear()
                self.client.post(RESEND, json={"email": unverified})
        self.assertEqual(len(sent_to), limit, f"{len(sent_to)} emails left the building")

    def test_being_throttled_is_not_visible_to_the_caller(self):
        """Only an address with a real unverified account can accumulate sends,
        so a distinguishable throttle response would reintroduce the oracle by a
        side door."""
        unverified = f"quiet-{secrets.token_hex(5)}@example.com"
        _seed_user(unverified, verified=0)
        limit, _window = bot.CONFIRMATION_EMAIL_PER_ADDRESS
        with patch.object(bot, "send_email_verification", return_value=True):
            for _ in range(limit):
                auth_subject_guard._SUBJECTS_BY_ACTOR.clear()
                allowed = self.client.post(RESEND, json={"email": unverified})
            auth_subject_guard._SUBJECTS_BY_ACTOR.clear()
            throttled = self.client.post(RESEND, json={"email": unverified})
        self.assertEqual(allowed.status_code, throttled.status_code)
        self.assertEqual(allowed.get_json(), throttled.get_json())

    def test_the_admin_console_still_gets_the_real_outcome(self):
        """The one caller that is allowed to know. It arrives through
        `admin_login_required` and can already read the user list, so there is
        nothing to withhold -- and the audit row it writes needs the trace id.
        It is also exempt from the per-address cap, because "an operator
        deliberately pressed resend for this customer" is the case where
        refusing would be the bug."""
        unverified = f"admin-{secrets.token_hex(5)}@example.com"
        _seed_user(unverified, verified=0)
        limit, _window = bot.CONFIRMATION_EMAIL_PER_ADDRESS
        for _ in range(limit + 2):
            auth_subject_guard.subject_event_refused(
                unverified, *bot.CONFIRMATION_EMAIL_PER_ADDRESS)
        with patch.object(bot, "send_email_verification", return_value=True):
            result = bot.resend_account_confirmation_by_email(
                unverified, source="admin_email_page", privileged=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("trace_id"), "the admin audit row needs a trace id")
        self.assertNotEqual(result["message"], bot.RESEND_CONFIRMATION_NEUTRAL_MESSAGE)

    def test_the_admin_route_asks_for_the_privileged_form(self):
        """Pinned from the call site. `privileged` defaults to False, so the leak
        cannot come back by someone deleting a keyword argument -- but the admin
        console losing its trace id can, and it fails silently."""
        import inspect
        source = inspect.getsource(bot.admin_emails_resend_confirmation)
        self.assertIn("privileged=True", source)
        signature = inspect.signature(bot.resend_account_confirmation_by_email)
        self.assertIs(signature.parameters["privileged"].default, False)
        self.assertEqual(signature.parameters["privileged"].kind,
                         inspect.Parameter.KEYWORD_ONLY)


if __name__ == "__main__":
    unittest.main()
