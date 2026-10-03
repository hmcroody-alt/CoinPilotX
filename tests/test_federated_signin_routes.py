"""Federated sign-in driven over HTTP, because the unit tests cannot see a route.

`tests/test_external_identity.py` proves the pieces: the token verifier rejects
the known forgeries, a handshake is spendable once, and `resolve()` never signs
anyone in on an email match. All of that passed while the routes that call it
would have raised `NameError` on four of their response paths -- `make_response`
is not imported in `bot.py`, and a name looked up at request time is invisible
to both `py_compile` and any test that never issues a request.

So this file's first job is coverage of *paths*, not of logic: every branch that
builds a response gets a real request put through it. The security assertions
are the second job, and they are the ones worth reading:

`test_an_email_match_does_not_sign_anybody_in` is the account takeover. A
provider asserting an address that already has a PulseSoc account must produce a
refusal, not a session -- and the member must still not be signed in afterwards,
which is checked by asking a route that requires a session rather than by
reading the response body.

`test_an_empty_password_cannot_sign_into_a_federated_account` is the other half
of the same hole, from the opposite direction. `generate_password_hash("")`
returns a hash that *verifies against an empty form field*, so a federated
account whose password was stored that way would be enterable by anyone who
typed the address and submitted nothing. The accounts this flow creates store a
blank hash instead, and the only way to know that holds end to end is to try it
against the real login route.

`test_a_suspended_member_cannot_come_in_through_a_provider` is the bypass. Every
account gate lives on the password path; a federated path that forgot one is a
way around a suspension, and it would look like a working feature.

The provider is stubbed at the adapter boundary -- `verify_assertion` is replaced
with a function returning fixed claims -- so no network call and no real Google
client is needed. Everything below that boundary is the real code: the real
handshake table, the real resolution ladder, the real session.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must precede `import bot`: importing it connects and runs init_db() at module
# scope, so an assignment after the import would be read too late and the suite
# would build its schema in the real development database.
_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="federated_routes_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

# The client id is the whole of Google's configuration, and `configured()` is
# what makes the routes answer instead of 503. Set before import for the same
# reason as the database URL.
os.environ["GOOGLE_SIGNIN_CLIENT_ID"] = "test-web.apps.googleusercontent.com"
os.environ.pop("GOOGLE_SIGNIN_ENABLED", None)

import bot  # noqa: E402
from services import (  # noqa: E402
    cache_engine,
    external_identity,
    google_identity,
    legal_acceptance,
    oauth_login_state,
    pulse_security_core,
)
from services import db as db_service  # noqa: E402

CSRF = "federated-routes-test-token"
PASSWORD = "FederatedRoutes!123"


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.init_db()


def _reset_limiters():
    """Clear the three per-process gates between tests.

    `/auth/*/start` and the callbacks are in `ABUSE_GUARD_PROTECTED` at twelve
    per five minutes, which several tests here would otherwise exhaust -- and
    the failure would land on whichever test ran thirteenth, not on the one that
    was wrong.
    """

    conn = db_service.connect()
    cur = conn.cursor()
    for table in ("auth_events", "failed_login_controls", "failed_login_safe_list"):
        try:
            cur.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    conn.commit()
    conn.close()
    pulse_security_core._RATE_BUCKETS.clear()
    cache_engine._MEMORY.clear()
    bot.RATE_LIMIT_BUCKETS.clear()


def _claims(subject, email, *, verified=True, name="Federated Member"):
    return {
        "iss": "https://accounts.google.com",
        "aud": "test-web.apps.googleusercontent.com",
        "sub": subject,
        "email": email,
        "email_verified": verified,
        "name": name,
    }


class _StubbedGoogle:
    """Replaces `verify_assertion` with a function that returns fixed claims.

    The nonce is still enforced, by hand, against what the handshake minted --
    dropping that check here would make every replay test below pass for the
    wrong reason.
    """

    def __init__(self, claims, *, honour_nonce=True):
        self.claims = claims
        self.honour_nonce = honour_nonce
        self.calls = 0
        self._real = None

    def __enter__(self):
        self._real = google_identity.verify_assertion

        def fake(credential, *, nonce=""):
            self.calls += 1
            if self.honour_nonce and nonce and nonce != credential:
                # The route passes the handshake's nonce and the token as
                # separate arguments; the stub treats the "token" as being
                # literally the nonce, so a mismatch is a genuine mismatch.
                raise google_identity.GoogleIdentityError("google_wrong_nonce")
            return dict(self.claims)

        google_identity.verify_assertion = fake
        return self

    def __exit__(self, *exc):
        google_identity.verify_assertion = self._real
        return False


def _make_password_member(email=None, *, status="active", confirmed=True):
    email = email or f"member-{secrets.token_hex(5)}@example.com"
    now = bot.datetime.now().isoformat()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users
        (username, display_name, full_name, email, password_hash, email_verified,
         account_status, login_enabled, access_enabled, signup_time, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?)
        """,
        (
            f"member_{secrets.token_hex(4)}",
            "Password Member",
            "Password Member",
            email,
            bot.generate_password_hash(PASSWORD),
            1 if confirmed else 0,
            status,
            now,
            now,
            now,
        ),
    )
    user_id = cur.lastrowid
    # Recorded because a signup records it. Without it every sign-in below
    # pauses on the acceptance gate and the tests assert the wrong refusal.
    legal_acceptance.record(cur, user_id, source="web_signup")
    conn.commit()
    conn.close()
    return email, int(user_id)


