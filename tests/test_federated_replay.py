"""A native provider credential buys exactly one session.

The thing being defended against is narrow and worth stating exactly: a native
Apple/Google ID token is a bearer credential whose signature, issuer, audience
and expiry all stay valid on every presentation. Verification cannot tell the
first use from the hundredth. So anything that obtains the token once -- a
hostile SDK in the process, a jailbroken device, a logging sink, a TLS-
terminating proxy -- can mint sessions until `exp`, typically an hour.

The nonce does not close it, and these tests do not pretend otherwise:

  * `oidc_tokens.py` compares a nonce only when the caller passes one
    (`if nonce:`), so omitting it skips rather than fails.
  * The value is chosen by the client, so a replayer reads the token's own nonce
    claim and presents it back. Both sides are attacker-controlled.
  * `@react-native-google-signin` v16.1.5 has no nonce field at all, so for
    native Google there is nothing to compare even in principle.

What closes it is the server remembering. These tests drive that at both levels:
the ledger itself, where concurrency and retention live, and the route, where
the only thing that matters is that a second presentation yields no session.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest

_DB_DIR = tempfile.mkdtemp(prefix="federated-replay-")
_DB_PATH = os.path.join(_DB_DIR, "replay.db")

# Bound before `import bot`: importing bot connects and runs init_db() at module
# scope, so a late binding writes ~170 tables into the real dev database.
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ.setdefault("SECRET_KEY", "federated-replay-test")
os.environ["GOOGLE_SIGNIN_CLIENT_ID"] = "test-web.apps.googleusercontent.com"
os.environ["GOOGLE_SIGNIN_NATIVE_CLIENT_IDS"] = "test-ios.apps.googleusercontent.com"
os.environ.pop("GOOGLE_SIGNIN_ENABLED", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402
from services import federated_replay, google_identity  # noqa: E402

NATIVE_AUD = "test-ios.apps.googleusercontent.com"


def _rows():
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT credential_hash, provider, expires_at FROM {federated_replay.TABLE}"
        )
        return [tuple(db_service.row_values(row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _clear():
    conn = db_service.connect()
    try:
        conn.execute(f"DELETE FROM {federated_replay.TABLE}")
        conn.commit()
    finally:
        conn.close()


class TheLedgerHonoursACredentialOnce(unittest.TestCase):
    def setUp(self):
        bot.init_db()
        _clear()

    def test_the_first_presentation_is_accepted(self):
        federated_replay.consume("google", "credential-one", expires_at_epoch=time.time() + 600)
        self.assertEqual(len(_rows()), 1)

    def test_the_same_bytes_a_second_time_are_refused(self):
        """The whole point, stated at its smallest."""

        token = "credential-replayed"
        federated_replay.consume("google", token, expires_at_epoch=time.time() + 600)
        with self.assertRaises(federated_replay.ReplayError) as caught:
            federated_replay.consume("google", token, expires_at_epoch=time.time() + 600)
        self.assertEqual(caught.exception.reason, "credential_replayed")

    def test_a_different_credential_for_the_same_member_is_not_a_replay(self):
        """Over-refusing would break normal sign-in, which is the likelier bug.

        Every real sheet completion mints a new token, so if the ledger keyed on
        anything coarser than the exact bytes -- the subject, say -- a member
        would be locked out of their second sign-in.
        """

        federated_replay.consume("google", "first-token", expires_at_epoch=time.time() + 600)
        federated_replay.consume("google", "second-token", expires_at_epoch=time.time() + 600)
        self.assertEqual(len(_rows()), 2)

    def test_an_empty_credential_is_refused_rather_than_consumed(self):
        """Otherwise one row with the digest of "" would consume every later blank."""

        with self.assertRaises(federated_replay.ReplayError) as caught:
            federated_replay.consume("google", "", expires_at_epoch=time.time() + 600)
        self.assertEqual(caught.exception.reason, "missing_credential")
        self.assertEqual(_rows(), [])


class TwoAtOnceDoNotBothWin(unittest.TestCase):
    """Concurrency, which is the case an attacker actually runs.

    Someone holding a captured token does not present it twice politely in
    sequence -- they fire both at once, hoping to land between a read and a
    write. A `SELECT` then `INSERT` would let both observe an unconsumed
    credential and both proceed. The guarantee has to come from the database.
    """

    def setUp(self):
        bot.init_db()
        _clear()

    def test_simultaneous_presentations_yield_exactly_one_success(self):
        token = "credential-raced"
        attempts = 8
        start = threading.Barrier(attempts)
        outcomes = []
        lock = threading.Lock()

        def attempt():
            # Released together so the inserts genuinely overlap rather than
            # queueing behind thread startup.
            start.wait(timeout=10)
            try:
                federated_replay.consume("google", token, expires_at_epoch=time.time() + 600)
                result = "accepted"
            except federated_replay.ReplayError:
                result = "refused"
            except Exception as exc:  # pragma: no cover - surfaced below
                result = f"error:{exc.__class__.__name__}"
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=attempt) for _ in range(attempts)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(len(outcomes), attempts, outcomes)
        self.assertEqual(
            outcomes.count("accepted"), 1,
            f"exactly one concurrent presentation may win, got {outcomes}",
        )
        # An error is not a pass. A refusal caused by a lock timeout would look
        # like defence while actually being an outage.
        self.assertEqual(
            outcomes.count("refused"), attempts - 1,
            f"the losers must be refusals, not errors: {outcomes}",
        )
        self.assertEqual(len(_rows()), 1)


class WhatIsStoredAndForHowLong(unittest.TestCase):
    def setUp(self):
        bot.init_db()
        _clear()

    def test_the_token_itself_is_never_written(self):
        """A stored ID token is a stored credential."""

        token = "a-very-distinctive-credential-string"
        federated_replay.consume("google", token, expires_at_epoch=time.time() + 600)
        stored = _rows()[0]
        self.assertNotIn(token, str(stored))
        # And what is stored is the digest, which cannot be turned back into a
        # token by whoever can read the table.
        self.assertEqual(stored[0], federated_replay.digest(token))
        self.assertEqual(len(stored[0]), 64)

    def test_retention_follows_the_credential_not_a_fixed_window(self):
        """The row stops mattering exactly when the signature check takes over."""

        expiry = time.time() + 300
        federated_replay.consume("google", "short-lived", expires_at_epoch=expiry)
        stored_expiry = _rows()[0][2]
        expected = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(expiry))
        self.assertEqual(stored_expiry, expected)

    def test_a_token_claiming_a_far_future_expiry_cannot_pin_a_row_forever(self):
        """The bound is written out here, not read from the module.

        An earlier version of this test computed its ceiling from
        `MAX_RETENTION_SECONDS` itself, which made it unfalsifiable: raising
        the constant to 400 days moved the ceiling with it and the assertion
        still passed. A retention bound has to be asserted against a number
        chosen independently of the code under test, or it is not a bound.
        """

        far = time.time() + (400 * 24 * 3600)
        federated_replay.consume("google", "immortal", expires_at_epoch=far)
        stored_expiry = _rows()[0][2]
        # Apple and Google both publish ID tokens with roughly hour-long
        # lifetimes; a day and a bit is already generous for remembering one.
        ceiling = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + (25 * 3600))
        )
        self.assertLess(
            stored_expiry, ceiling,
            f"retention reached {stored_expiry}, past the 25h bound this "
            f"module is allowed",
        )

    def test_the_declared_retention_ceiling_is_itself_bounded(self):
        """And the constant is pinned directly, so raising it is a visible act.

        The test above catches a long retention that actually gets stored. This
        one catches the constant drifting even if some other clamp happens to
        mask it, which keeps "how long do we keep credential digests?" an
        answer in the test suite rather than only in the source.
        """

        self.assertLessEqual(federated_replay.MAX_RETENTION_SECONDS, 25 * 3600)
        self.assertGreaterEqual(
            federated_replay.MAX_RETENTION_SECONDS, 3600,
            "shorter than a provider token's own lifetime would re-open the "
            "replay window inside the credential's validity",
        )
        self.assertLessEqual(federated_replay.FALLBACK_RETENTION_SECONDS,
                             federated_replay.MAX_RETENTION_SECONDS)

    def test_an_unreadable_expiry_falls_back_rather_than_forgetting(self):
        """Forgetting immediately would re-open the window it exists to close."""

        federated_replay.consume("google", "no-exp", expires_at_epoch=None)
        stored_expiry = _rows()[0][2]
        self.assertGreater(stored_expiry, time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))

    def test_expired_rows_are_purged_on_the_write_path(self):
        """Retention that depends on a worker nobody wrote is not retention.

        The sibling module `oauth_login_state` ships a `purge_expired` with zero
        call sites in the repository, so its table grows without bound. This one
        is driven from `consume`, which is the only path guaranteed to run.
        """

        conn = db_service.connect()
        try:
            conn.execute(
                f"INSERT INTO {federated_replay.TABLE} "
                f"(credential_hash, provider, consumed_at, expires_at) VALUES (?, ?, ?, ?)",
                ("stale" + "0" * 59, "google", "2000-01-01 00:00:00", "2000-01-01 01:00:00"),
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(len(_rows()), 1)

        federated_replay.consume("google", "fresh-one", expires_at_epoch=time.time() + 600)

        remaining = [row[0] for row in _rows()]
        self.assertNotIn("stale" + "0" * 59, remaining)
        self.assertIn(federated_replay.digest("fresh-one"), remaining)


class _NativeGoogle:
    """Google's verifier, stubbed, asserting the route asks for no nonce."""

    def __init__(self, subject, email, *, expires_in=600):
        self.claims = {
            "iss": "https://accounts.google.com",
            "aud": NATIVE_AUD,
            "sub": subject,
            "email": email,
            "email_verified": True,
            "name": "Replay Member",
            "exp": int(time.time()) + expires_in,
        }
        self._real = None

    def __enter__(self):
        self._real = google_identity.verify_assertion

        def fake(credential, *, nonce=""):
            if nonce:
                raise AssertionError("the Google leg must not be asked for a nonce")
            return dict(self.claims)

        google_identity.verify_assertion = fake
        return self

    def __exit__(self, *exc):
        google_identity.verify_assertion = self._real
        return False


