"""What `verify_id_token` actually accepts, measured with real signatures.

Every other federated suite in this repository stubs the verifier. That is the
right call there -- those tests are about the resolve ladder, the account gates
and the route's answers -- but it means the cryptographic floor underneath all
of them has no test of its own. `services/oidc_tokens.py` is the single place
where a forged, expired, misdirected or re-presented provider token is supposed
to be turned away, and a stub cannot tell us whether it does.

So nothing here is stubbed except the network. A real 2048-bit RSA key is
generated once, its public half is published through the same `_fetch_jwks` path
the live code uses, and tokens are signed for real. A refusal below is a
refusal by the actual signature check.

Two of these tests assert that something is *allowed* through, and they matter
more than they look:

  * `test_a_correctly_signed_token_verifies` is the control. Without it, every
    refusal in this file could be passing because the harness is broken rather
    than because the verifier works -- a JWKS that never loads would refuse
    everything and look like flawless security.
  * `test_the_same_token_verifies_a_second_time` pins the gap that
    `services/federated_replay.py` exists to close. It is not a bug in this
    module: a signature, an issuer, an audience and an expiry are all still
    valid on the hundredth presentation, and no amount of care here can change
    that. Recording it as a passing test is how the reason for the replay ledger
    stays visible if someone later wonders why the ledger is there.

The nonce tests are characterisation, not approval. `if nonce:` means an absent
expected nonce skips the comparison rather than failing it, and
`test_an_absent_expected_nonce_skips_the_check_entirely` says so out loud so the
weakness is a documented property rather than a surprise.
"""

from __future__ import annotations

import json
import time
import unittest

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import oidc_tokens  # noqa: E402

JWKS_URL = "https://provider.test/certs"
ISSUER = "https://accounts.provider.test"
AUDIENCE = "audience-under-test"
KID = "key-one"
OTHER_KID = "key-two"

# Generated once: an RSA keypair costs real time and none of these tests need a
# distinct one. The second key exists only so "signed by the wrong key" can be
# expressed as a genuinely valid signature over the same bytes.
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_IMPOSTOR = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key, kid):
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return public


class _Published:
    """The provider's JWKS, served through the real `_fetch_jwks`.

    `requests.get` is replaced rather than `_fetch_jwks` itself, so the document
    parsing, the kid indexing and the cache all run as they do in production.
    """

    def __init__(self, *keys):
        self._document = {"keys": [_jwk(key, kid) for key, kid in keys]}
        self._real = None

    def __enter__(self):
        self._real = oidc_tokens.requests.get
        document = self._document

        class _Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return document

        oidc_tokens.requests.get = lambda url, timeout=None: _Response()
        oidc_tokens.reset_cache()
        return self

    def __exit__(self, *exc):
        oidc_tokens.requests.get = self._real
        oidc_tokens.reset_cache()
        return False


def _token(
    *,
    key=_KEY,
    kid=KID,
    algorithm="RS256",
    issuer=ISSUER,
    audience=AUDIENCE,
    subject="provider-subject",
    expires_in=600,
    issued_ago=0,
    headers=None,
    **extra,
):
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": now - issued_ago,
    }
    if expires_in is not None:
        claims["exp"] = now + expires_in
    claims.update(extra)
    for empty in [name for name, value in list(claims.items()) if value is None]:
        del claims[empty]
    header = {"kid": kid} if kid else {}
    header.update(headers or {})
    return jwt.encode(claims, key, algorithm=algorithm, headers=header)


def _verify(token, **overrides):
    arguments = {
        "jwks_url": JWKS_URL,
        "issuers": {ISSUER},
        "audiences": {AUDIENCE},
    }
    arguments.update(overrides)
    return oidc_tokens.verify_id_token(token, **arguments)


