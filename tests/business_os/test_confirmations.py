"""Shared Business OS confirmation-grant security contract.

    python tests/business_os/test_confirmations.py
"""

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_confirm_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os import confirmations as grants  # noqa: E402


def setup_module(module=None):
    grants.ensure_schema()


def _expect(code, fn):
    try:
        fn()
    except grants.ConfirmationError as exc:
        assert exc.code == code, (code, exc.code)
        return
    raise AssertionError(f"expected ConfirmationError({code})")


def test_grant_is_hashed_bounded_and_single_use():
    grant = grants.mint(
        "marketplace", "user:7", "publish",
        {"product_id": "p1"}, ttl_override=5)
    assert grant["ttl_seconds"] == grants.TTL_MIN, grant
    assert grant["single_use"] is True, grant
    raw = grant["confirmation_token"]

    conn = db.connect()
    try:
        row = dict(conn.execute(
            f"SELECT * FROM {grants.TABLE} WHERE token_hash = ?",
            (grants.token_hash(raw),)).fetchone())
    finally:
        conn.close()
    assert raw not in " ".join(str(value) for value in row.values()), row
    assert row["status"] == "pending", row

    used = grants.consume(
        "marketplace", "user:7", "publish", {"product_id": "p1"}, raw)
    assert used["status"] == "consumed", used
    _expect(grants.CODE_USED, lambda: grants.consume(
        "marketplace", "user:7", "publish", {"product_id": "p1"}, raw))


def test_namespace_subject_tool_and_payload_are_all_bound():
    raw = grants.mint(
        "marketplace", "user:7", "publish",
        {"product_id": "p2"})["confirmation_token"]
    variants = (
        ("advertising", "user:7", "publish", {"product_id": "p2"}),
        ("marketplace", "user:8", "publish", {"product_id": "p2"}),
        ("marketplace", "user:7", "pause", {"product_id": "p2"}),
        ("marketplace", "user:7", "publish", {"product_id": "p3"}),
    )
    for namespace, subject, tool, payload in variants:
        _expect(grants.CODE_MISMATCH, lambda n=namespace, s=subject, t=tool, p=payload:
                grants.consume(n, s, t, p, raw))
    grants.consume(
        "marketplace", "user:7", "publish", {"product_id": "p2"}, raw)


def test_grant_can_be_revoked_only_by_its_subject():
    raw = grants.mint(
        "advertising", "user:11", "activate",
        {"campaign_id": "c1"})["confirmation_token"]
    assert grants.revoke("advertising", "user:12", raw)["revoked"] is False
    assert grants.revoke("marketplace", "user:11", raw)["revoked"] is False
    assert grants.revoke("advertising", "user:11", raw)["revoked"] is True
    _expect(grants.CODE_REVOKED, lambda: grants.consume(
        "advertising", "user:11", "activate", {"campaign_id": "c1"}, raw))


def test_elapsed_grant_is_refused_and_housekeeping_marks_it_expired():
    raw = grants.mint(
        "marketplace", "user:9", "pay",
        {"order_id": "o1"})["confirmation_token"]
    conn = db.connect()
    try:
        conn.execute(
            f"UPDATE {grants.TABLE} SET expires_at = ? WHERE token_hash = ?",
            ("2000-01-01T00:00:00.000000Z", grants.token_hash(raw)))
        conn.commit()
    finally:
        conn.close()
    _expect(grants.CODE_EXPIRED, lambda: grants.consume(
        "marketplace", "user:9", "pay", {"order_id": "o1"}, raw))
    assert grants.expire_stale() >= 1
    described = grants.describe("marketplace", "user:9", raw)
    assert described and described["status"] == "expired", described


def test_simultaneous_redemption_has_exactly_one_winner():
    raw = grants.mint(
        "marketplace", "user:20", "publish",
        {"product_id": "race"})["confirmation_token"]

    def consume_once():
        try:
            grants.consume(
                "marketplace", "user:20", "publish",
                {"product_id": "race"}, raw)
            return "used"
        except grants.ConfirmationError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume_once(), range(2)))
    assert sorted(results) == sorted(["used", grants.CODE_USED]), results


def _run_standalone():
    setup_module()
    tests = [
        test_grant_is_hashed_bounded_and_single_use,
        test_namespace_subject_tool_and_payload_are_all_bound,
        test_grant_can_be_revoked_only_by_its_subject,
        test_elapsed_grant_is_refused_and_housekeeping_marks_it_expired,
        test_simultaneous_redemption_has_exactly_one_winner,
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if _run_standalone() else 1)
