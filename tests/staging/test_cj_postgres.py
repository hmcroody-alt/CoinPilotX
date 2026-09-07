"""Real PostgreSQL engine acceptance; provider responses stay synthetic.

Every case gets a fresh, exclusively owned schema. No public-schema test rows,
no production fallback, and no CJ network calls. Existing assertions are reused
without importing their SQLite database fixture into this module.
"""
import importlib
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text

from services import db, schema_guard
from services.business_os.business import schema as business_schema
from services.business_os.store import schema as store_schema
from services.business_os.suppliers import schema, quota
from tests.business_os.test_cj_connections import SECRETS, FakeAdapter, auth
from tests.business_os.test_cj_fulfillment import ready
from tests.business_os.test_cj_quota import controller
from tests.business_os.test_cj_gateway import connected

CASES = {
    "connections": [
        "connect_verifies_settings_and_explicit_shop_before_secure_store",
        "scope_cannot_read_use_token_or_invoke_provider_for_other_merchant",
        "same_cj_account_cannot_be_claimed_by_another_merchant",
        "refresh_uses_returned_expiry_and_same_account",
        "refresh_cannot_swap_account", "durable_singleflight_refresh_has_one_provider_call",
        "identity_fingerprint_is_not_unkeyed_and_survives_cipher_key_rotation",
        "business_transfer_does_not_transfer_existing_secret_access",
    ],
    "quota": ["parallel_controllers_admit_only_one_same_account_request",
              "ip_allows_only_three_verified_or_pending_cj_accounts",
              "provider_429_blocks_complete_shared_egress_and_recovers_after_deadline",
              "stale_response_cannot_restore_points_reserved_by_new_request"],
    "gateway": ["cache_singleflight_lease_allows_only_one_provider_read",
                "import_draft_requires_manager_and_keeps_merchant_retail_fields_separate"],
    "fulfillment": ["exactly_one_concurrent_claim", "intent_persists_before_provider_and_is_immutable",
                    "one_canonical_order_cannot_create_via_two_owned_stores",
                    "sandbox_create_requires_independent_readback",
                    "timeout_after_provider_success_never_retries_post",
                    "unknown_not_found_is_not_absence_proof", "expired_sending_lease_never_reclaims_create",
                    "orders_and_funding_remain_canonical_unchanged"],
    "webhooks": ["valid_receipt_durable_redacted_deduplicated",
                 "concurrent_duplicates_create_one_durable_receipt",
                 "same_event_identity_different_raw_body_is_conflict",
                 "delayed_and_out_of_order_events_only_schedule_readback"],
    "worker": ["worker_dark_without_provider_approval", "bounded_worker_seeds_only_owned_selected_resources",
               "retry_after_not_shortened_by_worker", "successful_selected_read_persists_snapshot_and_sync_time"],
}
for area, cases in CASES.items():
    module = importlib.import_module("tests.business_os.test_cj_" + area)
    for name in cases:
        globals()["test_pg_" + area + "__" + name] = getattr(module, "test_" + name)


