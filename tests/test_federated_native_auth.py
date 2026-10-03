"""One account, whichever surface the member authenticated from.

The product claim this file exists to hold true is a single sentence: create an
account with Google on pulsesoc.com, open the iPhone app, tap Continue with
Google, and you are in *the same* PulseSoc account. It is easy to say and easy
to break, because the way it breaks is not a crash -- it is a second account,
silently created, with the member's name on it and none of their things in it.

So the native endpoint deliberately owns no identity logic. It verifies the
assertion with the same adapter function the web callback uses, hands the
profile to the same `external_identity.resolve` ladder, and applies the same
restriction and legal-acceptance gates in the same order. The tests below check
that it really is the same ladder by driving a member in through one surface and
out through the other, rather than by asserting that the code looks shared.

The other half is the refusal that must survive translation to a phone. A
verified Google email matching an existing PulseSoc account is not an
authorisation on the web and must not become one here; `account_link_required`
is the whole content of that, and a native client that received a session
instead would be the account-takeover bug with a nicer animation.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must precede `import bot`: importing it connects and runs init_db() at module
# scope, so an assignment after the import would be read too late and the suite
# would build its schema in the real development database.
_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="federated_native_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

# The web client id makes `configured()` true; the native one makes
# `native_client_ids()` non-empty, and the endpoint refuses with
# `provider_config_error` without it. Both set before import, for the same
# reason as the database URL.
os.environ["GOOGLE_SIGNIN_CLIENT_ID"] = "test-web.apps.googleusercontent.com"
os.environ["GOOGLE_SIGNIN_NATIVE_CLIENT_IDS"] = "test-ios.apps.googleusercontent.com"
os.environ.pop("GOOGLE_SIGNIN_ENABLED", None)

import bot  # noqa: E402
from services import (  # noqa: E402
    cache_engine,
    external_identity,
    google_identity,
    legal_acceptance,
    pulse_security_core,
)
from services import db as db_service  # noqa: E402

PASSWORD = "FederatedNative!123"
NATIVE_AUD = "test-ios.apps.googleusercontent.com"


def _ticket_body(claims):
    """The base64 half of a ticket, built the way the minter builds it.

    Written out here rather than called from `bot` so that a change to the
    minting format cannot silently change what these tests are handing over.
    """

    import base64

    raw = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _signed_ticket(claims):
    """A ticket signed with the real key -- what only this server can produce.

    Used to isolate the checks that happen *after* the signature, which an
    unsigned forgery can never reach.
    """

    import hashlib
    import hmac as hmac_module

    body = _ticket_body(claims)
    sig = hmac_module.new(
        bot.COINPILOTX_LEGAL_ACCEPTANCE_KEY.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{body}.{sig}"


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.init_db()


def _reset_limiters():
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


class _NativeGoogle:
    """Stands in for Google's signature check, still enforcing the nonce.

    The token string *is* the nonce, so the route's own nonce argument is
    compared against something that can genuinely differ -- dropping that would
    make the replay and mismatch tests below pass for the wrong reason.
    """

    def __init__(self, subject, email, *, verified=True, name="Native Member",
                 audience=NATIVE_AUD):
        self.claims = {
            "iss": "https://accounts.google.com",
            "aud": audience,
            "sub": subject,
            "email": email,
            "email_verified": verified,
            "name": name,
        }
        self._real = None

    def __enter__(self):
        self._real = google_identity.verify_assertion

        def fake(credential, *, nonce=""):
            if nonce and nonce != credential:
                raise google_identity.GoogleIdentityError("google_wrong_nonce")
            if self.claims["aud"] not in google_identity.audiences():
                raise google_identity.GoogleIdentityError("google_wrong_audience")
            return dict(self.claims)

        google_identity.verify_assertion = fake
        return self

    def __exit__(self, *exc):
        google_identity.verify_assertion = self._real
        return False


class NativeFederatedCase(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        _reset_limiters()
        bot.app.config["TESTING"] = True
        self.client = bot.app.test_client()

    # -- drivers ------------------------------------------------------------

    def _native(self, *, subject, email, nonce="native-nonce", **extra):
        payload = {"provider": "google", "id_token": nonce, "nonce": nonce}
        payload.update(extra)
        with _NativeGoogle(subject, email):
            return self.client.post("/api/mobile/auth/federated", json=payload)

    def _native_signup(self, response, **extra):
        """Answer the age/terms step with the ticket the refusal handed back."""

        body = response.get_json() or {}
        payload = {
            "signup_ticket": body.get("signup_ticket"),
            "age_confirmed": True,
            "terms_accepted": True,
            "country": "United Kingdom",
        }
        payload.update(extra)
        return self.client.post("/api/mobile/auth/federated/signup", json=payload)

    def _create_native_account(self, subject, email):
        first = self._native(subject=subject, email=email)
        self.assertEqual(first.status_code, 403, first.get_data(as_text=True))
        self.assertEqual((first.get_json() or {}).get("error_code"),
                         "federated_signup_required")
        done = self._native_signup(first)
        self.assertEqual(done.status_code, 200, done.get_data(as_text=True))
        return done

    def _user_id_of(self, email):
        conn = db_service.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM users WHERE lower(email)=?", (email.lower(),))
            rows = cur.fetchall()
            return [int(db_service.row_values(row)[0]) for row in rows]
        finally:
            conn.close()

    def _owners_of(self, provider, subject):
        """Which users hold a given provider subject. Should always be 0 or 1."""
        conn = db_service.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id FROM user_external_identities "
                "WHERE provider=? AND provider_subject=? ORDER BY user_id",
                (provider, subject),
            )
            return [int(db_service.row_values(row)[0]) for row in cur.fetchall()]
        finally:
            conn.close()

    def _subjects_on(self, user_id):
        """Every provider subject attached to one user, sorted."""
        conn = db_service.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT provider_subject FROM user_external_identities WHERE user_id=?",
                (user_id,),
            )
            return sorted(str(db_service.row_values(row)[0]) for row in cur.fetchall())
        finally:
            conn.close()

    def _make_password_member(self, email, *, status="active", accept_legal=True):
        now = bot.datetime.now().isoformat()
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users
            (username, display_name, full_name, email, password_hash, email_verified,
             account_status, login_enabled, access_enabled, signup_time, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, 1, 1, ?, ?, ?)
            """,
            (f"member_{secrets.token_hex(4)}", "Password Member", "Password Member",
             email, bot.generate_password_hash(PASSWORD), status, now, now, now),
        )
        user_id = int(cur.lastrowid)
        if accept_legal:
            legal_acceptance.record(cur, user_id, source="web_signup")
        conn.commit()
        conn.close()
        return user_id


