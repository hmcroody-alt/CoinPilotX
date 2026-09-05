"""The UNDX Capital Graph capability — registration, honesty, and reach.

Run either way::

    python -m pytest tests/private_office/test_capital_undx_capability.py
    python tests/private_office/test_capital_undx_capability.py

What this file defends
----------------------
``private.capital.portfolio`` is the one door UNDX has into the member's
Capital Graph, and its value — like ``capital_graph``'s — is mostly in what it
refuses to do. None of those refusals are visible in a working call, so each
is pinned here:

* **Registered everywhere or nowhere.** The capability id must appear in the
  registry, the policy ledger, the knowledge map, the executor table and the
  human-reviewed authorization baseline, all derived from the one spec module.
  A capability present in some surfaces and absent from others is the
  half-wired state the spec-module pattern exists to prevent.
* **Zero fields.** There is no argument at all, so there is nothing a model
  could widen — no owner, no symbol filter, no future "just one more field"
  that carries another account.
* **Honest numbers or none.** ``totals.value`` is ``None`` whenever any
  holding lacks a live quote, and per-asset unknowns are ``None``, never 0.
  The hook must relay that refusal untouched.
* **Evidence-cited.** Every returned row carries the fact ids and provenance
  the projection wrote, so an answer can say *why* the holding is believed.
* **No advice.** The description and intents ask about state; none of them
  request judgment, ranking, or action.
* **The hook is thin.** No SQL of its own, no second authorization gate — the
  owner gate lives in ``portfolio_view`` and a second one is a second place
  for the two to disagree.
"""

import ast
import importlib.util
import inspect
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_capital_undx_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ.setdefault("PORTFOLIO_CAPITAL_PROJECTION_ENABLED", "1")

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from services import db  # noqa: E402
from services import market_data  # noqa: E402
from services import portfolio_events  # noqa: E402
from services.private_office import portfolio_projection as projection  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import undx_capital_spec as spec  # noqa: E402

USER_A = 9901
USER_B = 9902

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    text = f"{label}{(' — ' + str(detail)) if detail != '' else ''}"
    _FAILURES.append(text)
    print(f"  FAIL  {text}")
    return False


def _connect():
    conn = db.connect()
    cur = conn.cursor()
    schema.ensure_private_schema(cur)
    portfolio_events.ensure_outbox_schema(cur)
    return conn, cur


def _board(symbols: dict[str, float]) -> dict:
    return {
        "source": "coingecko", "observed_epoch": 1_756_700_000,
        "age_seconds": 12, "warning": "",
        "markets": [
            {"symbol": symbol, "price": price, "change_24h": 1.5}
            for symbol, price in symbols.items()
        ],
    }


def setup_environment() -> None:
    schema.reset_schema_cache()
    portfolio_events.reset_outbox_schema_cache()
    conn, cur = _connect()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS portfolio_items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
        "symbol TEXT NOT NULL, coin_name TEXT NOT NULL DEFAULT '', "
        "amount REAL NOT NULL DEFAULT 0, average_buy_price REAL)"
    )
    # Two BTC lots with a stated basis; one XYZ lot with none. The aggregate
    # BTC basis is knowable, XYZ's is not — and XYZ will also go unpriced.
    cur.execute("INSERT INTO portfolio_items (user_id, symbol, coin_name, amount, average_buy_price) VALUES (?,?,?,?,?)",
                (USER_A, "BTC", "Bitcoin", 0.5, 40000.0))
    cur.execute("INSERT INTO portfolio_items (user_id, symbol, coin_name, amount, average_buy_price) VALUES (?,?,?,?,?)",
                (USER_A, "BTC", "Bitcoin", 0.25, 44000.0))
    cur.execute("INSERT INTO portfolio_items (user_id, symbol, coin_name, amount, average_buy_price) VALUES (?,?,?,?,?)",
                (USER_A, "XYZ", "Xyz Token", 100.0, None))
    portfolio_events.enqueue(cur, user_id=USER_A,
                             event_type=portfolio_events.EVENT_BACKFILL)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Registration — all five surfaces, or none
# ---------------------------------------------------------------------------