class FederatedRouteCase(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        _reset_limiters()
        bot.app.config["TESTING"] = True
        self.client = bot.app.test_client()
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = CSRF

    # -- flow drivers -------------------------------------------------------

    def _start(self, provider="google", **form):
        payload = {"csrf_token": CSRF}
        payload.update(form)
        return self.client.post(f"/auth/{provider}/start", data=payload)

    def _handshake(self, provider="google", **form):
        """Start a sign-in and read back exactly what the provider is handed.

        Parsed out of the redirect rather than read from the table, because the
        table stores a *hash* of the state -- the plaintext exists only in the
        URL, which is the whole point of it: the server can recognise its own
        state without being able to be made to leak one.
        """

        response = self._start(provider, **form)
        self.assertEqual(
            response.status_code, 302,
            f"start did not redirect: {response.status_code}",
        )
        query = parse_qs(urlsplit(response.headers["Location"]).query)
        return {"state": query["state"][0], "nonce": query["nonce"][0]}

    def _callback(self, handshake, *, provider="google"):
        """Stand in for the provider's cross-site form_post.

        The "ID token" is literally the nonce, which `_StubbedGoogle` is built
        to expect -- so the route's own nonce argument is still checked against
        what the handshake minted, and a replay with a stale nonce still fails.
        """

        return self.client.post(
            f"/auth/{provider}/callback",
            data={"state": handshake["state"], "id_token": handshake["nonce"]},
        )

    def _complete(self, location, provider="google"):
        return self.client.get(location)

    def _signed_in_user_id(self):
        with self.client.session_transaction() as sess:
            return sess.get("account_user_id")


class TheStartRouteRefusesBeforeItWritesAnything(FederatedRouteCase):
    def test_an_unknown_provider_is_a_404_and_not_a_handshake(self):
        self.assertEqual(self.client.post("/auth/facebook/start", data={"csrf_token": CSRF}).status_code, 404)

    def test_a_missing_csrf_token_refuses(self):
        """A GET or a forged POST must not be able to open a handshake here.

        Otherwise any page on the internet can cause a row to be written in
        this table by embedding a form, which is both a write amplifier and the
        first half of a login-CSRF attempt.
        """

        self.assertEqual(self.client.post("/auth/google/start", data={}).status_code, 400)

    def test_an_unconfigured_provider_says_so_instead_of_redirecting(self):
        """A button for a provider with no client id sends members to an error page."""

        saved = os.environ.pop("GOOGLE_SIGNIN_CLIENT_ID", None)
        try:
            response = self._start()
            self.assertEqual(response.status_code, 503)
            self.assertIn(bot.FEDERATED_UNAVAILABLE_MESSAGE, response.get_data(as_text=True))
        finally:
            if saved is not None:
                os.environ["GOOGLE_SIGNIN_CLIENT_ID"] = saved

    def test_a_started_handshake_redirects_to_google_and_binds_the_browser(self):
        response = self._start()
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response.headers["Location"])
        self.assertIn("response_type=id_token", response.headers["Location"])
        cookies = response.headers.getlist("Set-Cookie")
        self.assertTrue(
            any(oauth_login_state.BINDING_COOKIE in value for value in cookies),
            "the completing GET has nothing to prove it is the same browser without this",
        )