class TheSameAccountAnswersOnEitherSurface(NativeFederatedCase):
    """The product claim, driven end to end rather than asserted structurally."""

    def test_an_identity_created_on_the_web_signs_in_natively(self):
        # The web half, done the way the web does it: a row in the canonical
        # external identity table, which is what `/auth/google/complete` writes.
        email = "crossing@example.com"
        user_id = self._make_password_member(email)
        conn = db_service.connect()
        cur = conn.cursor()
        external_identity.link(cur, user_id, provider="google", subject="sub-web-1",
                               email=email, email_verified=True,
                               display_name="Crossing", source="web")
        conn.commit()
        conn.close()

        response = self._native(subject="sub-web-1", email=email)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self._signed_in(), user_id)
        self.assertEqual(len(self._user_id_of(email)), 1, "a second account appeared")

    def test_an_account_created_natively_is_one_account(self):
        email = "native-first@example.com"
        self._create_native_account("sub-native-1", email)
        self.assertEqual(len(self._user_id_of(email)), 1)

    def test_signing_in_again_natively_resolves_to_the_same_account(self):
        # The returning case. A second native sign-in must find the subject,
        # not create beside it -- which is the shape duplicate accounts take.
        email = "returning@example.com"
        self._create_native_account("sub-native-2", email)
        first = self._user_id_of(email)
        again = self._native(subject="sub-native-2", email=email)
        self.assertEqual(again.status_code, 200, again.get_data(as_text=True))
        self.assertEqual(self._user_id_of(email), first)

    def test_the_subject_is_the_identity_and_the_email_is_not(self):
        # Apple's Hide My Email can be switched off, and a Google account can
        # change its primary address. The member is the same member, so the
        # stable subject has to win over the string that moved.
        email = "before@example.com"
        self._create_native_account("sub-stable", email)
        user_id = self._signed_in()
        moved = self._native(subject="sub-stable", email="after@example.com")
        self.assertEqual(moved.status_code, 200, moved.get_data(as_text=True))
        self.assertEqual(self._signed_in(), user_id)
        self.assertEqual(self._user_id_of("after@example.com"), [],
                         "the changed address created an account")

    def _signed_in(self):
        with self.client.session_transaction() as sess:
            return sess.get("account_user_id")


