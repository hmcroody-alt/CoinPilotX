"""Sign in with Google — the provider adapter. Provider #2.

A sibling of `services.apple_identity`, not a second identity system: the table,
the resolution ladder and the token verifier are all shared, and this file holds
only what is Google-specific. If a third provider ever arrives it belongs beside
these two.

**This uses the Google Identity Services credential flow, not an authorization
code exchange.** That is a decision rather than a default, so here is the
reasoning:

PulseSoc needs exactly one thing from Google — a trustworthy assertion of *which
Google account this is*. It does not read the member's mail, calendar, contacts,
Drive or photos, and it never acts on their behalf while they are away. An
authorization-code flow exists to obtain **access tokens** for calling Google
APIs, plus a refresh token to keep doing so offline. Choosing it here would mean:

* a **client secret** to store, rotate and leak,
* a redirect URI to register and keep exact,
* access and refresh tokens arriving that we have no use for, and must then be
  careful to never persist — a credential whose safest handling is "do not keep
  it" is a liability with no upside.

The credential flow returns a signed **ID token and nothing else**. There is no
client secret in this module because Google does not need one to verify a
signature against its published keys, and there is no access token to mishandle
because none is issued. The least powerful credential that answers the question
is the right one.

Scope is therefore ``openid email profile`` and cannot widen: there is no
request here for Gmail, Drive, Calendar, Contacts, Photos, YouTube, or offline
access, which is also what keeps this client out of Google's restricted-scope
verification regime.

**CSRF.** Google posts the credential cross-site and documents a double-submit
cookie, ``g_csrf_token``, for it. :func:`verify_csrf_token` checks it — but it is
treated as a *second* defence, not the primary one, because its arrival depends
on cookie policy we do not control. The primary defence is the nonce: it is
minted server-side into a `oauth_login_state` handshake, travels inside the
token Google signs, and is spent once. See
`oauth_login_state.consume_by_nonce`.
"""

from __future__ import annotations

import hmac
import os

from services import oidc_tokens

#: Google publishes its issuer both ways and both are legitimate. A verifier
#: that hardcodes one of them rejects real tokens roughly at random.
ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

#: Everything PulseSoc asks for. `openid` and `email` identify the member;
#: `profile` supplies a display name and avatar at signup. Nothing else — see
#: the module docstring.
SCOPES = ("openid", "email", "profile")

#: Google's double-submit cookie and form field for the credential POST.
CSRF_COOKIE = "g_csrf_token"
CSRF_FIELD = "g_csrf_token"

#: The form field carrying the ID token.
CREDENTIAL_FIELD = "credential"


class GoogleIdentityError(Exception):
    """Google could not be used to sign in. `reason` is for the audit log."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def client_id() -> str:
    """The web client ID. Public by construction — it ships in the page.

    Worth being explicit about because it looks like a secret and is routinely
    treated as one: a Google client ID is an identifier, not a credential. It is
    embedded in the HTML that renders the button. What makes a token acceptable
    is the signature and the audience check, not the secrecy of this string.
    """

    return _env("GOOGLE_SIGNIN_CLIENT_ID")


def native_client_ids() -> tuple:
    """iOS/Android client ids whose tokens are also accepted, if ever issued.

    Google issues a *separate* client ID per platform, and unlike Apple the
    ``sub`` is stable across them for the same Google account — so a native
    sign-in resolves to the same member without any portal-side grouping. Only
    the audience differs, which is why this exists.
    """

    raw = _env("GOOGLE_SIGNIN_NATIVE_CLIENT_IDS")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def audiences() -> tuple:
    found = []
    for candidate in (client_id(),) + native_client_ids():
        if candidate and candidate not in found:
            found.append(candidate)
    return tuple(found)


def configured() -> bool:
    """A client ID is the whole configuration. No secret, by design."""

    if _env("GOOGLE_SIGNIN_ENABLED").lower() in {"0", "false", "no", "off"}:
        return False
    return bool(client_id())


def config_report() -> dict:
    return {
        "provider": "google",
        "configured": configured(),
        "client_id": client_id(),
        "native_client_ids": list(native_client_ids()),
        "scopes": list(SCOPES),
        "uses_client_secret": False,
        "requests_access_token": False,
    }


def verify_csrf_token(cookie_value: str, form_value: str) -> bool:
    """Google's documented double-submit check.

    Returns False when either side is missing. A caller must not read that as
    "no CSRF token was required" — absence is a failure of this check, and the
    route treats it as one.
    """

    if not cookie_value or not form_value:
        return False
    return hmac.compare_digest(str(cookie_value), str(form_value))


def verify_credential(credential: str, *, nonce: str = "") -> dict:
    """Validated claims from a GIS `credential`, or :class:`GoogleIdentityError`."""

    accepted = audiences()
    if not accepted:
        raise GoogleIdentityError("google_not_configured")
    try:
        return oidc_tokens.verify_id_token(
            credential,
            jwks_url=JWKS_URL,
            issuers=ISSUERS,
            audiences=accepted,
            nonce=nonce,
            algorithms=("RS256",),
        )
    except oidc_tokens.TokenError as exc:
        raise GoogleIdentityError(f"google_{exc.reason}", exc.detail) from exc


def unverified_nonce(credential: str) -> str:
    """The nonce claim without checking anything. For finding the handshake only.

    A chicken-and-egg step: the handshake holds the nonce to verify the token
    against, and the token holds the nonce to find the handshake by. So the
    claim is read unverified *once*, used only as a database lookup key, and
    then :func:`verify_credential` re-checks it against the row with the
    signature enforced. Nothing is trusted on the strength of this read — an
    attacker controlling it can select which handshake to attack, which they
    could do with a `state` parameter anyway, and still cannot pass the
    signature or the browser-binding check.
    """

    import jwt

    try:
        claims = jwt.decode(credential, options={"verify_signature": False})
    except Exception:
        return ""
    return str(claims.get("nonce") or "")


def profile_from_claims(claims: dict) -> dict:
    """The identity facts from a verified Google ID token.

    ``email_verified`` is carried through rather than assumed. For a consumer
    ``@gmail.com`` account it is always true, but a Workspace domain can assert
    an address the domain administrator never confirmed, and a federated sign-in
    is not entitled to more trust than the provider itself claims.
    """

    verified = claims.get("email_verified")
    if not isinstance(verified, bool):
        verified = str(verified or "").strip().lower() in {"true", "1", "yes"}

    return {
        "provider": "google",
        "subject": str(claims.get("sub") or "").strip(),
        "email": str(claims.get("email") or "").strip().lower(),
        "email_verified": bool(verified),
        "is_private_email": False,
        "display_name": str(claims.get("name") or "").strip(),
        "picture": str(claims.get("picture") or "").strip(),
        "hosted_domain": str(claims.get("hd") or "").strip(),
    }
