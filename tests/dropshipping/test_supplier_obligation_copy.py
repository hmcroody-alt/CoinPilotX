"""Every state a supplier order can be in reaches the merchant in words.

What this file is defending
---------------------------
A supplier order's state exists twice. On the Python side it is not an
enumeration at all — it is a handful of string literals scattered across SQL
``UPDATE`` statements in ``services/business_os/suppliers/fulfillment.py``,
``webhooks.py`` and ``worker.py``, plus a schema ``DEFAULT``. On the TypeScript
side it is ``SUPPLIER_ORDER_STATES`` in ``mobile-native/src/api/dropshipping.ts``.
No compiler spans the two.

This is the same seam that put the raw string ``SUPPLIER_VARIANT_UNBOUND`` on a
merchant's screen (see ``test_publish_problem_copy.py``). The failure mode here
is worse than a cosmetic one. ``UNKNOWN`` does not mean "we have not looked"; it
means a write to the supplier could not be confirmed, so the order may or may
not exist. If mobile has never heard of a state it falls back to generic copy,
and a merchant reading generic copy over an ``UNKNOWN`` row may place the
supplier order a second time. Duplicate supplier orders are real money.

How the Python side is measured
-------------------------------
Not by listing the states here — a list written in this file would be a third
copy, and it could drift from the writers exactly as the TypeScript one did.
Instead every upper-case string literal in the three modules that touch
``business_os_supplier_outbox.state`` is collected, and the ones that are
provably about something else are subtracted:

* the funding vocabulary, read from ``fulfillment.FUNDING_STATES`` rather than
  retyped, so it cannot drift here either;
* a small allowlist below, each entry with the reason it is not a state.

What survives is the outbox's vocabulary. The point of collecting *everything*
and subtracting is that a state added tomorrow needs no help from this file to
be noticed: it will simply appear, unaccounted for, and fail. An enumeration
cannot notice what was never on it, so this file does not keep an enumeration.

``worker.py`` is the check on that method — every upper-case literal in it is an
outbox state, so if the subtraction were wrong that file would show it.

The TypeScript side is read as text, because there is no other way across the
boundary, and what is read is a literal array and the keys of a literal object.
Data, not logic: a test that read a TS *function* to decide what it does would be
satisfied by whatever that function said.

Needs no database, but ``services.db`` binds ``DATABASE_URL`` at import, so this
sets one rather than inheriting whichever file ran first.
"""

import os
import re
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="supplier-obligation-copy-", suffix=".db")
os.close(_HANDLE)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_PATH}")
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"

from services.business_os.suppliers import fulfillment  # noqa: E402

MOBILE_API = os.path.join(REPO, "mobile-native", "src", "api", "dropshipping.ts")

#: The modules that write or read ``business_os_supplier_outbox.state``. Found by
#: grepping for the table name, not assumed: a fourth module that started
#: setting the column would need adding here, and the test below that pins the
#: writer set is what makes that omission visible.
STATE_MODULES = (
    os.path.join("services", "business_os", "suppliers", "fulfillment.py"),
    os.path.join("services", "business_os", "suppliers", "webhooks.py"),
    os.path.join("services", "business_os", "suppliers", "worker.py"),
)

#: Upper-case literals in those modules that are not outbox states, each with
#: the reason. Kept deliberately small and specific rather than pattern-based:
#: a regex like "ignore anything ending in _MODE" would also swallow a real
#: state one day, silently, which is the whole defect family this file is about.
NOT_A_STATE = {
    "CJ_ENVIRONMENT_MODE": "environment variable name",
    "PRODUCTION_CJ_FULFILLMENT_ENABLED": "environment variable name",
    "SANDBOX": "value of CJ_ENVIRONMENT_MODE",
    "CONNECTED": "a supplier *connection* status, not an order state",
    "ORDER_CONNNECTED": "CJ's own webhook type, triple-N typo included",
    "DROPSHIP": "marketplace_product_sources.fulfillment_mode",
    "IN_STOCK": "supplier availability, not order state",
    "USD": "currency code",
    "DELETE": "SQL verb / CJ webhook action",
    "INSERT": "SQL verb / CJ webhook action",
    "UPDATE": "SQL verb / CJ webhook action",
    "LOGISTIC": "CJ webhook resource type",
    "ORDER": "CJ webhook resource type",
    "PRODUCT": "CJ webhook resource type",
    "STOCK": "CJ webhook resource type",
    "VARIANT": "CJ webhook resource type",
}


