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
