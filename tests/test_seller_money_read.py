"""The live seller money surface — balances, payout state, and the real ledger.

This suite exists to hold one line: **every number the Payments screen renders
must be a direct render of a backend financial record.** The way that line gets
broken is never dramatic. It is a cached column trusted after it went stale, a
client adding rows together because the server did not offer a total, a hold
rendered with a minus sign, a refund quietly dropped because it looked untidy.
So each test below pins one of those.

The tests also pin the *absences*. This platform has no release path, no payout
initiation, no stored bank destination and no per-order escrow, and the service
reports each of those as a machine-readable field. If somebody later builds one
of them, the corresponding test fails and the client's "ship it absent" decision
gets revisited — which is the whole point of asserting an absence.

    python tests/test_seller_money_read.py     # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="seller_money_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys  # noqa: E402
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import db as db_service  # noqa: E402
from services import seller_money as sm  # noqa: E402

SELLER = 9101
OTHER = 9102


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def setup_module(module=None):
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS creator_wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, wallet_type TEXT, currency TEXT DEFAULT 'USD',
            available_balance_cents INTEGER DEFAULT 0,
            pending_balance_cents INTEGER DEFAULT 0,
            lifetime_earnings_cents INTEGER DEFAULT 0,
            lifetime_fees_cents INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active', created_at TEXT, updated_at TEXT,
            UNIQUE(user_id, wallet_type, currency))""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS creator_ledger_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_id INTEGER, user_id INTEGER, related_user_id INTEGER,
            source_type TEXT, source_id TEXT, entry_type TEXT,
            amount_cents INTEGER DEFAULT 0, currency TEXT DEFAULT 'USD',
            status TEXT DEFAULT 'posted', description TEXT, provider TEXT,
            provider_reference TEXT, trace_id TEXT, metadata_json TEXT,
            created_at TEXT)""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS seller_payout_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, seller_type TEXT, provider TEXT DEFAULT 'stripe',
            connected_account_id TEXT, provider_account_id TEXT,
            onboarding_status TEXT DEFAULT 'not_started',
            payouts_enabled INTEGER DEFAULT 0, charges_enabled INTEGER DEFAULT 0,
            missing_requirements_json TEXT, requirements_json TEXT,
            last_checked_at TEXT, last_synced_at TEXT,
            created_at TEXT, updated_at TEXT, UNIQUE(user_id, seller_type))""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS seller_payouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, seller_type TEXT, amount_cents INTEGER DEFAULT 0,
            currency TEXT DEFAULT 'USD', status TEXT DEFAULT 'pending',
            provider TEXT DEFAULT 'stripe', provider_payout_id TEXT,
            transaction_ids_json TEXT, failure_reason TEXT,
            created_at TEXT, updated_at TEXT)""")
    conn.commit()
    conn.close()


def _reset():
    conn = db_service.connect()
    cur = conn.cursor()
    for table in ("creator_wallets", "creator_ledger_entries",
                  "seller_payout_accounts", "seller_payouts"):
        cur.execute("DELETE FROM " + table)
    conn.commit()
    conn.close()


def _wallet(user_id, wallet_type="merchant", currency="USD",
            stored_available=0, stored_pending=0):
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO creator_wallets (user_id, wallet_type, currency, "
        "available_balance_cents, pending_balance_cents, lifetime_earnings_cents,"
        " lifetime_fees_cents, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,0,'active','2026-01-01T00:00:00','2026-01-01T00:00:00')",
        (user_id, wallet_type, currency, stored_available, stored_pending,
         stored_available + stored_pending))
    wallet_id = cur.lastrowid
    conn.commit()
    conn.close()
    return wallet_id


def _entry(wallet_id, user_id, entry_type, amount, status="posted",
           currency="USD", description="", source_type="product_sale",
           source_id="55", related=0, provider_reference="pi_abcdef123456"):
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO creator_ledger_entries (wallet_id, user_id, related_user_id,"
        " source_type, source_id, entry_type, amount_cents, currency, status,"
        " description, provider, provider_reference, trace_id, metadata_json,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,'stripe',?,'t1','{}',"
        "'2026-01-02T00:00:00')",
        (wallet_id, user_id, related, source_type, source_id, entry_type,
         amount, currency, status, description, provider_reference))
    entry_id = cur.lastrowid
    conn.commit()
    conn.close()
    return entry_id


