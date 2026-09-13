"""Google Cloud Translation requests must land in the month's spend report.

§22, applied to the last of the four non-chat call sites. Google bills per
character of submitted text, and until this wiring existed the only trace of a
translation was the HTTP log — so a month that translated ten million characters
and a month that translated none produced identical spend reports.

Two properties here are worth more than the rest, because both are invisible in a
report that only shows totals:

* **The billed string is the input, not the output.** Translated text is routinely
  30-40% longer than its source. Metering the response would have overstated spend
  by a margin that grows with the language pair — worst on the pairs used most.
* **Detection is billed too.** A detect-then-translate flow on unknown-language
  text pays twice. Metering only `translate()` would have made the cheaper-looking
  path the more expensive one.

Neither is observable through the real price table, because
`('translation', 'google')` has no price and every call therefore records $0.00
with `uncosted_calls=1`. So the character-count tests install a rate for the
duration of the test. That is not a workaround: it exercises the real pricing path
with a real rate shape, and it means the plumbing is already proven the day
someone reads Google's published figure into the table.
"""

from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from services import undx_capabilities as cap
from services import translation_providers as tp


#: $20.00 per million characters. Not Google's real rate — a deliberately round
#: number so the arithmetic in an assertion is readable, and deliberately not
#: close to any published figure so nobody mistakes it for one.
TEST_RATE_USD_PER_M = 20.0

SOURCE = "x" * 1000              # 1000 characters in
LONGER_OUTPUT = "y" * 3000       # 3000 characters back, to make the wrong answer loud
MICRO_FOR_SOURCE = 20_000        # 1000 chars at $20/M
MICRO_FOR_OUTPUT = 60_000        # what billing the response would have produced


