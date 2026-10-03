"""Federated sign-in: the token forgeries, the replays, and the takeover.

Three things are under test and they fail very differently.

`oidc_tokens` is where a mistake is a *total* authentication bypass — not a
degraded check, a bypass. So the cases here are the known JWT forgeries rather
than happy-path coverage: `alg: none`, an HMAC token signed with the provider's
own published modulus, a token minted for a different relying party, and an
expired one. Each of those is a token that a naive verifier accepts and that
lets anybody sign in as anybody.

`oauth_login_state` is where a mistake is login CSRF, whose payoff is a victim
silently signed into an attacker's account. The cases are the replay and the
browser substitution.

`external_identity.resolve` is where a mistake is account takeover by email
assertion, and the single most important test in this file is
`test_existing_email_is_never_signed_in_automatically` — because the behaviour it
forbids is the *default* behaviour of hand-rolled social login, it looks like a
feature when you demo it, and it hands an attacker any account whose address
they can get a provider to assert.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import unittest

# Must precede the first `services.db` import: ENGINE_URL is resolved at module
# scope, so a later assignment would be read after the engine already exists and
# the suite would quietly write to the real development database.
_TEST_DB = os.path.join(tempfile.mkdtemp(prefix="psx-identity-"), "identity.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402

from services import (  # noqa: E402
    apple_identity,
    db,
    external_identity,
    google_identity,
    oauth_login_state,
    oidc_tokens,
)

JWKS_URL = "https://provider.test/keys"
ISSUER = "https://provider.test"
AUDIENCE = "com.pulsesoc.services.test"
KEY_ID = "test-key-1"


def _b64u_int(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class _Keys:
    """One RSA keypair for the suite, plus the JWKS a provider would publish."""

    def __init__(self):
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = self.private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        numbers = self.private.public_key().public_numbers()
        self.modulus_b64 = _b64u_int(numbers.n)
        self.jwk = {
            "kty": "RSA",
            "kid": KEY_ID,
            "use": "sig",
            "alg": "RS256",
            "n": self.modulus_b64,
            "e": _b64u_int(numbers.e),
        }

    def document(self) -> dict:
        return {"keys": [self.jwk]}


KEYS = _Keys()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _JwksServer:
    """Stands in for the provider's key endpoint, and counts the fetches.

    Counting matters: a verifier that re-fetches per sign-in puts a remote call
    on the login critical path and hands the provider a rate limit it can use to
    take authentication down.
    """

    def __init__(self, document):
        self.document = document
        self.calls = 0

    def __call__(self, url, timeout=None):
        self.calls += 1
        return _FakeResponse(self.document)


def _claims(**overrides) -> dict:
    now = int(time.time())
    base = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "000123.subject.abc",
        "iat": now,
        "exp": now + 600,
        "email": "member@example.test",
        "email_verified": True,
    }
    base.update(overrides)
    return base


def _signed(claims=None, *, kid=KEY_ID) -> str:
    return jwt.encode(claims or _claims(), KEYS.pem, algorithm="RS256", headers={"kid": kid})


class OidcTokenVerificationTests(unittest.TestCase):
    """The forgeries. Every method here is a token a weak verifier accepts."""

    def setUp(self):
        oidc_tokens.reset_cache()
        self.server = _JwksServer(KEYS.document())
        self._real_get = oidc_tokens.requests.get
        oidc_tokens.requests.get = self.server

    def tearDown(self):
        oidc_tokens.requests.get = self._real_get
        oidc_tokens.reset_cache()

    def _verify(self, token, **kwargs):
        options = {
            "jwks_url": JWKS_URL,
            "issuers": {ISSUER},
            "audiences": {AUDIENCE},
        }
        options.update(kwargs)
        return oidc_tokens.verify_id_token(token, **options)

    def test_a_well_formed_token_is_accepted(self):
        claims = self._verify(_signed())
        self.assertEqual(claims["sub"], "000123.subject.abc")

    def test_alg_none_is_refused(self):
        """The original JWT forgery: drop the signature and declare it absent."""

        header = _b64u(json.dumps({"alg": "none", "kid": KEY_ID}).encode())
        payload = _b64u(json.dumps(_claims()).encode())
        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(f"{header}.{payload}.")
        self.assertEqual(caught.exception.reason, "unacceptable_algorithm")

    def test_hmac_signed_with_the_published_modulus_is_refused(self):
        """A JWKS is public, so permitting HS256 turns it into a shared secret.

        Anybody can read the provider's `n`, use it as an HMAC key, and mint a
        token for any `sub` they like. This is the most dangerous single
        acceptance in the whole flow, and it is refused on the algorithm before
        a key is even looked up.
        """

        forged = jwt.encode(
            _claims(sub="attacker-chosen"),
            KEYS.modulus_b64,
            algorithm="HS256",
            headers={"kid": KEY_ID},
        )
        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(forged)
        self.assertEqual(caught.exception.reason, "unacceptable_algorithm")
        self.assertEqual(self.server.calls, 0, "refused before any key lookup")

    def test_a_token_for_a_different_relying_party_is_refused(self):
        """Correctly signed by the provider, and still not ours.

        This is the check that is easy to omit because tokens verify without it.
        Any other application using the same provider could then replay its
        users' tokens here.
        """

        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(_signed(_claims(aud="com.someone.else")))
        self.assertEqual(caught.exception.reason, "wrong_audience")

    def test_a_token_from_another_issuer_is_refused(self):
        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(_signed(_claims(iss="https://evil.test")))
        self.assertEqual(caught.exception.reason, "wrong_issuer")

    def test_an_expired_token_is_refused(self):
        now = int(time.time())
        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(_signed(_claims(iat=now - 7200, exp=now - 3600)))
        self.assertEqual(caught.exception.reason, "expired_token")

    def test_a_token_signed_by_a_key_the_provider_never_published_is_refused(self):
        stranger = _Keys()
        forged = jwt.encode(_claims(), stranger.pem, algorithm="RS256", headers={"kid": KEY_ID})
        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(forged)
        self.assertEqual(caught.exception.reason, "bad_signature")

    def test_an_unknown_key_id_is_refused(self):
        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(_signed(kid="rotated-away"))
        self.assertEqual(caught.exception.reason, "unknown_signing_key")

    def test_a_missing_nonce_is_refused_when_one_was_issued(self):
        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(_signed(), nonce="expected-nonce")
        self.assertEqual(caught.exception.reason, "missing_nonce")

    def test_a_substituted_nonce_is_refused(self):
        token = _signed(_claims(nonce="some-other-handshake"))
        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(token, nonce="expected-nonce")
        self.assertEqual(caught.exception.reason, "wrong_nonce")

    def test_a_matching_nonce_is_accepted(self):
        token = _signed(_claims(nonce="expected-nonce"))
        self.assertEqual(self._verify(token, nonce="expected-nonce")["nonce"], "expected-nonce")

    def test_an_empty_subject_is_refused(self):
        """An empty `sub` would become one identity row every stranger lands on."""

        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(_signed(_claims(sub="")))
        self.assertEqual(caught.exception.reason, "missing_subject")

    def test_an_unconfigured_audience_does_not_mean_every_audience(self):
        """Fail closed: "nothing configured" must never read as "accept all"."""

        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(_signed(), audiences=set())
        self.assertEqual(caught.exception.reason, "no_audience_configured")

    def test_an_unconfigured_issuer_does_not_mean_every_issuer(self):
        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(_signed(), issuers=set())
        self.assertEqual(caught.exception.reason, "no_issuer_configured")

    def test_only_asymmetric_families_can_be_requested_by_a_caller(self):
        """Even a caller asking for HS256 explicitly is refused."""

        with self.assertRaises(oidc_tokens.TokenError) as caught:
            self._verify(_signed(), algorithms=("HS256",))
        self.assertEqual(caught.exception.reason, "no_acceptable_algorithm")

    def test_the_key_set_is_fetched_once_for_many_sign_ins(self):
        for _ in range(5):
            self._verify(_signed())
        self.assertEqual(self.server.calls, 1)

    def test_a_rotated_key_is_picked_up_without_waiting_for_the_cache_to_lapse(self):
        """An unknown `kid` is how rotation announces itself; there is no push."""

        self._verify(_signed())
        self.assertEqual(self.server.calls, 1)

        rotated = _Keys()
        rotated.jwk["kid"] = "test-key-2"
        self.server.document = {"keys": [rotated.jwk]}
        oidc_tokens._cache[JWKS_URL]["last_attempt"] -= 120

        token = jwt.encode(_claims(), rotated.pem, algorithm="RS256", headers={"kid": "test-key-2"})
        self.assertEqual(self._verify(token)["sub"], "000123.subject.abc")
        self.assertEqual(self.server.calls, 2)

    def test_a_storm_of_unknown_key_ids_cannot_be_amplified_at_the_provider(self):
        self._verify(_signed())
        self.assertEqual(self.server.calls, 1)
        for _ in range(25):
            with self.assertRaises(oidc_tokens.TokenError):
                self._verify(_signed(kid="does-not-exist"))
        self.assertEqual(self.server.calls, 1, "refetch is floored, not per-request")

    def test_a_provider_outage_does_not_invalidate_a_cached_key(self):
        """A key set we already hold is stale, not wrong. Nobody gets logged out."""

        self._verify(_signed())

        def broken(url, timeout=None):
            raise RuntimeError("provider unreachable")

        oidc_tokens.requests.get = broken
        oidc_tokens._cache[JWKS_URL]["fetched_at"] -= 10_000
        oidc_tokens._cache[JWKS_URL]["last_attempt"] -= 10_000
        self.assertEqual(self._verify(_signed())["sub"], "000123.subject.abc")


class StateHandshakeTests(unittest.TestCase):
    """Replay and browser substitution."""

    @classmethod
    def setUpClass(cls):
        oauth_login_state.ensure_schema()

    def test_a_handshake_is_spendable_exactly_once(self):
        opened = oauth_login_state.create("apple")
        first = oauth_login_state.consume(opened["state"], "apple")
        self.assertEqual(first["provider"], "apple")
        with self.assertRaises(oauth_login_state.StateError) as caught:
            oauth_login_state.consume(opened["state"], "apple")
        self.assertEqual(caught.exception.reason, "unknown_or_spent_state")

    def test_a_state_cannot_be_spent_against_the_wrong_provider(self):
        opened = oauth_login_state.create("apple")
        with self.assertRaises(oauth_login_state.StateError):
            oauth_login_state.consume(opened["state"], "google")

    def test_an_invented_state_is_refused(self):
        with self.assertRaises(oauth_login_state.StateError) as caught:
            oauth_login_state.consume("not-a-real-state", "apple")
        self.assertEqual(caught.exception.reason, "unknown_or_spent_state")

    def test_an_unknown_and_a_spent_state_are_indistinguishable(self):
        """A prober must not be able to learn that a state value was ever real."""

        opened = oauth_login_state.create("apple")
        oauth_login_state.consume(opened["state"], "apple")
        with self.assertRaises(oauth_login_state.StateError) as spent:
            oauth_login_state.consume(opened["state"], "apple")
        with self.assertRaises(oauth_login_state.StateError) as invented:
            oauth_login_state.consume("never-existed", "apple")
        self.assertEqual(spent.exception.reason, invented.exception.reason)

    def test_an_expired_handshake_is_refused_and_still_burned(self):
        opened = oauth_login_state.create("apple", ttl_seconds=60)
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE oauth_login_states SET expires_at='2000-01-01T00:00:00.000000Z' "
                "WHERE provider='apple' AND consumed_at=''"
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(oauth_login_state.StateError) as caught:
            oauth_login_state.consume(opened["state"], "apple")
        self.assertEqual(caught.exception.reason, "expired_state")
        with self.assertRaises(oauth_login_state.StateError) as again:
            oauth_login_state.consume(opened["state"], "apple")
        self.assertEqual(again.exception.reason, "unknown_or_spent_state")

    def test_the_completing_request_must_come_from_the_browser_that_started_it(self):
        """Login CSRF. A stolen `state` alone must not finish a sign-in."""

        opened = oauth_login_state.create("apple")
        spent = oauth_login_state.consume(opened["state"], "apple")
        with self.assertRaises(oauth_login_state.StateError) as caught:
            oauth_login_state.claim_handoff(spent["handoff_token"], "a-different-browser")
        self.assertEqual(caught.exception.reason, "binding_mismatch")

    def test_the_right_browser_completes(self):
        opened = oauth_login_state.create("apple", next_path="/pulse")
        spent = oauth_login_state.consume(opened["state"], "apple")
        claimed = oauth_login_state.claim_handoff(spent["handoff_token"], opened["binding_secret"])
        self.assertEqual(claimed["next_path"], "/pulse")
        self.assertEqual(claimed["nonce"], opened["nonce"])

    def test_a_failed_binding_check_burns_the_handoff_so_it_cannot_be_guessed_at(self):
        opened = oauth_login_state.create("apple")
        spent = oauth_login_state.consume(opened["state"], "apple")
        with self.assertRaises(oauth_login_state.StateError):
            oauth_login_state.claim_handoff(spent["handoff_token"], "wrong")
        with self.assertRaises(oauth_login_state.StateError) as caught:
            oauth_login_state.claim_handoff(spent["handoff_token"], opened["binding_secret"])
        self.assertEqual(caught.exception.reason, "unknown_handoff")

    def test_a_handoff_is_single_use(self):
        opened = oauth_login_state.create("apple")
        spent = oauth_login_state.consume(opened["state"], "apple")
        oauth_login_state.claim_handoff(spent["handoff_token"], opened["binding_secret"])
        with self.assertRaises(oauth_login_state.StateError) as caught:
            oauth_login_state.claim_handoff(spent["handoff_token"], opened["binding_secret"])
        self.assertEqual(caught.exception.reason, "unknown_handoff")

    def test_google_spends_its_handshake_by_nonce(self):
        opened = oauth_login_state.create("google")
        spent = oauth_login_state.consume_by_nonce(opened["nonce"], "google")
        self.assertEqual(spent["mode"], "login")
        with self.assertRaises(oauth_login_state.StateError):
            oauth_login_state.consume_by_nonce(opened["nonce"], "google")

    def test_a_login_handshake_cannot_name_a_member_in_advance(self):
        with self.assertRaises(ValueError):
            oauth_login_state.create("apple", mode="login", link_user_id=7)

    def test_a_link_handshake_must_name_one(self):
        with self.assertRaises(ValueError):
            oauth_login_state.create("apple", mode="link")

    def test_the_mode_is_fixed_when_the_handshake_opens(self):
        """So "add Google to my account" cannot be completed as "sign me in"."""

        opened = oauth_login_state.create("google", mode="link", link_user_id=42)
        spent = oauth_login_state.consume_by_nonce(opened["nonce"], "google")
        self.assertEqual(spent["mode"], "link")
        self.assertEqual(spent["link_user_id"], 42)

    def test_an_unknown_provider_cannot_open_a_handshake(self):
        with self.assertRaises(ValueError):
            oauth_login_state.create("facebook")


class IdentityResolutionTests(unittest.TestCase):
    """Who a verified assertion means, and what must never be inferred."""

    @classmethod
    def setUpClass(cls):
        external_identity.ensure_schema()
        conn = db.connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS users ("
                "user_id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, password_hash TEXT)"
            )
            conn.commit()
        finally:
            conn.close()

    def setUp(self):
        conn = db.connect()
        try:
            conn.execute("DELETE FROM user_external_identities")
            conn.execute("DELETE FROM users")
            conn.commit()
        finally:
            conn.close()

    def _member(self, email, password_hash="pbkdf2:sha256:x"):
        conn = db.connect()
        try:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash) VALUES (?,?)", (email, password_hash)
            )
            conn.commit()
            row = conn.execute(
                "SELECT user_id FROM users WHERE email=?", (email,)
            ).fetchone()
            return int(db.row_values(row)[0])
        finally:
            conn.close()

    def _link(self, user_id, provider, subject, email=""):
        conn = db.connect()
        try:
            external_identity.link(
                conn, user_id, provider=provider, subject=subject, email=email, source="test"
            )
            conn.commit()
        finally:
            conn.close()

    def test_a_known_subject_signs_in(self):
        member = self._member("known@example.test")
        self._link(member, "apple", "000.apple.sub", "known@example.test")
        decision = external_identity.resolve(
            "apple", {"subject": "000.apple.sub", "email": "known@example.test"}
        )
        self.assertEqual(decision["decision"], "sign_in")
        self.assertEqual(decision["user_id"], member)

    def test_identity_survives_the_provider_changing_the_email(self):
        """The whole reason the key is `sub`. A changed address is not a new person."""

        member = self._member("original@example.test")
        self._link(member, "google", "google-sub-1", "original@example.test")
        decision = external_identity.resolve(
            "google", {"subject": "google-sub-1", "email": "totally-different@example.test"}
        )
        self.assertEqual(decision["decision"], "sign_in")
        self.assertEqual(decision["user_id"], member)

    def test_a_brand_new_person_is_created(self):
        decision = external_identity.resolve(
            "apple", {"subject": "fresh.sub", "email": "newcomer@example.test", "email_verified": True}
        )
        self.assertEqual(decision["decision"], "create")
        self.assertEqual(decision["email"], "newcomer@example.test")

    def test_existing_email_is_never_signed_in_automatically(self):
        """THE account-takeover test.

        A provider asserting an address is not proof of control of the PulseSoc
        account holding it. Auto-linking here is the default behaviour of most
        hand-rolled social login and it hands over any account whose address an
        attacker can get a provider to assert. The only acceptable answer is to
        ask the human.
        """

        member = self._member("victim@example.test")
        decision = external_identity.resolve(
            "apple",
            {"subject": "attacker.controlled.sub", "email": "victim@example.test", "email_verified": True},
        )
        self.assertEqual(decision["decision"], "link_required")
        self.assertEqual(decision["user_id"], 0, "no member is identified by an email match")
        self.assertNotEqual(decision["user_id"], member)

    def test_a_verified_provider_email_still_does_not_authorize_a_link(self):
        """`email_verified: true` means the provider confirmed it, not that the
        bearer controls the PulseSoc account of the same name."""

        self._member("victim@example.test")
        decision = external_identity.resolve(
            "google",
            {"subject": "g-attacker", "email": "VICTIM@Example.Test", "email_verified": True},
        )
        self.assertEqual(decision["decision"], "link_required")

    def test_email_matching_ignores_case_and_surrounding_space(self):
        """Matching the users index's own identity, or a collision slips past."""

        self._member("Mixed.Case@Example.test")
        decision = external_identity.resolve(
            "google", {"subject": "g-1", "email": "  mixed.case@example.TEST  "}
        )
        self.assertEqual(decision["decision"], "link_required")

    def test_several_accounts_on_one_address_still_refuse_to_pick_one(self):
        self._member("duplicate@example.test")
        self._member("Duplicate@example.test")
        decision = external_identity.resolve(
            "apple", {"subject": "a-1", "email": "duplicate@example.test"}
        )
        self.assertEqual(decision["decision"], "link_required")
        self.assertEqual(decision["candidate_count"], 2)

    def test_a_provider_that_disclosed_no_email_is_refused_not_invented(self):
        """Apple discloses the address only on the first authorisation."""

        decision = external_identity.resolve("apple", {"subject": "silent.sub", "email": ""})
        self.assertEqual(decision["decision"], "refused")
        self.assertEqual(decision["reason"], "provider_email_missing")

    def test_a_missing_subject_is_refused(self):
        decision = external_identity.resolve("apple", {"subject": "", "email": "x@example.test"})
        self.assertEqual(decision["decision"], "refused")
        self.assertEqual(decision["reason"], "missing_subject")

    def test_an_unknown_provider_is_refused(self):
        decision = external_identity.resolve(
            "facebook", {"subject": "s", "email": "x@example.test"}
        )
        self.assertEqual(decision["decision"], "refused")

    def test_the_same_subject_on_two_providers_is_two_identities(self):
        """`sub` namespaces are per provider; collisions across them are expected."""

        apple_member = self._member("apple-person@example.test")
        google_member = self._member("google-person@example.test")
        self._link(apple_member, "apple", "shared-string")
        self._link(google_member, "google", "shared-string")
        self.assertEqual(
            external_identity.resolve("apple", {"subject": "shared-string"})["user_id"],
            apple_member,
        )
        self.assertEqual(
            external_identity.resolve("google", {"subject": "shared-string"})["user_id"],
            google_member,
        )

    def test_a_subject_held_by_another_member_cannot_be_repointed(self):
        first = self._member("first@example.test")
        second = self._member("second@example.test")
        self._link(first, "apple", "contested.sub")
        conn = db.connect()
        try:
            with self.assertRaises(ValueError):
                external_identity.link(conn, second, provider="apple", subject="contested.sub")
        finally:
            conn.close()

    def test_relinking_the_same_subject_to_the_same_member_refreshes_rather_than_duplicates(self):
        member = self._member("repeat@example.test")
        self._link(member, "apple", "repeat.sub", "repeat@example.test")
        self._link(member, "apple", "repeat.sub", "repeat@example.test")
        self.assertEqual(len(external_identity.for_user(member)), 1)

    def test_one_member_can_hold_both_providers(self):
        member = self._member("both@example.test")
        self._link(member, "apple", "a-sub", "both@example.test")
        self._link(member, "google", "g-sub", "both@example.test")
        self.assertEqual(
            [entry["provider"] for entry in external_identity.for_user(member)],
            ["apple", "google"],
        )

    def test_an_apple_relay_address_is_recorded_as_one(self):
        member = self._member("relay@example.test")
        self._link(member, "apple", "relay.sub", "abc123@privaterelay.appleid.com")
        self.assertTrue(external_identity.for_user(member)[0]["is_private_relay"])

    def test_the_last_way_into_an_account_cannot_be_unlinked(self):
        """A member who signed up through Apple has no password to fall back on.

        Unlinking would leave an account its own owner cannot reach, and the
        address may be an Apple relay that has stopped forwarding — so there is
        no recovery path either. Refusing is reversible; lockout is not.
        """

        member = self._member("apple-only@example.test", password_hash="")
        self._link(member, "apple", "only.sub")
        allowed, reason = external_identity.can_unlink(member, "apple", has_password=False)
        self.assertFalse(allowed)
        self.assertEqual(reason, "last_credential")

    def test_a_provider_can_be_unlinked_when_a_password_remains(self):
        member = self._member("has-password@example.test")
        self._link(member, "apple", "removable.sub")
        allowed, _ = external_identity.can_unlink(member, "apple", has_password=True)
        self.assertTrue(allowed)

    def test_a_provider_can_be_unlinked_when_the_other_provider_remains(self):
        member = self._member("two-providers@example.test", password_hash="")
        self._link(member, "apple", "a.sub")
        self._link(member, "google", "g.sub")
        allowed, _ = external_identity.can_unlink(member, "apple", has_password=False)
        self.assertTrue(allowed)

    def test_unlinking_removes_exactly_one_provider(self):
        member = self._member("tidy@example.test")
        self._link(member, "apple", "a.sub")
        self._link(member, "google", "g.sub")
        conn = db.connect()
        try:
            self.assertTrue(external_identity.unlink(conn, member, "apple"))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual([e["provider"] for e in external_identity.for_user(member)], ["google"])