class TheRouteRefusesAReplayedCredential(unittest.TestCase):
    """End to end, because the ledger being correct and the route consulting it
    are two separate things and only the route decides whether a session exists."""

    def setUp(self):
        bot.init_db()
        _clear()
        bot.app.config["TESTING"] = True
        self.client = bot.app.test_client()

    def _post(self, token, subject="replay-subject", email="replay@example.com"):
        with _NativeGoogle(subject, email):
            return self.client.post("/api/mobile/auth/federated", json={
                "provider": "google", "id_token": token, "nonce": "client-invented",
            })

    def test_the_second_presentation_of_one_token_is_refused(self):
        token = "route-level-token"
        first = self._post(token)
        # A new subject is asked for age and agreement -- that is a successful
        # verification, and it is the state an attacker would be replaying from.
        self.assertEqual(first.status_code, 403, first.get_data(as_text=True))
        self.assertEqual((first.get_json() or {}).get("error_code"),
                         "federated_signup_required")

        second = self._post(token)
        self.assertEqual(second.status_code, 401, second.get_data(as_text=True))
        self.assertEqual((second.get_json() or {}).get("error_code"),
                         "invalid_provider_response")

    def test_a_replay_hands_back_no_signup_ticket(self):
        """The ticket is the thing that becomes an account, so it is the thing
        a replayer wants. A second ticket for one credential would let one
        captured token be redeemed twice even though no session was issued."""

        token = "ticket-replay-token"
        first = self._post(token)
        self.assertTrue((first.get_json() or {}).get("signup_ticket"))

        second = self._post(token)
        self.assertFalse((second.get_json() or {}).get("signup_ticket"))

    def test_a_fresh_token_for_the_same_subject_still_works(self):
        """The regression that would matter most: refusing real sign-ins."""

        self._post("first-sheet-completion")
        again = self._post("second-sheet-completion")
        self.assertEqual(again.status_code, 403, again.get_data(as_text=True))
        self.assertEqual((again.get_json() or {}).get("error_code"),
                         "federated_signup_required")

    def test_the_refusal_names_no_credential(self):
        """A digest in a response or a log is a correlation key for the exact
        credential this module exists to protect."""

        token = "secret-bearer-token"
        self._post(token)
        body = self._post(token).get_data(as_text=True)
        self.assertNotIn(token, body)
        self.assertNotIn(federated_replay.digest(token), body)