def stage_registered_everywhere() -> None:
    print("\n[registration]")

    # Imported inside the stage so a broken registry reads as "registration is
    # red", not as this whole suite failing to collect.
    from services import undx_agent_tools as tools
    from services import undx_capability_registry as registry
    from services import undx_knowledge_map as knowledge_map
    from services import undx_policy as policy

    cid = spec.CAPABILITY_ID
    reg = registry.REGISTRY.get(cid)
    if not check("the capability is in the registry", reg is not None):
        return

    check("the registry entry is read_only", reg.risk == "read_only", reg.risk)
    check("the registry entry never confirms", reg.confirmation == "never",
          reg.confirmation)
    check("the registry scope is the caller's own account",
          reg.permission == "self_account_only", reg.permission)
    check("the registry declares zero fields", reg.fields == (), str(reg.fields))
    check("the registry has no verifier, because nothing is written",
          reg.verifier == "", reg.verifier)
    check("the tool name is derived, not restated",
          reg.tool_name == spec.tool_name(cid), reg.tool_name)
    check("the audit category names a capital read",
          reg.audit_category == spec.AUDIT_CATEGORY, reg.audit_category)
    check("the native route is the Capital Graph screen",
          reg.native_route == spec.SPEC["native_route"], reg.native_route)

    entry = policy.PRODUCTION_TOOL_REGISTRY.get(spec.tool_name(cid))
    if check("the tool is in the production policy ledger", entry is not None):
        check("the ledger agrees the read is read_only",
              entry.get("risk") == "read_only", str(entry))
        check("the ledger requires no confirmation",
              entry.get("confirmation") is False, str(entry))
        check("the ledger routes to the sanctioned view",
              entry.get("route") == spec.SERVICE_ROUTE, str(entry.get("route")))
        check("the ledger's canonical key is the caller",
              entry.get("canonical_key") == "user_id", str(entry))

    record = knowledge_map.BY_ID.get(cid)
    if check("the capability has a knowledge-map record", record is not None):
        check("the map scope is self_account_only",
              record.authorization_scope == "self_account_only",
              record.authorization_scope)
        check("the map names a real screen",
              record.native_screen in knowledge_map.NATIVE_ROUTES,
              record.native_screen)
        check("the map's limitations state the null-total contract",
              any("null" in text for text in record.known_limitations))
        check("the map's limitations state no advice and no execution",
              any("no advice" in text and "no execution" in text
                  for text in record.known_limitations))

    executor = tools.EXECUTORS.get(spec.executor_name(cid))
    if check("the executor is registered under the derived name",
             executor is not None):
        check("the executor is the capital read",
              executor.__name__ == "private_capital_portfolio",
              executor.__name__)

    baseline_path = os.path.join(
        _REPO_ROOT, "tests", "undx_agent", "authorization_surface_baseline.py")
    loader = importlib.util.spec_from_file_location("_surface_baseline",
                                                    baseline_path)
    baseline = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(baseline)
    rows = [row for row in baseline.AUTHORIZATION_SURFACE if row[0] == cid]
    if check("the human-reviewed baseline records the capability",
             len(rows) == 1, str(len(rows))):
        row = rows[0]
        check("the baseline row is a read with no write, verifier or fields",
              row[1] == "read_only" and row[2] == "never"
              and row[5] is False and row[8] == "" and row[9] == (), str(row))


# ---------------------------------------------------------------------------
# The declared surface
# ---------------------------------------------------------------------------

def stage_read_only_and_no_advice() -> None:
    print("\n[read only, no advice]")

    check("the declared risk is read_only", spec.RISK == "read_only", spec.RISK)
    check("no confirmation is declared, because nothing is written",
          spec.CONFIRMATION == "never", spec.CONFIRMATION)
    check("the permission scope is the caller's own account",
          spec.PERMISSION == "self_account_only", spec.PERMISSION)
    check("the audit category names a read",
          spec.AUDIT_CATEGORY == "private_capital_read", spec.AUDIT_CATEGORY)
    check("the service route is the sanctioned view",
          spec.SERVICE_ROUTE
          == "services.private_office.portfolio_projection.portfolio_view",
          spec.SERVICE_ROUTE)
    check("the capability id names no table and no verb",
          spec.CAPABILITY_ID == "private.capital.portfolio", spec.CAPABILITY_ID)
    check("the tool name follows the shipped convention",
          spec.tool_name(spec.CAPABILITY_ID) == "pulsesoc.private_capital.portfolio",
          spec.tool_name(spec.CAPABILITY_ID))

    prose = " ".join((spec.SPEC["description"],) + spec.SPEC["intents"]).lower()
    for word in ("recommend", "advice", "advise", "suggest", "should i",
                 "best ", "buy", "sell", "trade", "forecast", "predict"):
        check(f"the vocabulary avoids {word!r}", word not in prose)

    signature = inspect.signature(spec.execute)
    check("the hook accepts no model-authored arguments",
          list(signature.parameters) == ["cur", "owner_user_id"],
          str(signature))
    owner = signature.parameters["owner_user_id"]
    check("owner_user_id is keyword-only on the service hook",
          owner.kind is inspect.Parameter.KEYWORD_ONLY, str(owner.kind))
    check("owner_user_id has no default a caller could omit",
          owner.default is inspect.Parameter.empty, str(owner.default))

    check("the asset allowlist keeps evidence and drops graph plumbing",
          "evidence" in spec._ASSET_FIELDS
          and "freshness" not in spec._ASSET_FIELDS
          and "node_id" not in spec._ASSET_FIELDS,
          str(spec._ASSET_FIELDS))


def stage_hook_is_thin() -> None:
    """No SQL of its own and no second gate."""
    print("\n[hook shape]")

    tree = ast.parse(inspect.getsource(spec))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    body = ast.unparse(tree)

    for statement in ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE TABLE"):
        check(f"the spec module issues no {statement}",
              statement not in body.upper())
    check("the hook calls the sanctioned view", "portfolio_view(" in body)
    for banned in ("decide(", "is_owner", "has_entitlement", "unlock",
                   "passcode", "resolve_tier"):
        check(f"the hook runs no authorization of its own ({banned})",
              banned not in body)


