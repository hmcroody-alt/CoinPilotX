"""Tests for scripts/qa_account_classification.py.

These tests exist because the previous version of the classifier passed review
and was still wrong against production, in a way no test caught: it treated any
``subscriptions`` row as proof of a payment relationship, and PulseSoc
auto-provisions a trial subscription row for every account at signup. The result
was a script that classified 100% of matched production users as
"has money, do not touch" and therefore recommended hiding nobody.

So the tests here are built around two properties, not around output shape:

1. **Financial protection must discriminate.** A trial row must not protect, a
   real Stripe customer id must. A protective check that fires on every row is
   indistinguishable from no check at all, so each financial test asserts both
   the positive and the negative case.

2. **Classification must not equal write scope.** The script may label an
   account any number of ways, but ``--apply-hide`` may only ever write rows
   labelled HIDE_FROM_DISCOVERY. Tests 10-12 assert the labels that must survive
   a write untouched, because that is where a widening bug would do real damage:
   hiding a paying customer is a customer-facing outage, not a tidy-up.

The DB-backed tests run the script as a subprocess against a temp SQLite file,
which is the same entry point an operator uses. Unit tests cover `classify()`
directly where no database is needed.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "qa_account_classification.py")

sys.path.insert(0, REPO_ROOT)

_spec = importlib.util.spec_from_file_location("qa_account_classification", SCRIPT)
qa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qa)


def _row(user_id, username="", email="", display_name="", account_status="active", **extra):
    row = {
        "user_id": user_id,
        "username": username,
        "email": email,
        "display_name": display_name,
        "account_status": account_status,
    }
    row.update(extra)
    return row


class ClassifyUnitTest(unittest.TestCase):
    """`classify()` in isolation — no database, no financial lookups."""

    def label(self, row, financial_reason="", holds=frozenset()):
        return qa.classify(row, financial_reason, holds)[0]

    # --- 5/6/7/8: verified production QA patterns are detected ---------------

    def test_pulseqa_pattern_matches(self):
        """`pulseqa802505` — the exact production account the old `qa\\_%`
        pattern missed, because it required a literal `qa_` prefix."""
        self.assertEqual(
            self.label(_row(11, "pulseqa802505", "cherieroody+pulseqa1780802505@gmail.com")),
            "HIDE_FROM_DISCOVERY",
        )

    def test_iphone16qa_pattern_matches(self):
        """Matched on username alone: this account has an empty email in prod."""
        self.assertEqual(
            self.label(_row(30, "iphone16qa_83885056", "")),
            "HIDE_FROM_DISCOVERY",
        )

    def test_undxreleaseqa_pattern_matches(self):
        self.assertEqual(
            self.label(_row(34, "undxreleaseqa_85611099", "")),
            "HIDE_FROM_DISCOVERY",
        )

    def test_coinpilotx_test_email_matches(self):
        """Users 5 and 6 in production have *empty usernames* — the email domain
        is the only signal available, so the email patterns must stand alone."""
        self.assertEqual(
            self.label(_row(5, "", "phase2-media-test-1780283763@coinpilotx.test")),
            "HIDE_FROM_DISCOVERY",
        )

    def test_reserved_example_domains_match(self):
        for email in ("john.doe@example.com", "x@example.org", "y@example.test"):
            with self.subTest(email=email):
                self.assertEqual(self.label(_row(99, "someone", email)), "HIDE_FROM_DISCOVERY")

    # --- weak signals must NOT auto-hide -------------------------------------

    def test_weak_signal_alone_is_ambiguous_not_hidden(self):
        """"test" inside a name on a real consumer mailbox is a hint, not proof.

        This is the guard against the opposite failure mode from the original
        bug: over-matching and hiding a real user because their display name
        happens to contain "test".
        """
        label, reason = qa.classify(_row(35, "TestMeNow", "someone@gmail.com"), "", frozenset())
        self.assertEqual(label, "AMBIGUOUS")
        self.assertIn("human review", reason)

    def test_app_review_pattern_outranks_everything(self):
        self.assertEqual(self.label(_row(50, "appreview_apple", "review@example.com")),
                         "APP_REVIEW_REQUIRED")

    # --- 9: manual exclusion --------------------------------------------------

    def test_ambiguous_real_account_can_be_manually_excluded(self):
        """A strong-pattern account can still be pinned for human review.

        Without this, the only way to spare a specific account would be to
        weaken a pattern globally, which would spare a whole class of accounts
        as a side effect.
        """
        row = _row(77, "pulseqa999999", "")
        self.assertEqual(self.label(row), "HIDE_FROM_DISCOVERY")
        label, reason = qa.classify(row, "", frozenset({77}))
        self.assertEqual(label, "AMBIGUOUS")
        self.assertIn("manual hold", reason)

    def test_default_manual_holds_cover_the_owner_flagged_ids(self):
        self.assertEqual(set(qa.DEFAULT_MANUAL_HOLD_IDS), {10, 35})

    # --- financial protection outranks hiding ---------------------------------

    def test_financial_evidence_outranks_a_strong_qa_pattern(self):
        """Even a machine-provisioned name is protected once money is involved."""
        label, reason = qa.classify(_row(31, "undxqa_20260719222239", ""), "users.stripe_customer_id")
        self.assertEqual(label, "PROTECT_FINANCIAL_HISTORY")
        self.assertIn("stripe_customer_id", reason)

    def test_write_scope_constant_is_hide_only(self):
        self.assertEqual(set(qa.WRITABLE_LABELS), {"HIDE_FROM_DISCOVERY"})

    def test_like_patterns_are_bound_not_inlined(self):
        """Regression: PostgreSQL treats the query as a printf format string.

        Patterns containing the literal sequence ``%s`` — ``'%sample%'``,
        ``'%staging%'`` — get read as parameter placeholders when inlined, and
        the query dies with "IndexError: tuple index out of range". This is
        invisible on SQLite, so it cannot be caught by running the script
        against the test database; it has to be asserted structurally.
        """
        fragment, params = qa._pattern_clause("username", ("%sample%", "%staging%"))
        self.assertEqual(params, ["%sample%", "%staging%"])
        self.assertEqual(fragment.count("?"), 2)
        for pattern in params:
            self.assertNotIn(pattern, fragment,
                             "patterns must be bound as parameters, never inlined into SQL")

    def test_every_pattern_containing_percent_s_is_covered_by_binding(self):
        """Names the hazard explicitly so a future pattern addition is safe."""
        all_patterns = (qa.STRONG_NAME_PATTERNS + qa.STRONG_EMAIL_PATTERNS
                        + qa.WEAK_PATTERNS + qa.INTERNAL_PATTERNS)
        fragment, params = qa._pattern_clause("email", all_patterns)
        self.assertEqual(len(params), len(all_patterns))
        self.assertNotIn("%", fragment, "no literal % may reach the SQL string")

    def test_like_escape_char_is_not_a_backslash(self):
        """Regression: ``ESCAPE '\\'`` breaks placeholder translation on Postgres.

        ``services/db.py::_translate_sql`` converts ``?`` to ``%s`` for psycopg2
        and skips over string literals. It reads ``\\'`` as an escaped quote, so
        ``ESCAPE '\\'`` convinces it that it is inside an unterminated literal
        and it stops converting placeholders until the next such clause flips
        the state back — leaving every other ``?`` untranslated. Like the test
        above, SQLite happily accepts either escape character, so this is only
        catchable structurally.
        """
        fragment, _ = qa._pattern_clause("username", ("qa!_%",))
        self.assertIn("ESCAPE '!'", fragment)
        self.assertNotIn("\\", fragment)

    def test_escaped_underscore_matches_literally_in_both_engines(self):
        """The Python mirror and SQL LIKE must agree on what ``!_`` means."""
        # `!_` is a literal underscore, so a hyphen must not satisfy it.
        self.assertTrue(qa._matches("incident_prod_070937_22509", ("incident!_prod!_%",)))
        self.assertFalse(qa._matches("incidentXprodY123", ("incident!_prod!_%",)))
        with sqlite3.connect(":memory:") as conn:
            row = conn.execute(
                "SELECT ? LIKE ? ESCAPE '!', ? LIKE ? ESCAPE '!'",
                ("incident_prod_070937_22509", "incident!_prod!_%",
                 "incidentXprodY123", "incident!_prod!_%"),
            ).fetchone()
        self.assertEqual(tuple(row), (1, 0))


class FinancialEvidenceTest(unittest.TestCase):
    """1-4: what does and does not count as a payment relationship."""

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db", prefix="qa_classifier_fin_")
        os.close(handle)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.cursor()
        cur.execute("""CREATE TABLE users (
            user_id INTEGER PRIMARY KEY, username TEXT, email TEXT, display_name TEXT,
            account_status TEXT DEFAULT 'active', hidden_from_discovery INTEGER DEFAULT 0,
            stripe_customer_id TEXT, provider_customer_id TEXT, stripe_subscription_id TEXT,
            latest_payment_at TEXT, created_at TEXT)""")
        cur.execute("""CREATE TABLE subscriptions (
            id INTEGER PRIMARY KEY, user_id INTEGER, plan TEXT, status TEXT,
            payment_type TEXT, stripe_customer_id TEXT, stripe_subscription_id TEXT,
            provider_subscription_id TEXT)""")
        cur.execute("CREATE TABLE creator_payouts (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL)")
        cur.execute("CREATE TABLE stripe_events (id INTEGER PRIMARY KEY, user_id INTEGER, event_type TEXT)")
        self.conn.commit()
        self.cur = self.conn.cursor()
        self.tables = qa._financial_tables(self.cur)
        self.conditional = qa._conditional_financial_sql(self.cur)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def reason_for(self, user_id, **user_cols):
        row = _row(user_id, **user_cols)
        return qa._financial_reason(self.cur, self.tables, self.conditional, row)

    def test_subscriptions_is_predicate_guarded_not_existence_guarded(self):
        """The structural fix: `subscriptions` must never be an existence check."""
        self.assertNotIn("subscriptions", self.tables,
                         "subscriptions must not be treated as existence-is-proof")
        self.assertIn("subscriptions", self.conditional,
                      "subscriptions must be evaluated through a predicate")

    def test_1_trial_subscription_row_is_not_financial_history(self):
        """The exact production shape: every user is auto-provisioned this row."""
        self.cur.execute(
            "INSERT INTO subscriptions (user_id, plan, status, payment_type) VALUES (?,?,?,?)",
            (11, "pro", "trialing", "trial"))
        self.conn.commit()
        self.assertEqual(self.reason_for(11), "",
                         "an auto-provisioned trial row must not protect an account")

    def test_1b_a_real_paid_subscription_row_is_financial_history(self):
        """Negative control for the test above: the check must still fire."""
        self.cur.execute(
            "INSERT INTO subscriptions (user_id, plan, status, payment_type, stripe_customer_id) "
            "VALUES (?,?,?,?,?)", (12, "pro", "active", "stripe", "cus_real"))
        self.conn.commit()
        self.assertEqual(self.reason_for(12), "subscriptions (non-trial)")

    def test_2_stripe_customer_id_implies_financial_protection(self):
        self.assertEqual(self.reason_for(13, stripe_customer_id="cus_abc"),
                         "users.stripe_customer_id")
        self.assertEqual(self.reason_for(13, stripe_customer_id=""), "")

    def test_3_latest_payment_at_implies_financial_protection(self):
        self.assertEqual(self.reason_for(14, latest_payment_at="2026-08-10T21:36:43"),
                         "users.latest_payment_at")
        self.assertEqual(self.reason_for(14, latest_payment_at=None), "")

    def test_4_creator_payout_implies_financial_protection(self):
        self.assertEqual(self.reason_for(15), "")
        self.cur.execute("INSERT INTO creator_payouts (user_id, amount) VALUES (?,?)", (15, 12.5))
        self.conn.commit()
        self.assertEqual(self.reason_for(15), "creator_payouts row")

    def test_stripe_event_implies_financial_protection(self):
        self.cur.execute("INSERT INTO stripe_events (user_id, event_type) VALUES (?,?)",
                         (16, "invoice.paid"))
        self.conn.commit()
        self.assertEqual(self.reason_for(16), "stripe_events row")

    def test_provider_customer_id_implies_financial_protection(self):
        self.assertEqual(self.reason_for(17, provider_customer_id="prov_1"),
                         "users.provider_customer_id")


class ApplyScopeTest(unittest.TestCase):
    """10-13: what the write mode is actually allowed to touch.

    Runs the real script as a subprocess, the same way an operator would, so the
    argument parsing and write scoping are exercised end to end rather than
    approximated.
    """

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db", prefix="qa_classifier_apply_")
        os.close(handle)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""CREATE TABLE users (
            user_id INTEGER PRIMARY KEY, username TEXT, email TEXT, display_name TEXT,
            account_status TEXT DEFAULT 'active', hidden_from_discovery INTEGER DEFAULT 0,
            stripe_customer_id TEXT, provider_customer_id TEXT, stripe_subscription_id TEXT,
            latest_payment_at TEXT, created_at TEXT)""")
        cur.execute("""CREATE TABLE subscriptions (
            id INTEGER PRIMARY KEY, user_id INTEGER, plan TEXT, status TEXT,
            payment_type TEXT, stripe_customer_id TEXT, stripe_subscription_id TEXT,
            provider_subscription_id TEXT)""")
        cur.execute("CREATE TABLE creator_payouts (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL)")

        # A QA account with only the universal trial row: must be hidden.
        cur.execute("INSERT INTO users (user_id, username, email) VALUES (?,?,?)",
                    (11, "pulseqa802505", "cherieroody+pulseqa@gmail.com"))
        # A paying account that also looks testy: must be protected.
        cur.execute("INSERT INTO users (user_id, username, email, stripe_customer_id) VALUES (?,?,?,?)",
                    (35, "TestMeNow", "owner@gmail.com", "cus_live"))
        # An explicit internal account: reported, never written.
        cur.execute("INSERT INTO users (user_id, username, email) VALUES (?,?,?)",
                    (40, "internal_ops_tool", "internal_ops@example.com"))
        # The owner-held ambiguous account.
        cur.execute("INSERT INTO users (user_id, username, email) VALUES (?,?,?)",
                    (10, "JOHNDOE", "ulgwop@gmail.com"))
        # Every account gets the auto-provisioned trial row, as in production.
        for uid in (11, 35, 40, 10):
            cur.execute("INSERT INTO subscriptions (user_id, plan, status, payment_type) "
                        "VALUES (?,?,?,?)", (uid, "pro", "trialing", "trial"))
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def run_script(self, *args):
        env = dict(os.environ)
        env["DATABASE_URL"] = f"sqlite:///{self.db_path}"
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True,
                              text=True, env=env, cwd=REPO_ROOT)

    def hidden_map(self):
        conn = sqlite3.connect(self.db_path)
        rows = dict(conn.execute(
            "SELECT user_id, COALESCE(hidden_from_discovery,0) FROM users").fetchall())
        conn.close()
        return rows

    def test_13_dry_run_performs_zero_writes(self):
        before = self.hidden_map()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY RUN", result.stdout)
        self.assertEqual(self.hidden_map(), before, "dry run must not write anything")

    def test_10_only_hide_from_discovery_rows_are_written(self):
        result = self.run_script("--apply-hide", "--only", "HIDE_FROM_DISCOVERY")
        self.assertEqual(result.returncode, 0, result.stderr)
        hidden = self.hidden_map()
        self.assertEqual(hidden[11], 1, "the clean QA account must be hidden")
        self.assertEqual(hidden[35], 0, "a paying account must never be hidden")
        self.assertEqual(hidden[40], 0, "INTERNAL_ONLY must never be hidden")
        self.assertEqual(hidden[10], 0, "a manual hold must never be hidden")

    def test_11_internal_only_is_not_modified(self):
        self.run_script("--apply-hide")
        self.assertEqual(self.hidden_map()[40], 0)

    def test_12_protect_financial_history_is_not_modified(self):
        """The account that would have been wrongly hidden if the trial-row bug
        had been fixed carelessly — it carries a real Stripe customer id."""
        self.run_script("--apply-hide")
        self.assertEqual(self.hidden_map()[35], 0)

    def test_only_flag_rejects_non_writable_labels(self):
        """Defence in depth: even if a caller asks, the script refuses.

        This is the check that makes the write scope a property of the script
        rather than a property of the operator remembering the right flag.
        """
        for label in ("INTERNAL_ONLY", "PROTECT_FINANCIAL_HISTORY", "AMBIGUOUS",
                      "APP_REVIEW_REQUIRED", "DEACTIVATE"):
            with self.subTest(label=label):
                result = self.run_script("--apply-hide", "--only", label)
                self.assertEqual(result.returncode, 2)
                self.assertIn("may not include", result.stdout)
                self.assertEqual(self.hidden_map()[35], 0)

    def test_apply_never_deletes_or_deactivates(self):
        self.run_script("--apply-hide")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY user_id")]
        subs = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
        conn.close()
        self.assertEqual(len(rows), 4, "no account may be deleted")
        for row in rows:
            self.assertEqual(row["account_status"], "active", "no account may be deactivated")
        self.assertEqual(subs, 4, "financial rows must be untouched")
        self.assertEqual([r["stripe_customer_id"] for r in rows if r["user_id"] == 35], ["cus_live"])

    def test_script_has_no_delete_path_at_all(self):
        with open(SCRIPT, "r", encoding="utf-8") as handle:
            source = handle.read().upper()
        self.assertNotIn("DELETE FROM", source)
        self.assertNotIn("DROP TABLE", source)


if __name__ == "__main__":
    unittest.main()
