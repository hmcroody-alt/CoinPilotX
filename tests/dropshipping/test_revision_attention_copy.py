"""Every reason a live listing needs a human reaches that human in words. §26/§31.

What this file is defending
---------------------------
``revisions.ATTENTION_REASONS`` exists twice: once in Python, where it is raised,
and once in ``mobile-native/src/api/dropshipping.ts`` as ``REVISION_ATTENTION``,
where it is turned into a sentence. One is Python and the other TypeScript, so no
compiler spans them, and this is the second time this exact boundary has been the
problem — ``test_publish_problem_copy.py`` exists because ``PUBLISH_PROBLEMS``
drifted and put the raw string ``SUPPLIER_VARIANT_UNBOUND`` on merchants' screens.

This list is worse to drift, not better, and the reason is the direction of the
failure. ``revisionAttention`` *drops* reasons it has no copy for, deliberately:
showing a merchant the untranslatable token ``MARGIN_LOST`` is not communication.
But a dropped reason is a silent one. If the backend grows a seventh reason and
this list does not, the product it applies to shows up on "Sync & issues" with
nothing wrong — which is the precise false all-clear the attention column was
added to end. The drift would restore the bug through the front door.

Inside TypeScript, ``REVISION_ATTENTION_COPY`` is a total ``Record<RevisionAttention, …>``,
so adding to the union without copy is a build error. That closes the half
TypeScript can see. This file closes the other half.

It also checks the words are wired to something. A vocabulary nothing renders is
§31 scaffolding, and this one is three files away from the screen it exists for.

Needs no database, but ``services.db`` binds ``DATABASE_URL`` at import, so this
sets one rather than inheriting whichever file ran first.
"""

import os
import re
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="attention-copy-", suffix=".db")
os.close(_HANDLE)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_PATH}")
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"

from services.business_os.suppliers import revisions  # noqa: E402

MOBILE_API = os.path.join(REPO, "mobile-native", "src", "api", "dropshipping.ts")
SYNC_SCREEN = os.path.join(
    REPO, "mobile-native", "src", "screens", "dropshipping", "DropshippingSyncScreen.tsx")


def _api_source():
    return open(MOBILE_API, encoding="utf-8").read()


def mobile_reasons():
    """The literal array, read as data rather than as logic.

    Read as text because there is no other way across the language boundary. What
    is read is a literal array and the keys of a literal object — a test that read
    a TS *function* to decide what it does would be satisfied by whatever that
    function said.
    """
    block = re.search(r"export const REVISION_ATTENTION = \[(.*?)\] as const;",
                      _api_source(), re.S)
    assert block, "REVISION_ATTENTION is no longer a literal array — this test cannot read it"
    return re.findall(r'"([A-Z_]+)"', block.group(1))


def mobile_copy():
    """Each reason mapped to its ``severity``, taken from the copy table."""
    block = re.search(r"REVISION_ATTENTION_COPY: Record<\s*RevisionAttention,"
                      r".*?\n> = \{(.*?)\n\};", _api_source(), re.S)
    assert block, "REVISION_ATTENTION_COPY is no longer a literal object"
    body = block.group(1)
    entries = re.findall(
        r"([A-Z_]+):\s*\{\s*text:\s*\"(.*?)\",\s*action:\s*\"(.*?)\",\s*"
        r"severity:\s*\"(fix|info)\"\s*\}", body, re.S)
    return {name: {"text": text, "action": action, "severity": severity}
            for name, text, action, severity in entries}


# ---------------------------------------------------------------------------
# The two lists are one list
# ---------------------------------------------------------------------------

def test_the_mobile_vocabulary_names_exactly_what_the_backend_can_raise():
    assert sorted(mobile_reasons()) == sorted(revisions.ATTENTION_REASONS)


def test_every_reason_has_copy():
    assert sorted(mobile_copy()) == sorted(revisions.ATTENTION_REASONS)


def test_the_copy_table_declares_nothing_the_backend_cannot_raise():
    """A reason that can never arrive is copy nobody will ever read, and it makes
    the table look complete while a real one is missing from it."""
    assert set(mobile_copy()) <= set(revisions.ATTENTION_REASONS)


# ---------------------------------------------------------------------------
# The words are usable
# ---------------------------------------------------------------------------

def test_every_reason_says_what_happened_and_what_to_do():
    for reason, copy in mobile_copy().items():
        assert copy["text"].strip(), f"{reason} has no description"
        assert copy["action"].strip(), f"{reason} tells the merchant nothing to do"
        assert copy["text"] != reason, f"{reason} is rendering its own code as prose"


def test_no_reason_puts_its_code_in_front_of_a_merchant():
    """The failure `test_publish_problem_copy.py` was written after."""
    for reason, copy in mobile_copy().items():
        assert reason not in copy["text"]
        assert reason not in copy["action"]


def test_the_two_a_merchant_cannot_act_on_are_not_filed_under_needs_you():
    """"Needs you" has to be a list of things the merchant can actually do, or it
    stops being read — and then the item that really did need them is buried in it.

    An unreadable stock count and an unreported supplier cost are both real and
    both entirely outside the merchant's control: the answer to each is "we will
    ask again". Everything else here has an action today.
    """
    severities = {name: copy["severity"] for name, copy in mobile_copy().items()}
    assert severities[revisions.STOCK_UNREADABLE] == "info"
    assert severities[revisions.COST_UNAVAILABLE] == "info"
    assert severities[revisions.SELLING_BELOW_COST] == "fix"
    assert severities[revisions.MARGIN_LOST] == "fix"
    assert severities[revisions.REPRICE_IMPOSSIBLE] == "fix"
    assert severities[revisions.SUPPLIER_OUT_OF_STOCK] == "fix"


# ---------------------------------------------------------------------------
# It is wired to a screen
# ---------------------------------------------------------------------------

def test_the_sync_screen_reads_the_attention_field_and_not_only_the_sync_state():
    """§31. The whole point of the column is that `syncState` cannot carry it, so a
    screen that imported the copy table and went on filtering by sync state alone
    would still show the false all-clear."""
    source = open(SYNC_SCREEN, encoding="utf-8").read()
    assert "REVISION_ATTENTION_COPY" in source, \
        "the sync screen does not import the copy table"
    assert "row.attention" in source, \
        "the sync screen never reads the attention field"


def test_the_api_layer_parses_attention_onto_the_row_the_screen_renders():
    """The screen can only read `row.attention` if `normalizeImportedRow` puts it
    there; a typed field the normalizer never fills is `undefined` at runtime and
    a crash on the first `.map`."""
    source = _api_source()
    assert "attention: revisionAttention(raw.attention)" in source, \
        "normalizeImportedRow does not populate `attention`"


def test_the_backend_list_route_actually_serves_the_field():
    """The last link. Everything above could be perfect over a payload that never
    carries the key, and the screen would render an empty list forever."""
    import inspect

    from services.business_os.suppliers import drafts
    body = inspect.getsource(drafts.list_drafts)
    assert "s.attention_json" in body, "list_drafts does not select the column"
    assert 'row["attention"]' in body, "list_drafts does not serve the parsed list"
