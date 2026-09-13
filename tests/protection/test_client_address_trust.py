"""Locks for the value every per-IP control in this product is keyed on.

The defect these cover is not that production is broken. It is that production
was *correct by accident* and nothing could tell. `bot.client_ip_address()` read
the leftmost element of `X-Forwarded-For` -- the end of the chain a caller can
type -- and that is only safe because Railway's edge replaces the header instead
of appending to it. That was measured, not assumed: three requests to
/api/mobile/auth/login on 2026-09-13, two of them carrying forged
`X-Forwarded-For` values (one two elements long), all recorded the same real
peer address in `auth_events.ip_address`.

An accident that survives is still an accident. Adding a CDN, moving hosts, or
Railway changing an Envoy default would have silently turned every rate limit,
and the failed-login lockout that can block an address for 900 seconds, into
controls keyed on attacker-chosen strings. The tests below are what make the
assumption a property: they enumerate both edge behaviours and require the same
answer from each.

They parse source and call `services/client_address.py` directly rather than
importing `bot`, for the reason given in test_backup_and_secret_integrity.py --
importing bot boots a 124k-line Flask monolith.

Zero-arg tests, no fixtures: this directory runs files as scripts.
"""

import os
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
BOT = (REPO / "bot.py").read_text(encoding="utf-8")

import sys
sys.path.insert(0, str(REPO))

from services import client_address  # noqa: E402


HOPS = client_address.HOPS_ENV
GEO = client_address.GEO_HEADER_ENV


def _clear_env():
    for name in (HOPS, GEO):
        os.environ.pop(name, None)


def _resolve(xff=None, peer="", hops=None, headers=None):
    """One resolution with a clean, explicit environment."""
    _clear_env()
    if hops is not None:
        os.environ[HOPS] = str(hops)
    request_headers = dict(headers or {})
    if xff is not None:
        request_headers["X-Forwarded-For"] = xff
    try:
        return client_address.client_ip(request_headers, peer)
    finally:
        _clear_env()


# --- 1. The same answer under both edge behaviours ----------------------------
#
# These two are the whole point. A replacing edge and an appending edge present
# the app with different header values for the same request, and the resolver
# has to land on the real client in both cases. The old expression passed the
# first and failed the second.

def test_a_replacing_edge_yields_the_real_client():
    """Railway today: the edge discards what the client sent and writes one
    element. Forgery is invisible to the app because it never arrives."""
    assert _resolve(xff="203.0.113.7", peer="10.0.0.1") == "203.0.113.7"


def test_an_appending_edge_ignores_the_clients_own_prefix():
    """Envoy's default, and the case the old code got wrong.

    The client sent `X-Forwarded-For: 198.51.100.88`, the edge appended the real
    peer, and the app received both. Reading from the left returns the forgery.
    """
    resolved = _resolve(xff="198.51.100.88, 203.0.113.7", peer="10.0.0.1")
    assert resolved == "203.0.113.7", (
        "a client-supplied X-Forwarded-For prefix is being believed. Every "
        "per-IP limit and the failed-login lockout are keyed on this value."
    )


def test_a_long_forged_prefix_is_still_ignored():
    """Length is not a defence: the attacker chooses how many to send."""
    forged = ", ".join(f"198.51.100.{n}" for n in range(1, 25))
    assert _resolve(xff=f"{forged}, 203.0.113.7", peer="10.0.0.1") == "203.0.113.7"


def test_two_trusted_hops_read_two_from_the_right():
    """A CDN in front of Railway is the realistic next topology.

    With two trusted proxies the chain is [client-junk..., client, cdn] and the
    trustworthy element is the second from the right.
    """
    resolved = _resolve(
        xff="198.51.100.88, 203.0.113.7, 192.0.2.50", peer="10.0.0.1", hops=2)
    assert resolved == "203.0.113.7"


# --- 2. Failure modes that must not collapse into one bucket ------------------

def test_a_misconfigured_hop_count_does_not_collapse_every_client():
    """An unparseable hop count must fall back to the default, not to zero.

    Zero looks like the cautious choice -- trust no header -- but behind an edge
    it returns the *edge's* address for every request in the fleet, so all
    traffic shares one rate-limit bucket and the first busy minute locks out the
    internet. A typo in a Railway variable must not be able to do that.
    """
    assert _resolve(xff="203.0.113.7", peer="10.0.0.1", hops="not-a-number") == "203.0.113.7"


