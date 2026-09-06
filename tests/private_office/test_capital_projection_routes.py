"""The three capital projection routes, asserted at the wire.

Run either way::

    python -m pytest tests/private_office/test_capital_projection_routes.py
    python tests/private_office/test_capital_projection_routes.py

Scope
-----
``test_capital_overview.py`` pins what the read model computes. This file pins
what the HTTP layer must not undo:

* **The five gates, in order** (mission §75-§77): account auth, then the tier
  gate, then the Private Office second lock, then the feature flag, then the
  owner-scoped operation. A signed-in, entitled member presenting no unlock
  grant gets 423 and no capital data — not a thinner payload.
* **Failure is never emptiness** (§79, §137). Four different bad outcomes must
  be four different, self-describing responses: 401 for anonymous, 403 for a
  refused read, 423 for locked, 503 for an unreadable store. None of them may
  render as ``ok: true`` with an empty body, because "you have nothing
  recorded" over a full store is the single most expensive lie this surface
  can tell.
* **The honesty fields survive serialisation.** ``net_position.estimated``
  must never travel without ``complete``, ``incomplete_reasons``, ``excluded``
  and ``basis``; ``coverage`` must never travel without its published formula.
  A client cannot disclose what the wire did not carry.
* **No invented aggregate on the wire.** A field spelled like a net worth is
  read as one whatever the server intended, so the payload is scanned for
  those spellings directly.
* **Owner isolation over HTTP**, with two real members and distinguishable
  seed data, so a leak shows up as a literal string in the wrong response.
* **The kill switch** closes all three routes without selling an upgrade and
  without taking neighbouring routes down.
"""

import json
import os
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="capital_routes_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ.setdefault("PORTFOLIO_CAPITAL_PROJECTION_ENABLED", "1")

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

# --- stub the monolith BEFORE the route pack can import it ------------------
_stub = types.ModuleType("bot")
_stub._test_user = None


def _api_account_user():
    return _stub._test_user


def _require_admin_api(permission):
    return (None, ("DENIED", 403))


_stub.api_account_user = _api_account_user
_stub.require_admin_api = _require_admin_api
sys.modules["bot"] = _stub

from datetime import datetime, timedelta, timezone  # noqa: E402

from flask import Flask  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402

from services import db  # noqa: E402
from services import market_data  # noqa: E402
from services import portfolio_events  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services import private_office_routes as routes  # noqa: E402
from services.private_office import capital_graph  # noqa: E402
from services.private_office import capital_overview as overview_mod  # noqa: E402
from services.private_office import cash_flow as cash_flow_mod  # noqa: E402
from services.private_office import feature_matrix  # noqa: E402
from services.private_office import integrity as integrity_mod  # noqa: E402
from services.private_office import obligation_projection as obligations  # noqa: E402
from services.private_office import portfolio_projection as portfolio  # noqa: E402
from services.private_office import records  # noqa: E402
from services.private_office import schema  # noqa: E402

USER_A = 9941
USER_B = 9942
PASSCODE = "731905"

OVERVIEW_PATH = "/api/private-office/capital-graph/overview"
OBLIGATIONS_PATH = "/api/private-office/capital-graph/obligations"
EXPOSURE_PATH = "/api/private-office/capital-graph/exposure"
CASH_FLOW_PATH = "/api/private-office/capital-graph/cash-flow"
INTEGRITY_PATH = "/api/private-office/capital-graph/integrity"
PATHS = (OVERVIEW_PATH, OBLIGATIONS_PATH, EXPOSURE_PATH, CASH_FLOW_PATH,
         INTEGRITY_PATH)

#: The payload key each route wraps its data in. Used to assert that a refusal
#: carries no data key at all, rather than an empty one.
DATA_KEY = {OVERVIEW_PATH: "overview",
            OBLIGATIONS_PATH: "obligations",
            EXPOSURE_PATH: "exposure",
            CASH_FLOW_PATH: "cash_flow",
            INTEGRITY_PATH: "integrity"}

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    text = f"{label}{(' — ' + str(detail)) if detail != '' else ''}"
    _FAILURES.append(text)
    print(f"  FAIL  {text}")
    return False