# --- 1. the balance is recomputed, never taken on trust ---------------------
def test_balance_is_recomputed_from_the_ledger_not_read_from_the_column():
    """A cached column that drifted is the classic way a screen lies.

    The wallet row here CLAIMS 999_99 available. The ledger says otherwise. The
    service must return the ledger's answer and flag the disagreement rather
    than quietly serving whichever number was nearer to hand.
    """
    _reset()
    wid = _wallet(SELLER, stored_available=99_900, stored_pending=0)
    _entry(wid, SELLER, "hold", 12_000, status="pending")

    ov = sm.seller_money_overview(SELLER)
    _assert(ov["available_cents"] == 0, (
        "no credit or release entry exists, so available must be 0, not the "
        "99900 the stale column claims; got %s" % ov["available_cents"]))
    _assert(ov["processing_cents"] == 12_000, ov)
    _assert(ov["reconciled"] is False,
            "the column and the ledger disagree — that must be visible")
    w = ov["wallets"][0]
    _assert(w["stored_available_cents"] == 99_900, w)
    _assert(w["available_cents"] == 0, w)


def test_reconciled_is_true_when_the_column_agrees():
    _reset()
    wid = _wallet(SELLER, stored_available=0, stored_pending=8_000)
    _entry(wid, SELLER, "hold", 8_000, status="pending")
    ov = sm.seller_money_overview(SELLER)
    _assert(ov["reconciled"] is True, ov)
    _assert(ov["processing_cents"] == 8_000, ov)


# --- 2. totals are summed server-side, across wallet types ------------------
def test_totals_union_every_seller_wallet_so_the_client_never_adds():
    """A seller can sell as a merchant AND teach. That is two wallets.

    If the service returned the parts and left the client to total them, the
    Payments screen and any other surface would each be one arithmetic bug away
    from disagreeing about the same seller's money.
    """
    _reset()
    merchant = _wallet(SELLER, "merchant")
    teacher = _wallet(SELLER, "teacher")
    _entry(merchant, SELLER, "hold", 5_000, status="pending")
    _entry(teacher, SELLER, "hold", 7_500, status="pending")

    ov = sm.seller_money_overview(SELLER)
    _assert(ov["processing_cents"] == 12_500, ov)
    _assert(len(ov["wallets"]) == 2, ov)
    _assert(sum(w["processing_cents"] for w in ov["wallets"])
            == ov["processing_cents"],
            "the total must be the sum of the parts it shipped")


def test_the_platform_wallet_is_never_summed_into_a_sellers_balance():
    """`creator_wallets` also holds the house wallet. It is not the seller's."""
    _reset()
    wid = _wallet(SELLER, "merchant")
    _entry(wid, SELLER, "hold", 3_000, status="pending")
    platform = _wallet(0, "platform")
    _entry(platform, 0, "fee", 400_000, status="posted")

    ov = sm.seller_money_overview(SELLER)
    _assert(ov["processing_cents"] == 3_000, ov)
    _assert(all(w["wallet_type"] != "platform" for w in ov["wallets"]), ov)


def test_one_seller_never_sees_another_sellers_money():
    _reset()
    mine = _wallet(SELLER, "merchant")
    theirs = _wallet(OTHER, "merchant")
    _entry(mine, SELLER, "hold", 1_000, status="pending")
    _entry(theirs, OTHER, "hold", 90_000, status="pending")

    ov = sm.seller_money_overview(SELLER)
    _assert(ov["processing_cents"] == 1_000, ov)
    feed = sm.seller_activity(SELLER)
    _assert(all(e["amount_cents"] != 90_000 for e in feed["entries"]), feed)


def test_currency_scopes_the_read():
    _reset()
    usd = _wallet(SELLER, "merchant", "USD")
    eur = _wallet(SELLER, "merchant", "EUR")
    _entry(usd, SELLER, "hold", 2_000, status="pending", currency="USD")
    _entry(eur, SELLER, "hold", 4_000, status="pending", currency="EUR")

    _assert(sm.seller_money_overview(SELLER, "USD")["processing_cents"] == 2_000,
            "USD read leaked the EUR wallet")
    _assert(sm.seller_money_overview(SELLER, "EUR")["processing_cents"] == 4_000,
            "EUR read leaked the USD wallet")