def test_zero_hops_means_direct_exposure_and_ignores_the_header():
    """Configured deliberately, hops=0 is correct: with nothing in front, the
    header is pure client input and only the socket peer means anything."""
    assert _resolve(xff="198.51.100.88", peer="203.0.113.7", hops=0) == "203.0.113.7"


def test_a_chain_shorter_than_the_hop_count_is_not_trusted():
    """The bounds check that quietly undoes the fix.

    With two trusted proxies the shortest legitimate chain has two elements, one
    appended by each. A one-element chain means the request skipped one of them
    -- an origin reachable directly past the CDN, or a wrong hop count -- so that
    element is the caller's own string. Clamping the index into range with
    `min(hops, len(chain))` reads it at `[-1]` and hands back a value the
    attacker chose, which is this module's entire defect wearing a bounds check.

    It cannot be caught at hops=1, where the clamp never binds. So it would pass
    every test written against today's topology and arm itself on the day a CDN
    goes in front -- the day this module's own docstring tells an operator to
    raise the hop count to 2.
    """
    resolved = _resolve(xff="198.51.100.88", peer="10.0.0.1", hops=2)
    assert resolved == "10.0.0.1", (
        "a chain shorter than the trusted hop count was read anyway; at hops=2 "
        "that returns the one element the caller typed"
    )


def test_a_non_address_in_the_trusted_slot_is_refused():
    """`unknown` and obfuscated identifiers are legal in X-Forwarded-For.

    Passing one through makes it a rate-limit subject shared by every client
    behind a proxy that emits the same token -- a bucket collision wearing an
    IP's clothes.
    """
    assert _resolve(xff="198.51.100.88, unknown", peer="203.0.113.7") == "203.0.113.7"


def test_a_missing_header_falls_back_to_the_peer():
    assert _resolve(xff=None, peer="203.0.113.7") == "203.0.113.7"


def test_an_empty_chain_falls_back_to_the_peer():
    assert _resolve(xff="   ,  ,", peer="203.0.113.7") == "203.0.113.7"


def test_a_port_suffix_does_not_defeat_validation():
    """Some proxies attach the source port. Rejecting `203.0.113.7:51234` as
    "not an IP" would silently demote the request to the peer address, i.e. to
    the shared edge bucket, for every client behind that proxy."""
    assert _resolve(xff="203.0.113.7:51234", peer="10.0.0.1") == "203.0.113.7"
    assert _resolve(xff="[2001:db8::8]:443", peer="10.0.0.1") == "2001:db8::8"


def test_ipv6_survives_intact():
    """Splitting on ':' to strip a port would truncate every IPv6 address to
    "2001", collapsing the entire v6 internet into one bucket."""
    assert _resolve(xff="2001:db8::8", peer="10.0.0.1") == "2001:db8::8"


# --- 3. Country is evidence or it is absent -----------------------------------

def test_country_is_empty_when_no_trusted_edge_is_named():
    """Production's real state, and the injection that state used to allow.

    A probe sending `X-Country-Code: ZZ` was recorded as ZZ and surfaced through
    login_security_details() into security alerts. Returning "" is not a
    regression: "" is what production already produced for real visitors,
    because production has no geo edge.
    """
    _clear_env()
    assert client_address.client_country({"CF-IPCountry": "ZZ", "X-Country-Code": "ZZ"}) == ""


def test_country_is_read_only_from_the_one_named_header():
    _clear_env()
    os.environ[GEO] = "CF-IPCountry"
    try:
        assert client_address.client_country({"CF-IPCountry": "US"}) == "US"
        # The header that is not configured stays untrusted even though the old
        # code accepted it.
        assert client_address.client_country({"X-Country-Code": "US"}) == ""
    finally:
        _clear_env()


def test_country_rejects_anything_that_is_not_a_country_code():
    """Pointing this at the wrong header must yield nothing, not edge text in
    a security alert."""
    _clear_env()
    os.environ[GEO] = "X-Edge-Info"
    try:
        for value in ("United States", "1", "", "<b>US</b>", "U5"):
            assert client_address.client_country({"X-Edge-Info": value}) == "", value
    finally:
        _clear_env()


