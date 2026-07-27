"""Advertising Stage 2 — the governed UNDX Advertising Assistant.

Proves the two properties a language model cannot be trusted to enforce itself and
which the assistant therefore enforces SERVER-SIDE (spec Part 4: "Governed UNDX tools
requiring confirmation before publish/activate/pause/budget-change/etc. Verify every
claimed action against canonical backend state"):

  1. **Confirmation before any consequential change.** A read-only tool runs immediately
     from ``plan``; a consequential tool (``set_budget``, ``activate_campaign``, ...) mints
     a server-side confirmation grant bound to the EXACT (user, tool, canonical params)
     and ``execute`` refuses to run it without a matching token — 428
     ``confirmation_required`` when absent, 409 ``confirmation_mismatch`` when forged or
     minted for a different action. A valid grant is single-use and cannot be replayed.

  2. **Read-after-write verification against canonical state.** ``execute`` never reports
     success from the verb's return value; it RE-READS the authoritative row and reports
     ``verified`` from the observed state (budget_cents == requested, operational_status ==
     'active', etc.).

Also proves the write kill switch (``BUSINESS_OS_ADVERTISING_ASSISTANT_DISABLE_WRITES``)
disables writes without touching reads, and that ownership is enforced (a campaign the
caller does not own ⇒ 404) on both read and write paths.

    python tests/business_os/test_advertising_assistant.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_adasst_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"
os.environ.pop("BUSINESS_OS_ADVERTISING_ASSISTANT_DISABLE_WRITES", None)

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import schema as ad_schema  # noqa: E402
from services.business_os.advertising import service as ad  # noqa: E402
from services.business_os.advertising import pricing  # noqa: E402
from services.business_os.advertising import assistant  # noqa: E402
from services.business_os.advertising.service import AdvertisingError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


OWNER = 700
OTHER = 701
ADMIN = 9


def setup_module(module=None):
    ad_schema.ensure_schema()
    ledger.ensure_schema()
    pricing.publish_policy("cpm", "usd", 500, actor="admin")
    pricing.publish_policy("cpc", "usd", 25, actor="admin")
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)")
        conn.commit()
    finally:
        conn.close()


def _ctx():
    return {"account_status": "active", "access_enabled": 1}


def _approve(uid):
    ad.upsert_advertiser(uid)
    ad.set_advertiser_status(uid, "approved", actor=ADMIN)


def _draft(uid, name="asst"):
    c = ad.create_campaign_draft(uid, name=name, objective="traffic", context=_ctx())
    return c["campaign_id"]


def _expect_error(fn, code=None, http=None):
    try:
        fn()
    except AdvertisingError as e:
        if code is not None:
            assert e.code == code, f"expected code {code}, got {e.code}"
        if http is not None:
            assert e.http_status == http, f"expected http {http}, got {e.http_status}"
        return
    raise AssertionError(f"expected AdvertisingError(code={code}, http={http})")


# --- (a) read-only tools run from plan() with no confirmation ---------------
def test_read_tools_run_without_confirmation():
    _approve(OWNER)
    cid = _draft(OWNER, "read-me")
    for tool in ("operational_status", "funding_status", "spend", "report"):
        out = assistant.plan(OWNER, tool, {"campaign_id": cid})
        assert out["requires_confirmation"] is False, out
        assert out["write"] is False, out
        assert "result" in out and out["result"] is not None, out
    # catalog marks reads as non-confirming and writes correctly
    cat = {t["tool"]: t for t in assistant.list_tools()}
    assert cat["operational_status"]["requires_confirmation"] is False, cat
    assert cat["set_budget"]["requires_confirmation"] is True, cat
    assert cat["set_budget"]["is_write"] is True, cat


# --- (b) consequential tool: token required, mismatch refused ---------------
def test_consequential_requires_matching_token():
    _approve(OWNER)
    cid = _draft(OWNER, "budget-gate")
    p = assistant.plan(OWNER, "set_budget",
                       {"campaign_id": cid, "budget_cents": 5000, "currency": "usd"})
    assert p["requires_confirmation"] is True, p
    assert p["confirmation_token"], p
    assert p["canonical_params"]["budget_cents"] == 5000, p

    # execute WITHOUT a token -> 428 confirmation_required
    _expect_error(
        lambda: assistant.execute(
            OWNER, "set_budget",
            {"campaign_id": cid, "budget_cents": 5000, "currency": "usd"}),
        code="confirmation_required", http=428)

    # execute with a FORGED token -> 409 confirmation_mismatch
    _expect_error(
        lambda: assistant.execute(
            OWNER, "set_budget",
            {"campaign_id": cid, "budget_cents": 5000, "currency": "usd"},
            confirmation_token="deadbeef" * 8),
        code="confirmation_mismatch", http=409)

    # a token minted for a DIFFERENT amount cannot execute this one
    p2 = assistant.plan(OWNER, "set_budget",
                        {"campaign_id": cid, "budget_cents": 9999, "currency": "usd"})
    _expect_error(
        lambda: assistant.execute(
            OWNER, "set_budget",
            {"campaign_id": cid, "budget_cents": 5000, "currency": "usd"},
            confirmation_token=p2["confirmation_token"]),
        code="confirmation_mismatch", http=409)


# --- (c) correct token executes and is verified against canonical state -----
def test_execute_with_token_is_verified_against_canonical_state():
    _approve(OWNER)
    cid = _draft(OWNER, "budget-do")
    p = assistant.plan(OWNER, "set_budget",
                       {"campaign_id": cid, "budget_cents": 7500, "currency": "usd"})
    out = assistant.execute(
        OWNER, "set_budget",
        {"campaign_id": cid, "budget_cents": 7500, "currency": "usd"},
        confirmation_token=p["confirmation_token"])
    assert out["ok"] is True and out["write"] is True, out
    assert out["verified"] is True, out
    # ``ok`` is derived from verification; ``write_applied`` separately reports that the
    # canonical verb ran. Both must be present so neither style of client reads a
    # missing key as falsy. See services.business_os.results.
    assert out["write_applied"] is True, out
    assert out["observed"]["budget_cents"] == 7500, out
    _expect_error(
        lambda: assistant.execute(
            OWNER, "set_budget",
            {"campaign_id": cid, "budget_cents": 7500, "currency": "usd"},
            confirmation_token=p["confirmation_token"]),
        code="confirmation_used", http=409)
    # canonical funding view independently confirms the change actually landed
    fv = assistant.execute(OWNER, "funding_status", {"campaign_id": cid})
    assert fv["result"]["budget_cents"] == 7500, fv


# --- (c2) lifecycle: submit is gated, executes, and verifies 'submitted' ----
def test_submit_campaign_gated_and_verified():
    _approve(OWNER)
    cid = _draft(OWNER, "submit-me")
    assistant.execute(OWNER, "set_budget",
                      {"campaign_id": cid, "budget_cents": 5000, "currency": "usd"},
                      confirmation_token=assistant.plan(
                          OWNER, "set_budget",
                          {"campaign_id": cid, "budget_cents": 5000,
                           "currency": "usd"})["confirmation_token"])
    p = assistant.plan(OWNER, "submit_campaign", {"campaign_id": cid})
    assert p["requires_confirmation"] is True, p
    out = assistant.execute(OWNER, "submit_campaign", {"campaign_id": cid},
                            confirmation_token=p["confirmation_token"])
    assert out["verified"] is True, out
    assert out["observed"]["status"] == "submitted", out


# --- (d) write kill switch disables writes but not reads --------------------
def test_writes_kill_switch():
    _approve(OWNER)
    cid = _draft(OWNER, "killswitch")
    p = assistant.plan(OWNER, "set_budget",
                       {"campaign_id": cid, "budget_cents": 4000, "currency": "usd"})
    os.environ["BUSINESS_OS_ADVERTISING_ASSISTANT_DISABLE_WRITES"] = "1"
    try:
        # even with a valid token, the write is refused
        _expect_error(
            lambda: assistant.execute(
                OWNER, "set_budget",
                {"campaign_id": cid, "budget_cents": 4000, "currency": "usd"},
                confirmation_token=p["confirmation_token"]),
            code="writes_disabled", http=409)
        # reads still work
        r = assistant.execute(OWNER, "funding_status", {"campaign_id": cid})
        assert r["ok"] is True, r
    finally:
        os.environ.pop("BUSINESS_OS_ADVERTISING_ASSISTANT_DISABLE_WRITES", None)


# --- (e) ownership is enforced on read and write paths ----------------------
def test_ownership_enforced():
    _approve(OWNER)
    _approve(OTHER)
    cid = _draft(OWNER, "owned")
    # OTHER cannot read OWNER's campaign
    _expect_error(lambda: assistant.plan(OTHER, "operational_status",
                                         {"campaign_id": cid}), http=404)
    _expect_error(lambda: assistant.plan(OTHER, "report",
                                         {"campaign_id": cid}), http=404)
    # OTHER cannot write to OWNER's campaign even with a self-minted token
    p = assistant.plan(OTHER, "set_budget",
                       {"campaign_id": cid, "budget_cents": 100, "currency": "usd"})
    _expect_error(
        lambda: assistant.execute(
            OTHER, "set_budget",
            {"campaign_id": cid, "budget_cents": 100, "currency": "usd"},
            confirmation_token=p["confirmation_token"]),
        http=404)


# --- (f) create_draft is a verified low-risk write (no confirmation) --------
def test_create_draft_low_risk_verified():
    _approve(OWNER)
    out = assistant.execute(OWNER, "create_draft",
                            {"name": "born via assistant", "objective": "traffic"})
    assert out["ok"] is True and out["write"] is True, out
    assert out["verified"] is True, out
    assert out["observed"]["status"] == "draft", out


# --- unknown tool is rejected -----------------------------------------------
def test_unknown_tool_rejected():
    _expect_error(lambda: assistant.plan(OWNER, "delete_everything", {}),
                  code="unknown_tool", http=400)


def _run_standalone():
    setup_module()
    tests = [
        test_read_tools_run_without_confirmation,
        test_consequential_requires_matching_token,
        test_execute_with_token_is_verified_against_canonical_state,
        test_submit_campaign_gated_and_verified,
        test_writes_kill_switch,
        test_ownership_enforced,
        test_create_draft_low_risk_verified,
        test_unknown_tool_rejected,
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