class AMatchingEmailIsStillNotAnAuthorisation(NativeFederatedCase):
    """The takeover invariant, on the surface where it is easiest to forget."""

    def test_a_verified_email_matching_an_account_is_refused_not_linked(self):
        email = "owner@example.com"
        user_id = self._make_password_member(email)
        response = self._native(subject="attacker-subject", email=email)
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual((response.get_json() or {}).get("error_code"),
                         "account_link_required")

    def test_the_refusal_grants_no_session_and_writes_no_identity(self):
        email = "owner2@example.com"
        user_id = self._make_password_member(email)
        self._native(subject="attacker-subject-2", email=email)
        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("account_user_id"))
        self.assertEqual(external_identity.for_user(user_id), [])

    def test_the_signup_step_re_asks_and_refuses_a_collision_that_appeared_late(self):
        # The ticket is minted when no account owns the address. If one is
        # created while the member reads the age screen, honouring the ticket
        # would attach their provider to a stranger's brand new account.
        email = "racing@example.com"
        first = self._native(subject="sub-race", email=email)
        self.assertEqual(first.status_code, 403)
        self._make_password_member(email)
        late = self._native_signup(first)
        self.assertEqual(late.status_code, 409, late.get_data(as_text=True))
        self.assertEqual((late.get_json() or {}).get("error_code"),
                         "account_link_required")


class TheGatesAreNotOptionalOnAPhone(NativeFederatedCase):
    def test_a_suspended_account_is_refused_a_native_session(self):
        email = "suspended@example.com"
        user_id = self._make_password_member(email, status="suspended")
        conn = db_service.connect()
        cur = conn.cursor()
        external_identity.link(cur, user_id, provider="google", subject="sub-susp",
                               email=email, email_verified=True,
                               display_name="S", source="web")
        conn.commit()
        conn.close()
        response = self._native(subject="sub-susp", email=email)
        self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
        self.assertEqual((response.get_json() or {}).get("error_code"), "account_restricted")
        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("account_user_id"))

    def test_an_outstanding_document_pauses_the_sign_in(self):
        # Never accepted anything, so the ledger has an outstanding version.
        email = "unagreed@example.com"
        user_id = self._make_password_member(email, accept_legal=False)
        conn = db_service.connect()
        cur = conn.cursor()
        external_identity.link(cur, user_id, provider="google", subject="sub-legal",
                               email=email, email_verified=True,
                               display_name="L", source="web")
        conn.commit()
        conn.close()
        response = self._native(subject="sub-legal", email=email)
        self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
        body = response.get_json() or {}
        self.assertTrue(body.get("legal_acceptance") or body.get("ticket"),
                        f"no acceptance challenge in {body}")
        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("account_user_id"))

    def test_a_native_signup_records_the_agreement_it_collected(self):
        # The account is created only after the member answers, so the ledger
        # must show it -- otherwise the next sign-in pauses on a document they
        # just agreed to and the gate looks broken.
        email = "agreed@example.com"
        self._create_native_account("sub-agreed", email)
        user_id = self._user_id_of(email)[0]
        self.assertFalse(legal_acceptance.outstanding(user_id))

    def test_consent_is_required_and_is_not_carried_by_the_ticket(self):
        # The ticket is minted before anybody is asked. A consent travelling
        # inside it would be a consent nobody gave.
        email = "noconsent@example.com"
        first = self._native(subject="sub-noconsent", email=email)
        refused = self._native_signup(first, age_confirmed=False, terms_accepted=False)
        self.assertEqual(refused.status_code, 400, refused.get_data(as_text=True))
        self.assertEqual((refused.get_json() or {}).get("error_code"), "consent_required")
        self.assertEqual(self._user_id_of(email), [], "an account was created anyway")


