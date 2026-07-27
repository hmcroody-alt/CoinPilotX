"""Creator commerce controller contract (Stage 6 Part 15).

Proves the framework-agnostic contract: DARK (404) when the flag is off; missing
payload/fields -> 400 with curated codes; recording an offering + contribution;
supporters report is computed-on-read; offerings/earnings reports; recompute runs.
Curated codes only, never a raw exception.

    python tests/business_os/test_creator_api.py   # no pytest needed
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_creatapi_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_CREATOR_COMMERCE"] = "on"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.creator_commerce import schema as sch  # noqa: E402
from services.business_os.creator_commerce import api  # noqa: E402


def setup_module(module=None):
    sch.ensure_schema()


# --- (a) dark when disabled -------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_CREATOR_COMMERCE"] = "0"
    try:
        assert api.record_offering({})[0] == 404
        assert api.record_contribution({})[0] == 404
        assert api.supporters_report("c")[0] == 404
        assert api.offerings_report("c")[0] == 404
        assert api.earnings_report("c")[0] == 404
        assert api.run_recompute("c")[0] == 404
    finally:
        os.environ["BUSINESS_OS_CREATOR_COMMERCE"] = "on"


# --- (b) validation ---------------------------------------------------------
def test_offering_missing_fields():
    st, body = api.record_offering({"creator_id": "c"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_offering_bad_type_curated():
    st, body = api.record_offering({"creator_id": "c", "offering_type": "banana"})
    assert st == 400 and body["code"] == "invalid_offering", body


def test_contribution_missing_fields():
    st, body = api.record_contribution({"creator_id": "c", "supporter_id": "s"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_contribution_bad_amount_curated():
    st, body = api.record_contribution({"creator_id": "c", "supporter_id": "s",
                                        "amount": "notnum"})
    assert st == 400 and body["code"] == "invalid_contribution", body


def test_supporters_missing_creator():
    st, body = api.supporters_report("")
    assert st == 400 and body["code"] == "missing_fields", body


# --- (c) record + compute-on-read supporters --------------------------------
def test_supporters_computed_on_read():
    api.record_contribution({"creator_id": "C1", "supporter_id": "whale",
                             "amount": "600.00"})
    api.record_contribution({"creator_id": "C1", "supporter_id": "fan",
                             "amount": "10.00"})
    st, body = api.supporters_report("C1")
    assert st == 200, body
    sups = body["result"]["supporters"]
    by_id = {s["supporter_id"]: s for s in sups}
    assert by_id["whale"]["tier"] == "platinum", by_id
    assert by_id["whale"]["rank"] == 1, by_id
    assert by_id["fan"]["tier"] == "bronze", by_id


# --- (d) offerings + earnings reports + recompute ---------------------------
def test_offerings_and_earnings_reports():
    api.record_offering({"creator_id": "C1", "offering_type": "membership",
                         "name": "Gold"})
    st, body = api.offerings_report("C1")
    assert st == 200 and any(o["offering_type"] == "membership"
                             for o in body["result"]["offerings"]), body
    st2, b2 = api.earnings_report("C1")
    assert st2 == 200 and b2["result"]["total_support"] == "610.00", b2


def test_recompute_runs():
    st, body = api.run_recompute("C1")
    assert st == 200 and "supporters" in body["result"], body
    st2, b2 = api.run_recompute("")
    assert st2 == 400 and b2["code"] == "missing_fields", b2


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_offering_missing_fields,
        test_offering_bad_type_curated,
        test_contribution_missing_fields,
        test_contribution_bad_amount_curated,
        test_supporters_missing_creator,
        test_supporters_computed_on_read,
        test_offerings_and_earnings_reports,
        test_recompute_runs,
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
