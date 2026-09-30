"""Two copy implementations, one vocabulary — asserted against the TypeScript source.

``services/delivery/copy.py`` says, in its own docstring, that this file exists:
"there are two implementations and a **contract test that compares them**:
``tests/delivery/test_delivery_copy.py`` reads the TypeScript source and asserts
that every sentence, every reason grouping and every tone name in it is the same
as the ones here." Without this file that paragraph is a promise about a test, and
the duplication it justifies is just duplication.

Why the duplication is deliberate is argued in both modules and not re-argued
here. What matters for a test is the failure it catches, and it is a specific
one: **the same parcel described differently on the web and in the app.** A
sentence edited on one side is not a crash, not a type error and not a lint
failure. It ships. The two surfaces are seen together constantly — a buyer opens
a shared product link, then opens the app; a reviewer puts a store screenshot
beside the web page — and "arranged with them after your order is confirmed" on
one with "arranged with the seller" on the other is exactly the class of drift
that made this mission necessary in the first place.

So the comparison is **byte-for-byte over the strings a buyer reads**, including
the EN DASH in the range separator and the ellipsis character in the loading
line, because a hyphen on one side and an en dash on the other is invisible until
the two are side by side.

How it reads the TypeScript
---------------------------
Regex over the source, with comments stripped first. Not a JS runtime and not a
JSON export generated at build time:

* A runtime (node, a bundler) would make this test depend on the mobile app's
  toolchain being installed in the backend's CI job, which it is not.
* A generated JSON artifact would be a third copy of the vocabulary, and the
  generator could go stale exactly as quietly as the strings can.

Comments are stripped before matching because both files *quote* the sentences in
their prose while explaining them — a naive substring search over the raw file
passes on the documentation rather than on the code. Every extractor below
asserts it found something, so a renamed constant fails loudly instead of
comparing two empty collections and passing.
"""

import os
import pathlib
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.delivery import copy as delivery_copy  # noqa: E402
from services.delivery import estimate, quote  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
TS_COPY = REPO / "mobile-native" / "src" / "components" / "commerce" / "deliveryCopy.ts"
TS_API = REPO / "mobile-native" / "src" / "api" / "delivery.ts"


def source(path: pathlib.Path) -> str:
    """The file with its comments removed.

    ``/* … */`` first, then ``// …`` to end of line. Crude, and safe for these two
    files: a ``//`` inside a string literal would be mis-stripped, and neither
    file contains one — the only strings in them are the buyer-facing sentences
    this test is about.
    """
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)//.*$", "", text)


def block(text: str, opener: str) -> str:
    """The body of an object literal or call that starts with ``opener``."""
    start = text.index(opener) + len(opener)
    depth = 1
    for offset in range(start, len(text)):
        char = text[offset]
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
            if depth == 0:
                return text[start:offset]
    raise AssertionError("unbalanced literal after %r" % opener)


def string_table(body: str) -> dict:
    """``key: "value"`` pairs from an object-literal body, including empty values."""
    return {key: value for key, value in
            re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"((?:[^"\\]|\\.)*)"', body)}


def string_set(body: str) -> set:
    return set(re.findall(r'"([^"]*)"', body))


@pytest.fixture(scope="module")
def ts() -> str:
    return source(TS_COPY)


@pytest.fixture(scope="module")
def ts_api() -> str:
    return source(TS_API)


# ---------------------------------------------------------------------------
# The tones
# ---------------------------------------------------------------------------

def test_the_four_tone_names_are_the_same_on_both_sides(ts):
    """The tone is on the wire in neither direction — it is computed twice, from
    the same estimate. A fifth tone on one side, or a renamed one, means a caller
    branching on a value the other side never produces."""
    # A union type, not an object literal, so it ends at the semicolon rather than
    # at a closing brace.
    union = ts.split("export type DeliveryTone =")[1].split(";")[0]
    declared = re.findall(r'"(\w+)"', union)
    assert declared, "DeliveryTone stopped being a union of string literals"
    assert tuple(declared) == delivery_copy.TONES


def test_the_tone_default_sentences_are_identical(ts):
    found = string_table(block(ts, "const TONE_DEFAULTS: Record<DeliveryTone, string> = {"))
    assert found, "TONE_DEFAULTS was renamed or restructured"
    assert found == delivery_copy.TONE_DEFAULTS


# ---------------------------------------------------------------------------
# The reasons
# ---------------------------------------------------------------------------

def test_the_per_reason_sentences_are_identical(ts):
    """Keys included. The Python table is keyed by ``quote.REASON_*`` *values*, so
    this compares the wire tokens too: a reason renamed on the server without the
    app's table following would silently fall through to the generic retryable
    line on the app and print the specific sentence on the web."""
    found = string_table(block(ts, "const REASON_LINES: Record<string, string> = {"))
    assert found, "REASON_LINES was renamed or restructured"
    assert found == delivery_copy.REASON_LINES