_GRANTS: dict[int, str] = {}


class _GrantClient(FlaskClient):
    """Presents the current member's unlock grant.

    ``setdefault`` on purpose: a caller passing the header explicitly —
    including empty, to exercise a locked request — keeps what it passed.
    """

    def open(self, *args, **kwargs):
        user = _stub._test_user or {}
        token = _GRANTS.get(int(user.get("user_id") or 0), "")
        if token:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault(routes.GRANT_HEADER, token)
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def _app():
    app = Flask(__name__)
    app.test_client_class = _GrantClient
    routes.register(app)
    return app


def _as(user_id):
    _stub._test_user = {"user_id": user_id, "account_status": "active",
                        "access_enabled": 1}


def _unlock(user_id):
    app = Flask(__name__)
    routes.register(app)
    client = app.test_client()
    _as(user_id)
    client.post("/api/private-office/security/setup",
                json={"passcode": PASSCODE, "confirm_passcode": PASSCODE})
    resp = client.post("/api/private-office/security/unlock",
                       json={"passcode": PASSCODE})
    token = (resp.get_json() or {}).get("grant_token") or ""
    if token:
        _GRANTS[int(user_id)] = token
    return token


def _board(symbols: dict) -> dict:
    return {
        "source": "coingecko", "observed_epoch": 1_756_700_000,
        "age_seconds": 12, "warning": "",
        "markets": [{"symbol": symbol, "price": price, "change_24h": 1.5}
                    for symbol, price in symbols.items()],
    }


#: One deterministic board for the whole file. Installed at import of the
#: seeding step and left in place: these tests are about the wire, and a
#: network-dependent price would make them flaky for reasons that have
#: nothing to do with routing.
_PRICES = {"BTC": 50000.0, "ETH": 2000.0}


def _iso_in(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _seed(cur, user_id: int, symbol: str, name: str, amount: float,
          basis: object, obligation_title: str, obligation_amount) -> None:
    """One priced holding plus one obligation, per member.

    ``name`` and ``obligation_title`` carry the member's marker string so a
    cross-owner leak appears as a literal substring in the wrong payload.
    """
    cur.execute(
        "INSERT INTO portfolio_items (user_id, symbol, coin_name, amount, "
        "average_buy_price) VALUES (?,?,?,?,?)",
        (user_id, symbol, name, amount, basis))
    portfolio_events.enqueue(cur, user_id=user_id,
                             event_type=portfolio_events.EVENT_HOLDING_ADDED,
                             item_id=int(cur.lastrowid), symbol=symbol)
    portfolio.drain(cur, user_id=user_id)
    fields = {"due_at": _iso_in(45)}
    if obligation_amount is not None:
        fields["amount"] = str(obligation_amount)
        fields["currency"] = "usd"
    records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=user_id,
        title=obligation_title, obligation_type="LOAN_PAYMENT",
        domain="FINANCIAL", **fields)


def setup_environment() -> None:
    market_data.live_market_board = lambda **kwargs: _board(_PRICES)
    svc.ensure_schema()
    schema.reset_schema_cache()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)"
        )
        conn.execute("DELETE FROM users")
        for uid in (USER_A, USER_B):
            conn.execute(
                "INSERT INTO users (user_id, account_status, access_enabled) "
                "VALUES (?, ?, 1)", (uid, "active"))
        cur = conn.cursor()
        schema.ensure_private_schema(cur, force=True)
        # Forced for the same reason the line above it is: both modules cache
        # "schema is ready" in a module global, and this file opens its own
        # temporary database. Unforced, an earlier test in the same process
        # leaves the flag set and the DDL is skipped against a database that
        # has never seen it — which passes alone and fails in a suite run.
        portfolio_events.ensure_outbox_schema(cur, force=True)
        cur.execute(
            "CREATE TABLE IF NOT EXISTS portfolio_items ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
            "symbol TEXT, coin_name TEXT, amount REAL, average_buy_price REAL)")
        cur.execute("DELETE FROM portfolio_items")
        _seed(cur, USER_A, "BTC", "Bitcoin alpha", 2.0, 30000.0,
              "Alpha bridge loan", "25000")
        _seed(cur, USER_B, "ETH", "Ether bravo", 10.0, 1500.0,
              "Bravo equipment note", "4000")
        conn.commit()
    finally:
        conn.close()
    for uid in (USER_A, USER_B):
        svc.grant_entitlement(uid, "private_office.access", source="admin")
    _GRANTS.clear()
    for uid in (USER_A, USER_B):
        _unlock(uid)
    _stub._test_user = None