def _source(relative_path):
    with open(os.path.join(REPO, relative_path), encoding="utf-8") as handle:
        source = handle.read()
    # Comments and docstring prose name states while discussing them; only code
    # can put one in the database.
    source = re.sub(r"^\s*#.*$", "", source, flags=re.M)
    return source


def python_states():
    """The outbox vocabulary, as the Python writers actually spell it."""
    found = set()
    for relative_path in STATE_MODULES:
        found |= set(re.findall(r"[\"']([A-Z][A-Z_]{2,})[\"']", _source(relative_path)))
    # All three subtracted sets are *read* rather than retyped, for the same
    # reason: a vocabulary transcribed into this file is a copy that can drift
    # from the one it describes, which is the defect the whole file exists to
    # catch. `BLOCKERS` says why an order cannot be placed; the outbox's states
    # say where one that was placed has got to. They share a module and nothing
    # else.
    #
    # `DRAIN_STATES` is the third because it describes the *worker*, not any
    # order: whether anything is emptying the outbox at all. Adding it here was
    # not a choice — declaring those constants in `fulfillment.py` failed this
    # test immediately, which is this collect-everything-and-subtract method
    # doing precisely what its docstring claims, on a vocabulary it had never
    # seen. They are pinned in their own right further down this file.
    return (found - set(fulfillment.FUNDING_STATES) - set(fulfillment.BLOCKERS)
            - set(fulfillment.DRAIN_STATES) - set(NOT_A_STATE))


def mobile_states():
    source = open(MOBILE_API, encoding="utf-8").read()
    block = re.search(
        r"export const SUPPLIER_ORDER_STATES = \[(.*?)\] as const;", source, re.S)
    assert block, ("SUPPLIER_ORDER_STATES is no longer a literal array — this "
                   "test can no longer read it")
    return re.findall(r'"([A-Z_]+)"', block.group(1))


def mobile_copy():
    source = open(MOBILE_API, encoding="utf-8").read()
    block = re.search(
        r"export const SUPPLIER_ORDER_STATE_COPY: Record<SupplierOrderState, string> = \{(.*?)\n\};",
        source, re.S)
    assert block, (
        "SUPPLIER_ORDER_STATE_COPY is no longer a total Record over "
        "SupplierOrderState. That annotation is what makes the TypeScript half "
        "self-checking; widening it to Record<string, …> removes the compile "
        "error that catches a state added without copy.")
    return dict(re.findall(r'^  ([A-Z_]+): "([^"]*)"', block.group(1), re.M))


def test_mobile_names_every_state_the_backend_can_store():
    # The direction that matters. A state the backend writes and mobile has
    # never heard of reaches `supplierOrderStateCopy`'s fallback, which honestly
    # says it does not recognise the state — but says nothing about whether the
    # supplier order exists, which is the only thing the merchant needs.
    missing = python_states() - set(mobile_states())
    assert not missing, (
        "These supplier order states are written by the backend but absent from "
        f"SUPPLIER_ORDER_STATES, so the app cannot explain them: {sorted(missing)}")


def test_mobile_invents_no_state_the_backend_cannot_store():
    # A state only mobile knows about is copy for something that cannot happen,
    # maintained forever by people who assume it can. `AWAITING_SUPPLIER_ORDER`
    # is not an exception: it is declared in fulfillment.py, so the subtraction
    # below finds it on the Python side too.
    invented = set(mobile_states()) - python_states()
    assert not invented, (
        "SUPPLIER_ORDER_STATES names states nothing in the backend produces: "
        f"{sorted(invented)}")


def test_every_state_has_words_a_merchant_can_read():
    # The total Record is a typecheck; this is the same claim from outside the
    # typechecker, because the annotation could be widened in one edit and the
    # build would stay green with copy missing.
    copy = mobile_copy()
    missing = set(mobile_states()) - set(copy)
    assert not missing, f"No merchant-readable copy for: {sorted(missing)}"
    for state, words in copy.items():
        assert words.strip(), f"{state} has empty copy"
        # No identifier leaking into prose: no snake_case, no camelCase, and not
        # the state's own name, which is the shape of copy that was never written.
        assert not re.search(r"[a-z]_[a-z]|[a-z][A-Z]", words), (
            f"{state} copy reads like an identifier, not a sentence: {words!r}")
        assert state not in words, f"{state} copy is just the state name: {words!r}"


