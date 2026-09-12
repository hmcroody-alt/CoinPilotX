"""Paid search queries must land in the month's spend report.

§22, applied to the one kind of AI spend that has no tokens. Four of the five
providers in `services/pulse_ai_web_search.py` are `paid=True` in
`undx_capabilities` and not one of them had ever appeared in a cost report,
because the ledger only ever recorded chat and chat is measured in tokens. A
search query is measured in queries, so it was invisible by construction rather
than by anyone's decision.

The distinction these tests exist to protect is **billed** versus **useful**.
This module's `ok` flag means "results were found", and metering on it would have
undercounted exactly the queries that get retried: a vendor charges for an
accepted query whether or not it had anything to say. So the metering hangs off
the HTTP status, and the tests below pin both halves of that — a 2xx with an
empty body is a charge, a 429 is not.

Every test pins `DATABASE_URL` at a temp file. `services.db` falls back to the
relative path `coinpilotx.db` when it is unset, so a ledger assertion without
this is really an assertion about how many times the suite has been run on this
machine, and it leaves rows behind in the developer's dev database.
"""

from __future__ import annotations

import json

import pytest

from services import undx_capabilities as cap
from services import pulse_ai_web_search as web


#: `should_search` has to say yes or `search()` returns `not_needed` without
#: calling anybody. "price" is in FRESHNESS_TERMS.
QUERY = "current bitcoin price"

ALL_KEYS = {
    "BRAVE_SEARCH_API_KEY": "brave-key",
    "BING_SEARCH_API_KEY": "bing-key",
    "SERPAPI_API_KEY": "serpapi-key",
    "TAVILY_API_KEY": "tavily-key",
}


class FakeResponse:
    def __init__(self, status_code: int, body, *, raw: str | None = None):
        self.status_code = status_code
        self._body = body
        self._raw = raw

    def json(self):
        if self._raw is not None:
            return json.loads(self._raw)  # raises ValueError, as requests does
        return self._body


def brave_body(count: int = 1):
    return {"web": {"results": [{"title": f"t{i}", "url": f"https://x/{i}", "description": "d"} for i in range(count)]}}


def ddg_body(count: int = 1):
    return {
        "AbstractText": "abstract" if count else "",
        "AbstractURL": "https://duckduckgo.com/a" if count else "",
        "Heading": "H",
    }