@pytest.fixture(autouse=True)
def database(monkeypatch):
    url = os.getenv("CJ_ACCEPTANCE_DATABASE_URL", "")
    if not url:
        pytest.skip("CJ_ACCEPTANCE_DATABASE_URL required; no SQLite substitute")
    assert os.getenv("CJ_ACCEPTANCE_POSTGRES_APPROVED") == "1"
    assert url.startswith(("postgresql://", "postgres://", "postgresql+psycopg2://"))
    normalized = db._normalize_engine_url(url)
    schema_name = "cj_acceptance_" + uuid.uuid4().hex
    assert re.fullmatch(r"cj_acceptance_[a-f0-9]{32}", schema_name)
    admin = create_engine(normalized, connect_args={"connect_timeout": 15,
                          "options": "-c lock_timeout=10000 -c statement_timeout=30000"})
    with admin.begin() as connection:
        connection.execute(text('CREATE SCHEMA "' + schema_name + '"'))
    engine = create_engine(normalized, pool_size=8, max_overflow=4, pool_pre_ping=True,
                           connect_args={"connect_timeout": 15, "options": "-c search_path=" + schema_name
                                         + " -c lock_timeout=10000 -c statement_timeout=30000"})
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "IS_POSTGRES", True)
    monkeypatch.setattr(db, "ENGINE_NAME", "postgresql")
    monkeypatch.setattr(db, "ENGINE_URL", normalized)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("BUSINESS_OS_SUPPLIERS_CJ", "ON")
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "SANDBOX")
    monkeypatch.setenv("PRODUCTION_CJ_FULFILLMENT_ENABLED", "OFF")
    monkeypatch.setenv("REAL_CJ_FUNDING_ENABLED", "OFF")
    monkeypatch.setenv("SUPPLIER_CREDENTIAL_KEYS", "test:" + "13" * 32)
    monkeypatch.setenv("SUPPLIER_CREDENTIAL_KEY_ACTIVE", "test")
    monkeypatch.setenv("SUPPLIER_ACCOUNT_INDEX_KEY", "24" * 32)
    monkeypatch.delenv("CJ_EGRESS_GROUP", raising=False)
    opened = []
    original_connect = db.connect
    def tracked_connect():
        connection = original_connect()
        opened.append(connection)
        return connection
    monkeypatch.setattr(db, "connect", tracked_connect)
    import requests
    def no_http(*args, **kwargs):
        raise AssertionError("Live HTTP forbidden during PostgreSQL fixture acceptance")
    monkeypatch.setattr(requests.sessions.Session, "request", no_http)
    schema_guard.reset_all()
    try:
        business_schema.ensure_schema()
        store_schema.ensure_schema()
        schema.ensure_schema()
        quota.ensure_schema()
        conn = db.connect()
        try:
            assert conn.execute("SELECT current_schema()").fetchone()[0] == schema_name
            assert conn.execute("SHOW transaction_isolation").fetchone()[0] == "read committed"
            for suffix, owner in (("a", "100"), ("b", "200")):
                conn.execute("INSERT INTO business_os_business(business_id,owner_user_id,display_name,created_at,updated_at) VALUES(?,?,?,?,?)",
                             ("biz-" + suffix, owner, "Synthetic PG Acceptance", "now", "now"))
                conn.execute("INSERT INTO business_os_store_storefront(storefront_id,business_id,name,created_at,updated_at) VALUES(?,?,?,?,?)",
                             ("store-" + suffix, "biz-" + suffix, "Synthetic PG Acceptance", "now", "now"))
            for actor, role in (("300", "viewer"), ("400", "manager")):
                conn.execute("INSERT INTO business_os_business_members(member_id,business_id,user_id,role,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                             (actor, "biz-a", actor, role, "active", "now", "now"))
            conn.commit()
        finally:
            conn.close()
        yield
    finally:
        # A failed assertion can retain a cursor/connection in pytest's traceback.
        # Close those explicitly before schema cleanup instead of waiting on GC.
        for connection in opened:
            connection.close()
        engine.dispose()
        with admin.begin() as connection:
            # Only the random schema created by this test, never public/customer data.
            connection.execute(text('DROP SCHEMA "' + schema_name + '" CASCADE'))
        admin.dispose()
        schema_guard.reset_all()


def test_pg_bootstrap_indexes_and_reconnect():
    from services.business_os.suppliers import gateway, fulfillment, worker
    from services.business_os.payments import webhook_inbox
    for ensure in (schema.ensure_schema, gateway.ensure_schema, fulfillment.ensure_schema,
                   quota.ensure_schema, worker.ensure_schema, webhook_inbox.ensure_schema):
        ensure()
        ensure()
    conn = db.connect()
    try:
        indexes = conn.execute("SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema()").fetchall()
        assert len(indexes) >= 20
        assert any("UNIQUE" in row[0] and "business_os_supplier_connections" in row[0] for row in indexes)
        assert any("UNIQUE" in row[0] and "business_os_supplier_intents" in row[0] for row in indexes)
        conn.commit()
    finally:
        conn.close()
    db.engine.dispose()  # New physical pool: persisted schema remains available.
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM business_os_supplier_connections").fetchone()[0] == 0
    conn.close()


def test_pg_concurrent_account_claims_one_owner():
    from services.business_os.suppliers import connections
    import threading
    barrier = threading.Barrier(2)
    class ClaimAdapter(FakeAdapter):
        def get_shops(self):
            barrier.wait(timeout=30)
            return super().get_shops()
    def claim(suffix, actor):
        try:
            connections.connect_cj("biz-" + suffix, "store-" + suffix, actor,
                                   SECRETS["api_key"], "cj-shop-a", adapter=ClaimAdapter())
            return "connected"
        except connections.SupplierConnectionError:
            return "rejected"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim, "a", "100"), pool.submit(claim, "b", "200")]
        outcomes = [future.result(timeout=60) for future in futures]
    assert sorted(outcomes) == ["connected", "rejected"]
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM business_os_supplier_connections").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM business_os_supplier_credential_vault").fetchone()[0] == 1
    conn.close()