# --- 4. The observable that replaces the assumption ---------------------------

def test_chain_lengths_are_counted_so_a_topology_change_is_visible():
    """`element_counts` is the live evidence for this module's premise.

    Today it reads {1: everything}. The first {2: ...} is the day the edge
    started appending -- which is exactly the day the old code became
    exploitable, and the day nothing would have said so.
    """
    client_address.reset_for_tests()
    _resolve(xff="203.0.113.7", peer="10.0.0.1")
    _resolve(xff="198.51.100.88, 203.0.113.7", peer="10.0.0.1")
    counts = client_address.stats()["element_counts"]
    assert counts.get(1) == 1 and counts.get(2) == 1, counts
    client_address.reset_for_tests()


def test_the_chain_length_map_cannot_be_grown_without_bound_by_a_caller():
    """The caller chooses the chain length, so an unbounded key space here is a
    memory-growth vector reachable from the internet -- the defect
    security_guard.BUCKETS already has, not one to add."""
    client_address.reset_for_tests()
    for n in range(1, 60):
        _resolve(xff=", ".join(["203.0.113.7"] * n), peer="10.0.0.1")
    counts = client_address.stats()["element_counts"]
    assert len(counts) <= 17, f"element_counts grew to {len(counts)} keys"
    assert -1 in counts, "overflow lengths must be folded into one bucket, not dropped"
    client_address.reset_for_tests()


# --- 4b. The observable has to be readable, and has to stay quiet -------------
#
# A counter nobody reads is the same defect as a shadow mode nobody can read:
# an off switch with extra steps. These cover the state an operator actually
# sees, including the case that must NOT light it up.


def test_edge_status_is_absent_rather_than_ok_before_anything_is_observed():
    """A worker that has resolved nothing has verified nothing.

    The Operations Center renders an omitted service neutral, and reporting
    "ok" here would be a fabricated green light -- the same defect as the
    hard-coded `"ok": True` removed from /health, and the no-data exit code the
    shadow report keeps distinct from success.
    """
    client_address.reset_for_tests()
    _clear_env()
    assert client_address.edge_status() is None
    client_address.reset_for_tests()


def test_a_short_chain_raises_the_edge_status():
    """The condition is operator-actionable and client-unreachable.

    A client cannot shorten the chain -- every proxy appends -- so this can only
    mean the hop count is wrong or the origin is reachable past the edge. That
    is what makes it safe to alert on: nobody can spam it from outside.
    """
    client_address.reset_for_tests()
    _resolve(xff="198.51.100.88", peer="10.0.0.1", hops=2)
    status = client_address.edge_status()
    assert status is not None and status["state"] == "warn", status
    assert status["counters"]["short_chain"] == 1, status
    client_address.reset_for_tests()


def test_zero_hops_while_something_is_forwarding_raises_the_edge_status():
    """hops=0 says "nothing is in front of me". Forwarded elements arriving say
    otherwise, and if something *is* in front, the peer address is that thing --
    so every request in the fleet shares one rate-limit bucket."""
    client_address.reset_for_tests()
    _resolve(xff="203.0.113.7", peer="10.0.0.1", hops=0)
    status = client_address.edge_status()
    assert status is not None and status["state"] == "warn", status
    client_address.reset_for_tests()


def test_a_deliberately_direct_deployment_does_not_warn():
    """hops=0 with no forwarded elements is a correct configuration, not a
    finding. Warning on it would make the light permanent for anyone running
    this without a proxy."""
    client_address.reset_for_tests()
    _resolve(xff=None, peer="203.0.113.7", hops=0)
    status = client_address.edge_status()
    assert status is not None and status["state"] == "ok", status
    client_address.reset_for_tests()


def test_a_chain_longer_than_the_hop_count_does_not_raise_a_permanent_warning():
    """The case that must stay quiet, and the reason this is a counter.

    On an appending edge a chain longer than the hop count is every client that
    sends its own X-Forwarded-For -- i.e. constant, unavoidable, and chosen by
    strangers. Warning on it would pin the light yellow forever on half the
    possible deployments, and a light that is always yellow is a light nobody
    reads. The shape stays visible in element_counts, where an operator can look
    at the distribution instead of being paged by it.
    """
    client_address.reset_for_tests()
    _resolve(xff="198.51.100.88, 203.0.113.7", peer="10.0.0.1")  # hops defaults to 1
    status = client_address.edge_status()
    assert status is not None and status["state"] == "ok", status
    assert status["counters"]["element_counts"].get(2) == 1, (
        "the longer chain still has to be visible in the distribution")
    client_address.reset_for_tests()


