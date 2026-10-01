"""Advertising — the ad wallet as one shared object.

The Payments money hub and the Advertising screen both render "ad wallet". The
mission's rule is that divergence between those two screens is a bug, so there
must be exactly one place that answers the question. That place is
``funding.wallet_view``, and these tests pin the properties that make sharing it
safe:

  1. the balance is a real ledger balance, not budget-minus-spend arithmetic
  2. reserving a campaign budget moves cents OUT of the wallet, so the wallet is
     already net of reservations and a caller must not subtract again
  3. ``reserved_cents`` is summed server-side from ledger balances, scoped to the
     caller's own campaigns — one advertiser never sees another's reservation
  4. releasing restores the wallet exactly
  5. the account name has one owner (``wallet_account``), so two screens cannot
     build the string differently and drift apart
  6. top-up is reported as unsupported rather than omitted — the wallet has no
     in-product funding path, and a client must ship that affordance absent
     rather than render a button that cannot work
  7. the whole thing is dark when the flag is off

    python tests/business_os/test_advertising_wallet_read.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_adwallet_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys  # noqa: E402
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.advertising import service as ad  # noqa: E402
from services.business_os.advertising import funding as adf  # noqa: E402
from services.business_os.advertising import api as adapi  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402

ADMIN = 8
ACTIVE = {"account_status": "active", "access_enabled": 1}
_uid_seq = [7100]


def setup_module(module=None):
    ad.ensure_schema()
    ledger.ensure_schema()


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _new_owner():
    _uid_seq[0] += 1
    return _uid_seq[0]


def _approved_campaign(owner):
    ad.upsert_advertiser(owner)
    ad.set_advertiser_status(owner, "approved", actor=ADMIN)
    s, b = adapi.create_draft(
        owner, {"name": "C", "objective": "traffic",
                "destination_url": "https://ex.com"}, context=ACTIVE)
    _assert(s == 201, (s, b))
    cid = b["campaign"]["campaign_id"]
    s, b = adapi.submit(owner, cid, context=ACTIVE)
    _assert(s == 200, (s, b))
    s, b = adapi.admin_review(ADMIN, cid, "approve")
    _assert(s == 200, (s, b))
    return cid


def _seed(uid, cents):
    """Fund a wallet by direct ledger posting.

    Note what this helper is: a TEST fixture reaching into the ledger. There is
    no product path that does this — which is exactly the finding that
    ``funding_source: none_in_product`` reports.
    """
    ledger.post_entry(
        idempotency_key="seed:%s:%s" % (uid, os.urandom(5).hex()),
        actor="test-seed", amount_cents=cents, currency="usd",
        entry_type="seed_deposit", source="platform:ad_funding_source",
        destination=adf.wallet_account(uid))


def _reserve(owner, cid, cents, key):
    return adapi.reserve(owner, cid, {"amount_cents": cents, "currency": "usd",
                                      "idempotency_key": key}, context=ACTIVE)


# --- 1. the balance is the ledger's, not a derived figure -------------------
def test_balance_is_the_ledger_balance_itself():
    uid = _new_owner()
    view = adf.wallet_view(uid)
    _assert(view["balance_cents"] == 0,
            "an unfunded wallet is 0, not None and not an error")

    _seed(uid, 25_000)
    view = adf.wallet_view(uid)
    _assert(view["balance_cents"] == ledger.get_balance(adf.wallet_account(uid), "usd"),
            "the view must return the ledger balance, not recompute one")
    _assert(view["balance_cents"] == 25_000, view)
    _assert(view["reserved_cents"] == 0, view)


# --- 2. the wallet is already net of reservations ---------------------------
def test_reserving_moves_money_out_of_the_wallet_not_alongside_it():
    uid = _new_owner()
    _seed(uid, 30_000)
    cid = _approved_campaign(uid)
    s, b = adapi.set_budget(uid, cid, {"budget_cents": 12_000, "currency": "usd"},
                            context=ACTIVE)
    _assert(s == 200, (s, b))
    s, b = _reserve(uid, cid, 12_000, "k-res-1")
    _assert(s == 200, (s, b))

    view = adf.wallet_view(uid)
    _assert(view["balance_cents"] == 18_000, (
        "spendable must already exclude the reservation; got %s" % view))
    _assert(view["reserved_cents"] == 12_000, view)
    _assert(view["reserved_campaign_count"] == 1, view)
    # The two figures are separate money, not the same money counted twice.
    _assert(view["balance_cents"] + view["reserved_cents"] == 30_000, view)


def test_reserved_total_sums_across_campaigns():
    uid = _new_owner()
    _seed(uid, 50_000)
    for i, amount in enumerate((5_000, 7_000, 9_000)):
        cid = _approved_campaign(uid)
        adapi.set_budget(uid, cid, {"budget_cents": amount, "currency": "usd"},
                         context=ACTIVE)
        s, b = _reserve(uid, cid, amount, "k-multi-%d-%d" % (uid, i))
        _assert(s == 200, (s, b))

    view = adf.wallet_view(uid)
    _assert(view["reserved_cents"] == 21_000, view)
    _assert(view["reserved_campaign_count"] == 3, view)
    _assert(view["balance_cents"] == 29_000, view)
    # Summed server-side: the client is handed the total, not the parts to add.
    independent = sum(ledger.get_balance(a, "usd")
                      for a in view["accounts"]["campaign_escrow"])
    _assert(independent == view["reserved_cents"], (independent, view))


# --- 3. one advertiser never sees another's money ---------------------------
def test_wallets_are_isolated_per_advertiser():
    a, b_uid = _new_owner(), _new_owner()
    _seed(a, 40_000)
    _seed(b_uid, 1_000)
    cid = _approved_campaign(a)
    adapi.set_budget(a, cid, {"budget_cents": 10_000, "currency": "usd"},
                     context=ACTIVE)
    _reserve(a, cid, 10_000, "k-iso-%d" % a)

    other = adf.wallet_view(b_uid)
    _assert(other["balance_cents"] == 1_000, other)
    _assert(other["reserved_cents"] == 0,
            "another advertiser's reservation must not appear here")
    _assert(other["accounts"]["campaign_escrow"] == [], other)
    _assert(other["account"] != adf.wallet_account(a), other)


# --- 4. release restores exactly ---------------------------------------------
def test_release_returns_the_money_to_spendable():
    uid = _new_owner()
    _seed(uid, 20_000)
    cid = _approved_campaign(uid)
    adapi.set_budget(uid, cid, {"budget_cents": 8_000, "currency": "usd"},
                     context=ACTIVE)
    _reserve(uid, cid, 8_000, "k-rel-%d" % uid)
    _assert(adf.wallet_view(uid)["balance_cents"] == 12_000,
            "reservation should have moved 8000 out of spendable")

    s, b = adapi.release(uid, cid, {"idempotency_key": "k-rel-back-%d" % uid},
                         context=ACTIVE)
    _assert(s == 200, (s, b))
    view = adf.wallet_view(uid)
    _assert(view["balance_cents"] == 20_000, view)
    _assert(view["reserved_cents"] == 0, view)
    _assert(view["reserved_campaign_count"] == 0, view)


# --- 5. the account name has exactly one owner ------------------------------
def test_account_name_is_exported_so_no_screen_rebuilds_it():
    uid = _new_owner()
    _assert(adf.wallet_account(uid) == adf._wallet_account(uid),
            "the public name must be the same string the writer uses")
    _assert(adf.wallet_view(uid)["account"] == adf.wallet_account(uid),
            "the view must report the account it read, so it is auditable")


# --- 6. top-up absence is stated, not left to be discovered -----------------
def test_topup_is_reported_unsupported_rather_than_omitted():
    view = adf.wallet_view(_new_owner())
    _assert(view["funding_source"] == "none_in_product", view)
    _assert(view["auto_topup"] == "unsupported", view)
    for invented in ("next_topup_at", "topup_threshold_cents",
                     "topup_amount_cents", "payment_method", "auto_topup_enabled"):
        _assert(invented not in view,
                "wallet_view invented %r — there is no backend source for it"
                % invented)


def test_no_product_path_credits_the_wallet():
    """The finding above, asserted against the source rather than trusted.

    Only two postings in the funding service touch a wallet account and both of
    them have a campaign escrow on the other side. If someone later adds an
    external funding source, this test fails and the wallet's ``funding_source``
    claim has to be revisited — which is the point.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "services",
                            "business_os", "advertising", "funding.py"),
               encoding="utf-8").read()
    # Strip docstrings/comments so prose about funding is not mistaken for code.
    import ast
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Module)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree)
    code = "\n".join(l.split("#")[0] for l in code.splitlines())

    posts = code.count("post_entry(")
    _assert(posts == 2, (
        "funding.py posts %d ledger entries; this test knows about 2 (reserve "
        "and release). A new posting may be an external top-up, which would "
        "make wallet_view's funding_source claim false." % posts))
    _assert("destination=_wallet_account" in code, "release must credit the wallet")
    _assert(code.count("destination=_wallet_account") == 1, (
        "more than one posting credits the wallet — if the new one is an "
        "external top-up then funding_source is no longer 'none_in_product'"))


# --- 7. dark when the flag is off -------------------------------------------
def test_wallet_is_dark_when_the_flag_is_off():
    os.environ["BUSINESS_OS_ADVERTISING"] = "off"
    try:
        status, body = adapi.get_wallet(_new_owner())
        _assert(status == 404 and body["ok"] is False, (status, body))
    finally:
        os.environ["BUSINESS_OS_ADVERTISING"] = "on"


def test_controller_wraps_the_view_under_a_stable_key():
    uid = _new_owner()
    _seed(uid, 3_300)
    status, body = adapi.get_wallet(uid, currency="usd")
    _assert(status == 200 and body["ok"] is True, (status, body))
    _assert(body["wallet"]["balance_cents"] == 3_300, body)
    _assert(body["wallet"]["currency"] == "usd", body)


if __name__ == "__main__":
    setup_module()
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok  %s" % fn.__name__)
    print("\n%d passed" % len(fns))
