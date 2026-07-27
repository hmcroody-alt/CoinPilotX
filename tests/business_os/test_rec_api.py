"""Recommendations controller contract (Stage 6 Part 7).

Proves the framework-agnostic contract: DARK (404) when the flag is off; missing
payload/fields -> 400; unauthenticated -> 401; recording an interaction; a user's
recommendations report is computed-on-read and scoped to the caller; popularity
report and recompute run. Curated codes only, never a raw exception.

    python tests/business_os/test_rec_api.py   # no pytest needed
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_recapi_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_RECOMMENDATIONS"] = "on"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.recommendations import schema as asch  # noqa: E402
from services.business_os.recommendations import api  # noqa: E402

_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_BASE = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds=0):
    return (_BASE + timedelta(seconds=seconds)).strftime(_FMT)


def setup_module(module=None):
    asch.ensure_schema()


# --- (a) dark when disabled -------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_RECOMMENDATIONS"] = "0"
    try:
        assert api.record_item({})[0] == 404
        assert api.record_interaction("u1", {})[0] == 404
        assert api.recommendations_report("u1")[0] == 404
        assert api.popularity_report()[0] == 404
        assert api.run_recompute("u1")[0] == 404
    finally:
        os.environ["BUSINESS_OS_RECOMMENDATIONS"] = "on"


# --- (b) validation ---------------------------------------------------------
def test_item_missing_fields():
    st, body = api.record_item({"item_type": "product"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_interaction_missing_fields():
    st, body = api.record_interaction("u1", {"item_id": "i1"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_interaction_unauthenticated():
    st, body = api.record_interaction("", {"item_id": "i1", "interaction_type": "view"})
    assert st == 401 and body["code"] == "unauthenticated", body


def test_interaction_bad_type_curated():
    st, body = api.record_interaction("u1", {"item_id": "i1",
                                             "interaction_type": "warp"})
    assert st == 400 and body["code"] == "invalid_interaction", body
    assert "interaction_type" in body["error"]


# --- (c) record + compute-on-read recommendations ---------------------------
def test_recommendations_computed_on_read():
    api.record_item({"item_id": "AI1", "item_type": "product", "tags": ["ai"]})
    api.record_item({"item_id": "AI2", "item_type": "product", "tags": ["ai"]})
    # user engages AI1; another user co-engages AI1 + AI2 to seed collaborative
    api.record_interaction("uR", {"item_id": "AI1", "interaction_type": "like",
                                  "occurred_at": _ts(0)})
    api.record_interaction("oR", {"item_id": "AI1", "interaction_type": "like",
                                  "occurred_at": _ts(1)})
    api.record_interaction("oR", {"item_id": "AI2", "interaction_type": "purchase",
                                  "weight": 5, "occurred_at": _ts(2)})
    st, body = api.recommendations_report("uR", "hybrid")
    assert st == 200, body
    recs = body["result"]["recommendations"]
    ids = {r["item_id"] for r in recs}
    assert "AI2" in ids, recs          # recommended
    assert "AI1" not in ids, recs      # already engaged -> excluded


def test_recommendations_bad_model():
    st, body = api.recommendations_report("uR", "psychic")
    assert st == 400 and body["code"] == "invalid_model", body


# --- (d) popularity report + recompute --------------------------------------
def test_popularity_report_and_recompute():
    st, body = api.popularity_report()
    assert st == 200 and "rows" in body["result"], body
    st2, b2 = api.run_recompute("uR", ["popularity", "content_based"])
    assert st2 == 200 and set(b2["result"]["models"]) == {"popularity",
                                                          "content_based"}, b2
    # recompute with bad model -> invalid_model
    st3, b3 = api.run_recompute("uR", ["nope"])
    assert st3 == 400 and b3["code"] == "invalid_model", b3
    # recompute missing user -> missing_fields
    st4, b4 = api.run_recompute("")
    assert st4 == 400 and b4["code"] == "missing_fields", b4


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_item_missing_fields,
        test_interaction_missing_fields,
        test_interaction_unauthenticated,
        test_interaction_bad_type_curated,
        test_recommendations_computed_on_read,
        test_recommendations_bad_model,
        test_popularity_report_and_recompute,
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
