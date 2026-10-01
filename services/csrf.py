"""One CSRF contract, for every client.

Before this module, six pieces of code answered "is this write CSRF-safe?" --
``bot.verify_csrf``, ``bot._business_os_ent_csrf_ok``,
``bot.pulse_ads_verify_write``, ``bot._subscription_action_write_allowed``,
``business_os_commerce_routes._csrf_ok``, and a sixth spelled inline in
``admin_business_os_reconcile``. They disagreed on four of nine request shapes.
Measured, not inferred -- each was driven through the same shapes inside a
request context (three of the six shown; the other three tracked one of these):

    request shape                    verify_csrf  _business_os_ent_csrf_ok  _csrf_ok
    form field, correct              ACCEPT       ACCEPT                    ACCEPT
    X-CSRF-Token, correct            refuse       ACCEPT                    ACCEPT
    X-CSRFToken, correct             refuse       refuse                    ACCEPT
    X-Csrf-Token lowercase, correct  refuse       ACCEPT                    ACCEPT
    header correct, form wrong       refuse       ACCEPT                    ACCEPT
    bearer, no token anywhere        refuse       refuse                    ACCEPT
    constant-time comparison         no           no                        yes

The row that matters for the web rebuild is the second. ``bot.verify_csrf()``
read ``request.form`` and nothing else, and it is the verifier behind 52 call
sites plus ``enforce_admin_form_csrf``, the before_request hook covering every
state-changing ``/admin`` and ``/api/admin`` request. A ``fetch`` client sending
``X-CSRF-Token`` -- which is what three of our own JS bundles already send, and
what the planned SPA will send -- was refused by all of them. Not with a
recognisable error, either: the admin paths answer 400 with ``csrf_failed`` and
the page routes re-render themselves with "Security check failed", which reads
as a stale tab rather than as a contract mismatch.

Six implementations also means six places to fix a bug and six chances to fix it
in five of them. The alias row is the live example: ``X-CSRFToken`` (Django's
spelling) was accepted in exactly one, so whether a client worked depended on
which route pack it happened to be talking to. The bearer row is the other:
``pulse_ads_verify_write`` intended the exemption and said so in a comment, but
implemented it as a bare ``g.mobile_access_user_id`` test -- a flag
``account_user_id()`` only sets when it reaches its bearer branch, which it
skips whenever a session cookie is present. The native app sends both, so that
exemption never fired in production.

WHAT IS ACCEPTED, AND WHY EACH
------------------------------
``X-CSRF-Token`` is the contract. A custom request header cannot be attached to
a cross-origin request without a CORS preflight, and this app sets no CORS
response headers at all (``undx_desktop_connector.py`` does, but that is a
separate Flask app bound to localhost, not ``webhook_app``), so the preflight is
never answered and the forged request never arrives. The header is therefore
strictly stronger evidence than a form field, not weaker -- widening a
form-only verifier to accept it takes nothing away.

``X-CSRFToken`` is accepted as a legacy alias rather than dropped. It is already
accepted by the Business OS commerce and supplier packs and is already named in
their diagnostics, so some client somewhere may be sending it; removing it is a
narrowing that would fail closed on traffic this repo cannot see. It is listed,
counted, and deprecated in one place instead of being an accident in one of
three.

``csrf_token`` as a form field stays because 15 templates emit it and
``inject_admin_form_csrf`` writes it into every admin POST form by construction.
Server-rendered forms are not going away when the SPA ships.

When more than one channel carries a token, **any** of them matching is enough.
Precedence exists only to decide what a *refusal* reports. The first draft made
the header win outright, and an exhaustive 6-verifier x 30-shape sweep against
the pre-unification code showed that this narrowed the gate in 12 places: a
valid form field alongside a stale header passed before and would have been
refused after. Accepting any matching channel concedes nothing -- an attacker
who cannot produce the token on one channel cannot produce it on two.

WHAT IS NOT ACCEPTED
--------------------
The ``<meta name="csrf-token">`` element in ``pulse_advertiser_portal.html`` is
a *source* for the client to read, not a channel the server accepts. It is named
here only so nobody adds a fourth spelling by reading that template and
assuming symmetry.

THE BEARER EXEMPTION IS A PARAMETER, NOT A DIFFERENCE
-----------------------------------------------------
A request carrying a bearer the server can verify is inherently CSRF-safe: an
attacker's page cannot read the victim's token out of the keychain, so it cannot
attach one. The native app relies on this -- it sends a cookie and a bearer and
has no CSRF token to echo at all.

But it is deliberately NOT switched on everywhere. ``allow_bearer`` defaults to
False, and the admin form path leaves it False. The bearer resolves a *member*
identity; letting it exempt a request whose authority comes from
``session['admin_user_id']`` would mean a member credential vouching for an
admin action. That is not a CSRF hole on its own (the attacker has neither the
victim's bearer nor the ability to set the victim's cookie), but it is authority
crossing a boundary the rest of the codebase works to keep, for no gain: there
is no admin client that carries a bearer.

The important change is that the exemption is now visible at each call site as
an argument, instead of being an undocumented difference between two functions
with similar names.
"""
from __future__ import annotations