# ---------------------------------------------------------------------------
# The service hook, against a real projection
# ---------------------------------------------------------------------------

def stage_reads_with_evidence_and_honest_totals() -> None:
    print("\n[read]")
    conn, cur = _connect()
    original_board = market_data.live_market_board
    market_data.live_market_board = lambda **kwargs: _board({"BTC": 60000.0})
    try:
        result = spec.execute(cur, owner_user_id=USER_A)
        conn.commit()
    finally:
        market_data.live_market_board = original_board

    if not check("the owner's read succeeds", result.get("ok") is True,
                 str(result)[:200]):
        conn.close()
        return

    records = result["records"]
    check("both projected assets are returned",
          result["counts"]["returned"] == 2 and len(records) == 2,
          str(result["counts"]))
    by_symbol = {row.get("symbol"): row for row in records}
    check("the rows are the seeded symbols",
          set(by_symbol) == {"BTC", "XYZ"}, str(sorted(by_symbol)))

    for row in records:
        check(f"{row.get('symbol')} crosses the boundary through the allowlist",
              tuple(sorted(row)) == tuple(sorted(spec._ASSET_FIELDS)),
              str(sorted(row)))

    btc = by_symbol.get("BTC") or {}
    check("BTC aggregates its two lots into one asset",
          btc.get("quantity") == 0.75 and btc.get("lot_count") == 2, str(btc))
    check("BTC is priced by the live quote",
          btc.get("priced") is True and btc.get("price") == 60000.0
          and btc.get("value") == 45000.0, str(btc))
    check("BTC's basis is the sum of its lots",
          btc.get("cost_basis") == 0.5 * 40000.0 + 0.25 * 44000.0,
          str(btc.get("cost_basis")))
    evidence = btc.get("evidence") or {}
    check("BTC carries the projection's evidence",
          bool(evidence.get("fact_ids")) and evidence.get("provenance") is not None,
          str(evidence)[:200])

    xyz = by_symbol.get("XYZ") or {}
    check("an unpriced holding carries None, never zero",
          xyz.get("priced") is False and xyz.get("price") is None
          and xyz.get("value") is None, str(xyz))
    check("an unknowable basis is None, not a partial sum",
          xyz.get("cost_basis") is None, str(xyz.get("cost_basis")))

    totals = result["totals"]
    check("an incomplete set refuses to total",
          totals.get("value") is None and totals.get("complete") is False,
          str(totals))
    check("the refusal names the unpriced symbol",
          totals.get("unpriced_symbols") == ["XYZ"]
          and totals.get("priced") == 1, str(totals))

    prices = result["prices"]
    check("the price feed confesses its own freshness",
          prices.get("source") == "coingecko"
          and prices.get("age_seconds") == 12, str(prices))
    check("the sync report crossed the boundary",
          "pending" in (result.get("sync") or {}), str(result.get("sync")))
    conn.close()


def stage_totals_when_everything_priced() -> None:
    print("\n[complete totals]")
    conn, cur = _connect()
    original_board = market_data.live_market_board
    market_data.live_market_board = lambda **kwargs: _board(
        {"BTC": 60000.0, "XYZ": 2.0})
    try:
        result = spec.execute(cur, owner_user_id=USER_A)
        conn.commit()
    finally:
        market_data.live_market_board = original_board

    totals = result.get("totals") or {}
    check("a fully priced set does total",
          totals.get("complete") is True
          and totals.get("value") == 45000.0 + 200.0, str(totals))
    check("no symbol is reported unpriced",
          totals.get("unpriced_symbols") == [], str(totals))
    conn.close()


def stage_denials_and_isolation() -> None:
    print("\n[denial and isolation]")
    conn, cur = _connect()
    original_board = market_data.live_market_board
    market_data.live_market_board = lambda **kwargs: _board({"BTC": 60000.0})
    try:
        ownerless = spec.execute(cur, owner_user_id=0)
        check("an ownerless call is denied with no records",
              ownerless.get("ok") is False and ownerless.get("records") == []
              and ownerless["counts"]["returned"] == 0, str(ownerless)[:200])
        check("the denial arrives verbatim from the view",
              (ownerless.get("denied") or {}).get("reason") == "actor_is_not_owner",
              str(ownerless.get("denied")))

        theirs = spec.execute(cur, owner_user_id=USER_B)
        check("B's portfolio is empty, not refused",
              theirs.get("ok") is True and theirs["counts"]["returned"] == 0,
              str(theirs)[:200])
        check("none of A's holdings reach B",
              "BTC" not in repr(theirs.get("records"))
              and "XYZ" not in repr(theirs.get("records")),
              repr(theirs.get("records"))[:200])
        conn.commit()
    finally:
        market_data.live_market_board = original_board
    conn.close()


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    setup_environment()
    stage_registered_everywhere()
    stage_read_only_and_no_advice()
    stage_hook_is_thin()
    stage_reads_with_evidence_and_honest_totals()
    stage_totals_when_everything_priced()
    stage_denials_and_isolation()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_capital_undx_capability():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
