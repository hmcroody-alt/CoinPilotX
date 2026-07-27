"""Shared Business OS assistant result contract.

    python tests/business_os/test_results.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os import results  # noqa: E402


def test_read_result_is_successful():
    out = results.read_result("report", {"total": 4})
    assert out == {
        "ok": True,
        "tool": "report",
        "write": False,
        "result": {"total": 4},
    }
    status, body = results.envelope(out)
    assert status == 200, (status, body)
    assert body["ok"] is True and body["result"] == out, body


def test_verified_write_is_successful():
    out = results.write_result("publish", True, {"status": "published"}, {"id": "p1"})
    assert out["ok"] is True, out
    assert out["write_applied"] is True, out
    assert out["verified"] is True, out
    status, body = results.envelope(out)
    assert status == 200, (status, body)
    assert body["ok"] is True, body


def test_unverified_write_is_not_successful_or_retry_safe():
    out = results.write_result("publish", False, {"status": "draft"}, {"id": "p1"})
    assert out["ok"] is False, out
    assert out["write_applied"] is True, out
    assert out["verified"] is False, out
    assert out["code"] == results.CODE_VERIFICATION_FAILED, out
    assert out["retry_safe"] is False, out

    status, body = results.envelope(out)
    assert status == results.HTTP_VERIFICATION_FAILED, (status, body)
    assert body["ok"] is False, body
    assert body["code"] == results.CODE_VERIFICATION_FAILED, body
    assert "do NOT retry blindly" in body["error"], body


def _run_standalone():
    tests = [
        test_read_result_is_successful,
        test_verified_write_is_successful,
        test_unverified_write_is_not_successful_or_retry_safe,
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if _run_standalone() else 1)
