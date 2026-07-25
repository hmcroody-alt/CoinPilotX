"""Creator commerce engine (Stage 6 Part 14).

Proves the deterministic earnings/tier projection: ingest is idempotent on
(source, external_ref); bad amounts are curated; per-supporter totals sum correctly;
tiers are assigned by fixed cumulative-support thresholds (bronze/silver/gold/platinum)
at and around each boundary; supporters rank by total desc then supporter_id asc;
recompute is a deterministic idempotent replace (no duplicate rows); the earnings
rollup sums per offering; and nothing beyond the four canonical tables is created (no
money moves, no action taken).

    python tests/business_os/test_creator_engine.py   # no pytest needed
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_createng_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.creator_commerce import schema as sch  # noqa: E402
from services.business_os.creator_commerce import engine as eng  # noqa: E402

_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_BASE = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds=0):
    return (_BASE + timedelta(seconds=seconds)).strftime(_FMT)


def setup_module(module=None):
    sch.ensure_schema()


def test_offering_and_contribution_dedupe():
    r1 = eng.record_offering("cA", "tip", name="Tip jar", source="feed",
                             external_ref="OF1")
    r2 = eng.record_offering("cA", "tip", name="Tip jar", source="feed",
                             external_ref="OF1")
    assert r1["recorded"] is True and r2["deduped"] is True, (r1, r2)
    assert r1["offering_id"] == r2["offering_id"]

    c1 = eng.record_contribution("cA", "s1", "10.00", source="feed",
                                 external_ref="CT1")
    c2 = eng.record_contribution("cA", "s1", "10.00", source="feed",
                                 external_ref="CT1")
    assert c1["recorded"] is True and c2["deduped"] is True, (c1, c2)


def test_bad_amount_curated():
    for bad in ("notnum", "-5"):
        raised = False
        try:
            eng.record_contribution("cB", "s1", bad)
        except eng.CreatorCommerceError:
            raised = True
        assert raised, f"amount {bad!r} should be rejected"


def test_bad_offering_type_curated():
    raised = False
    try:
        eng.record_offering("cB", "banana")
    except eng.CreatorCommerceError:
        raised = True
    assert raised, "unknown offering_type should be rejected"


def test_totals_and_ranking():
    # cC: three supporters with distinct totals -> ranked desc by total.
    eng.record_contribution("cC", "big", "300.00", occurred_at=_ts(0))
    eng.record_contribution("cC", "mid", "50.00", occurred_at=_ts(1))
    eng.record_contribution("cC", "mid", "10.00", occurred_at=_ts(2))
    eng.record_contribution("cC", "small", "5.00", occurred_at=_ts(3))
    out = eng.compute_creator("cC")
    ranks = [(s["supporter_id"], s["total_amount"], s["rank"]) for s in out["supporters"]]
    assert ranks == [("big", "300.00", 1), ("mid", "60.00", 2),
                     ("small", "5.00", 3)], ranks
    counts = {s["supporter_id"]: s["contribution_count"] for s in out["supporters"]}
    assert counts["mid"] == 2, counts


def test_tie_break_supporter_id_asc():
    # Equal totals -> supporter_id ascending.
    eng.record_contribution("cTie", "zebra", "20.00")
    eng.record_contribution("cTie", "alpha", "20.00")
    out = eng.compute_creator("cTie")
    order = [s["supporter_id"] for s in out["supporters"]]
    assert order == ["alpha", "zebra"], order


def test_tier_thresholds_at_boundaries():
    # bronze >=0, silver >=25, gold >=100, platinum >=500.
    cases = {
        "b_zero": ("0.00", "bronze"),
        "b_just_under": ("24.99", "bronze"),
        "s_at": ("25.00", "silver"),
        "s_just_under_gold": ("99.99", "silver"),
        "g_at": ("100.00", "gold"),
        "g_just_under_plat": ("499.99", "gold"),
        "p_at": ("500.00", "platinum"),
        "p_above": ("1000.00", "platinum"),
    }
    for sid, (amt, _tier) in cases.items():
        eng.record_contribution("cTier", sid, amt)
    out = eng.compute_creator("cTier")
    got = {s["supporter_id"]: s["tier"] for s in out["supporters"]}
    for sid, (_amt, tier) in cases.items():
        assert got[sid] == tier, (sid, got.get(sid), tier)


def test_recompute_idempotent_replace():
    eng.record_contribution("cR", "s1", "40.00")
    eng.record_contribution("cR", "s2", "5.00")
    first = eng.compute_creator("cR")
    second = eng.compute_creator("cR")
    assert first["supporters"] == second["supporters"], (first, second)
    # exactly one row per supporter after two recomputes
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT supporter_id, COUNT(*) c FROM business_os_creator_supporters "
            "WHERE creator_id = ? GROUP BY supporter_id", ("cR",)).fetchall()
        for r in rows:
            assert dict(r)["c"] == 1, dict(r)
    finally:
        conn.close()


def test_earnings_rollup_per_offering():
    of1 = eng.record_offering("cE", "membership", name="Gold")["offering_id"]
    of2 = eng.record_offering("cE", "tip", name="Tips")["offering_id"]
    eng.record_contribution("cE", "s1", "30.00", offering_id=of1)
    eng.record_contribution("cE", "s2", "70.00", offering_id=of1)
    eng.record_contribution("cE", "s3", "5.00", offering_id=of2)
    eng.record_contribution("cE", "s4", "5.00")  # unassigned
    rep = eng.earnings_report("cE")
    assert rep["total_support"] == "110.00", rep
    assert rep["contribution_count"] == 4, rep
    per = {o["offering_id"]: o["total"] for o in rep["offerings"]}
    assert per[of1] == "100.00", per
    assert per[of2] == "5.00", per
    assert per["(unassigned)"] == "5.00", per


def test_no_side_effects():
    eng.record_contribution("cN", "s1", "10.00")
    eng.compute_creator("cN")
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'business_os_creator_%'").fetchall()
        names = {r[0] for r in rows}
        assert names == {
            "business_os_creator_offerings",
            "business_os_creator_contributions",
            "business_os_creator_supporters",
            "business_os_creator_audit"}, names
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_offering_and_contribution_dedupe,
        test_bad_amount_curated,
        test_bad_offering_type_curated,
        test_totals_and_ranking,
        test_tie_break_supporter_id_asc,
        test_tier_thresholds_at_boundaries,
        test_recompute_idempotent_replace,
        test_earnings_rollup_per_offering,
        test_no_side_effects,
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
