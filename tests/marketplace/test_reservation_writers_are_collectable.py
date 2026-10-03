"""Every lane that holds stock must write a deadline the sweeper can see.

Why this is a whole-codebase invariant and not a per-lane test
--------------------------------------------------------------
``marketplace_reservation_sweeper`` selects its candidates with::

    status = 'held' AND expires_at IS NOT NULL AND expires_at <> ''
             AND expires_at <= cutoff

That predicate is correct — inventing a retroactive deadline for a row that
never had one would release stock out from under an order that may still be
mid-flight. But it has a consequence that is easy to miss: a reservation
written *without* ``expires_at`` is not merely collected late, it is
**permanently invisible**. No sweep cycle will ever consider it, no
``payment_intent.canceled`` webhook fires for a dismissed Apple Pay sheet, and
so the decremented ``marketplace_listings.quantity`` never comes back. The
stock is gone for the lifetime of the row.

Measured in production on 2026-10-02, before this file existed: **all eleven**
``marketplace_inventory_reservations`` rows had no deadline — 7 ``released``
and 4 still ``held``, the oldest stranded since 2026-08-13. Four units of real
sellable inventory, unreachable.

The reason that happened is the reason this test is written the way it is.
There are *three* separate writers of this table, each in its own lane:

* ``services/marketplace_cart_routes.py``     — Cart checkout
* ``services/marketplace_offers_routes.py``   — Offer acceptance
* ``bot.py`` ``api_pulse_payments_checkout``  — Buy Now  (**the live one**)

Two of them were repaired one at a time, each with its own lane-scoped test,
and each repair looked complete. The leak continued, because the third writer
was still minting fresh deadline-less holds. A test that asserts "the cart
lane writes ``expires_at``" cannot fail when somebody adds a fourth lane, and
a fourth lane is exactly what the next feature looks like. So this test does
not name lanes. It *discovers* the writers and holds every one of them to the
same contract, which means a new lane either satisfies the lifecycle or turns
this file red on the commit that introduces it.

How it finds them
-----------------
By parsing, never by importing. ``import bot`` runs ``init_db()`` at module
scope and costs minutes on a cold ``__pycache__``; ``ast.parse`` on the same
file is milliseconds and has no side effects at all.

Two parsing traps are deliberately avoided:

* Line numbers are never used to re-read source text. ``ast`` and
  ``str.splitlines()`` disagree about what a line is, because ``splitlines``
  breaks on U+2028/U+2029 and the Python tokenizer does not. ``bot.py``
  embeds JavaScript containing those characters, so any index built from
  ``splitlines()`` is shifted relative to ``node.lineno``. Everything here
  works on the nodes themselves.
* The SQL is recovered from the whole expression, not from
  ``node.args[0].value``. Adjacent string literals are folded by the parser,
  but a writer is free to use an f-string or ``"..." + x``, and reading only
  a bare ``Constant`` would silently skip it — the failure mode where the
  test finds zero writers and passes for the wrong reason. The
  ``test_the_discovery_itself_works`` case below exists to make that
  impossible to regress.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(REPO))

from services import marketplace_reservation_policy as policy

TABLE = "marketplace_inventory_reservations"

#: Production source only. Test helpers legitimately insert bare rows — that is
#: how the sweeper's "legacy row" cases are set up — so including ``tests/``
#: here would make the invariant unassertable.
SOURCE_ROOTS = ("bot.py", "services")


def _python_sources():
    yield REPO / "bot.py"
    for path in sorted((REPO / "services").rglob("*.py")):
        yield path


def _sql_text(node: ast.AST) -> str:
    """Every string constant anywhere inside an expression, concatenated.

    Covers a plain literal, implicit adjacent-literal concatenation, an
    f-string, and ``"..." + variable``. A writer that assembles its SQL from a
    name this cannot see is invisible to the audit — which is what the
    accounting assertion in :func:`test_the_discovery_itself_works` is for.
    """
    return " ".join(
        n.value for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )


class Writer:
    """One ``INSERT INTO marketplace_inventory_reservations`` call site."""

    def __init__(self, path: Path, node: ast.Call, sql: str, scope: ast.AST | None):
        self.path = path.relative_to(REPO)
        self.node = node
        self.sql = sql
        #: The enclosing ``def``. The deadline is not always computed in the
        #: argument list — see :func:`test_no_writer_invents_its_own_ttl`.
        self.scope = scope if scope is not None else node
        self.lineno = node.lineno

    @property
    def columns(self) -> set[str]:
        """The column names in the INSERT's parenthesised column list."""
        m = re.search(
            rf"INSERT\s+INTO\s+{TABLE}\s*\((?P<cols>[^)]*)\)",
            self.sql, re.I | re.S)
        if not m:
            return set()
        return {c.strip().lower() for c in m.group("cols").split(",") if c.strip()}

    def __repr__(self):
        return f"{self.path}:{self.lineno}"


def _discover_writers() -> list[Writer]:
    found = []
    for path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - a broken file is its own failure
            continue
        # Walk functions rather than the bare tree so each INSERT keeps a
        # handle on the scope that computed its arguments.
        scopes = [n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        owner = {}
        for scope in scopes:
            for node in ast.walk(scope):
                # The innermost enclosing def wins, so a nested helper is not
                # attributed to the outer route.
                if isinstance(node, ast.Call):
                    prev = owner.get(id(node))
                    if prev is None or scope.lineno > prev.lineno:
                        owner[id(node)] = scope
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            sql = _sql_text(node)
            if not re.search(rf"INSERT\s+INTO\s+{TABLE}\b", sql, re.I):
                continue
            # ``cur.execute(...)`` and ``conn.execute(...)`` both qualify; a
            # bare helper that merely *returns* the SQL string does not, and is
            # caught by its caller instead.
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                    "execute", "executemany"}:
                found.append(Writer(path, node, sql, owner.get(id(node))))
    return found


