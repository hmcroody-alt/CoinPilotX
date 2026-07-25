"""Crypto vertical schema — structural checks (Stage 5 Part 1).

Proves the additive ``business_os_crypto_*`` surface: ensure_schema is idempotent
(runs twice cleanly), every table + expected column exists, the dedupe/uniqueness
indexes actually enforce (append-only idempotent ingest, one alert-event per
crossing), and NO legacy table is touched.

    python tests/business_os/test_crypto_schema.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_cryptoschema_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.crypto import schema as cs  # noqa: E402


_TABLES = [
    "business_os_crypto_transactions",
    "business_os_crypto_holdings",
    "business_os_crypto_lots",
    "business_os_crypto_alerts",
    "business_os_crypto_alert_events",
    "business_os_crypto_audit",
]


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_idempotent_creates_all_tables():
    cs.ensure_schema()
    cs.ensure_schema()  # must not raise on second run
    conn = db.connect()
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for t in _TABLES:
            assert t in names, f"missing table {t}"
    finally:
        conn.close()


def test_transaction_columns():
    conn = db.connect()
    try:
        cols = _columns(conn, "business_os_crypto_transactions")
        for c in ("txn_id", "user_id", "symbol", "side", "quantity",
                  "unit_price_cents", "fee_cents", "executed_at", "source",
                  "external_ref"):
            assert c in cols, c
    finally:
        conn.close()


def test_holdings_and_lots_columns():
    conn = db.connect()
    try:
        h = _columns(conn, "business_os_crypto_holdings")
        for c in ("holding_id", "user_id", "symbol", "quantity",
                  "cost_basis_cents", "realized_pnl_cents", "method"):
            assert c in h, c
        l = _columns(conn, "business_os_crypto_lots")
        for c in ("lot_id", "txn_id", "original_quantity", "remaining_quantity",
                  "unit_cost_cents", "closed"):
            assert c in l, c
    finally:
        conn.close()


def test_alert_columns():
    conn = db.connect()
    try:
        a = _columns(conn, "business_os_crypto_alerts")
        for c in ("alert_id", "user_id", "symbol", "metric", "comparator",
                  "threshold", "active", "last_state", "last_fired_at",
                  "cooldown_seconds"):
            assert c in a, c
        e = _columns(conn, "business_os_crypto_alert_events")
        for c in ("event_id", "alert_id", "crossing_key", "observed_value",
                  "delivered"):
            assert c in e, c
    finally:
        conn.close()


def test_side_check_constraint():
    conn = db.connect()
    try:
        try:
            conn.execute(
                "INSERT INTO business_os_crypto_transactions "
                "(txn_id,user_id,symbol,side,quantity,unit_price_cents,executed_at,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (cs.new_id(), "u1", "BTC", "hodl", "1.0", 100, cs.utc_now_iso(),
                 cs.utc_now_iso()))
            conn.commit()
            raise AssertionError("expected CHECK(side) to reject 'hodl'")
        except AssertionError:
            raise
        except Exception:
            conn.rollback()
    finally:
        conn.close()


def test_idempotent_ingest_unique_source_ref():
    conn = db.connect()
    try:
        row = (cs.new_id(), "u2", "ETH", "buy", "2.0", 200000, 0, "usd",
               cs.utc_now_iso(), "coinbase", "cb-evt-1", None, cs.utc_now_iso())
        cols = ("txn_id,user_id,symbol,side,quantity,unit_price_cents,fee_cents,"
                "currency,executed_at,source,external_ref,notes,created_at")
        q = f"INSERT INTO business_os_crypto_transactions ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
        conn.execute(q, row)
        conn.commit()
        # Same (source, external_ref) must collide.
        dup = (cs.new_id(), "u2", "ETH", "buy", "2.0", 200000, 0, "usd",
               cs.utc_now_iso(), "coinbase", "cb-evt-1", None, cs.utc_now_iso())
        try:
            conn.execute(q, dup)
            conn.commit()
            raise AssertionError("expected UNIQUE(source, external_ref) to reject replay")
        except AssertionError:
            raise
        except Exception:
            conn.rollback()
        # Manual entries (NULL external_ref) are exempt — two must coexist.
        for _ in range(2):
            m = (cs.new_id(), "u2", "ETH", "buy", "1.0", 100000, 0, "usd",
                 cs.utc_now_iso(), "manual", None, None, cs.utc_now_iso())
            conn.execute(q, m)
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) FROM business_os_crypto_transactions "
            "WHERE source='manual'").fetchone()[0]
        assert n == 2, n
    finally:
        conn.close()


def test_alert_event_crossing_dedupe():
    conn = db.connect()
    try:
        cols = ("event_id,alert_id,user_id,symbol,crossing_key,observed_value,"
                "threshold,comparator,delivered,created_at")
        q = f"INSERT INTO business_os_crypto_alert_events ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?)"
        conn.execute(q, (cs.new_id(), "al1", "u3", "BTC", "cross-1", "70000",
                         "65000", "crosses_above", 0, cs.utc_now_iso()))
        conn.commit()
        try:
            conn.execute(q, (cs.new_id(), "al1", "u3", "BTC", "cross-1", "70001",
                             "65000", "crosses_above", 0, cs.utc_now_iso()))
            conn.commit()
            raise AssertionError("expected UNIQUE(alert_id, crossing_key) to dedupe")
        except AssertionError:
            raise
        except Exception:
            conn.rollback()
    finally:
        conn.close()


def test_legacy_tables_untouched():
    """ensure_schema must never create/alter legacy portfolio/alert tables."""
    conn = db.connect()
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for legacy in ("portfolio_items", "manual_portfolio", "user_alerts",
                       "watchlist_items", "watchlists"):
            assert legacy not in names, f"schema must not create legacy {legacy}"
    finally:
        conn.close()


def _run_standalone():
    tests = [
        test_idempotent_creates_all_tables,
        test_transaction_columns,
        test_holdings_and_lots_columns,
        test_alert_columns,
        test_side_check_constraint,
        test_idempotent_ingest_unique_source_ref,
        test_alert_event_crossing_dedupe,
        test_legacy_tables_untouched,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
