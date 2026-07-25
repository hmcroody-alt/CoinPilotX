"""Advertising slice 4 — campaign funding readiness controller test matrix.

Exercises budget configuration + fund reservation/release directly against the
importable controller (services/business_os/advertising/api.py) + funding service
+ the canonical ledger (bot.py is not importable in the hermetic sandbox; the
route adapters are checked structurally in test_advertising_slice4_routes.py).

Proves the completion boundary and its guardrails:

    approved campaign -> budget configured -> funds reserved ONCE -> activation-ready

and: only approved+owned+non-archived campaigns can be funded; a suspended
advertiser cannot fund; the funding amount must match the configured budget;
insufficient wallet balance is rejected atomically (campaign left funding_failed,
NOT funded, funds NOT moved); a retried idempotency key never double-reserves; the
same key reused for a different operation is rejected; concurrent reservations
cannot overdraw; release restores funds exactly once and is idempotent; approval
alone moves no money; funding delivers nothing; every money move has a ledger
entry and the balance is reconstructable; flag-off darkness; legacy untouched.

    python tests/business_os/test_advertising_slice4_api.py   # no pytest needed
"""

import os
import tempfile
import threading

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ad4_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import service as ad  # noqa: E402
from services.business_os.advertising import funding as adf  # noqa: E402
from services.business_os.advertising import api as adapi  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402

ADMIN = 8
ACTIVE = {"account_status": "active", "access_enabled": 1}
SUSPENDED = {"account_status": "suspended", "access_enabled": 1}
_uid_seq = [900]


def setup_module(module=None):
    ad.ensure_schema()
    ledger.ensure_schema()


def _new_owner():
    _uid_seq[0] += 1
    return _uid_seq[0]


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _approve(uid):
    ad.upsert_advertiser(uid)
    ad.set_advertiser_status(uid, "approved", actor=ADMIN)


def _draft(owner, name="Camp", objective="traffic"):
    s, b = adapi.create_draft(
        owner, {"name": name, "objective": objective,
                "destination_url": "https://ex.com"}, context=ACTIVE)
    _assert(s == 201, (s, b))
    return b["campaign"]["campaign_id"]


def _approved_campaign(owner):
    """draft -> submit -> admin approve. Returns the approved campaign_id."""
    _approve(owner)
    cid = _draft(owner)
    s, b = adapi.submit(owner, cid, context=ACTIVE)
    _assert(s == 200, (s, b))
    s, b = adapi.admin_review(ADMIN, cid, "approve")
    _assert(s == 200 and b["after_status"] == "approved", (s, b))
    return cid


def _fund_wallet(uid, cents):
    """Seed the advertiser wallet from an allow-negative platform funding source."""
    ledger.post_entry(
        idempotency_key=f"seed:{uid}:{cents}:{os.urandom(4).hex()}",
        actor="test-seed", amount_cents=cents, currency="usd",
        entry_type="seed_deposit", source="platform:ad_funding_source",
        destination=adf._wallet_account(uid))


def _wallet_balance(uid):
    return ledger.get_balance(adf._wallet_account(uid), "usd")


def _escrow_balance(cid):
    return ledger.get_balance(adf._escrow_account(cid), "usd")


def _set_budget(owner, cid, cents, currency="usd"):
    return adapi.set_budget(
        owner, cid, {"budget_cents": cents, "currency": currency}, context=ACTIVE)


def _reserve(owner, cid, cents, key, currency="usd", context=ACTIVE):
    return adapi.reserve(
        owner, cid,
        {"amount_cents": cents, "currency": currency, "idempotency_key": key},
        context=context)


# 1 -- flag OFF: every funding handler is dark (404) -------------------------
def test_flag_off_dark():
    os.environ["BUSINESS_OS_ADVERTISING"] = "off"
    for result in (
        adapi.get_funding(1, "nope"),
        adapi.set_budget(1, "nope", {"budget_cents": 100, "currency": "usd"}),
        adapi.reserve(1, "nope", {"amount_cents": 100, "currency": "usd",
                                  "idempotency_key": "k"}),
        adapi.release(1, "nope", {"idempotency_key": "k"}),
        adapi.admin_get_funding("nope"),
        adapi.admin_list_funding(),
    ):
        status, body = result
        _assert(status == 404 and body["ok"] is False, f"expected dark 404, got {result}")
    os.environ["BUSINESS_OS_ADVERTISING"] = "on"