WRITERS = _discover_writers()


def test_the_discovery_itself_works():
    """Guard against a vacuous pass.

    Every assertion below is a loop over ``WRITERS``. If the discovery breaks
    — a rename, a reformat that defeats the regex, an SQL string assembled
    from a variable — the loops run zero times and this whole file goes green
    while the invariant is unprotected. That is the single most likely way for
    this test to lie, so it is checked directly.

    The count is pinned deliberately rather than asserted ``> 0``. Three lanes
    hold stock today and each one is named in this module's docstring. A fourth
    appearing is not a failure of correctness, but it *is* a thing a human
    should look at, so it fails here and asks to be added to the list.
    """
    assert WRITERS, (
        f"found no INSERT INTO {TABLE} in {SOURCE_ROOTS} — the audit below is "
        "asserting nothing. Check the table name and _sql_text().")
    paths = {str(w.path) for w in WRITERS}
    assert paths == {
        "bot.py",
        "services/marketplace_cart_routes.py",
        "services/marketplace_offers_routes.py",
    }, f"the set of lanes that hold stock changed: {sorted(paths)}"
    # And each one really did yield a parsed column list, not an empty set that
    # would satisfy a subset check trivially.
    for w in WRITERS:
        assert w.columns, f"{w} matched the table but parsed no column list"


@pytest.mark.parametrize("writer", WRITERS, ids=repr)
def test_every_writer_persists_a_deadline(writer):
    """The column that decides whether stock is ever returned.

    Without ``expires_at`` the sweeper's ``expires_at IS NOT NULL`` term makes
    the row unreachable forever, so this is not a tidiness rule — it is the
    difference between a hold that resolves itself and inventory that is
    silently destroyed.
    """
    assert "expires_at" in writer.columns, (
        f"{writer} holds stock with no deadline. The sweeper selects on "
        "`expires_at IS NOT NULL`, so this hold can never be collected and "
        "its units never return to the listing.")


@pytest.mark.parametrize("writer", WRITERS, ids=repr)
def test_every_writer_records_when_the_hold_began(writer):
    """``reserved_at`` is what makes the deadline auditable after the fact.

    ``expires_at`` alone says when a hold dies but not when it started, so a
    stranded row cannot be distinguished from a freshly-taken one without it,
    and no "confirmation age" metric the directive asks for can be computed.
    """
    assert "reserved_at" in writer.columns, (
        f"{writer} writes a deadline but no reservation start, so the hold's "
        "age cannot be measured")


@pytest.mark.parametrize("writer", WRITERS, ids=repr)
def test_no_writer_invents_its_own_ttl(writer):
    """The deadline must come from the policy module, not a local literal.

    Three lanes computing their own TTL is three chances to drift, and a lane
    whose window is shorter than the Stripe sheet's own lifetime releases
    stock under a buyer who is still typing a card number. ``expires_at_for``
    is the single source of truth; this asserts the call site reaches for it
    rather than reaching for a number.

    Searched over the enclosing **function**, not over the ``execute()`` call.
    The first version of this test looked only inside the ``Call`` node and
    failed the cart lane — wrongly. Cart hoists
    ``reservation_expires_at = reservation_policy.expires_at_for(now)`` above
    its per-line loop, which is not a defect but the more correct shape: every
    line in one cart group is one order and should share one deadline, rather
    than each line getting a slightly later one as the loop advances. A test
    that forces the policy call into the argument list would have pushed a
    real improvement back out of the code, so the scope is the function.
    """
    names = {
        n.attr for n in ast.walk(writer.scope)
        if isinstance(n, ast.Attribute)
    }
    assert "expires_at_for" in names or "legacy_backfill_expiry" in names, (
        f"{writer} does not reach reservation_policy.expires_at_for() anywhere "
        f"in {getattr(writer.scope, 'name', '?')}() — a TTL spelled locally "
        "will drift from the policy")


@pytest.mark.parametrize("writer", WRITERS, ids=repr)
def test_every_writer_is_idempotent_on_the_transaction(writer):
    """One transaction, at most one hold.

    A retry that re-ran the INSERT without this would decrement stock twice
    for one order. The ``seller_transaction_id`` uniqueness plus ``DO NOTHING``
    is what makes a replayed checkout a no-op rather than a second hold.
    """
    assert re.search(r"ON\s+CONFLICT\s*\(\s*seller_transaction_id\s*\)\s*DO\s+NOTHING",
                     writer.sql, re.I), (
        f"{writer} can create a second hold for one transaction on retry")


def test_the_policy_deadline_is_actually_in_the_future():
    """A sanity check on the value the writers are required to persist.

    If ``expires_at_for`` ever returned something at or before its own base
    instant, every lane would write a deadline that the sweeper collects on
    its next cycle — releasing stock for orders that are seconds old. The
    writers above are asserted to *call* this function; this asserts the
    function is worth calling.
    """
    base = "2026-10-02T12:00:00+00:00"
    deadline = policy.expires_at_for(base)
    assert deadline > base, f"TTL is not forward-going: {base} -> {deadline}"
    assert policy.reservation_ttl_seconds() > 0
    # And a hold taken now is not instantly collectable, grace period included.
    assert not policy.is_expired(deadline, now=base)