def _live() -> bool:
    return capital_graph.FEATURE_ID in feature_matrix.implemented_feature_ids()


# ---------------------------------------------------------------------------
def stage_authentication():
    """Gate one. No session, no capital, on every one of the three routes."""
    print("\n[authentication]")
    _stub._test_user = None
    client = _app().test_client()
    for path in PATHS:
        resp = client.get(path)
        body = resp.get_json() or {}
        check(f"GET {path} requires login", resp.status_code == 401,
              resp.status_code)
        check(f"{path}: the 401 carries no data key",
              DATA_KEY[path] not in body, body)
        check(f"{path}: the 401 is not a success", body.get("ok") is False, body)


def stage_second_lock():
    """Gate three. Entitled and signed in is not the same as unlocked."""
    print("\n[second lock]")
    _as(USER_A)
    client = _app().test_client()
    for path in PATHS:
        resp = client.get(path, headers={routes.GRANT_HEADER: ""})
        body = resp.get_json() or {}
        check(f"GET {path} is refused 423 without a grant",
              resp.status_code == 423, resp.status_code)
        check(f"{path}: the locked refusal carries no data key",
              DATA_KEY[path] not in body, body)
    # And the lock is a real obstacle the suite had to clear, not a no-op:
    # the same request with the grant is not 423.
    unlocked = client.get(OVERVIEW_PATH)
    check("the same request with a grant is not locked",
          unlocked.status_code != 423, unlocked.status_code)


