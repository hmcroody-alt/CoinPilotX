import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="mkt_ops_"), "test.db")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from services import marketplace_commercial_operations as ops
from services import marketplace_settlement_service as settlements
from services.business_os.ledger import ledger
from services import marketplace_payout_scheduler as scheduler
import json
from datetime import datetime, timedelta, timezone

def test_current_terms_are_ten_percent_and_future_notice_hidden():
    t = ops.terms()
    assert t["current"]["platform_fee_bps"] == 1000
    assert t["future_notice"] == {"published": False, "policy_version": "MARKETPLACE_STANDARD_V1", "platform_fee_bps": 500, "effective_at": None}
    accepted = ops.accept_terms(7, source="native")
    assert accepted["terms_version"] == ops.CURRENT_TERMS_VERSION

def test_ip_case_has_audited_transitions():
    case = ops.submit_ip_case(listing_id=1, seller_id=7, claimant_reference="rights-holder:1",
                              claim_type="trademark", evidence_refs=["object:e1"])
    decided = ops.transition_ip_case(case["case_id"], "listing_restricted", actor="admin:1",
                                     reason="credible ownership evidence", evidence_refs=["object:e2"])
    assert decided["status"] == "listing_restricted"
    assert decided["decision_reason"] == "credible ownership evidence"

def test_compliance_allowlist_and_safe_public_fields():
    row = ops.set_compliance(7, "required", requirements=["identity_business", "contact"],
        completed=["contact"], actor_reason="versioned policy evaluation",
        public_disclosure={"business_name":"Store","tax_id":"secret"})
    assert row["status"] == "required"
    assert "tax_id" not in row["public_disclosure_json"]

def test_reconciliation_is_read_only_and_readiness_stays_locked():
    ledger.ensure_schema(); settlements.ensure_schema()
    run = ops.reconcile()
    assert run["status"] == "balanced"
    ready = ops.readiness()
    assert ready["owner_approved"] == "NO" and ready["activatable"] == "NO"

def test_scheduler_uses_canonical_payout_and_does_not_claim_paid():
    tx = {"id": 91, "seller_user_id": 7, "item_type": "marketplace_product", "amount_cents": 10000,
          "platform_fee_cents": 1000, "seller_net_cents": 9000, "currency": "USD",
          "metadata_json": json.dumps({"commercial_quote": {"merchandise_net_minor":10000,
          "buyer_total_minor":10000,"platform_fee_bps":1000,"fee_policy_version":"MARKETPLACE_LEGACY_CURRENT"}})}
    settlements.settle_paid_transaction(tx, payout_ready=True)
    settlements.mark_delivered(91, actor="carrier", idempotency_key="delivered:91")
    settlements.evaluate_eligibility(91, now=datetime.now(timezone.utc)+timedelta(days=3))
    metrics = scheduler.run_once(account_resolver=lambda _: {"connected_account_id":"acct_test","payouts_enabled":True},
                                 provider_create=lambda _: {"id":"po_test"})
    assert metrics["scheduled_count"] == 1
    assert settlements.get_settlement(91)["payout_state"] == "scheduled"
    assert scheduler.apply_provider_event("po_test", paid=True, event_id="evt_paid")["changed"] == 1
    assert settlements.get_settlement(91)["payout_state"] == "paid"
