"""Sign in with Apple — the provider adapter. Provider #1.

Everything Apple-specific lives here; the identity consequences live in
`services.external_identity` and the token checking in `services.oidc_tokens`.
Adding Google meant adding a sibling to this file and touching neither of those.

Three things about Apple's web flow are unlike a textbook OAuth client, and each
one has cost somebody a weekend:

**The client secret is not a secret you are given.** Apple issues no client
secret string. You mint one: a short-lived ES256 JWT signed with a ``.p8``
elliptic-curve key, where ``iss`` is the Team ID, ``sub`` is the Services ID and
``aud`` is Apple. So the "secret" in the token exchange is generated per
request. The ``.p8`` itself is downloadable exactly once, at creation, and Apple
will not show it again — which is why this module reads it from the environment
and never writes it anywhere, not to a log line, not to an error message, and
not into the config report.

**The Services ID is the web client_id, and it is not the bundle ID.** The
native app authenticates as ``com.pulsesoc.app``; the website authenticates as a
separate Services ID. Both must sit under the same *Primary App ID* at Apple, or
the same human gets two different ``sub`` values — one per client group — and
"sign in with Apple on the web" silently creates a second account for somebody
who already has one. That grouping is a portal setting with no runtime signal,
and the only in-code defence is that :data:`AUDIENCES` accepts both client ids
so a token from either is recognised as the same identity domain.

**The response is a cross-site POST.** Asking for ``name`` or ``email`` scope
obliges ``response_mode=form_post``. See `services.oauth_login_state` for what
that does to cookies; it is the reason this flow has three routes rather than
two.

One more asymmetry worth stating because it drives
:func:`profile_from_response`: the member's *name*, and their real email when
they chose Hide My Email, arrive **once** — in a ``user`` form field on the very
first authorisation, not in the ID token, and never again. A later sign-in
returns only ``sub``. So the first authorisation is the only chance to record a
display name, and code that expects the name on every callback will look correct
in testing and be empty for every returning member.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

import jwt
import requests

from services import oidc_tokens

ISSUER = "https://appleid.apple.com"
AUTHORIZE_URL = "https://appleid.apple.com/auth/authorize"
TOKEN_URL = "https://appleid.apple.com/auth/token"
JWKS_URL = "https://appleid.apple.com/auth/keys"

#: What PulseSoc asks Apple for, and nothing else. ``name`` and ``email`` are
#: the two scopes Sign in with Apple offers; there is no wider grant to decline.
SCOPE = "name email"

#: Apple caps the client-secret JWT at six months. One hour, because it is
#: regenerated per exchange anyway — a long-lived assertion sitting in memory
#: buys nothing and widens what a heap dump is worth.
CLIENT_SECRET_TTL_SECONDS = 3600

_HTTP_TIMEOUT_SECONDS = 10


class AppleIdentityError(Exception):
    """Apple could not be used to sign in. `reason` is for the audit log."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def services_id() -> str:
    """The web ``client_id``. A Services ID, not the bundle ID — see docstring."""

    return _env("APPLE_SIGNIN_SERVICES_ID")


def team_id() -> str:
    """The Apple Team ID.

    Reuses ``PULSESOC_APPLE_TEAM_ID``, already in the environment for the
    apple-app-site-association file. It is the same team and a second variable
    could only ever disagree with the first.
    """

    return _env("APPLE_SIGNIN_TEAM_ID") or _env("PULSESOC_APPLE_TEAM_ID")


def key_id() -> str:
    return _env("APPLE_SIGNIN_KEY_ID")


def native_client_ids() -> tuple:
    """Bundle ids whose ID tokens are also accepted, for the iOS app's own flow."""

    raw = _env("APPLE_SIGNIN_NATIVE_CLIENT_IDS") or _env("PULSESOC_APPLE_ASSOCIATED_BUNDLE_IDS")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def audiences() -> tuple:
    """Every ``aud`` a legitimate PulseSoc Apple token can carry.

    Both client ids, because the website and the app are two clients of one
    Primary App ID. Still a closed set: an empty one makes
    `oidc_tokens.verify_id_token` refuse rather than accept anything.
    """

    found = []
    for candidate in (services_id(),) + native_client_ids():
        if candidate and candidate not in found:
            found.append(candidate)
    return tuple(found)


def _private_key() -> str:
    """The ``.p8`` contents, PEM.

    Accepts the literal newlines Railway stores and the ``\\n``-escaped form a
    shell or a JSON config would produce, because getting this wrong presents as
    an unreadable cryptography error rather than as "your key is escaped".
    """

    raw = str(os.getenv("APPLE_SIGNIN_PRIVATE_KEY", "") or "")
    if not raw.strip():
        return ""
    return raw.replace("\\n", "\n").strip()


def configured() -> bool:
    """Whether every piece needed to complete an exchange is present.

    Enablement is derived from configuration rather than announced by a flag, so
    there is no state where a flag says "on" and the exchange cannot run. The
    explicit variable is only ever a way to turn it *off*.
    """

    if _env("APPLE_SIGNIN_ENABLED").lower() in {"0", "false", "no", "off"}:
        return False
    return bool(services_id() and team_id() and key_id() and _private_key())


