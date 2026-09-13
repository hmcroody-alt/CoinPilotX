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

The fix is one index, and one precondition
------------------------------------------

With ``hops`` trusted reverse proxies in front of the app, the trustworthy
element is ``xff[-hops]``: the rightmost entries are the ones infrastructure you
control appended, and everything to their left is unverified. Reading from the
right is correct whether the edge appends or replaces -- on a replacing edge
with ``hops=1`` the list has exactly one element and ``xff[-1] is xff[0]``, so
this changes nothing in production right now. That is the intended shape. This
is a guard, not a behaviour change; it is supposed to be a no-op until the day
it isn't.

The index alone is not enough, because it presumes the chain is at least
``hops`` long. Clamping it into range when it isn't -- the natural
``min(hops, len(chain))`` -- re-opens the hole it was written to close: at
``hops=2`` a one-element chain is read at ``[-1]``, which is the element the
caller typed. That is unreachable at ``hops=1``, so it survives every test run
against today's topology and arms itself on the day someone puts a CDN in front,
which is the day this file tells them to raise ``hops`` to 2. A chain shorter
than ``hops`` therefore falls back to the socket peer and is counted, never
read.

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
    "short_chain": 0,      # fewer proxies appended than are configured as trusted
    "element_counts": {},  # observed len(X-Forwarded-For) -> occurrences
    "observed_hops": None,  # hop count in effect when the above were recorded
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
    # Recorded, not re-read later. The counters below are a history, and the hop
    # count is re-read from the environment on every resolution -- so a status
    # that pairs historical counters with a freshly-read config value can
    # describe observations under a configuration that was not in effect when
    # they were made. The observation and its configuration travel together.
    with _LOCK:
        _STATS["observed_hops"] = hops

    # Observed always, trusted only per configuration. Counting the chain even
    # when we are configured to ignore it is what makes `hops=0` diagnosable: if
    # the app believes it is directly exposed and forwarded elements keep
    # arriving anyway, something is in front of it that nobody told it about,
    # and every request is being attributed to that thing's address.
    try:
        raw = headers.get("X-Forwarded-For", "") or ""
    except Exception:
        raw = ""
    chain = [part.strip() for part in str(raw).split(",") if part.strip()]
    _note_elements(len(chain))

    if hops <= 0:
        # Directly exposed: X-Forwarded-For is unverified client input.
        _note("hops_zero")
        _note("from_peer")
        return peer

    if not chain:
        _note("from_peer")
        return peer

    # A chain shorter than the trusted hop count did not traverse the proxies we
    # believe are in front, so none of its elements were written by us. Clamping
    # the index into range instead -- `min(hops, len(chain))` -- reads whatever
    # is there, which at hops=2 hands a one-element chain straight back to the
    # caller who typed it: this module's entire defect, reintroduced by a bounds
    # check. It is not reachable at hops=1, which is why it would have shipped
    # quietly and armed itself on the day a CDN went in front.
    #
    # A client cannot cause this: every proxy appends, so the chain can only be
    # short if the request skipped one (origin reachable directly past the CDN)
    # or the hop count is wrong. Both are operator-actionable, which is what
    # makes this safe to raise an alert on -- see `edge_status()`.
    if len(chain) < hops:
        _note("short_chain")
        _note("from_peer")
        return peer

    # Count from the right. Entries to the left of the trusted suffix were not
    # written by infrastructure we control and are not read.
    trusted = _usable(chain[-hops])
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

    Every counter here counts *resolutions*, not requests. ``client_ip_hash()``
    alone is called from 53 places in ``bot.py`` and up to three times inside a
    single pass through ``basic_abuse_guard``, so one request can contribute
    several. That makes these numbers useful for ratios and for "has this ever
    happened", and wrong for "how many callers" -- which is why nothing built on
    them is phrased in requests.
    """
    with _LOCK:
        snapshot = dict(_STATS)
        snapshot["element_counts"] = dict(_STATS["element_counts"])
    snapshot["trusted_proxy_hops"] = trusted_proxy_hops()
    snapshot["trusted_geo_header"] = (os.getenv(GEO_HEADER_ENV) or "").strip()
    return snapshot


def edge_status():
    """Honest state for an operations surface, or ``None`` if unobserved.

    ``None`` rather than ``"ok"`` when this process has resolved nothing. A
    worker that has answered no requests has verified nothing about the edge,
    and the Operations Center renders an omitted service neutral rather than
    green -- the same rule the rest of ``/admin/ops/status.json`` follows, and
    the same distinction the shadow report draws with its no-data exit code.

    Per-process, which is adequate here and would not be for a rate count. The
    counters live in one of four gunicorn workers and the poll lands on whichever
    one answers, so this under-samples. But both conditions it reports are
    properties of the *deployment* -- the hop count is one environment variable
    and the topology is shared -- so any worker that sees one is representative,
    and the poll re-runs every 20 seconds against a fresh draw.

    Two conditions warn, and one deliberately does not:

    ``short_chain`` -- resolutions that saw fewer forwarded elements than the
    configured hop count. Those addresses are being attributed to the socket
    peer, which behind an edge is the edge, so those callers share a single
    rate-limit bucket. Unreachable by a client (proxies only ever append), so it
    cannot be used to spam the alert.

    ``hops_zero`` alongside observed forwarded elements -- the app is configured
    as directly exposed but something is forwarding to it, so every caller in
    the fleet is being attributed to that thing.

    A chain *longer* than the hop count is not a warning, on purpose. On an
    appending edge that is every client that sends its own header, i.e. a
    permanently yellow light, which is a light nobody reads. It is the reason
    ``element_counts`` is a distribution rather than a log line, and it stays in
    the detail below where an operator can see the shape and decide.
    """
    snapshot = stats()
    if not snapshot["resolutions"]:
        return None

    forwarded_seen = any(
        length >= 1 for length in snapshot["element_counts"] if length != -1
    ) or bool(snapshot["element_counts"].get(-1))

    if snapshot["short_chain"]:
        state, detail = "warn", (
            f"{snapshot['short_chain']} resolution(s) saw fewer forwarded "
            f"elements than the {snapshot['observed_hops']} trusted hop(s) in "
            f"effect; those were attributed to the socket peer")
    elif not snapshot["observed_hops"] and forwarded_seen:
        state, detail = "warn", (
            "configured as directly exposed, but forwarded elements are "
            "arriving; every caller is being attributed to whatever is in front")
    else:
        state, detail = "ok", "chain shape matches the configured hop count"

    return {"state": state, "detail": detail, "counters": snapshot}


def reset_for_tests() -> None:
    with _LOCK:
        _STATS.update({
            "resolutions": 0, "from_forwarded": 0, "from_peer": 0,
            "rejected_value": 0, "hops_zero": 0, "short_chain": 0,
            "element_counts": {}, "observed_hops": None,
        })