def test_awaiting_and_unknown_are_never_the_same_words():
    # The load-bearing distinction. AWAITING_SUPPLIER_ORDER means no supplier
    # order exists and placing one is safe. UNKNOWN means one may already exist.
    # Identical copy for both is an instruction to double-order.
    copy = mobile_copy()
    assert copy["AWAITING_SUPPLIER_ORDER"] != copy["UNKNOWN"]
    assert "re-order" in copy["UNKNOWN"].lower(), (
        "UNKNOWN's copy must tell the merchant not to place the order again; "
        f"it currently reads {copy['UNKNOWN']!r}")


def test_the_awaiting_state_is_never_written_to_the_outbox():
    # `AWAITING_SUPPLIER_ORDER` is derived on read by `list_obligations` and is
    # deliberately not part of the outbox's vocabulary — there is no intent to
    # deliver, so there is no outbox row. If it ever appears in an UPDATE or an
    # INSERT against the outbox, the two vocabularies have merged and a row can
    # sit in a state `dispatch` will never pick up.
    for relative_path in STATE_MODULES:
        source = _source(relative_path)
        for statement in re.findall(r"(?is)\b(?:UPDATE|INSERT INTO)\s+business_os_supplier_outbox.*?(?=[\"'])", source):
            assert fulfillment.AWAITING_SUPPLIER_ORDER not in statement, (
                f"{relative_path} persists {fulfillment.AWAITING_SUPPLIER_ORDER} "
                "to the outbox; it is a read-time derivation, not a stored state")


def test_the_list_has_no_duplicates():
    states = mobile_states()
    assert len(states) == len(set(states)), "SUPPLIER_ORDER_STATES lists a state twice"


def mobile_blockers():
    source = open(MOBILE_API, encoding="utf-8").read()
    block = re.search(
        r"export const SUPPLIER_OBLIGATION_BLOCKERS = \[(.*?)\] as const;", source, re.S)
    assert block, ("SUPPLIER_OBLIGATION_BLOCKERS is no longer a literal array — "
                   "this test can no longer read it")
    return re.findall(r'"([A-Z_]+)"', block.group(1))


def mobile_blocker_copy():
    source = open(MOBILE_API, encoding="utf-8").read()
    block = re.search(
        r"export const SUPPLIER_OBLIGATION_BLOCKER_COPY: "
        r"Record<SupplierObligationBlocker, string> = \{(.*?)\n\};", source, re.S)
    assert block, (
        "SUPPLIER_OBLIGATION_BLOCKER_COPY is no longer a total Record over "
        "SupplierObligationBlocker. That annotation is the compile error that "
        "catches a blocker added without copy; widening it removes it.")
    return dict(re.findall(r'^  ([A-Z_]+): "([^"]*)"', block.group(1), re.M))


# The blocker vocabulary is the third cross-language enumeration in this
# subsystem, pinned the same way with one difference: the Python side *is* a
# declared tuple, so it is read rather than reconstructed by subtraction. What
# the first test below then has to prove is that the tuple is not itself another
# copy — that it and the code that appends blockers have not drifted apart.

def test_the_blocker_tuple_is_what_the_code_actually_appends():
    # Read off `co_names` — the globals each function loads — rather than
    # `co_consts`. The blockers are self-named module constants referenced by
    # name, so they are not literals inside these functions; a `co_consts` read
    # finds only the two inline strings and would pass by finding nothing.
    declared = {name for name, value in vars(fulfillment).items()
                if isinstance(value, str) and value == name}
    found = {name for function in (fulfillment.list_obligations,
                                   fulfillment.supplier_destination)
             for name in function.__code__.co_names if name in declared}
    # `AWAITING_SUPPLIER_ORDER` is a state, not a blocker: `list_obligations`
    # derives it on the same row and it is deliberately not in `BLOCKERS`.
    found -= {fulfillment.AWAITING_SUPPLIER_ORDER}
    assert found == set(fulfillment.BLOCKERS), (
        "fulfillment.BLOCKERS and the blockers the code can append disagree: "
        f"only in BLOCKERS {sorted(set(fulfillment.BLOCKERS) - found)}, "
        f"only in the code {sorted(found - set(fulfillment.BLOCKERS))}")


def test_mobile_names_every_blocker_the_backend_can_emit():
    # This has teeth the state version does not. Several blockers *are*
    # merchant-actionable, so falling back to generic copy is not a wait — it is
    # a dead end: the merchant is told the order cannot go and not told what to
    # change about it.
    missing = set(fulfillment.BLOCKERS) - set(mobile_blockers())
    assert not missing, (
        "These obligation blockers are emitted by the backend but absent from "
        f"SUPPLIER_OBLIGATION_BLOCKERS: {sorted(missing)}")


