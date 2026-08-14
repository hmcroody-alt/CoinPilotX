"""Sentinel Mission 5 — forensic financial source registry (Stage 1/4).

Every financial data source discovered in the Stage-1 inventory is recorded
here with an honest classification. The classification answers one question:
HOW MUCH can Sentinel trust a fact read from this source?

Absolute rule (mission charter): CLIENT_REPORTED data — e.g. a user pasting a
crypto txid into `payment_verifications`, or a mobile client claiming
"payment succeeded" — can NEVER become canonical authority, never confirm a
payment, and never clear a risk finding. It may only *raise questions*.

This module is a read-only map. It performs no queries, opens no
connections, and cannot mutate anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Source classes, ordered roughly by trustworthiness. The ceiling is the
# maximum confidence any Sentinel event/finding derived purely from that
# source may carry. Fusion may go LOWER, never higher.
# ---------------------------------------------------------------------------

SOURCE_CLASSES: Tuple[str, ...] = (
    "AUTHORITATIVE",       # canonical record, written by trusted server path
    "PROVIDER_REPORTED",   # external provider's claim (Stripe webhook, IAP)
    "DERIVED",             # computed server-side from authoritative inputs
    "PROJECTION",          # denormalized copy/estimate; may lag or drift
    "CACHE",               # recomputed-on-read convenience value
    "LEGACY",              # superseded subsystem; retained, not maintained
    "CLIENT_REPORTED",     # the user/client said so; NEVER authority
    "UNKNOWN",             # unclassified; treat as nearly worthless
)

CLASS_CONFIDENCE_CEILING = {
    "AUTHORITATIVE": 1.0,
    "PROVIDER_REPORTED": 0.9,
    "DERIVED": 0.8,
    "PROJECTION": 0.7,
    "CACHE": 0.6,
    "LEGACY": 0.5,
    "CLIENT_REPORTED": 0.3,
    "UNKNOWN": 0.1,
}

# Map source class -> sentinel source_trust grade (source_trust.py) so
# Event.__post_init__'s trust ceiling composes with the class ceiling.
CLASS_TRUST_GRADE = {
    "AUTHORITATIVE": "AUTHORITATIVE",
    "PROVIDER_REPORTED": "MEASURED",
    "DERIVED": "DERIVED",
    "PROJECTION": "DERIVED",
    "CACHE": "CONFIGURED",
    "LEGACY": "CONFIGURED",
    "CLIENT_REPORTED": "UNKNOWN",
    "UNKNOWN": "UNKNOWN",
}


@dataclass(frozen=True)
class FinancialSource:
    """One financial data source, as found by the Stage-1 forensic sweep."""

    source_id: str
    source_class: str
    location: str            # table/module + defining line where known
    written_by: str          # who legitimately writes it
    description: str
    notes: str = ""

    def __post_init__(self):
        if self.source_class not in SOURCE_CLASSES:
            raise ValueError(f"unknown source class {self.source_class!r}")

    @property
    def confidence_ceiling(self) -> float:
        return CLASS_CONFIDENCE_CEILING[self.source_class]

    @property
    def trust_grade(self) -> str:
        return CLASS_TRUST_GRADE[self.source_class]

    @property
    def canonical_authority_allowed(self) -> bool:
        """Only AUTHORITATIVE sources may state canonical financial truth.
        Explicitly: CLIENT_REPORTED can never be promoted to authority."""
        return self.source_class == "AUTHORITATIVE"


_S = FinancialSource

SOURCES: Tuple[FinancialSource, ...] = (
    # --- marketplace core ---------------------------------------------------
    _S("seller_transactions", "AUTHORITATIVE",
       "table seller_transactions (bot.py:104808)",
       "Stripe webhook handler only (bot.py:97067-97076)",
       "Primary marketplace transaction record: amounts, fee, seller net, "
       "status transitions paid/checkout_completed/refunded.",
       "Status is written only by the verified-signature Stripe webhook."),
    _S("marketplace_orders", "PROJECTION",
       "table marketplace_orders (bot.py:104490)",
       "pulse_upsert_marketplace_order (bot.py:50188)",
       "Denormalized order projection for feed/UI; may lag the txn record."),
    _S("business_os_mkt_orders", "AUTHORITATIVE",
       "business_os schema.py:127-171",
       "Business OS order pipeline (env-gated)",
       "Gated Business OS order record incl. platform_fee_bps, "
       "refunded_cents, payout_status."),
    _S("platform_fee_rules", "AUTHORITATIVE",
       "table platform_fee_rules (bot.py:104780)",
       "owner/admin configuration",
       "Live fee policy: merchant 1000bps, teacher 1500bps. The proposed "
       "500bps standard is INACTIVE behind three env gates."),
    _S("commercial_quote_snapshot", "AUTHORITATIVE",
       "seller_transactions.metadata_json['commercial_quote'] (bot.py:85400)",
       "checkout flow at quote time",
       "Immutable fee snapshot; refunds must use this, never current policy."),
    _S("marketplace_commercial_settlements", "AUTHORITATIVE",
       "settlement_service.py:77-90",
       "settlement service (append-oriented)",
       "Immutable settlement rows + payout_state machine."),
    _S("marketplace_payout_state_events", "AUTHORITATIVE",
       "settlement service append-only event table",
       "settlement service",
       "Append-only payout state transition history."),
    _S("marketplace_sellers", "DERIVED",
       "table marketplace_sellers (bot.py:104284)",
       "seller onboarding flows",
       "Seller registry; identity/config, not money truth."),
    _S("seller_payout_accounts", "PROJECTION",
       "table seller_payout_accounts (bot.py:104791)",
       "Stripe Connect sync",
       "Projection of Stripe Connect account state; Stripe is the truth."),
    # --- payments -------------------------------------------------------------
    _S("stripe_webhook", "PROVIDER_REPORTED",
       "bot.py:96970-97026",
       "Stripe (signature-verified via STRIPE_WEBHOOK_SECRET)",
       "checkout.session.completed / invoice.paid / payment_intent.succeeded; "
       "signature verified, then projected into authoritative tables.",
       "Provider claim: high trust but still external testimony."),
    _S("provider_webhook_events", "AUTHORITATIVE",
       "webhook_inbox.py:89-383, UNIQUE(provider, provider_event_id)",
       "webhook inbox",
       "Idempotency inbox: received/processing/processed/failed/skipped, "
       "DLQ escalation after 5 retries."),
    _S("stripe_events_legacy_dedupe", "LEGACY",
       "bot.py:96515-96523",
       "legacy webhook path",
       "Older Stripe event dedupe table; superseded by the inbox."),
    _S("payment_records", "PROVIDER_REPORTED",
       "db.py:464",
       "payment provider callbacks",
       "Provider-reported payment records."),
    _S("apple_iap_webhook", "PROVIDER_REPORTED",
       "bot.py:23977",
       "Apple server notifications",
       "IAP events; verification weaker than Stripe's signed webhooks."),
    _S("google_iap_webhook", "PROVIDER_REPORTED",
       "bot.py:24006",
       "Google RTDN",
       "IAP events; verification weaker than Stripe's signed webhooks."),
    _S("payment_verifications", "CLIENT_REPORTED",
       "db.py:158, bot.py:26591-26626",
       "END USERS (self-reported crypto txids)",
       "User-submitted payment claims. MUST NEVER become canonical "
       "authority, confirm a payment, or clear a finding.",
       "The canonical example of the client-authority attack surface."),
    _S("unmatched_payments", "PROVIDER_REPORTED",
       "db.py:467",
       "payment reconciliation",
       "Orphaned provider payments awaiting attribution."),
    _S("pulse_payment_router_internal", "AUTHORITATIVE",
       "services/pulse_payment_router.py PROVIDER_INTERNAL_LEDGER",
       "server promo-credit path",
       "Internal ledger rail for promo credits."),
    # --- refunds / payouts / ledger -------------------------------------------
    _S("business_os_mkt_refunds", "AUTHORITATIVE",
       "refunds.py:178-216, schema.py:214-232",
       "refund_order() — admin/system actor + reason required",
       "Refund ledger with idempotency SHA256(order_id+key) and cumulative "
       "over-refund guard."),
    _S("seller_payout_requests", "AUTHORITATIVE",
       "seller_payouts.py:141-169",
       "payout service (Wave B)",
       "Payout request + state machine; illegal transitions raise "
       "PAYOUT_STATE_CONFLICT incidents."),
    _S("seller_payout_events", "AUTHORITATIVE",
       "seller_payouts.py (append-only)",
       "payout service + apply_stripe_payout_event() (694-889)",
       "Payout event history projecting Stripe truth."),
    _S("ledger_transactions", "AUTHORITATIVE",
       "ledger.py:163-206 (idempotency_key UNIQUE)",
       "double-entry ledger service",
       "Canonical money movement: transactions + entries + balances with "
       "overdraft guards; escrow accounts mkt_order_escrow:<order_id>."),
    _S("ledger_balances", "AUTHORITATIVE",
       "ledger.py",
       "double-entry ledger service",
       "Balance authority incl. seller_payable:<uid>."),
    _S("creator_wallets", "CACHE",
       "bot.py:101869 / seller_money.py",
       "recomputed on read",
       "Display cache of seller money; ledger_balances is the truth."),
    _S("seller_payouts_wave_a", "LEGACY",
       "db.py:418",
       "legacy Wave A payouts",
       "Superseded by seller_payout_requests (Wave B)."),
    # --- advertising -----------------------------------------------------------
    _S("ad_wallet_ledger_accounts", "AUTHORITATIVE",
       "funding.py:404-506 (advertiser:<uid>:wallet, ad_campaign_escrow:<cid>)",
       "ad funding service (idempotent reserve/release)",
       "Overdraft-guarded advertiser wallet + campaign escrow accounts."),
    _S("business_os_ad_campaign_funding", "AUTHORITATIVE",
       "business_os ad funding tables",
       "ad funding service",
       "Campaign budget + funding_status unfunded→funded→released."),
    _S("business_os_ad_funding_ops", "AUTHORITATIVE",
       "UNIQUE idempotency, append-only",
       "ad funding service",
       "Append-only funding operation log."),
    _S("business_os_ad_billing_events", "AUTHORITATIVE",
       "billing_status pending→{processed,ineligible,failed}",
       "ad billing pipeline (idempotent, ledger_txn_reference)",
       "Authoritative ad spend billing events."),
    _S("business_os_ad_spend_accumulator", "DERIVED",
       "accrued_millicents + budget_exhausted latch (BEGIN IMMEDIATE)",
       "ad spend pipeline",
       "Sub-cent spend accrual; one of three spend sources that must agree."),
    _S("business_os_ad_pricing_policy", "AUTHORITATIVE",
       "versioned server-authoritative pricing",
       "owner/admin",
       "Server-side ad pricing policy versions."),
    _S("ad_budget_exhaustion_projection", "PROJECTION",
       "spend.py project_budget_exhaustion()",
       "ad spend pipeline",
       "Forecast, not fact: when a budget will run out."),
    # --- shared / misc ---------------------------------------------------------
    _S("fee_ledger", "DERIVED",
       "db.py:428", "fee accounting", "Platform fee accounting rows."),
    _S("treasury_transactions", "AUTHORITATIVE",
       "db.py:420", "treasury flows", "Treasury movement records."),
    _S("payment_audit_logs", "AUTHORITATIVE",
       "db.py:413", "payment paths (append-only)",
       "Payment audit trail."),
    _S("pulse_ad_wallet_transactions", "DERIVED",
       "db.py:337", "pulse ad wallet path",
       "Pulse-side ad wallet transaction rows."),
    _S("payout_queue_history_failures", "LEGACY",
       "db.py:422-424", "legacy payout worker",
       "Legacy payout queue tables."),
    _S("admin_audit_logs", "AUTHORITATIVE",
       "db.py:462 via services/audit_service.py",
       "log_admin_action()", "Admin action audit trail."),
    _S("crypto_legacy_tables", "LEGACY",
       "transaction_history, transactions, connected_wallets, "
       "paper_simulator_* (various)",
       "legacy crypto bot subsystem",
       "Do NOT share the double-entry ledger; excluded from reconciliation."),
    _S("client_payment_claim", "CLIENT_REPORTED",
       "any client-submitted 'payment succeeded' signal (mobile/web)",
       "END USERS / CLIENT APPS",
       "Client claims about payment outcomes. Evidence of a QUESTION, "
       "never an ANSWER."),
)

_BY_ID = {s.source_id: s for s in SOURCES}


def get(source_id: str) -> Optional[FinancialSource]:
    return _BY_ID.get(source_id)


def source_ids() -> Tuple[str, ...]:
    return tuple(_BY_ID)


def confidence_ceiling(source_id: str) -> float:
    src = _BY_ID.get(source_id)
    if src is None:
        return CLASS_CONFIDENCE_CEILING["UNKNOWN"]
    return src.confidence_ceiling


def trust_grade(source_id: str) -> str:
    src = _BY_ID.get(source_id)
    if src is None:
        return "UNKNOWN"
    return src.trust_grade


def is_canonical_authority(source_id: str) -> bool:
    src = _BY_ID.get(source_id)
    return bool(src and src.canonical_authority_allowed)


def client_reported_sources() -> Tuple[str, ...]:
    return tuple(s.source_id for s in SOURCES
                 if s.source_class == "CLIENT_REPORTED")


def inventory() -> list:
    """Read-only inventory dump for docs/observability."""
    return [{
        "source_id": s.source_id,
        "class": s.source_class,
        "location": s.location,
        "written_by": s.written_by,
        "confidence_ceiling": s.confidence_ceiling,
        "canonical_authority_allowed": s.canonical_authority_allowed,
    } for s in SOURCES]
