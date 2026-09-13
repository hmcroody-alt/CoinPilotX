"""Where the platform is allowed to believe a request came from.

Every per-IP control in this product -- ``bot.basic_abuse_guard``, the
failed-login lockout in ``bot.register_failed_login``, ``pulse_security_core``,
and the distributed counter in ``services/sentinel/rate_limit.py`` -- keys on a
value derived from one line that appeared four times in ``bot.py``::

    request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]

That reads the *leftmost* element of ``X-Forwarded-For``. Leftmost is the end of
the chain nearest the client, which is to say: the part a client can type. On a
deployment whose edge *appends* the peer address -- Envoy's default with
``use_remote_address``, which is to say most of them -- ``xff[0]`` is whatever
the caller sent, and every limit above is keyed on an attacker-chosen string.
Worse than evasion: ``register_failed_login`` arms
``failed_login_controls`` on that value, so forging a victim's address and
failing eight logins locks the victim out.

Measured, not assumed
---------------------

That is not what this deployment does today. Three requests to
``https://pulsesoc.com/api/mobile/auth/login`` on 2026-09-13, two carrying
forged ``X-Forwarded-For`` headers (one of them two elements long) and one
carrying none, all produced the same ``auth_events.ip_address``: the real peer.
Railway's edge **replaces** ``X-Forwarded-For`` rather than appending to it, the
same way it overwrites ``X-Forwarded-Proto``. So the leftmost read is, today,
the rightmost read, and the vulnerability is not live.

It is safe by accident. Nothing in the repository stated the assumption, no test
would fail if it stopped holding, and the things that would end it are ordinary:
putting a CDN in front (the CSP already names ``static.cloudflareinsights.com``),
moving off Railway, or Railway changing an Envoy default. The point of this
module is to make the accident into a property.

The fix is one index
--------------------

With ``hops`` trusted reverse proxies in front of the app, the trustworthy
element is ``xff[-hops]``: the rightmost entries are the ones infrastructure you
control appended, and everything to their left is unverified. Reading from the
right is correct whether the edge appends or replaces -- on a replacing edge
with ``hops=1`` the list has exactly one element and ``xff[-1] is xff[0]``, so
this changes nothing in production right now. That is the intended shape. This
is a guard, not a behaviour change; it is supposed to be a no-op until the day
it isn't.

Why a count and not a log line
------------------------------

``element_counts`` records how long the received ``X-Forwarded-For`` actually
was. Today that is ``{1: everything}``. The first ``{2: ...}`` is the topology
having changed under the assumption above. This is a counter rather than a
warning on purpose: on a *replacing* edge any second element is a surprise worth
seeing, but on an *appending* edge a second element is every client that sends
its own header, and a log line would be pure noise on half the possible
deployments while being the whole signal on the other half. A distribution is
readable under both.

Country
-------

``request_country()`` in ``bot.py`` read ``CF-IPCountry``, ``X-Country-Code``,
``X-Appengine-Country`` and ``CloudFront-Viewer-Country`` straight off the
request with no validation. No edge in front of this deployment sets any of
them, which was confirmed in the same probe -- the control request recorded
``country=''`` -- and the probe that sent ``X-Country-Code: ZZ`` recorded
``ZZ``. So that field was not geolocation, it was a free-text field the caller
filled in, and it is displayed in login-security alerts as though it were
evidence. ``PULSESOC_TRUSTED_GEO_HEADER`` names the single header a known edge
sets; unset means no header is trusted, which reproduces the empty string
production already returns while closing the injection.
"""

from __future__ import annotations

import ipaddress
import os
import threading

#: Reverse proxies between the internet and this process whose appended
#: ``X-Forwarded-For`` entries may be believed. Railway's edge is one hop.
#: ``0`` means the app is directly exposed, in which case ``X-Forwarded-For`` is
#: unverified client input and only the socket peer is trustworthy.
HOPS_ENV = "PULSESOC_TRUSTED_PROXY_HOPS"
DEFAULT_HOPS = 1

#: The one header a trusted edge sets with a resolved country. Unset => none.
GEO_HEADER_ENV = "PULSESOC_TRUSTED_GEO_HEADER"

_LOCK = threading.Lock()
_STATS: dict = {
    "resolutions": 0,
    "from_forwarded": 0,
    "from_peer": 0,        # no usable X-Forwarded-For; socket peer used
    "rejected_value": 0,   # the trusted slot held something that is not an IP
    "hops_zero": 0,        # configured as directly exposed
    "element_counts": {},  # observed len(X-Forwarded-For) -> occurrences
}