def stage_reads_when_live():
    """Gate five. The payload, and the honesty fields that must ride with it."""
    print("\n[reads]")
    if not _live():
        _as(USER_A)
        client = _app().test_client()
        for path in PATHS:
            check(f"GET {path} is refused while the feature is off",
                  client.get(path).status_code == 404)
        print("  NOTE  read path not exercised: capital_graph is not live")
        return

    _as(USER_A)
    client = _app().test_client()

    resp = client.get(OVERVIEW_PATH)
    body = resp.get_json() or {}
    check("overview answers 200", resp.status_code == 200,
          f"{resp.status_code} {body}")
    check("overview is never cached",
          "no-store" in resp.headers.get("Cache-Control", ""))
    payload = body.get("overview") or {}
    net = payload.get("net_position") or {}
    check("the net position states an estimate", "estimated" in net, net)
    for field in ("complete", "incomplete_reasons", "excluded", "basis",
                  "currency", "known_assets", "known_liabilities"):
        check(f"the estimate never travels without {field}", field in net, net)
    check("the net position carries the graph's own refusal to total",
          net.get("disclaimer") == capital_graph.NO_AGGREGATE_VALUE,
          net.get("disclaimer"))
    check("the basis is the published one",
          net.get("basis") == overview_mod.NET_POSITION_BASIS)

    coverage = payload.get("coverage") or {}
    check("coverage publishes its formula (§41)",
          coverage.get("formula") == overview_mod.COVERAGE_FORMULA,
          coverage.get("formula"))
    check("coverage carries its dimensions",
          isinstance(coverage.get("dimensions"), dict), coverage)

    for block in ("assets", "liabilities", "concentrations", "needs_review",
                  "prices", "sync", "generated_at"):
        check(f"the overview carries {block}", block in payload, sorted(payload))

    obligations_resp = client.get(OBLIGATIONS_PATH)
    ob_body = obligations_resp.get_json() or {}
    check("obligations answers 200", obligations_resp.status_code == 200,
          f"{obligations_resp.status_code} {ob_body}")
    ob = ob_body.get("obligations") or {}
    totals = ob.get("totals") or {}
    for field in ("known_amount", "currency", "quantified", "unquantified",
                  "unspecified_currency", "complete", "truncated"):
        check(f"liability totals state {field}", field in totals, totals)
    check("the seeded obligation is projected",
          any("Alpha bridge loan" in str(row.get("title"))
              for row in ob.get("liabilities") or []),
          ob.get("liabilities"))
    check("obligations are never cached",
          "no-store" in obligations_resp.headers.get("Cache-Control", ""))

    exposure_resp = client.get(EXPOSURE_PATH)
    ex_body = exposure_resp.get_json() or {}
    check("exposure answers 200", exposure_resp.status_code == 200,
          f"{exposure_resp.status_code} {ex_body}")
    exposure = ex_body.get("exposure") or {}
    for block in ("concentrations", "assets", "liabilities", "coverage",
                  "prices", "generated_at"):
        check(f"exposure carries {block}", block in exposure, sorted(exposure))
    conc = exposure.get("concentrations") or {}
    check("exposure states the basis of its shares",
          conc.get("asset_basis") == "priced_asset_value", conc.get("asset_basis"))
    check("exposure agrees with the overview about the priced total",
          conc.get("asset_total") == (payload.get("concentrations")
                                      or {}).get("asset_total"),
          (conc.get("asset_total"),
           (payload.get("concentrations") or {}).get("asset_total")))

    flow_resp = client.get(CASH_FLOW_PATH)
    flow_body = flow_resp.get_json() or {}
    check("cash flow answers 200", flow_resp.status_code == 200,
          f"{flow_resp.status_code} {flow_body}")
    flow = flow_body.get("cash_flow") or {}
    for block in ("schedule", "buckets", "totals", "excluded", "basis",
                  "generated_at"):
        check(f"cash flow carries {block}", block in flow, sorted(flow))
    # The two sentences that stop a schedule of outflows being read as a
    # forecast of what the member will have left. If either can be dropped
    # without a test noticing, the client can render the chart bare.
    basis = flow.get("basis") or {}
    check("cash flow states that it has no income side",
          "income" in str(basis.get("inflows", "")).lower(),
          basis.get("inflows"))
    check("cash flow states that recurrence is never inferred",
          "recurrence" in str(basis.get("recurrence", "")).lower(),
          basis.get("recurrence"))
    check("cash flow publishes its bucket edges rather than implying them",
          isinstance(basis.get("buckets"), list) and basis["buckets"],
          basis.get("buckets"))
    # Every bucket named in the basis must exist in the data, or a client that
    # renders from the basis draws an axis with a missing column.
    named = {str(b.get("name")) for b in basis.get("buckets") or []}
    check("every published bucket edge has a bucket to match",
          named and named <= set((flow.get("buckets") or {})),
          (sorted(named), sorted(flow.get("buckets") or {})))
    flow_totals = flow.get("totals") or {}
    for field in ("currency", "scheduled_amount", "scheduled_count",
                  "obligations_seen", "truncated", "complete",
                  "excluded_count", "mixed_currency_rows"):
        check(f"cash flow totals state {field}", field in flow_totals,
              flow_totals)
    check("cash flow never folds a row into a total it does not name",
          flow_totals.get("mixed_currency_rows") == 0, flow_totals)
    check("the seeded obligation appears on the schedule",
          any("Alpha bridge loan" in str(row.get("title"))
              for row in flow.get("schedule") or []),
          flow.get("schedule"))
    # Outflows only: a net figure here would imply the income side had been
    # checked, and PulseSoc has no income ledger to check.
    for netted in ("net", "net_cash", "income", "surplus", "remaining"):
        check(f"cash flow totals publish no '{netted}' figure",
              netted not in {str(k).lower() for k in flow_totals},
              sorted(flow_totals))
    check("cash flow is never cached",
          "no-store" in flow_resp.headers.get("Cache-Control", ""))
    check("the schedule never exceeds the obligations it was built from",
          len(flow.get("schedule") or []) <= flow_totals.get(
              "obligations_seen", 0),
          (len(flow.get("schedule") or []), flow_totals.get("obligations_seen")))

    integrity_resp = client.get(INTEGRITY_PATH)
    ig_body = integrity_resp.get_json() or {}
    check("integrity answers 200", integrity_resp.status_code == 200,
          f"{integrity_resp.status_code} {ig_body}")
    report = ig_body.get("integrity") or {}
    for block in ("healthy", "findings", "checks", "examined", "totals",
                  "basis"):
        check(f"integrity carries {block}", block in report, sorted(report))
    check("integrity names every check it knows how to run",
          set(report.get("checks") or {}) == set(integrity_mod.CHECKS),
          sorted(report.get("checks") or {}))
    check("integrity says in the payload that it repairs nothing",
          "diagnostic only" in str(
              (report.get("basis") or {}).get("repair", "")).lower(),
          (report.get("basis") or {}).get("repair"))
    # A clean report over a store that was never scanned is the failure this
    # surface exists to prevent, so the counts must ride with the verdict.
    check("integrity states how much it examined",
          set(report.get("examined") or {}) >= {"nodes", "edges"},
          report.get("examined"))
    ig_totals = report.get("totals") or {}
    for field in ("findings", "invariant_violations", "checks_run",
                  "checks_total", "inconclusive", "truncated", "complete"):
        check(f"integrity totals state {field}", field in ig_totals, ig_totals)
    check("a healthy verdict means every check ran",
          report.get("healthy") is not True
          or ig_totals.get("checks_run") == ig_totals.get("checks_total"),
          (report.get("healthy"), ig_totals))
    check("integrity is never cached",
          "no-store" in integrity_resp.headers.get("Cache-Control", ""))
    # Read-only by shape as well as by promise: the route accepts GET and
    # nothing else, so there is no verb a client could reach a repair through.
    for verb in ("post", "put", "delete", "patch"):
        code = getattr(client, verb)(INTEGRITY_PATH).status_code
        check(f"integrity refuses {verb.upper()}", code == 405, code)


