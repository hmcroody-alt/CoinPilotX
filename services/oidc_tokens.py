"""Verifying an OpenID Connect ID token, for any provider that issues one.

Apple and Google both answer a sign-in with a signed JWT asserting *who* the
member is. Nothing else in this repository has ever had to verify a token it did
not sign itself, so this is the first place where "the signature checks out" is
not the same question as "this token was meant for us".

Four separate things have to hold, and dropping any one of them is a full
authentication bypass rather than a degradation:

* **The signature**, against the provider's *published* key — fetched over TLS
  from the provider, never a key supplied by the token.
* **The issuer**, so a token minted by some other OIDC provider the caller also
  happens to trust cannot be replayed here.
* **The audience**, so a token Apple issued for a *different* Apple relying
  party cannot be replayed at PulseSoc. This is the one that is easy to skip
  because tokens still "verify" without it.
* **The expiry**, with only a small clock-skew allowance.

Two algorithm rules exist because they are the classic JWT forgeries rather than
because any provider does this:

* ``alg: none`` is never accepted. PyJWT refuses it when an algorithm list is
  passed, and one is always passed here.
* HMAC algorithms are never accepted. A JWKS is *public*. If ``HS256`` were
  allowed, that public modulus becomes a shared secret anybody can read, and
  anybody can then mint a token for any ``sub`` they like. So the allowlist is
  checked against asymmetric families before PyJWT ever sees it, and a token
  whose header asks for anything else is refused without a key lookup.

The JWKS cache is not an optimisation. Fetching per sign-in would put a remote
HTTP call on the critical path of every login and hand the provider a way to
take authentication down by rate-limiting us. But a cache cannot be allowed to
outlive key rotation either: when a token arrives with a ``kid`` that is not in
the cache, that is exactly the signal that the provider rotated, so the cache
refetches once — floored by `_JWKS_MIN_REFETCH_SECONDS` so a stream of garbage
``kid``s cannot be turned into a request amplifier against the provider.
"""

from __future__ import annotations

import logging
import threading
import time

import jwt
import requests

#: Signature families whose verification key is *public*. Anything outside this
#: set is refused before a key is looked up — see the module docstring on why
#: allowing an HMAC family against a published JWKS is a total bypass.
ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"})

#: How long a successfully fetched key set is trusted without re-asking.
_JWKS_TTL_SECONDS = 600

#: Floor between two fetches of the same key set. An unknown `kid` triggers a
#: refetch, so without this an attacker could replay junk tokens to make us
#: hammer the provider on their behalf.
_JWKS_MIN_REFETCH_SECONDS = 30

_JWKS_TIMEOUT_SECONDS = 5

_lock = threading.Lock()
#: url -> {"keys": {kid: jwk_dict}, "fetched_at": float, "last_attempt": float}
_cache: dict[str, dict] = {}


class TokenError(Exception):
    """An ID token was not acceptable. Carries a stable reason, never the token.

    The reason is for our own audit log and for tests. It is deliberately not
    phrased for an end user and must not be echoed to one: the difference
    between "signed by the wrong key" and "issued to a different audience" is
    useful to somebody probing the endpoint and useless to a member.
    """

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def reset_cache() -> None:
    """Forget every cached key set. For tests, and for a key-rotation drill."""

    with _lock:
        _cache.clear()


def _fetch_jwks(url: str) -> dict:
    response = requests.get(url, timeout=_JWKS_TIMEOUT_SECONDS)
    response.raise_for_status()
    document = response.json()
    keys = {}
    for key in document.get("keys") or []:
        kid = str(key.get("kid") or "")
        if kid:
            keys[kid] = key
    if not keys:
        raise TokenError("jwks_empty", url)
    return keys


def signing_key(jwks_url: str, kid: str):
    """The provider's public key for `kid`, from cache when it is still trusted.

    Refetches when the cache is stale *or* when `kid` is absent from it, because
    an absent `kid` is how key rotation announces itself — there is no push.
    """

    now = time.monotonic()
    with _lock:
        entry = _cache.get(jwks_url)
        if entry:
            fresh = (now - entry["fetched_at"]) < _JWKS_TTL_SECONDS
            if fresh and kid in entry["keys"]:
                return jwt.PyJWK(entry["keys"][kid]).key
            if (now - entry["last_attempt"]) < _JWKS_MIN_REFETCH_SECONDS:
                # Too soon to ask again. Serve a known `kid` from the stale set
                # rather than failing a login over an expired clock; refuse an
                # unknown one, which is the only case that needs a live answer.
                if kid in entry["keys"]:
                    return jwt.PyJWK(entry["keys"][kid]).key
                raise TokenError("unknown_signing_key", kid)
            entry["last_attempt"] = now
        else:
            _cache[jwks_url] = {"keys": {}, "fetched_at": 0.0, "last_attempt": now}

    try:
        keys = _fetch_jwks(jwks_url)
    except TokenError:
        raise
    except Exception as exc:
        logging.warning("OIDC_JWKS_FETCH_FAILED url=%s error=%s", jwks_url, exc)
        with _lock:
            entry = _cache.get(jwks_url) or {}
            # A provider outage must not log everyone out. If we still hold a
            # key set, keep using it; it is stale, not wrong.
            if entry.get("keys", {}).get(kid):
                return jwt.PyJWK(entry["keys"][kid]).key
        raise TokenError("jwks_unavailable", str(exc)) from exc

    with _lock:
        _cache[jwks_url] = {"keys": keys, "fetched_at": time.monotonic(), "last_attempt": time.monotonic()}

    if kid not in keys:
        raise TokenError("unknown_signing_key", kid)
    return jwt.PyJWK(keys[kid]).key


