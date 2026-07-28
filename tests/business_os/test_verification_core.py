"""Business OS — Section 10 (Verification) service, exercised DIRECTLY (no pytest).

Proves the canonical cross-domain trust domain:

  * DARK when BUSINESS_OS_VERIFICATION is off — the service raises ``disabled`` (503);
  * a clean business (events created, sold, settled through the ledger) verifies PASS with
    every integrity check green;
  * the checks are real: corrupting the ledger/counter state DIRECTLY (bypassing the service)
    makes the corresponding check flip to FAIL — the attestation is not a rubber stamp;
  * runs + per-check rows are persisted and re-readable;
  * RBAC: a stranger sees 404 (existence not leaked).

    python tests/business_os/test_verification_core.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_verif_core_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_EVENTS"] = "on"
os.environ["BUSINESS_OS_VERIFICATION"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.ledger import ledger as ledger_mod  # noqa: E402
from services.business_os.events import schema as ev_schema  # noqa: E402
from services.business_os.events import service as ev  # noqa: E402
from services.business_os.verification import schema as vf_schema  # noqa: E402
from services.business_os.verification import service as vf  # noqa: E402


OWNER = 870
STAFF = 871
BUYER = 872
BUYER2 = 873
STRANGER = 874


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    ledger_mod.ensure_schema()
    ev_schema.ensure_schema()
    vf_schema.ensure_schema()


def _business():
    biz = biz_svc.create_business(OWNER, {"display_name": "Verify Co"}, context=_ctx())
    bid = biz["business_id"]
    biz_svc.add_member(bid, OWNER, STAFF, "staff", context=_ctx())
    return bid


def _sold_and_settled_event(bid):
    ev_ = ev.create_event(bid, OWNER, {"title": "Gala", "capacity": 100}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, OWNER, {"name": "GA", "price_cents": 5000},
                            context=_ctx())
    ttid = tt["ticket_type_id"]
    ev.publish_event(eid, OWNER, context=_ctx())
    ev.purchase_ticket(eid, ttid, BUYER, client_ref="v1", context=_ctx())
    ev.purchase_ticket(eid, ttid, BUYER2, client_ref="v2", context=_ctx())
    ev.settle_event(eid, OWNER, context=_ctx())
    return eid, ttid


def _by_name(run):
    return {c["name"]: c["ok"] for c in run["checks"]}


# ---------------------------------------------------------------------------
def test_dark_when_disabled_raises():
    os.environ["BUSINESS_OS_VERIFICATION"] = ""
    try:
        try:
            vf.run_verification("b", OWNER, context=_ctx())
            assert False, "expected disabled"
        except vf.VerificationError as e:
            assert e.http_status == 503 and e.code == "disabled", (e.http_status, e.code)
    finally:
        os.environ["BUSINESS_OS_VERIFICATION"] = "on"


def test_clean_business_passes_all_checks():
    bid = _business()
    _sold_and_settled_event(bid)
    run = vf.run_verification(bid, OWNER, context=_ctx())
    assert run["status"] == "pass", run
    assert run["checks_passed"] == run["checks_total"]
    names = _by_name(run)
    for key in ("settled_events_escrow_zero", "paid_tickets_have_capture_ref",
                "ticket_type_sold_counts_consistent", "no_orphan_tickets",
                "business_event_payable_nonnegative"):
        assert names.get(key) is True, (key, names)


def test_run_and_checks_persisted_and_reread():
    bid = _business()
    _sold_and_settled_event(bid)
    run = vf.run_verification(bid, STAFF, context=_ctx())
    got = vf.get_run(run["run_id"], STAFF)
    assert got["run_id"] == run["run_id"]
    assert got["status"] == "pass"
    assert len(got["checks"]) == run["checks_total"] >= 8
    runs = vf.list_runs(bid, OWNER)
    assert any(r["run_id"] == run["run_id"] for r in runs)


def test_capture_ref_check_flips_on_corruption():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Corrupt"}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, OWNER, {"name": "GA", "price_cents": 3000},
                            context=_ctx())
    ev.publish_event(eid, OWNER, context=_ctx())
    tkt = ev.purchase_ticket(eid, tt["ticket_type_id"], BUYER, client_ref="c1",
                             context=_ctx())
    # Directly strip the ledger capture reference off a PAID live ticket.
    conn = db.connect()
    conn.execute(
        "UPDATE business_os_event_tickets SET capture_txn_ref = NULL WHERE ticket_id = ?",
        (tkt["ticket_id"],))
    conn.commit()
    conn.close()
    run = vf.run_verification(bid, OWNER, context=_ctx())
    names = _by_name(run)
    assert names["paid_tickets_have_capture_ref"] is False, run
    assert run["status"] == "fail"


def test_sold_counter_drift_flips_check():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Drift"}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, OWNER, {"name": "GA"}, context=_ctx())
    ttid = tt["ticket_type_id"]
    ev.publish_event(eid, OWNER, context=_ctx())
    ev.purchase_ticket(eid, ttid, BUYER, client_ref="d1", context=_ctx())
    # Corrupt the sold counter so it no longer matches live tickets.
    conn = db.connect()
    conn.execute(
        "UPDATE business_os_event_ticket_types SET quantity_sold = 99 "
        "WHERE ticket_type_id = ?", (ttid,))
    conn.commit()
    conn.close()
    run = vf.run_verification(bid, OWNER, context=_ctx())
    names = _by_name(run)
    assert names["ticket_type_sold_counts_consistent"] is False, run
    assert run["status"] == "fail"


def test_stranger_cannot_verify_or_read():
    bid = _business()
    _sold_and_settled_event(bid)
    for fn in (
        lambda: vf.run_verification(bid, STRANGER, context=_ctx()),
        lambda: vf.list_runs(bid, STRANGER),
    ):
        try:
            fn()
            assert False, "expected not_found"
        except vf.VerificationError as e:
            assert e.http_status == 404 and e.code == "not_found", (e.http_status, e.code)


def test_missing_business_404():
    try:
        vf.run_verification("nope-nope", OWNER, context=_ctx())
        assert False, "expected not_found"
    except vf.VerificationError as e:
        assert e.http_status == 404 and e.code == "not_found", (e.http_status, e.code)


def test_get_run_stranger_404_not_leaked():
    bid = _business()
    _sold_and_settled_event(bid)
    run = vf.run_verification(bid, OWNER, context=_ctx())
    try:
        vf.get_run(run["run_id"], STRANGER)
        assert False, "expected not_found"
    except vf.VerificationError as e:
        assert e.http_status == 404 and e.code == "not_found", (e.http_status, e.code)


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled_raises,
        test_clean_business_passes_all_checks,
        test_run_and_checks_persisted_and_reread,
        test_capture_ref_check_flips_on_corruption,
        test_sold_counter_drift_flips_check,
        test_stranger_cannot_verify_or_read,
        test_missing_business_404,
        test_get_run_stranger_404_not_leaked,
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