def test_mobile_invents_no_blocker_the_backend_cannot_emit():
    invented = set(mobile_blockers()) - set(fulfillment.BLOCKERS)
    assert not invented, (
        "SUPPLIER_OBLIGATION_BLOCKERS names blockers nothing in the backend "
        f"produces: {sorted(invented)}")


def test_every_blocker_has_words_a_merchant_can_read():
    copy = mobile_blocker_copy()
    missing = set(mobile_blockers()) - set(copy)
    assert not missing, f"No merchant-readable copy for: {sorted(missing)}"
    for blocker, words in copy.items():
        assert words.strip(), f"{blocker} has empty copy"
        assert not re.search(r"[a-z]_[a-z]|[a-z][A-Z]", words), (
            f"{blocker} copy reads like an identifier, not a sentence: {words!r}")
        assert blocker not in words, f"{blocker} copy is just its own name: {words!r}"


def test_no_blocker_asks_a_merchant_for_the_buyers_address():
    # The two destination blockers are not things a merchant can fix. The
    # address belongs to the buyer, the obligation deliberately does not carry
    # it, and copy phrased as a prompt would have merchants inventing delivery
    # addresses for other people's parcels.
    copy = mobile_blocker_copy()
    for blocker in ("DESTINATION_MISSING", "DESTINATION_INCOMPLETE"):
        assert not re.search(r"\b(enter|add|type|provide|fill)\b", copy[blocker], re.I), (
            f"{blocker} copy reads as a prompt for an address the merchant does "
            f"not have: {copy[blocker]!r}")


def test_a_blocker_and_a_state_are_never_the_same_word():
    # They render in different places and mean different things: a state
    # describes a supplier order that exists, a blocker describes why one does
    # not. `SUPPLIER_ORDER_ALREADY_PLACED` sits close enough to `LINKED` to make
    # merging them tempting, and merging them makes both lists unreadable.
    overlap = set(mobile_blockers()) & set(mobile_states())
    assert not overlap, f"Named as both a state and a blocker: {sorted(overlap)}"


def test_the_worker_only_speaks_in_outbox_states():
    # The check on the subtraction method above. Every upper-case literal in
    # worker.py is an outbox state today, and it holds no env var names,
    # currencies or webhook types. If a literal there ever fails to be a state,
    # either the module grew a new responsibility or NOT_A_STATE is wrong — and
    # NOT_A_STATE being wrong is how a real state gets silently subtracted.
    literals = set(re.findall(r"[\"']([A-Z][A-Z_]{2,})[\"']",
                              _source(STATE_MODULES[2])))
    unaccounted = literals - set(mobile_states())
    assert not unaccounted, (
        "worker.py names upper-case literals that are not supplier order "
        f"states: {sorted(unaccounted)}")


# --------------------------------------------------------------------------
# The reason vocabulary: the enumeration that was being rendered before anyone
# decided it was an enumeration
# --------------------------------------------------------------------------
#
# `business_os_supplier_outbox.last_error` is named like a diagnostic and used
# like one. It was also being *printed to the merchant* — `DropshippingOrdersScreen`
# rendered it verbatim under a comment calling it "the supplier's own refusal
# text ... the words their supplier used".
#
# Both halves of that comment were false, and neither had ever been checked:
# `suppliers/errors.py` exists precisely to guarantee no provider string reaches
# this package's persisted state, and the values are identifiers written in
# Python. A merchant with a blocked order read `preflight_blocked`.
#
# And it was one word for about a dozen causes, because `dispatch` flattened
# every non-retryable preflight failure before storing it. So these tests pin
# three separate things: that every reason the Python can write has words, that
# the mapping from internal code to reason cannot name a code nothing raises,
# and that the flattening does not come back.


def mobile_reasons():
    source = open(MOBILE_API, encoding="utf-8").read()
    block = re.search(
        r"export const SUPPLIER_ORDER_REASONS = \[(.*?)\] as const;", source, re.S)
    assert block, ("SUPPLIER_ORDER_REASONS is no longer a literal array — this "
                   "test can no longer read it")
    return re.findall(r'"([a-z_]+)"', block.group(1))


def mobile_reason_copy():
    source = open(MOBILE_API, encoding="utf-8").read()
    block = re.search(
        r"export const SUPPLIER_ORDER_REASON_COPY: "
        r"Record<SupplierOrderReason, string> = \{(.*?)\n\};", source, re.S)
    assert block, (
        "SUPPLIER_ORDER_REASON_COPY is no longer a total Record over "
        "SupplierOrderReason. That annotation is the compile error that catches "
        "a reason added without copy; widening it removes it.")
    return dict(re.findall(r'^  ([a-z_]+): "([^"]*)"', block.group(1), re.M))