import hmac
import secrets

from flask import g, request, session

#: The header clients should send. One spelling, documented, and the one the
#: rebuilt web client must use.
CSRF_HEADER = "X-CSRF-Token"

#: Accepted but deprecated. Kept because dropping a spelling that is live
#: somewhere fails closed on writes, and a write that silently stops working is
#: the failure mode this whole module exists to remove. New clients must not use
#: these; `header_alias_uses()` exists so the list can be retired on evidence.
CSRF_HEADER_ALIASES = ("X-CSRFToken",)

#: The form field name. Emitted by templates and by `inject_admin_form_csrf`.
CSRF_FORM_FIELD = "csrf_token"

#: The session key the token is stored under. Named here so nothing else has to
#: spell it, and so a future rotation has one place to change.
CSRF_SESSION_KEY = "csrf_token"

#: Where a submitted token came from, for diagnostics. Ordered by preference.
SOURCE_HEADER = "header"
SOURCE_HEADER_ALIAS = "header-alias"
SOURCE_FORM = "form"
SOURCE_BEARER = "bearer"
SOURCE_NONE = "none"


def issue_token() -> str:
    """The session's CSRF token, minting one on first use.

    ``secrets.token_urlsafe(32)`` -- 256 bits from the OS CSPRNG. Stored in the
    signed session cookie, so a client cannot choose it.
    """
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def submitted_tokens() -> list[tuple[str, str]]:
    """Every token the client sent, with the channel each arrived on.

    All of them, not the first one. The first draft of this module returned only
    the highest-precedence channel, and an exhaustive sweep of 6 verifiers x 30
    request shapes against the pre-unification code found that this *narrowed*
    the gate in 12 places: a request carrying a valid ``csrf_token`` form field
    and a stale ``X-CSRF-Token`` header passed before and would have been
    refused after. That shape is not hypothetical -- a page renders its form
    field and its meta tag at the same instant, but JS that caches the header
    value and a form that is re-submitted after a session rotation do not go
    stale together.

    Accepting any matching channel gives an attacker nothing: they cannot set
    the correct value on either channel, so checking two candidates instead of
    one does not help them. Precedence survives only to decide what a refusal
    *reports*, which is a diagnostic question, not an authorisation one.
    """
    found: list[tuple[str, str]] = []
    value = request.headers.get(CSRF_HEADER)
    if value:
        found.append((value, SOURCE_HEADER))
    for alias in CSRF_HEADER_ALIASES:
        value = request.headers.get(alias)
        if value:
            found.append((value, SOURCE_HEADER_ALIAS))
    # `request.form` on a JSON body is an empty MultiDict, not an error, so this
    # is safe for every content type. Guarded anyway: reading `.form` on a
    # malformed multipart body can raise.
    try:
        value = request.form.get(CSRF_FORM_FIELD)
    except Exception:
        value = None
    if value:
        found.append((value, SOURCE_FORM))
    return found


