"""Durable, restart-safe crypto alerts (Stage 5 Part 4).

Proves: CRUD (create/list/deactivate); the first observation ARMS state without
firing; a genuine crossing fires once; staying in-region does not re-fire (edge
only); a replayed crossing (same edge, stale state — the restart case) is deduped
to a single event; repeat_mode='always' re-fires on a fresh crossing while 'once'
deactivates; a missing quote skips without touching state; sweep drives it all
through a price_lookup.

    python tests/business_os/test_crypto_alerts.py   # no pytest needed
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_cryptoalert_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.crypto import schema as cs  # noqa: E402
from services.business_os.crypto import alerts as ca  # noqa: E402


def setup_module(module=None):
    cs.ensure_schema()


def _event_count(alert_id):
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM business_os_crypto_alert_events "
            "WHERE alert_id = ?", (alert_id,)).fetchone()[0]
    finally:
        conn.close()


def _alert_row(alert_id):
    conn = db.connect()
    try:
        r = conn.execute(
            "SELECT alert_id,user_id,symbol,metric,comparator,threshold,active,"
            "repeat_mode,last_state,last_value,last_fired_at,cooldown_seconds "
            "FROM business_os_crypto_alerts WHERE alert_id = ?",
            (alert_id,)).fetchone()
        return {"alert_id": r[0], "user_id": r[1], "symbol": r[2], "metric": r[3],
                "comparator": r[4], "threshold": r[5], "active": bool(r[6]),
                "repeat_mode": r[7], "last_state": r[8], "last_value": r[9],
                "last_fired_at": r[10], "cooldown_seconds": r[11]}
    finally:
        conn.close()


# --- (a) CRUD ----------------------------------------------------------------
def test_crud():
    a = ca.create_alert("u1", "btc", "crosses_above", "65000")
    lst = ca.list_alerts("u1")
    assert len(lst) == 1 and lst[0]["alert_id"] == a["alert_id"]
    assert ca.deactivate_alert(a["alert_id"]) is True
    assert ca.list_alerts("u1", active_only=True) == []


def test_bad_comparator():
    try:
        ca.create_alert("u1", "btc", "sideways", "1")
    except ca.CryptoAlertError:
        return
    raise AssertionError("expected CryptoAlertError")


# --- (b) first observation arms, does not fire ------------------------------
def test_first_obs_arms_no_fire():
    a = ca.create_alert("u2", "btc", "crosses_above", "60000")
    row = _alert_row(a["alert_id"])
    # already above threshold on first sight -> must NOT fire (no prior state).
    res = ca.evaluate_alert(row, 6500000)  # $65,000.00 in cents
    assert res["fired"] is False and res["side"] == "above", res
    assert _event_count(a["alert_id"]) == 0


# --- (c) genuine crossing fires once ----------------------------------------
def test_crossing_fires_once():
    a = ca.create_alert("u3", "btc", "crosses_above", "60000")
    # first obs below arms 'below'
    ca.evaluate_alert(_alert_row(a["alert_id"]), 5000000)
    # now cross above -> fires
    res = ca.evaluate_alert(_alert_row(a["alert_id"]), 6100000)
    assert res["fired"] is True and res["side"] == "above", res
    assert _event_count(a["alert_id"]) == 1
    # staying above -> no re-fire (once mode also deactivated it)
    row = _alert_row(a["alert_id"])
    assert row["active"] is False  # repeat_mode once
    res2 = ca.evaluate_alert(row, 6200000)
    assert res2["fired"] is False
    assert _event_count(a["alert_id"]) == 1


# --- (d) replayed crossing deduped (restart case) ---------------------------
def test_replay_deduped():
    a = ca.create_alert("u4", "btc", "crosses_above", "60000", repeat_mode="always")
    fixed = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)
    stale = _alert_row(a["alert_id"])
    stale["last_state"] = "below"  # simulate state that never got persisted
    r1 = ca.evaluate_alert(dict(stale), 6100000, now=fixed)
    r2 = ca.evaluate_alert(dict(stale), 6100000, now=fixed)  # replay, stale state
    assert r1["fired"] is True, r1
    assert r2["fired"] is False and r2["reason"] == "duplicate_suppressed", r2
    assert _event_count(a["alert_id"]) == 1


# --- (e) repeat 'always' re-fires on a new crossing -------------------------
def test_repeat_always_refires():
    a = ca.create_alert("u5", "eth", "crosses_above", "2000", repeat_mode="always")
    aid = a["alert_id"]
    t0 = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)
    ca.evaluate_alert(_alert_row(aid), 150000, now=t0)             # below arm
    ca.evaluate_alert(_alert_row(aid), 250000, now=t0 + timedelta(seconds=1))  # cross up -> fire
    ca.evaluate_alert(_alert_row(aid), 150000, now=t0 + timedelta(seconds=2))  # back below
    res = ca.evaluate_alert(_alert_row(aid), 250000, now=t0 + timedelta(seconds=3))  # cross up again
    assert res["fired"] is True, res
    assert _event_count(aid) == 2  # two distinct crossings


# --- (f) no quote skips ------------------------------------------------------
def test_no_quote_skip():
    a = ca.create_alert("u6", "btc", "above", "60000")
    res = ca.evaluate_alert(_alert_row(a["alert_id"]), None)
    assert res["fired"] is False and res["reason"] == "no_quote"


# --- (g) sweep integrates ----------------------------------------------------
def test_sweep():
    a = ca.create_alert("u7", "sol", "crosses_above", "100", repeat_mode="always")
    prices = {"SOL": 5000}   # $50 -> below
    ca.sweep(lambda s: prices.get(s))            # arm below
    prices["SOL"] = 12000    # $120 -> crosses above
    out = ca.sweep(lambda s: prices.get(s))
    assert out["fired_count"] == 1, out
    assert out["fired"][0]["alert_id"] == a["alert_id"]


def _run_standalone():
    setup_module()
    tests = [
        test_crud,
        test_bad_comparator,
        test_first_obs_arms_no_fire,
        test_crossing_fires_once,
        test_replay_deduped,
        test_repeat_always_refires,
        test_no_quote_skip,
        test_sweep,
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
