"""Which seller routes are gated, pinned — so the next one cannot be forgotten.

`seller_access_refusal` says in its own docstring that "every seller mutation
route calls this and nothing else". That was written before it was true: two
listing routes, three upload routes and the payout-onboarding route were still
on `approved_marketplace_seller_for_user`, a single-table read that returns a
bare 403 with no access state, so a client could not route the seller anywhere
from it — and it cannot see a divergence between `marketplace_sellers` and
`business_os_mkt_sellers` at all.

That gap survived a read of the docstring, a passing test run and very nearly a
report claiming full coverage. Per-route tests would not have caught it either,
because the failure is an *absence*: nobody writes a test for the route they
forgot. So this is a census rather than a set of cases. It walks bot.py's AST,
finds every write route in the seller namespaces, and requires each one to
either reach a gate or appear below with a reason.

Default-deny: a new write route under those namespaces fails this test until
somebody decides which side it is on. That is the point — the decision is
cheap, and the omission is what costs.
"""

import ast
import functools
import os
import re

import pytest

BOT_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")

#: The chokepoints. `_seller_profile_access_refusal` is a thin wrapper that opens
#: its own connection and delegates; reaching either one is reaching the gate.
GATE_FUNCTIONS = {"seller_access_refusal", "_seller_profile_access_refusal"}

#: Route namespaces where a write is a seller acting on seller-owned things.
SELLER_WRITE_NAMESPACES = re.compile(
    r"/api/pulse/(?:"
    r"marketplace/seller/"
    r"|marketplace/media/"
    r"|marketplace/digital-files/"
    r"|business/profile"
    r"|payouts/connect"
    r"|seller/"
    r"|payments/seller/"
    r")"
)

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: Routes in those namespaces that must NOT be gated, each with the reason.
#:
#: Two reasons only, and both are structural rather than convenient:
#:
#:   1. The application routes are the only way to *clear* the gate. Gating them
#:      is the closed loop where a seller is told to finish their application,
#:      sent to the application, and refused entry to it.
#:   2. Obligations outlive privileges. A suspended seller still owes their
#:      buyers fulfilment, refunds and settlement, so the routes that discharge
#:      those debts stay open — the same rule `can_manage_existing_orders`
#:      encodes on the read side.
UNGATED_BY_DESIGN = {
    "/api/pulse/marketplace/seller/apply": "applying is how the gate is cleared",
    "/api/pulse/seller/application/draft": "applying is how the gate is cleared",
    "/api/pulse/seller/application/submit": "applying is how the gate is cleared",
    "/api/pulse/seller/application/withdraw": "applying is how the gate is cleared",
    "/api/pulse/seller/application/documents": "applying is how the gate is cleared",
    "/api/pulse/seller/application/documents/<int:document_id>/remove":
        "applying is how the gate is cleared",
    "/api/pulse/payments/seller/payouts":
        "a suspended seller is still owed money they already earned",
    "/api/pulse/payments/seller/orders/<int:transaction_id>/cash-collected":
        "settling a completed sale is an obligation, not a privilege",
}


@functools.lru_cache(maxsize=1)
def _tree():
    """bot.py's AST, parsed once.

    111k lines costs a few seconds to parse, and every check here walks the
    same tree. Parsing per test turned a structural check into a 96-second
    one, which is how a useful test quietly stops being run.
    """
    return ast.parse(open(BOT_PY, encoding="utf-8").read())