def trusted_proxy_hops() -> int:
    """How many appended ``X-Forwarded-For`` entries may be believed.

    An unparseable value falls back to the default rather than to ``0``.
    Falling back to ``0`` would look like the safer choice -- trust nothing --
    but it is not: with ``hops=0`` this returns the socket peer, which behind
    Railway's edge is the edge itself, so *every* request in the fleet would
    collapse into one rate-limit bucket and the first mildly busy minute would
    lock out the entire internet. A typo in a Railway variable must not be able
    to do that.
    """
    raw = (os.getenv(HOPS_ENV) or "").strip()
    if not raw:
        return DEFAULT_HOPS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_HOPS


def _note(field: str, amount: int = 1) -> None:
    with _LOCK:
        _STATS[field] = _STATS.get(field, 0) + amount


def _note_elements(count: int) -> None:
    with _LOCK:
        counts = _STATS["element_counts"]
        # Bounded: a client controls how many elements it sends, so an unbounded
        # key space here would be a memory-growth vector reachable from the
        # internet -- the same defect this module's neighbours already have.
        if count not in counts and len(counts) >= 16:
            count = -1
        counts[count] = counts.get(count, 0) + 1


def _usable(candidate: str) -> str:
    """Return ``candidate`` if it is an IP address, else ``""``.

    ``X-Forwarded-For`` is allowed to carry tokens that are not addresses --
    ``unknown`` is in RFC 7239's ancestry, and obfuscated identifiers are
    common. Passing one through would make it a rate-limit subject shared by
    every client whose proxy emits the same token, which is a bucket collision
    dressed as an IP.
    """
    value = (candidate or "").strip()
    if not value:
        return ""
    # A port may be attached to IPv6 only in bracket form; bare IPv4:port also
    # occurs. Strip the obvious cases before validating.
    if value.startswith("[") and "]" in value:
        value = value[1:value.index("]")]
    elif value.count(":") == 1 and "." in value:
        value = value.split(":", 1)[0]
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return ""
    return value


def client_ip(headers, remote_addr: str = "") -> str:
    """The address this request may be attributed to. ``""`` if none.

    ``headers`` is anything with ``.get(name, default)`` -- a Flask
    ``request.headers`` or a plain dict. Taking it as an argument rather than
    reaching for ``flask.request`` keeps this testable without a request
    context, which is what lets the tests below enumerate edge topologies
    instead of mocking one.
    """
    _note("resolutions")
    peer = _usable(remote_addr)
    hops = trusted_proxy_hops()

    if hops <= 0:
        # Directly exposed: X-Forwarded-For is unverified client input.
        _note("hops_zero")
        _note("from_peer")
        return peer

    try:
        raw = headers.get("X-Forwarded-For", "") or ""
    except Exception:
        raw = ""
    chain = [part.strip() for part in str(raw).split(",") if part.strip()]
    _note_elements(len(chain))

    if not chain:
        _note("from_peer")
        return peer

    # Count from the right. Entries to the left of the trusted suffix were not
    # written by infrastructure we control and are not read.
    index = min(hops, len(chain))
    trusted = _usable(chain[-index])
    if not trusted:
        _note("rejected_value")
        _note("from_peer")
        return peer

    _note("from_forwarded")
    return trusted


def client_country(headers) -> str:
    """The country a *trusted edge* resolved, or ``""``.

    Never falls back to a client-supplied header. The absence of geolocation is
    reported as absent; a security alert that says nothing about where a login
    came from is honest, and one that says "ZZ" because the caller typed it is
    not.
    """
    name = (os.getenv(GEO_HEADER_ENV) or "").strip()
    if not name:
        return ""
    try:
        value = headers.get(name, "") or ""
    except Exception:
        return ""
    value = str(value).strip()[:8]
    # Country codes only. A trusted edge emits two letters; anything else is a
    # misconfiguration pointing this at the wrong header, and passing it through
    # would put arbitrary edge text into an alert.
    return value.upper() if len(value) == 2 and value.isalpha() else ""


def stats() -> dict:
    """Per-process counters for a health surface.

    ``element_counts`` is the one worth reading: it is the evidence for the
    assumption in this module's docstring, sampled from live traffic rather than
    asserted once in a probe.
    """
    with _LOCK:
        snapshot = dict(_STATS)
        snapshot["element_counts"] = dict(_STATS["element_counts"])
    snapshot["trusted_proxy_hops"] = trusted_proxy_hops()
    snapshot["trusted_geo_header"] = (os.getenv(GEO_HEADER_ENV) or "").strip()
    return snapshot


def reset_for_tests() -> None:
    with _LOCK:
        _STATS.update({
            "resolutions": 0, "from_forwarded": 0, "from_peer": 0,
            "rejected_value": 0, "hops_zero": 0, "element_counts": {},
        })