class Response:
    def __init__(self, body, status_code: int = 200):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class Session:
    """Records every outbound request and replies with a queued response."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def request(self, method, url, **_kwargs):
        self.calls.append((method, url))
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]


def _adapter(session) -> tp.GoogleAdvancedProvider:
    return tp.GoogleAdvancedProvider(
        tp.GoogleConfig(project_id="qa-project", api_key="sealed-test-key", max_retries=0),
        session=session,
    )


def translate_body(text: str = "Hola", detected: str = "en"):
    return {"translations": [{"translatedText": text, "detectedLanguageCode": detected}]}


@pytest.fixture()
def ledger(monkeypatch, tmp_path):
    from services import undx_cost

    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "ledger.db"))
    undx_cost.reset_for_tests()
    yield undx_cost
    undx_cost.reset_for_tests()


@pytest.fixture()
def priced(monkeypatch):
    """Give `('translation', 'google')` a rate for the duration of one test.

    Patching `provider_for` rather than mutating `CAPABILITIES`: the table holds
    frozen dataclasses inside a module-level dict, and editing it in place would
    leak into every test that ran afterwards in the same process.
    """
    real = cap.provider_for

    def priced_provider_for(kind, provider):
        entry = real(kind, provider)
        if entry is not None and kind == cap.CALL_KIND_TRANSLATION and entry.name == "google":
            return dataclasses.replace(entry, prices={"": TEST_RATE_USD_PER_M})
        return entry

    monkeypatch.setattr(cap, "provider_for", priced_provider_for)


def _kinds(undx_cost):
    snapshot = undx_cost.month_snapshot()
    assert snapshot["source"] == "ledger"
    return snapshot


# ---------------------------------------------------------------------------


def test_a_translation_is_recorded_as_translation_not_chat(ledger):
    """The laundering §22 forbids. Google also serves chat models elsewhere in this
    repo, so a translation folded into `chat` would sit in a plausible-looking row
    on a provider that really does have chat spend — the hardest kind to notice."""
    result = _adapter(Session(Response(translate_body()))).translate("Hello", "auto", "es")
    assert result["translated_text"] == "Hola"

    snapshot = _kinds(ledger)
    assert snapshot["kinds"]["translation"]["calls"] == 1
    assert "chat" not in snapshot["kinds"]
    assert "google" in snapshot["providers"]


def test_an_unpriced_translation_is_uncosted_not_free(ledger):
    """§34 against the real table.

    Google's per-million-character rate is public but has not been read and dated
    into `undx_capabilities`, so a translation records as "one call happened, we do
    not know what it cost". Recording $0.00 would make the month look complete
    while being wrong by the entire translation bill.
    """
    _adapter(Session(Response(translate_body()))).translate(SOURCE, "en", "es")

    row = _kinds(ledger)["kinds"]["translation"]
    assert (row["calls"], row["cost_micro_usd"], row["uncosted_calls"]) == (1, 0, 1)
    assert (cap.CALL_KIND_TRANSLATION, "google") in cap.unpriced_providers()


def test_the_billed_characters_are_the_ones_we_sent(ledger, priced):
    """The input is what Google meters, and the output is 3x longer here so the
    wrong answer cannot hide inside rounding.

    This is the defect that would have been hardest to find in production: billing
    the response overstates spend by whatever the expansion ratio of the language
    pair happens to be, which means the error is largest on the pairs used most and
    varies by locale, so no single wrong number ever appears twice.
    """
    _adapter(Session(Response(translate_body(LONGER_OUTPUT)))).translate(SOURCE, "en", "de")

    row = _kinds(ledger)["kinds"]["translation"]
    assert row["cost_micro_usd"] == MICRO_FOR_SOURCE
    assert row["cost_micro_usd"] != MICRO_FOR_OUTPUT
    assert row["uncosted_calls"] == 0


def test_html_markup_counts_as_characters(ledger, priced):
    """Google does not discount markup and neither does this.

    Stripping tags before counting would produce a tidier number and a wrong bill.
    Asserted with markup that is most of the payload, so a tag-stripping
    implementation could not pass by accident.
    """
    html = "<p><strong>" + ("z" * 100) + "</strong></p>" + ("<br/>" * 100)
    _adapter(Session(Response(translate_body()))).translate(html, "en", "fr", mime_type="text/html")

    expected = int(round(len(html) / 1_000_000 * TEST_RATE_USD_PER_M * 1_000_000))
    assert _kinds(ledger)["kinds"]["translation"]["cost_micro_usd"] == expected


def test_language_detection_is_billed_on_the_same_footing(ledger, priced):
    """Detection is per-character at the same rate, so it is metered the same way.

    Omitting it would make detect-then-translate — the path taken for every piece
    of unknown-language content — look half as expensive as it is.
    """
    session = Session(Response({"languages": [{"languageCode": "en", "confidence": 0.98}]}))
    _adapter(session).detect_language(SOURCE)

    row = _kinds(ledger)["kinds"]["translation"]
    assert (row["calls"], row["cost_micro_usd"]) == (1, MICRO_FOR_SOURCE)


def test_detect_then_translate_pays_twice(ledger, priced):
    """The flow the test above exists to protect, asserted end to end: two accepted
    requests over the same 1000 characters is two charges, not one."""
    _adapter(Session(Response({"languages": [{"languageCode": "de"}]}))).detect_language(SOURCE)
    _adapter(Session(Response(translate_body()))).translate(SOURCE, "de", "en")

    row = _kinds(ledger)["kinds"]["translation"]
    assert (row["calls"], row["cost_micro_usd"]) == (2, MICRO_FOR_SOURCE * 2)


def test_a_refused_request_is_not_billed(ledger):
    """401/403 raises before the metering call. Counting refusals would inflate the
    month with requests nobody was charged for, and the inflation would be worst
    during a credentials outage — exactly when the number gets read."""
    with pytest.raises(tp.ProviderError) as exc:
        _adapter(Session(Response({}, status_code=403))).translate(SOURCE, "en", "es")
    assert exc.value.code == "invalid_credentials"

    assert _kinds(ledger)["kinds"] == {}


def test_an_unconfigured_provider_is_not_billed(ledger):
    """No project id means `_request` raises before any HTTP call, so there is
    nothing to bill. Pinned because a metering call placed at the top of
    `translate()` would charge for a request that never left the process."""
    adapter = tp.GoogleAdvancedProvider(tp.GoogleConfig(project_id=""))
    with pytest.raises(tp.ProviderError) as exc:
        adapter.translate("Hello", "en", "fr")
    assert exc.value.code == "provider_not_configured"

    assert _kinds(ledger)["kinds"] == {}


def test_a_retried_request_is_billed_once(ledger, priced):
    """Two 503s then a 200 is one accepted request, so it is one charge.

    The retry loop lives below the metering call precisely so this holds. Metering
    inside the loop would have multiplied the bill by the provider's flakiness,
    which is the opposite of what a cost report is for.
    """
    session = Session(Response({}, 503), Response({}, 503), Response(translate_body()))
    adapter = tp.GoogleAdvancedProvider(
        tp.GoogleConfig(project_id="qa-project", api_key="k", max_retries=2),
        session=session,
    )
    adapter.translate(SOURCE, "en", "es")

    assert len(session.calls) == 3
    row = _kinds(ledger)["kinds"]["translation"]
    assert (row["calls"], row["cost_micro_usd"]) == (1, MICRO_FOR_SOURCE)


def test_a_two_hundred_with_no_usable_translation_is_still_billed(ledger, priced):
    """`invalid_provider_response` is our judgement about the body, not Google's
    about the bill. A 200 carrying an empty `translatedText` was still an accepted
    request, and dropping it would make a provider having a bad day look like a
    provider we had stopped using while they kept invoicing."""
    with pytest.raises(tp.ProviderError) as exc:
        _adapter(Session(Response({"translations": [{"translatedText": "   "}]}))).translate(SOURCE, "en", "es")
    assert exc.value.code == "invalid_provider_response"

    assert _kinds(ledger)["kinds"]["translation"]["cost_micro_usd"] == MICRO_FOR_SOURCE


def test_listing_supported_languages_is_not_translation_spend(ledger):
    """`getSupportedLanguages` is free and is not a translation.

    Recording it would corrupt the one thing the durable row does carry. The ledger
    keys on (month, provider, call_kind) with no operation dimension, so a free
    metadata lookup counted here would be indistinguishable from a paid
    translation in the call count — and that count is currently the *only* signal,
    because the price is unknown and the character volume is not persisted.
    """
    session = Session(Response({"languages": [{"languageCode": "es", "supportTarget": True}]}))
    assert _adapter(session).supported_languages()[0]["code"] == "es"

    assert _kinds(ledger)["kinds"] == {}


def test_an_unreachable_ledger_does_not_cost_the_caller_its_translation(monkeypatch, ledger):
    """Metering is observation, not a precondition.

    Broken at the database connection rather than by stubbing `record_spend` to
    raise — that function is documented never to raise, so faking it would exercise
    an impossible state. The in-process mirror still answers, which is what
    separates degraded from silent.
    """
    monkeypatch.setattr(
        ledger, "_connect",
        lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.OperationalError("gone")),
    )
    result = _adapter(Session(Response(translate_body()))).translate(SOURCE, "en", "es")
    assert result["translated_text"] == "Hola"

    snapshot = ledger.month_snapshot()
    assert snapshot["source"] != "ledger"
    assert snapshot["kinds"]["translation"]["calls"] == 1