class VerificationCase(unittest.TestCase):
    def setUp(self):
        self._published = _Published((_KEY, KID))
        self._published.__enter__()
        self.addCleanup(self._published.__exit__)

    def assertRefused(self, reason, token, **overrides):
        with self.assertRaises(oidc_tokens.TokenError) as caught:
            _verify(token, **overrides)
        self.assertEqual(
            caught.exception.reason, reason,
            f"expected {reason!r}, got {caught.exception.reason!r}",
        )
        return caught.exception


class AGenuineTokenIsAccepted(VerificationCase):
    def test_a_correctly_signed_token_verifies(self):
        """The control. Every refusal below is only meaningful next to this."""

        claims = _verify(_token())
        self.assertEqual(claims["sub"], "provider-subject")
        self.assertEqual(claims["aud"], AUDIENCE)

    def test_either_spelling_of_a_providers_issuer_is_accepted(self):
        """Google publishes `accounts.google.com` and the https form, both real."""

        bare = _token(issuer="accounts.provider.test")
        claims = _verify(bare, issuers={ISSUER, "accounts.provider.test"})
        self.assertEqual(claims["iss"], "accounts.provider.test")

    def test_one_of_several_configured_audiences_is_enough(self):
        """A deployment legitimately accepts its web ID and its bundle ID."""

        claims = _verify(_token(audience="native-audience"),
                         audiences={AUDIENCE, "native-audience"})
        self.assertEqual(claims["aud"], "native-audience")

    def test_a_token_issued_slightly_in_the_future_is_tolerated(self):
        """Clock skew between a provider and this server is normal and benign."""

        self.assertTrue(_verify(_token(issued_ago=-30))["sub"])


class TheSignatureIsTheWholeBasisOfTrust(VerificationCase):
    def test_a_token_signed_by_another_key_is_refused(self):
        """A valid signature over the same claims, by a key the provider never
        published. This is forgery, and it is the case the JWKS exists for."""

        forged = _token(key=_IMPOSTOR)
        self.assertRefused("bad_signature", forged)

    def test_a_tampered_payload_is_refused(self):
        """Editing a claim after signing: the attack the signature prevents."""

        header, payload, signature = _token().split(".")
        import base64

        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(raw)
        claims["sub"] = "somebody-else"
        swapped = base64.urlsafe_b64encode(
            json.dumps(claims).encode("utf-8")
        ).decode("ascii").rstrip("=")
        self.assertRefused("bad_signature", f"{header}.{swapped}.{signature}")

    def test_an_hmac_token_signed_with_the_public_key_is_refused(self):
        """The classic JWKS confusion attack, and the reason HMAC is excluded.

        A JWKS is public. If `HS256` were accepted, anyone could sign a token
        with the published key material as the shared secret and the server
        would verify it happily. Refused on the algorithm, before any key is
        even fetched.
        """

        # Assembled by hand: PyJWT refuses to *encode* this, which is a useful
        # guard for honest callers but means the attack has to be built the way
        # an attacker would build it -- with an HMAC over the published key.
        import base64
        import hmac as hmac_module
        import hashlib

        from cryptography.hazmat.primitives import serialization

        public_pem = _KEY.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        def segment(payload):
            return base64.urlsafe_b64encode(
                json.dumps(payload).encode("utf-8")
            ).decode("ascii").rstrip("=")

        signing_input = ".".join([
            segment({"alg": "HS256", "typ": "JWT", "kid": KID}),
            segment({"iss": ISSUER, "aud": AUDIENCE, "sub": "forged",
                     "iat": int(time.time()), "exp": int(time.time()) + 600}),
        ]).encode("ascii")
        signature = base64.urlsafe_b64encode(
            hmac_module.new(public_pem, signing_input, hashlib.sha256).digest()
        ).decode("ascii").rstrip("=")
        hmac_token = f"{signing_input.decode('ascii')}.{signature}"

        # Refused on the algorithm, before any key is fetched -- so the
        # published key material is never even offered as a secret.
        self.assertRefused("unacceptable_algorithm", hmac_token)

    def test_an_unsigned_token_is_refused(self):
        """`alg: none` -- refused by the same closed set, with no special case."""

        unsigned = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "forged",
             "iat": int(time.time()), "exp": int(time.time()) + 600},
            key="",
            algorithm="none",
            headers={"kid": KID},
        )
        self.assertRefused("unacceptable_algorithm", unsigned)

    def test_a_token_naming_no_key_is_refused(self):
        self.assertRefused("missing_key_id", _token(kid=None))

    def test_a_token_naming_an_unpublished_key_is_refused(self):
        self.assertRefused("unknown_signing_key", _token(kid=OTHER_KID))

    def test_a_rotated_key_is_picked_up_without_a_restart(self):
        """An unknown `kid` is how rotation announces itself; there is no push.

        The refusal above must be "not in the published set", not "not in the
        set I happened to cache first", or every rotation would be an outage.
        """

        self._published.__exit__()
        with _Published((_KEY, KID)):
            self.assertRefused("unknown_signing_key", _token(kid=OTHER_KID))
            with _Published((_KEY, KID), (_IMPOSTOR, OTHER_KID)):
                claims = _verify(_token(key=_IMPOSTOR, kid=OTHER_KID))
                self.assertEqual(claims["sub"], "provider-subject")