def verify_id_token(
    token: str,
    *,
    jwks_url: str,
    issuers,
    audiences,
    nonce: str = "",
    algorithms=("RS256",),
    leeway_seconds: int = 60,
) -> dict:
    """Return the claims of `token`, or raise :class:`TokenError`.

    `issuers` and `audiences` are *sets*: Google publishes its issuer two ways
    (`accounts.google.com` and `https://accounts.google.com`) and both are
    legitimate, and one deployment can legitimately accept tokens for both its
    web Services ID and its native bundle ID. Both are still closed sets — an
    empty one raises rather than meaning "any", because "no audience configured"
    must never read as "every audience accepted".

    `nonce` is compared only when the caller passes one. That is a skip, not a
    failure, so it cannot be the replay defence -- and it could not be even if
    it were mandatory, because the value is chosen by the *client*, so a holder
    of a stolen token reads its own nonce claim and presents it back. Both sides
    would be attacker-controlled.

    Which is why the callers differ, deliberately. Apple's sheet binds a nonce
    into the token (`expo-apple-authentication` exposes `nonce?: string`), so
    the Apple adapter passes it and it earns its place catching an SDK or
    configuration mismatch. `@react-native-google-signin` v16.1.5 has no nonce
    field at all, so the native Google adapter passes none -- expecting one
    there could only ever fail every sign-in.

    Single-use enforcement lives in `services/federated_replay.py`, which keys
    on the server's own memory of the credential rather than on anything the
    client supplies. `tests/test_oidc_token_verification.py` pins both halves:
    that a valid token verifies indefinitely, and that an absent expected nonce
    skips the check.
    """

    allowed = {str(a) for a in algorithms if str(a) in ASYMMETRIC_ALGORITHMS}
    if not allowed:
        raise TokenError("no_acceptable_algorithm")
    issuer_set = {str(i) for i in issuers if str(i)}
    audience_set = {str(a) for a in audiences if str(a)}
    if not issuer_set:
        raise TokenError("no_issuer_configured")
    if not audience_set:
        raise TokenError("no_audience_configured")
    if not token:
        raise TokenError("missing_token")

    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:
        raise TokenError("malformed_token", str(exc)) from exc

    algorithm = str(header.get("alg") or "")
    if algorithm not in allowed:
        # Covers `none` and every HMAC family without a special case.
        raise TokenError("unacceptable_algorithm", algorithm)
    kid = str(header.get("kid") or "")
    if not kid:
        raise TokenError("missing_key_id")

    key = signing_key(jwks_url, kid)

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            audience=sorted(audience_set),
            leeway=leeway_seconds,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_aud": True,
                "require": ["iss", "sub", "aud", "exp", "iat"],
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("expired_token", str(exc)) from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenError("wrong_audience", str(exc)) from exc
    except jwt.InvalidSignatureError as exc:
        raise TokenError("bad_signature", str(exc)) from exc
    except jwt.MissingRequiredClaimError as exc:
        raise TokenError("missing_claim", str(exc)) from exc
    except Exception as exc:
        raise TokenError("invalid_token", str(exc)) from exc

    # Checked here rather than handed to PyJWT's `issuer=`, which takes a single
    # string and would force one of Google's two legitimate spellings to fail.
    if str(claims.get("iss") or "") not in issuer_set:
        raise TokenError("wrong_issuer", str(claims.get("iss") or ""))

    subject = str(claims.get("sub") or "").strip()
    if not subject:
        # An empty `sub` would otherwise become a *shared* identity row that
        # every future tokenless sign-in resolves onto.
        raise TokenError("missing_subject")

    if nonce:
        presented = str(claims.get("nonce") or "")
        if not presented:
            raise TokenError("missing_nonce")
        if not _constant_time_equals(presented, nonce):
            raise TokenError("wrong_nonce")

    return claims


def _constant_time_equals(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
