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
    return found - set(fulfillment.FUNDING_STATES) - set(NOT_A_STATE)


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