class ALedgerThatCannotAnswerAdmitsNobody(unittest.TestCase):
    """The `except` around `consume` catches `ReplayError` and nothing else.

    That narrowness is the whole fail-closed property, and it is invisible: a
    broadened `except Exception: pass` would leave every other test in this file
    green while turning a database outage into an unlimited replay window. So it
    is pinned here by making the ledger raise something that is not a
    `ReplayError` and asserting no session, no ticket and no account come out.

    Which is the right direction. A ledger that cannot say whether a credential
    was already honoured has exactly two answers available, and "yes, come in"
    is the one that cannot be taken back.
    """

    def setUp(self):
        bot.init_db()
        _clear()
        bot.app.config["TESTING"] = True
        self._real_consume = federated_replay.consume

    def tearDown(self):
        federated_replay.consume = self._real_consume
        bot.app.config.pop("PROPAGATE_EXCEPTIONS", None)

    def _break_the_ledger(self):
        def unavailable(*args, **kwargs):
            raise RuntimeError("the credential ledger is unavailable")

        federated_replay.consume = unavailable

    def _post(self, client, token, subject, email):
        # Handled the way production handles it -- a 500 from the error handler
        # -- rather than re-raised into the test, which would prove only that an
        # exception happened and not what the caller did with it.
        bot.app.config["PROPAGATE_EXCEPTIONS"] = False
        with _NativeGoogle(subject, email):
            return client.post("/api/mobile/auth/federated", json={
                "provider": "google", "id_token": token, "nonce": "client-invented",
            })

    def test_a_new_identity_gets_no_signup_ticket_while_the_ledger_is_down(self):
        client = bot.app.test_client()
        self._break_the_ledger()
        response = self._post(client, "ledger-down-token",
                              "ledger-down-subject", "ledger-down@example.com")

        self.assertEqual(response.status_code, 500, response.get_data(as_text=True))
        body = response.get_json() or {}
        self.assertFalse(body.get("signup_ticket"))
        self.assertFalse(body.get("token"))
        self.assertEqual(_rows(), [])

    def test_an_already_linked_member_is_not_admitted_while_the_ledger_is_down(self):
        """The costlier half. A new subject only loses a ticket; an existing one
        is a real account, and a storage error must not be a way into it."""

        client = bot.app.test_client()
        first = self._post(client, "pre-outage-token",
                           "outage-subject", "outage@example.com")
        self.assertEqual((first.get_json() or {}).get("error_code"),
                         "federated_signup_required", first.get_data(as_text=True))
        created = client.post("/api/mobile/auth/federated/signup", json={
            "signup_ticket": (first.get_json() or {}).get("signup_ticket"),
            "age_confirmed": True,
            "terms_accepted": True,
            "country": "United Kingdom",
        })
        self.assertEqual(created.status_code, 200, created.get_data(as_text=True))

        # A fresh client, because the account just created left a session cookie
        # on this one -- Flask sessions here are signed cookies, so reusing it
        # would show a session that predates the outage and prove nothing.
        signed_out = bot.app.test_client()
        self._break_the_ledger()
        response = self._post(signed_out, "during-outage-token",
                              "outage-subject", "outage@example.com")

        self.assertEqual(response.status_code, 500, response.get_data(as_text=True))
        body = response.get_json() or {}
        self.assertFalse(body.get("token"))
        self.assertFalse(body.get("refresh_token"))
        with signed_out.session_transaction() as session:
            self.assertIsNone(session.get("user_id"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