# 2 -- approved campaign: budget -> reserve -> funded -> activation-ready -----
def test_approved_campaign_can_be_funded():
    owner = _new_owner()
    cid = _approved_campaign(owner)
    _fund_wallet(owner, 10000)
    s, b = _set_budget(owner, cid, 5000)
    _assert(s == 200 and b["funding"]["funding_status"] == "unfunded", b)
    _assert(b["funding"]["activation_ready"] is False, b)
    s, b = _reserve(owner, cid, 5000, key=f"resv-{cid}")
    _assert(s == 200 and b["funding"]["funding_status"] == "funded", b)
    _assert(b["funding"]["activation_ready"] is True, b)
    _assert(b["funding"]["reserved_amount_cents"] == 5000, b)
    _assert(b["funding"]["reservation_txn_id"], "reservation must reference a ledger txn")
    # money moved wallet -> escrow, reconstructable from the ledger
    _assert(_wallet_balance(owner) == 5000, _wallet_balance(owner))
    _assert(_escrow_balance(cid) == 5000, _escrow_balance(cid))


# 3 -- non-approved review states cannot be funded ---------------------------
def test_non_approved_states_cannot_be_funded():
    owner = _new_owner()
    _approve(owner)
    _fund_wallet(owner, 10000)
    # draft
    cid = _draft(owner)
    _set_budget(owner, cid, 5000)
    s, b = _reserve(owner, cid, 5000, key=f"r-draft-{cid}")
    _assert(s == 409 and b["code"] == "not_approved", b)
    # submitted
    adapi.submit(owner, cid, context=ACTIVE)
    s, b = _reserve(owner, cid, 5000, key=f"r-sub-{cid}")
    _assert(s == 409 and b["code"] == "not_approved", b)
    # rejected
    adapi.admin_review(ADMIN, cid, "reject", reason="no")
    s, b = _reserve(owner, cid, 5000, key=f"r-rej-{cid}")
    _assert(s == 409 and b["code"] == "not_approved", b)
    # archived (from an approved campaign)
    cid2 = _approved_campaign(owner)
    _set_budget(owner, cid2, 5000)
    adapi.lifecycle(owner, cid2, "archive")
    s, b = _reserve(owner, cid2, 5000, key=f"r-arch-{cid2}")
    _assert(s == 409 and b["code"] == "archived", b)


# 4 -- suspended advertiser cannot fund --------------------------------------
def test_suspended_advertiser_cannot_fund():
    owner = _new_owner()
    cid = _approved_campaign(owner)
    _fund_wallet(owner, 10000)
    _set_budget(owner, cid, 5000)
    # advertiser approval revoked -> ineligible
    ad.set_advertiser_status(owner, "suspended", actor=ADMIN)
    s, b = _reserve(owner, cid, 5000, key=f"r-susp-{cid}")
    _assert(s == 403 and b["code"] == "ineligible", b)
    ad.set_advertiser_status(owner, "approved", actor=ADMIN)  # restore
    # account hold (suspended context) overrides advertiser approval
    s, b = _reserve(owner, cid, 5000, key=f"r-hold-{cid}", context=SUSPENDED)
    _assert(s == 403 and b["code"] == "ineligible", b)


# 5 -- budget guardrails: no budget / amount mismatch / currency -------------
def test_budget_and_amount_guards():
    owner = _new_owner()
    cid = _approved_campaign(owner)
    _fund_wallet(owner, 10000)
    # reserve before a budget is set
    s, b = _reserve(owner, cid, 5000, key=f"r-nob-{cid}")
    _assert(s == 409 and b["code"] == "no_budget", b)
    # bad budget input
    s, b = _set_budget(owner, cid, 0)
    _assert(s == 400 and b["code"] == "bad_amount", b)
    s, b = _set_budget(owner, cid, 5000, currency="eur")
    _assert(s == 400 and b["code"] == "bad_currency", b)
    # good budget, then amount must match exactly
    _set_budget(owner, cid, 5000)
    s, b = _reserve(owner, cid, 4000, key=f"r-mm-{cid}")
    _assert(s == 400 and b["code"] == "amount_mismatch", b)
    # non-owner cannot see/set funding (existence not leaked)
    other = _new_owner()
    _approve(other)
    s, b = adapi.get_funding(other, cid)
    _assert(s == 404 and b["code"] == "not_found", b)
    s, b = _set_budget(other, cid, 5000)
    _assert(s == 404 and b["code"] == "not_found", b)


