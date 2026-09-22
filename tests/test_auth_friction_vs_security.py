"""A person locked out of their own account is not an attacker.

Three places asked `status='failed'` and read the answer as "an attack". That
column records how a request ended, not who sent it. It is equally true of
`forgot_password_request_failed` (our own exception), of
`unverified_email_change_failed` (a wrong password typed by someone already
logged in), and of `verification_link_rejected` (a mail client prefetched the
link, or the user double-tapped it).

Two of the three readers were display -- the Security Center's Failed Logins tab
and its Suspicious Domains list, where every row carries a "Block Domain"
button. The third was `failed_login_recent_count`, which is not display at all:
it drives the automated cooldowns. Fourteen `status='failed'` rows on one domain
inside five minutes buys that entire domain a fifteen-minute lockout.

A provider outage produces precisely that shape. Every pending signup emits
`verification_email_failed` at once, and most users share two or three mail
domains -- so the mail breaking is what locks out everybody whose mail broke,
and gmail.com is the first domain over the line. That is the failure this file
exists to prevent.

`login_unconfirmed` is the sharpest case in the other direction: the visitor
supplied the *correct* password, which is evidence they own the account.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import re
import secrets
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="auth_friction_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import db as db_service  # noqa: E402

_SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.init_db()


class AuthFrictionCase(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        bot.app.config["TESTING"] = True
        self.client = bot.app.test_client()
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM auth_events")
        now = bot.datetime.now().isoformat()
        cur.execute(
            "INSERT INTO admin_users (full_name, email, password_hash, role, status, must_change_password, created_at) "
            "VALUES ('Owner', ?, ?, 'owner', 'active', 0, ?)",
            (f"owner-{secrets.token_hex(6)}@example.com", bot.generate_password_hash("OwnerPass!12345"), now),
        )
        conn.commit()
        cur.execute("SELECT id FROM admin_users ORDER BY id DESC LIMIT 1")
        self.admin_id = cur.fetchone()[0]
        conn.close()

    def _seed(self, event_type, domain="gmail.com", count=1, email_hash=None, status="failed"):
        now = bot.datetime.now().isoformat()
        conn = db_service.connect()
        cur = conn.cursor()
        for index in range(count):
            cur.execute(
                "INSERT INTO auth_events (event_type, email, email_hash, email_domain, user_id, status, severity, "
                "ip_address, created_at) VALUES (?, ?, ?, ?, 0, ?, 'Low', '203.0.113.5', ?)",
                (
                    event_type,
                    "s***@" + domain,
                    email_hash or f"hash-{event_type}-{index}",
                    domain,
                    status,
                    now,
                ),
            )
        conn.commit()
        conn.close()

    def _security_page(self, tab):
        with self.client.session_transaction() as sess:
            now = bot.datetime.now().isoformat()
            sess["admin_user_id"] = self.admin_id
            sess["admin_session_issued_at"] = now
            sess["admin_session_last_seen"] = now
        return self.client.get(f"/admin/security?tab={tab}").get_data(as_text=True)

    def test_a_every_event_the_code_emits_is_classified(self):
        # The guard that keeps the rest of this file true as events are added.
        # An unclassified event reaches neither the blocking surface nor the
        # friction signal, so it goes silently missing from both.
        source = open(_SOURCE, encoding="utf-8").read()
        emitted = set()
        for call in re.finditer(r"log_auth_event\(", source):
            # The first argument, which is sometimes a conditional naming two
            # event types. Read to the first comma at depth zero.
            window = source[call.end():call.end() + 400]
            depth, end = 0, len(window)
            for index, char in enumerate(window):
                if char in "([{":
                    depth += 1
                elif char in ")]}":
                    if depth == 0:
                        end = index
                        break
                    depth -= 1
                elif char == "," and depth == 0:
                    end = index
                    break
            emitted |= set(re.findall(r'"([a-z][a-z_]+)"', window[:end]))
        self.assertGreaterEqual(len(emitted), 20, "the scraper stopped finding event names")
        unclassified = sorted(e for e in emitted if bot.auth_event_class(e) == "unclassified")
        self.assertEqual(unclassified, [])

    def test_b_knowing_the_password_is_not_an_attack(self):
        # `login_unconfirmed` means the credentials were right and only the
        # mailbox was unconfirmed.
        self.assertEqual(bot.auth_event_class("login_unconfirmed"), "friction")
        self.assertEqual(bot.auth_event_class("mobile_login_unconfirmed"), "friction")
        self.assertEqual(bot.auth_event_class("login_failed"), "security")

    def test_c_an_unknown_event_lands_in_neither_bucket(self):
        # Either default is wrong in one direction, so there is a fourth answer.
        self.assertEqual(bot.auth_event_class("something_invented_later"), "unclassified")
        self.assertNotIn("something_invented_later", bot.AUTH_SECURITY_EVENTS)
        self.assertNotIn("something_invented_later", bot.AUTH_FRICTION_EVENTS)

    def test_d_friction_alone_never_makes_a_domain_suspicious(self):
        # The exact shape that put gmail.com on the block list: three failures,
        # none of them an attack.
        self._seed("mobile_login_unconfirmed", count=2)
        self._seed("forgot_password_invalid_email", count=2)
        self._seed("verification_link_rejected", count=2)
        body = self._security_page("suspicious-domains")
        self.assertIn("No suspicious domains.", body)

    def test_e_real_failed_logins_still_make_a_domain_suspicious(self):
        # The control. Removing friction from the list must not empty it.
        self._seed("login_failed", domain="attacker.example", count=4)
        body = self._security_page("suspicious-domains")
        self.assertIn("attacker.example", body)
        self.assertNotIn("No suspicious domains.", body)

    def test_f_friction_does_not_pad_a_real_domains_count(self):
        self._seed("login_failed", domain="mixed.example", count=3)
        self._seed("mobile_login_unconfirmed", domain="mixed.example", count=40)
        body = self._security_page("suspicious-domains")
        row = [line for line in body.split("<tr>") if "mixed.example" in line]
        self.assertEqual(len(row), 1)
        self.assertIn(">3<", row[0])

    def test_g_the_failed_logins_tab_shows_no_friction_events(self):
        self._seed("login_failed", count=1)
        self._seed("verification_link_rejected", count=1)
        self._seed("forgot_password_invalid_email", count=1)
        body = self._security_page("failed-logins")
        self.assertIn("login_failed", body)
        self.assertNotIn("verification_link_rejected", body)
        self.assertNotIn("forgot_password_invalid_email", body)

    def test_h_the_friction_level_counts_people_not_retries(self):
        # One person retrying twelve times is one person. Twelve people hitting
        # the same wall once each is an outage.
        self.assertEqual(bot.auth_friction_level(events=1, accounts=1), "AUTH_FRICTION_LOW")
        self.assertEqual(bot.auth_friction_level(events=3, accounts=3), "AUTH_FRICTION_MEDIUM")
        self.assertEqual(bot.auth_friction_level(events=6, accounts=6), "AUTH_FRICTION_HIGH")
        self.assertEqual(bot.auth_friction_level(events=9, accounts=1), "AUTH_FRICTION_LOW")

    def test_i_the_snapshot_groups_a_journey_by_account_not_by_ip(self):
        # A phone moving between cells changes IP mid-journey. Grouping by IP
        # splits one stuck person into several and makes them look like several.
        self._seed("mobile_login_unconfirmed", count=3, email_hash="one-person")
        snapshot = bot.auth_friction_snapshot()
        self.assertEqual(snapshot["accounts"], 1)
        self.assertEqual(snapshot["events"], 3)
        self.assertEqual(snapshot["level"], "AUTH_FRICTION_LOW")

    def test_j_an_outage_across_accounts_reads_as_high(self):
        for index in range(6):
            self._seed("verification_email_failed", count=2, email_hash=f"person-{index}")
        snapshot = bot.auth_friction_snapshot()
        self.assertEqual(snapshot["accounts"], 6)
        self.assertEqual(snapshot["level"], "AUTH_FRICTION_HIGH")

    def test_k_security_events_are_absent_from_the_friction_signal(self):
        self._seed("login_failed", count=50, email_hash="attacker")
        snapshot = bot.auth_friction_snapshot()
        self.assertEqual(snapshot["events"], 0)
        self.assertEqual(snapshot["level"], "AUTH_FRICTION_LOW")

    def test_l_the_friction_panel_offers_no_blocking_control(self):
        self._seed("mobile_login_unconfirmed", count=3, email_hash="stuck")
        body = self._security_page("user-friction")
        panel = body.split("Nobody is stuck right now")[0]
        for control in ("block_ip", "block_domain"):
            self.assertNotIn(control, panel.split("<nav")[-1])

    def _lockout_count(self, where_sql="", params=()):
        conn = db_service.connect()
        cur = conn.cursor()
        try:
            return bot.failed_login_recent_count(cur, where_sql, params)
        finally:
            conn.close()

    def test_n_a_mail_outage_cannot_lock_out_a_domain(self):
        # The one that matters. FAILED_LOGIN_DOMAIN_LIMIT is 14 in a 300s
        # window; an outage while 20 people are signing up used to put every
        # one of those failures on gmail.com's tally and take the domain down
        # with the mail.
        self._seed("verification_email_failed", domain="gmail.com", count=20)
        self.assertEqual(self._lockout_count("AND email_domain=?", ("gmail.com",)), 0)
        self.assertLess(bot.FAILED_LOGIN_DOMAIN_LIMIT, 20, "the seeding no longer exceeds the limit it is testing")

    def test_o_a_double_tapped_verification_link_is_not_a_wrong_password(self):
        # Mail clients prefetch links and people double-tap them, so this is
        # ordinary behaviour by the account's actual owner.
        self._seed("verification_link_rejected", count=9, email_hash="owner")
        self.assertEqual(self._lockout_count("AND email_hash=?", ("owner",)), 0)
        self.assertLess(bot.FAILED_LOGIN_EMAIL_LIMIT, 9)

    def test_p_a_wrong_password_still_counts_towards_the_cooldown(self):
        # The control. Narrowing the predicate must not disarm the lockout.
        self._seed("login_failed", count=6, email_hash="attacker")
        self.assertEqual(self._lockout_count("AND email_hash=?", ("attacker",)), 6)

    def test_q_the_retired_event_name_still_counts(self):
        # `mobile_login_failed` has no emitter left but 23 rows exist in
        # production, and a lockout that forgets its own history under-counts.
        self._seed("mobile_login_failed", count=3, email_hash="legacy")
        self.assertEqual(self._lockout_count("AND email_hash=?", ("legacy",)), 3)
        self.assertEqual(bot.auth_event_class("mobile_login_failed"), "security")

    def test_r_a_block_cannot_supply_the_evidence_for_its_own_renewal(self):
        # `login_blocked` and `login_challenge_required` are emitted *by* the
        # lockout. Counting them would make a cooldown self-sustaining: it
        # would keep producing the rows that justify extending it.
        self._seed("login_blocked", count=30, email_hash="looped", status="blocked")
        self._seed("login_challenge_required", count=30, email_hash="looped", status="challenge")
        self.assertEqual(self._lockout_count("AND email_hash=?", ("looped",)), 0)
        for event in ("login_blocked", "login_challenge_required"):
            self.assertEqual(bot.auth_event_class(event), "security")
            self.assertNotIn(event, bot.AUTH_LOCKOUT_EVENTS)

    def test_s_our_own_crash_does_not_lock_out_the_person_who_hit_it(self):
        # `signup_failed` is a server-side exception. Whoever was signing up
        # did nothing wrong.
        self._seed("signup_failed", count=10, email_hash="unlucky")
        self.assertEqual(self._lockout_count("AND email_hash=?", ("unlucky",)), 0)

    def test_t_every_lockout_event_is_a_declared_security_event(self):
        # The lockout set is narrower than the security set, never wider: you
        # cannot be blocked for something we do not consider an attack.
        for event in bot.AUTH_LOCKOUT_EVENTS:
            self.assertIn(event, bot.AUTH_SECURITY_EVENTS)

    def test_m_no_readable_address_reaches_the_friction_panel(self):
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO auth_events (event_type, email, email_hash, email_domain, user_id, status, severity, created_at) "
            "VALUES ('mobile_login_unconfirmed', ?, 'h', 'example.com', 0, 'failed', 'Low', ?)",
            ("r***@example.com", bot.datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        snapshot = bot.auth_friction_snapshot()
        for journey in snapshot["journeys"]:
            self.assertIn("*", journey["masked_email"])


if __name__ == "__main__":
    unittest.main()