def persisted_reasons():
    """Every reason the Python *code* can put in `last_error`, read off the code.

    Not `fulfillment.OUTBOX_REASONS`. That constant is the declaration, and a
    declaration is the thing that drifts — the whole file is about enumerations
    that stopped matching what was written. What is collected here is every
    literal actually handed to a `last_error=` column or an `error=` keyword
    across the three modules that write the outbox.
    """
    found = set()
    for relative_path in STATE_MODULES:
        source = _source(relative_path)
        found |= set(re.findall(r"last_error='([a-z_]+)'", source))
        found |= set(re.findall(r'\berror="([a-z_]+)"', source))
    return found


def test_the_declared_reason_set_is_what_the_code_actually_writes():
    # `OUTBOX_REASONS` exists so mobile can be checked against something; that
    # is only worth anything if it is also checked against the writers. A
    # `settle(..., error="something_new")` added without touching the constant
    # would otherwise reach a merchant as a raw identifier, which is exactly the
    # failure this subsystem has already shipped once.
    written = persisted_reasons()
    undeclared = written - set(fulfillment.OUTBOX_REASONS)
    assert not undeclared, (
        "These reasons are written to business_os_supplier_outbox.last_error but "
        f"are not in fulfillment.OUTBOX_REASONS: {sorted(undeclared)}")


def test_every_reason_a_merchant_can_be_shown_has_words():
    # The direction that costs something. A reason mobile has never heard of
    # used to render as the identifier itself; it now renders a fallback that
    # admits it cannot name the cause. Either way the merchant is not told what
    # to do, so the set has to be complete.
    missing = set(fulfillment.OUTBOX_REASONS) - set(mobile_reasons())
    assert not missing, (
        "These outbox reasons are written by the backend but absent from "
        f"SUPPLIER_ORDER_REASONS, so the app cannot explain them: {sorted(missing)}")
    copy = mobile_reason_copy()
    uncopied = set(mobile_reasons()) - set(copy)
    assert not uncopied, f"No merchant-readable copy for: {sorted(uncopied)}"
    for reason, words in copy.items():
        assert words.strip(), f"{reason} has empty copy"
        # Same rule as the state copy: the identifier must not survive into the
        # prose, in either spelling, and must not *be* the prose.
        assert not re.search(r"[a-z]_[a-z]|[a-z][A-Z]", words), (
            f"{reason} copy reads like an identifier, not a sentence: {words!r}")
        assert reason not in words, f"{reason} copy is just the code: {words!r}"


def test_mobile_invents_no_reason_the_backend_cannot_write():
    invented = set(mobile_reasons()) - set(fulfillment.OUTBOX_REASONS)
    assert not invented, (
        "SUPPLIER_ORDER_REASONS names reasons nothing in the backend writes: "
        f"{sorted(invented)}")
    reasons = mobile_reasons()
    assert len(reasons) == len(set(reasons)), "SUPPLIER_ORDER_REASONS lists a reason twice"


def test_every_internal_code_mapped_to_a_reason_is_one_something_raises():
    """The mapping cannot name a code no line raises.

    `PREFLIGHT_REASONS` is keyed by this module's own `FulfillmentError` codes.
    A key with a typo, or one left behind after the code that raised it was
    renamed, is an entry that can never match — so the cause it was written for
    silently falls back to `preflight_blocked` and every test still passes,
    because nothing failed. That is this repo's recurring defect in its purest
    form and the only way to catch it is to check the keys against the raises.
    """
    raised = set()
    for relative_path in STATE_MODULES + ("services/business_os/suppliers/gateway.py",):
        source = _source(relative_path)
        raised |= set(re.findall(r'(?:FulfillmentError|SupplierError|GatewayError)\("([a-z_]+)"',
                                 source))
    unraisable = set(fulfillment.PREFLIGHT_REASONS) - raised
    assert not unraisable, (
        "PREFLIGHT_REASONS maps internal codes that nothing raises, so these "
        f"entries can never match and their causes stay unnamed: {sorted(unraisable)}")


