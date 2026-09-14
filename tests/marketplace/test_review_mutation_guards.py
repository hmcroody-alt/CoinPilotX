"""§45. Break each guard on purpose and prove something goes red.

A green suite is evidence of nothing until you know it can go red. Every
assertion in ``test_admin_review_batch_route.py`` and
``test_admin_review_queue_page.py`` could be vacuous — asserting on a field that
is always present, a count that is always zero, a block that the fixture never
actually triggers — and the whole file would still pass. That is the failure
mode this exists to rule out: not "is the code correct" but "would these tests
notice if it were not".

Each test below does the same three things:

  1. run a **detector** against the real code and require it to *pass*. A
     detector that is already failing proves nothing about the mutation;
  2. apply one **mutation** — a small, plausible change a reasonable engineer
     might make — and require the same detector to *fail*;
  3. restore.

Step 1 is the half that is easy to leave out and the half that matters. Without
it, a detector that raises unconditionally (a typo'd table name, a fixture that
never seeds) reads as "the mutation was caught" for every mutation in the file.

The mutations are applied to *module attributes at runtime*, never to files on
disk. Nothing here writes to the repository, so a mutation left applied by a
crashed test dies with the process instead of being committed.

Run standalone (this file binds its own ``DATABASE_URL`` before importing
``bot``, so it cannot share a pytest process with another marketplace file)::

    ./.venv/bin/python3 -m pytest tests/marketplace/test_review_mutation_guards.py
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="review_mutations_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.business_os.marketplace import listing_review as rv  # noqa: E402

SELLER = 79301
REVIEWER = 79302
NOW = "2026-09-01T00:00:00"

ENDPOINT = "/api/admin/marketplace/review/batch"
PAGE = "/admin/marketplace-command"

TICK = re.compile(r"class='review-tick' value='(\d+)' data-block=\"([^\"]*)\"")


class Restore:
    """Set a module attribute and put the old one back, whatever happens.

    Not ``unittest.mock.patch``: several of these replace a *constant*
    (``REASON_REQUIRED``) rather than a callable, and the point of the file is
    that a mutation is never left applied — a leaked mutation would make every
    later test in the process report a false catch.
    """

    def __init__(self, target, name, value):
        self.target, self.name, self.value = target, name, value

    def __enter__(self):
        self.previous = getattr(self.target, self.name)
        setattr(self.target, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.target, self.name, self.previous)
        return False


class ReviewMutationGuardTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        rv.ensure_schema()
        cls._real_require_admin_api = bot.require_admin_api
        cls._real_require_admin_page = bot.require_admin_page
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

        def _require_admin_api(permission="users.view"):
            return {"id": REVIEWER, "username": "reviewer",
                    "email": "reviewer@example.com"}, None

        def _require_admin_page(permission="users.view"):
            return {"id": REVIEWER, "username": "reviewer",
                    "email": "reviewer@example.com", "role": "owner"}, None

        bot.require_admin_api = _require_admin_api
        bot.require_admin_page = _require_admin_page

    @classmethod
    def tearDownClass(cls):
        bot.require_admin_api = cls._real_require_admin_api
        bot.require_admin_page = cls._real_require_admin_page

    def setUp(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM marketplace_listings")
            cur.execute("DELETE FROM marketplace_listing_variants")
            cur.execute("DELETE FROM marketplace_review_batches")
            cur.execute("DELETE FROM admin_audit_logs")
            cur.execute("DELETE FROM pulse_notifications WHERE user_id IN (?,?)",
                        (SELLER, REVIEWER))
            cur.execute("DELETE FROM marketplace_sellers WHERE user_id IN (?,?)",
                        (SELLER, REVIEWER))
            for user_id, store in ((SELLER, "Lamp Co"), (REVIEWER, "Reviewer's Own Store")):
                cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name)"
                            " VALUES (?,?,?)", (user_id, f"user{user_id}", store))
                cur.execute("INSERT INTO marketplace_sellers"
                            " (user_id, display_name, status, created_at, updated_at)"
                            " VALUES (?,?,?,?,?)", (user_id, store, "approved", NOW, NOW))
            conn.commit()
        finally:
            conn.close()

    # -- harness ---------------------------------------------------------------

    def insert_listing(self, seller_user_id=SELLER, **overrides):
        row = {
            "seller_user_id": seller_user_id,
            "title": "Brass desk lamp",
            "description": "A weighted brass lamp with a linen shade.",
            "category": "Home",
            "price_label": "$24.00",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "status": lifecycle.PENDING_REVIEW,
            "approval_status": lifecycle.PENDING_REVIEW,
            "listing_type": "physical",
            "product_type": "physical",
            "quantity": 12,
            "created_at": NOW,
            "updated_at": NOW,
        }
        row.update(overrides)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(f"INSERT INTO marketplace_listings ({cols}) VALUES ({marks})",
                    tuple(row.values()))
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    def insert_variant(self, listing_id, **overrides):
        row = {
            "listing_id": listing_id, "seller_user_id": SELLER,
            "variant_key": "default", "sku": "LAMP-1", "currency": "USD",
            "price_cents": 2400, "cost_cents": 900, "stock_quantity": 12,
            "stock_state": "in_stock", "stock_synced_at": NOW, "position": 0,
            "status": "active", "created_at": NOW, "updated_at": NOW,
        }
        row.update(overrides)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"INSERT INTO marketplace_listing_variants ({cols}) VALUES ({marks})",
                     tuple(row.values()))
        conn.commit()
        conn.close()

    def review(self, action, listing_ids, key=None, **extra):
        body = {"action": action, "listing_ids": listing_ids,
                "idempotency_key": key or f"key-{uuid.uuid4()}"}
        body.update(extra)
        return self.client.post(ENDPOINT, data=json.dumps(body),
                                content_type="application/json")

    def stored(self, listing_id):
        return self.query("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,))[0]

    def query(self, sql, params=()):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        return rows

    def outcomes(self, response):
        body = response.get_json()
        return body, {int(e["listing_id"]): e for e in body.get("results", [])}

    def assert_mutation_is_caught(self, name, mutation, detector):
        """The whole shape of this file, in one method.

        ``detector`` must pass now and fail under ``mutation``. Both halves are
        assertions: a detector that cannot pass is broken, and a detector that
        cannot fail is decoration.
        """
        self.setUp()
        try:
            detector()
        except AssertionError as unmutated_failure:
            self.fail(f"detector for {name!r} fails against the real code, so it "
                      f"proves nothing about the mutation: {unmutated_failure}")

        self.setUp()
        with mutation:
            try:
                detector()
            except AssertionError:
                return
        self.fail(f"MUTATION SURVIVED: {name}. The code was broken on purpose and "
                  f"every assertion still passed — this guard is untested.")

    # -- 1. §18 the reviewer's own listing -------------------------------------

    def test_removing_the_self_review_gate_is_caught(self):
        def blind_to_ownership(listing, action, *, reviewer_id):
            """"Admins are trusted, and the ownership check is a nuisance in
            testing." The rewrite that drops exactly one clause."""
            if listing is None:
                return rv.NOT_FOUND
            if not lifecycle.awaiting_moderation(listing):
                return rv.NOT_AWAITING_REVIEW
            if action == rv.APPROVE and rv._goods.evaluate(
                    listing).get("decision") == "PROHIBITED":
                return rv.PROHIBITED
            return None

        def detector():
            listing_id = self.insert_listing(seller_user_id=REVIEWER)
            _, results = self.outcomes(self.review(rv.APPROVE, [listing_id]))
            self.assertEqual(results[listing_id]["outcome"], rv.BLOCKED)
            self.assertEqual(results[listing_id]["error_code"], rv.SELF_REVIEW)
            self.assertEqual(self.stored(listing_id)["approval_status"],
                             lifecycle.PENDING_REVIEW)

        self.assert_mutation_is_caught(
            "§18 self-review gate removed",
            Restore(rv, "block_reason", blind_to_ownership), detector)

    # -- 2. §34 prohibited products --------------------------------------------

    def test_letting_a_human_approve_a_prohibited_product_is_caught(self):
        def human_override(listing, action, *, reviewer_id):
            """"A reviewer looked at it, so the policy engine can stand down."
            §34 exists because this is the reasonable-sounding version."""
            if listing is None:
                return rv.NOT_FOUND
            if not lifecycle.awaiting_moderation(listing):
                return rv.NOT_AWAITING_REVIEW
            if str(listing.get("seller_user_id") or "") == str(reviewer_id or "\0"):
                return rv.SELF_REVIEW
            return None

        def detector():
            listing_id = self.insert_listing(title="Case of whisky", category="Alcohol")
            _, results = self.outcomes(self.review(rv.APPROVE, [listing_id]))
            entry = results[listing_id]
            # `outcome` first, `error_code` second. A succeeded entry carries no
            # `error_code` at all, so reading it first turns the catch into a
            # KeyError -- red, but not an *assertion*, and `assert_mutation_is_caught`
            # only counts assertions on purpose: a detector that crashes rather
            # than asserts is one whose failure message tells you nothing.
            self.assertEqual(entry["outcome"], rv.BLOCKED)
            self.assertEqual(entry.get("error_code"), rv.PROHIBITED)
            self.assertNotEqual(self.stored(listing_id)["status"], lifecycle.PUBLISHED)

        self.assert_mutation_is_caught(
            "§34 prohibited products approvable by a human",
            Restore(rv, "block_reason", human_override), detector)

    # -- 3. §9 a rejection with no reason --------------------------------------

    def test_making_the_rejection_reason_optional_is_caught(self):
        def detector():
            listing_id = self.insert_listing()
            response = self.review(rv.REJECT, [listing_id])
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["error"], "REASON_REQUIRED")
            self.assertEqual(self.stored(listing_id)["approval_status"],
                             lifecycle.PENDING_REVIEW)

        self.assert_mutation_is_caught(
            "§9 reason no longer required to reject",
            Restore(rv, "REASON_REQUIRED", frozenset()), detector)

    # -- 4. §36 an unknown reason code -----------------------------------------

    def test_filing_an_unknown_reason_code_as_other_is_caught(self):
        def coerce_to_other(action, reason_code, note=None):
            """"Be liberal in what you accept." Which turns a reviewer's typo
            into a real category in the audit trail, silently."""
            code = str(reason_code or "").strip().upper()
            if action not in rv.REASON_REQUIRED:
                return {"reason_code": "", "note": str(note or "").strip()}
            if code not in rv.REASON_CODES:
                code = rv.OTHER
            return {"reason_code": code, "note": str(note or "").strip()}

        def detector():
            listing_id = self.insert_listing()
            response = self.review(rv.REJECT, [listing_id], reason_code="BAD_VIBES")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["error"], "UNKNOWN_REASON")
            self.assertEqual(self.stored(listing_id)["moderation_category"] or "", "")

        self.assert_mutation_is_caught(
            "§36 unknown reason code coerced to OTHER",
            Restore(rv, "normalize_reason", coerce_to_other), detector)

    # -- 5. §17 the double tap --------------------------------------------------

    def test_bypassing_the_idempotency_claim_is_caught(self):
        def always_fresh(cur, reviewer_id, normalized):
            """"The unique index on the key already stops duplicates." It does
            not: the second request is a *different transaction* that reads the
            rows again and answers from whatever it finds."""
            return {"state": "claimed", "batch_id": f"mrb_{uuid.uuid4().hex[:12]}"}

        def detector():
            listing_id = self.insert_listing()
            key = "double-tap"
            first = self.review(rv.APPROVE, [listing_id], key=key)
            second = self.review(rv.APPROVE, [listing_id], key=key)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            body = second.get_json()
            self.assertTrue(body.get("replayed"),
                            "the second tap decided again instead of replaying")
            self.assertEqual(body["successful_count"], 1)
            self.assertEqual(body["batch_id"], first.get_json()["batch_id"])

        self.assert_mutation_is_caught(
            "§17 idempotency claim bypassed",
            Restore(rv, "claim", always_fresh), detector)

    # -- 6. §15 the two that were blocked --------------------------------------

    def test_dropping_the_blocked_rows_from_the_results_is_caught(self):
        real_evaluate = rv.evaluate_rows

        def quietly_shrink(rows, listing_ids, action, *, reviewer_id):
            """"Only report what we acted on." The two products the reviewer
            most needs told about are the two that then never appear."""
            verdict = real_evaluate(rows, listing_ids, action, reviewer_id=reviewer_id)
            return {"eligible": verdict["eligible"], "blocked": []}

        def detector():
            good = self.insert_listing()
            mine = self.insert_listing(seller_user_id=REVIEWER)
            body, results = self.outcomes(self.review(rv.APPROVE, [good, mine]))
            self.assertEqual(body["requested_count"], 2)
            self.assertEqual(body["blocked_count"], 1)
            self.assertIn(mine, results)

        self.assert_mutation_is_caught(
            "§15 blocked rows dropped instead of reported",
            Restore(rv, "evaluate_rows", quietly_shrink), detector)

    # -- 7. §37 "the UPDATE returned, so it is live" ----------------------------

    def test_reporting_live_without_reading_the_row_back_is_caught(self):
        def optimistic(listing):
            """"We just approved it, so it is live." Three of the five
            publication conditions live on rows the UPDATE never touched."""
            return {"review_state": lifecycle.APPROVED,
                    "publication_state": lifecycle.PUBLISHED,
                    "live": True, "blockers": [], "note": ""}

        def detector():
            listing_id = self.insert_listing(quantity=0)
            _, results = self.outcomes(self.review(rv.APPROVE, [listing_id]))
            entry = results[listing_id]
            self.assertEqual(entry["outcome"], rv.SUCCEEDED)
            self.assertFalse(entry.get("live"),
                             "an out-of-stock listing was reported live")
            self.assertTrue(entry.get("blockers"))

        self.assert_mutation_is_caught(
            "§37 publication read-back replaced with optimism",
            Restore(rv, "publication_readback", optimistic), detector)

    # -- 8. §19 the audit trail -------------------------------------------------

    def test_losing_the_audit_row_is_caught(self):
        def silent(cur, reviewer_id, action, listing_id, payload):
            """This is not hypothetical. The route shipped calling a helper that
            opened its own connection, hit SQLite's write lock, and swallowed the
            error — twenty-five decisions and zero audit rows, behind a 200."""
            return None

        def detector():
            listing_id = self.insert_listing()
            response = self.review(rv.APPROVE, [listing_id])
            self.assertEqual(response.status_code, 200)
            rows = self.query(
                "SELECT * FROM admin_audit_logs WHERE target_type='marketplace_listing'"
                " AND target_id=?", (str(listing_id),))
            self.assertEqual(len(rows), 1, "no audit row for a completed decision")
            self.assertIn(response.get_json()["batch_id"], rows[0]["metadata"])

        self.assert_mutation_is_caught(
            "§19 audit row not written",
            Restore(bot, "_marketplace_review_audit", silent), detector)

    # -- 9. §43 the reviewer's private note -------------------------------------

    def test_leaking_the_reviewers_note_to_the_seller_is_caught(self):
        secret = "seller is a repeat offender, watch this account"
        real_emit = bot.pulse_emit_marketplace_inventory_event

        def helpful_leak(cur, seller_user_id, event, **kwargs):
            """"The seller would understand the rejection better with the
            reviewer's actual words." The note is internal (§43) and the
            structured code exists precisely so there is a safe thing to send."""
            extra = dict(kwargs.pop("extra", None) or {})
            extra["seller_message"] = secret
            return real_emit(cur, seller_user_id, event, extra=extra, **kwargs)

        def detector():
            listing_id = self.insert_listing()
            self.review(rv.REJECT, [listing_id],
                        reason_code=rv.MISLEADING_DESCRIPTION, note=secret)
            notes = self.query(
                "SELECT * FROM pulse_notifications WHERE user_id=?", (SELLER,))
            self.assertTrue(notes, "the seller was not told at all")
            blob = json.dumps(notes)
            self.assertNotIn(secret, blob, "the reviewer's private note reached the seller")
            self.assertIn(rv.SELLER_MESSAGES[rv.MISLEADING_DESCRIPTION], blob)

        self.assert_mutation_is_caught(
            "§43 reviewer's note sent to the seller",
            Restore(bot, "pulse_emit_marketplace_inventory_event", helpful_leak), detector)

    # -- 10. §38 a listing the queue cannot show --------------------------------

    def test_a_queue_narrower_than_the_gate_is_caught(self):
        real_where = rv.queue_where

        def hide_the_oldest(query, alias="l"):
            """The §38 defect in miniature: a predicate that drifts from the one
            the Approve gate uses. Nothing errors. A listing is simply in review
            forever and appears on nobody's screen."""
            where, params = real_where(query, alias)
            return f"({where}) AND {alias}.id <> (SELECT MIN(id) FROM marketplace_listings)", params

        def detector():
            ids = {self.insert_listing(title=f"Lamp {index}") for index in range(3)}
            response = self.client.get(PAGE, query_string={"page_size": 50})
            self.assertEqual(response.status_code, 200)
            shown = {int(listing_id) for listing_id, _ in
                     TICK.findall(response.get_data(as_text=True))}
            self.assertEqual(shown, ids, "a pending listing is not on any page")

        self.assert_mutation_is_caught(
            "§38 queue predicate narrower than the approve gate",
            Restore(rv, "queue_where", hide_the_oldest), detector)

    # -- 11. §7/§1 the dossier growing an opinion -------------------------------

    def test_the_detail_page_deciding_for_itself_is_caught(self):
        real_inspection = rv.inspection

        def trusting_dossier(listing, **kwargs):
            """The plausible refactor: "we already know whether it's awaiting
            review, why call `block_reason` four times". One boolean replaces
            four verdicts, §18 and §34 both vanish from the page, and the buttons
            go live on listings the endpoint will refuse. Nothing errors — the
            reviewer just gets a 403 after clicking, which is what the last one
            of these looked like in production."""
            result = real_inspection(listing, **kwargs)
            if result.get("found"):
                result["verdicts"] = {action: None for action in rv.ACTIONS}
            return result

        def detector():
            listing_id = self.insert_listing(seller_user_id=REVIEWER)
            response = self.client.get(f"{PAGE}/listing/{listing_id}")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            for action in rv.ACTIONS:
                markup = re.search(
                    r"<button[^>]*data-detail-action='"
                    + re.escape(action) + r"'([^>]*)>", html)
                self.assertIsNotNone(markup, f"no {action} button")
                self.assertIn("disabled", markup.group(1),
                              f"{action} is offered on the reviewer's own listing")

        self.assert_mutation_is_caught(
            "§7 dossier verdicts replaced with a blanket yes",
            Restore(rv, "inspection", trusting_dossier), detector)

    # -- 12. §7 unknown supplier cost rendered as zero --------------------------

    def test_treating_an_absent_supplier_cost_as_zero_is_caught(self):
        real_economics = rv.variant_economics

        def coerce_missing_to_zero(variant):
            """`int(variant.get("cost_cents") or 0)` — the one-character version
            of this bug, and the reason `_int_or_none` exists. Every listing with
            no supplier data reports a 100% margin, so the products the reviewer
            knows least about are the ones that look most worth approving. No
            exception, no log line, a plausible number on every row."""
            return real_economics(dict(variant, cost_cents=int(variant.get("cost_cents") or 0)))

        def detector():
            listing_id = self.insert_listing()
            self.insert_variant(listing_id, price_cents=2400, cost_cents=None)
            response = self.client.get(f"{PAGE}/listing/{listing_id}")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertNotIn("100.0%", html,
                             "an unrecorded supplier cost is reporting a perfect margin")
            self.assertIn("margin cannot be checked", html)

        self.assert_mutation_is_caught(
            "§7 missing supplier cost coerced to zero",
            Restore(rv, "variant_economics", coerce_missing_to_zero), detector)

    # -- 13. §43 supplier cost escaping its section ------------------------------

    def test_supplier_cost_leaving_the_internal_section_is_caught(self):
        real_inspection = rv.inspection

        def flatten_the_economics(listing, **kwargs):
            """The convenience refactor that ends the §43 guarantee: hoist the
            pricing summary to the top level "so callers don't have to reach into
            a nested dict". The admin page renders identically. The next caller
            to build a seller payload from `inspection()` now ships supplier cost
            to the merchant, and no reviewer of that diff sees a cost field —
            they see `dossier["min_margin_pct"]`."""
            result = real_inspection(listing, **kwargs)
            if result.get("found"):
                result.update(result[rv.INTERNAL_SECTION])
            return result

        def detector():
            listing_id = self.insert_listing()
            self.insert_variant(listing_id, price_cents=2400, cost_cents=900)
            row = self.stored(listing_id)
            variants = self.query(
                "SELECT * FROM marketplace_listing_variants WHERE listing_id=?",
                (listing_id,))
            dossier = rv.inspection(row, variants=variants, reviewer_id=REVIEWER)
            outside = {key: value for key, value in dossier.items()
                       if key != rv.INTERNAL_SECTION}
            self.assertNotIn("cost", repr(outside).lower(),
                             "supplier cost is reachable outside the internal section")

        self.assert_mutation_is_caught(
            "§43 supplier economics hoisted out of the internal section",
            Restore(rv, "inspection", flatten_the_economics), detector)

    # -- §24 supplier refresh helpers -------------------------------------------

    def bind_supplier(self, listing_id, seller_user_id=SELLER, **overrides):
        from services import marketplace_supplier_schema as supplier_schema

        row = {
            "listing_id": listing_id, "seller_user_id": seller_user_id,
            "provider": "cj", "provider_product_id": f"CJ-PROD-{listing_id}",
            "fulfillment_mode": "DROPSHIP", "supplier_connection_id": "conn_7",
            "business_id": "biz_2", "store_id": "store_5",
            "created_at": NOW, "updated_at": NOW,
        }
        row.update(overrides)
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            supplier_schema.ensure_supplier_schema(cur, force=True)
            cur.execute(
                f"INSERT INTO {supplier_schema.SOURCE_TABLE} ({', '.join(row)}) "
                f"VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
            conn.commit()
        finally:
            conn.close()

    def sync_jobs(self):
        from services.business_os.suppliers import worker as supplier_worker

        supplier_worker.ensure_schema()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM business_os_supplier_sync_jobs")]
        conn.execute("DELETE FROM business_os_supplier_sync_jobs")
        conn.commit()
        conn.close()
        return rows

    # -- 14. §24 refreshing on a verdict that sells nothing ---------------------

    def test_refreshing_the_supplier_on_a_rejection_is_caught(self):
        real_plan = rv.supplier_sync_plan

        def refresh_everything(source, *, action):
            """"Why gate it on the action at all -- keeping supplier data fresh
            is always good." It is, until a bulk reject of forty imported
            products spends eighty metered supplier reads on pages no buyer can
            reach, and the quota controller then throttles the approvals that
            needed it. Nothing errors; the queue just gets slower on the days it
            is busiest."""
            return real_plan(source, action=rv.APPROVE)

        def detector():
            listing_id = self.insert_listing()
            self.bind_supplier(listing_id)
            self.sync_jobs()
            self.review(rv.REJECT, [listing_id],
                        reason_code=rv.REASON_CODES[0], note="No.")
            self.assertEqual(self.sync_jobs(), [],
                             "a rejected listing queued a supplier refresh")

        self.assert_mutation_is_caught(
            "§24 supplier refresh scheduled for every verdict",
            Restore(rv, "supplier_sync_plan", refresh_everything), detector)

    # -- 15. §24 a job written outside its connection scope ---------------------

    def test_queuing_a_refresh_with_half_a_connection_scope_is_caught(self):
        real_plan = rv.supplier_sync_plan

        def scope_by_connection_alone(source, *, action):
            """"The connection id is unique, so business and store are
            redundant." They are not redundant, they are how every later read is
            matched. A job missing them is claimed, resolves against a scope no
            binding matches, and goes back on the queue -- a row that is
            permanently scheduled and permanently useless, with no error
            anywhere. This is the failure that looks exactly like success in
            every dashboard that counts queued jobs."""
            plan = real_plan(source, action=action)
            if not plan["scheduled"] and plan["skip_reason"] == "UNBOUND_SUPPLIER":
                connection_id = str((source or {}).get("supplier_connection_id") or "")
                product_id = str((source or {}).get("provider_product_id") or "")
                if connection_id and product_id:
                    return dict(plan, scheduled=True, skip_reason="", jobs=[
                        {"connection_id": connection_id, "business_id": "",
                         "store_id": "", "kind": kind, "resource_id": product_id}
                        for kind in rv.SUPPLIER_SYNC_KINDS])
            return plan

        def detector():
            listing_id = self.insert_listing()
            self.bind_supplier(listing_id, store_id="")
            self.sync_jobs()
            _, results = self.outcomes(self.review(rv.APPROVE, [listing_id]))
            self.assertEqual(results[listing_id]["supplier_sync"], "skipped",
                             "a listing with no reachable connection was queued anyway")
            self.assertEqual(self.sync_jobs(), [])

        self.assert_mutation_is_caught(
            "§24 refresh queued without the full connection scope",
            Restore(rv, "supplier_sync_plan", scope_by_connection_alone), detector)

    # -- 16. §24 a refresh reported as queued when it was not -------------------

    def test_reporting_a_refresh_that_never_reached_the_queue_is_caught(self):
        real_helper = bot._marketplace_review_supplier_sync

        def report_without_queuing(plans, results):
            """The optimistic version: mark the entries and enqueue, but let the
            enqueue fail quietly because "the worker will pick it up on its next
            cadence sweep anyway". It will not -- there is no row for it to
            sweep. The reviewer is told stock was refreshed, the listing goes
            live on the count it was imported with, and the first sign of
            trouble is an oversold order."""
            for listing_id in plans:
                for entry in results:
                    if int(entry.get("listing_id") or 0) == listing_id:
                        entry["supplier_sync"] = "queued"
                        entry["supplier_sync_note"] = "Queued a supplier refresh."

        def detector():
            listing_id = self.insert_listing()
            self.bind_supplier(listing_id)
            self.sync_jobs()
            _, results = self.outcomes(self.review(rv.APPROVE, [listing_id]))
            self.assertEqual(results[listing_id]["supplier_sync"], "queued")
            self.assertTrue(self.sync_jobs(),
                            "the response claims a refresh was queued and the "
                            "job table is empty")

        self.assert_mutation_is_caught(
            "§24 refresh reported as queued with no job row",
            Restore(bot, "_marketplace_review_supplier_sync", report_without_queuing),
            detector)

    # -- 17. §36 the page's reason vocabulary ----------------------------------

    def test_letting_the_page_store_an_off_vocabulary_reason_is_caught(self):
        """The mutation is the defect that was actually shipped, minus the form.

        The row form used to offer its own prose list while the bulk bar six
        inches below offered ``REASON_CODES``, and the page POST wrote whichever
        it was handed straight into ``moderation_category``. Widening the
        vocabulary to admit the prose is the tempting one-line "fix" -- it makes
        the refusal go away and leaves ``seller_message`` unable to resolve the
        stored value, so the rejected seller is told nothing specific.

        Anchored on what reaches the column rather than on the 422, because a
        page that answers 422 and stores it anyway is the same defect.
        """
        def detector():
            listing_id = self.insert_listing()
            response = self.client.post(PAGE, data={
                "listing_id": listing_id, "action": "reject",
                "reason": "Counterfeit packaging.",
                "reason_category": "Counterfeit concern"})
            stored = self.stored(listing_id)
            self.assertEqual(stored.get("moderation_category") or "", "",
                             "the page stored a category seller_message cannot read")
            self.assertEqual(str(stored.get("approval_status") or "").lower(),
                             lifecycle.PENDING_REVIEW)
            self.assertEqual(response.status_code, 422)

        self.assert_mutation_is_caught(
            "§36 page accepts a reason category outside REASON_CODES",
            Restore(rv, "REASON_CODES", tuple(rv.REASON_CODES) + ("COUNTERFEIT CONCERN",)),
            detector)

    def test_dropping_the_structured_code_requirement_on_the_page_is_caught(self):
        """§36/§1. A free-text note alone is not a reason code, and the page must
        refuse it exactly where the batch endpoint does. Otherwise the same
        verdict is structured or unstructured depending on which control the
        reviewer used."""
        def detector():
            listing_id = self.insert_listing()
            response = self.client.post(PAGE, data={
                "listing_id": listing_id, "action": "reject",
                "reason": "The photos are somebody else's."})
            self.assertEqual(str(self.stored(listing_id).get("approval_status") or "").lower(),
                             lifecycle.PENDING_REVIEW,
                             "a rejection landed with no structured reason code")
            self.assertEqual(response.status_code, 422)

        self.assert_mutation_is_caught(
            "§36 page rejects without a structured code",
            Restore(rv, "REASON_REQUIRED", frozenset()), detector)


if __name__ == "__main__":
    unittest.main()