class ExpiryAudienceAndIssuerAreAllClosed(VerificationCase):
    def test_an_expired_token_is_refused(self):
        """Past the 60s leeway, so this is expiry and not skew tolerance."""

        self.assertRefused("expired_token", _token(expires_in=-300))

    def test_a_token_expiring_inside_the_leeway_is_still_accepted(self):
        """Pins the leeway as deliberate. Without this, someone tightening
        `leeway_seconds` to zero would pass every test and start failing real
        sign-ins on ordinary clock drift."""

        self.assertTrue(_verify(_token(expires_in=-30))["sub"])

    def test_a_token_with_no_expiry_is_refused(self):
        """A token that never expires is a permanent credential."""

        self.assertRefused("missing_claim", _token(expires_in=None))

    def test_a_token_minted_for_another_audience_is_refused(self):
        """Another app's token, genuinely signed by the same provider. Without
        this check any Google client's token would sign in here."""

        self.assertRefused("wrong_audience", _token(audience="someone-elses-app"))

    def test_a_token_from_another_issuer_is_refused(self):
        self.assertRefused("wrong_issuer", _token(issuer="https://evil.test"))

    def test_an_empty_audience_set_refuses_rather_than_meaning_any(self):
        """"Nothing configured" must never read as "everything accepted"."""

        self.assertRefused("no_audience_configured", _token(), audiences=set())

    def test_an_empty_issuer_set_refuses_rather_than_meaning_any(self):
        self.assertRefused("no_issuer_configured", _token(), issuers=set())

    def test_an_hmac_only_algorithm_list_refuses_rather_than_downgrading(self):
        self.assertRefused("no_acceptable_algorithm", _token(), algorithms=("HS256",))


class TheSubjectMustExist(VerificationCase):
    def test_a_token_with_no_subject_is_refused(self):
        self.assertRefused("missing_claim", _token(subject=None, sub=None))

    def test_a_blank_subject_is_refused(self):
        """An empty `sub` would become one identity row that every later
        subjectless sign-in resolves onto -- a shared account, not an identity."""

        self.assertRefused("missing_subject", _token(subject="   "))


class MalformedInputIsRefusedNotGuessed(VerificationCase):
    def test_an_empty_token_is_refused(self):
        self.assertRefused("missing_token", "")

    def test_a_token_that_is_not_a_jwt_at_all_is_refused(self):
        self.assertRefused("malformed_token", "this is not a token")

    def test_a_truncated_token_is_refused(self):
        self.assertRefused("malformed_token", _token().split(".")[0])

    def test_a_token_whose_header_is_not_json_is_refused(self):
        self.assertRefused("malformed_token", "not-base64.not-base64.not-base64")