def submitted_token() -> tuple[str | None, str]:
    """The highest-precedence token the client sent, for reporting.

    Returns ``(None, SOURCE_NONE)`` when the client sent nothing, which is
    distinct from sending something wrong -- a distinction the callers' error
    messages have never made, and which is the difference between "your tab is
    stale" and "your client is misconfigured".

    Not used to decide anything. See ``submitted_tokens``.
    """
    found = submitted_tokens()
    return found[0] if found else (None, SOURCE_NONE)


def _bot():
    """The bot module, imported late and through one seam.

    Late because ``bot`` imports this module, so a top-level import here is a
    cycle. Through a named function because this is the only place the bearer
    verifier is resolved, and the suites that prove the gate fails closed --
    forged bearer, verifier absent, verifier raising, bearer naming a different
    user than the cookie -- all inject their verifier by replacing this.

    That seam is load-bearing in a way that is easy to destroy silently. When
    this logic moved here from ``business_os_commerce_routes`` it briefly called
    ``import bot`` inline instead. Seven tests failed loudly, which was the good
    half; the other five kept passing while testing nothing, because they assert
    a *denial* and an ignored fake bearer denies just as well as a rejected one.
    A security test that cannot fail is worse than no test, so the seam stays.
    """
    import bot
    return bot


def bearer_is_csrf_safe() -> bool:
    """True when a bearer token the server can verify accompanies this request.

    ``g.mobile_access_user_id`` is set by ``bot.account_user_id()`` only when
    that function reaches its bearer branch, which it skips whenever a session
    cookie is present -- and the native app sends both. So the flag alone is not
    enough, and the bearer is re-verified here when it is missing.

    Re-verification goes through ``bot.account_user_id_from_mobile_access_token``,
    which checks signature, expiry, device hash and an active non-revoked
    ``mobile_security_sessions`` row. Authority comes from the database, never
    from the client's say-so. A bearer naming a different user than the cookie
    is refused rather than preferred: disagreement is a sign of a mixed-up
    client, and the safe reading of two identities is neither.
    """
    if getattr(g, "mobile_access_user_id", None):
        return True
    header = (request.headers.get("Authorization") or "").strip()
    if not header.lower().startswith("bearer "):
        return False
    try:
        resolve = getattr(_bot(), "account_user_id_from_mobile_access_token", None)
        bearer_user_id = resolve() if callable(resolve) else None
    except Exception:
        return False
    if not bearer_user_id:
        return False
    cookie_user_id = session.get("account_user_id")
    if cookie_user_id and str(cookie_user_id) != str(bearer_user_id):
        return False
    return True


def token_matches() -> bool:
    """Constant-time comparison of the submitted token against the session's.

    ``hmac.compare_digest`` rather than ``==``, which is what four of the six
    predecessors used. The practical exposure of a fast comparison on a 256-bit
    random token is small, but "small" is an argument that has to be re-made
    every time somebody reads the code, and the constant-time call costs
    nothing.
    """
    expected = session.get(CSRF_SESSION_KEY)
    if not expected:
        return False
    expected = str(expected)
    # Every supplied channel is compared, and every comparison runs even after a
    # match, so the work done does not depend on which channel was right.
    matched = False
    for supplied, _source in submitted_tokens():
        if hmac.compare_digest(expected, str(supplied)):
            matched = True
    return matched


def verify(allow_bearer: bool = False) -> bool:
    """The single answer to "is this write CSRF-safe?".

    ``allow_bearer`` is off by default so that adding a CSRF check somewhere new
    cannot accidentally inherit an exemption. See the module docstring for why
    the admin form path leaves it off.
    """
    if allow_bearer and bearer_is_csrf_safe():
        return True
    return token_matches()


def refusal_detail() -> dict:
    """Why a refusal happened, in fields safe to log or return.

    No token values, no user ids, no header contents -- booleans and a channel
    name. The three predecessors all answered a refusal with the same opaque
    string, which is why ``business_os_supplier_routes`` had to grow its own
    five-bit diagnostic to tell "client sent nothing" apart from "client sent a
    stale token": those need different fixes and one of them is ours.
    """
    supplied, source = submitted_token()
    return {
        "sent": source,
        "session_has_token": bool(session.get(CSRF_SESSION_KEY)),
        "matched": bool(supplied) and token_matches(),
        "bearer_safe": bearer_is_csrf_safe(),
    }
