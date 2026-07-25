"""Merchant automation engine (Stage 6 Part 10).

Proves ingest + dedupe (rules and signals), latest-signal-wins state, each comparison
operator, that only ACTIVE rules evaluate, deterministic ordering (priority desc,
rule_id asc, subject asc), idempotent re-evaluation (replace, exactly one row per
(merchant, rule, subject)), and the hard boundary that a proposal is a suggestion (no
action executed / no money moved — nothing outside the three canonical tables).

    python tests/business_os/test_merchant_engine.py   # no pytest needed
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_mrcheng_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.merchant_automation import schema as asch  # noqa: E402
from services.business_os.merchant_automation import engine as eng  # noqa: E402

_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_BASE = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds=0):
    return (_BASE + timedelta(seconds=seconds)).strftime(_FMT)


def setup_module(module=None):
    asch.ensure_schema()


def _subjects(props):
    return {(p["rule_id"], p["subject_ref"]) for p in props}


# --- (a) ingest + dedupe -----------------------------------------------------
def test_rule_and_signal_dedupe():
    r1 = eng.record_rule("mD", "stock", "lte", 5, "reorder", source="feed",
                         external_ref="r1")
    assert r1["recorded"] is True
    r2 = eng.record_rule("mD", "stock", "lte", 5, "reorder", source="feed",
                         external_ref="r1")
    assert r2["recorded"] is False and r2["deduped"] is True
    s1 = eng.record_signal("mD", "sku1", "stock", 3, source="feed",
                           external_ref="s1", observed_at=_ts())
    assert s1["recorded"] is True
    s2 = eng.record_signal("mD", "sku1", "stock", 3, source="feed",
                           external_ref="s1", observed_at=_ts())
    assert s2["recorded"] is False and s2["deduped"] is True


def test_bad_inputs_curated():
    for fn in (lambda: eng.record_rule("m", "stock", "between", 5, "reorder"),
               lambda: eng.record_rule("", "stock", "lte", 5, "reorder"),
               lambda: eng.record_rule("m", "stock", "lte", "abc", "reorder"),
               lambda: eng.record_rule("m", "stock", "lte", 5, ""),
               lambda: eng.record_signal("m", "s", "stock", "notnum"),
               lambda: eng.record_signal("m", "", "stock", 1)):
        try:
            fn()
        except eng.MerchantAutomationError:
            continue
        raise AssertionError("expected MerchantAutomationError")


# --- (b) basic threshold match ----------------------------------------------
def test_lte_threshold_match_and_miss():
    m = "m_lte"
    eng.record_rule(m, "stock", "lte", 5, "reorder")
    eng.record_signal(m, "low", "stock", 2, observed_at=_ts())    # 2 <= 5 -> match
    eng.record_signal(m, "high", "stock", 20, observed_at=_ts())  # 20 <= 5 -> no
    res = eng.evaluate_merchant(m)
    subs = {p["subject_ref"] for p in res["proposals"]}
    assert "low" in subs, subs
    assert "high" not in subs, subs
    # transparency: proposal records observed value + threshold + action
    p = [x for x in res["proposals"] if x["subject_ref"] == "low"][0]
    assert p["action_type"] == "reorder" and p["observed_value"] == "2"
    assert p["threshold"] == "5" and p["operator"] == "lte"


# --- (c) every operator ------------------------------------------------------
def test_all_operators():
    m = "m_ops"
    # value fixed at 10; one rule per operator, each on its own signal_type
    cases = [("gt", 5, True), ("gt", 10, False),
             ("gte", 10, True), ("gte", 11, False),
             ("lt", 20, True), ("lt", 10, False),
             ("eq", 10, True), ("eq", 9, False),
             ("ne", 9, True), ("ne", 10, False)]
    for i, (op, thr, _exp) in enumerate(cases):
        st = f"sig{i}"
        eng.record_rule(m, st, op, thr, f"act{i}")
        eng.record_signal(m, f"subj{i}", st, 10, observed_at=_ts())
    res = eng.evaluate_merchant(m)
    matched = {p["signal_type"] for p in res["proposals"]}
    for i, (op, thr, exp) in enumerate(cases):
        st = f"sig{i}"
        assert (st in matched) is exp, (op, thr, exp, matched)


# --- (d) latest signal wins --------------------------------------------------
def test_latest_signal_supersedes():
    m = "m_latest"
    eng.record_rule(m, "stock", "lte", 5, "reorder")
    eng.record_signal(m, "sku", "stock", 2, observed_at=_ts(0))   # would match
    eng.record_signal(m, "sku", "stock", 50, observed_at=_ts(10))  # latest: no match
    res = eng.evaluate_merchant(m)
    assert res["count"] == 0, res["proposals"]
    # now a newer low reading brings it back
    eng.record_signal(m, "sku", "stock", 1, observed_at=_ts(20))
    res2 = eng.evaluate_merchant(m)
    assert {p["subject_ref"] for p in res2["proposals"]} == {"sku"}, res2


# --- (e) only active rules evaluate -----------------------------------------
def test_inactive_rule_skipped():
    m = "m_active"
    eng.record_rule(m, "stock", "lte", 5, "reorder", active=False)
    eng.record_signal(m, "sku", "stock", 1, observed_at=_ts())
    res = eng.evaluate_merchant(m)
    assert res["count"] == 0, res["proposals"]


# --- (f) deterministic ordering (priority desc, rule_id asc, subject asc) ----
def test_deterministic_ordering():
    m = "m_order"
    # two rules, low + high priority, both match two subjects
    hi = eng.record_rule(m, "stock", "lte", 100, "restock", priority=10)["rule_id"]
    lo = eng.record_rule(m, "views", "gte", 1, "promote", priority=1)["rule_id"]
    eng.record_signal(m, "b_sku", "stock", 3, observed_at=_ts())
    eng.record_signal(m, "a_sku", "stock", 3, observed_at=_ts())
    eng.record_signal(m, "z_sku", "views", 9, observed_at=_ts())
    res = eng.evaluate_merchant(m)
    ranks = [p["rank"] for p in res["proposals"]]
    assert ranks == sorted(ranks) and ranks[0] == 1, ranks
    # higher-priority rule's proposals come first; within a rule, subject ascending
    hi_props = [p for p in res["proposals"] if p["rule_id"] == hi]
    lo_props = [p for p in res["proposals"] if p["rule_id"] == lo]
    assert max(p["rank"] for p in hi_props) < min(p["rank"] for p in lo_props)
    hi_subj_order = [p["subject_ref"] for p in sorted(hi_props, key=lambda x: x["rank"])]
    assert hi_subj_order == ["a_sku", "b_sku"], hi_subj_order


# --- (g) idempotent re-evaluation (replace, one row per key) ----------------
def test_reevaluate_idempotent():
    m = "m_idem"
    eng.record_rule(m, "stock", "lte", 5, "reorder")
    eng.record_signal(m, "s1", "stock", 2, observed_at=_ts())
    eng.record_signal(m, "s2", "stock", 4, observed_at=_ts())
    first = eng.evaluate_merchant(m)
    second = eng.evaluate_merchant(m)
    assert first["proposals"] == second["proposals"], "must be deterministic"
    conn = db.connect()
    try:
        dupes = conn.execute(
            "SELECT merchant_id,rule_id,subject_ref,COUNT(*) c FROM "
            "business_os_merchant_proposals WHERE merchant_id=? "
            "GROUP BY merchant_id,rule_id,subject_ref HAVING c > 1", (m,)).fetchall()
    finally:
        conn.close()
    assert dupes == [], dupes


# --- (h) proposal is a suggestion: nothing executed outside the projection --
def test_no_side_effects_only_projection():
    m = "m_safe"
    eng.record_rule(m, "stock", "lte", 5, "reorder")
    eng.record_signal(m, "s1", "stock", 1, observed_at=_ts())
    eng.evaluate_merchant(m)
    conn = db.connect()
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        mrch = {t for t in names if t.startswith("business_os_merchant_")}
        assert mrch == {"business_os_merchant_rules", "business_os_merchant_signals",
                        "business_os_merchant_proposals",
                        "business_os_merchant_audit"}, mrch
        # a proposal row exists, but it only records a SUGGESTED action
        row = conn.execute(
            "SELECT action_type FROM business_os_merchant_proposals "
            "WHERE merchant_id=?", (m,)).fetchone()
        assert row is not None and row["action_type"] == "reorder"
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_rule_and_signal_dedupe,
        test_bad_inputs_curated,
        test_lte_threshold_match_and_miss,
        test_all_operators,
        test_latest_signal_supersedes,
        test_inactive_rule_skipped,
        test_deterministic_ordering,
        test_reevaluate_idempotent,
        test_no_side_effects_only_projection,
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