def test_dispatch_does_not_flatten_every_refusal_into_one_word():
    """The regression guard on the fix itself.

    `dispatch`'s handler chose between three constants and threw the exception's
    own code away. Restoring that -- by editing the expression back, or by
    emptying `PREFLIGHT_REASONS` -- would leave every test above green: the
    declared set would still be covered by copy, and a merchant would still be
    told something. They would just all be told the same thing.

    So this asserts the property rather than the wording: the BLOCKED branch
    resolves through the map, and the map distinguishes the causes a merchant
    can actually act on.
    """
    source = _source("services/business_os/suppliers/fulfillment.py")
    handler = source.split("def dispatch(", 1)[-1].split("\ndef ", 1)[0]
    assert "PREFLIGHT_REASONS.get(" in handler, (
        "dispatch no longer resolves its BLOCKED reason through "
        "PREFLIGHT_REASONS, so every preflight refusal is being stored as one "
        "word again")
    actionable = {"supplier_quote_expired", "supplier_cost_changed",
                  "supplier_connection_changed", "supplier_shop_unbound",
                  "supplier_item_changed", "supplier_stock_unconfirmed"}
    distinct = set(fulfillment.PREFLIGHT_REASONS.values())
    assert actionable <= distinct, (
        "These are the refusals a merchant can do something about, and "
        f"PREFLIGHT_REASONS no longer distinguishes: {sorted(actionable - distinct)}")
    assert "preflight_blocked" not in distinct, (
        "preflight_blocked is the fallback for a cause this module did not "
        "choose; mapping a known code onto it re-flattens that cause")


def test_the_blocked_copy_does_not_blame_a_supplier_that_was_never_asked():
    """BLOCKED means *this* deployment did not send, not that CJ said no.

    The copy said "Your supplier refused this order" — a sentence that sends a
    merchant to argue with their supplier about a message the supplier never
    sent. What makes it wrong is not a judgement about which causes are common;
    it is a branch. `dispatch`'s handler tests `sent` **first**, and anything
    sent becomes `UNKNOWN`. So `BLOCKED` is unreachable once the write has been
    attempted, and the copy is entitled to say nothing went out — as long as
    that ordering holds, which is what is asserted here.

    Deliberately not asserted by checking where each code is raised. An earlier
    version of this test did that, textually, and it was both wrong (four of the
    codes are raised in helpers `dispatch` calls) and a proxy for the thing that
    actually guarantees the claim.
    """
    words = mobile_copy()["BLOCKED"].lower()
    assert "refus" not in words and "declin" not in words and "reject" not in words, (
        f"BLOCKED copy attributes a refusal to the supplier: {words!r}")
    handler = _source("services/business_os/suppliers/fulfillment.py")
    handler = handler.split("except Exception as exc:", 1)[-1].split("\n    finally:", 1)[0]
    unknown = handler.find('state = "UNKNOWN"')
    blocked = handler.find('state = "BLOCKED"')
    sent_test = handler.find("if sent or ")
    assert 0 <= sent_test < unknown < blocked, (
        "dispatch's handler no longer decides UNKNOWN-because-sent before it "
        "can reach BLOCKED. BLOCKED's copy claims nothing was sent to the "
        "supplier, and that claim rests entirely on this ordering.")


# --------------------------------------------------------------------------
# The country table: a fifth cross-language enumeration, and the one that
# existed in TypeScript only
# --------------------------------------------------------------------------

CHECKOUT_COUNTRIES = os.path.join(REPO, "mobile-native", "src", "api", "checkoutCountries.ts")


def picker_country_names():
    """The checkout picker's code -> name table, read as data."""
    with open(CHECKOUT_COUNTRIES, encoding="utf-8") as handle:
        source = handle.read()
    block = re.search(
        r"const COUNTRY_NAMES: Record<string, string> = \{(.*?)\n\};", source, re.S)
    assert block, ("COUNTRY_NAMES is no longer a literal object in "
                   "checkoutCountries.ts -- this test can no longer read it")
    return dict(re.findall(r'([A-Z]{2}): "([^"]*)"', block.group(1)))