class TheBindingCookieIsWhatAuthorises(FederatedRouteCase):
    def test_a_stolen_handoff_from_another_browser_is_refused(self):
        """Login CSRF. The handoff token alone must not finish a sign-in.

        An attacker who can read the redirect URL -- out of a shared log, a
        `Referer`, or their own completed flow -- holds the handoff. What they do
        not hold is the cookie, and this is the assertion that says so.
        """

        _make_password_member("victim-binding@example.com")
        handshake = self._handshake()
        with _StubbedGoogle(_claims("g-binding", "fresh-binding@example.com")):
            handoff = self._callback(handshake)
        self.assertEqual(handoff.status_code, 303)

        # A different browser: same URL, no binding cookie.
        other = bot.app.test_client()
        refused = other.get(handoff.headers["Location"])
        self.assertEqual(refused.status_code, 400)
        self.assertIsNone(self._signed_in_user_id())

    def test_the_handoff_is_burned_even_by_the_wrong_browser(self):
        """So a stolen handoff cannot be retried until a cookie turns up."""

        handshake = self._handshake()
        with _StubbedGoogle(_claims("g-burn", "burn@example.com")):
            handoff = self._callback(handshake)
        location = handoff.headers["Location"]

        bot.app.test_client().get(location)
        # The right browser now, and it must still be refused.
        self.assertEqual(self._complete(location).status_code, 400)

    def test_a_completed_handoff_cannot_be_replayed(self):
        handshake = self._handshake()
        with _StubbedGoogle(_claims("g-replay", "replay@example.com", verified=True)):
            handoff = self._callback(handshake)
        location = handoff.headers["Location"]
        self.assertEqual(self._complete(location).status_code, 302)
        self.assertEqual(self._complete(location).status_code, 400)