# --- 3. a hold is not an outflow --------------------------------------------
def test_a_hold_is_unsigned_and_never_rendered_as_a_loss():
    """The single most important row rule on the screen.

    Held money is the seller's money, waiting. Rendering it with a minus sign
    tells the seller they lost it. The sign is decided here, server-side, so two
    screens cannot decide it differently.
    """
    _reset()
    wid = _wallet(SELLER)
    _entry(wid, SELLER, "hold", 6_000, status="pending", description="Pending sale")
    row = sm.seller_activity(SELLER)["entries"][0]
    _assert(row["kind"] == "escrow", row)
    _assert(row["sign"] == "none", (
        "a hold must be unsigned; got %r" % row["sign"]))
    _assert(row["amount_cents"] == 6_000, row)


def test_refunds_and_fees_are_outflows_income_is_positive():
    _reset()
    wid = _wallet(SELLER)
    _entry(wid, SELLER, "refund", 2_500, status="posted")
    _entry(wid, SELLER, "fee", 400, status="posted")
    _entry(wid, SELLER, "credit", 9_000, status="posted")
    _entry(wid, SELLER, "payout", 5_000, status="posted")
    by_type = {e["entry_type"]: e for e in sm.seller_activity(SELLER)["entries"]}
    _assert(by_type["refund"]["sign"] == "-"
            and by_type["refund"]["kind"] == "refund", by_type["refund"])
    _assert(by_type["fee"]["sign"] == "-"
            and by_type["fee"]["kind"] == "spend", by_type["fee"])
    _assert(by_type["credit"]["sign"] == "+"
            and by_type["credit"]["kind"] == "income", by_type["credit"])
    _assert(by_type["payout"]["sign"] == "-"
            and by_type["payout"]["kind"] == "payout", by_type["payout"])


def test_an_unknown_entry_type_gets_no_sign_rather_than_a_guessed_one():
    """Guessing the direction of an unfamiliar entry is worse than declining to.

    An entry type nobody has defined yet renders with its real name, its real
    amount and no arrow. A wrong arrow on a money row is a wrong statement about
    whether the seller gained or lost.
    """
    _reset()
    wid = _wallet(SELLER)
    _entry(wid, SELLER, "chargeback_provision", 1_200, status="posted")
    row = sm.seller_activity(SELLER)["entries"][0]
    _assert(row["kind"] == "other", row)
    _assert(row["sign"] == "none", row)
    _assert(row["entry_type"] == "chargeback_provision", row)


# --- 4. rows are real, keep their status, and never disappear ----------------
def test_failed_and_disputed_rows_stay_in_the_feed_with_their_real_status():
    _reset()
    wid = _wallet(SELLER)
    _entry(wid, SELLER, "hold", 4_000, status="pending")
    _entry(wid, SELLER, "refund", 4_000, status="disputed")
    _entry(wid, SELLER, "payout", 1_000, status="failed")
    statuses = {e["status"] for e in sm.seller_activity(SELLER)["entries"]}
    _assert({"pending", "disputed", "failed"} <= statuses, statuses)


def test_every_row_carries_the_ledgers_own_primary_key():
    _reset()
    wid = _wallet(SELLER)
    ids = [_entry(wid, SELLER, "hold", 100 + i, status="pending") for i in range(5)]
    feed = sm.seller_activity(SELLER)
    _assert([e["id"] for e in feed["entries"]] == sorted(ids, reverse=True),
            "ids must be the real row ids, newest first")
    _assert(all(isinstance(e["id"], int) and e["id"] > 0 for e in feed["entries"]),
            feed)


def test_title_falls_back_without_inventing_a_counterparty_or_item():
    _reset()
    wid = _wallet(SELLER)
    _entry(wid, SELLER, "hold", 500, status="pending", description="",
           source_type="course_sale")
    row = sm.seller_activity(SELLER)["entries"][0]
    _assert(row["title"] == "course sale hold", row)
    for invented in ("buyer_name", "item_title", "counterparty_name",
                     "buyer", "listing"):
        _assert(invented not in row,
                "activity invented %r — this table stores no such field" % invented)


