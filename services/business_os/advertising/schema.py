"""Advertising vertical schema — additive `business_os_ad_*` tables.

Follows the entitlement slice's ``ensure_schema`` convention (services/business_os/
entitlements/schema.py): idempotent ``CREATE TABLE IF NOT EXISTS`` via
``services.db`` (SQLite dev / PostgreSQL prod), no ``bot.py`` import, never mutates
any legacy table. In particular the legacy ``pulse_ads_service`` tables
(``pulse_ad_campaigns`` …) are left completely untouched; this slice builds a new
canonical surface alongside them.

Slice-1 tables:

* ``business_os_ad_advertisers`` — advertiser approval state, ONE row per user.
  This is the *merchant/advertiser approval* input (§8 of the shared-foundation
  checkpoint), kept separate from commercial entitlement and from account hold.
* ``business_os_ad_campaigns`` — campaign drafts with ownership + lifecycle state.
* ``business_os_ad_audit`` — append-only audit of advertiser/campaign changes.

Text UUID primary keys are used for campaigns to avoid depending on
engine-specific ``lastrowid`` semantics across SQLite/PostgreSQL.

Everything here is structural and inert: creating empty tables changes zero
runtime behaviour. All reads/writes are gated in ``service`` behind the
``BUSINESS_OS_ADVERTISING`` flag.
"""

from __future__ import annotations

from datetime import datetime, timezone