class AppleAdapterTests(unittest.TestCase):
    """The parts of Apple that are not like a textbook OAuth client."""

    def setUp(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.p8 = self.key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        self._saved = {k: os.environ.get(k) for k in (
            "APPLE_SIGNIN_SERVICES_ID", "APPLE_SIGNIN_KEY_ID", "APPLE_SIGNIN_PRIVATE_KEY",
            "APPLE_SIGNIN_TEAM_ID", "APPLE_SIGNIN_NATIVE_CLIENT_IDS", "APPLE_SIGNIN_ENABLED",
        )}
        os.environ.update({
            "APPLE_SIGNIN_SERVICES_ID": "com.pulsesoc.web",
            "APPLE_SIGNIN_KEY_ID": "ABC1234567",
            "APPLE_SIGNIN_TEAM_ID": "87ZC69AGSR",
            "APPLE_SIGNIN_PRIVATE_KEY": self.p8,
            "APPLE_SIGNIN_NATIVE_CLIENT_IDS": "com.pulsesoc.app",
        })
        os.environ.pop("APPLE_SIGNIN_ENABLED", None)

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_configuration_is_derived_from_completeness_not_announced_by_a_flag(self):
        self.assertTrue(apple_identity.configured())
        os.environ["APPLE_SIGNIN_PRIVATE_KEY"] = ""
        self.assertFalse(apple_identity.configured())

    def test_the_flag_can_only_turn_it_off(self):
        os.environ["APPLE_SIGNIN_ENABLED"] = "false"
        self.assertFalse(apple_identity.configured())

    def test_the_client_secret_is_an_es256_assertion_apple_would_accept(self):
        secret = apple_identity.client_secret()
        header = jwt.get_unverified_header(secret)
        self.assertEqual(header["alg"], "ES256")
        self.assertEqual(header["kid"], "ABC1234567")
        claims = jwt.decode(
            secret,
            self.key.public_key(),
            algorithms=["ES256"],
            audience=apple_identity.ISSUER,
        )
        self.assertEqual(claims["iss"], "87ZC69AGSR")
        self.assertEqual(claims["sub"], "com.pulsesoc.web")
        self.assertLessEqual(claims["exp"] - claims["iat"], 15777000)

    def test_the_client_secret_is_short_lived(self):
        claims = jwt.decode(
            apple_identity.client_secret(),
            self.key.public_key(),
            algorithms=["ES256"],
            audience=apple_identity.ISSUER,
        )
        self.assertLessEqual(claims["exp"] - claims["iat"], 3600)

    def test_both_the_web_and_the_native_client_are_accepted_audiences(self):
        """One Primary App ID, two clients, one identity domain."""

        self.assertEqual(
            set(apple_identity.audiences()), {"com.pulsesoc.web", "com.pulsesoc.app"}
        )

    def test_the_config_report_never_carries_key_material(self):
        report = json.dumps(apple_identity.config_report())
        self.assertNotIn("BEGIN PRIVATE KEY", report)
        self.assertNotIn(self.p8.splitlines()[1], report)
        self.assertTrue(apple_identity.config_report()["private_key_present"])

    def test_a_malformed_key_does_not_echo_itself_in_the_error(self):
        os.environ["APPLE_SIGNIN_PRIVATE_KEY"] = (
            "-----BEGIN PRIVATE KEY-----\nSUPERSECRETGARBAGE\n-----END PRIVATE KEY-----"
        )
        with self.assertRaises(apple_identity.AppleIdentityError) as caught:
            apple_identity.client_secret()
        self.assertNotIn("SUPERSECRETGARBAGE", str(caught.exception))

    def test_an_escaped_newline_key_is_accepted(self):
        os.environ["APPLE_SIGNIN_PRIVATE_KEY"] = self.p8.replace("\n", "\\n")
        self.assertTrue(apple_identity.configured())
        self.assertTrue(apple_identity.client_secret())

    def test_the_authorization_request_uses_form_post_and_carries_state_and_nonce(self):
        url = apple_identity.authorization_url(
            state="the-state", nonce="the-nonce", redirect_uri="https://pulsesoc.com/auth/apple/callback"
        )
        self.assertIn("response_mode=form_post", url)
        self.assertIn("client_id=com.pulsesoc.web", url)
        self.assertIn("state=the-state", url)
        self.assertIn("nonce=the-nonce", url)
        self.assertIn("response_type=code", url)

    def test_apple_is_asked_for_nothing_beyond_name_and_email(self):
        self.assertEqual(set(apple_identity.SCOPE.split()), {"name", "email"})

    def test_the_display_name_arrives_outside_the_token_and_is_not_identity(self):
        profile = apple_identity.profile_from_response(
            {"sub": "s-1", "email": "signed@example.test", "email_verified": "true"},
            json.dumps({"name": {"firstName": "Ada", "lastName": "Lovelace"}}),
        )
        self.assertEqual(profile["display_name"], "Ada Lovelace")
        self.assertEqual(profile["subject"], "s-1")
        self.assertEqual(profile["email"], "signed@example.test", "email comes from the token")
        self.assertTrue(profile["email_verified"])

    def test_an_unparseable_user_field_costs_a_name_and_not_a_sign_in(self):
        profile = apple_identity.profile_from_response({"sub": "s-2", "email": "a@b.test"}, "{{{")
        self.assertEqual(profile["display_name"], "")
        self.assertEqual(profile["subject"], "s-2")

    def test_a_returning_member_sends_no_user_field_at_all(self):
        profile = apple_identity.profile_from_response({"sub": "s-3"}, "")
        self.assertEqual(profile["subject"], "s-3")
        self.assertEqual(profile["display_name"], "")
        self.assertEqual(profile["email"], "")


class GoogleAdapterTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (
            "GOOGLE_SIGNIN_CLIENT_ID", "GOOGLE_SIGNIN_NATIVE_CLIENT_IDS", "GOOGLE_SIGNIN_ENABLED",
        )}
        os.environ["GOOGLE_SIGNIN_CLIENT_ID"] = "123-web.apps.googleusercontent.com"
        os.environ.pop("GOOGLE_SIGNIN_NATIVE_CLIENT_IDS", None)
        os.environ.pop("GOOGLE_SIGNIN_ENABLED", None)

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_a_client_id_is_the_whole_configuration(self):
        self.assertTrue(google_identity.configured())
        self.assertFalse(google_identity.config_report()["uses_client_secret"])
        self.assertFalse(google_identity.config_report()["requests_access_token"])

    def test_scopes_are_identity_only(self):
        self.assertEqual(set(google_identity.SCOPES), {"openid", "email", "profile"})

    def test_no_scope_reaches_for_mail_drive_calendar_or_offline_access(self):
        joined = " ".join(google_identity.SCOPES)
        for forbidden in ("gmail", "drive", "calendar", "contacts", "photos", "youtube", "offline"):
            self.assertNotIn(forbidden, joined)

    def test_both_spellings_of_googles_issuer_are_accepted(self):
        self.assertEqual(
            google_identity.ISSUERS,
            frozenset({"https://accounts.google.com", "accounts.google.com"}),
        )

    def test_the_double_submit_csrf_check_matches_only_an_exact_pair(self):
        self.assertTrue(google_identity.verify_csrf_token("abc", "abc"))
        self.assertFalse(google_identity.verify_csrf_token("abc", "abd"))

    def test_a_missing_csrf_token_is_a_failure_and_not_a_waiver(self):
        self.assertFalse(google_identity.verify_csrf_token("", ""))
        self.assertFalse(google_identity.verify_csrf_token("abc", ""))
        self.assertFalse(google_identity.verify_csrf_token("", "abc"))

    def test_an_unverified_workspace_address_is_carried_through_as_unverified(self):
        profile = google_identity.profile_from_claims(
            {"sub": "g-1", "email": "person@corp.test", "email_verified": False, "hd": "corp.test"}
        )
        self.assertFalse(profile["email_verified"])
        self.assertEqual(profile["hosted_domain"], "corp.test")

    def test_the_nonce_is_readable_before_verification_only_as_a_lookup_key(self):
        token = jwt.encode({"nonce": "handshake-7", "sub": "g"}, KEYS.pem, algorithm="RS256")
        self.assertEqual(google_identity.unverified_nonce(token), "handshake-7")
        self.assertEqual(google_identity.unverified_nonce("garbage"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