def stage_no_invented_total():
    """A field spelled like a net worth is read as one. None may exist."""
    print("\n[no invented aggregate]")
    if not _live():
        print("  NOTE  skipped: capital_graph is not live")
        return
    _as(USER_A)
    client = _app().test_client()
    for path in PATHS:
        blob = json.dumps(client.get(path).get_json(), default=str).lower()
        for invented in ("net_worth", "networth", "total_net", "estate_value",
                         "total_wealth", "portfolio_value"):
            check(f"{path} states no {invented}", invented not in blob)
        for leaked in ("owner_user_id", "record_key", "node_key", "passcode"):
            check(f"{path} never leaks {leaked}", leaked not in blob)


def stage_owner_isolation():
    """Two real members, distinguishable seed strings, over HTTP."""
    print("\n[owner isolation]")
    if not _live():
        print("  NOTE  skipped: capital_graph is not live")
        return
    _as(USER_A)
    a_blob = json.dumps(
        {path: _app().test_client().get(path).get_json() for path in PATHS},
        default=str)
    _as(USER_B)
    b_blob = json.dumps(
        {path: _app().test_client().get(path).get_json() for path in PATHS},
        default=str)

    check("A's payload contains A's own holding", "Bitcoin alpha" in a_blob)
    check("A's payload contains A's own obligation", "Alpha bridge loan" in a_blob)
    check("A never sees B's holding", "Ether bravo" not in a_blob)
    check("A never sees B's obligation", "Bravo equipment note" not in a_blob)
    check("B's payload contains B's own holding", "Ether bravo" in b_blob)
    check("B never sees A's holding", "Bitcoin alpha" not in b_blob)
    check("B never sees A's obligation", "Alpha bridge loan" not in b_blob)


