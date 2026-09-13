"""All three checkout lanes revalidate through one authority, before the money. §21/§22.

Why a structural suite
----------------------
``tests/marketplace/test_supplier_checkout_gate.py`` proves the gate decides
correctly. It cannot prove anybody calls it, and a §22 gate nothing calls is worth
less than no gate at all — it reads as done, so nobody looks again.

Three checkout entry points is the structural reason this class of defect keeps
recurring in this subsystem: cart, accepted-offer and buy-now each create a
transaction, each can fail before Stripe, and each has historically grown its own
private copy of whatever the other two were doing. The settlement invariant in
``test_reservation_webhook_wiring.py`` exists because the offers lane was still
hand-rolling a release long after the other two were consolidated, and it survived
because the guard that should have caught it only read ``bot.py``. So this file
names all three lanes, and a fourth cannot be added with a private copy without
failing here.

What is actually asserted
-------------------------
*Ordering*, above all. The gate must run **before** the lane's first
``INSERT INTO seller_transactions``. A refusal after that point leaves a ``created``
transaction row behind for ``settle_failed_transactions`` to sweep — the buyer sees
the same sentence either way, so the bug is invisible from the outside and shows up
later as orphaned rows and a reconciliation that does not balance. That ordering is
the one property no unit test of the gate can see, and it is checked in AST line
numbers rather than by slicing text, because a raw U+2028 anywhere earlier in
``bot.py`` makes ``str.splitlines()`` and ``ast`` disagree about which line is which.

*One authority.* Every lane goes through ``screen``. A lane calling ``evaluate`` or
``reconciliation_evidence`` directly has started interpreting the evidence itself,
which is how the three of them come to disagree about whether an unverified line may
be charged — the exact drift ``screen`` was extracted to prevent.

Every call here is matched on **receiver and attribute**, never on the attribute
alone. The first draft of this file matched ``evaluate`` by name and failed on two
lanes — both were calling ``marketplace_goods_policy.evaluate``, a different
authority that has every right to exist. A bare-name match turns any method named
``screen`` or ``evaluate`` anywhere in a 111k-line file into a failure here, and the
obvious way to make that failure go away is to delete the assertion. So the receiver
is resolved from the lane's own import statement, and a lane that binds no name for
the gate module fails loudly rather than passing vacuously.

*The audit annotation.* An allowed-but-unverified sale has to be recorded on the
transaction it allowed. Without it, the gap this gate knowingly permits leaves no
trace, and "we allowed it and said so" quietly becomes "we allowed it".
"""

import ast
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

#: (label, module path, the checkout function's name).
#:
#: The buy-now lane's gate is scoped to ``marketplace_product`` inside the function,
#: because that route also sells courses, subscriptions and tips whose ``item_id``
#: counts in a different table. That scoping is asserted separately below rather
#: than being allowed to look like a missing call.
LANES = [
    ("cart", "services/marketplace_cart_routes.py", "cart_checkout"),
    ("offers", "services/marketplace_offers_routes.py", "offer_checkout"),
    ("buy-now", "bot.py", "api_pulse_payments_checkout"),
]


#: The gate module's own name, as imported. Not an alias — the thing being aliased.
GATE_MODULE = "marketplace_supplier_checkout"


def _tree(rel_path):
    path = os.path.join(REPO_ROOT, rel_path)
    with open(path, encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=path)


def _function(tree, name, rel_path):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {rel_path} — the lane moved or was renamed")


