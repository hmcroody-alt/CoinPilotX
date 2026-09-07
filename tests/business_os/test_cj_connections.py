"""Real SQLite tenancy/vault tests against synthetic (never live) CJ responses."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading
from types import SimpleNamespace

import pytest

from services import db
from services.business_os.business import schema as business_schema
from services.business_os.store import schema as store_schema
from services.business_os.suppliers import connections as svc, schema, vault

SECRETS = {"api_key": "fixture-api-key-A", "access_token": "fixture-access-A",
           "refresh_token": "fixture-refresh-A", "open_id": "fixture-open-id-A"}


def auth(**overrides):
    values = dict(access_token=SECRETS["access_token"], refresh_token=SECRETS["refresh_token"],
                  open_id=SECRETS["open_id"],
                  access_expires_at=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
                  refresh_expires_at=(datetime.now(timezone.utc) + timedelta(days=10)).isoformat())
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeAdapter:
    def __init__(self, bundle=None, shops=None, fail=None):
        self.bundle = bundle or auth()
        self.shops = shops if shops is not None else [{"shop_id": "cj-shop-a", "name": "Same name", "platform": "API", "status": 1}]
        self.fail = fail
        self.calls = []
        self.settings_account = self.bundle.open_id

    def authenticate(self, api_key):
        self.calls.append("authenticate")
        if self.fail:
            raise self.fail
        return self.bundle

    def set_credentials(self, bundle, *, account_ref):
        self.calls.append("set_credentials")
        self.used_account_ref = account_ref
        self.used_access_token = bundle.access_token

    def get_settings(self):
        self.calls.append("get_settings")
        if self.fail:
            raise self.fail
        return {"account_id": self.settings_account, "is_sandbox": 1}

    def get_shops(self):
        self.calls.append("get_shops")
        return self.shops

    def refresh_authentication(self, refresh_token):
        self.calls.append("refresh")
        if self.fail:
            raise self.fail
        assert refresh_token == SECRETS["refresh_token"]
        return self.bundle


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "suppliers.sqlite"))
    monkeypatch.setattr(db, "IS_POSTGRES", False)
    monkeypatch.setenv("BUSINESS_OS_SUPPLIERS_CJ", "on")
    monkeypatch.setenv(vault.KEYRING_ENV, "test:" + "13" * 32)
    monkeypatch.setenv(vault.ACTIVE_KEY_ENV, "test")
    monkeypatch.setenv(vault.INDEX_KEY_ENV, "24" * 32)
    business_schema.ensure_schema()
    store_schema.ensure_schema()
    schema.ensure_schema()
    conn = db.connect()
    for suffix, owner in (("a", "100"), ("b", "200")):
        conn.execute("INSERT INTO business_os_business (business_id, owner_user_id, display_name, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?)", ("biz-" + suffix, owner, "Same Name", "now", "now"))
        conn.execute("INSERT INTO business_os_store_storefront (storefront_id, business_id, name, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?)", ("store-" + suffix, "biz-" + suffix, "Same Name", "now", "now"))
    for actor, role in (("300", "viewer"), ("400", "manager")):
        conn.execute("INSERT INTO business_os_business_members (member_id, business_id, user_id, role, status, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (actor, "biz-a", actor, role, "active", "now", "now"))
    conn.commit()
    conn.close()


def connect(adapter=None, actor="100"):
    return svc.connect_cj("biz-a", "store-a", actor, SECRETS["api_key"], "cj-shop-a", adapter=adapter or FakeAdapter())


def expire(connection_id):
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_connections SET access_expires_at=? WHERE id=?",
                 ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), connection_id))
    conn.commit()
    conn.close()


def test_connect_verifies_settings_and_explicit_shop_before_secure_store(caplog):
    adapter = FakeAdapter()
    result = connect(adapter)
    assert result["status"] == "CONNECTED" and result["merchant_id"] == "100"
    assert result["external_shop_id"] == "cj-shop-a"
    assert adapter.calls == ["authenticate", "set_credentials", "get_settings", "get_shops"]
    assert result["credential_present"] and not result["production_fulfillment_enabled"]
    assert result["external_account_id"] != SECRETS["open_id"]
    assert all(value not in json.dumps(result) and value not in caplog.text for value in SECRETS.values())
    conn = db.connect()
    for table in ("business_os_supplier_connections", "business_os_supplier_credential_vault", "business_os_store_audit"):
        dump = json.dumps([dict(row) for row in conn.execute("SELECT * FROM " + table)])
        assert all(value not in dump for value in SECRETS.values())
    conn.close()


@pytest.mark.parametrize("business,store,actor", [("biz-a", "store-a", "200"), ("biz-a", "store-b", "100"),
                                                    ("biz-b", "store-a", "200"), ("biz-a", "store-a", None)])
def test_authorization_before_provider_or_secret_lookup(business, store, actor):
    adapter = FakeAdapter()
    with pytest.raises(svc.SupplierConnectionError):
        svc.connect_cj(business, store, actor, SECRETS["api_key"], "cj-shop-a", adapter=adapter)
    assert adapter.calls == []


def test_viewer_cannot_connect_manager_derives_owner_not_actor():
    denied = FakeAdapter()
    with pytest.raises(svc.SupplierConnectionError) as failure:
        connect(denied, actor="300")
    assert failure.value.http_status == 403 and denied.calls == []
    assert connect(actor="400")["merchant_id"] == "100"


def test_scope_cannot_read_use_token_or_invoke_provider_for_other_merchant():
    row = connect()
    for business, store, actor in (("biz-a", "store-a", "200"), ("biz-b", "store-b", "200"),
                                  ("biz-a", "store-b", "100")):
        with pytest.raises(svc.SupplierConnectionError):
            svc.get_connection(row["id"], business, store, actor)
        fake = FakeAdapter()
        with pytest.raises(svc.SupplierConnectionError):
            svc.adapter_for(business, store, actor, row["id"], adapter=fake)
        assert fake.calls == []
    with pytest.raises(svc.SupplierConnectionError):
        svc.worker_connection(row["id"], "biz-b", "store-b")


def test_cross_shop_binding_and_identity_conflict_rejected():
    wrong_shop = FakeAdapter(shops=[{"shop_id": "cj-shop-b", "name": "Same name", "platform": "API"}])
    with pytest.raises(svc.SupplierConnectionError, match="belonging"):
        connect(wrong_shop)
    wrong_identity = FakeAdapter()
    wrong_identity.settings_account = "other-account"
    with pytest.raises(svc.SupplierConnectionError, match="verification"):
        connect(wrong_identity)
    assert svc.list_connections("biz-a", "store-a", "100") == []


def test_same_cj_account_cannot_be_claimed_by_another_merchant():
    connect()
    with pytest.raises(svc.SupplierConnectionError) as failure:
        svc.connect_cj("biz-b", "store-b", "200", "fixture-other-key", "cj-shop-a", adapter=FakeAdapter())
    assert failure.value.code == "account_ownership_conflict"


def test_discover_shops_returns_allowlist_not_credentials_and_stores_nothing():
    result = svc.discover_shops("biz-a", "store-a", "100", SECRETS["api_key"], adapter=FakeAdapter())
    assert result["requires_explicit_shop_selection"]
    assert result["shops"][0]["shop_id"] == "cj-shop-a"
    assert all(value not in json.dumps(result) for value in SECRETS.values())
    assert svc.list_connections("biz-a", "store-a", "100") == []


def test_invalid_vault_fails_before_using_api_key(monkeypatch):
    monkeypatch.delenv(vault.KEYRING_ENV)
    fake = FakeAdapter()
    with pytest.raises(vault.VaultError):
        connect(fake)
    assert fake.calls == []


@pytest.mark.parametrize("metadata", [dict(access_expires_at="not-a-date"), dict(refresh_expires_at=""),
    dict(access_expires_at="2000-01-01T00:00:00Z"), dict(open_id=""), dict(access_token="")])
def test_invalid_or_expired_auth_never_connected(metadata):
    fake = FakeAdapter(bundle=auth(**metadata))
    with pytest.raises(svc.SupplierConnectionError):
        connect(fake)
    assert fake.calls == ["authenticate"]
    assert svc.list_connections("biz-a", "store-a", "100") == []


def test_refresh_uses_returned_expiry_and_same_account():
    row = connect()
    expire(row["id"])
    fake = FakeAdapter(bundle=auth(access_token="fixture-new-access"))
    assert svc.adapter_for("biz-a", "store-a", "100", row["id"], adapter=fake) is fake
    assert fake.calls.count("refresh") == 1
    assert fake.used_access_token == "fixture-new-access"
    stored = svc.worker_connection(row["id"], "biz-a", "store-a")
    assert stored["credentials"]["access_token"] == "fixture-new-access"
    assert svc._date(stored["connection"]["access_expires_at"]) == svc._date(fake.bundle.access_expires_at)
    assert svc.get_connection(row["id"], "biz-a", "store-a", "100")["status"] == "CONNECTED"


def test_refresh_cannot_swap_account():
    row = connect()
    expire(row["id"])
    fake = FakeAdapter(bundle=auth(open_id="other-tenant-openid"))
    with pytest.raises(svc.SupplierConnectionError):
        svc.worker_adapter(row["id"], "biz-a", "store-a", adapter=fake)
    stored = svc.worker_connection(row["id"], "biz-a", "store-a")
    assert stored["credentials"]["open_id"] == SECRETS["open_id"]
    assert stored["connection"]["status"] == "REAUTH_REQUIRED"


def test_refresh_missing_openid_retains_only_prior_verified_identity():
    row = connect()
    expire(row["id"])
    fake = FakeAdapter(bundle=auth(open_id=None, access_token="fixture-omitted-id-refresh"))
    fake.settings_account = SECRETS["open_id"]
    result = svc.health_connection(row["id"], "biz-a", "store-a", "100", adapter=fake)
    assert result["status"] == "CONNECTED"
    stored = svc.worker_connection(row["id"], "biz-a", "store-a")
    assert stored["credentials"]["open_id"] == SECRETS["open_id"]
    assert stored["credentials"]["access_token"] == "fixture-omitted-id-refresh"


def test_refresh_failure_requires_reauth_and_no_silent_retry():
    row = connect()
    expire(row["id"])
    failed = FakeAdapter(fail=svc.SupplierConnectionError("unavailable", 502, "provider_unavailable"))
    result = svc.health_connection(row["id"], "biz-a", "store-a", "100", adapter=failed)
    assert result["status"] == "REAUTH_REQUIRED"
    assert failed.calls.count("refresh") == 1
    another = FakeAdapter()
    with pytest.raises(svc.SupplierConnectionError) as failure:
        svc.worker_adapter(row["id"], "biz-a", "store-a", adapter=another)
    assert failure.value.code == "reauth_required" and another.calls == []


@pytest.mark.parametrize("status", [0, 2, None, True])
def test_inactive_or_unknown_cj_shop_cannot_be_bound(status):
    fake = FakeAdapter(shops=[{"shop_id": "cj-shop-a", "name": "Same name", "platform": "API", "status": status}])
    with pytest.raises(svc.SupplierConnectionError) as failure:
        connect(fake)
    assert failure.value.code == "shop_not_authorized"


@pytest.mark.parametrize("secret", list(SECRETS.values()))
def test_malicious_provider_shop_name_cannot_echo_secrets_to_response(secret):
    fake = FakeAdapter(shops=[{"shop_id": "cj-shop-a", "name": "leaked=" + secret, "platform": "API", "status": 1}])
    with pytest.raises(svc.SupplierConnectionError) as failure:
        svc.discover_shops("biz-a", "store-a", "100", SECRETS["api_key"], adapter=fake)
    assert failure.value.code == "unsafe_provider_response"
    assert secret not in str(failure.value)


def test_index_key_is_required_before_authentication(monkeypatch):
    monkeypatch.delenv(vault.INDEX_KEY_ENV)
    fake = FakeAdapter()
    with pytest.raises(vault.VaultError):
        connect(fake)
    assert fake.calls == []


def test_points_metadata_is_persisted_and_allowlisted():
    fake = FakeAdapter()
    fake.points_info = {"remaining": 500, "usedToday": 49500, "total": 50000,
                        "openId": SECRETS["open_id"]}
    row = connect(fake)
    assert row["quota_state"] == "CRITICAL"
    assert row["points_info"] == {"remaining": 500, "usedToday": 49500, "total": 50000}
    stored = svc.get_connection(row["id"], "biz-a", "store-a", "100")
    assert stored["points_info"] == row["points_info"]


def test_identity_fingerprint_is_not_unkeyed_and_survives_cipher_key_rotation(monkeypatch):
    import hashlib
    before = svc.account_reference(SECRETS["open_id"])
    assert before != "cja_" + hashlib.sha256(("pulsesoc:cj:account:v1:" + SECRETS["open_id"]).encode()).hexdigest()
    monkeypatch.setenv(vault.KEYRING_ENV, "next:" + "37" * 32)
    monkeypatch.setenv(vault.ACTIVE_KEY_ENV, "next")
    assert svc.account_reference(SECRETS["open_id"]) == before


@pytest.mark.parametrize("code,status", [("rate_limited", "RATE_LIMITED"), ("api_suspended", "REACTIVATION_REQUIRED"),
    ("auth_expired", "REAUTH_REQUIRED"), ("provider_unavailable", "PROVIDER_UNAVAILABLE")])
def test_health_reports_failures_without_secret_or_exception_text(code, status):
    row = connect()
    fake = FakeAdapter(fail=svc.SupplierConnectionError("fixed safe error", 502, code))
    result = svc.health_connection(row["id"], "biz-a", "store-a", "100", adapter=fake)
    assert result["status"] == status
    assert all(value not in json.dumps(result) for value in SECRETS.values())
    if status == "REACTIVATION_REQUIRED":
        assert "reactivation" in result["message"] and "reconnect" not in result["message"].lower()


def test_expired_credentials_are_not_healthy_merely_because_stored():
    row = connect()
    expire(row["id"])
    assert svc.get_connection(row["id"], "biz-a", "store-a", "100")["status"] == "AUTH_EXPIRED"
    assert not svc.admin_health()["provider_reachable_recently"]


def test_missing_vault_or_ciphertext_tamper_makes_health_unavailable():
    row = connect()
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_credential_vault SET ciphertext='not-a-ciphertext'")
    conn.commit()
    conn.close()
    result = svc.health_connection(row["id"], "biz-a", "store-a", "100", adapter=FakeAdapter())
    assert result["status"] == "PROVIDER_UNAVAILABLE"


def test_durable_singleflight_refresh_has_one_provider_call():
    row = connect()
    expire(row["id"])
    started, release = threading.Event(), threading.Event()

    class BlockingAdapter(FakeAdapter):
        def refresh_authentication(self, refresh_token):
            started.set()
            assert release.wait(4)
            return super().refresh_authentication(refresh_token)

    first, second = BlockingAdapter(), FakeAdapter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(svc.worker_adapter, row["id"], "biz-a", "store-a", adapter=first)
        try:
            assert started.wait(3)
            with pytest.raises(svc.SupplierConnectionError) as failure:
                svc.worker_adapter(row["id"], "biz-a", "store-a", adapter=second)
            assert failure.value.code == "refresh_in_progress"
            assert "refresh" not in second.calls
        finally:
            release.set()
        assert future.result(timeout=5) is first
    assert first.calls.count("refresh") == 1


def test_explicit_reauth_preserves_binding_and_cannot_silently_change_account():
    old = connect()
    fresh = connect(FakeAdapter(bundle=auth(access_token="fixture-rotated-access")))
    assert old["id"] == fresh["id"]
    assert svc.worker_connection(old["id"], "biz-a", "store-a")["credentials"]["access_token"] == "fixture-rotated-access"
    with pytest.raises(svc.SupplierConnectionError) as failure:
        connect(FakeAdapter(bundle=auth(open_id="fixture-another-account")))
    assert failure.value.code == "connection_binding_conflict"


def test_business_transfer_does_not_transfer_existing_secret_access():
    row = connect()
    conn = db.connect()
    conn.execute("UPDATE business_os_business SET owner_user_id='200' WHERE business_id='biz-a'")
    conn.commit()
    conn.close()
    for actor in ("100", "200"):
        with pytest.raises(svc.SupplierConnectionError):
            svc.get_connection(row["id"], "biz-a", "store-a", actor)
    with pytest.raises(svc.SupplierConnectionError):
        svc.worker_connection(row["id"], "biz-a", "store-a")


def test_account_hold_blocks_provider_and_secret_access():
    row = connect()
    fake = FakeAdapter()
    with pytest.raises(svc.SupplierConnectionError):
        svc.adapter_for("biz-a", "store-a", "100", row["id"], context={"account_status": "suspended"}, adapter=fake)
    assert fake.calls == []


def test_default_flag_blocks_connect_before_provider(monkeypatch):
    monkeypatch.delenv("BUSINESS_OS_SUPPLIERS_CJ")
    fake = FakeAdapter()
    with pytest.raises(svc.SupplierConnectionError) as failure:
        connect(fake)
    assert failure.value.code == "disabled" and fake.calls == []


def test_admin_health_aggregate_has_no_tenant_or_secret_material():
    connect()
    result = svc.admin_health()
    assert result["connections_count"] == 1 and result["states"]["CONNECTED"] == 1
    text = json.dumps(result)
    assert all(secret not in text for secret in SECRETS.values())
    assert "biz-a" not in text and "store-a" not in text and "merchant_id" not in text
    assert result["worker"]["state"] == "NOT_INITIALIZED"
    assert result["webhook_inbox"]["state"] == "NOT_INITIALIZED"


def test_record_activity_captures_post_hydration_429_points_and_successful_sync():
    row = connect()
    fake = FakeAdapter()
    fake.points_info = {"remaining": 0, "usedToday": 50000, "total": 50000}
    svc.record_activity(row["id"], "biz-a", "store-a", adapter=fake,
                        error=svc.SupplierConnectionError("safe", 429, "rate_limited"), synced=True)
    current = svc.get_connection(row["id"], "biz-a", "store-a", "100")
    assert current["status"] == "RATE_LIMITED" and current["quota_state"] == "EXHAUSTED"
    assert current["last_sync_at"] is None
    fake.points_info = {"remaining": 10000, "usedToday": 500, "total": 50000}
    svc.record_activity(row["id"], "biz-a", "store-a", adapter=fake, synced=True)
    current = svc.get_connection(row["id"], "biz-a", "store-a", "100")
    assert current["last_sync_at"] is not None and current["quota_state"] == "NORMAL"
    assert current["status"] == "RATE_LIMITED"  # a catalog success is not full health proof
    with pytest.raises(svc.SupplierConnectionError):
        svc.record_activity(row["id"], "biz-b", "store-b", adapter=fake)


def test_admin_health_worker_and_cj_only_inbox_lag():
    from services.business_os.suppliers import worker
    from services.business_os.payments import webhook_inbox
    worker.ensure_schema()
    webhook_inbox.ensure_schema()
    row = connect()
    worker.schedule(connection_id=row["id"], business_id="biz-a", store_id="store-a", kind="health", now=1000)
    webhook_inbox.enqueue_event(provider="cj", provider_event_id="fixture-event", payload={}, event_type="PRODUCT", signature_verified=True)
    webhook_inbox.enqueue_event(provider="stripe", provider_event_id="unrelated-event", payload={}, event_type="PRODUCT", signature_verified=True)
    result = svc.admin_health()
    assert result["worker"]["due_count"] == 1 and result["worker"]["state"] == "BACKLOG"
    assert result["worker"]["reconciliation_lag_seconds"] > 0 and not result["worker"]["execution_observed"]
    assert result["webhook_inbox"]["counts"]["received"] == 1
    assert result["webhook_inbox"]["oldest_pending_lag_seconds"] >= 0