class TheAssertionIsTheOnlyThingTrusted(NativeFederatedCase):
    def test_a_token_whose_nonce_does_not_match_is_refused(self):
        with _NativeGoogle("sub-nonce", "nonce@example.com"):
            response = self.client.post("/api/mobile/auth/federated", json={
                "provider": "google", "id_token": "token-value", "nonce": "a-different-nonce",
            })
        self.assertEqual(response.status_code, 401, response.get_data(as_text=True))
        self.assertEqual((response.get_json() or {}).get("error_code"),
                         "invalid_provider_response")

    def test_a_token_minted_for_another_audience_is_refused(self):
        with _NativeGoogle("sub-aud", "aud@example.com", audience="someone-else.apps.googleusercontent.com"):
            response = self.client.post("/api/mobile/auth/federated", json={
                "provider": "google", "id_token": "n", "nonce": "n",
            })
        self.assertEqual(response.status_code, 401, response.get_data(as_text=True))

    def test_a_client_supplied_subject_is_ignored(self):
        # The one that would undo everything: if the body could name the
        # subject, any phone could claim any identity. The attacker holds a
        # valid assertion -- for their OWN provider account -- and asks to be
        # treated as somebody else's by saying so in the JSON.
        email = "claimed@example.com"
        self._create_native_account("sub-real", email)
        victim = self._user_id_of(email)[0]

        attacker = bot.app.test_client()
        with _NativeGoogle("sub-other", "other@example.com"):
            attacker.post("/api/mobile/auth/federated", json={
                "provider": "google", "id_token": "n", "nonce": "n",
                "sub": "sub-real", "subject": "sub-real",
                "provider_subject": "sub-real", "user_id": victim,
                "email": email,
            })

        # Asserting the attacker's own session is not the victim is necessary
        # but not sufficient -- a fresh client would fail that comparison even
        # if the endpoint were wide open, because the attacker would be signed
        # in as their own new account either way. The load-bearing check is the
        # ledger: `sub-real` still belongs to exactly one user, the victim, and
        # the attacker's subject was not quietly attached to them.
        with attacker.session_transaction() as sess:
            self.assertNotEqual(sess.get("account_user_id"), victim)

        self.assertEqual(
            self._owners_of("google", "sub-real"), [victim],
            "the real subject changed hands",
        )
        self.assertEqual(
            self._subjects_on(victim), ["sub-real"],
            "the attacker's subject was linked onto the victim",
        )

    def test_a_build_with_no_native_audience_configured_refuses_everything(self):
        # The guard that makes the audience check above mean anything. Each
        # adapter's `audiences()` is the web client id *plus* the native ones,
        # combined -- so with no native client id configured the web id is still
        # an accepted audience, and a token minted for pulsesoc.com in a
        # browser would verify when posted here from a phone. The endpoint
        # therefore refuses outright, as configuration, rather than quietly
        # falling back to the web audience.
        real = google_identity.native_client_ids
        google_identity.native_client_ids = lambda: []
        try:
            with _NativeGoogle("sub-no-native-aud", "noaud@example.com",
                               audience="test-web.apps.googleusercontent.com"):
                response = self.client.post("/api/mobile/auth/federated", json={
                    "provider": "google", "id_token": "n", "nonce": "n",
                })
        finally:
            google_identity.native_client_ids = real
        self.assertEqual(response.status_code, 503, response.get_data(as_text=True))
        self.assertEqual(response.get_json().get("error_code"), "provider_config_error")
        self.assertEqual(self._user_id_of("noaud@example.com"), [],
                         "a web-audience token created an account from the app")

    def test_an_unknown_provider_is_refused(self):
        response = self.client.post("/api/mobile/auth/federated", json={
            "provider": "facebook", "id_token": "n", "nonce": "n",
        })
        self.assertEqual(response.status_code, 404, response.get_data(as_text=True))

    def test_a_malformed_signup_ticket_creates_nothing(self):
        response = self.client.post("/api/mobile/auth/federated/signup", json={
            "signup_ticket": "forged.deadbeef",
            "age_confirmed": True, "terms_accepted": True,
        })
        self.assertEqual(response.status_code, 401, response.get_data(as_text=True))

    def test_a_well_formed_signup_ticket_with_a_wrong_signature_creates_nothing(self):
        # The test that actually pins the HMAC. Garbage like "forged.deadbeef"
        # above is refused by the base64 and JSON decoding alone, so it would
        # still be refused with no signature check at all -- it proves the
        # endpoint survives junk, not that the ticket is authenticated. This
        # one hands over a payload that is correct in every respect the reader
        # inspects (purpose, provider, subject, an expiry in the future) and
        # wrong only in that the attacker could not sign it.
        forged = _ticket_body({
            "p": bot.FEDERATED_SIGNUP_TICKET_PURPOSE,
            "pr": "google",
            "sub": "sub-forged",
            "em": "forged@example.com",
            "ev": 1,
            "dn": "Forged",
            "iat": int(time.time()),
            "exp": int(time.time()) + 900,
        }) + "." + "0" * 64

        self.assertEqual(bot.read_federated_signup_ticket(forged), {})
        response = self.client.post("/api/mobile/auth/federated/signup", json={
            "signup_ticket": forged, "age_confirmed": True, "terms_accepted": True,
        })
        self.assertEqual(response.status_code, 401, response.get_data(as_text=True))
        self.assertEqual(self._user_id_of("forged@example.com"), [],
                         "an unsigned claim of identity created an account")

    def test_an_expired_signup_ticket_creates_nothing(self):
        stale, _ = bot.federated_signup_ticket(
            "google",
            {"subject": "sub-stale", "email": "stale@example.com",
             "email_verified": True, "display_name": "Stale"},
            issued_at=time.time() - 86_400,
        )
        response = self.client.post("/api/mobile/auth/federated/signup", json={
            "signup_ticket": stale, "age_confirmed": True, "terms_accepted": True,
        })
        self.assertEqual(response.status_code, 401, response.get_data(as_text=True))
        self.assertEqual(self._user_id_of("stale@example.com"), [])

    def test_the_signup_window_stays_short(self):
        # Hardcoded, not derived from the constant: a window computed from the
        # value under test stays "expired" however large the value becomes.
        self.assertLessEqual(bot.FEDERATED_SIGNUP_TICKET_TTL_SECONDS, 1800)

    def test_a_legal_ticket_cannot_be_spent_as_a_signup_ticket(self):
        # Both are HMAC'd with the same key, so a signature check alone does
        # not separate them. This one is refused twice over: its purpose is
        # wrong, and it also carries no provider or subject -- so it does NOT
        # on its own demonstrate that the purpose field is doing any work. The
        # next test isolates that.
        user_id = self._make_password_member("tickets@example.com")
        legal, _ = bot.mobile_legal_acceptance_ticket(user_id, {"terms": "1"})
        self.assertEqual(bot.read_federated_signup_ticket(legal), {})

    def test_a_validly_signed_ticket_of_another_purpose_is_refused(self):
        # The purpose field in isolation. Signed with the real key and complete
        # in every other respect, so the only thing wrong with it is that it was
        # minted for something else. Today's sibling ticket type happens to also
        # lack a provider and subject, which means the purpose check is
        # currently belt-and-braces -- but the next ticket type this server
        # signs with this key may not be so conveniently shaped, and by then
        # this is the check standing between the two.
        signed = _signed_ticket({
            "p": "some_other_feature",
            "pr": "google",
            "sub": "sub-other-purpose",
            "em": "purpose@example.com",
            "ev": 1,
            "dn": "Purpose",
            "iat": int(time.time()),
            "exp": int(time.time()) + 900,
        })
        self.assertEqual(bot.read_federated_signup_ticket(signed), {})
        response = self.client.post("/api/mobile/auth/federated/signup", json={
            "signup_ticket": signed, "age_confirmed": True, "terms_accepted": True,
        })
        self.assertEqual(response.status_code, 401, response.get_data(as_text=True))
        self.assertEqual(self._user_id_of("purpose@example.com"), [])