def test_country_names_match_the_picker():
    """The two tables are one table, spelled twice.

    Named in the `_COUNTRY_NAMES` comment because the table was TypeScript-only
    for as long as the client was its only reader, under a comment asserting
    "The server never sees them; it sees the ISO-3166-1 alpha-2 code, which is
    the contract." That was true of the buyer's half of the wire and false of
    the supplier's: CJ's create-order takes `shippingCountryCode` *and*
    `shippingCountry`, and the latter is a name.

    So a copy of the table had to exist server-side, and two copies of one fact
    in two languages with no compiler between them is the defect family this
    whole file is about. Divergence is not cosmetic here -- a code the server
    can spell but not name is a paid order that cannot be sent to a supplier.
    """
    from services import marketplace_fulfillment as mf

    server = dict(mf._COUNTRY_NAMES)
    picker = picker_country_names()
    assert picker, "read no countries out of the picker"
    only_server = {code: server[code] for code in sorted(set(server) - set(picker))}
    only_picker = {code: picker[code] for code in sorted(set(picker) - set(server))}
    assert not only_server and not only_picker, (
        "the two country tables have drifted. A code the picker offers and the "
        "server cannot name is a checkout that completes into an order no "
        "supplier can be given; a code the server names and the picker does not "
        "offer is dead weight.\n"
        f"  server only: {only_server}\n  picker only: {only_picker}")
    disagreements = {code: (server[code], picker[code])
                     for code in sorted(server) if server[code] != picker[code]}
    assert not disagreements, (
        "the same code is named differently on the two sides. The supplier is "
        "given the server's spelling, so a mismatch ships against a name the "
        f"buyer never saw: {disagreements}")


def test_the_server_admits_it_cannot_name_an_unknown_country():
    """The two sides fall back in opposite directions, on purpose.

    `countryName` in the picker answers the code itself, so an unrecognised
    country the server *does* accept stays selectable. `country_name` answers
    "" so its caller can say the address is incomplete. Making the server match
    the picker would send `XK` to CJ as the name of a country, which is this
    repo's recurring defect -- asserting a fact rather than admitting it is
    unknown -- in one line.
    """
    from services import marketplace_fulfillment as mf

    assert mf.country_name("ZZ") == ""
    assert mf.country_name("") == ""
    assert mf.country_name(None) == ""
    with open(CHECKOUT_COUNTRIES, encoding="utf-8") as handle:
        source = handle.read()
    assert "COUNTRY_NAMES[key] || key" in source, (
        "the picker's fallback changed. If it stopped answering the code it "
        "would drop a selectable country; if the server started answering the "
        "code it would name a country it cannot name. They are not symmetric.")


def test_every_country_this_platform_can_name_is_keyed_by_an_alpha_2_code():
    """The shape invariant, asserted on the table instead of on every request.

    `supplier_destination` used to re-check that the country *code* it was
    about to send was two characters long, with a comment saying that check was
    "what distinguishes a country this platform can ship to from one it can
    only spell". It was not: `country_name` answers "" for any code its table
    does not hold, and the very next field assembled is that name, so a
    three-character code was already refused one line later with the same
    blocker. The check could not fire, and the comment claiming it could was
    this repo's recurring defect written while fixing an instance of it.

    What is true is a property of the table: every key is an ISO-3166-1 alpha-2
    code. That is worth one assertion here rather than a branch per request,
    and it is load-bearing on both sides -- the picker's `toCountryOptions`
    silently drops any code whose length is not two, so a three-character key
    would name a country the buyer could never select.
    """
    from services import marketplace_fulfillment as mf

    def misshapen(codes):
        return sorted(code for code in codes
                      if not (isinstance(code, str) and len(code) == 2
                              and code.isalpha() and code.isupper()))

    assert not misshapen(mf._COUNTRY_NAMES), (
        f"_COUNTRY_NAMES is keyed by {misshapen(mf._COUNTRY_NAMES)}, which is "
        "not an ISO-3166-1 alpha-2 code. `country_name` upper-cases before it "
        "looks up, so a lowercase key is unreachable; and the checkout picker "
        "drops anything that is not two characters. Either way the entry names "
        "a country nobody can order to.")
    assert not misshapen(picker_country_names())


# ---------------------------------------------------------------------------
# The sixth copy of a Python enumeration: whether anything drains the outbox.
#
# `fulfillment.DRAIN_STATES` on one side, `SUPPLIER_DRAIN_STATES` on the other,
# and the same seam as every block above. This one guards a claim rather than a
# vocabulary: each queued row says "Queued to send to your supplier", and
# `run_once` -- the only thing that sends them -- has no entry point in the
# Procfile. The notice these states drive is the only thing on the screen that
# can contradict that sentence, so a drain state the backend can report and
# mobile cannot name is a merchant left waiting on nothing.
# ---------------------------------------------------------------------------


def mobile_drain_states():
    source = open(MOBILE_API, encoding="utf-8").read()
    block = re.search(
        r"export const SUPPLIER_DRAIN_STATES = \[(.*?)\] as const;", source, re.S)
    assert block, ("SUPPLIER_DRAIN_STATES is no longer a literal array — this "
                   "test can no longer read it")
    return re.findall(r'"([A-Z_]+)"', block.group(1))