# --- 5. pagination is keyset on id, which same-second rows require -----------
def test_pagination_is_keyset_and_does_not_skip_same_timestamp_rows():
    """Every seeded row shares one `created_at`, on purpose.

    `created_at` here is a second-resolution string the application writes, so
    a batch of entries from one webhook genuinely shares a value. A cursor on
    that column would drop whichever rows straddled the page boundary. Paging
    the whole feed and counting is the only honest way to prove it does not.
    """
    _reset()
    wid = _wallet(SELLER)
    for i in range(25):
        _entry(wid, SELLER, "hold", 1_000 + i, status="pending")

    seen, cursor, pages = [], None, 0
    while True:
        page = sm.seller_activity(SELLER, limit=7, before_cursor=cursor)
        seen.extend(e["id"] for e in page["entries"])
        pages += 1
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
        _assert(cursor is not None, "has_more with no cursor is an infinite loop")
        _assert(pages < 20, "pagination did not terminate")

    _assert(len(seen) == 25, "paged %d of 25 rows" % len(seen))
    _assert(len(set(seen)) == 25, "a row was returned on two pages")
    _assert(seen == sorted(seen, reverse=True), "order broke across pages")


def test_last_page_reports_no_cursor():
    _reset()
    wid = _wallet(SELLER)
    for _ in range(3):
        _entry(wid, SELLER, "hold", 100, status="pending")
    page = sm.seller_activity(SELLER, limit=10)
    _assert(page["has_more"] is False and page["next_cursor"] is None, page)


def test_a_junk_cursor_is_a_client_error_not_a_crash():
    _reset()
    _wallet(SELLER)
    for junk in ("abc", "../../etc/passwd", "-1", "1;DROP TABLE"):
        try:
            sm.seller_activity(SELLER, before_cursor=junk)
        except sm.SellerMoneyError:
            continue
        raise AssertionError("cursor %r was accepted" % junk)


def test_limit_is_clamped_not_trusted():
    _reset()
    wid = _wallet(SELLER)
    for _ in range(5):
        _entry(wid, SELLER, "hold", 100, status="pending")
    _assert(len(sm.seller_activity(SELLER, limit=100_000)["entries"]) == 5,
            "an absurd limit must clamp, not fetch the table")
    _assert(len(sm.seller_activity(SELLER, limit=0)["entries"]) == 1,
            "limit 0 must clamp up to 1, not return everything")
    _assert(len(sm.seller_activity(SELLER, limit="junk")["entries"]) == 5,
            "a junk limit falls back to the default")


def test_a_seller_with_no_wallet_gets_an_empty_feed_not_an_error():
    _reset()
    feed = sm.seller_activity(SELLER)
    _assert(feed["entries"] == [] and feed["has_more"] is False, feed)
    ov = sm.seller_money_overview(SELLER)
    _assert(ov["has_wallet"] is False and ov["available_cents"] == 0, ov)


# --- 6. nothing sensitive reaches the client --------------------------------
def test_the_full_stripe_identifier_never_leaves_the_server():
    _reset()
    _wallet(SELLER)
    conn = db_service.connect()
    conn.cursor().execute(
        "INSERT INTO seller_payout_accounts (user_id, seller_type, provider,"
        " connected_account_id, onboarding_status, payouts_enabled,"
        " charges_enabled, missing_requirements_json, created_at, updated_at)"
        " VALUES (?,'merchant','stripe','acct_1SuperSecret9999','complete',1,1,"
        "'[]','2026-01-01','2026-01-01')", (SELLER,))
    conn.commit()
    conn.close()

    method = sm.seller_money_overview(SELLER)["payout_method"]
    _assert(method["destination_masked"] == "····9999", method)
    blob = repr(method)
    _assert("acct_1SuperSecret9999" not in blob,
            "the full connected-account id reached the client payload")
    _assert("SuperSecret" not in blob, blob)
    _assert(method["bank_destination"] == "not_stored", (
        "there is no bank account number in this platform, and claiming one "
        "would be a fabricated masked destination"))


def test_provider_references_in_the_feed_are_masked_too():
    _reset()
    wid = _wallet(SELLER)
    _entry(wid, SELLER, "hold", 100, status="pending",
           provider_reference="pi_3QsecretPaymentIntent7788")
    row = sm.seller_activity(SELLER)["entries"][0]
    _assert("secretPaymentIntent" not in repr(row), row)
    _assert(row["provider_reference"] == "····7788", row)


def test_no_payout_method_is_none_rather_than_a_hollow_object():
    """The design says this state outranks every other prompt, so it has to be
    unambiguous. `None` cannot be mistaken for a method with empty fields."""
    _reset()
    _wallet(SELLER)
    _assert(sm.seller_money_overview(SELLER)["payout_method"] is None,
            "a seller who never onboarded has no method object")


