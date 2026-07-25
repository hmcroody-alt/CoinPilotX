"""Cross-subsystem confirmation-grant conformance — ONE suite, EVERY approval boundary.

Per-subsystem tests prove a subsystem works. They cannot prove the *ecosystem* holds one
line, and governance drift is exactly what happens when four boundaries each carry their
own approval code. So this suite states the required properties once and runs them against
every confirmation boundary in the repo:

  L1  ``services.business_os.confirmations``          the canonical grant service
  L2  ``business_os.marketplace.assistant``           (the only assistant that moves money)
  L3  ``business_os.advertising.assistant``           (spend / campaign lifecycle)
  L4  ``business_os.undx_actions.engine``             (org-scoped governed action requests)
  L5  ``services.undx_architecture`` + pulse_ai       (per-user PulseSoc tool confirmations)

The required properties, and why each one is not optional:

  replay          A redeemed approval must never work twice, or one human "yes" pays an
                  order, then pays it again.
  expiry          An approval with no deadline is a standing authorization. Expiry must be
                  impossible to disable — by env, by caller, or by omission.
  revocation      An approval that cannot be withdrawn leaves an approver who changed
                  their mind with only two options: race the executor, or wait.
  actor binding   Another account's approval must not authorize my action.
  tool binding    An approval for "pause" must not execute "publish".
  payload binding Editing the payload after approval must invalidate it, or "approve $50"
                  becomes "$50,000".
  concurrency     Two simultaneous redemptions of one approval must yield exactly one
                  execution. A check-then-act that is not atomic is a double-spend.
  cross-tenant    A grant minted in one namespace/org must be unredeemable in another,
                  even when the tool names collide.
  failed exec     A burnt approval must stay burnt when the underlying verb then fails —
                  otherwise a failure hands back a reusable authorization.

A boundary that cannot demonstrate a property fails here rather than being described as
compliant in prose.

    python tests/business_os/test_confirmation_conformance.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_confconf_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"
os.environ["BUSINESS_OS_ADVERTISING"] = "on"
os.environ["BUSINESS_OS_UNDX_ACTIONS"] = "on"
os.environ.pop("BUSINESS_OS_MARKETPLACE_ASSISTANT_DISABLE_WRITES", None)
os.environ.pop("BUSINESS_OS_ADVERTISING_ASSISTANT_DISABLE_WRITES", None)
os.environ.pop("BUSINESS_OS_CONFIRMATION_TTL_SECONDS", None)

import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db                                                      # noqa: E402
from services.business_os import confirmations as cf                         # noqa: E402
from services.business_os import results as res                              # noqa: E402
from services.business_os.ledger import ledger                               # noqa: E402
from services.business_os.marketplace import schema as mkt_schema            # noqa: E402
from services.business_os.marketplace import service as mkt                  # noqa: E402
from services.business_os.marketplace import orders as mkt_orders            # noqa: E402
from services.business_os.marketplace import assistant as mkt_asst           # noqa: E402
from services.business_os.marketplace import api as mkt_api                  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError        # noqa: E402
from services.business_os.advertising import schema as ad_schema             # noqa: E402
from services.business_os.advertising import service as ad                   # noqa: E402
from services.business_os.advertising import pricing as ad_pricing           # noqa: E402
from services.business_os.advertising import assistant as ad_asst            # noqa: E402
from services.business_os.advertising.service import AdvertisingError        # noqa: E402
from services.business_os.undx_actions import schema as undx_schema          # noqa: E402
from services.business_os.undx_actions import engine as undx_engine          # noqa: E402
from services import undx_architecture                                       # noqa: E402


ADMIN = 9
SELLER, BUYER, THIRD = 900, 901, 902
ADVERTISER, AD_OTHER = 910, 911
PULSE_USER, PULSE_OTHER = 920, 921


def setup_module(module=None):
    mkt_schema.ensure_schema()
    ad_schema.ensure_schema()
    undx_schema.ensure_schema()
    ledger.ensure_schema()
    cf.ensure_schema()
    ad_pricing.publish_policy("cpm", "usd", 500, actor="admin")
    ad_pricing.publish_policy("cpc", "usd", 25, actor="admin")
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)")
        undx_architecture.ensure_schema(conn.cursor())
        conn.commit()
    finally:
        conn.close()
    mkt.upsert_seller(SELLER, display_name="Conformance Seller")
    mkt.set_seller_status(SELLER, "approved", actor=ADMIN)
    for uid in (ADVERTISER, AD_OTHER):
        ad.upsert_advertiser(uid)
        ad.set_advertiser_status(uid, "approved", actor=ADMIN)


def _ctx():
    return {"account_status": "active", "access_enabled": 1}


# --- shared assertion helpers ------------------------------------------------
def _expect(exc_type, fn, code=None, http=None, label=""):
    try:
        fn()
    except exc_type as e:
        if code is not None:
            assert getattr(e, "code", None) == code, \
                f"{label}: expected code {code}, got {getattr(e, 'code', None)}"
        if http is not None:
            assert getattr(e, "http_status", None) == http, \
                f"{label}: expected http {http}, got {getattr(e, 'http_status', None)}"
        return e
    raise AssertionError(f"{label}: expected {exc_type.__name__}(code={code}, http={http})")


def _expire_now(token):
    """Force a grant past its deadline without sleeping through the TTL.

    Reaching into the row is deliberate: a real clock test would need a 30s+ sleep (the
    TTL floor is clamped so it CANNOT be shortened below that), and a fake clock would
    only prove the fake works. Rewriting the stored deadline exercises the same
    comparison the production path performs.
    """
    conn = db.connect()
    try:
        conn.execute(
            f"UPDATE {cf.TABLE} SET expires_at = '2000-01-01T00:00:00.000000Z' "
            "WHERE token_hash = ?", (cf.token_hash(token),))
        conn.commit()
    finally:
        conn.close()


def _race(fn, workers=8):
    """Run ``fn`` in ``workers`` threads at once; return (successes, failures).

    Threads are released by one barrier so the redemptions genuinely overlap rather
    than queueing.
    """
    barrier = threading.Barrier(workers)
    wins, losses = [], []
    lock = threading.Lock()

    def _worker(i):
        barrier.wait()
        try:
            out = fn(i)
        except Exception as exc:            # any refusal is a loss, which is the point
            with lock:
                losses.append(exc)
        else:
            with lock:
                wins.append(out)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return wins, losses


# =============================================================================
# L1 — the canonical grant service
# =============================================================================
NS_A, NS_B = "conf_ns_a", "conf_ns_b"


def test_L1_replay_refused():
    g = cf.mint(NS_A, "u1", "pay", {"order_id": "o1"})
    cf.consume(NS_A, "u1", "pay", {"order_id": "o1"}, g["confirmation_token"])
    _expect(cf.ConfirmationError,
            lambda: cf.consume(NS_A, "u1", "pay", {"order_id": "o1"},
                               g["confirmation_token"]),
            code=cf.CODE_USED, http=409, label="L1 replay")


def test_L1_expiry_enforced_and_cannot_be_disabled():
    g = cf.mint(NS_A, "u1", "pay", {"order_id": "o-exp"})
    _expire_now(g["confirmation_token"])
    _expect(cf.ConfirmationError,
            lambda: cf.consume(NS_A, "u1", "pay", {"order_id": "o-exp"},
                               g["confirmation_token"]),
            code=cf.CODE_EXPIRED, http=409, label="L1 expiry")

    # Neither a caller nor an operator may switch expiry off. A huge, zero, negative or
    # junk TTL all clamp into [TTL_MIN, TTL_MAX] — an unbounded TTL is the same defect
    # as no expiry at all.
    for attempt in (10 ** 9, 0, -1, 999999):
        assert cf.TTL_MIN <= cf.ttl_seconds(attempt) <= cf.TTL_MAX, attempt
    saved = os.environ.get(cf.TTL_ENV)
    try:
        for env_attempt in ("999999999", "0", "-5", "not-a-number", ""):
            os.environ[cf.TTL_ENV] = env_attempt
            assert cf.TTL_MIN <= cf.ttl_seconds() <= cf.TTL_MAX, env_attempt
        os.environ[cf.TTL_ENV] = "10000000"
        g2 = cf.mint(NS_A, "u1", "pay", {"order_id": "o-clamp"})
        assert g2["ttl_seconds"] <= cf.TTL_MAX, g2
    finally:
        if saved is None:
            os.environ.pop(cf.TTL_ENV, None)
        else:
            os.environ[cf.TTL_ENV] = saved


def test_L1_revocation():
    g = cf.mint(NS_A, "u1", "pay", {"order_id": "o-rev"})
    assert cf.revoke(NS_A, "u1", g["confirmation_token"])["revoked"] is True
    _expect(cf.ConfirmationError,
            lambda: cf.consume(NS_A, "u1", "pay", {"order_id": "o-rev"},
                               g["confirmation_token"]),
            code=cf.CODE_REVOKED, http=409, label="L1 revoked")
    # idempotent, and only the owning actor may revoke
    assert cf.revoke(NS_A, "u1", g["confirmation_token"])["revoked"] is False
    g2 = cf.mint(NS_A, "u1", "pay", {"order_id": "o-rev2"})
    assert cf.revoke(NS_A, "u-other", g2["confirmation_token"])["revoked"] is False
    cf.consume(NS_A, "u1", "pay", {"order_id": "o-rev2"}, g2["confirmation_token"])


def test_L1_actor_tool_and_payload_binding():
    g = cf.mint(NS_A, "u1", "pay", {"order_id": "o-bind", "amount": 50})
    tok = g["confirmation_token"]
    for label, call in (
        ("actor",   lambda: cf.consume(NS_A, "u2", "pay", {"order_id": "o-bind", "amount": 50}, tok)),
        ("tool",    lambda: cf.consume(NS_A, "u1", "refund", {"order_id": "o-bind", "amount": 50}, tok)),
        ("payload", lambda: cf.consume(NS_A, "u1", "pay", {"order_id": "o-bind", "amount": 50000}, tok)),
        ("missing", lambda: cf.consume(NS_A, "u1", "pay", {"order_id": "o-bind"}, tok)),
        ("forged",  lambda: cf.consume(NS_A, "u1", "pay", {"order_id": "o-bind", "amount": 50}, "deadbeef" * 8)),
    ):
        _expect(cf.ConfirmationError, call, code=cf.CODE_MISMATCH, http=409,
                label=f"L1 {label} binding")
    # every refusal above left the grant intact and redeemable for its own action
    cf.consume(NS_A, "u1", "pay", {"order_id": "o-bind", "amount": 50}, tok)


def test_L1_cross_namespace_isolation():
    """Same actor, same tool name, same payload — different subsystem. Must not redeem."""
    g = cf.mint(NS_A, "u1", "publish", {"id": "x"})
    _expect(cf.ConfirmationError,
            lambda: cf.consume(NS_B, "u1", "publish", {"id": "x"},
                               g["confirmation_token"]),
            code=cf.CODE_MISMATCH, http=409, label="L1 cross-namespace")
    cf.consume(NS_A, "u1", "publish", {"id": "x"}, g["confirmation_token"])


def test_L1_concurrent_redemption_yields_exactly_one_winner():
    g = cf.mint(NS_A, "u1", "pay", {"order_id": "o-race"})
    tok = g["confirmation_token"]
    wins, losses = _race(
        lambda i: cf.consume(NS_A, "u1", "pay", {"order_id": "o-race"}, tok), workers=8)
    assert len(wins) == 1, f"expected exactly 1 winner, got {len(wins)}"
    assert len(losses) == 7, f"expected 7 refusals, got {len(losses)}"
    assert all(isinstance(e, cf.ConfirmationError) for e in losses), losses


def test_L1_missing_token_is_required_not_mismatch():
    for empty in (None, ""):
        _expect(cf.ConfirmationError,
                lambda: cf.consume(NS_A, "u1", "pay", {"order_id": "o"}, empty),
                code=cf.CODE_REQUIRED, http=428, label="L1 required")


def test_L1_raw_token_never_persisted():
    g = cf.mint(NS_A, "u1", "pay", {"order_id": "o-secret"})
    raw = g["confirmation_token"]
    conn = db.connect()
    try:
        rows = conn.execute(f"SELECT * FROM {cf.TABLE}").fetchall()
    finally:
        conn.close()
    blob = " ".join(str(dict(r)) for r in rows)
    assert raw not in blob, "raw confirmation token was persisted — a DB read grants approval"
    assert cf.token_hash(raw) in blob, "grant row not found at all"
    # describe() is a lookup, not a leak
    d = cf.describe(NS_A, "u1", raw)
    assert d and "confirmation_token" not in d, d


# =============================================================================
# L2 — marketplace assistant (money)
# =============================================================================
def _product(price=2000, inv=9):
    p = mkt.create_product(SELLER, title="Conf Widget", price_cents=price,
                           fulfillment_type="physical", inventory_qty=inv, context=_ctx())
    mkt.transition_product(SELLER, p["product_id"], "publish")
    return p["product_id"]


def _order(pid, qty=1):
    return mkt_orders.create_order(BUYER, pid, quantity=qty)["order_id"]


def test_L2_replay_expiry_revocation():
    pid = _product()
    oid = _order(pid)
    p = mkt_asst.plan(BUYER, "pay_order", {"order_id": oid})
    mkt_asst.execute(BUYER, "pay_order", {"order_id": oid},
                     confirmation_token=p["confirmation_token"])
    _expect(MarketplaceError,
            lambda: mkt_asst.execute(BUYER, "pay_order", {"order_id": oid},
                                     confirmation_token=p["confirmation_token"]),
            code="confirmation_used", http=409, label="L2 replay")

    oid2 = _order(pid)
    p2 = mkt_asst.plan(BUYER, "pay_order", {"order_id": oid2})
    _expire_now(p2["confirmation_token"])
    _expect(MarketplaceError,
            lambda: mkt_asst.execute(BUYER, "pay_order", {"order_id": oid2},
                                     confirmation_token=p2["confirmation_token"]),
            code="confirmation_expired", http=409, label="L2 expiry")

    oid3 = _order(pid)
    p3 = mkt_asst.plan(BUYER, "pay_order", {"order_id": oid3})
    assert mkt_asst.revoke_confirmation(BUYER, p3["confirmation_token"])["revoked"] is True
    _expect(MarketplaceError,
            lambda: mkt_asst.execute(BUYER, "pay_order", {"order_id": oid3},
                                     confirmation_token=p3["confirmation_token"]),
            code="confirmation_revoked", http=409, label="L2 revoked")
    # none of the three refused orders moved money
    for refused in (oid2, oid3):
        assert mkt_orders.get_order(refused, requester_user_id=BUYER)["status"] == "created"


def test_L2_actor_tool_and_payload_binding():
    pid = _product()
    oid = _order(pid)
    p = mkt_asst.plan(BUYER, "pay_order", {"order_id": oid})
    tok = p["confirmation_token"]
    # a third party cannot redeem the buyer's approval
    _expect(MarketplaceError,
            lambda: mkt_asst.execute(THIRD, "pay_order", {"order_id": oid},
                                     confirmation_token=tok),
            code="confirmation_mismatch", http=409, label="L2 actor binding")
    # the buyer cannot use a pay approval to cancel
    _expect(MarketplaceError,
            lambda: mkt_asst.execute(BUYER, "cancel_order", {"order_id": oid},
                                     confirmation_token=tok),
            code="confirmation_mismatch", http=409, label="L2 tool binding")
    # editing the payload after approval invalidates it
    oid_other = _order(pid)
    _expect(MarketplaceError,
            lambda: mkt_asst.execute(BUYER, "pay_order", {"order_id": oid_other},
                                     confirmation_token=tok),
            code="confirmation_mismatch", http=409, label="L2 payload binding")
    # and the approval still works for exactly what was approved
    out = mkt_asst.execute(BUYER, "pay_order", {"order_id": oid},
                           confirmation_token=tok)
    assert out["ok"] is True and out["verified"] is True, out


def test_L2_cross_namespace_token_from_advertising_refused():
    """Tool names collide across subsystems; approvals must not."""
    pid = _product()
    oid = _order(pid)
    ad_grant = cf.mint(ad_asst.CONFIRM_NAMESPACE, str(BUYER), "pay_order",
                       {"order_id": oid})
    _expect(MarketplaceError,
            lambda: mkt_asst.execute(BUYER, "pay_order", {"order_id": oid},
                                     confirmation_token=ad_grant["confirmation_token"]),
            code="confirmation_mismatch", http=409, label="L2 cross-namespace")
    assert mkt_orders.get_order(oid, requester_user_id=BUYER)["status"] == "created"


def test_L2_concurrent_pay_charges_once():
    """The money case. Eight threads, one approval: exactly one payment."""
    pid = _product(price=2000, inv=9)
    oid = _order(pid, qty=1)
    tok = mkt_asst.plan(BUYER, "pay_order", {"order_id": oid})["confirmation_token"]
    wins, losses = _race(
        lambda i: mkt_asst.execute(BUYER, "pay_order", {"order_id": oid},
                                   confirmation_token=tok), workers=8)
    assert len(wins) == 1, f"expected exactly 1 payment, got {len(wins)}: {wins}"
    assert mkt_orders.get_order(oid, requester_user_id=BUYER)["status"] == "paid"
    # escrow holds one order's worth, not eight
    assert ledger.get_balance(mkt_orders.escrow_account(oid)) == 2000, "double charge"


def test_L2_failed_execution_still_consumes():
    """A verb that fails must not hand the approval back."""
    pid = _product()
    oid = _order(pid)
    # complete_order is illegal from 'created' — the handler raises after consumption
    tok = mkt_asst.plan(BUYER, "complete_order", {"order_id": oid})["confirmation_token"]
    try:
        mkt_asst.execute(BUYER, "complete_order", {"order_id": oid},
                         confirmation_token=tok)
        raise AssertionError("complete_order from 'created' should have failed")
    except MarketplaceError:
        pass
    d = cf.describe(mkt_asst.CONFIRM_NAMESPACE, str(BUYER), tok)
    assert d and d["status"] == "consumed", f"approval survived a failed execution: {d}"
    _expect(MarketplaceError,
            lambda: mkt_asst.execute(BUYER, "complete_order", {"order_id": oid},
                                     confirmation_token=tok),
            code="confirmation_used", http=409, label="L2 failed-exec reuse")


# =============================================================================
# L3 — advertising assistant (the boundary that was replayable)
# =============================================================================
def _campaign(uid=ADVERTISER, name="conf"):
    return ad.create_campaign_draft(uid, name=name, objective="traffic",
                                    context=_ctx())["campaign_id"]


def _ad_budget(cid, uid=ADVERTISER):
    """Read the budget from the canonical funding view the assistant verifies against."""
    return ad_asst.execute(uid, "funding_status", {"campaign_id": cid})["result"]["budget_cents"]


def test_L3_replay_refused_regression():
    """The exact defect that was observed: replaying one budget approval.

    Before the fix the token was ``sha256(salt|user|tool|params)`` — derivable, therefore
    reusable forever — so a single approved "set budget to 5000" could be replayed to undo
    any later change. This is the regression guard for that.
    """
    cid = _campaign(name="replay-guard")
    params = {"campaign_id": cid, "budget_cents": 5000, "currency": "usd"}
    tok = ad_asst.plan(ADVERTISER, "set_budget", params)["confirmation_token"]
    out = ad_asst.execute(ADVERTISER, "set_budget", dict(params), confirmation_token=tok)
    assert out["verified"] is True and out["observed"]["budget_cents"] == 5000, out

    # a second, separately approved change moves the budget
    p2 = {"campaign_id": cid, "budget_cents": 111, "currency": "usd"}
    ad_asst.execute(ADVERTISER, "set_budget", dict(p2),
                    confirmation_token=ad_asst.plan(
                        ADVERTISER, "set_budget", p2)["confirmation_token"])
    # replaying the FIRST approval must not resurrect 5000
    _expect(AdvertisingError,
            lambda: ad_asst.execute(ADVERTISER, "set_budget", dict(params),
                                    confirmation_token=tok),
            code="confirmation_used", http=409, label="L3 replay")
    assert _ad_budget(cid) == 111, f"replayed approval resurrected the old budget: {_ad_budget(cid)}"


def test_L3_expiry_and_revocation():
    cid = _campaign(name="exp-rev")
    params = {"campaign_id": cid, "budget_cents": 4000, "currency": "usd"}
    p = ad_asst.plan(ADVERTISER, "set_budget", params)
    assert p["expires_at"] and p["single_use"] is True, p
    _expire_now(p["confirmation_token"])
    _expect(AdvertisingError,
            lambda: ad_asst.execute(ADVERTISER, "set_budget", dict(params),
                                    confirmation_token=p["confirmation_token"]),
            code="confirmation_expired", http=409, label="L3 expiry")

    p2 = ad_asst.plan(ADVERTISER, "set_budget", params)
    assert ad_asst.revoke_confirmation(
        ADVERTISER, p2["confirmation_token"])["revoked"] is True
    _expect(AdvertisingError,
            lambda: ad_asst.execute(ADVERTISER, "set_budget", dict(params),
                                    confirmation_token=p2["confirmation_token"]),
            code="confirmation_revoked", http=409, label="L3 revoked")


def test_L3_actor_tool_and_payload_binding():
    cid = _campaign(name="bindings")
    params = {"campaign_id": cid, "budget_cents": 7000, "currency": "usd"}
    tok = ad_asst.plan(ADVERTISER, "set_budget", params)["confirmation_token"]
    # another advertiser cannot redeem it (mismatch is checked before ownership)
    _expect(AdvertisingError,
            lambda: ad_asst.execute(AD_OTHER, "set_budget", dict(params),
                                    confirmation_token=tok),
            code="confirmation_mismatch", http=409, label="L3 actor binding")
    # a budget approval is not a submit approval
    _expect(AdvertisingError,
            lambda: ad_asst.execute(ADVERTISER, "submit_campaign",
                                    {"campaign_id": cid}, confirmation_token=tok),
            code="confirmation_mismatch", http=409, label="L3 tool binding")
    # the amount cannot be edited after approval
    edited = {"campaign_id": cid, "budget_cents": 7000000, "currency": "usd"}
    _expect(AdvertisingError,
            lambda: ad_asst.execute(ADVERTISER, "set_budget", edited,
                                    confirmation_token=tok),
            code="confirmation_mismatch", http=409, label="L3 payload binding")
    out = ad_asst.execute(ADVERTISER, "set_budget", dict(params),
                          confirmation_token=tok)
    assert out["observed"]["budget_cents"] == 7000, out


def test_L3_cross_namespace_token_from_marketplace_refused():
    cid = _campaign(name="xns")
    params = {"campaign_id": cid, "budget_cents": 3000, "currency": "usd"}
    mkt_grant = cf.mint(mkt_asst.CONFIRM_NAMESPACE, str(ADVERTISER), "set_budget",
                        ad_asst._norm_params("set_budget", params))
    _expect(AdvertisingError,
            lambda: ad_asst.execute(ADVERTISER, "set_budget", dict(params),
                                    confirmation_token=mkt_grant["confirmation_token"]),
            code="confirmation_mismatch", http=409, label="L3 cross-namespace")


def test_L3_concurrent_budget_change_applies_once():
    cid = _campaign(name="race")
    params = {"campaign_id": cid, "budget_cents": 6500, "currency": "usd"}
    tok = ad_asst.plan(ADVERTISER, "set_budget", params)["confirmation_token"]
    wins, losses = _race(
        lambda i: ad_asst.execute(ADVERTISER, "set_budget", dict(params),
                                  confirmation_token=tok), workers=8)
    assert len(wins) == 1, f"expected exactly 1 applied change, got {len(wins)}"
    assert all(isinstance(e, AdvertisingError) for e in losses), losses


def test_L3_failed_execution_still_consumes():
    cid = _campaign(name="failexec")
    # activate from 'draft' is an illegal transition: consumption happens first
    tok = ad_asst.plan(ADVERTISER, "activate_campaign",
                       {"campaign_id": cid})["confirmation_token"]
    try:
        ad_asst.execute(ADVERTISER, "activate_campaign", {"campaign_id": cid},
                        confirmation_token=tok)
        raise AssertionError("activate from draft should have failed")
    except AdvertisingError:
        pass
    d = cf.describe(ad_asst.CONFIRM_NAMESPACE, str(ADVERTISER), tok)
    assert d and d["status"] == "consumed", f"approval survived a failed execution: {d}"


# =============================================================================
# L4 — UNDX governed action engine (org-scoped)
# =============================================================================
def _undx_request(org, actor, action_type="marketplace.product.publish", pid="p1"):
    return undx_engine.record_action_request(
        org, actor, action_type, params={"product_id": pid})["request_id"]


def test_L4_replay_and_binding():
    org, actor = "orgConf", "seller:1"
    rid = _undx_request(org, actor)
    undx_engine.record_confirmation(org, rid, actor, "payload:p1")
    for label, args in (
        ("org",     ("orgOther", rid, actor, "payload:p1")),
        ("actor",   (org, rid, "seller:2", "payload:p1")),
        ("payload", (org, rid, actor, "payload:EDITED")),
    ):
        _expect(undx_engine.UndxActionsError,
                lambda a=args: undx_engine.redeem_confirmation(*a),
                label=f"L4 {label} binding")
    assert undx_engine.redeem_confirmation(org, rid, actor, "payload:p1")["redeemed"] is True
    _expect(undx_engine.UndxActionsError,
            lambda: undx_engine.redeem_confirmation(org, rid, actor, "payload:p1"),
            label="L4 replay")


def test_L4_expiry_is_mandatory_and_client_cannot_extend_it():
    """An omitted deadline used to mean "never expires". It must now be stamped."""
    org, actor = "orgConf", "seller:exp"
    rid = _undx_request(org, actor, pid="p-noexp")
    undx_engine.record_confirmation(org, rid, actor, "payload:p-noexp")  # no expires_at
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT expires_at FROM business_os_undx_confirmations "
            "WHERE request_id = ? ORDER BY created_at DESC LIMIT 1", (rid,)).fetchone()
    finally:
        conn.close()
    assert row is not None and str(row["expires_at"] or "").strip(), \
        "confirmation stored with no deadline — an approval that never expires"

    # a client-supplied far-future deadline is clamped down to the shared ceiling
    rid2 = _undx_request(org, actor, pid="p-far")
    undx_engine.record_confirmation(org, rid2, actor, "payload:p-far",
                                    expires_at="2999-01-01T00:00:00.000000Z")
    conn = db.connect()
    try:
        row2 = conn.execute(
            "SELECT expires_at FROM business_os_undx_confirmations "
            "WHERE request_id = ? ORDER BY created_at DESC LIMIT 1", (rid2,)).fetchone()
    finally:
        conn.close()
    assert str(row2["expires_at"]) < "2999", \
        f"client dictated its own approval lifetime: {row2['expires_at']}"

    # and an actually-expired approval is refused
    rid3 = _undx_request(org, actor, pid="p-old")
    undx_engine.record_confirmation(org, rid3, actor, "payload:p-old",
                                    expires_at="2000-01-01T00:00:00.000000Z")
    _expect(undx_engine.UndxActionsError,
            lambda: undx_engine.redeem_confirmation(org, rid3, actor, "payload:p-old"),
            label="L4 expiry")


def test_L4_legacy_row_with_null_deadline_fails_closed():
    """Rows written before expiry was mandatory must be refused, not honoured."""
    org, actor = "orgConf", "seller:legacy"
    rid = _undx_request(org, actor, pid="p-legacy")
    undx_engine.record_confirmation(org, rid, actor, "payload:p-legacy")
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE business_os_undx_confirmations SET expires_at = NULL "
            "WHERE request_id = ?", (rid,))
        conn.commit()
    finally:
        conn.close()
    _expect(undx_engine.UndxActionsError,
            lambda: undx_engine.redeem_confirmation(org, rid, actor, "payload:p-legacy"),
            label="L4 null deadline")


def test_L4_revocation():
    org, actor = "orgConf", "seller:rev"
    rid = _undx_request(org, actor, pid="p-rev")
    undx_engine.record_confirmation(org, rid, actor, "payload:p-rev")
    assert undx_engine.revoke_confirmation(org, rid, actor)["revoked"] is True
    _expect(undx_engine.UndxActionsError,
            lambda: undx_engine.redeem_confirmation(org, rid, actor, "payload:p-rev"),
            label="L4 revoked")
    assert undx_engine.revoke_confirmation(org, rid, actor)["revoked"] is False
    # cannot revoke another org's / actor's approval
    rid2 = _undx_request(org, actor, pid="p-rev2")
    undx_engine.record_confirmation(org, rid2, actor, "payload:p-rev2")
    assert undx_engine.revoke_confirmation("orgOther", rid2, actor)["revoked"] is False
    assert undx_engine.revoke_confirmation(org, rid2, "seller:x")["revoked"] is False
    assert undx_engine.redeem_confirmation(org, rid2, actor, "payload:p-rev2")["redeemed"] is True


def test_L4_concurrent_redemption_yields_exactly_one_winner():
    org, actor = "orgConf", "seller:race"
    rid = _undx_request(org, actor, pid="p-race")
    undx_engine.record_confirmation(org, rid, actor, "payload:p-race")
    wins, losses = _race(
        lambda i: undx_engine.redeem_confirmation(org, rid, actor, "payload:p-race"),
        workers=8)
    assert len(wins) == 1, f"expected exactly 1 redemption, got {len(wins)}"


# =============================================================================
# L5 — PulseSoc per-user tool confirmations (undx_architecture)
# =============================================================================
_PULSE_ACTION = {
    "action_id": "notifications.preference.update",
    "action_version": "4.0",
    "target_id": "global",
    "arguments": {"category": "global", "push": True},
}


def _pulse_conn():
    conn = db.connect()
    return conn, conn.cursor()


def test_L5_replay_and_actor_binding():
    conn, cur = _pulse_conn()
    try:
        g = undx_architecture.create_confirmation(cur, PULSE_USER, _PULSE_ACTION)
        conn.commit()
        tok = g["confirmation_token"]
        # another account cannot redeem it
        assert undx_architecture.consume_confirmation(cur, PULSE_OTHER, tok) is None, \
            "another account redeemed this approval"
        conn.commit()
        assert undx_architecture.consume_confirmation(cur, PULSE_USER, tok) is not None
        conn.commit()
        assert undx_architecture.consume_confirmation(cur, PULSE_USER, tok) is None, \
            "approval was replayable"
        conn.commit()
    finally:
        conn.close()


def test_L5_action_and_payload_binding_checked_before_consumption():
    """A wrong-action request must be refused WITHOUT burning a good approval."""
    conn, cur = _pulse_conn()
    try:
        g = undx_architecture.create_confirmation(cur, PULSE_USER, _PULSE_ACTION)
        conn.commit()
        tok = g["confirmation_token"]
        assert undx_architecture.consume_confirmation(
            cur, PULSE_USER, tok, expect_action_id="pulsesoc.send_message") is None, \
            "an approval for one action redeemed for another"
        conn.commit()
        wrong_hash = undx_architecture.argument_hash({"category": "global", "push": False})
        assert undx_architecture.consume_confirmation(
            cur, PULSE_USER, tok, expect_argument_hash=wrong_hash) is None, \
            "an edited payload redeemed the original approval"
        conn.commit()
        # the approval survived both mis-bound attempts and works for its own action
        right_hash = undx_architecture.argument_hash(_PULSE_ACTION["arguments"])
        row = undx_architecture.consume_confirmation(
            cur, PULSE_USER, tok,
            expect_action_id="notifications.preference.update",
            expect_argument_hash=right_hash)
        conn.commit()
        assert row is not None, "a correctly-bound redemption was refused"
    finally:
        conn.close()


def test_L5_expiry_and_revocation():
    conn, cur = _pulse_conn()
    try:
        # expiry: TTL is clamped, so rewrite the stored deadline (same comparison path)
        g = undx_architecture.create_confirmation(cur, PULSE_USER, _PULSE_ACTION,
                                                  ttl_seconds=10 ** 9)
        conn.commit()
        assert g["expires_at"] < "2100", f"TTL ceiling not enforced: {g['expires_at']}"
        cur.execute(
            "UPDATE pulse_ai_confirmations SET expires_at='2000-01-01T00:00:00+00:00' "
            "WHERE confirmation_id=?", (g["confirmation_id"],))
        conn.commit()
        assert undx_architecture.consume_confirmation(
            cur, PULSE_USER, g["confirmation_token"]) is None, "expired approval redeemed"
        conn.commit()

        # revocation
        g2 = undx_architecture.create_confirmation(cur, PULSE_USER, _PULSE_ACTION)
        conn.commit()
        assert undx_architecture.revoke_confirmation(
            cur, PULSE_USER, g2["confirmation_token"])["revoked"] is True
        conn.commit()
        assert undx_architecture.consume_confirmation(
            cur, PULSE_USER, g2["confirmation_token"]) is None, "revoked approval redeemed"
        conn.commit()
        # idempotent, and scoped to the owning account
        assert undx_architecture.revoke_confirmation(
            cur, PULSE_USER, g2["confirmation_token"])["revoked"] is False
        g3 = undx_architecture.create_confirmation(cur, PULSE_USER, _PULSE_ACTION)
        conn.commit()
        assert undx_architecture.revoke_confirmation(
            cur, PULSE_OTHER, g3["confirmation_token"])["revoked"] is False
        conn.commit()
        assert undx_architecture.consume_confirmation(
            cur, PULSE_USER, g3["confirmation_token"]) is not None
        conn.commit()
    finally:
        conn.close()


def test_L5_raw_token_never_persisted():
    conn, cur = _pulse_conn()
    try:
        g = undx_architecture.create_confirmation(cur, PULSE_USER, _PULSE_ACTION)
        conn.commit()
        cur.execute("SELECT * FROM pulse_ai_confirmations")
        blob = " ".join(str(dict(r)) for r in cur.fetchall())
        assert g["confirmation_token"] not in blob, \
            "raw confirmation token was persisted — a DB read grants approval"
    finally:
        conn.close()


# =============================================================================
# The execution-result contract (shared by every governed assistant)
# =============================================================================
def test_result_contract_unverified_is_not_ok():
    """``ok`` must never be True for a write canonical state did not confirm.

    The common client idiom is ``if resp["ok"]``. A shape that can report ok:True with
    verified:False turns an unconfirmed money movement into a reported success.
    """
    good = res.write_result("pay_order", True, {"status": "paid"}, {"order_id": "o1"})
    assert good["ok"] is True and good["verified"] is True, good
    assert "code" not in good, good

    bad = res.write_result("pay_order", False, {"status": "created"}, {"order_id": "o1"})
    assert bad["ok"] is False, "an unverified write reported ok"
    assert bad["verified"] is False and bad["write_applied"] is True, bad
    assert bad["code"] == res.CODE_VERIFICATION_FAILED, bad
    assert bad["retry_safe"] is False, "an unverified write must not invite a blind retry"

    # the HTTP envelope cannot report success either, by body or by status code
    st_ok, body_ok = res.envelope(good)
    assert st_ok == 200 and body_ok["ok"] is True, (st_ok, body_ok)
    st_bad, body_bad = res.envelope(bad)
    assert st_bad != 200, f"unverified write returned a 2xx: {st_bad}"
    assert body_bad["ok"] is False and body_bad["code"] == res.CODE_VERIFICATION_FAILED, body_bad


def test_result_contract_live_through_both_assistants():
    """The verified path really does flow through the shared contract, end to end."""
    pid = _product()
    oid = _order(pid)
    tok = mkt_asst.plan(BUYER, "pay_order", {"order_id": oid})["confirmation_token"]
    out = mkt_asst.execute(BUYER, "pay_order", {"order_id": oid}, confirmation_token=tok)
    for key in ("ok", "verified", "write_applied", "observed", "canonical_params"):
        assert key in out, f"marketplace result missing {key}: {out}"
    assert out["ok"] is out["verified"] is True, out

    cid = _campaign(name="contract")
    params = {"campaign_id": cid, "budget_cents": 2500, "currency": "usd"}
    tok2 = ad_asst.plan(ADVERTISER, "set_budget", params)["confirmation_token"]
    out2 = ad_asst.execute(ADVERTISER, "set_budget", dict(params), confirmation_token=tok2)
    for key in ("ok", "verified", "write_applied", "observed", "canonical_params"):
        assert key in out2, f"advertising result missing {key}: {out2}"
    assert out2["ok"] is out2["verified"] is True, out2

    # reads are unchanged
    r = mkt_asst.execute(BUYER, "order_status", {"order_id": oid})
    assert r["ok"] is True and r["write"] is False and "result" in r, r


def test_api_envelope_does_not_report_unverified_as_success():
    """Through the HTTP layer: a verified action is 200; an unverified one is not."""
    pid = _product()
    oid = _order(pid)
    tok = mkt_asst.plan(BUYER, "pay_order", {"order_id": oid})["confirmation_token"]
    st, body = mkt_api.assistant_execute(
        BUYER, {"tool": "pay_order", "params": {"order_id": oid},
                "confirmation_token": tok})
    assert st == 200 and body["ok"] is True and body["result"]["verified"] is True, (st, body)

    # simulate a write whose canonical state does not confirm it
    st2, body2 = res.envelope(
        res.write_result("pay_order", False, {"status": "created"}, {"order_id": oid}))
    assert st2 == 409 and body2["ok"] is False, (st2, body2)


def _run_standalone():
    setup_module()
    tests = [
        # L1 — canonical grant service
        test_L1_replay_refused,
        test_L1_expiry_enforced_and_cannot_be_disabled,
        test_L1_revocation,
        test_L1_actor_tool_and_payload_binding,
        test_L1_cross_namespace_isolation,
        test_L1_concurrent_redemption_yields_exactly_one_winner,
        test_L1_missing_token_is_required_not_mismatch,
        test_L1_raw_token_never_persisted,
        # L2 — marketplace assistant
        test_L2_replay_expiry_revocation,
        test_L2_actor_tool_and_payload_binding,
        test_L2_cross_namespace_token_from_advertising_refused,
        test_L2_concurrent_pay_charges_once,
        test_L2_failed_execution_still_consumes,
        # L3 — advertising assistant
        test_L3_replay_refused_regression,
        test_L3_expiry_and_revocation,
        test_L3_actor_tool_and_payload_binding,
        test_L3_cross_namespace_token_from_marketplace_refused,
        test_L3_concurrent_budget_change_applies_once,
        test_L3_failed_execution_still_consumes,
        # L4 — UNDX governed action engine
        test_L4_replay_and_binding,
        test_L4_expiry_is_mandatory_and_client_cannot_extend_it,
        test_L4_legacy_row_with_null_deadline_fails_closed,
        test_L4_revocation,
        test_L4_concurrent_redemption_yields_exactly_one_winner,
        # L5 — PulseSoc per-user confirmations
        test_L5_replay_and_actor_binding,
        test_L5_action_and_payload_binding_checked_before_consumption,
        test_L5_expiry_and_revocation,
        test_L5_raw_token_never_persisted,
        # execution-result contract
        test_result_contract_unverified_is_not_ok,
        test_result_contract_live_through_both_assistants,
        test_api_envelope_does_not_report_unverified_as_success,
    ]
    passed = 0
    for t in tests:
        t()
        print("PASS  " + t.__name__)
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    raise SystemExit(0 if _run_standalone() else 1)