from services import db


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _existing_columns(conn, table: str) -> set:
    """Column names present on ``table`` (cross-engine). Empty set on any error."""
    try:
        if db.ENGINE_NAME == "sqlite":
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return {r[1] for r in rows}  # PRAGMA: (cid, name, type, ...)
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def _ensure_columns(conn, table: str, columns: dict) -> None:
    """Additively add any missing ``{name: sql_type}`` columns to ``table``.

    Idempotent: introspects current columns first and only issues
    ``ALTER TABLE ADD COLUMN`` for absent ones. Column names/types come only from
    the fixed literal mapping below — never from caller input — so the f-string
    DDL carries no injection surface. On PostgreSQL the db layer additionally
    rewrites ADD COLUMN to ADD COLUMN IF NOT EXISTS, so this is race-safe there.
    """
    present = _existing_columns(conn, table)
    for name, sql_type in columns.items():
        if name in present:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def ensure_schema(conn=None) -> None:
    """Create the advertising tables if absent. Idempotent.

    Safe to call at startup and from tests. Owns its connection unless one is
    passed in (so callers can compose it into a larger transaction).
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # Advertiser approval state — one row per user. Separate authority from
        # commercial entitlement and from account hold/suspension.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_advertisers (
                user_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                display_name TEXT,
                notes TEXT,
                approved_by TEXT,
                approved_at TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_advertisers_status "
            "ON business_os_ad_advertisers (status)"
        )
        # Campaign drafts — ownership + lifecycle state.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_campaigns (
                campaign_id TEXT PRIMARY KEY,
                advertiser_user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                objective TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                destination_url TEXT,
                created_by TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_campaigns_owner "
            "ON business_os_ad_campaigns (advertiser_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_campaigns_status "
            "ON business_os_ad_campaigns (status)"
        )
        # Slice-3 (review lifecycle): additive column holding the admin's rejection
        # reason so the campaign owner can see WHY a submission was rejected. Added
        # idempotently so upgrading an existing slice-1/2 table is safe. The review
        # STATE itself lives in the existing `status` column (submitted/approved/
        # rejected); the reviewing admin is captured in the audit trail, not here.
        _ensure_columns(conn, "business_os_ad_campaigns", {"review_reason": "TEXT"})
        # Slice-4 (funding readiness): per-campaign funding record. ONE row per
        # campaign. Holds the configured budget and the funding STATE
        # (unfunded|funding_pending|funded|funding_failed|released) — deliberately
        # SEPARATE from the review `status` column above. The money itself lives in
        # the canonical ledger (ledger_entries); the reservation/release ledger
        # transaction ids are referenced here so the state is reconstructable and
        # auditable. No mutable balance is stored here — balances are derived from
        # the ledger.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_campaign_funding (
                campaign_id TEXT PRIMARY KEY,
                advertiser_user_id TEXT NOT NULL,
                budget_cents INTEGER,
                currency TEXT,
                funding_status TEXT NOT NULL DEFAULT 'unfunded',
                reserved_amount_cents INTEGER,
                reservation_key TEXT,
                reservation_txn_id TEXT,
                release_txn_id TEXT,
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_funding_status "
            "ON business_os_ad_campaign_funding (funding_status)"
        )
        # Slice-4: append-only funding operation log. The UNIQUE idempotency_key is
        # the DB-level idempotency guarantee for reserve/release: a retried
        # operation collides here and is served as a no-op; the same key reused for
        # a DIFFERENT operation is detected and rejected. Each row references the
        # ledger transaction it drove (ledger_txn_id) so funding is traceable back
        # to immutable ledger entries.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_funding_ops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                campaign_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                currency TEXT NOT NULL,
                ledger_txn_id TEXT,
                related_txn_id TEXT,
                actor TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_funding_ops_campaign "
            "ON business_os_ad_funding_ops (campaign_id)"
        )
        # Slice-5 (operational lifecycle): per-campaign operational record. ONE row
        # per campaign. Holds the operational STATE
        # (inactive|scheduled|active|paused|completed|cancelled) — deliberately
        # SEPARATE from BOTH the review `status` column and the funding_status. It
        # authorizes a campaign for FUTURE delivery; it never delivers, auctions,
        # paces, or moves money. Optional UTC start/end bound when the campaign is
        # eligible to run; `activated_at` records when it first became active. No
        # audience, impression, click, spend, or escrow field lives here.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_campaign_operations (
                campaign_id TEXT PRIMARY KEY,
                advertiser_user_id TEXT NOT NULL,
                operational_status TEXT NOT NULL DEFAULT 'inactive',
                start_at TEXT,
                end_at TEXT,
                activated_at TEXT,
                paused_at TEXT,
                completed_at TEXT,
                cancelled_at TEXT,
                last_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_operations_status "
            "ON business_os_ad_campaign_operations (operational_status)"
        )
        # Slice-6 (ad sets + creative foundation): the campaign's children in the
        # canonical hierarchy `advertiser -> campaign -> ad set -> creative`.
        #
        # An ad set carries its OWN lifecycle STATE
        # (draft|submitted|approved|rejected|paused|archived) in the `status`
        # column — deliberately SEPARATE from the campaign review `status`, the
        # funding_status, and the operational_status. An ad set is NEVER made
        # deliverable merely because its parent campaign is active; delivery
        # readiness is DERIVED live (never a stored boolean) from all the separate
        # inputs. `placements_json` holds the placement allowlist selection (feed/
        # reels, config only — no delivery is wired); `audience_json` holds the
        # governed, validated, versioned audience spec (never arbitrary client
        # JSON); `budget_allocation_json` holds optional allocation METADATA only —
        # no money is stored or moved here (funds live in the canonical ledger).
        # `version` is the optimistic-concurrency / revision counter.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_sets (
                ad_set_id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                advertiser_user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                placements_json TEXT,
                audience_json TEXT,
                schedule_start_at TEXT,
                schedule_end_at TEXT,
                budget_allocation_json TEXT,
                review_reason TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                archived_at TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_sets_campaign "
            "ON business_os_ad_sets (campaign_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_sets_owner "
            "ON business_os_ad_sets (advertiser_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_sets_status "
            "ON business_os_ad_sets (status)"
        )
        # Slice-6: creatives — the leaf of the hierarchy. Bound to BOTH an ad set
        # and its campaign, and to the SAME advertiser owner (parent-child
        # integrity is enforced in the service, never assumed here). `creative_type`
        # is image|video|reels_video. `media_asset_id`/`thumbnail_asset_id` are
        # canonical references into the authoritative PulseSoc media ownership
        # system (`pulse_media_assets.id`) — never a raw client filesystem path.
        # `destination_type`/`destination_ref` capture where a tap leads: an
        # internal canonical id (profile/post/reel/marketplace_product, existence
        # verified) or a normalized external HTTPS URL (stored normalized behind an
        # explicit later-safety-review boundary). Review STATE lives in `status`
        # (draft|submitted|approved|rejected|archived); `review_reason` is the
        # admin's structured rejection reason, visible to the owner. `version` is
        # the revision counter; `supersedes_creative_id` links a new version spawned
        # when an already-submitted/approved creative is materially revised — the
        # prior version and its review history are NEVER silently mutated.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_creatives (
                creative_id TEXT PRIMARY KEY,
                ad_set_id TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                advertiser_user_id TEXT NOT NULL,
                creative_type TEXT NOT NULL,
                media_asset_id TEXT,
                thumbnail_asset_id TEXT,
                headline TEXT,
                body TEXT,
                call_to_action TEXT,
                destination_type TEXT,
                destination_ref TEXT,
                accessibility_text TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                review_reason TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                supersedes_creative_id TEXT,
                archived_at TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_creatives_ad_set "
            "ON business_os_ad_creatives (ad_set_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_creatives_campaign "
            "ON business_os_ad_creatives (campaign_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_creatives_owner "
            "ON business_os_ad_creatives (advertiser_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_creatives_status "
            "ON business_os_ad_creatives (status)"
        )
        # Slice-7 (Basic Feed/Reels delivery MVP): the delivery-instance +
        # immutable event tables. A delivery instance is a server-authorized
        # opportunity to display ONE approved creative version at a placement; it
        # binds the EXACT hierarchy (campaign/ad set/creative + creative_version)
        # that was eligible at decision time. `subject_ref` is a privacy-safe
        # salted-hash viewer reference — never a raw user id. `impression_token`
        # is a server secret proving the client received THIS delivery; the bound
        # creative can never be substituted because the authoritative creative/
        # version is always read back FROM this row, never from client input.
        # `eligibility_snapshot_json` is the structured "why eligible" snapshot.
        # No money is stored or moved here.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_delivery_instances (
                delivery_id TEXT PRIMARY KEY,
                advertiser_user_id TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                ad_set_id TEXT NOT NULL,
                creative_id TEXT NOT NULL,
                creative_version INTEGER NOT NULL,
                placement TEXT NOT NULL,
                subject_ref TEXT NOT NULL,
                destination_type TEXT,
                destination_ref TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                decision_at TEXT NOT NULL,
                expires_at TEXT,
                request_ref TEXT,
                impression_token TEXT NOT NULL,
                eligibility_snapshot_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_delivery_campaign "
            "ON business_os_ad_delivery_instances (campaign_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_delivery_creative "
            "ON business_os_ad_delivery_instances (creative_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_delivery_advertiser "
            "ON business_os_ad_delivery_instances (advertiser_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_delivery_subject "
            "ON business_os_ad_delivery_instances (subject_ref, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_delivery_placement "
            "ON business_os_ad_delivery_instances (placement)"
        )
        # Immutable impression events. `dedup_key` UNIQUE makes a retried/duplicate
        # impression idempotent (collides, served as no-op). The
        # (campaign_id, subject_ref, event_at) index backs the server-authoritative
        # frequency cap. billing_eligible/billing_processed carry the canonical
        # reference the NEXT (billing) slice needs, WITHOUT deducting money here.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_impression_events (
                event_id TEXT PRIMARY KEY,
                delivery_id TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                ad_set_id TEXT NOT NULL,
                creative_id TEXT NOT NULL,
                creative_version INTEGER NOT NULL,
                placement TEXT NOT NULL,
                subject_ref TEXT NOT NULL,
                advertiser_user_id TEXT NOT NULL,
                event_at TEXT NOT NULL,
                dedup_key TEXT NOT NULL UNIQUE,
                request_meta_json TEXT,
                fraud_status TEXT NOT NULL DEFAULT 'clean',
                billing_eligible INTEGER NOT NULL DEFAULT 0,
                billing_processed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_impr_delivery "
            "ON business_os_ad_impression_events (delivery_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_impr_campaign "
            "ON business_os_ad_impression_events (campaign_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_impr_freq "
            "ON business_os_ad_impression_events (campaign_id, subject_ref, event_at)"
        )
        # Immutable click events. By policy a click requires an accepted impression
        # on the same delivery. The destination is SERVER-RESOLVED from the
        # delivery's bound creative version — never trusted from the client.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_click_events (
                event_id TEXT PRIMARY KEY,
                delivery_id TEXT NOT NULL,
                impression_event_id TEXT,
                campaign_id TEXT NOT NULL,
                ad_set_id TEXT NOT NULL,
                creative_id TEXT NOT NULL,
                creative_version INTEGER NOT NULL,
                placement TEXT NOT NULL,
                subject_ref TEXT NOT NULL,
                advertiser_user_id TEXT NOT NULL,
                destination_type TEXT,
                destination_ref TEXT,
                event_at TEXT NOT NULL,
                dedup_key TEXT NOT NULL UNIQUE,
                request_meta_json TEXT,
                fraud_status TEXT NOT NULL DEFAULT 'clean',
                billing_eligible INTEGER NOT NULL DEFAULT 0,
                billing_processed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_click_delivery "
            "ON business_os_ad_click_events (delivery_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_click_campaign "
            "ON business_os_ad_click_events (campaign_id)"
        )
        # Billing (canonical CPM/CPC billing events + escrow consumption). No money
        # is held here — escrow lives in the ledger; a billing event is the
        # immutable record of a server decision to charge for ONE accepted
        # impression/click. idempotency_key UNIQUE is the double-charge backbone.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_billing_events (
                billing_event_id TEXT PRIMARY KEY,
                advertiser_user_id TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                ad_set_id TEXT,
                creative_id TEXT,
                creative_version INTEGER,
                delivery_instance_id TEXT,
                source_event_type TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                billing_model TEXT NOT NULL,
                billable_quantity INTEGER NOT NULL DEFAULT 1,
                unit_price_cents INTEGER NOT NULL,
                pricing_policy_version INTEGER,
                total_amount_cents INTEGER NOT NULL DEFAULT 0
                    CHECK (total_amount_cents >= 0),
                accrued_millicents INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL,
                billing_status TEXT NOT NULL DEFAULT 'pending',
                ledger_txn_reference TEXT,
                idempotency_key TEXT NOT NULL UNIQUE,
                eligibility_decision TEXT,
                failure_reason TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_billing_campaign "
            "ON business_os_ad_billing_events (campaign_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_billing_advertiser "
            "ON business_os_ad_billing_events (advertiser_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_billing_source "
            "ON business_os_ad_billing_events (source_event_type, source_event_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_billing_status "
            "ON business_os_ad_billing_events (billing_status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_billing_created "
            "ON business_os_ad_billing_events (campaign_id, created_at)"
        )
        # Versioned, server-authoritative pricing policy. No hardcoded production
        # prices in code: the active price for a (billing_model, currency) is the
        # active row with the highest effective_version.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_pricing_policy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                billing_model TEXT NOT NULL,
                currency TEXT NOT NULL,
                unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
                effective_version INTEGER NOT NULL,
                min_price_cents INTEGER,
                max_price_cents INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                created_by TEXT,
                note TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (billing_model, currency, effective_version)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_pricing_lookup "
            "ON business_os_ad_pricing_policy "
            "(billing_model, currency, active, effective_version)"
        )
        # Per-campaign sub-cent CPM accumulator + budget-exhaustion latch. Only
        # whole cents are ever posted to the ledger; the milli-cent remainder is
        # carried here so CPM rounding is deterministic and value-preserving.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_spend_accumulator (
                campaign_id TEXT NOT NULL,
                currency TEXT NOT NULL,
                accrued_millicents INTEGER NOT NULL DEFAULT 0
                    CHECK (accrued_millicents >= 0),
                billed_cents INTEGER NOT NULL DEFAULT 0 CHECK (billed_cents >= 0),
                impressions_billed INTEGER NOT NULL DEFAULT 0,
                clicks_billed INTEGER NOT NULL DEFAULT 0,
                budget_exhausted INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (campaign_id, currency)
            )
            """
        )
        # Append-only audit.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT,
                advertiser_user_id TEXT,
                action TEXT NOT NULL,
                actor TEXT,
                reason TEXT,
                before_json TEXT,
                after_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_audit_campaign "
            "ON business_os_ad_audit (campaign_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_audit_advertiser "
            "ON business_os_ad_audit (advertiser_user_id)"
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
