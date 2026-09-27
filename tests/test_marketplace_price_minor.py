"""`marketplace_listings.price_minor` must move with `price_label`.

`price_label` is free text -- "$35.00", "Free", "Request access" -- so the
storefront cannot sort by price without a companion integer. `price_minor` is
that integer. The pair only works if *every* writer of one writes the other,
and the failure mode when one drifts is quiet: the grid keeps rendering the
label, so the page looks right while "Price: low to high" orders by a number
that has not been true since the merchant last repriced.

Casting the label in SQL was the alternative and is not available. SQLite
returns 0.0 for `CAST('$35.00' AS REAL)` and Postgres raises
`invalid input syntax for type numeric`, so a cast-based ORDER BY passes the
whole local suite and 500s the production grid -- which is why the column
exists rather than a clever expression.

Two kinds of check here:

* a *static* sweep of the source, which is the only thing that can catch a
  write site added next year by someone who never read this file, and
* *behavioural* tests that the backfill and the parse agree with checkout.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Files that write `marketplace_listings`. Kept explicit rather than globbed:
# a glob would silently start passing the day a file is renamed, and the point
# of this test is to notice exactly that kind of move.
WRITE_SITE_FILES = (
    "bot.py",
    "services/business_os/suppliers/importer.py",
    "services/business_os/suppliers/drafts.py",
    "services/business_os/suppliers/revisions.py",
)

# An UPDATE/INSERT naming `price_label` in a statement that targets
# `marketplace_listings`. The SQL is assembled from adjacent string literals in
# several places, so this matches against the concatenated literal rather than
# against a single source line.
_TARGETS_LISTINGS = re.compile(r"\bmarketplace_listings\b", re.I)
_PRICE_LABEL_WRITE = re.compile(r"price_label\s*=\s*[?%]|price_label\s*,|,\s*price_label\b", re.I)
_PRICE_MINOR = re.compile(r"\bprice_minor\b", re.I)


def _sql_literals(path: pathlib.Path):
    """Every string constant in the file, with concatenations already folded.

    `ast` folds implicit adjacency (`"a" "b"`) into one Constant for us, which
    is what makes a multi-line SQL string readable here as a single unit.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            # f-strings: rebuild the literal skeleton so a dynamically
            # assembled `SET {assignments}` still shows its table name.
            parts = [
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            ]
            yield node.lineno, "".join(parts)


class PriceMinorTravelsWithPriceLabel(unittest.TestCase):
    def test_every_sql_write_of_price_label_also_writes_price_minor(self):
        offenders = []
        for rel in WRITE_SITE_FILES:
            path = REPO / rel
            self.assertTrue(path.exists(), f"{rel} has moved; update WRITE_SITE_FILES")
            for lineno, sql in _sql_literals(path):
                if not _TARGETS_LISTINGS.search(sql):
                    continue
                if not re.search(r"\b(UPDATE|INSERT)\b", sql, re.I):
                    continue
                if not _PRICE_LABEL_WRITE.search(sql):
                    continue
                if _PRICE_MINOR.search(sql):
                    continue
                offenders.append(f"{rel}:{lineno}  {' '.join(sql.split())[:120]}")
        self.assertEqual(
            [], offenders,
            "These statements write marketplace_listings.price_label without "
            "price_minor. The storefront sorts on price_minor, so the listing "
            "would keep displaying a price it no longer sorts by:\n  "
            + "\n  ".join(offenders),
        )

    def test_the_dynamic_update_in_drafts_refuses_a_half_written_pair(self):
        """The one write site whose columns are chosen at runtime.

        `drafts.update_draft` builds `SET a=?, b=?` from a dict, so the static
        sweep above cannot see which columns it names. It guards itself
        instead; this pins that the guard exists and is symmetric.
        """
        source = (REPO / "services/business_os/suppliers/drafts.py").read_text(encoding="utf-8")
        self.assertIn(
            '("price_label" in updates) != ("price_minor" in updates)',
            source,
            "drafts.update_draft assembles its UPDATE from a dict. Without this "
            "guard a future branch can add price_label to `updates` alone.",
        )


class BackfillDerivesTheCheckoutPrice(unittest.TestCase):
    """The number the grid sorts by must be the number the buyer is charged.

    `services/marketplace_seo.parse_price` is stricter and anchored, and using
    it here would be defensible in isolation -- but it disagrees with
    `parse_price_label_to_cents` on labels like "From $12.99", and the sort has
    to agree with the till, not with the SEO module.
    """

    @classmethod
    def setUpClass(cls):
        import bot  # noqa: PLC0415 -- importing bot runs init_db(); keep it lazy

        cls.bot = bot

    def test_parse_matches_checkout_for_the_labels_production_carries(self):
        # Drawn from the live table: 21 rows carry a "$N.NN" label and 26 are
        # blank. Both shapes have to land somewhere defensible.
        for label, expected in (
            ("$35.00", 3500),
            ("$38.00", 3800),
            ("$2,500.00", 250000),
            ("", 0),
            ("Free", 0),
            ("Request access", 0),
        ):
            with self.subTest(label=label):
                cents, _currency = self.bot.parse_price_label_to_cents(label)
                self.assertEqual(expected, cents)

    def test_backfill_is_idempotent_and_leaves_no_null_behind(self):
        conn = self.bot.db()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO marketplace_listings "
                "(seller_user_id, title, price_label, price_minor, status) "
                "VALUES (?,?,?,?,?)",
                (99_001, "price_minor backfill fixture", "$41.25", None, "draft"),
            )
            cur.execute(
                "SELECT id FROM marketplace_listings WHERE seller_user_id=? "
                "ORDER BY id DESC LIMIT 1",
                (99_001,),
            )
            listing_id = self.bot.db_service.row_values(cur.fetchone())[0]

            self.bot._backfill_marketplace_price_minor(cur)
            cur.execute("SELECT price_minor FROM marketplace_listings WHERE id=?", (listing_id,))
            self.assertEqual(4125, self.bot.db_service.row_values(cur.fetchone())[0])

            # A second pass must be a no-op rather than a re-derivation: the
            # row's label is authoritative only until a merchant overrides the
            # number, and a backfill that kept running would undo them.
            cur.execute(
                "UPDATE marketplace_listings SET price_minor=? WHERE id=?", (777, listing_id))
            self.bot._backfill_marketplace_price_minor(cur)
            cur.execute("SELECT price_minor FROM marketplace_listings WHERE id=?", (listing_id,))
            self.assertEqual(
                777, self.bot.db_service.row_values(cur.fetchone())[0],
                "the backfill re-derived a row it had already written",
            )

            cur.execute("DELETE FROM marketplace_listings WHERE id=?", (listing_id,))
            conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
