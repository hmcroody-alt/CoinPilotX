"""Attribution engine (Stage 6 Part 2).

Proves each model's credit split, that credits sum to the conversion value EXACTLY
(remainder-safe), lookback-window exclusion, deterministic idempotent recompute,
single-touch and zero-touch (unattributed) handling, and report aggregation.

    python tests/business_os/test_attr_engine.py   # no pytest needed
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_attreng_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.attribution import schema as asch  # noqa: E402
from services.business_os.attribution import engine as eng  # noqa: E402

_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_BASE = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds=0, days=0):
    return (_BASE + timedelta(seconds=seconds, days=days)).strftime(_FMT)


def setup_module(module=None):
    asch.ensure_schema()


def _mk_path(user, channels, *, start=0):
    """Create a sequence of click touchpoints spaced 1s apart; return their ids."""
    ids = []
    for i, ch in enumerate(channels):
        r = eng.record_touchpoint(user, ch, "click", campaign_ref=f"camp_{ch}",
                                  occurred_at=_ts(seconds=start + i))
        ids.append(r["touchpoint_id"])
    return ids


def _credit_map(conversion_id, model):
    return {c["touchpoint_id"]: c["credit_cents"]
            for c in eng.conversion_credits(conversion_id, model)}


# --- (a) ingest + dedupe -----------------------------------------------------
def test_record_and_dedupe():
    r1 = eng.record_touchpoint("uA", "ad", "impression", source="feed",
                               external_ref="e1", occurred_at=_ts())
    assert r1["recorded"] is True
    r2 = eng.record_touchpoint("uA", "ad", "impression", source="feed",
                               external_ref="e1", occurred_at=_ts())
    assert r2["recorded"] is False and r2["deduped"] is True
    c1 = eng.record_conversion("uA", "purchase", 5000, source="ord",
                               external_ref="o1", occurred_at=_ts(seconds=10))
    c2 = eng.record_conversion("uA", "purchase", 5000, source="ord",
                               external_ref="o1", occurred_at=_ts(seconds=10))
    assert c1["recorded"] is True and c2["deduped"] is True


def test_bad_inputs_curated():
    for fn in (lambda: eng.record_touchpoint("u", "ad", "nope"),
               lambda: eng.record_conversion("u", "purchase", -1),
               lambda: eng.record_conversion("u", "purchase", "abc")):
        try:
            fn()
        except eng.AttributionError:
            continue
        raise AssertionError("expected AttributionError")


# --- (b) last / first touch --------------------------------------------------
def test_last_and_first_touch():
    ids = _mk_path("u1", ["ad", "email", "organic"], start=0)
    cv = eng.record_conversion("u1", "purchase", 9999, occurred_at=_ts(seconds=100))
    cid = cv["conversion_id"]
    lt = eng.compute_credits(cid, "last_touch")
    assert lt["attributed"] and lt["total_credit_cents"] == 9999
    m = _credit_map(cid, "last_touch")
    assert m[ids[-1]] == 9999 and m[ids[0]] == 0 and m[ids[1]] == 0
    ft = eng.compute_credits(cid, "first_touch")
    m = _credit_map(cid, "first_touch")
    assert m[ids[0]] == 9999 and m[ids[-1]] == 0
    assert ft["total_credit_cents"] == 9999


# --- (c) linear split is exact (remainder-safe) -----------------------------
def test_linear_remainder_safe():
    ids = _mk_path("u2", ["a", "b", "c"], start=0)   # 3 touches
    cv = eng.record_conversion("u2", "purchase", 100, occurred_at=_ts(seconds=100))
    cid = cv["conversion_id"]
    res = eng.compute_credits(cid, "linear")
    m = _credit_map(cid, "linear")
    assert sum(m.values()) == 100, m           # EXACT, no lost penny
    assert set(m.values()) == {33, 34} or set(m.values()) == {33, 34, 33}
    assert sorted(m.values()) == [33, 33, 34]
    assert res["total_credit_cents"] == 100


# --- (d) position-based U-shape ---------------------------------------------
def test_position_based_shapes():
    # n>=3: 40 / 20-split / 40
    ids = _mk_path("u3", ["a", "b", "c", "d"], start=0)   # 4 touches
    cv = eng.record_conversion("u3", "purchase", 1000, occurred_at=_ts(seconds=100))
    cid = cv["conversion_id"]
    eng.compute_credits(cid, "position_based")
    m = _credit_map(cid, "position_based")
    assert m[ids[0]] == 400 and m[ids[-1]] == 400
    assert m[ids[1]] == 100 and m[ids[2]] == 100
    assert sum(m.values()) == 1000

    # n==1 -> all
    one = _mk_path("u3b", ["a"], start=0)
    cv1 = eng.record_conversion("u3b", "purchase", 777, occurred_at=_ts(seconds=100))
    eng.compute_credits(cv1["conversion_id"], "position_based")
    m1 = _credit_map(cv1["conversion_id"], "position_based")
    assert m1[one[0]] == 777

    # n==2 -> 50/50 (odd value splits remainder-safe)
    two = _mk_path("u3c", ["a", "b"], start=0)
    cv2 = eng.record_conversion("u3c", "purchase", 101, occurred_at=_ts(seconds=100))
    eng.compute_credits(cv2["conversion_id"], "position_based")
    m2 = _credit_map(cv2["conversion_id"], "position_based")
    assert sum(m2.values()) == 101 and sorted(m2.values()) == [50, 51]


# --- (e) lookback window excludes stale touches -----------------------------
def test_lookback_excludes():
    # one touch 40 days before, one 1 day before; 30-day window keeps only the recent
    eng.record_touchpoint("u4", "old", "click", campaign_ref="c_old",
                          occurred_at=_ts(days=-40))
    recent = eng.record_touchpoint("u4", "new", "click", campaign_ref="c_new",
                                   occurred_at=_ts(days=-1))
    cv = eng.record_conversion("u4", "purchase", 500, lookback_days=30,
                               occurred_at=_ts(seconds=0))
    cid = cv["conversion_id"]
    eng.compute_credits(cid, "linear")
    m = _credit_map(cid, "linear")
    assert list(m.keys()) == [recent["touchpoint_id"]], m
    assert m[recent["touchpoint_id"]] == 500


# --- (f) unattributed: no eligible touches ----------------------------------
def test_unattributed():
    cv = eng.record_conversion("u5_nobody", "signup", 1000, occurred_at=_ts())
    res = eng.compute_credits(cv["conversion_id"], "last_touch")
    assert res["attributed"] is False and res["total_credit_cents"] == 0
    assert eng.conversion_credits(cv["conversion_id"], "last_touch") == []


# --- (g) idempotent recompute ------------------------------------------------
def test_recompute_idempotent():
    _mk_path("u6", ["a", "b", "c"], start=0)
    cv = eng.record_conversion("u6", "purchase", 1000, occurred_at=_ts(seconds=100))
    cid = cv["conversion_id"]
    eng.recompute_conversion(cid)                 # all models
    first = {mdl: _credit_map(cid, mdl) for mdl in eng.VALID_MODELS}
    eng.recompute_conversion(cid)                 # again
    second = {mdl: _credit_map(cid, mdl) for mdl in eng.VALID_MODELS}
    assert first == second, "recompute must be deterministic"
    # exactly one credit row per (conversion, model, touchpoint) — no duplication
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM business_os_attr_credits WHERE conversion_id = ?",
            (cid,)).fetchone()[0]
    finally:
        conn.close()
    assert n == 3 * len(eng.VALID_MODELS), n


# --- (h) reports aggregate ---------------------------------------------------
def test_reports():
    rep = eng.campaign_report("last_touch")
    assert "rows" in rep and isinstance(rep["rows"], list)
    ch = eng.channel_report("linear")
    assert "rows" in ch
    # sanity: channel totals under linear are all non-negative ints
    assert all(r["credit_cents"] >= 0 for r in ch["rows"])


def _run_standalone():
    setup_module()
    tests = [
        test_record_and_dedupe,
        test_bad_inputs_curated,
        test_last_and_first_touch,
        test_linear_remainder_safe,
        test_position_based_shapes,
        test_lookback_excludes,
        test_unattributed,
        test_recompute_idempotent,
        test_reports,
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