def test_production_shape_is_ok():
    """One element, one trusted hop: what Railway presents today."""
    client_address.reset_for_tests()
    _resolve(xff="203.0.113.7", peer="10.0.0.1")
    status = client_address.edge_status()
    assert status is not None and status["state"] == "ok", status
    client_address.reset_for_tests()


def test_the_status_counts_resolutions_and_does_not_call_them_requests():
    """These counters count resolver calls, and one request makes several.

    `client_ip_hash()` is called from 53 places in bot.py and up to three times
    in a single pass through `basic_abuse_guard`. An operator message that says
    "3 requests" when one request arrived is a small lie of exactly the kind
    this whole surface exists to prevent, and it is the kind that survives
    review because the number is real -- it is only the noun that is wrong.
    """
    client_address.reset_for_tests()
    for _ in range(3):
        _resolve(xff="198.51.100.88", peer="10.0.0.1", hops=2)
    status = client_address.edge_status()
    assert status["counters"]["short_chain"] == 3, status
    assert "resolution" in status["detail"], status["detail"]
    assert "request" not in status["detail"], (
        f"the status calls resolutions requests: {status['detail']!r}")
    client_address.reset_for_tests()


def test_the_edge_status_is_admin_gated_and_not_on_a_public_surface():
    """This answers "how long is the forwarded chain here?", which is most of
    the way to answering "does X-Forwarded-For forgery work on this
    deployment?" -- the question the counter exists to detect someone asking.
    Publishing it on /health would hand over the reconnaissance for free."""
    assert BOT.count("client_address.edge_status()") == 1, (
        "edge_status() is read in more than one place; each one needs its own "
        "gate reviewed")
    call = BOT.index("client_address.edge_status()")
    enclosing = BOT.rindex("\ndef ", 0, call)
    name = BOT[enclosing + 5:BOT.index("(", enclosing)]
    assert name == "admin_ops_status_json", f"edge_status() moved into {name}"
    assert "admin_login_required()" in BOT[enclosing:call], (
        f"{name} reads the edge status before checking it is an admin")


def test_the_edge_status_has_somewhere_to_render():
    """A key in a JSON payload that no chip reads is still unreadable. The
    status strip is server-rendered with a fixed set of data-svc nodes, so the
    service key alone would paint nothing."""
    assert "data-svc='edge'" in BOT, (
        "services['edge'] is reported but the Operations Center strip has no "
        "chip for it, so nothing renders it")


# --- 5. bot.py must not grow the old expression back --------------------------

def test_bot_no_longer_reads_the_leftmost_forwarded_element():
    """The literal that was present four times and is the whole defect."""
    pattern = re.compile(
        r'headers\.get\(\s*["\']X-Forwarded-For["\'].*?\)\s*\.split\(\s*["\'],["\']\s*\)\s*\[\s*0\s*\]')
    assert not pattern.search(BOT), (
        "bot.py reads the leftmost X-Forwarded-For element again. On an "
        "appending edge that is a caller-chosen rate-limit subject."
    )


def test_bot_resolves_addresses_through_the_one_module():
    for site in ("def client_ip_address(", "def client_ip_hash("):
        start = BOT.index(site)
        body = BOT[start:start + 1800]
        assert "client_address.client_ip(" in body, f"{site} bypasses the resolver"


def test_no_country_header_is_read_outside_the_resolver():
    """Three call sites read geo headers directly, two of them bypassing
    request_country() entirely -- which is how a "fixed" resolver can still
    leave the injection open."""
    for header in ("CF-IPCountry", "X-Country-Code", "X-Appengine-Country",
                   "CloudFront-Viewer-Country", "X-Country", "X-Region", "X-City"):
        assert f'headers.get("{header}")' not in BOT and \
               f'headers.get("{header}",' not in BOT, (
            f"bot.py reads {header} off the request again. Nothing in front of "
            f"this deployment sets it, so its value is whatever the caller typed."
        )


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
