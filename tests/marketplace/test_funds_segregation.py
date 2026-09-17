"""The reserve is the only thing standing between a seller and PulseSoc's bank.

Under separate charges and transfers there is one Stripe balance and it pools
PulseSoc's revenue with money belonging to sellers, buyers, advertisers and a tax
authority. `services/marketplace_funds_segregation.py` computes how much of that
pool is not PulseSoc's, so the owner can set a platform payout schedule that never
withdraws past it.

Every test here is really one question asked from a different angle: *does the
reserve ever come out too small?* Too large costs PulseSoc a withdrawal it could
have made. Too small means the money is already gone and nobody knows yet. So the
asymmetry is deliberate, and the tests that matter most are the ones proving the
module errs upward — in particular
:func:`test_an_account_type_nobody_classified_is_still_held_for_someone_else`,
which is the whole reason the classification is inverted.

The module was first written with the opposite polarity: a list of designated
prefixes, everything else PulseSoc's. It missed ``mkt_order_escrow:``,
``advertiser:<uid>:wallet`` and ``ad_campaign_escrow:`` — captured buyer money and
prepaid advertiser funds — and reported them as withdrawable. That is what the AST
drift test exists to stop happening a second time.
"""

import ast
import os
import pathlib
import sys
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="mkt_funds_seg_"), "test.db")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import marketplace_funds_segregation as seg  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_ledger():
    """Each test owns the whole balance table; the reserve is a global sum."""
    ledger.ensure_schema()
    from services import db
    conn = db.connect()
    try:
        for table in ("ledger_entries", "ledger_transactions", "ledger_balances"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()
    yield


def post(key, amount, source, destination, currency="usd"):
    ledger.post_entry(idempotency_key=key, actor="test", amount_cents=amount,
                      currency=currency, entry_type="test", source=source,
                      destination=destination, allow_negative=True)


FUNDING = "external:stripe_marketplace"


# --------------------------------------------------------------------------
# the inverted classification
# --------------------------------------------------------------------------

def test_an_account_type_nobody_classified_is_still_held_for_someone_else():
    """The point of the inversion.

    An account prefix this module has never heard of is money of unknown
    ownership. Treating it as PulseSoc's would let an unreviewed feature quietly
    enlarge the withdrawable amount; treating it as designated costs at worst a
    withdrawal PulseSoc could have made.
    """
    post("u1", 4200, FUNDING, "some_future_holding:abc")
    report = seg.designated_funds("usd")
    assert report["designated_minor"] == 4200
    assert report["unclassified_minor"] == 4200
    assert report["unclassified_accounts"] == ["some_future_holding:abc"]


def test_the_three_accounts_the_first_version_of_this_module_missed():
    """Captured buyer money and prepaid advertiser funds are not PulseSoc's.

    ``mkt_order_escrow:`` is a buyer's money, refundable until the order settles.
    The advertiser wallet and campaign escrow are funds paid in advance for a
    service not yet delivered. A reserve that omits them says PulseSoc may
    withdraw money it would have to give back.
    """
    post("e1", 5000, "platform:marketplace_intake", "mkt_order_escrow:ord_1")
    post("e2", 2000, FUNDING, "advertiser:12:wallet")
    post("e3", 700, "advertiser:12:wallet", "ad_campaign_escrow:camp_9")
    report = seg.designated_funds("usd")
    assert report["order_escrow_minor"] == 5000
    assert report["advertiser_wallet_minor"] == 1300
    assert report["ad_escrow_minor"] == 700
    assert report["designated_minor"] == 7000
    # None of it landed in the default bucket: these are classified by name.
    assert report["unclassified_minor"] == 0


@pytest.mark.parametrize("account", sorted(seg.PLATFORM_OWNED))
def test_platform_accounts_are_not_designated(account):
    assert seg.is_designated(account) is False


@pytest.mark.parametrize("account", [
    "seller_payable:9", "seller_payout_pending:9", "liability:marketplace_tax",
    "mkt_order_escrow:ord_1", "ad_campaign_escrow:c1", "advertiser:3:wallet",
    "something_nobody_wrote_yet:1",
])
def test_everything_else_is_designated(account):
    assert seg.is_designated(account) is True


def test_a_platform_prefixed_name_is_not_trusted_on_its_prefix_alone():
    """Membership is by exact name, not by ``platform:``.

    If it matched the prefix, naming a liability account ``platform:tax_owed``
    would remove it from the reserve. The exact-name set means a new
    ``platform:*`` account is designated until someone classifies it.
    """
    assert seg.is_designated("platform:money_we_owe_someone") is True
    post("p1", 800, FUNDING, "platform:money_we_owe_someone")
    assert seg.designated_funds("usd")["designated_minor"] == 800


def test_external_accounts_are_not_a_balance_on_either_side():
    """The funding counterparty is not held money and is not PulseSoc's revenue."""
    post("x1", 1000, FUNDING, "platform:marketplace_revenue")
    assert seg.is_designated(FUNDING) is False
    assert seg.designated_funds("usd")["designated_minor"] == 0
    assert seg.platform_owned("usd") == 1000


# --------------------------------------------------------------------------
# no netting between people
# --------------------------------------------------------------------------

def test_one_sellers_overdraft_does_not_shrink_what_another_seller_is_owed():
    """The reserve sums positive balances only.

    A negative ``seller_payable:88`` is seller 88 having been overpaid. Netting it
    against seller 77's credit would fund seller 77's payout with seller 88's
    debt, which is the exact thing segregation exists to prevent. PulseSoc still
    has to hold all 9000 for seller 77.
    """
    post("n1", 9000, FUNDING, "seller_payable:77")
    post("n2", 400, "seller_payable:88", "seller_payout_pending:88")
    report = seg.designated_funds("usd")
    assert report["seller_payable_minor"] == 9000, "88's -400 must not reduce 77's credit"
    assert report["overdrawn_accounts"] == {"seller_payable:88": -400}
    assert report["overdrawn_minor"] == 400
    # The fenced payout is still money PulseSoc is holding and about to send.
    assert report["seller_payout_pending_minor"] == 400
    assert report["designated_minor"] == 9400


def test_an_overdrawn_account_is_reported_rather_than_silently_dropped():
    """It is a receivable. Excluding it from the total must not hide it."""
    post("n3", 250, "seller_payable:5", "platform:marketplace_revenue")
    report = seg.designated_funds("usd")
    assert report["designated_minor"] == 0
    assert report["overdrawn_minor"] == 250
    assert "seller_payable:5" in report["overdrawn_accounts"]


# --------------------------------------------------------------------------
# money that has already gone
# --------------------------------------------------------------------------

def test_settled_payouts_are_counted_on_neither_side():
    """``platform:payouts_settled`` is a record of absence.

    It grows as money leaves for a seller's bank. Counting it as designated would
    reserve against money that is no longer there; counting it as revenue would
    report PulseSoc as owning what it just paid away.
    """
    post("s1", 9000, FUNDING, "seller_payable:77")
    post("s2", 9000, "seller_payable:77", "seller_payout_pending:77")
    post("s3", 9000, "seller_payout_pending:77", "platform:payouts_settled")
    report = seg.designated_funds("usd")
    assert report["designated_minor"] == 0, "the money has left; nothing to reserve"
    assert seg.platform_owned("usd") == 0, "and PulseSoc did not earn it"


def test_the_payout_lifecycle_never_changes_the_total_until_the_money_leaves():
    """payable → pending is a relabel, not a release.

    Requesting a payout fences the funds but they are still in the platform
    balance, so the reserve must not fall when a seller hits request.
    """
    post("l1", 9000, FUNDING, "seller_payable:77")
    before = seg.designated_funds("usd")["designated_minor"]
    post("l2", 9000, "seller_payable:77", "seller_payout_pending:77")
    during = seg.designated_funds("usd")["designated_minor"]
    post("l3", 9000, "seller_payout_pending:77", "platform:payouts_settled")
    after = seg.designated_funds("usd")["designated_minor"]
    assert before == during == 9000
    assert after == 0


# --------------------------------------------------------------------------
# the solvency verdict
# --------------------------------------------------------------------------

def test_a_balance_above_what_is_owed_is_solvent_and_the_rest_is_withdrawable():
    post("a1", 9000, FUNDING, "seller_payable:77")
    post("a2", 1000, FUNDING, "platform:marketplace_revenue")
    verdict = seg.assess("usd", available_minor=10000)
    assert verdict["status"] == "solvent"
    assert verdict["must_retain_minor"] == 9000
    assert verdict["withdrawable_minor"] == 1000
    assert verdict["shortfall_minor"] == 0


def test_a_balance_below_what_is_owed_is_a_shortfall_not_a_negative_withdrawal():
    """Withdrawable clamps at zero.

    A negative withdrawable is arithmetically the shortfall with the sign flipped,
    and a caller that formats "you may withdraw X" would render it as a number.
    The shortfall gets its own field so it cannot be mistaken for an allowance.
    """
    post("b1", 9000, FUNDING, "seller_payable:77")
    verdict = seg.assess("usd", available_minor=5000)
    assert verdict["status"] == "shortfall"
    assert verdict["withdrawable_minor"] == 0
    assert verdict["shortfall_minor"] == 4000


def test_exactly_covering_what_is_owed_is_solvent_with_nothing_withdrawable():
    post("c1", 9000, FUNDING, "seller_payable:77")
    verdict = seg.assess("usd", available_minor=9000)
    assert verdict["status"] == "solvent"
    assert verdict["withdrawable_minor"] == 0
    assert verdict["shortfall_minor"] == 0


def test_withdrawable_is_never_derived_from_what_the_ledger_thinks_was_earned():
    """Stripe fees come out of the real balance and the ledger never sees them.

    Here the ledger says PulseSoc earned 1000, but Stripe is holding 9200 against
    9000 owed. The truthful answer is 200, not 1000 — so the verdict has to be
    anchored on the real balance.
    """
    post("d1", 9000, FUNDING, "seller_payable:77")
    post("d2", 1000, FUNDING, "platform:marketplace_revenue")
    verdict = seg.assess("usd", available_minor=9200)
    assert verdict["platform_owned_minor"] == 1000
    assert verdict["withdrawable_minor"] == 200


def test_without_a_stripe_key_the_answer_is_unknown_rather_than_a_guess():
    """A missing balance must not be indistinguishable from a healthy one.

    Defaulting to solvent invites a withdrawal; defaulting to shortfall would page
    someone every night on a dev box. ``unknown`` still carries the floor, which
    is the part that does not depend on the balance.
    """
    from services import payment_provider
    assert payment_provider._stripe_ready() is False, "test presumes no key configured"
    post("e4", 9000, FUNDING, "seller_payable:77")
    verdict = seg.assess("usd")
    assert verdict["status"] == "unknown"
    assert verdict["reason"] == "stripe_not_configured"
    assert verdict["must_retain_minor"] == 9000
    assert "withdrawable_minor" not in verdict, "no balance means no allowance to quote"


def test_an_unknown_verdict_still_reports_the_designated_breakdown():
    post("e5", 5000, "platform:marketplace_intake", "mkt_order_escrow:ord_2")
    verdict = seg.assess("usd")
    assert verdict["status"] == "unknown"
    assert verdict["order_escrow_minor"] == 5000


def test_the_balance_source_is_labelled_so_a_report_cannot_imply_stripe_said_it():
    post("f1", 100, FUNDING, "seller_payable:77")
    assert seg.assess("usd", available_minor=500)["balance_source"] == "caller"


def test_an_empty_ledger_owes_nothing_and_is_solvent():
    verdict = seg.assess("usd", available_minor=0)
    assert verdict["status"] == "solvent"
    assert verdict["designated_minor"] == 0


# --------------------------------------------------------------------------
# currency
# --------------------------------------------------------------------------

def test_a_reserve_in_one_currency_does_not_answer_for_another():
    """Stripe holds a separate balance per currency and they do not cross-fund."""
    post("g1", 9000, FUNDING, "seller_payable:77", currency="usd")
    post("g2", 4000, FUNDING, "seller_payable:78", currency="eur")
    assert seg.designated_funds("usd")["designated_minor"] == 9000
    assert seg.designated_funds("eur")["designated_minor"] == 4000


def test_the_currency_code_is_normalised_the_way_the_ledger_stores_it():
    post("g3", 9000, FUNDING, "seller_payable:77", currency="usd")
    assert seg.designated_funds("USD")["designated_minor"] == 9000


# --------------------------------------------------------------------------
# this module cannot move money
# --------------------------------------------------------------------------

def test_reading_the_reserve_writes_nothing_to_the_ledger():
    """It is a reporting module. If it could post, it could hide a shortfall."""
    post("h1", 9000, FUNDING, "seller_payable:77")
    from services import db
    conn = db.connect()
    try:
        before = conn.execute("SELECT COUNT(*) AS n FROM ledger_entries").fetchone()
        before = dict(before)["n"]
    finally:
        conn.close()

    seg.designated_funds("usd")
    seg.platform_owned("usd")
    seg.assess("usd", available_minor=10000)

    conn = db.connect()
    try:
        after = dict(conn.execute("SELECT COUNT(*) AS n FROM ledger_entries").fetchone())["n"]
    finally:
        conn.close()
    assert after == before


def test_the_module_contains_no_money_moving_call():
    """Structural, so it stays true as the module grows."""
    source = (REPO_ROOT / "services" / "marketplace_funds_segregation.py").read_text()
    tree = ast.parse(source)
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            called.add(fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", ""))
    for forbidden in ("post_entry", "create", "Transfer", "Payout", "modify"):
        assert forbidden not in called, f"a reporting module called {forbidden}"


# --------------------------------------------------------------------------
# drift: an account type added later must be classified
# --------------------------------------------------------------------------

def _account_prefixes_used_in_repo():
    """Every ledger account expression reachable from a posting, by AST.

    Resolves the two shapes the codebase actually uses — a module constant and a
    one-line helper returning an f-string — because almost no call site passes a
    literal. Both maps are built **per file**: ``INTAKE_ACCOUNT`` is defined in
    two modules with different values, and a repo-global map silently lets one
    overwrite the other.
    """
    def literal(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr) and node.values:
            head = node.values[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                return head.value
        return None

    found = {}
    for path in sorted(REPO_ROOT.rglob("*.py")):
        text = str(path)
        # `scripts/` is deliberately NOT excluded. Nothing there posts to the
        # ledger today, so including it is free, and a one-off script is exactly
        # the kind of place an unreviewed account name would first appear.
        if any(s in text for s in ("/.venv/", "/node_modules/", "/tests/")):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue

        helpers, consts = {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                returns = [n for n in ast.walk(node)
                           if isinstance(n, ast.Return) and n.value is not None]
                if len(returns) == 1:
                    value = literal(returns[0].value)
                    if value and ":" in value:
                        helpers[node.name] = value
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id.isupper():
                    value = literal(node.value)
                    if value and ":" in value:
                        consts[target.id] = value

        def resolve(node):
            value = literal(node)
            if value:
                return value
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                return helpers.get(name)
            if isinstance(node, ast.Name):
                return consts.get(node.id)
            if isinstance(node, ast.Attribute):
                return consts.get(node.attr)
            return None

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name != "post_entry":
                continue
            for keyword in node.keywords:
                if keyword.arg in ("source", "destination"):
                    value = resolve(keyword.value)
                    if value:
                        found.setdefault(value, set()).add(
                            str(path.relative_to(REPO_ROOT)))
    return found


def test_the_scanner_finds_the_postings_it_is_supposed_to_find():
    """Positive control.

    The drift test below passes trivially if the scan returns nothing, and it
    would return nothing after any refactor that stops it resolving helpers. These
    four are the shapes it has to handle: a bare literal, a module constant, a
    helper returning an f-string, and a constant shadowed across two files.
    """
    found = _account_prefixes_used_in_repo()
    assert "seller_payable:" in found
    assert "liability:marketplace_tax" in found
    assert "mkt_order_escrow:" in found
    assert "platform:marketplace_intake" in found
    assert "platform:events_intake" in found, "per-file const scoping regressed"


def test_every_account_the_repo_posts_to_is_explicitly_classified():
    """A new money-holding account must be classified, not defaulted.

    The runtime default is safe — an unknown account counts as designated — but
    safe is not the same as correct, and a prefix nobody has looked at might
    belong on the platform's side. This fails in CI so the decision is made once,
    deliberately, by whoever adds the account.
    """
    known = set(seg.PLATFORM_OWNED)
    known.update(prefix for prefix, _ in seg.DESIGNATED_BUCKETS)

    unclassified = {}
    for prefix, files in _account_prefixes_used_in_repo().items():
        if prefix in known:
            continue
        if prefix.startswith(seg.NOT_A_HELD_BALANCE):
            continue
        if any(prefix.startswith(k) for k in known):
            continue
        unclassified[prefix] = sorted(files)

    assert not unclassified, (
        "these ledger accounts are not classified in marketplace_funds_segregation; "
        "decide whether each is PulseSoc's own money or held for someone else: "
        + repr(unclassified))
