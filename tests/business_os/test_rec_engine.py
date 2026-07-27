"""Recommendations engine (Stage 6 Part 6).

Proves ingest + dedupe, each ranking model (popularity / content_based /
collaborative / hybrid), that already-engaged items (including dismiss) are excluded,
deterministic tie-break (score desc, item_id asc), and idempotent recompute (replace,
exactly one row per (user, model, item)).

    python tests/business_os/test_rec_engine.py   # no pytest needed
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_receng_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.recommendations import schema as asch  # noqa: E402
from services.business_os.recommendations import engine as eng  # noqa: E402

_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_BASE = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds=0):
    return (_BASE + timedelta(seconds=seconds)).strftime(_FMT)


def setup_module(module=None):
    asch.ensure_schema()


def _order(recs, ids):
    """Item ids from recs restricted to the given set, in rank order."""
    keep = set(ids)
    return [r["item_id"] for r in sorted(recs, key=lambda r: r["rank"])
            if r["item_id"] in keep]


# --- (a) ingest + dedupe -----------------------------------------------------
def test_item_and_interaction_dedupe():
    r1 = eng.record_item("itA", "product", tags=["sports", "shoes"])
    assert r1["recorded"] is True
    r2 = eng.record_item("itA", "product", tags=["x"])
    assert r2["recorded"] is False and r2["deduped"] is True
    i1 = eng.record_interaction("uD", "itA", "view", source="feed",
                                external_ref="e1", occurred_at=_ts())
    assert i1["recorded"] is True
    i2 = eng.record_interaction("uD", "itA", "view", source="feed",
                                external_ref="e1", occurred_at=_ts())
    assert i2["recorded"] is False and i2["deduped"] is True


def test_bad_inputs_curated():
    for fn in (lambda: eng.record_interaction("u", "i", "nope"),
               lambda: eng.record_interaction("", "i", "view"),
               lambda: eng.record_interaction("u", "i", "view", weight=-1),
               lambda: eng.record_item("", "product")):
        try:
            fn()
        except eng.RecommendationError:
            continue
        raise AssertionError("expected RecommendationError")


# --- (b) popularity ----------------------------------------------------------
def test_popularity_ranks_by_weight():
    for iid in ("P1", "P2", "P3"):
        eng.record_item(iid, "product")
    # P2 gets the most positive weight, P1 next, P3 least
    eng.record_interaction("a1", "P2", "purchase", weight=5, occurred_at=_ts())
    eng.record_interaction("a2", "P2", "like", weight=3, occurred_at=_ts())
    eng.record_interaction("a3", "P1", "click", weight=4, occurred_at=_ts())
    eng.record_interaction("a4", "P3", "view", weight=1, occurred_at=_ts())
    # target user u_pop has engaged none of these
    res = eng.compute_recommendations("u_pop", "popularity")
    order = _order(res["recommendations"], {"P1", "P2", "P3"})
    assert order == ["P2", "P1", "P3"], order


def test_popularity_excludes_seen():
    eng.record_interaction("u_pop", "P2", "view", occurred_at=_ts())
    res = eng.compute_recommendations("u_pop", "popularity")
    order = _order(res["recommendations"], {"P1", "P2", "P3"})
    assert "P2" not in order, order  # seen -> excluded
    assert order == ["P1", "P3"], order


# --- (c) content-based -------------------------------------------------------
def test_content_based_tag_overlap():
    eng.record_item("C_sport1", "article", tags=["sports", "news"])
    eng.record_item("C_sport2", "article", tags=["sports"])
    eng.record_item("C_cook", "article", tags=["cooking"])
    # user engages a sports item -> profile favors 'sports'
    eng.record_interaction("u_con", "C_sport1", "like", weight=2, occurred_at=_ts())
    res = eng.compute_recommendations("u_con", "content_based")
    ids = {r["item_id"] for r in res["recommendations"]}
    assert "C_sport2" in ids, ids            # shares 'sports' tag
    assert "C_cook" not in ids, ids          # no tag overlap
    assert "C_sport1" not in ids, ids        # already engaged
    row = [r for r in res["recommendations"] if r["item_id"] == "C_sport2"][0]
    assert "sports" in row["reason"], row


# --- (d) collaborative co-occurrence ----------------------------------------
def test_collaborative_cooccurrence():
    for iid in ("CO_A", "CO_B", "CO_C"):
        eng.record_item(iid, "product")
    # target engaged A
    eng.record_interaction("u_col", "CO_A", "purchase", occurred_at=_ts())
    # two other users engaged both A and B -> A~B co-occur
    for u in ("o1", "o2"):
        eng.record_interaction(u, "CO_A", "purchase", occurred_at=_ts())
        eng.record_interaction(u, "CO_B", "purchase", occurred_at=_ts())
    # a third user engaged only C (no overlap with A's user set)
    eng.record_interaction("o3", "CO_C", "view", occurred_at=_ts())
    res = eng.compute_recommendations("u_col", "collaborative")
    ids = {r["item_id"] for r in res["recommendations"]}
    assert "CO_B" in ids, ids       # co-engaged with A -> recommended
    assert "CO_C" not in ids, ids   # no co-occurrence with A
    assert "CO_A" not in ids, ids   # already engaged


# --- (e) hybrid blends + excludes seen --------------------------------------
def test_hybrid_blend():
    for iid in ("H1", "H2"):
        eng.record_item(iid, "product", tags=["gadget"])
    eng.record_item("H_seed", "product", tags=["gadget"])
    # user engages a gadget seed (content), others co-engage H1 with the seed
    eng.record_interaction("u_hy", "H_seed", "like", occurred_at=_ts())
    for u in ("ho1", "ho2"):
        eng.record_interaction(u, "H_seed", "like", occurred_at=_ts())
        eng.record_interaction(u, "H1", "purchase", weight=5, occurred_at=_ts())
    eng.record_interaction("hpop", "H2", "view", occurred_at=_ts())
    res = eng.compute_recommendations("u_hy", "hybrid")
    ids = [r["item_id"] for r in res["recommendations"]]
    assert "H_seed" not in ids, ids       # engaged -> excluded
    assert "H1" in ids, ids               # content + collaborative signal
    # scores are quantized decimal strings and strictly ranked
    ranks = [r["rank"] for r in res["recommendations"]]
    assert ranks == sorted(ranks) and ranks[0] == 1


# --- (f) deterministic tie-break (score desc, item_id asc) ------------------
def test_deterministic_tiebreak():
    # two items with identical popularity weight -> item_id ascending wins
    eng.record_item("T_bbb", "product")
    eng.record_item("T_aaa", "product")
    eng.record_interaction("tt1", "T_bbb", "click", weight=2, occurred_at=_ts())
    eng.record_interaction("tt2", "T_aaa", "click", weight=2, occurred_at=_ts())
    res = eng.compute_recommendations("u_tie", "popularity")
    order = _order(res["recommendations"], {"T_aaa", "T_bbb"})
    assert order == ["T_aaa", "T_bbb"], order  # tie -> ascending item_id


# --- (g) dismiss marks seen (still excluded) --------------------------------
def test_dismiss_excludes():
    eng.record_item("D_item", "product", tags=["z"])
    eng.record_item("D_other", "product", tags=["z"])
    # user dismisses D_item -> it must never be recommended to them
    eng.record_interaction("u_dis", "D_item", "dismiss", occurred_at=_ts())
    # give D_item global popularity so it would otherwise surface
    eng.record_interaction("dz1", "D_item", "purchase", weight=9, occurred_at=_ts())
    res = eng.compute_recommendations("u_dis", "popularity")
    ids = {r["item_id"] for r in res["recommendations"]}
    assert "D_item" not in ids, ids


# --- (h) idempotent recompute (replace, one row per user/model/item) --------
def test_recompute_idempotent():
    for iid in ("R1", "R2", "R3"):
        eng.record_item(iid, "product")
    eng.record_interaction("rr1", "R1", "purchase", weight=3, occurred_at=_ts())
    eng.record_interaction("rr2", "R2", "like", weight=2, occurred_at=_ts())
    eng.record_interaction("rr3", "R3", "view", weight=1, occurred_at=_ts())
    eng.recompute_user("u_re")             # all models
    first = {m: eng.get_recommendations("u_re", m) for m in eng.VALID_MODELS}
    eng.recompute_user("u_re")             # again
    second = {m: eng.get_recommendations("u_re", m) for m in eng.VALID_MODELS}
    assert first == second, "recompute must be deterministic"
    conn = db.connect()
    try:
        # exactly one row per (user, model, item) — no duplication
        dupes = conn.execute(
            "SELECT user_id,model,item_id,COUNT(*) c FROM "
            "business_os_rec_recommendations WHERE user_id='u_re' "
            "GROUP BY user_id,model,item_id HAVING c > 1").fetchall()
    finally:
        conn.close()
    assert dupes == [], dupes


def _run_standalone():
    setup_module()
    tests = [
        test_item_and_interaction_dedupe,
        test_bad_inputs_curated,
        test_popularity_ranks_by_weight,
        test_popularity_excludes_seen,
        test_content_based_tag_overlap,
        test_collaborative_cooccurrence,
        test_hybrid_blend,
        test_deterministic_tiebreak,
        test_dismiss_excludes,
        test_recompute_idempotent,
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