def test_the_actionable_and_terminal_groupings_are_identical(ts):
    """Grouping, not wording: which reasons offer a retry control. A reason that is
    terminal on one surface and retryable on the other gives the same buyer a
    button on their phone that is absent in their browser, for the same listing.
    """
    actionable = string_set(block(ts, "const ACTIONABLE_REASONS = new Set("))
    terminal = string_set(block(ts, "const TERMINAL_REASONS = new Set("))
    assert actionable and terminal
    assert actionable == set(delivery_copy.ACTIONABLE_REASONS)
    assert terminal == set(delivery_copy.TERMINAL_REASONS)


def test_every_grouped_reason_is_a_reason_the_server_can_actually_send(ts):
    """A grouping is only as good as its tokens. Both sides can agree on a spelling
    that ``quote`` never emits, and the agreement would hide the typo."""
    emitted = {value for name, value in vars(quote).items()
               if name.startswith("REASON_") and isinstance(value, str)}
    grouped = set(delivery_copy.ACTIONABLE_REASONS) | set(delivery_copy.TERMINAL_REASONS)
    grouped |= set(delivery_copy.REASON_LINES)
    assert grouped <= emitted, sorted(grouped - emitted)


# ---------------------------------------------------------------------------
# The sentences that are built rather than tabled
# ---------------------------------------------------------------------------

def test_the_window_sentence_is_assembled_the_same_way(ts):
    """The one sentence neither side stores whole. Compared as its literal
    template, because this is the line nearly every buyer reads."""
    assert "text: `Estimated delivery ${window}${suffix}`" in ts
    assert "Estimated delivery {window}{suffix}" == _python_window_template()
    assert "` to ${destination.country}`" in ts


def _python_window_template() -> str:
    """The server's window sentence with its two slots emptied.

    Derived by running the real function rather than by reading the source, so a
    change to the f-string is caught even if it keeps the same shape.
    """
    line = delivery_copy.delivery_copy(
        delivery={"state": estimate.STATE_ESTIMATED,
                  "earliest": "2026-03-16", "latest": "2026-03-16"},
        destination={"country": "US", "known": False})
    return line["text"].replace("16 Mar", "{window}") + "{suffix}"


def test_the_country_suffix_is_only_appended_to_a_known_destination(ts):
    """Both sides gate on ``known``, and this is the assertion that they gate on the
    *same* thing: an edge-header guess or the single-country policy corridor must
    not be printed as "to United States"."""
    assert "destination.known && destination.country" in ts
    known = delivery_copy.delivery_copy(
        delivery={"state": estimate.STATE_ESTIMATED,
                  "earliest": "2026-03-16", "latest": "2026-03-21"},
        destination={"country": "United States", "known": True})
    guessed = delivery_copy.delivery_copy(
        delivery={"state": estimate.STATE_ESTIMATED,
                  "earliest": "2026-03-16", "latest": "2026-03-21"},
        destination={"country": "United States", "known": False})
    assert known["text"].endswith(" to United States")
    assert "United States" not in guessed["text"]


def test_the_range_separator_is_the_same_bytes(ts_api):
    """Space, EN DASH (U+2013), space. A hyphen on one side is invisible until a
    screenshot from each is put side by side."""
    assert delivery_copy.RANGE_SEPARATOR == " – "
    assert "${from.split(\" \")[0]} – ${to}" in ts_api
    assert "${from} – ${to}" in ts_api


def test_the_month_abbreviations_are_the_same_table(ts_api):
    months = re.findall(r'"(\w{3})"', block(ts_api, "const MONTHS = ["))
    assert tuple(months) == delivery_copy.MONTHS


def test_the_window_formatters_agree_on_the_hard_cases():
    """Same month, month boundary, single day. Asserted against the literals the TS
    docstring uses as its own examples, so the two sets of examples cannot drift
    from the two implementations independently."""
    def window(earliest, latest):
        return delivery_copy.format_window({"state": estimate.STATE_ESTIMATED,
                                            "earliest": earliest, "latest": latest})
    assert window("2026-03-16", "2026-03-21") == "16 – 21 Mar"
    assert window("2026-03-28", "2026-04-02") == "28 Mar – 2 Apr"
    assert window("2026-03-16", "2026-03-16") == "16 Mar"
    # Unpadded day, matching `Number(day)` on the other side. "06 Mar" is what
    # `strftime("%d %b")` would have produced.
    assert window("2026-03-06", "2026-03-06") == "6 Mar"


def test_the_loading_sentence_is_the_same_bytes(ts):
    """Including the single-character ellipsis. This is the string a
    server-rendered product page carries before any fetch resolves, so it is read
    by more people than any other line here."""
    match = re.search(r'DELIVERY_LOADING_TEXT\s*=\s*"((?:[^"\\]|\\.)*)"', ts)
    assert match, "DELIVERY_LOADING_TEXT was renamed"
    assert match.group(1) == delivery_copy.LOADING_TEXT
    assert delivery_copy.LOADING_TEXT.endswith("…")
    assert delivery_copy.loading_copy()["text"] == delivery_copy.LOADING_TEXT