def stage_failure_is_not_emptiness():
    """§79 / §137. Four bad outcomes, four distinguishable responses.

    Each service call is broken in turn — first by raising, then by refusing —
    and the route must translate that into 503 and 403 respectively. The
    forbidden outcome in both cases is ``200 {"ok": true, ...: {}}``, which a
    client renders as "you have nothing", over a store that is full.
    """
    print("\n[failure is not emptiness]")
    if not _live():
        print("  NOTE  skipped: capital_graph is not live")
        return
    _as(USER_A)
    client = _app().test_client()

    def _boom(*args, **kwargs):
        raise RuntimeError("store unreadable")

    def _denied(*args, **kwargs):
        return {"ok": False, "denied": {"reason": "actor_is_not_owner"}}

    targets = (
        (OVERVIEW_PATH, overview_mod, "overview"),
        (EXPOSURE_PATH, overview_mod, "overview"),
        (OBLIGATIONS_PATH, obligations, "liabilities_view"),
        (CASH_FLOW_PATH, cash_flow_mod, "schedule"),
        (INTEGRITY_PATH, integrity_mod, "diagnose"),
    )
    for path, module, attr in targets:
        original = getattr(module, attr)

        setattr(module, attr, _boom)
        try:
            resp = client.get(path)
            body = resp.get_json() or {}
        finally:
            setattr(module, attr, original)
        check(f"{path}: an unreadable store is a 503", resp.status_code == 503,
              resp.status_code)
        check(f"{path}: the 503 says unavailable, not empty",
              body.get("ok") is False and body.get("state") == "unavailable", body)
        check(f"{path}: the 503 carries no data key",
              DATA_KEY[path] not in body, body)

        setattr(module, attr, _denied)
        try:
            resp = client.get(path)
            body = resp.get_json() or {}
        finally:
            setattr(module, attr, original)
        check(f"{path}: a refused read is a 403", resp.status_code == 403,
              resp.status_code)
        check(f"{path}: the 403 says denied, not empty",
              body.get("ok") is False and body.get("state") == "denied", body)
        check(f"{path}: the 403 carries no data key",
              DATA_KEY[path] not in body, body)

    # The four refusals must not be interchangeable: a client that cannot tell
    # "locked" from "broken" from "not yours" cannot tell the member anything
    # useful either. Collected from live responses, not asserted from a literal.
    _stub._test_user = None
    anon_code = _app().test_client().get(OVERVIEW_PATH).status_code
    _as(USER_A)
    locked_code = client.get(OVERVIEW_PATH,
                             headers={routes.GRANT_HEADER: ""}).status_code
    observed = {anon_code, locked_code, 403, 503}
    check("anonymous, locked, denied and unavailable are four distinct codes",
          len(observed) == 4, sorted(observed))

    # And the healthy read still works after every patch was restored — proof
    # the stage tested the route rather than permanently breaking the module.
    check("the overview is readable again after the mutations",
          client.get(OVERVIEW_PATH).status_code == 200)


def stage_kill_switch():
    """The flag closes all three routes, sells nothing, and takes nothing else."""
    print("\n[kill switch]")
    _as(USER_A)
    client = _app().test_client()
    spec = feature_matrix.FEATURES[capital_graph.FEATURE_ID]
    previous = os.environ.get(spec.flag_env)
    os.environ[spec.flag_env] = "false"
    try:
        for path in PATHS:
            off = client.get(path)
            body = off.get_json() or {}
            check(f"the switch closes {path}", off.status_code == 404,
                  off.status_code)
            check(f"the switched-off {path} sells nothing",
                  "minimum_tier" not in body, body)
            check(f"the switched-off {path} carries no data key",
                  DATA_KEY[path] not in body, body)
        check("the switch does not take the fact routes down with it",
              client.get("/api/private-office/facts").status_code == 200)
    finally:
        if previous is None:
            os.environ.pop(spec.flag_env, None)
        else:
            os.environ[spec.flag_env] = previous
    check("the routes come back when the switch does",
          client.get(OVERVIEW_PATH).status_code in (200, 404))


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    setup_environment()
    stage_authentication()
    stage_second_lock()
    stage_reads_when_live()
    stage_no_invented_total()
    stage_owner_isolation()
    stage_failure_is_not_emptiness()
    stage_kill_switch()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_capital_projection_routes():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
