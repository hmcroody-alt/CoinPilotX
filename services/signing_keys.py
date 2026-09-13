"""One root secret, five independent signing keys.

``COINPILOTX_SECRET_KEY`` signed five unrelated credential families: the Flask
session cookie, the mobile bearer access token, messenger media URLs, password
reset links, and the arithmetic captcha. One key for five purposes means you
cannot rotate one without rotating all five, and the costs are not remotely
comparable. Measured, by reading what each family actually does on a key change:

    family              rotation cost              how it recovers
    ------------------  -------------------------  --------------------------
    Flask session       every web user logged out,  re-login, and only that:
                        permanently                the cookie is client-side
                                                   signed with no server-side
                                                   row, and lives 10 years
    mobile access       <= 15 minutes              automatic. The client
                        (PULSESOC_MOBILE_ACCESS_   refreshes on a bare 401 and
                        TOKEN_TTL_SECONDS = 900)   replays
    messenger media     <= 15 minutes              automatic, URLs re-mint
    password reset      <= 1 hour of pending       request another reset
                        links
    captcha             one request                retry

The asymmetry is the finding, and it runs the opposite way to intuition. The
bearer key is nearly free to rotate on its own -- the refresh token is a random
string hashed with a plain SHA-256 and stored in ``mobile_security_sessions``,
so it does not involve this secret at all, and ``/api/mobile/auth/refresh``
accepts it without a bearer. A phone recovers by itself inside fifteen minutes.
But because the two share a key today, rotating the cheap one forces a
permanent logout of every web session. The coupling taxes exactly the operation
you most want to be able to perform quickly.

DERIVED, NOT CONFIGURED
-----------------------
Each purpose gets ``HMAC-SHA256(root, "pulsesoc/key/v1/" + purpose)``. Deriving
rather than adding five required environment variables means the split takes
effect on the next deploy with no operator action, and no possibility of a
deploy that half-works because someone set four of five. The root stays the only
thing that must be configured.

Derivation is one-way, so a leaked media-URL key does not expose the session
key or the root. It does not, of course, help if the *root* leaks -- that is
what the per-purpose overrides are for.

``PULSESOC_<PURPOSE>_SECRET`` overrides a single derived key. That is the
mechanism for independent rotation: set one variable and one credential family
rotates while the other four keep working. It is the whole point of the split,
and it is why the override is read on every call rather than captured at import.
"""
from __future__ import annotations

import hashlib
import hmac
import os

#: Bumped only if the derivation itself changes. Baked into the derived value so
#: a future scheme change cannot silently produce the same key for a purpose.
_SCHEME = "pulsesoc/key/v1/"

#: The Flask session cookie. Rotating this is the expensive one: web sessions
#: have no server-side record and a 10-year lifetime, so there is no recovery
#: path except asking every user to sign in again.
SESSION = "session"

#: The mobile bearer access token, minted by `mobile_access_token()` and
#: verified on every native request. Cheap to rotate: 900s TTL and the client
#: self-heals through a refresh token that does not use this secret.
MOBILE_ACCESS = "mobile-access"

#: Per-attachment, per-viewer, per-window media URLs. 900s TTL, re-minted on
#: read, so rotation is invisible.
MESSENGER_MEDIA = "messenger-media"

#: The lookup hash stored in `password_reset_tokens.token_hash`. Rotating this
#: orphans pending links rather than merely invalidating a signature, because
#: the hash *is* the index -- which is why the verify path checks the legacy
#: derivation too.
PASSWORD_RESET = "password-reset"

#: The arithmetic captcha answer hash. Single request-response; rotation costs
#: one retry to whoever was mid-form.
CAPTCHA = "captcha"

PURPOSES = (SESSION, MOBILE_ACCESS, MESSENGER_MEDIA, PASSWORD_RESET, CAPTCHA)

#: Per-purpose override variables. Named here so `.env.example`, the tests and
#: the runtime cannot disagree about the spelling.
OVERRIDE_ENV = {
    SESSION: "PULSESOC_SESSION_SECRET",
    MOBILE_ACCESS: "PULSESOC_MOBILE_ACCESS_SECRET",
    MESSENGER_MEDIA: "PULSESOC_MESSENGER_MEDIA_SECRET",
    PASSWORD_RESET: "PULSESOC_PASSWORD_RESET_SECRET",
    CAPTCHA: "PULSESOC_CAPTCHA_SECRET",
}


def derive(root: str, purpose: str) -> str:
    """The signing key for one purpose.

    Pure, and takes the root as an argument rather than reading the environment
    for it. ``bot`` resolves the root through a chain of three variables, a
    random fallback and a boot-time guard; re-resolving that here would be a
    second implementation of the same decision, and the failure mode of the two
    disagreeing is every token in the system failing to verify.
    """
    if purpose not in OVERRIDE_ENV:
        raise ValueError(
            f"unknown signing purpose {purpose!r}. Add it to PURPOSES and "
            f"OVERRIDE_ENV rather than passing a bare string: the point of the "
            f"list is that a typo cannot silently mint a new key family."
        )
    override = (os.getenv(OVERRIDE_ENV[purpose]) or "").strip()
    if override:
        return override
    if not root:
        raise ValueError("cannot derive a signing key from an empty root secret")
    return hmac.new(
        root.encode("utf-8"),
        (_SCHEME + purpose).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