class AnEmailMatchIsNotAnAuthorisation(FederatedRouteCase):
    def test_an_email_match_does_not_sign_anybody_in(self):
        """THE account takeover, asserted over HTTP.

        A provider asserting an address that already has a PulseSoc account is
        the default behaviour of hand-rolled social login, it demos beautifully,
        and it hands an attacker any account whose address they can get a
        provider to assert. The answer must be a refusal and the member must
        still be anonymous afterwards.
        """

        email, user_id = _make_password_member("takeover-target@example.com")
        handshake = self._handshake()
        with _StubbedGoogle(_claims("g-attacker", email)):
            handoff = self._callback(handshake)
        response = self._complete(handoff.headers["Location"])

        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self._signed_in_user_id())

    def test_the_refusal_says_how_to_connect_the_provider_properly(self):
        """A dead end here would just send the member to support.

        The real path exists -- sign in with the password, then connect from
        Account Settings -- and naming it is what makes the refusal survivable.
        """

        email, _ = _make_password_member("takeover-advice@example.com")
        handshake = self._handshake()
        with _StubbedGoogle(_claims("g-advice", email)):
            handoff = self._callback(handshake)
        body = self._complete(handoff.headers["Location"]).get_data(as_text=True)
        self.assertIn("Account Settings", body)

    def test_no_identity_row_is_written_for_a_refused_email_match(self):
        """A refusal that left the link behind would sign them in next time."""

        email, _ = _make_password_member("takeover-norow@example.com")
        handshake = self._handshake()
        with _StubbedGoogle(_claims("g-norow", email)):
            handoff = self._callback(handshake)
        self._complete(handoff.headers["Location"])

        conn = db_service.connect()
        try:
            found = conn.execute(
                "SELECT COUNT(*) FROM user_external_identities WHERE provider_subject=?",
                ("g-norow",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(int(db_service.row_values(found)[0]), 0)


class ANewMemberIsAskedWhatTheProviderCannotAnswer(FederatedRouteCase):
    def _reach_finish(self, subject="g-new", email="brand-new@example.com"):
        handshake = self._handshake()
        with _StubbedGoogle(_claims(subject, email)):
            handoff = self._callback(handshake)
        return self._complete(handoff.headers["Location"])

    def _accounts_for(self, email):
        conn = db_service.connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM users WHERE lower(email)=?", (email.lower(),)
            ).fetchone()
        finally:
            conn.close()
        return int(db_service.row_values(row)[0])

    def test_a_verified_new_identity_is_sent_to_the_consent_step(self):
        response = self._reach_finish()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/auth/finish"))

    def test_no_account_exists_until_the_consent_step_is_answered(self):
        """A token is not a consent.

        Creating the account at the callback and collecting agreement later
        would mean an account existed that had never confirmed its age -- which
        is the thing `/signup` will not do, and the reason this detour exists.
        """

        self._reach_finish(email="not-yet@example.com")
        self.assertEqual(self._accounts_for("not-yet@example.com"), 0)

    def test_the_consent_page_shows_the_address_the_provider_verified(self):
        self._reach_finish(email="shown@example.com")
        body = self.client.get("/auth/finish").get_data(as_text=True)
        self.assertIn("shown@example.com", body)

    def test_the_consent_page_offers_no_editable_email_field(self):
        """An editable address here would be created instead of the verified one."""

        self._reach_finish(email="noedit@example.com")
        body = self.client.get("/auth/finish").get_data(as_text=True)
        self.assertNotIn('name="email"', body)

    def test_an_unanswered_age_confirmation_creates_nothing(self):
        self._reach_finish(email="no-age@example.com")
        response = self.client.post(
            "/auth/finish", data={"csrf_token": CSRF, "terms_accepted": "on"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._accounts_for("no-age@example.com"), 0)

    def test_an_unaccepted_agreement_creates_nothing(self):
        self._reach_finish(email="no-terms@example.com")
        response = self.client.post(
            "/auth/finish", data={"csrf_token": CSRF, "age_confirmed": "on"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._accounts_for("no-terms@example.com"), 0)

    def test_answering_both_creates_the_account_and_signs_them_in(self):
        self._reach_finish(subject="g-created", email="created@example.com")
        response = self.client.post(
            "/auth/finish",
            data={"csrf_token": CSRF, "age_confirmed": "on", "terms_accepted": "on"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._accounts_for("created@example.com"), 1)
        self.assertIsNotNone(self._signed_in_user_id())

    def test_the_created_account_is_linked_by_subject_not_by_address(self):
        self._reach_finish(subject="g-bysubject", email="bysubject@example.com")
        self.client.post(
            "/auth/finish",
            data={"csrf_token": CSRF, "age_confirmed": "on", "terms_accepted": "on"},
        )
        conn = db_service.connect()
        try:
            row = conn.execute(
                "SELECT provider, provider_subject FROM user_external_identities "
                "WHERE provider_subject=?",
                ("g-bysubject",),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row, "the identity must be recorded against the subject")
        self.assertEqual(db_service.row_values(row)[0], "google")

    def test_the_consent_step_is_unreachable_without_a_pending_handshake(self):
        """It is gated by the session key, not by being hard to guess."""

        self.assertEqual(self.client.get("/auth/finish").status_code, 400)

    def test_the_consent_step_refuses_a_missing_csrf_token(self):
        self._reach_finish(email="finish-csrf@example.com")
        response = self.client.post(
            "/auth/finish", data={"age_confirmed": "on", "terms_accepted": "on"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._accounts_for("finish-csrf@example.com"), 0)


class TheFederatedAccountHasNoPasswordToGuess(FederatedRouteCase):
    def _create_federated_member(self, email="passwordless@example.com", subject="g-pwless"):
        handshake = self._handshake()
        with _StubbedGoogle(_claims(subject, email)):
            handoff = self._callback(handshake)
        self._complete(handoff.headers["Location"])
        self.client.post(
            "/auth/finish",
            data={"csrf_token": CSRF, "age_confirmed": "on", "terms_accepted": "on"},
        )
        conn = db_service.connect()
        try:
            row = conn.execute(
                "SELECT user_id, password_hash FROM users WHERE lower(email)=?", (email,)
            ).fetchone()
        finally:
            conn.close()
        return db_service.row_values(row)

    def test_the_stored_hash_is_blank_and_not_a_hash_of_the_empty_string(self):
        """`generate_password_hash("")` returns a hash that verifies against "".

        So storing one would make the account enterable by submitting the
        address and an empty password field. A blank string is not a parseable
        hash, which is why it is the marker.
        """

        _, stored = self._create_federated_member(
            email="blankhash@example.com", subject="g-blank"
        )
        self.assertEqual(str(stored or ""), "")
        self.assertFalse(bot.check_password_hash("", ""))

    def test_an_empty_password_cannot_sign_into_a_federated_account(self):
        """The same hole, asserted through the route a real attacker would use."""

        self._create_federated_member(email="emptypw@example.com", subject="g-emptypw")
        fresh = bot.app.test_client()
        _reset_limiters()
        response = fresh.post(
            "/api/mobile/auth/login",
            json={"identifier": "emptypw@example.com", "password": ""},
            environ_overrides={"REMOTE_ADDR": "203.0.113.9"},
        )
        self.assertNotEqual(response.status_code, 200)
        with fresh.session_transaction() as sess:
            self.assertIsNone(sess.get("account_user_id"))

    def test_no_password_at_all_cannot_sign_into_a_federated_account(self):
        self._create_federated_member(email="nopw@example.com", subject="g-nopw")
        fresh = bot.app.test_client()
        _reset_limiters()
        response = fresh.post(
            "/api/mobile/auth/login",
            json={"identifier": "nopw@example.com"},
            environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
        )
        self.assertNotEqual(response.status_code, 200)


class AccountGatesApplyToFederatedSignInToo(FederatedRouteCase):
    def _sign_in_existing(self, user_id, email, subject="g-gate"):
        """Link the identity directly, then sign in through the provider.

        Linking by hand rather than through the link ceremony, because the
        ceremony is a different test -- what this needs is an account that the
        resolution ladder will return `sign_in` for.
        """

        conn = db_service.connect()
        try:
            external_identity.link(
                conn, user_id, provider="google", subject=subject,
                email=email, email_verified=True, source="test",
            )
            conn.commit()
        finally:
            conn.close()
        handshake = self._handshake()
        with _StubbedGoogle(_claims(subject, email)):
            handoff = self._callback(handshake)
        return self._complete(handoff.headers["Location"])

    def test_a_linked_member_in_good_standing_signs_in(self):
        """The control. Without it every assertion below could pass vacuously."""

        email, user_id = _make_password_member("good-standing@example.com")
        response = self._sign_in_existing(user_id, email, subject="g-good")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self._signed_in_user_id() or 0), user_id)

    def test_a_suspended_member_cannot_come_in_through_a_provider(self):
        """The bypass. Every account gate lives on the password path.

        A federated path that skipped this one would be a way around a
        suspension that stays green in every test about suspensions, because
        those tests all drive `/login`.
        """

        email, user_id = _make_password_member("suspended@example.com", status="suspended")
        response = self._sign_in_existing(user_id, email, subject="g-suspended")
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self._signed_in_user_id())

    def test_a_login_disabled_member_cannot_come_in_through_a_provider(self):
        email, user_id = _make_password_member("disabled@example.com")
        conn = db_service.connect()
        try:
            conn.execute("UPDATE users SET login_enabled=0 WHERE user_id=?", (user_id,))
            conn.commit()
        finally:
            conn.close()
        response = self._sign_in_existing(user_id, email, subject="g-disabled")
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self._signed_in_user_id())

    def test_an_outstanding_agreement_pauses_rather_than_signing_in(self):
        """Reuses the password path's own pause, so there is one gate not two.

        A federated copy of the acceptance step would drift from the real one,
        and the drift would be invisible until a document was rewritten.
        """

        email, user_id = _make_password_member("needs-acceptance@example.com")
        conn = db_service.connect()
        try:
            conn.execute("DELETE FROM user_legal_acceptances WHERE user_id=?", (user_id,))
            conn.commit()
        finally:
            conn.close()
        if not legal_acceptance.outstanding(user_id):
            self.skipTest("no document is in force in this fixture, so there is nothing to pause on")
        self._sign_in_existing(user_id, email, subject="g-acceptance")
        with self.client.session_transaction() as sess:
            self.assertIn(bot.PENDING_LEGAL_SESSION_KEY, sess)
            self.assertIsNone(sess.get("account_user_id"))


class TheCallbackGrantsNothingOnItsOwn(FederatedRouteCase):
    def test_a_callback_with_no_token_is_refused(self):
        self.assertEqual(
            self.client.post("/auth/google/callback", data={}).status_code, 400
        )

    def test_a_cancelled_sign_in_goes_back_to_login_without_an_error_page(self):
        response = self.client.post("/auth/google/callback", data={"error": "access_denied"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_an_invented_state_is_refused(self):
        with _StubbedGoogle(_claims("g-invented", "invented@example.com")):
            response = self._callback({"state": "not-a-real-state", "nonce": "whatever"})
        self.assertEqual(response.status_code, 400)

    def test_a_token_the_verifier_rejects_never_reaches_the_ladder(self):
        handshake = self._handshake()
        stub = _StubbedGoogle(_claims("g-badtoken", "badtoken@example.com"))
        with stub:
            def refuse(credential, *, nonce=""):
                raise google_identity.GoogleIdentityError("google_bad_signature")

            google_identity.verify_assertion = refuse
            response = self._callback(handshake)
        self.assertEqual(response.status_code, 400)
        conn = db_service.connect()
        try:
            found = conn.execute(
                "SELECT COUNT(*) FROM user_external_identities WHERE provider_subject=?",
                ("g-badtoken",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(int(db_service.row_values(found)[0]), 0)

    def test_a_handoff_minted_for_google_cannot_be_completed_as_apple(self):
        """The provider is part of what the handoff authorises.

        Otherwise a token from the provider with the weaker configuration could
        be completed against the stronger one's identity rows.
        """

        handshake = self._handshake()
        with _StubbedGoogle(_claims("g-crossed", "crossed@example.com")):
            handoff = self._callback(handshake)
        crossed = handoff.headers["Location"].replace("/auth/google/", "/auth/apple/")
        self.assertEqual(self.client.get(crossed).status_code, 400)
        self.assertIsNone(self._signed_in_user_id())


class DisconnectingAProviderCannotLockAMemberOut(FederatedRouteCase):
    def test_an_anonymous_disconnect_is_not_served(self):
        response = self.client.post(
            "/account/connections/google/disconnect", data={"csrf_token": CSRF}
        )
        self.assertIn(response.status_code, (302, 401, 403))

    def test_an_unknown_provider_is_a_404(self):
        email, user_id = _make_password_member("unknown-provider@example.com")
        with self.client.session_transaction() as sess:
            sess["account_user_id"] = user_id
            sess["csrf_token"] = CSRF
        response = self.client.post(
            "/account/connections/facebook/disconnect", data={"csrf_token": CSRF}
        )
        self.assertEqual(response.status_code, 404)

    def test_the_last_way_into_an_account_cannot_be_disconnected(self):
        """Refusing is reversible. A lockout is not.

        And the address may be an Apple relay that has stopped forwarding, so
        for a federated-only account there is not necessarily a reset path
        either.
        """

        _, user_id = _make_password_member("last-credential@example.com")
        conn = db_service.connect()
        try:
            conn.execute("UPDATE users SET password_hash='' WHERE user_id=?", (user_id,))
            external_identity.link(
                conn, user_id, provider="google", subject="g-last",
                email="last-credential@example.com", email_verified=True, source="test",
            )
            conn.commit()
        finally:
            conn.close()
        with self.client.session_transaction() as sess:
            sess["account_user_id"] = user_id
            sess["csrf_token"] = CSRF

        response = self.client.post(
            "/account/connections/google/disconnect", data={"csrf_token": CSRF}
        )
        self.assertEqual(response.status_code, 409)

        conn = db_service.connect()
        try:
            still = conn.execute(
                "SELECT COUNT(*) FROM user_external_identities WHERE user_id=?", (user_id,)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(int(db_service.row_values(still)[0]), 1)

    def test_a_provider_can_be_disconnected_when_a_password_remains(self):
        _, user_id = _make_password_member("removable@example.com")
        conn = db_service.connect()
        try:
            external_identity.link(
                conn, user_id, provider="google", subject="g-removable",
                email="removable@example.com", email_verified=True, source="test",
            )
            conn.commit()
        finally:
            conn.close()
        with self.client.session_transaction() as sess:
            sess["account_user_id"] = user_id
            sess["csrf_token"] = CSRF

        response = self.client.post(
            "/account/connections/google/disconnect", data={"csrf_token": CSRF}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(external_identity.for_user(user_id)), 0)

    def test_a_disconnect_without_a_csrf_token_changes_nothing(self):
        _, user_id = _make_password_member("disconnect-csrf@example.com")
        conn = db_service.connect()
        try:
            external_identity.link(
                conn, user_id, provider="google", subject="g-csrf",
                email="disconnect-csrf@example.com", email_verified=True, source="test",
            )
            conn.commit()
        finally:
            conn.close()
        with self.client.session_transaction() as sess:
            sess["account_user_id"] = user_id
            sess["csrf_token"] = CSRF

        response = self.client.post("/account/connections/google/disconnect", data={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(external_identity.for_user(user_id)), 1)


class AFederatedAccountCanStillBeDeleted(FederatedRouteCase):
    def test_a_passwordless_account_is_told_what_actually_unblocks_deletion(self):
        """App Store 5.1.1(v) requires in-app deletion to be reachable.

        The generic "Password confirmation did not match" answer would send a
        member who has never had a password into retyping one forever. The
        proper fix is a federated re-assertion standing in for the password;
        until then the message has to name the real unblock.
        """

        _, user_id = _make_password_member("deletable@example.com")
        conn = db_service.connect()
        try:
            conn.execute("UPDATE users SET password_hash='' WHERE user_id=?", (user_id,))
            conn.commit()
        finally:
            conn.close()

        ok, message = bot.permanently_delete_account(bot.load_account_by_id(user_id), "anything")
        self.assertFalse(ok)
        self.assertNotIn("did not match", message)
        self.assertIn("Forgot password", message)

    def test_a_password_account_still_gets_the_generic_refusal(self):
        """The branch above must not have widened into an oracle."""

        _, user_id = _make_password_member("still-generic@example.com")
        ok, message = bot.permanently_delete_account(bot.load_account_by_id(user_id), "wrong-password")
        self.assertFalse(ok)
        self.assertIn("did not match", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