def config_report() -> dict:
    """Presence, never content. Safe to log and safe to render on an admin page.

    The private key is reported as a boolean and a length. Reporting a prefix or
    a fingerprint would be the beginning of leaking it, and nothing operational
    needs more than "is it there".
    """

    key = _private_key()
    return {
        "provider": "apple",
        "configured": configured(),
        "services_id": services_id(),
        "team_id": team_id(),
        "key_id": key_id(),
        "native_client_ids": list(native_client_ids()),
        "private_key_present": bool(key),
        "private_key_length": len(key),
        "private_key_pem_header": key.startswith("-----BEGIN") if key else False,
    }


def client_secret(*, now: float | None = None) -> str:
    """Mint the per-exchange client-secret assertion. ES256 over the ``.p8``."""

    key = _private_key()
    if not (key and team_id() and key_id() and services_id()):
        raise AppleIdentityError("apple_not_configured")
    issued = int(now if now is not None else time.time())
    try:
        return jwt.encode(
            {
                "iss": team_id(),
                "iat": issued,
                "exp": issued + CLIENT_SECRET_TTL_SECONDS,
                "aud": ISSUER,
                "sub": services_id(),
            },
            key,
            algorithm="ES256",
            headers={"kid": key_id(), "alg": "ES256"},
        )
    except AppleIdentityError:
        raise
    except Exception as exc:
        # Deliberately does not include `exc` in `detail`: a cryptography error
        # on a malformed PEM can echo part of the key material.
        logging.error("APPLE_CLIENT_SECRET_SIGN_FAILED type=%s", type(exc).__name__)
        raise AppleIdentityError("apple_client_secret_unavailable") from exc


def authorization_url(*, state: str, nonce: str, redirect_uri: str) -> str:
    """Where to send the browser to start a sign-in."""

    if not configured():
        raise AppleIdentityError("apple_not_configured")
    from urllib.parse import urlencode

    return AUTHORIZE_URL + "?" + urlencode(
        {
            "client_id": services_id(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            # Obligatory once `name`/`email` scope is requested, and the whole
            # reason the callback is a POST that no session cookie reaches.
            "response_mode": "form_post",
            "scope": SCOPE,
            "state": state,
            "nonce": nonce,
        }
    )


def exchange_code(code: str, *, redirect_uri: str) -> dict:
    """Trade the authorization code for Apple's token response.

    The code is single-use at Apple, so a replayed callback fails here even
    before the state store refuses it — two independent defences, which is the
    point.
    """

    if not code:
        raise AppleIdentityError("missing_code")
    try:
        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": services_id(),
                "client_secret": client_secret(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except AppleIdentityError:
        raise
    except Exception as exc:
        raise AppleIdentityError("apple_token_endpoint_unreachable", str(exc)) from exc

    if response.status_code != 200:
        # Apple's errors are a short machine code (`invalid_client`,
        # `invalid_grant`). Logged for operators; never shown to a member.
        detail = ""
        try:
            detail = str((response.json() or {}).get("error") or "")
        except Exception:
            detail = f"http_{response.status_code}"
        logging.warning("APPLE_TOKEN_EXCHANGE_REJECTED status=%s error=%s", response.status_code, detail)
        raise AppleIdentityError("apple_token_exchange_rejected", detail)

    try:
        payload = response.json() or {}
    except Exception as exc:
        raise AppleIdentityError("apple_token_response_unreadable", str(exc)) from exc
    if not payload.get("id_token"):
        raise AppleIdentityError("apple_token_response_missing_id_token")
    return payload


def verify_id_token(token: str, *, nonce: str = "") -> dict:
    """Validated claims, or :class:`AppleIdentityError`."""

    accepted = audiences()
    if not accepted:
        raise AppleIdentityError("apple_not_configured")
    try:
        return oidc_tokens.verify_id_token(
            token,
            jwks_url=JWKS_URL,
            issuers={ISSUER},
            audiences=accepted,
            nonce=nonce,
            algorithms=("RS256",),
        )
    except oidc_tokens.TokenError as exc:
        raise AppleIdentityError(f"apple_{exc.reason}", exc.detail) from exc


def _truthy_claim(value) -> bool:
    """Apple sends ``email_verified`` as a bool *or* the string ``"true"``."""

    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def profile_from_response(claims: dict, user_field: str = "") -> dict:
    """The identity facts, from the token plus Apple's first-authorisation extras.

    `user_field` is the raw ``user`` form value, which Apple includes **only on
    the first authorisation** and which carries the given/family name. It is
    attacker-controllable in the sense that it arrives in the POST body rather
    than inside the signed token — so it is used for the display name and for
    nothing that decides identity or access. The email is taken from the signed
    claims in preference to it for exactly that reason.
    """

    display_name = ""
    if user_field:
        try:
            parsed = json.loads(user_field) or {}
            name = parsed.get("name") or {}
            display_name = " ".join(
                part for part in (name.get("firstName"), name.get("lastName")) if part
            ).strip()
        except Exception:
            # A malformed `user` field costs a display name, not a sign-in.
            logging.info("APPLE_USER_FIELD_UNPARSEABLE")

    email = str(claims.get("email") or "").strip().lower()
    return {
        "provider": "apple",
        "subject": str(claims.get("sub") or "").strip(),
        "email": email,
        "email_verified": _truthy_claim(claims.get("email_verified")),
        "is_private_email": _truthy_claim(claims.get("is_private_email")),
        "display_name": display_name,
        "authenticated_at": datetime.now(timezone.utc).isoformat(),
    }