def _gate_aliases(tree):
    """Every local name this file binds to the gate module.

    Discovered rather than hardcoded, because the three lanes legitimately spell the
    import differently: two alias it at module scope, ``bot.py`` imports it inside the
    function to keep the monolith's import graph flat. A test that hardcoded one
    spelling would stop looking at any lane that changed its import style, which is
    exactly the moment it most needs to be looking.

    ``import a.b.marketplace_supplier_checkout`` without an ``as`` binds ``a``, and the
    resulting ``a.b.marketplace_supplier_checkout.screen(...)`` has an ``Attribute``
    receiver this resolver deliberately does not chase. It returns nothing for that
    spelling, and the non-vacuity test below turns that into a failure rather than a
    silent pass.
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == GATE_MODULE:
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] == GATE_MODULE and alias.asname:
                    names.add(alias.asname)
    return names


def _gate_calls(node, aliases):
    """``{attribute: [line, ...]}`` for ``<gate alias>.attribute(...)`` inside ``node``.

    Receiver-scoped on purpose. See the module docstring: matching the attribute alone
    collides with unrelated authorities that share a verb.
    """
    found = {}
    for child in ast.walk(node):
        if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id in aliases):
            found.setdefault(child.func.attr, []).append(child.lineno)
    return found


def _string_lines(node, needle):
    return [child.lineno for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
            and needle in child.value]


@pytest.fixture(scope="module")
def lanes():
    """Parsed once. ``bot.py`` is 111k lines and every test here would re-parse it."""
    parsed = {}
    for label, path, name in LANES:
        tree = _tree(path)
        parsed[label] = (_function(tree, name, path), path, _gate_aliases(tree))
    return parsed


@pytest.mark.parametrize("label", [lane[0] for lane in LANES])
def test_every_lane_binds_a_name_for_the_gate_module(lanes, label):
    """The non-vacuity check the rest of this file rests on.

    Every other assertion here looks for calls on a name bound to the gate module. If
    that set were empty the whole suite would pass by finding nothing — a lane could
    drop §22 entirely and this file would go green. So the set is asserted non-empty
    first, separately, with its own name in the report.
    """
    _, path, aliases = lanes[label]
    assert aliases, (
        f"the {label} lane ({path}) imports no name for {GATE_MODULE}; every other "
        f"assertion in this file would pass vacuously")


@pytest.mark.parametrize("label", [lane[0] for lane in LANES])
def test_every_checkout_lane_revalidates_the_supplier(lanes, label):
    node, path, aliases = lanes[label]
    calls = _gate_calls(node, aliases)
    assert calls.get("screen"), (
        f"the {label} lane ({path}) never calls {GATE_MODULE}.screen — §22 is "
        f"not enforced on this lane")


@pytest.mark.parametrize("label", [lane[0] for lane in LANES])
def test_the_gate_runs_before_the_lane_creates_a_transaction(lanes, label):
    """The ordering no unit test of the gate can see.

    A refusal after the INSERT leaves a ``created`` row behind. The buyer reads the
    same sentence, so nothing looks wrong until the orphans are counted.
    """
    node, path, aliases = lanes[label]
    screen_lines = _gate_calls(node, aliases).get("screen") or []
    insert_lines = _string_lines(node, "INSERT INTO seller_transactions")
    assert screen_lines, f"{label}: no screen call to order"
    assert insert_lines, (
        f"{label}: no INSERT INTO seller_transactions found in {path} — this test "
        f"is no longer measuring what it claims")
    assert min(screen_lines) < min(insert_lines), (
        f"{label}: the supplier gate runs at line {min(screen_lines)}, after the "
        f"transaction INSERT at line {min(insert_lines)}; a refusal there would "
        f"strand a created transaction row")


@pytest.mark.parametrize("label", [lane[0] for lane in LANES])
def test_no_lane_interprets_the_supplier_evidence_itself(lanes, label):
    """One authority. §21.

    ``evaluate`` and ``reconciliation_evidence`` are the gate's internals. A lane
    reaching for either has started deciding for itself — and the first thing it
    will get wrong is the unverified case, which looks like an ALLOW and is only
    safe if the annotation travels with it.
    """
    node, path, aliases = lanes[label]
    calls = _gate_calls(node, aliases)
    for private in ("evaluate", "reconciliation_evidence"):
        assert private not in calls, (
            f"{label} ({path}) calls {GATE_MODULE}.{private} directly instead of "
            f"screen; the three lanes will drift")


@pytest.mark.parametrize("label", [lane[0] for lane in LANES])
def test_every_lane_records_the_sale_it_could_not_confirm(lanes, label):
    """The allowed-but-unverified case has to leave a trace.

    This gate knowingly permits sales it cannot vouch for, because on this
    deployment nothing refreshes supplier confirmations yet. That is defensible
    only while it is recorded. A lane that screens and then drops the annotation
    turns "allowed and said so" into "allowed".
    """
    node, path, aliases = lanes[label]
    calls = _gate_calls(node, aliases)
    assert calls.get("audit_for"), (
        f"{label} ({path}) screens the supplier but never writes the unverified "
        f"annotation onto its transaction metadata")


@pytest.mark.parametrize("label", [lane[0] for lane in LANES])
def test_every_lane_refuses_with_the_client_facing_code(lanes, label):
    """The wire code is the gate's to decide, not the lane's.

    A supplier sell-out travels as ``OUT_OF_STOCK`` because the native client
    already maps it; the unconfirmed refusal travels as itself and relies on the
    client's documented fallback to server prose. A lane that passed the internal
    reason straight through would send a code the client cannot map for the one
    case where it can.
    """
    node, path, aliases = lanes[label]
    assert _gate_calls(node, aliases).get("refusal_code"), (
        f"{label} ({path}) does not translate the refusal through "
        f"{GATE_MODULE}.refusal_code")


def test_the_buy_now_lane_only_screens_marketplace_products(lanes):
    """Scoped, because this route sells more than listings.

    ``/api/pulse/payments/checkout`` also takes courses, subscriptions and tips,
    whose ``item_id`` counts in a different table. Screening one of those would ask
    ``marketplace_product_sources`` about whichever listing happens to share that
    number — and could refuse a course because an unrelated product sold out.
    """
    node, _, aliases = lanes["buy-now"]
    screen_lines = _gate_calls(node, aliases).get("screen") or []
    assert len(screen_lines) == 1, screen_lines

    guarding = []
    for child in ast.walk(node):
        if not isinstance(child, ast.If):
            continue
        test_src = ast.dump(child.test)
        if "marketplace_product" not in test_src:
            continue
        if _gate_calls(child, aliases).get("screen"):
            guarding.append(child.lineno)
    assert guarding, ("the buy-now screen call is not inside an "
                      "item_type == 'marketplace_product' guard")


def test_the_lanes_share_one_gate_module():
    """Named literally, so a fourth lane cannot import a private fork.

    The settlement invariant this file is modelled on was defeated once by a guard
    that read only ``bot.py``. Reading all three by name is the cheap half of the
    lesson.
    """
    for _, rel_path, _ in LANES:
        with open(os.path.join(REPO_ROOT, rel_path), encoding="utf-8") as handle:
            source = handle.read()
        assert "marketplace_supplier_checkout" in source, rel_path
