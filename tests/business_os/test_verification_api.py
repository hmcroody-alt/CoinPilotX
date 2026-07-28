"""Business OS — Section 10 (Verification) HTTP controller, exercised DIRECTLY.

Proves the framework-agnostic ``(status_code, body)`` controller over the verification
service:

  * DARK when BUSINESS_OS_VERIFICATION is off — every handler returns 404 not_found;
  * a manager runs a verification pass (201), re-reads the run (200), and lists runs (200);
  * a clean business attests PASS;
  * access is enforced by the service — a stranger gets 404 (existence not leaked).

    python tests/business_os/test_verification_api.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_verif_api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_EVENTS"] = "on"
os.environ["BUSINESS_OS_VERIFICATION"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.ledger import ledger as ledger_mod  # noqa: E402
from services.business_os.events import schema as ev_schema  # noqa: E402
from services.business_os.events import service as ev  # noqa: E402
from services.business_os.verification import schema as vf_schema  # noqa: E402
from services.business_os.verification import api  # noqa: E402


OWNER = 890
STAFF = 891
BUYER = 892
STRANGER = 893


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    ledger_mod.ensure_schema()
    ev_schema.ensure_schema()
    vf_schema.ensure_schema()


def _business():
    biz = biz_svc.create_business(OWNER, {"display_name": "Verify API"}, context=_ctx())
    bid = biz["business_id"]
    biz_svc.add_member(bid, OWNER, STAFF, "staff", context=_ctx())
    return bid


def _event(bid):
    ev_ = ev.create_event(bid, OWNER, {"title": "E"}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, OWNER, {"name": "GA", "price_cents": 1000},
                            context=_ctx())
    ev.publish_event(eid, OWNER, context=_ctx())
    ev.purchase_ticket(eid, tt["ticket_type_id"], BUYER, client_ref="e1", context=_ctx())
    ev.settle_event(eid, OWNER, context=_ctx())


# ---------------------------------------------------------------------------
def test_dark_when_disabled_all_handlers_404():
    os.environ["BUSINESS_OS_VERIFICATION"] = ""
    try:
        for fn in (
            lambda: api.run_verification(OWNER, "b"),
            lambda: api.get_run(OWNER, "r"),
            lambda: api.list_runs(OWNER, "b"),
        ):
            status, body = fn()
            assert status == 404 and body["code"] == "not_found", body
    finally:
        os.environ["BUSINESS_OS_VERIFICATION"] = "on"


def test_run_get_list_over_http():
    bid = _business()
    _event(bid)
    status, body = api.run_verification(OWNER, bid, context=_ctx())
    assert status == 201 and body["ok"] is True
    run = body["run"]
    assert run["status"] == "pass" and run["checks_passed"] == run["checks_total"]
    rid = run["run_id"]

    status, body = api.get_run(STAFF, rid)
    assert status == 200 and body["run"]["run_id"] == rid
    assert len(body["run"]["checks"]) >= 8

    status, body = api.list_runs(OWNER, bid)
    assert status == 200 and any(r["run_id"] == rid for r in body["runs"])


def test_stranger_run_404_not_leaked():
    bid = _business()
    _event(bid)
    status, body = api.run_verification(STRANGER, bid, context=_ctx())
    assert status == 404 and body["code"] == "not_found", body
    status, body = api.list_runs(STRANGER, bid)
    assert status == 404 and body["code"] == "not_found", body


def test_missing_business_404():
    status, body = api.run_verification(OWNER, "nope-nope", context=_ctx())
    assert status == 404 and body["code"] == "not_found", body


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled_all_handlers_404,
        test_run_get_list_over_http,
        test_stranger_run_404_not_leaked,
        test_missing_business_404,
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