@functools.lru_cache(maxsize=1)
def _routes():
    """Every `@*.route(...)` in bot.py, with its methods and whether it gates.

    Read from the AST rather than from the Flask url_map: importing bot.py
    registers optional route packs inside `except Exception` blocks, so a
    subsystem that failed to import would silently shrink the census into
    passing. The source cannot fail to import.
    """
    tree = _tree()
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        called = {
            n.func.id
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        gated = bool(called & GATE_FUNCTIONS)
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            if dec.func.attr != "route" or not dec.args:
                continue
            if not isinstance(dec.args[0], ast.Constant):
                continue
            methods = {"GET"}
            for kw in dec.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    methods = {
                        e.value for e in kw.value.elts if isinstance(e, ast.Constant)
                    }
            found.append((dec.args[0].value, frozenset(methods), gated, node.name))
    return tuple(found)


@functools.lru_cache(maxsize=1)
def _seller_writes():
    return tuple(
        r for r in _routes()
        if (r[1] & WRITE_METHODS) and SELLER_WRITE_NAMESPACES.search(r[0])
    )


def test_the_census_actually_finds_routes():
    """A guard against the whole file passing because it found nothing.

    Every assertion below is over a list. An AST walk that silently matched
    zero routes — a decorator shape change, a moved file — would make each of
    them vacuously true, and this file would go on reporting success about a
    gate it was no longer looking at.
    """
    writes = _seller_writes()
    assert len(writes) >= 15, f"census collapsed to {len(writes)} routes"
    gated = [r for r in writes if r[2]]
    assert len(gated) >= 10, f"only {len(gated)} gated routes found"


def test_every_seller_write_route_reaches_the_gate_or_is_listed():
    ungated = sorted(
        {path for path, _m, gated, _n in _seller_writes()
         if not gated and path not in UNGATED_BY_DESIGN}
    )
    assert not ungated, (
        "These seller write routes do not reach `seller_access_refusal`:\n  "
        + "\n  ".join(ungated)
        + "\n\nGate them, or add them to UNGATED_BY_DESIGN with the reason they "
          "must stay open. Ownership is not approval: a seller suspended for "
          "fraud still owns their listings."
    )


def test_the_exemption_list_has_no_dead_entries():
    """A stale exemption is a hole nobody is looking at.

    If a route is renamed or removed, its entry here keeps a *future* route at
    that path exempt by accident. Same reasoning as the route-contract
    allowlist: an entry that no longer describes anything must be deleted in
    the commit that stopped it describing something.
    """
    live = {path for path, _m, _g, _n in _seller_writes()}
    dead = sorted(set(UNGATED_BY_DESIGN) - live)
    assert not dead, f"UNGATED_BY_DESIGN names routes that no longer exist: {dead}"


def test_no_exemption_is_silently_gated_as_well():
    """The exemptions must really be ungated, or the list is fiction.

    If one of these grows a gate, the reason written beside it has stopped
    being true and the entry is now lying about the system — most likely by
    closing the loop where applying is the only way out.
    """
    contradictions = sorted(
        {path for path, _m, gated, _n in _seller_writes()
         if gated and path in UNGATED_BY_DESIGN}
    )
    assert not contradictions, (
        "These are listed as ungated by design but now reach the gate: "
        f"{contradictions}"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/pulse/marketplace/seller/listings/<int:listing_id>",
        "/api/pulse/marketplace/seller/listings/<int:listing_id>/pause",
        "/api/pulse/marketplace/seller/listings/<int:listing_id>/resume",
        "/api/pulse/marketplace/seller/listings/<int:listing_id>/delete",
        "/api/pulse/marketplace/seller/listings/<int:listing_id>/submit",
        "/api/pulse/marketplace/seller/listings/batch",
        "/api/pulse/marketplace/media/upload",
        "/api/pulse/marketplace/media/attach",
        "/api/pulse/marketplace/digital-files/upload",
        "/api/pulse/business/profile",
        "/api/pulse/business/profile/hours",
        "/api/pulse/business/profile/link",
        "/api/pulse/business/profile/address",
        "/api/pulse/business/profile/publish",
        "/api/pulse/payouts/connect",
    ],
)
def test_named_route_is_gated(path):
    """The routes this change was about, named one by one.

    Redundant with the census above and kept anyway: the census would still
    pass if the namespace regex drifted off one of these, and naming them makes
    a regression say *which* surface came unlocked rather than reporting a
    count.
    """
    gated = {p for p, _m, g, _n in _seller_writes() if g}
    assert path in gated, f"{path} no longer reaches seller_access_refusal"


def test_the_legacy_single_table_gate_is_gone_from_seller_writes():
    """No seller write route may still decide approval from one table.

    `approved_marketplace_seller_for_user` reads `marketplace_sellers` alone,
    so it cannot see a seller suspended in `business_os_mkt_sellers` — the
    exact divergence the reconciliation exists to close. It has legitimate
    remaining callers (the dual-lane teacher paths, and buyer checkout asking
    about *someone else's* approval), so it is not dead; it just must not be
    what decides a seller's own write.
    """
    offenders = []
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.FunctionDef):
            continue
        paths = [
            d.args[0].value
            for d in node.decorator_list
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "route"
            and d.args
            and isinstance(d.args[0], ast.Constant)
        ]
        seller_paths = [
            p for p in paths
            if SELLER_WRITE_NAMESPACES.search(p) and p not in UNGATED_BY_DESIGN
        ]
        if not seller_paths:
            continue
        called = {
            n.func.id
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        if "approved_marketplace_seller_for_user" in called:
            offenders.extend(seller_paths)
    assert not sorted(set(offenders)), (
        "These seller routes still decide approval from `marketplace_sellers` "
        f"alone: {sorted(set(offenders))}"
    )
