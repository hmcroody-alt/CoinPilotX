"""The server end of the price-label parity contract.

One price label is read twice: once in TypeScript, to put an amount on the Pay
button, and once here in Python, to build the Stripe charge. Two implementations
of one rule drift, and these two had — the app's regex did not consume thousands
separators, so a ``$12,345.67`` listing offered to charge $12.00 and then charged
$12,345.67. The app's own comment said the two were "the same regex".

So the claim stopped being a comment. Both sides now read
``mobile-native/src/api/__tests__/fixtures/priceLabelParity.json``: this file
checks the table against ``bot.parse_price_label_to_cents``, and
``marketplaceCheckoutIntegrity.test.ts`` checks it against
``marketplaceListingPriceMinor``. Changing either parser alone turns one of the
two suites red, and changing the table alone turns both red.

``bot`` is not imported. Importing the monolith starts its workers, and this
contract is about twenty lines of it; the two functions under test are read out
of the source and executed on their own, which is also why a stray import added
to them later would surface here as a failure rather than as a silent pass.
"""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "mobile-native/src/api/__tests__/fixtures/priceLabelParity.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _load_price_functions():
    """Execute ``parse_price_label_to_cents`` and its constants, and nothing else."""
    source = ROOT.joinpath("bot.py").read_text(encoding="utf-8")
    namespace = {"re": re, "Decimal": Decimal}
    for constant in ("PRICE_LABEL_UNPRICED = ", "MAX_PRICE_LABEL_CENTS = "):
        start = source.index(constant)
        exec(source[start:source.index("\n", start)], namespace)  # noqa: S102
    for function in ("def parse_price_label_to_cents", "def marketplace_normalize_price_label"):
        start = source.index(function)
        exec(source[start:source.index("\n\n\ndef ", start)], namespace)  # noqa: S102
    return namespace


PRICE = _load_price_functions()


@pytest.mark.parametrize("case", CASES, ids=[c["label"] or "<blank>" for c in CASES])
def test_the_server_reads_each_label_as_the_app_promises(case):
    cents, _currency = PRICE["parse_price_label_to_cents"](case["label"], "USD")
    # The app returns null where the server returns 0; both then decline to show
    # the buyer a figure, which is the behaviour the two agree on.
    expected = case["minor"] if case["minor"] is not None else 0
    assert cents == expected, (
        f"{case['label']!r} ({case['why']}): the app would show "
        f"{case['minor']} minor units and the card would be charged {cents}"
    )


def test_the_server_writes_the_separators_the_app_has_to_read():
    """Parity on thousands separators is not hypothetical: the server emits them.

    ``marketplace_normalize_price_label`` formats with ``,``, so every listing
    priced at or above $1,000 is stored in the format the app used to misread.
    If this ever stops being true the parity cases above become academic, and
    this test says so rather than letting them look load-bearing.
    """
    label, cents, _currency, error = PRICE["marketplace_normalize_price_label"]("12345.67", "USD")
    assert error == ""
    assert label == "$12,345.67"
    assert cents == 1234567


def test_every_case_states_why_it_is_in_the_table():
    """A parity table is only as useful as the reason each row was chosen.

    Without this, a row deleted because it failed looks identical to a row that
    was never there.
    """
    assert len(CASES) >= 16
    for case in CASES:
        assert case["why"].strip(), f"{case['label']!r} has no stated reason"