# 6 -- insufficient balance rejected atomically; left funding_failed ---------
def test_insufficient_balance_rejected_atomically():
    owner = _new_owner()
    cid = _approved_campaign(owner)
    _fund_wallet(owner, 4999)  # one cent short of the budget
    _set_budget(owner, cid, 5000)
    s, b = _reserve(owner, cid, 5000, key=f"r-insuf-{cid}")
    _assert(s == 402 and b["code"] == "insufficient_funds", b)
    # NOT funded; funds never moved; campaign flagged failed (never clamped to 0)
    s, b = adapi.get_funding(owner, cid)
    _assert(b["funding"]["funding_status"] == "funding_failed", b)
    _assert(b["funding"]["activation_ready"] is False, b)
    _assert(_wallet_balance(owner) == 4999, _wallet_balance(owner))
    _assert(_escrow_balance(cid) == 0, _escrow_balance(cid))


# 7 -- retry with the SAME idempotency key does not double-reserve -----------
def test_retry_same_key_no_double_reserve():
    owner = _new_owner()
    cid = _approved_campaign(owner)
    _fund_wallet(owner, 10000)
    _set_budget(owner, cid, 5000)
    key = f"retry-{cid}"
    s1, b1 = _reserve(owner, cid, 5000, key=key)
    s2, b2 = _reserve(owner, cid, 5000, key=key)  # exact retry
    _assert(s1 == 200 and s2 == 200, (s1, s2))
    _assert(b1["funding"]["reservation_txn_id"] == b2["funding"]["reservation_txn_id"], "retry must be the same reservation")
    # wallet debited exactly once
    _assert(_wallet_balance(owner) == 5000, _wallet_balance(owner))
    _assert(_escrow_balance(cid) == 5000, _escrow_balance(cid))
    # exactly one ledger transaction + one funding op for this key
    conn = db.connect()
    try:
        ltx = conn.execute(
            "SELECT COUNT(*) c FROM ledger_transactions WHERE idempotency_key = ?",
            (adf._ledger_key("reserve", key),)).fetchone()
        ops = conn.execute(
            "SELECT COUNT(*) c FROM business_os_ad_funding_ops WHERE idempotency_key = ?",
            (key,)).fetchone()
    finally:
        conn.close()
    _assert(int(ltx["c"]) == 1, f"expected 1 ledger txn, got {ltx['c']}")
    _assert(int(ops["c"]) == 1, f"expected 1 funding op, got {ops['c']}")


# 8 -- reusing a key for a DIFFERENT operation is rejected -------------------
def test_key_reuse_different_operation_rejected():
    owner = _new_owner()
    cid = _approved_campaign(owner)
    _fund_wallet(owner, 10000)
    _set_budget(owner, cid, 5000)
    key = f"dup-{cid}"
    s, b = _reserve(owner, cid, 5000, key=key)
    _assert(s == 200, b)
    # same key, now for a release -> conflict
    s, b = adapi.release(owner, cid, {"idempotency_key": key})
    _assert(s == 409 and b["code"] == "idempotency_conflict", b)
    # same key reused to reserve a DIFFERENT campaign -> conflict
    cid2 = _approved_campaign(owner)
    _fund_wallet(owner, 10000)
    _set_budget(owner, cid2, 5000)
    s, b = _reserve(owner, cid2, 5000, key=key)
    _assert(s == 409 and b["code"] == "idempotency_conflict", b)