class TheNonceCannotCarryTheReplayDefence(VerificationCase):
    """Characterisation. These record what the nonce does and does not do, which
    is why `services/federated_replay.py` exists instead of a mandatory nonce."""

    def test_a_matching_nonce_passes(self):
        claims = _verify(_token(nonce="server-value"), nonce="server-value")
        self.assertEqual(claims["nonce"], "server-value")

    def test_a_mismatched_nonce_is_refused(self):
        self.assertRefused("wrong_nonce", _token(nonce="something-else"),
                           nonce="server-value")

    def test_a_token_carrying_no_nonce_is_refused_when_one_is_expected(self):
        self.assertRefused("missing_nonce", _token(), nonce="server-value")

    def test_an_absent_expected_nonce_skips_the_check_entirely(self):
        """The hole, stated as a test rather than left as a reading of `if nonce:`.

        A caller that passes no nonce gets no nonce check -- not a failure, a
        skip. So "the nonce protects us" is only true of callers that both mint
        one and pass it back, and `@react-native-google-signin` v16.1.5 cannot
        put one in the token at all. This is the reason the replay defence had
        to move to the server's own memory.
        """

        self.assertTrue(_verify(_token())["sub"])
        self.assertTrue(_verify(_token(nonce="client-invented"))["sub"])


class VerificationAloneCannotStopAReplay(VerificationCase):
    """The measurement the replay ledger was built from."""

    def test_the_same_token_verifies_a_second_time(self):
        """Identical bytes, accepted twice. Nothing in this module is wrong --
        the signature, issuer, audience and expiry are all still valid. That is
        exactly why single-use has to be enforced somewhere that remembers."""

        token = _token()
        self.assertEqual(_verify(token)["sub"], "provider-subject")
        self.assertEqual(_verify(token)["sub"], "provider-subject")

    def test_it_keeps_verifying_for_as_long_as_the_token_lives(self):
        token = _token(expires_in=3600)
        for _ in range(5):
            self.assertTrue(_verify(token)["sub"])

    def test_the_ledger_keys_on_the_bytes_this_module_verified(self):
        """The division of labour, stated: this module answers "is it genuine?",
        the ledger answers "have I spent it?" -- over the very same bytes."""

        from services import federated_replay

        token = _token()
        self.assertTrue(_verify(token)["sub"])
        self.assertEqual(federated_replay.digest(token),
                         federated_replay.digest(token))
        self.assertNotEqual(
            federated_replay.digest(token),
            federated_replay.digest(_token(subject="a-different-member")),
            "distinct credentials must not collide, or one member's sign-in "
            "would consume another's",
        )

    def test_identical_claims_in_the_same_second_are_literally_one_credential(self):
        """A boundary worth recording rather than discovering later.

        RSA PKCS#1 v1.5 signing is deterministic and `iat`/`exp` have
        one-second resolution, so two tokens minted inside the same second from
        otherwise identical claims are not merely similar -- they are the same
        bytes. The ledger would treat the second as a replay, because by its
        definition it *is* one.

        This is not reachable with a real provider token, which carries
        per-issuance claims the two sides of a comparison cannot both control
        (Apple binds the client `nonce`; Google's ID token carries `at_hash`
        over a freshly issued access token). But the direction of the failure is
        what makes it safe to leave: an over-refusal costs a member one extra
        tap on a credential that will differ a second later, while the opposite
        error would hand a replayer a session. Erring toward refusal is the
        whole posture of this module.
        """

        from services import federated_replay

        first = _token(expires_in=600)
        second = _token(expires_in=600)
        if first != second:  # pragma: no cover - a second boundary was crossed
            self.skipTest("the clock ticked between the two mints")
        self.assertEqual(federated_replay.digest(first),
                         federated_replay.digest(second))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
