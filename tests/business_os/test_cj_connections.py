"""Real SQLite tenancy/vault tests against synthetic (never live) CJ responses."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading
import time
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
    def __init__(self, bundle=None, shops=None, fail=None, shops_error=None):
        self.bundle = bundle or auth()
        self.shops = shops if shops is not None else [{"shop_id": "cj-shop-a", "name": "Same name", "platform": "API", "status": 1}]
        self.fail = fail
        # Separate from `fail` on purpose: the interesting cases are the ones
        # where authentication and identity succeed and only the shop list does
        # not, which is what a real CJ account without a storefront does.
        self.shops_error = shops_error
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
        if self.shops_error:
            raise self.shops_error
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


def test_a_connection_response_carries_only_its_allowlisted_keys():
    """The serializer's allowlist is a control, so something must observe it.

    `_public` builds its output by naming keys rather than by copying the row,
    and the reason is stated in a comment there: a column added to
    `business_os_supplier_connections` later must not reach a response just by
    existing. That is a promise about code nobody has written yet, which makes
    it precisely the kind of promise that rots unnoticed -- the test above
    checks that today's *secret values* do not leak, and it would keep passing
    if the allowlist were replaced by `dict(row)` tomorrow, because today's
    secrets live encrypted in the vault table rather than in this row.

    So this asserts the shape instead of the contents. It fails on any widening,
    including a widening that leaks nothing, and that is the intended cost: the
    allowlist is only worth having if changing it requires saying so out loud.
    Adding a field here is a one-line edit; adding one by accident is not.
    """
    result = connect()
    assert set(result) == {
        "id", "merchant_id", "business_id", "store_id", "provider", "connection_type",
        "external_account_id", "external_shop_id", "status", "access_expires_at",
        "refresh_expires_at", "quota_state", "last_verified_at", "last_sync_at",
        "created_at", "updated_at",
        # Derived, never columns: the first three are computed, and `points_info`
        # is itself a nested allowlist over `quota_json` rather than that column.
        "credential_present", "environment", "production_fulfillment_enabled", "points_info",
    }
    # The allowlist is only doing work if the row it filters is genuinely wider.
    # `credential_reference` is the vault lookup key and `refresh_lease_token` a
    # concurrency lease -- neither is a secret on its own, and neither has any
    # business being handed to a client.
    conn = db.connect()
    row = dict(conn.execute("SELECT * FROM business_os_supplier_connections WHERE id=?",
                            (result["id"],)).fetchone())
    conn.close()
    assert {"credential_reference", "refresh_lease_token", "quota_json", "version"} <= set(row)
    assert set(row) - set(result), "row is no wider than the response; the allowlist filters nothing"
    assert not {"credential_reference", "refresh_lease_token", "quota_json"} & set(result)


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


def test_a_cj_account_that_owns_no_storefront_can_still_connect():
    """The case that made a real merchant unconnectable.

    A CJ "shop" is an external storefront (Shopify, Woo) authorized inside the
    merchant's CJ account. Selling through PulseSoc means PulseSoc *is* the
    storefront, so the shop list is legitimately empty -- and importing products
    never needed one. The absence is recorded as "" rather than NULL because
    every downstream binding check compares this column as a string.
    """
    adapter = FakeAdapter(shops=[])
    result = svc.connect_cj("biz-a", "store-a", "100", SECRETS["api_key"], None, adapter=adapter)
    assert result["status"] == "CONNECTED"
    assert result["external_shop_id"] == ""
    # Identity was still proven. That is the check that keeps one merchant's
    # credential from answering for another's, and it is not the optional half.
    assert adapter.calls == ["authenticate", "set_credentials", "get_settings", "get_shops"]


def test_a_shop_list_we_cannot_read_does_not_block_a_connection_that_selects_none():
    """CJ answers `shop/getShops` for such an account with an undocumented code.

    We refuse to interpret business codes CJ does not publish -- correctly -- so
    the call raises. With no shop selected there is nothing that answer would
    have authorized, so it must not take the whole connection down with it.
    """
    adapter = FakeAdapter(shops_error=svc.SupplierError("SUPPLIER_REJECTED", http_status=422))
    result = svc.connect_cj("biz-a", "store-a", "100", SECRETS["api_key"], adapter=adapter)
    assert result["status"] == "CONNECTED" and result["external_shop_id"] == ""


def test_a_shop_list_we_cannot_read_is_fatal_when_a_shop_was_selected():
    """The security half. "We could not ask" must never mean "you are allowed".

    This is the mutation that matters: if the survivable case were widened to
    cover a selected shop, an unreadable list would silently bind a shop that
    was never proved to belong to this credential.
    """
    adapter = FakeAdapter(shops_error=svc.SupplierError("SUPPLIER_REJECTED", http_status=422))
    with pytest.raises(svc.SupplierError):
        connect(adapter)


def test_selecting_a_shop_is_still_checked_against_an_empty_live_list():
    adapter = FakeAdapter(shops=[])
    with pytest.raises(svc.SupplierConnectionError) as failure:
        connect(adapter)
    assert failure.value.code == "shop_not_authorized"


def test_a_shopless_connection_cannot_quietly_acquire_a_shop_or_lose_one():
    """Rebinding is still refused in both directions.

    Making the shop optional widened who may connect, not what an existing
    connection may become: a reauthentication rotates credentials underneath the
    same account and shop, and changing either is a different operation with
    persisted fulfillment intents already pointing at the old binding.
    """
    svc.connect_cj("biz-a", "store-a", "100", SECRETS["api_key"], None, adapter=FakeAdapter(shops=[]))
    with pytest.raises(svc.SupplierConnectionError) as failure:
        connect(FakeAdapter())
    assert failure.value.code == "connection_binding_conflict"


def test_discovery_reports_no_shops_rather_than_failing_when_cj_refuses():
    adapter = FakeAdapter(shops_error=svc.SupplierError("SUPPLIER_REJECTED", http_status=422))
    result = svc.discover_shops("biz-a", "store-a", "100", SECRETS["api_key"], adapter=adapter)
    assert result["shops"] == [] and result["requires_explicit_shop_selection"] is True


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


def _link_order(connection_id, *, sandbox, created_at, order_id, ref):
    """Write an intent CJ acknowledged, with the sandbox flag as actually sent."""
    from services.business_os.suppliers import fulfillment
    conn = db.connect()
    fulfillment.ensure_schema(conn)
    conn.execute(
        "INSERT INTO business_os_supplier_intents (id,connection_id,business_id,store_id,merchant_id,"
        "order_id,external_account_id,external_shop_id,idempotency_key,external_order_ref,"
        "snapshot_json,snapshot_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ref, connection_id, "biz-a", "store-a", "100", order_id, "acct", "shop", ref, ref,
         json.dumps({"isSandbox": sandbox}), "hash-" + ref, created_at))
    conn.execute("INSERT INTO business_os_supplier_outbox (intent_id,state,provider_order_id,available_at,updated_at) "
                 "VALUES (?,'LINKED',?,0,0)", (ref, "cj-" + ref))
    conn.commit()
    conn.close()


def test_a_sandbox_only_connection_is_on_cj_s_inactivity_clock_from_day_one():
    """Sandbox orders do not reset CJ's thirty-day clock, so this must not pretend they do.

    Every payload this deployment sends carries isSandbox=1 -- `assert_sandbox`
    permits nothing else -- so the real-order count is structurally zero and the
    connection is counting down from the moment it exists. A merchant finding
    that out from CJ instead of from us is the failure this reports around.
    """
    row = connect()
    _link_order(row["id"], sandbox=1, created_at=time.time(), order_id="ord-1", ref="intent-sandbox")
    forecast = svc.inactivity_forecast(row["id"], "biz-a", "store-a", "100")
    assert forecast["real_orders"] == 0
    assert forecast["last_real_order_at"] is None
    assert forecast["reference"] == "connection_created"
    assert forecast["state"] == "OK"
    assert forecast["sandbox_orders_count_toward_this"] is False
    # A connection made moments ago has very nearly the full window left.
    assert 29.9 < forecast["days_until_disable_estimate"] <= 30


def test_the_countdown_admits_it_may_be_late_until_a_real_order_anchors_it():
    """`estimate_may_be_late` is the load-bearing field, not the day count.

    Counting from connection creation assumes CJ's clock started when ours did.
    If the merchant's CJ account was already idle when they connected, CJ's
    started earlier and CJ disables before the day we name -- an optimistic
    error, which is the dangerous direction for a deadline. The flag says so,
    and it clears only when we are counting from an order we actually sent.
    """
    row = connect()
    forecast = svc.inactivity_forecast(row["id"], "biz-a", "store-a", "100")
    assert forecast["estimate_may_be_late"] is True

    real = time.time() - 9 * 86400
    _link_order(row["id"], sandbox=0, created_at=real, order_id="ord-2", ref="intent-real")
    forecast = svc.inactivity_forecast(row["id"], "biz-a", "store-a", "100")
    assert forecast["real_orders"] == 1
    assert forecast["reference"] == "last_real_order"
    assert forecast["estimate_may_be_late"] is False
    assert 8.9 < forecast["days_since_reference"] < 9.1
    assert forecast["state"] == "WARNING"  # past seven days, short of thirty
    assert 20.9 < forecast["days_until_disable_estimate"] < 21.1


def test_an_unreadable_order_snapshot_does_not_silence_the_warning():
    """A snapshot we cannot parse is not evidence of a real order.

    Counting it as real would reset the clock on the strength of a record we
    could not read, which is exactly backwards: the whole function exists to
    raise a warning, so an unreadable row must not be able to suppress one.
    """
    row = connect()
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_connections SET created_at=? WHERE id=?",
                 ((datetime.now(timezone.utc) - timedelta(days=40)).isoformat(), row["id"]))
    conn.commit()
    conn.close()
    _link_order(row["id"], sandbox=0, created_at=time.time(), order_id="ord-3", ref="intent-broken")
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_intents SET snapshot_json='{not json' WHERE id=?", ("intent-broken",))
    conn.commit()
    conn.close()
    forecast = svc.inactivity_forecast(row["id"], "biz-a", "store-a", "100")
    assert forecast["real_orders"] == 0
    assert forecast["state"] == "DISABLE_EXPECTED"
    assert forecast["days_until_disable_estimate"] == 0  # floored, never negative


@pytest.mark.parametrize("business,store,actor", [("biz-a", "store-a", "200"), ("biz-b", "store-a", "100"),
                                                  ("biz-a", "store-a", None)])
def test_the_inactivity_forecast_is_behind_the_same_tenant_gate(business, store, actor):
    row = connect()
    with pytest.raises(svc.SupplierConnectionError):
        svc.inactivity_forecast(row["id"], business, store, actor)