# 9 -- concurrent reservations from one wallet cannot overdraw ---------------
def test_concurrent_reservations_cannot_overdraw():
    owner = _new_owner()
    _approve(owner)
    _fund_wallet(owner, 5000)  # only enough for ONE of the two budgets
    cids = []
    for _ in range(2):
        c = _approved_campaign_same_owner(owner)
        _set_budget(owner, c, 5000)
        cids.append(c)
    results = {}
    barrier = threading.Barrier(2)

    def worker(cid):
        barrier.wait()
        results[cid] = _reserve(owner, cid, 5000, key=f"conc-{cid}")

    threads = [threading.Thread(target=worker, args=(c,)) for c in cids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    statuses = sorted(s for s, _ in results.values())
    _assert(statuses == [200, 402], f"expected one funded (200) and one insufficient (402), got {statuses}")
    # wallet fully drained but NEVER negative; exactly one escrow holds the funds
    _assert(_wallet_balance(owner) == 0, _wallet_balance(owner))
    total_escrow = sum(_escrow_balance(c) for c in cids)
    _assert(total_escrow == 5000, f"exactly one reservation should hold funds, escrow total={total_escrow}")


def _approved_campaign_same_owner(owner):
    """Like _approved_campaign but the advertiser is already approved (avoids
    re-approving mid-test which would not change state anyway)."""
    cid = _draft(owner)
    adapi.submit(owner, cid, context=ACTIVE)
    adapi.admin_review(ADMIN, cid, "approve")
    return cid


# 10 -- release restores funds exactly once; duplicate release idempotent ----
def test_release_restores_once_and_is_idempotent():
    owner = _new_owner()
    cid = _approved_campaign(owner)
    _fund_wallet(owner, 10000)
    _set_budget(owner, cid, 5000)
    _reserve(owner, cid, 5000, key=f"resv-{cid}")
    _assert(_wallet_balance(owner) == 5000, _wallet_balance(owner))
    rkey = f"rel-{cid}"
    s, b = adapi.release(owner, cid, {"idempotency_key": rkey}, context=ACTIVE)
    _assert(s == 200 and b["funding"]["funding_status"] == "released", b)
    _assert(b["funding"]["activation_ready"] is False, b)
    _assert(b["funding"]["release_txn_id"], "release must reference a ledger txn")
    _assert(_wallet_balance(owner) == 10000, _wallet_balance(owner))
    _assert(_escrow_balance(cid) == 0, _escrow_balance(cid))
    # duplicate release (same key) -> idempotent no-op, funds not double-restored
    s, b = adapi.release(owner, cid, {"idempotency_key": rkey}, context=ACTIVE)
    _assert(s == 200 and b["funding"]["funding_status"] == "released", b)
    _assert(_wallet_balance(owner) == 10000, _wallet_balance(owner))
    # a DIFFERENT key after already-released is also idempotent (no new movement)
    s, b = adapi.release(owner, cid, {"idempotency_key": f"{rkey}-2"}, context=ACTIVE)
    _assert(s == 200 and b["funding"]["funding_status"] == "released", b)
    _assert(_wallet_balance(owner) == 10000, _wallet_balance(owner))


# 11 -- release requires a funded campaign; keys/ids validated ---------------
def test_release_requires_funded():
    owner = _new_owner()
    cid = _approved_campaign(owner)
    _fund_wallet(owner, 10000)
    _set_budget(owner, cid, 5000)
    # not funded yet -> cannot release
    s, b = adapi.release(owner, cid, {"idempotency_key": f"rel-early-{cid}"})
    _assert(s == 409 and b["code"] == "not_funded", b)
    # missing idempotency key -> 400
    s, b = adapi.release(owner, cid, {})
    _assert(s == 400 and b["code"] == "idempotency_key_required", b)


# 12 -- approval alone moves no money ----------------------------------------
def test_approval_alone_no_spend():
    owner = _new_owner()
    cid = _approved_campaign(owner)
    # no budget, no reservation -> wallet untouched, unfunded, not activation-ready
    _assert(_wallet_balance(owner) == 0, _wallet_balance(owner))
    _assert(_escrow_balance(cid) == 0, _escrow_balance(cid))
    s, b = adapi.get_funding(owner, cid)
    _assert(s == 200 and b["funding"]["funding_status"] == "unfunded", b)
    _assert(b["funding"]["activation_ready"] is False, b)


# 13 -- funding moves money but delivers nothing -----------------------------
def test_funding_no_delivery():
    owner = _new_owner()
    cid = _approved_campaign(owner)
    _fund_wallet(owner, 10000)
    _set_budget(owner, cid, 5000)
    _reserve(owner, cid, 5000, key=f"resv-{cid}")
    # funding view exposes no delivery/impression fields
    s, b = adapi.get_funding(owner, cid)
    for banned in ("impressions", "delivered", "clicks", "spend", "auction",
                   "served", "views"):
        _assert(banned not in b["funding"], f"funding view leaked delivery field {banned!r}")
    # the underlying campaign row is still just review-approved
    s, b = adapi.get_own_campaign(owner, cid)
    _assert(b["campaign"]["status"] == "approved", b)
    # the legacy delivery table is never created by the canonical funding path
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pulse_ad_campaigns'"
        ).fetchone()
    finally:
        conn.close()
    _assert(row is None, "canonical funding path must not create legacy pulse_ad_campaigns")