def mobile_drain_notice():
    source = open(MOBILE_API, encoding="utf-8").read()
    block = re.search(
        r"export const SUPPLIER_DRAIN_NOTICE: "
        r"Record<SupplierDrainState, string \| null> = \{(.*?)\n\};", source, re.S)
    assert block, (
        "SUPPLIER_DRAIN_NOTICE is no longer a total Record over "
        "SupplierDrainState. That annotation is the compile error that catches a "
        "drain state added without an answer; widening it removes it.")
    entries = dict(re.findall(r'^  ([A-Z_]+):\s*(null|"[^"]*"|\n?\s*"[^"]*")',
                              block.group(1), re.M))
    return {name: (None if value.strip() == "null" else value.strip().strip('"'))
            for name, value in entries.items()}


def test_mobile_names_every_drain_state_the_backend_can_report():
    missing = set(fulfillment.DRAIN_STATES) - set(mobile_drain_states())
    assert not missing, (
        f"the backend can report drain states mobile has never heard of: "
        f"{sorted(missing)}. Each one falls through to no notice at all, which "
        "leaves every queued row claiming it is about to be sent.")


def test_mobile_invents_no_drain_state_the_backend_cannot_report():
    extra = set(mobile_drain_states()) - set(fulfillment.DRAIN_STATES)
    assert not extra, (
        f"mobile answers drain states the backend cannot report: {sorted(extra)}")


def test_every_drain_state_has_an_answer_and_only_the_healthy_one_is_silent():
    """Silence is a decision here, so it is asserted rather than allowed.

    `DRAINING` is the one state with no notice, because the per-row copy is
    already correct then. Every other state means queued orders are not moving,
    and a `null` against one of those would restore the exact defect: a screen
    that says "Queued to send to your supplier" and nothing else, forever.
    """
    notice = mobile_drain_notice()
    assert set(notice) == set(fulfillment.DRAIN_STATES), (
        f"notice map covers {sorted(notice)}, backend reports "
        f"{sorted(fulfillment.DRAIN_STATES)}")
    assert notice["DRAINING"] is None, (
        "a healthy drain now raises a banner, which will teach merchants to "
        "ignore the banner that matters")
    for state, words in notice.items():
        if state == "DRAINING":
            continue
        assert words, f"{state} means queued orders are not being sent and says nothing"
        assert not re.search(r"[a-z]_[a-z]|[a-z][A-Z]", words), (
            f"{state} renders an identifier rather than words: {words!r}")
        assert state.lower() not in words.lower(), (
            f"{state} copy contains its own code: {words!r}")
        # The notice contradicts a promise, so it has to say what is not
        # happening. Without this the copy could drift into something
        # reassuring and the row's own sentence would win.
        assert "not being sent" in words.lower() or "not been sent" in words.lower(), (
            f"{state} does not tell the merchant their queued orders are not "
            f"going out, which is the only thing this notice exists to say: {words!r}")


def test_the_drain_state_reaches_the_envelope_the_screen_reads():
    """Pins the two ends of the wire, since neither compiler sees the other.

    `list_obligations` returns the fact and `listSupplierObligations` reads it.
    A field renamed on one side alone leaves mobile permanently silent — and
    silent is indistinguishable, on screen, from healthy.
    """
    source = _source("services/business_os/suppliers/fulfillment.py")
    assert '"drain": drain_status()' in source, (
        "list_obligations no longer reports the drain on its envelope")
    mobile = open(MOBILE_API, encoding="utf-8").read()
    assert "response.drain" in mobile and "drain.state" in mobile, (
        "listSupplierObligations no longer reads the drain the server reports")


def test_the_worker_records_a_tick_even_when_it_cannot_finish_one():
    """Read off the worker, because the notice's honesty depends on it.

    `TICKING_BUT_NOT_COMPLETING` exists only if the latch is written before the
    work. Move `record_drain_tick` below the body and a broken worker becomes
    indistinguishable from an absent one, which sends the owner hunting a
    process that is already running.
    """
    source = _source("services/business_os/suppliers/worker.py")
    body = source.split("def run_once(", 1)[-1]
    started = body.find("record_drain_tick(now=now)")
    seeded = body.find("_seed_jobs(limit, now)")
    completed = body.find("record_drain_tick(now=now, completed=True)")
    assert 0 <= started < seeded < completed, (
        "run_once no longer latches the start of a tick before doing the work "
        "and the completion after it; drain_status can only tell a crashing "
        "worker from a missing one while it does")