class TappingTwiceDoesNotCreateTwoAccounts(NativeFederatedCase):
    def test_the_same_ticket_spent_twice_yields_one_account(self):
        # A double tap, or a retry after a dropped response. The second attempt
        # re-resolves, finds the subject now linked, and signs in instead of
        # creating beside it.
        email = "double@example.com"
        first = self._native(subject="sub-double", email=email)
        self.assertEqual(first.status_code, 403)
        one = self._native_signup(first)
        two = self._native_signup(first)
        self.assertEqual(one.status_code, 200, one.get_data(as_text=True))
        self.assertEqual(two.status_code, 200, two.get_data(as_text=True))
        self.assertEqual(len(self._user_id_of(email)), 1, "the retry created a second account")

    def test_the_database_refuses_a_second_row_for_one_subject(self):
        # Not left to SELECT-before-INSERT: two requests that interleave past
        # the check both pass it, and only a constraint stops the second write.
        email = "unique@example.com"
        self._create_native_account("sub-unique", email)
        other = self._make_password_member("other@example.com")
        conn = db_service.connect()
        cur = conn.cursor()
        with self.assertRaises(Exception):
            external_identity.link(cur, other, provider="google", subject="sub-unique",
                                   email="other@example.com", email_verified=True,
                                   display_name="Other", source="web")
            conn.commit()
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()