# --- 7. payout state comes from real rows -----------------------------------
def _payout(user_id, amount, status, failure="", created_at="2026-02-01"):
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO seller_payouts (user_id, seller_type, amount_cents,"
        " currency, status, provider, provider_payout_id, failure_reason,"
        " created_at, updated_at) VALUES (?,'merchant',?,'USD',?,'stripe',"
        " 'po_abc1234',?,?,?)",
        (user_id, amount, status, failure, created_at, created_at))
    conn.commit()
    conn.close()


def test_payout_in_flight_and_failure_are_read_from_records():
    _reset()
    _wallet(SELLER)
    _payout(SELLER, 15_000, "failed", "account_closed", created_at="2026-02-01")
    _payout(SELLER, 20_000, "in_transit", created_at="2026-02-05")
    ov = sm.seller_money_overview(SELLER)
    _assert(ov["payout_in_flight"]["amount_cents"] == 20_000, ov)
    _assert(ov["last_failed_payout"]["failure_reason"] == "account_closed", ov)
    _assert(ov["last_failed_payout"]["amount_cents"] == 15_000, ov)


def test_no_payouts_means_both_states_are_absent():
    _reset()
    _wallet(SELLER)
    ov = sm.seller_money_overview(SELLER)
    _assert(ov["payout_in_flight"] is None and ov["last_failed_payout"] is None, ov)


# --- 8. the absences, asserted so that building one breaks this test --------
def test_the_missing_capabilities_are_declared_not_omitted():
    _reset()
    _wallet(SELLER)
    ov = sm.seller_money_overview(SELLER)
    _assert(ov["release_path"] == "none_in_product", ov)
    _assert(ov["payout_initiation"] == "unsupported", ov)
    _assert(ov["instant_payout"] == "unsupported", ov)
    _assert(ov["statements"] == "unsupported", ov)
    _assert(ov["tax_documents"] == "unsupported", ov)
    _assert(ov["escrow"]["supported"] is False, ov)
    _assert(ov["ad_wallet_source"] == "pulse_ads_wallet_endpoint", ov)
    for invented in ("next_payout_at", "payout_schedule", "instant_fee_cents",
                     "estimated_arrival", "tax_year", "statement_url"):
        _assert(invented not in ov,
                "overview invented %r — there is no backend source for it" % invented)


def test_no_release_path_exists_anywhere_in_the_codebase():
    """The finding behind ``release_path``, checked against the tree.

    ``available = max(0, credits - debits)``. If nothing ever writes a ``credit``
    or ``release`` entry, available is structurally zero and the seller's money
    stays in Processing forever. That is a real, reportable property of this
    platform — not a bug in this module — and the screen has to say something
    true about it. If somebody builds the release path, this test fails and the
    hero's explanatory copy has to be rewritten.
    """
    import glob
    import re
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    targets = [os.path.join(root, "bot.py")] + glob.glob(
        os.path.join(root, "services", "*.py"))
    writers = []
    pattern = re.compile(r"""entry_type\s*=\s*["'](credit|release)["']""")
    for path in targets:
        if os.path.basename(path) == "seller_money.py":
            continue  # the reader names the types; it does not write them
        with open(path, encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                if pattern.search(line):
                    writers.append("%s:%d" % (os.path.basename(path), lineno))
    _assert(not writers, (
        "a credit/release writer now exists (%s), so available balances can "
        "become non-zero. `release_path: none_in_product` is no longer true and "
        "the Payments hero copy must be revisited." % ", ".join(writers)))


def test_this_module_cannot_move_money():
    """A read module that could also write would be a second payment path.

    Enforced against the source rather than assumed, and docstrings are stripped
    first so this file stays free to *describe* the write side in prose.
    """
    import ast
    path = os.path.join(os.path.dirname(__file__), "..", "services",
                        "seller_money.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Module)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree)
    code = "\n".join(line.split("#")[0] for line in code.splitlines()).upper()
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "COMMIT()",
                      "DROP ", "POST_ENTRY", "RECONCILE_WALLET", "ENSURE_WALLET"):
        _assert(forbidden not in code,
                "seller_money.py contains %r — it must be read-only" % forbidden)


if __name__ == "__main__":
    setup_module()
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok  %s" % fn.__name__)
    print("\n%d passed" % len(fns))
