"""Durable SQLite quota decisions shared across controller instances/processes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
import threading

import pytest

from services import db
from services.business_os.suppliers.errors import SupplierError
from services.business_os.suppliers.quota import DurableCJQuota, budget_state, ensure_schema


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds=1.01):
        self.now += seconds


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "quota.sqlite"))
    monkeypatch.setattr(db, "IS_POSTGRES", False)
    monkeypatch.delenv("CJ_EGRESS_GROUP", raising=False)
    ensure_schema()


@pytest.fixture
def controller():
    clock = Clock()
    return DurableCJQuota(egress_group="test-egress", reserve=1000, clock=clock), clock


def observe(quota, remaining=5000, total=50000, used=100):
    quota.observe("account-a", {"remaining": remaining, "usedToday": used, "total": total})


@pytest.mark.parametrize("remaining,total,state", [(None, None, "UNKNOWN"), (0, 50000, "EXHAUSTED"),
    (1000, 50000, "CRITICAL"), (1001, 50000, "CONSTRAINED"), (5000, 50000, "CONSTRAINED"),
    (5001, 50000, "NORMAL")])
def test_budget_states(remaining, total, state):
    assert budget_state(remaining, total) == state


def test_unknown_points_admit_only_zero_cost_bootstrap(controller):
    quota, clock = controller
    with pytest.raises(SupplierError) as failure:
        quota.reserve_request("account-a", cost=50)
    assert failure.value.code == "QUOTA_UNKNOWN"
    quota.reserve_request("account-a", cost=0, authentication=True)
    clock.advance()
    assert quota.snapshot("account-a")["state"] == "UNKNOWN"
    assert quota.snapshot("account-a")["remaining"] is None


def test_known_remaining_debits_atomically_and_preserves_fulfillment_reserve(controller):
    quota, clock = controller
    observe(quota, 1049)
    with pytest.raises(SupplierError) as failure:
        quota.reserve_request("account-a", cost=50)
    assert failure.value.code == "POINT_BUDGET_RESERVED"
    quota.reserve_request("account-a", cost=50, critical=True)
    assert quota.snapshot("account-a")["remaining"] == 999
    clock.advance()
    with pytest.raises(SupplierError):
        quota.reserve_request("account-a", cost=1000, critical=True)
    assert quota.snapshot("account-a")["remaining"] == 999


def test_two_instances_share_account_qps_and_egress_ip_limit(controller):
    first, clock = controller
    second = DurableCJQuota(egress_group="test-egress", clock=clock)
    first.reserve_request("account-a", cost=0)
    with pytest.raises(SupplierError) as same_account:
        second.reserve_request("account-a", cost=0)
    assert same_account.value.retry_after == pytest.approx(1.0)
    with pytest.raises(SupplierError) as same_ip:
        second.reserve_request("account-b", cost=0)
    assert same_ip.value.retry_after == pytest.approx(.1)
    clock.advance(.101)
    second.reserve_request("account-b", cost=0)
    clock.advance(.91)
    second.reserve_request("account-a", cost=0)


def test_ip_allows_only_three_verified_or_pending_cj_accounts(controller):
    quota, _ = controller
    for account in ("account-a", "account-b", "account-c"):
        quota.snapshot(account)
    with pytest.raises(SupplierError) as failure:
        quota.snapshot("account-d")
    assert failure.value.code == "EGRESS_ACCOUNT_CAPACITY"
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM business_os_cj_account_quota").fetchone()[0] == 3
    conn.close()


def test_pending_identity_promotion_does_not_consume_an_extra_account_slot(controller):
    quota, _ = controller
    quota.snapshot("pending-api-key")
    quota.snapshot("account-b")
    quota.snapshot("account-c")
    quota.rebind_account("pending-api-key", "verified-account-a")
    assert quota.snapshot("verified-account-a")["state"] == "UNKNOWN"
    conn = db.connect()
    rows = conn.execute("SELECT account_ref FROM business_os_cj_account_quota").fetchall()
    assert sorted(row[0] for row in rows) == ["account-b", "account-c", "verified-account-a"]
    conn.close()


def test_multiple_keys_for_same_account_merge_conservatively(controller):
    quota, _ = controller
    quota.observe("pending-api-key", {"remaining": 9000, "usedToday": 100, "total": 50000})
    quota.observe("verified-account", {"remaining": 8000, "usedToday": 200, "total": 50000})
    quota.penalize("pending-api-key", "300")
    quota.rebind_account("pending-api-key", "verified-account")
    snap = quota.snapshot("verified-account")
    assert snap["remaining"] is None and snap["retry_after"] == 300
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM business_os_cj_account_quota").fetchone()[0] == 1
    conn.close()


def test_documented_qps_ceiling_and_auth_pacing_are_independent(controller):
    quota, clock = controller
    quota.snapshot("account-a")
    conn = db.connect()
    conn.execute("UPDATE business_os_cj_account_quota SET qps=99 WHERE account_ref='account-a'")
    conn.commit()
    conn.close()
    quota.reserve_request("account-a")
    clock.advance(.101)
    with pytest.raises(SupplierError) as failure:
        quota.reserve_request("account-a")
    assert failure.value.retry_after == pytest.approx(1 / 6 - .101)
    clock.advance(1)
    quota.reserve_request("account-a", authentication=True)
    clock.advance(.2)
    with pytest.raises(SupplierError) as failure:
        quota.reserve_request("account-a", authentication=True)
    assert failure.value.retry_after == pytest.approx(.8)


def test_parallel_controllers_admit_only_one_same_account_request(controller):
    _, clock = controller
    barrier = threading.Barrier(2)

    def reserve():
        controller = DurableCJQuota(egress_group="test-egress", clock=clock)
        barrier.wait(timeout=3)
        try:
            controller.reserve_request("account-a")
            return "admitted"
        except SupplierError as failure:
            return failure.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        results = sorted(future.result(timeout=5) for future in futures)
    assert results == ["RATE_LIMITED", "admitted"]


def test_provider_429_blocks_complete_shared_egress_and_recovers_after_deadline(controller):
    quota, clock = controller
    quota.snapshot("account-b")
    assert quota.penalize("account-a", "90") == 90
    for account in ("account-a", "account-b"):
        with pytest.raises(SupplierError) as failure:
            quota.reserve_request(account)
        assert failure.value.code == "RATE_LIMITED" and failure.value.retry_after == 90
    clock.advance(90.01)
    quota.reserve_request("account-b")


def test_retry_after_valid_large_value_is_never_shortened(controller):
    quota, clock = controller
    assert quota.penalize("account-a", "7200") == 7200
    clock.advance(100)
    assert quota.penalize("account-a", "2") == 7100
    with pytest.raises(SupplierError) as failure:
        quota.reserve_request("account-a")
    assert failure.value.retry_after == 7100


def test_retry_after_http_date(controller):
    quota, clock = controller
    future = format_datetime(datetime.fromtimestamp(clock.now + 600, timezone.utc), usegmt=True)
    assert quota.penalize("account-a", future) == 600


@pytest.mark.parametrize("invalid", [None, "invalid", "-100", "0", "nan", "inf"])
def test_missing_or_invalid_retry_after_uses_bounded_exponential_backoff(controller, invalid):
    quota, _ = controller
    assert quota.penalize("account-a", invalid) == 30
    assert quota.penalize("account-a", invalid) == 60
    for _ in range(12):
        value = quota.penalize("account-a", invalid)
    assert value == 3600


def test_stale_response_cannot_restore_points_reserved_by_new_request(controller):
    quota, clock = controller
    observe(quota)
    old = quota.reserve_request("account-a", cost=50)
    clock.advance()
    newest = quota.reserve_request("account-a", cost=10)
    assert newest > old and quota.snapshot("account-a")["remaining"] == 4940
    quota.observe("account-a", {"remaining": 4990, "usedToday": 10, "total": 50000}, sequence=old)
    assert quota.snapshot("account-a")["remaining"] == 4940


def test_new_valid_observation_can_prove_refill_but_wallclock_does_not(controller):
    quota, clock = controller
    observe(quota, remaining=0)
    clock.advance(86400)
    assert quota.snapshot("account-a")["state"] == "EXHAUSTED"
    with pytest.raises(SupplierError):
        quota.reserve_request("account-a", cost=10, critical=True)
    observe(quota, remaining=10000)
    assert quota.snapshot("account-a")["state"] == "NORMAL"
    quota.reserve_request("account-a", cost=50)


@pytest.mark.parametrize("points", [{"remaining": True, "usedToday": 0, "total": 50000},
    {"remaining": 50001, "usedToday": 0, "total": 50000}, {"remaining": -1, "usedToday": 0, "total": 50000},
    {"remaining": 1000}, None])
def test_malformed_quota_observation_never_fabricates_capacity(controller, points):
    quota, _ = controller
    quota.observe("account-a", points)
    assert quota.snapshot("account-a")["remaining"] is None


@pytest.mark.parametrize("cost", [-1, 1001, True, 1.5, "50"])
def test_invalid_point_cost_rejected(controller, cost):
    quota, _ = controller
    with pytest.raises(SupplierError) as failure:
        quota.reserve_request("account-a", cost=cost)
    assert failure.value.code == "INVALID_POINT_COST"


def test_missing_fixed_egress_config_refuses_admission():
    with pytest.raises(SupplierError) as failure:
        DurableCJQuota().reserve_request("account-a")
    assert failure.value.code == "EGRESS_CONFIGURATION_REQUIRED"