class TheRefusalsAreDistinguishable(NativeFederatedCase):
    """§38: a client that cannot tell the cases apart cannot help anybody."""

    def test_every_declared_code_is_distinct_and_named(self):
        codes = list(bot.FEDERATED_NATIVE_ERRORS)
        self.assertEqual(len(codes), len(set(codes)))
        for code in codes:
            self.assertTrue(bot.FEDERATED_NATIVE_ERRORS[code].strip())

    def test_no_refusal_leaks_verification_internals(self):
        # The member gets a sentence; the reason stays in the log.
        with _NativeGoogle("sub-leak", "leak@example.com", audience="wrong.example"):
            response = self.client.post("/api/mobile/auth/federated", json={
                "provider": "google", "id_token": "n", "nonce": "n",
            })
        body = response.get_data(as_text=True).lower()
        for leak in ("jwks", "signature", "modulus", "aud=", "issuer", "traceback"):
            self.assertNotIn(leak, body)

    def test_the_token_never_appears_in_the_response(self):
        secret_token = "super-secret-assertion-value"
        with _NativeGoogle("sub-tok", "tok@example.com"):
            response = self.client.post("/api/mobile/auth/federated", json={
                "provider": "google", "id_token": secret_token, "nonce": "different",
            })
        self.assertNotIn(secret_token, response.get_data(as_text=True))

    def test_no_auth_event_records_the_assertion(self):
        secret_token = "another-secret-assertion"
        with _NativeGoogle("sub-tok2", "tok2@example.com"):
            self.client.post("/api/mobile/auth/federated", json={
                "provider": "google", "id_token": secret_token, "nonce": "mismatch",
            })
        conn = db_service.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT details FROM auth_events")
            blob = json.dumps([db_service.row_values(r)[0] for r in cur.fetchall()])
        finally:
            conn.close()
        self.assertNotIn(secret_token, blob)

    def test_every_event_this_endpoint_emits_is_classified(self):
        # The native surface emits into the same ledger as the web one, so an
        # unclassified name goes missing from the Security Center either way.
        conn = db_service.connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM auth_events")
            conn.commit()
        finally:
            conn.close()
        self._native(subject="sub-ev", email="ev@example.com")
        conn = db_service.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT event_type FROM auth_events")
            names = [db_service.row_values(r)[0] for r in cur.fetchall()]
        finally:
            conn.close()
        self.assertTrue(names, "the endpoint emitted nothing at all")
        for name in names:
            self.assertNotEqual(bot.auth_event_class(name), "unclassified", name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
