"""UNDX governed Marketplace listing workflow.

Proves the first production workflow uses the existing Marketplace assistant and the
UNDX governance layer: draft creation is blocked without permission, allowed drafts
persist through Marketplace, publish requires the Marketplace confirmation token, and
emergency stop blocks even when permissions exist.

    python tests/business_os/test_undx_marketplace_workflow.py
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_undxmkt_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_UNDX_ACTIONS"] = "on"
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.undx_actions import schema as undx_schema  # noqa: E402
from services.business_os.undx_actions import engine as eng  # noqa: E402
from services.business_os.undx_actions import marketplace_workflow as wf  # noqa: E402
from services.business_os.undx_actions import api as undx_api  # noqa: E402


USER_ID = "seller_42"
ACTOR = "seller:seller_42"
ORG = "coinplotxai"


def setup_module(module=None):
    mkt_schema.ensure_schema()
    undx_schema.ensure_schema()
    mkt.set_seller_status(USER_ID, "approved", actor="owner", reason="test seller")


def _listing(title="Neon Studio Pack"):
    return {
        "source_text": "Neon Studio Pack\nA creator kit for PulseSoc stores.",
        "title": title,
        "description": "A creator kit for PulseSoc stores.",
        "price_cents": 2999,
        "currency": "usd",
        "fulfillment_type": "physical",
        "inventory_qty": 4,
    }


def test_draft_blocked_without_permission():
    out = wf.create_listing_draft(
        org_id="no_policy_org",
        actor=ACTOR,
        user_id=USER_ID,
        listing=_listing("Blocked Draft"))
    assert out["ok"] is False, out
    assert out["requires_approval"] is True, out
    assert out["decision"]["effect"] == "require_approval", out


def test_allowed_draft_uses_marketplace_and_records_receipt():
    eng.grant_permission(ORG, ACTOR, wf.ACTION_CREATE, "allow")
    out = wf.create_listing_draft(
        org_id=ORG,
        actor=ACTOR,
        user_id=USER_ID,
        listing=_listing("Verified Draft"))
    assert out["ok"] is True, out
    observed = out["marketplace"]["observed"]
    product_id = observed["product_id"]
    product = mkt.get_product(product_id, requester_user_id=USER_ID)
    assert product["status"] == "draft", product
    assert product["title"] == "Verified Draft", product
    assert product["description"] == "A creator kit for PulseSoc stores.", product
    assert product["currency"] == "usd", product
    center = eng.action_center(ORG)
    assert any(r["canonical_ref"] == f"marketplace_product:{product_id}"
               and r["status"] == "verified" for r in center["receipts"]), center


def test_publish_plan_and_execute_use_confirmation_token():
    eng.grant_permission(ORG, ACTOR, wf.ACTION_PUBLISH, "allow")
    draft = wf.create_listing_draft(
        org_id=ORG,
        actor=ACTOR,
        user_id=USER_ID,
        listing=_listing("Publishable Draft"))
    product_id = draft["marketplace"]["observed"]["product_id"]
    plan = wf.plan_publish_listing(
        org_id=ORG,
        actor=ACTOR,
        user_id=USER_ID,
        product_id=product_id)
    assert plan["ok"] is True, plan
    token = plan["plan"]["confirmation_token"]
    done = wf.execute_publish_listing(
        org_id=ORG,
        actor=ACTOR,
        user_id=USER_ID,
        request_id=plan["request"]["request_id"],
        product_id=product_id,
        confirmation_token=token)
    assert done["ok"] is True, done
    product = mkt.get_product(product_id, requester_user_id=USER_ID)
    assert product["status"] == "active", product


def test_publish_execution_rejects_misbound_and_replayed_confirmation():
    org = "binding_org"
    actor = ACTOR
    eng.grant_permission(org, actor, wf.ACTION_CREATE, "allow")
    eng.grant_permission(org, actor, wf.ACTION_PUBLISH, "allow")
    draft = wf.create_listing_draft(
        org_id=org, actor=actor, user_id=USER_ID,
        listing=_listing("Bound Confirmation Draft"))
    product_id = draft["marketplace"]["observed"]["product_id"]
    plan = wf.plan_publish_listing(
        org_id=org, actor=actor, user_id=USER_ID, product_id=product_id)

    for bad in (
        {"org_id": "other_org", "actor": actor,
         "request_id": plan["request"]["request_id"], "product_id": product_id},
        {"org_id": org, "actor": "seller:someone_else",
         "request_id": plan["request"]["request_id"], "product_id": product_id},
        {"org_id": org, "actor": actor,
         "request_id": plan["request"]["request_id"], "product_id": "wrong_product"},
    ):
        try:
            wf.execute_publish_listing(
                user_id=USER_ID,
                confirmation_token=plan["plan"]["confirmation_token"], **bad)
            assert False, f"misbound confirmation should fail: {bad}"
        except eng.UndxActionsError:
            pass
    assert mkt.get_product(product_id, requester_user_id=USER_ID)["status"] == "draft"

    good = wf.execute_publish_listing(
        org_id=org, actor=actor, user_id=USER_ID,
        request_id=plan["request"]["request_id"], product_id=product_id,
        confirmation_token=plan["plan"]["confirmation_token"])
    assert good["ok"] is True, good
    assert good["confirmation"]["status"] == "confirmed", good

    try:
        wf.execute_publish_listing(
            org_id=org, actor=actor, user_id=USER_ID,
            request_id=plan["request"]["request_id"], product_id=product_id,
            confirmation_token=plan["plan"]["confirmation_token"])
        assert False, "replayed UNDX confirmation should fail"
    except eng.UndxActionsError as exc:
        assert "no longer pending" in str(exc), exc


def test_emergency_stop_after_plan_blocks_execution():
    org = "late_stop_org"
    eng.grant_permission(org, ACTOR, wf.ACTION_CREATE, "allow")
    eng.grant_permission(org, ACTOR, wf.ACTION_PUBLISH, "allow")
    draft = wf.create_listing_draft(
        org_id=org, actor=ACTOR, user_id=USER_ID,
        listing=_listing("Late Stop Draft"))
    product_id = draft["marketplace"]["observed"]["product_id"]
    plan = wf.plan_publish_listing(
        org_id=org, actor=ACTOR, user_id=USER_ID, product_id=product_id)
    eng.activate_emergency_stop(
        org, "operator", "publish frozen after plan",
        action_type=wf.ACTION_PUBLISH)
    out = wf.execute_publish_listing(
        org_id=org, actor=ACTOR, user_id=USER_ID,
        request_id=plan["request"]["request_id"], product_id=product_id,
        confirmation_token=plan["plan"]["confirmation_token"])
    assert out["ok"] is False and out["code"] == "governance_denied", out
    assert mkt.get_product(product_id, requester_user_id=USER_ID)["status"] == "draft"


def test_emergency_stop_blocks_publish():
    eng.grant_permission("stopped_org", ACTOR, wf.ACTION_CREATE, "allow")
    eng.grant_permission("stopped_org", ACTOR, wf.ACTION_PUBLISH, "allow")
    draft = wf.create_listing_draft(
        org_id="stopped_org",
        actor=ACTOR,
        user_id=USER_ID,
        listing=_listing("Stopped Draft"))
    product_id = draft["marketplace"]["observed"]["product_id"]
    eng.activate_emergency_stop(
        "stopped_org", "operator", "marketplace freeze", action_type=wf.ACTION_PUBLISH)
    plan = wf.plan_publish_listing(
        org_id="stopped_org",
        actor=ACTOR,
        user_id=USER_ID,
        product_id=product_id)
    assert plan["ok"] is False, plan
    assert plan["decision"]["effect"] == "deny", plan
    product = mkt.get_product(product_id, requester_user_id=USER_ID)
    assert product["status"] == "draft", product


def test_controller_surface_for_draft_and_publish():
    eng.grant_permission("api_org", ACTOR, wf.ACTION_CREATE, "allow")
    eng.grant_permission("api_org", ACTOR, wf.ACTION_PUBLISH, "allow")
    st, body = undx_api.marketplace_create_listing_draft(USER_ID, {
        "org_id": "api_org",
        "actor": ACTOR,
        "listing": _listing("API Draft"),
    })
    assert st == 200 and body["ok"] is True, body
    product_id = body["result"]["marketplace"]["observed"]["product_id"]
    st2, b2 = undx_api.marketplace_plan_publish(USER_ID, {
        "org_id": "api_org",
        "actor": ACTOR,
        "product_id": product_id,
    })
    assert st2 == 200 and b2["ok"] is True, b2
    st3, b3 = undx_api.marketplace_execute_publish(USER_ID, {
        "org_id": "api_org",
        "actor": ACTOR,
        "request_id": b2["result"]["request"]["request_id"],
        "product_id": product_id,
        "confirmation_token": b2["result"]["plan"]["confirmation_token"],
    })
    assert st3 == 200 and b3["ok"] is True, b3


def test_controller_trusted_identity_overrides_client_actor_and_org():
    trusted_org = "trusted_org"
    trusted_actor = "user:seller_42"
    eng.grant_permission(
        trusted_org, trusted_actor, wf.ACTION_CREATE, "allow")
    st, body = undx_api.marketplace_create_listing_draft(
        USER_ID,
        {
            "org_id": "attacker_org",
            "actor": "user:someone_else",
            "listing": _listing("Trusted Identity Draft"),
        },
        trusted_org_id=trusted_org,
        trusted_actor=trusted_actor)
    assert st == 200 and body["ok"] is True, body
    center = eng.action_center(trusted_org, actor=trusted_actor)
    request_id = body["result"]["request"]["request_id"]
    assert any(row["request_id"] == request_id
               and row["actor"] == trusted_actor
               for row in center["requests"]), center
    assert eng.action_center("attacker_org", actor="user:someone_else")["requests"] == []


def _run_standalone():
    setup_module()
    tests = [
        test_draft_blocked_without_permission,
        test_allowed_draft_uses_marketplace_and_records_receipt,
        test_publish_plan_and_execute_use_confirmation_token,
        test_publish_execution_rejects_misbound_and_replayed_confirmation,
        test_emergency_stop_after_plan_blocks_execution,
        test_emergency_stop_blocks_publish,
        test_controller_surface_for_draft_and_publish,
        test_controller_trusted_identity_overrides_client_actor_and_org,
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