# 14 -- every money move has a ledger entry; balance reconstructable ---------
def test_ledger_backed_and_reconstructable():
    owner = _new_owner()
    cid = _approved_campaign(owner)
    _fund_wallet(owner, 10000)
    _set_budget(owner, cid, 5000)
    r = _reserve(owner, cid, 5000, key=f"resv-{cid}")[1]["funding"]
    # the reservation txn exists in the ledger and moved wallet -> escrow
    conn = db.connect()
    try:
        tx = conn.execute(
            "SELECT * FROM ledger_transactions WHERE transaction_id = ?",
            (r["reservation_txn_id"],)).fetchone()
    finally:
        conn.close()
    _assert(tx is not None, "reservation must have a ledger transaction row")
    _assert(tx["source_account"] == adf._wallet_account(owner), dict(tx))
    _assert(tx["destination_account"] == adf._escrow_account(cid), dict(tx))
    # balance recomputed purely from entries matches the cached balance
    recomputed = ledger.recompute_balance(adf._escrow_account(cid), "usd")
    _assert(recomputed == 5000, f"escrow balance must reconstruct from entries, got {recomputed}")


# 15 -- admin funding visibility (state + ledger refs + op log) --------------
def test_admin_funding_visibility():
    owner = _new_owner()
    cid = _approved_campaign(owner)
    _fund_wallet(owner, 10000)
    _set_budget(owner, cid, 5000)
    _reserve(owner, cid, 5000, key=f"resv-{cid}")
    s, b = adapi.admin_get_funding(cid)
    _assert(s == 200, b)
    view = b["funding"]
    _assert(view["funding_status"] == "funded", view)
    _assert(view["reservation_txn_id"], view)
    _assert(view["escrow_balance_cents"] == 5000, view)
    _assert(any(op["operation"] == "reserve" for op in view["operations"]), view)
    # failed reservations are inspectable via the status filter
    poor = _new_owner()
    pc = _approved_campaign(poor)
    _fund_wallet(poor, 1)
    _set_budget(poor, pc, 5000)
    _reserve(poor, pc, 5000, key=f"resv-{pc}")  # -> funding_failed
    s, b = adapi.admin_list_funding(funding_status="funding_failed")
    _assert(s == 200 and any(r["campaign_id"] == pc for r in b["funding"]), b)


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_flag_off_dark,
        test_approved_campaign_can_be_funded,
        test_non_approved_states_cannot_be_funded,
        test_suspended_advertiser_cannot_fund,
        test_budget_and_amount_guards,
        test_insufficient_balance_rejected_atomically,
        test_retry_same_key_no_double_reserve,
        test_key_reuse_different_operation_rejected,
        test_concurrent_reservations_cannot_overdraw,
        test_release_restores_once_and_is_idempotent,
        test_release_requires_funded,
        test_approval_alone_no_spend,
        test_funding_no_delivery,
        test_ledger_backed_and_reconstructable,
        test_admin_funding_visibility,
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