def test_the_free_shipping_line_and_its_trigger_are_the_same(ts):
    """The phrase §3/§32/§59 standardise, and the token that licenses it. Both sides
    compare the server's value rather than asserting the policy, so a change to the
    policy cannot leave a binary claiming free shipping."""
    assert 'const FREE_SHIPPING_LINE = "%s"' % delivery_copy.FREE_SHIPPING_LINE in ts
    assert 'const SHIPPING_FREE = "%s"' % delivery_copy.SHIPPING_FREE in ts
    assert delivery_copy.SHIPPING_FREE == quote.SHIPPING_FREE


# ---------------------------------------------------------------------------
# The prohibitions, on both sides at once
# ---------------------------------------------------------------------------

def test_neither_implementation_can_reach_the_word_guaranteed(ts):
    """§58, §125. Not "does not currently say it" — there is no branch that could.
    The estimate carries a ``guaranteed`` flag and neither module reads it, because
    no value of it would license the word."""
    python = source_of_python()
    for text in (ts, python):
        assert "Guaranteed" not in text
        assert "guaranteed" not in text


def test_every_state_and_every_reason_yields_a_sentence():
    """§ "never silence". The old blanket copy grew in the gap a failed estimate
    left behind, so the property is asserted over every state the estimator can
    return *and* an unrecognised reason, which is the case a one-to-one reason
    table would have rendered blank.
    """
    states = {value for name, value in vars(estimate).items()
              if name.startswith("STATE_") and isinstance(value, str)}
    assert states, "estimate stopped exporting STATE_* constants"
    reasons = {value for name, value in vars(quote).items()
               if name.startswith("REASON_") and isinstance(value, str)}
    reasons.add("a_reason_from_a_future_deployment")
    for state in states:
        for reason in reasons | {None}:
            line = delivery_copy.delivery_copy(
                delivery={"state": state, "reason": reason,
                          "earliest": "2026-03-16", "latest": "2026-03-21"},
                destination={"country": "US", "known": True})
            assert line["text"].strip(), (state, reason)
            assert line["tone"] in delivery_copy.TONES, (state, reason)


def test_an_unrecognised_reason_lands_in_the_retryable_group():
    """The eleventh reason a future deployment adds. It must say less than it knows
    rather than more, and it must not be terminal — a terminal default would tell a
    buyer to stop because the server said something the app had not heard of."""
    line = delivery_copy.delivery_copy(
        delivery={"state": "SOMETHING_NEW", "reason": "brand_new_reason"},
        destination=None)
    assert line["tone"] == delivery_copy.TONE_RETRYABLE
    assert line["retryable"] is True
    assert line["text"] == delivery_copy.TONE_DEFAULTS[delivery_copy.TONE_RETRYABLE]


def test_a_single_fabricated_date_is_never_printed():
    """§124. An estimate with only one end formats as nothing rather than as that
    one day: half a window read as a promise is worse than no window."""
    assert delivery_copy.format_window(
        {"state": estimate.STATE_ESTIMATED, "earliest": "2026-03-16",
         "latest": None}) is None
    line = delivery_copy.delivery_copy(
        delivery={"state": estimate.STATE_ESTIMATED, "earliest": "2026-03-16",
                  "latest": None}, destination=None)
    assert "Mar" not in line["text"]
    assert line["tone"] == delivery_copy.TONE_RETRYABLE


def test_the_loading_line_never_claims_free_shipping():
    """The one claim the page is sure of, beside the one it cannot yet support --
    and if the estimate comes back ``not_supplier_fulfilled``, "FREE Shipping" was
    attached to a parcel PulseSoc is not shipping."""
    assert delivery_copy.loading_copy()["shipping"] is None


def test_only_an_unserviceable_corridor_blocks_a_checkout():
    """§55, and the shadow-mode property: an operator who has not finished
    configuring delivery must not have silently closed the store."""
    assert delivery_copy.blocks_checkout(
        {"state": estimate.STATE_UNSUPPORTED_ROUTE}) is True
    for reason in sorted({value for name, value in vars(quote).items()
                          if name.startswith("REASON_") and isinstance(value, str)}):
        assert delivery_copy.blocks_checkout(
            {"state": estimate.STATE_UNAVAILABLE, "reason": reason}) is False
    assert delivery_copy.blocks_checkout(None) is False


def source_of_python() -> str:
    """``copy.py`` with its comments and docstrings removed.

    Same reason as the TypeScript side: the module *documents* that it never says
    "Guaranteed", so a raw substring search over the file would match the sentence
    explaining the rule and fail on its own justification.
    """
    import ast

    tree = ast.parse((REPO / "services" / "delivery" / "copy.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    return ast.unparse(tree)