@pytest.fixture()
def ledger(monkeypatch, tmp_path):
    """Isolate the cost ledger, the provider keys, and the module cache."""
    from services import undx_cost

    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "ledger.db"))
    # Every key cleared by default, so a test that wants a provider to be called
    # has to say so. Otherwise the developer's own shell environment decides which
    # providers the fallback chain reaches, and the test means something different
    # on every machine.
    for name in ("BRAVE_SEARCH_API_KEY", "BING_SEARCH_API_KEY",
                 "BING_SEARCH_V7_SUBSCRIPTION_KEY", "SERPAPI_API_KEY",
                 "TAVILY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    web._CACHE.clear()
    undx_cost.reset_for_tests()
    yield undx_cost
    web._CACHE.clear()
    undx_cost.reset_for_tests()


def _kinds(undx_cost):
    snapshot = undx_cost.month_snapshot()
    assert snapshot["source"] == "ledger"
    return snapshot


def _fake_http(monkeypatch, responses: dict[str, FakeResponse]):
    """Route by URL fragment so the fallback chain stays real.

    Stubbing `requests.get`/`post` rather than the `_search_*` functions on
    purpose: the functions are where the status check and the metering call live,
    and replacing them would delete the thing under test.
    """
    def pick(url, **_kwargs):
        for fragment, response in responses.items():
            if fragment in url:
                return response
        raise AssertionError(f"unexpected request to {url}")

    monkeypatch.setattr(web.requests, "get", pick)
    monkeypatch.setattr(web.requests, "post", pick)


# ---------------------------------------------------------------------------


def test_a_successful_query_is_recorded_as_research_not_chat(monkeypatch, ledger):
    """The laundering §22 forbids. Folded into chat this would be invisible,
    because chat is the number everyone already reads and it is meant to be big."""
    _fake_http(monkeypatch, {"duckduckgo.com": FakeResponse(200, ddg_body())})
    assert web.search(QUERY)["ok"] is True

    snapshot = _kinds(ledger)
    assert snapshot["kinds"]["research"]["calls"] == 1
    assert "chat" not in snapshot["kinds"]
    assert "duckduckgo_instant" in snapshot["providers"]


def test_a_query_that_found_nothing_is_still_a_query_we_paid_for(monkeypatch, ledger):
    """The central case, and the one metering-on-success would have missed.

    Brave bills per accepted query. A 200 carrying zero results is Brave doing
    the job it was paid for and reporting that the web has nothing — and because
    this module's `ok` flag means *results found*, the natural place to put the
    metering call would have skipped it. These are also the queries most likely to
    be retried, so the undercount would have been worst exactly where spend is
    highest.
    """
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-key")
    _fake_http(monkeypatch, {
        "search.brave.com": FakeResponse(200, brave_body(0)),
        "duckduckgo.com": FakeResponse(200, ddg_body()),
    })
    web.search(QUERY)

    providers = _kinds(ledger)["providers"]
    assert providers["brave"]["calls"] == 1
    assert providers["duckduckgo_instant"]["calls"] == 1


def test_a_rejected_query_is_not_recorded_as_spend(monkeypatch, ledger):
    """The other half. 401/429/5xx are refusals, and counting them would inflate
    the month with queries nobody was billed for — the mirror-image error of the
    test above, and the reason the metering hangs off the status code rather than
    off "a request was attempted"."""
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-key")
    _fake_http(monkeypatch, {
        "search.brave.com": FakeResponse(429, {}),
        "duckduckgo.com": FakeResponse(200, ddg_body()),
    })
    web.search(QUERY)

    providers = _kinds(ledger)["providers"]
    assert "brave" not in providers
    assert providers["duckduckgo_instant"]["calls"] == 1


def test_an_unconfigured_provider_is_not_billed(ledger, monkeypatch):
    """No key means the function returns before `requests.get`, so there is
    nothing to bill. Asserted because a metering call placed at the top of a
    `_search_*` function would charge for a request that never left the process —
    and with four unconfigured providers in the chain, that is four phantom
    queries per real search."""
    _fake_http(monkeypatch, {"duckduckgo.com": FakeResponse(200, ddg_body())})
    web.search(QUERY)

    providers = _kinds(ledger)["providers"]
    assert set(providers) == {"duckduckgo_instant"}


def test_a_paid_provider_with_no_published_price_is_uncosted_not_free(monkeypatch, ledger):
    """§34: unknown is not $0.00.

    None of the four paid search vendors has a price in the table, so a query
    records as "one call happened, we do not know what it cost". Recording $0.00
    would make the month's dollar total look complete while being wrong by
    however many cents a Brave query costs, and `unpriced_providers()` — which
    still names all four — is the list that enumerates the remaining work.
    """
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-key")
    _fake_http(monkeypatch, {"search.brave.com": FakeResponse(200, brave_body())})
    web.search(QUERY)

    row = _kinds(ledger)["providers"]["brave"]
    assert (row["calls"], row["cost_micro_usd"], row["uncosted_calls"]) == (1, 0, 1)
    assert (cap.CALL_KIND_RESEARCH, "brave") in cap.unpriced_providers()


def test_duckduckgo_is_a_measured_zero_and_not_an_unknown(monkeypatch, ledger):
    """The distinction §34 turns on, in the one place this repo can actually
    demonstrate it.

    DuckDuckGo's instant-answer endpoint is keyless and charges nothing, so its
    `0.0` is a measurement, not a missing entry. It therefore has to land as
    `uncosted_calls=0` — same dollar figure as Brave's row, different meaning —
    or the number that counts "spend we cannot price" would include the one
    provider we can price with certainty.
    """
    _fake_http(monkeypatch, {"duckduckgo.com": FakeResponse(200, ddg_body())})
    web.search(QUERY)

    row = _kinds(ledger)["providers"]["duckduckgo_instant"]
    assert (row["calls"], row["cost_micro_usd"], row["uncosted_calls"]) == (1, 0, 0)
    assert (cap.CALL_KIND_RESEARCH, "duckduckgo_instant") not in cap.unpriced_providers()


def test_a_query_billed_before_a_later_provider_succeeded_is_still_recorded(monkeypatch, ledger):
    """Fallback does not refund the attempt that came first.

    The same property §41 requires of provider health: a request that succeeded
    at the end of the chain must not erase what the earlier hops cost. Three paid
    providers answer 200-empty here and the fourth hop is the one that returns
    results, so a report showing one query would be understating the search bill
    by 3x on every one of these searches.
    """
    for name, value in ALL_KEYS.items():
        monkeypatch.setenv(name, value)
    _fake_http(monkeypatch, {
        "search.brave.com": FakeResponse(200, brave_body(0)),
        "api.bing.microsoft.com": FakeResponse(200, {}),
        "serpapi.com": FakeResponse(200, {}),
        "api.tavily.com": FakeResponse(200, {"results": [{"title": "t", "url": "https://x", "content": "c"}]}),
    })
    assert web.search(QUERY)["provider"] == "tavily"

    snapshot = _kinds(ledger)
    assert snapshot["kinds"]["research"]["calls"] == 4
    assert set(snapshot["providers"]) == {"brave", "bing", "serpapi", "tavily"}
    assert snapshot["kinds"]["research"]["uncosted_calls"] == 4


def test_a_two_hundred_whose_body_will_not_parse_is_still_billed(monkeypatch, ledger):
    """Parsing is our problem, not the vendor's.

    This is why the metering call sits *above* `response.json()`. A 200 we cannot
    read is a query the vendor accepted, answered and charged for; dropping it
    would mean a provider having a bad serialization day looked like a provider
    we had stopped using.
    """
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-key")
    _fake_http(monkeypatch, {
        "search.brave.com": FakeResponse(200, None, raw="{not json"),
        "duckduckgo.com": FakeResponse(200, ddg_body()),
    })
    assert web.search(QUERY)["ok"] is True

    assert _kinds(ledger)["providers"]["brave"]["calls"] == 1


def test_a_cache_hit_is_not_a_second_charge(monkeypatch, ledger):
    """The cache exists so repeated questions do not cost twice, and the ledger
    has to agree with it. If a served-from-cache answer were metered, the report
    would measure how often users ask rather than how much we spend."""
    _fake_http(monkeypatch, {"duckduckgo.com": FakeResponse(200, ddg_body())})
    web.search(QUERY)
    assert web.search(QUERY)["cache_hit"] is True

    assert _kinds(ledger)["kinds"]["research"]["calls"] == 1


def test_a_query_never_sent_records_nothing(monkeypatch, ledger):
    """A query that fails `should_search` reaches no vendor, so it is not spend.
    Pinned because "count the searches" and "count the search requests" are easy
    to conflate, and only one of them is a bill."""
    def refuse(*_a, **_k):
        raise AssertionError("no request should have been made")

    monkeypatch.setattr(web.requests, "get", refuse)
    monkeypatch.setattr(web.requests, "post", refuse)
    assert web.search("hello there")["error"] == "not_needed"

    assert _kinds(ledger)["kinds"] == {}


def test_an_unreachable_ledger_does_not_cost_the_caller_its_results(monkeypatch, ledger):
    """Metering is observation, not a precondition.

    Broken by killing the database connection rather than by stubbing
    `record_spend` to raise: that function is documented never to raise, so a
    test faking it would exercise an impossible state. The in-process mirror
    still answers, which is what separates degraded from silent.
    """
    import sqlite3

    _fake_http(monkeypatch, {"duckduckgo.com": FakeResponse(200, ddg_body())})
    monkeypatch.setattr(
        ledger, "_connect",
        lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.OperationalError("gone")),
    )
    assert web.search(QUERY)["ok"] is True

    snapshot = ledger.month_snapshot()
    assert snapshot["source"] != "ledger"
    assert snapshot["kinds"]["research"]["calls"] == 1
