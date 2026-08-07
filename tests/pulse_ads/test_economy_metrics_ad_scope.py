"""Economy dashboard advertising figures count rows that actually exist.

`_count` and `_sum_cents` return 0 when their query raises, so the Economy
Command Center could not be taken down by one missing table. The cost was that a
query filtering on a column that does not exist produced exactly the same 0 as a
genuinely empty table.

Four advertising figures were in that state: the tables carry `account_id`, and
only `pulse_ad_accounts` carries `owner_user_id`, so every filter written as
`owner_user_id=?` against a wallet, transaction or refund table raised on every
call. A fifth read `pulse_ad_chargebacks`, which has never existed in any schema.

These tests assert the figures are non-zero when the underlying rows are there,
which is the thing a swallowed exception cannot fake.
"""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import dashboard_economy_command_center as economy  # noqa: E402


OWNER_ID = 4242
OTHER_OWNER_ID = 9999

SCHEMA = """
CREATE TABLE pulse_ad_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER,
    business_name TEXT
);
CREATE TABLE pulse_ad_wallets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    available_balance_cents INTEGER DEFAULT 0
);
CREATE TABLE pulse_ad_wallet_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    transaction_type TEXT,
    amount_cents INTEGER,
    status TEXT
);
CREATE TABLE pulse_ad_refunds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    amount_cents INTEGER,
    status TEXT
);
"""


class EconomyAdScopeTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.cur = self.conn.cursor()
        self.cur.execute("INSERT INTO pulse_ad_accounts (owner_user_id, business_name) VALUES (?, 'Mine')", (OWNER_ID,))
        self.account_id = self.cur.lastrowid
        self.cur.execute("INSERT INTO pulse_ad_accounts (owner_user_id, business_name) VALUES (?, 'Theirs')", (OTHER_OWNER_ID,))
        self.other_account_id = self.cur.lastrowid
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_owner_scoped_wallet_balance_is_not_a_swallowed_zero(self):
        self.cur.execute(
            "INSERT INTO pulse_ad_wallets (account_id, available_balance_cents) VALUES (?, 12345)", (self.account_id,)
        )
        self.cur.execute(
            "INSERT INTO pulse_ad_wallets (account_id, available_balance_cents) VALUES (?, 99999)", (self.other_account_id,)
        )
        self.conn.commit()

        total = economy._sum_cents(
            self.cur, "pulse_ad_wallets", "available_balance_cents", economy._AD_ACCOUNTS_FOR_OWNER, (OWNER_ID,)
        )
        self.assertEqual(total, 12345, "must find this owner's wallet and only this owner's wallet")

    def test_owner_scoped_refund_queue_counts_pending_rows(self):
        for status in ("requested", "pending", "refunded"):
            self.cur.execute(
                "INSERT INTO pulse_ad_refunds (account_id, amount_cents, status) VALUES (?, 500, ?)",
                (self.account_id, status),
            )
        self.cur.execute(
            "INSERT INTO pulse_ad_refunds (account_id, amount_cents, status) VALUES (?, 500, 'pending')",
            (self.other_account_id,),
        )
        self.conn.commit()

        queued = economy._count(
            self.cur,
            "pulse_ad_refunds",
            f"{economy._AD_ACCOUNTS_FOR_OWNER} AND lower(coalesce(status,'')) IN ('requested','pending','processing')",
            (OWNER_ID,),
        )
        self.assertEqual(queued, 2)

    def test_owner_scoped_failed_transactions_are_counted(self):
        for status in ("failed", "declined", "posted"):
            self.cur.execute(
                "INSERT INTO pulse_ad_wallet_transactions (account_id, transaction_type, amount_cents, status) VALUES (?, 'funding', 100, ?)",
                (self.account_id, status),
            )
        self.conn.commit()

        failures = economy._count(
            self.cur,
            "pulse_ad_wallet_transactions",
            f"{economy._AD_ACCOUNTS_FOR_OWNER} AND lower(coalesce(status,'')) IN ('failed','error','declined')",
            (OWNER_ID,),
        )
        self.assertEqual(failures, 2)

    def test_disputes_come_from_the_table_that_actually_records_them(self):
        """`pulse_ad_chargebacks` does not exist; disputed refunds do."""
        self.assertFalse(economy._table_exists(self.cur, "pulse_ad_chargebacks"))
        for status in ("disputed", "disputed", "refunded"):
            self.cur.execute(
                "INSERT INTO pulse_ad_refunds (account_id, amount_cents, status) VALUES (?, 2000, ?)",
                (self.account_id, status),
            )
        self.conn.commit()

        owner_disputes = economy._count(
            self.cur,
            "pulse_ad_refunds",
            f"{economy._AD_ACCOUNTS_FOR_OWNER} AND lower(coalesce(status,''))='disputed'",
            (OWNER_ID,),
        )
        self.assertEqual(owner_disputes, 2)

        platform_disputes = economy._count(self.cur, "pulse_ad_refunds", "lower(coalesce(status,''))='disputed'")
        self.assertEqual(platform_disputes, 2)

    def test_the_old_owner_user_id_filters_really_were_broken(self):
        """The bug, stated as a test: these columns do not exist.

        Each of these is how the metric was written before. Every one raises and
        is swallowed to a zero indistinguishable from an empty table.
        """
        self.cur.execute(
            "INSERT INTO pulse_ad_wallets (account_id, available_balance_cents) VALUES (?, 5000)", (self.account_id,)
        )
        self.cur.execute(
            "INSERT INTO pulse_ad_refunds (account_id, amount_cents, status) VALUES (?, 5000, 'pending')",
            (self.account_id,),
        )
        self.conn.commit()

        with self.assertRaises(sqlite3.OperationalError):
            self.cur.execute("SELECT SUM(available_balance_cents) FROM pulse_ad_wallets WHERE owner_user_id=?", (OWNER_ID,))
        with self.assertRaises(sqlite3.OperationalError):
            self.cur.execute("SELECT COUNT(*) FROM pulse_ad_refunds WHERE owner_user_id=?", (OWNER_ID,))

        # And the helper turns each of those into a confident zero.
        self.assertEqual(
            economy._sum_cents(self.cur, "pulse_ad_wallets", "available_balance_cents", "owner_user_id=?", (OWNER_ID,)),
            0,
        )
        self.assertEqual(economy._count(self.cur, "pulse_ad_refunds", "owner_user_id=?", (OWNER_ID,)), 0)

    def test_a_failed_query_is_logged_rather_than_silently_zeroed(self):
        with self.assertLogs(economy.logger, level="WARNING") as captured:
            economy._count(self.cur, "pulse_ad_refunds", "no_such_column=?", (OWNER_ID,))
        self.assertTrue(any("ECONOMY_METRIC_QUERY_FAILED" in line for line in captured.output))

    def test_a_genuinely_empty_table_is_not_logged_as_a_failure(self):
        with self.assertNoLogs(economy.logger, level="WARNING"):
            result = economy._count(
                self.cur,
                "pulse_ad_refunds",
                f"{economy._AD_ACCOUNTS_FOR_OWNER} AND lower(coalesce(status,''))='disputed'",
                (OWNER_ID,),
            )
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
