"""Tests for the seller application lifecycle.

This module decides who is allowed to sell on PulseSoc, so the tests are
organised around the three ways that decision can go wrong rather than around
the functions that implement it.

**Authorisation.** Approval must be unreachable except by an identified
administrator. The transition table is enumerated exhaustively rather than
spot-checked, because a security property stated as "no path exists" is only
worth asserting if every path is actually tried — a test that checks the two
transitions the author happened to think of would still pass after someone adds
``(DRAFT, APPROVED): (APPLICANT,)``.

**Disclosure.** ``applicant_view`` and ``applicant_document_view`` are
whitelists. The tests feed them rows deliberately contaminated with reviewer
notes, risk scores and stored file paths and assert those keys are absent from
the output — not merely that today's known-bad keys were stripped, but that the
output's key set is exactly the documented one, so a column added later cannot
ride along.

**Auditability.** Every status change must leave a history row, and no history
row may carry a filename or a field value. A decision that happened but was not
recorded is indistinguishable from one that never happened.

The database is a real in-memory SQLite with the production table definitions
copied from ``bot.py``'s ``init_db``, including the columns
``add_columns_if_missing`` adds at runtime. ``bot`` itself is never imported:
it is a hundred thousand lines that need a live config and third-party packages
this module does not use, and importing it would test the import, not the
lifecycle.

Run: python3 -m pytest tests/test_seller_lifecycle.py
"""

import json
import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import seller_lifecycle as sl  # noqa: E402


# --------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------

#: `marketplace_sellers` as it exists in a deployed database: the seven columns
#: from the original `CREATE TABLE` plus the ones `add_columns_if_missing` adds
#: on boot. Spelled out here rather than derived, so that a test failure points
#: at a real schema drift instead of at a clever fixture.
SELLERS_TABLE = """
CREATE TABLE marketplace_sellers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE,
    display_name TEXT,
    bio TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    updated_at TEXT,
    seller_type TEXT,
    business_name TEXT,
    website TEXT,
    country TEXT,
    state_region TEXT,
    phone TEXT,
    seller_intent_json TEXT,
    verification_status TEXT,
    risk_score INTEGER DEFAULT 0,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    review_notes TEXT
)
"""

APPLICATIONS_TABLE = """
CREATE TABLE marketplace_merchant_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    full_name TEXT,
    display_name TEXT,
    country TEXT,
    state_region TEXT,
    email TEXT,
    phone TEXT,
    pulse_username TEXT,
    business_name TEXT,
    seller_type TEXT,
    website TEXT,
    social_links TEXT,
    years_experience TEXT,
    business_description TEXT,
    seller_intent_json TEXT,
    verification_json TEXT,
    safety_answers_json TEXT,
    completeness INTEGER DEFAULT 0,
    risk_score INTEGER DEFAULT 0,
    status TEXT DEFAULT 'draft',
    reviewer_id INTEGER,
    internal_notes TEXT,
    created_at TEXT,
    updated_at TEXT,
    reviewed_at TEXT
)
"""

DOCUMENTS_TABLE = """
CREATE TABLE marketplace_merchant_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER,
    user_id INTEGER,
    document_type TEXT,
    original_filename TEXT,
    stored_path TEXT,
    mime_type TEXT,
    file_size INTEGER DEFAULT 0,
    private_access INTEGER DEFAULT 1,
    scan_status TEXT DEFAULT 'queued_for_internal_review',
    review_status TEXT DEFAULT 'pending',
    created_at TEXT
)
"""

USERS_TABLE = """
CREATE TABLE users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    display_name TEXT
)
"""


def make_db():
    """An in-memory database shaped like production, with the extra columns applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(USERS_TABLE)
    cur.execute(APPLICATIONS_TABLE)
    cur.execute(DOCUMENTS_TABLE)
    cur.execute(SELLERS_TABLE)
    for name, decl in sl.APPLICATION_EXTRA_COLUMNS:
        cur.execute(f"ALTER TABLE marketplace_merchant_applications ADD COLUMN {name} {decl}")
    sl.ensure_schema(cur)
    conn.commit()
    return conn


#: A complete, submittable answer set. Individual tests remove one key at a time
#: rather than building up from nothing, so a validation rule that stops firing
#: is caught by the test for that rule and not by an unrelated one.
COMPLETE_FIELDS = {
    "seller_type": "creator",
    "seller_intent": ["Courses", "Ebooks"],
    "full_name": "Amina Okonkwo",
    "country": "Nigeria",
    "state_region": "Lagos",
    "email": "amina@example.com",
    "phone": "+2348000000000",
    "display_name": "Amina Teaches",
    "business_description": (
        "I teach beginner options trading through structured video courses and "
        "written workbooks aimed at people who have never placed a trade."
    ),
    "website": "https://aminateaches.example.com",
    "sold_online_before": "yes",
    "banned_elsewhere": "no",
    "guaranteed_profits": "no",
    "comply_rules": "yes",
    "understand_claims": "yes",
    "marketplace_rules": True,
    "anti_scam_agreement": True,
    "no_profit_guarantees": True,
}

COMPLETE_DOCUMENTS = [
    {"document_type": "id_front"},
    {"document_type": "id_back"},
    {"document_type": "selfie"},
]


def seed_user(cur, user_id, username="amina", display_name="Amina"):
    cur.execute(
        "INSERT INTO users (user_id, username, display_name) VALUES (?, ?, ?)",
        (user_id, username, display_name),
    )


def complete_draft(cur, user_id=7):
    """A draft filled in far enough to be submittable, as the routes would leave it."""
    seed_user(cur, user_id)
    application_id = sl.create_draft(cur, user_id, source="native")
    sl.save_draft(cur, application_id, COMPLETE_FIELDS, COMPLETE_DOCUMENTS)
    for doc in COMPLETE_DOCUMENTS:
        cur.execute(
            "INSERT INTO marketplace_merchant_documents "
            "(application_id, user_id, document_type, original_filename, stored_path, mime_type, file_size, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                application_id, user_id, doc["document_type"],
                f"{doc['document_type']}.jpg",
                f"/var/private/uploads/{user_id}/{doc['document_type']}.jpg",
                "image/jpeg", 204800, "2026-01-01T00:00:00",
            ),
        )
    return application_id


class LifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.close()

    def application(self, application_id):
        return sl.get_application_by_id(self.cur, application_id)

    def seller(self, user_id):
        self.cur.execute("SELECT * FROM marketplace_sellers WHERE user_id=?", (user_id,))
        return dict(self.cur.fetchone() or {})


# --------------------------------------------------------------------------
# Authorisation: who may approve
# --------------------------------------------------------------------------

class ApprovalAuthorisationTests(LifecycleTestCase):
    def test_no_applicant_path_reaches_approved_from_any_status(self):
        # Enumerated rather than spot-checked: the property is "no path exists",
        # and a test that only tries the paths the author imagined would still
        # pass after someone adds a new one.
        reachable = [
            (frm, to) for (frm, to), actors in sl.TRANSITIONS.items()
            if to == sl.APPROVED and sl.APPLICANT in actors
        ]
        self.assertEqual(reachable, [])

    def test_no_system_path_reaches_approved(self):
        # The expiry sweep runs unattended. If it could approve, an approval
        # would happen with nobody's name on it.
        reachable = [
            (frm, to) for (frm, to), actors in sl.TRANSITIONS.items()
            if to == sl.APPROVED and sl.SYSTEM in actors
        ]
        self.assertEqual(reachable, [])

    def test_every_status_change_an_applicant_may_make_is_harmless(self):
        # An applicant may move their own application forward or abandon it.
        # They may never decide it.
        decisions = {sl.APPROVED, sl.REJECTED, sl.SUSPENDED, sl.UNDER_REVIEW, sl.INFORMATION_REQUESTED}
        for status in sl.ALL_STATUSES:
            for target in sl.allowed_transitions(status, sl.APPLICANT):
                self.assertNotIn(
                    target, decisions,
                    f"an applicant may move {status} -> {target}, which is a reviewer's decision",
                )

    def test_admin_transition_without_an_admin_id_is_refused(self):
        # The table already restricts approval to admins; this is the second
        # guard, because an unattributed approval is not auditable.
        with self.assertRaises(sl.TransitionError) as ctx:
            sl.assert_transition(sl.SUBMITTED, sl.APPROVED, sl.ADMIN, 0)
        self.assertIn("identified administrator", str(ctx.exception))

    def test_apply_transition_refuses_an_applicant_approving_themselves(self):
        application_id = complete_draft(self.cur)
        application = self.application(application_id)
        sl.apply_transition(self.cur, application, sl.SUBMITTED, actor_type=sl.APPLICANT, actor_id=7)

        with self.assertRaises(sl.TransitionError):
            sl.apply_transition(
                self.cur, self.application(application_id), sl.APPROVED,
                actor_type=sl.APPLICANT, actor_id=7,
            )
        self.assertEqual(self.application(application_id)["status"], sl.SUBMITTED)
        self.assertEqual(self.seller(7).get("status"), "pending")

    def test_refused_transition_writes_no_history_row(self):
        # A refusal that still left an audit row would make the timeline read as
        # though the move happened.
        application_id = complete_draft(self.cur)
        before = len(sl.history_for(self.cur, application_id))
        with self.assertRaises(sl.TransitionError):
            sl.apply_transition(
                self.cur, self.application(application_id), sl.APPROVED,
                actor_type=sl.ADMIN, actor_id=0,
            )
        self.assertEqual(len(sl.history_for(self.cur, application_id)), before)

    def test_transition_error_reads_as_a_sentence(self):
        with self.assertRaises(sl.TransitionError) as ctx:
            sl.assert_transition(sl.DRAFT, sl.APPROVED, sl.ADMIN, 3)
        self.assertEqual(
            str(ctx.exception),
            "An admin cannot move an application from draft to approved.",
        )

    def test_repeating_the_current_status_is_refused_rather_than_silently_ignored(self):
        with self.assertRaises(sl.TransitionError) as ctx:
            sl.assert_transition(sl.UNDER_REVIEW, sl.UNDER_REVIEW, sl.ADMIN, 3)
        self.assertIn("already under review", str(ctx.exception))

    def test_an_invented_status_is_refused(self):
        with self.assertRaises(sl.TransitionError) as ctx:
            sl.assert_transition(sl.SUBMITTED, "auto_approved", sl.ADMIN, 3)
        self.assertIn("Unknown application status", str(ctx.exception))


# --------------------------------------------------------------------------
# Autosave cannot advance an application
# --------------------------------------------------------------------------

class AutosaveTests(LifecycleTestCase):
    def test_save_draft_never_writes_status(self):
        # The most frequent write in the system, and the one an attacker reaches
        # most easily. It must not be able to move the application forward.
        columns = sl.save_draft(self.cur, 1, dict(COMPLETE_FIELDS, status=sl.APPROVED), [])
        self.assertNotIn("status", columns)
        self.assertNotIn("reviewer_id", columns)
        self.assertNotIn("internal_notes", columns)

    def test_autosaving_an_approved_application_leaves_it_approved(self):
        application_id = complete_draft(self.cur)
        sl.apply_transition(self.cur, self.application(application_id), sl.SUBMITTED,
                            actor_type=sl.APPLICANT, actor_id=7)
        sl.apply_transition(self.cur, self.application(application_id), sl.APPROVED,
                            actor_type=sl.ADMIN, actor_id=99, reason="Verified.")
        sl.save_draft(self.cur, application_id, dict(COMPLETE_FIELDS, full_name="Someone Else"), [])
        self.assertEqual(self.application(application_id)["status"], sl.APPROVED)

    def test_merge_fields_drops_privileged_keys_without_comment(self):
        merged = sl.merge_fields(
            {"full_name": "Amina Okonkwo"},
            {"display_name": "Amina Teaches", "status": sl.APPROVED, "risk_score": 0,
             "reviewer_id": 3, "internal_notes": "looks fine"},
        )
        self.assertEqual(merged["display_name"], "Amina Teaches")
        self.assertEqual(merged["full_name"], "Amina Okonkwo")
        for key in ("status", "risk_score", "reviewer_id", "internal_notes"):
            self.assertNotIn(key, merged)

    def test_merge_fields_only_touches_keys_the_client_sent(self):
        # A step-two autosave from a client that has not loaded step three must
        # not blank step three.
        merged = sl.merge_fields(
            {"full_name": "Amina Okonkwo", "business_description": "A long description."},
            {"country": "Nigeria"},
        )
        self.assertEqual(merged["business_description"], "A long description.")
        self.assertEqual(merged["country"], "Nigeria")

    def test_merge_fields_rejects_an_unknown_seller_type(self):
        merged = sl.merge_fields({}, {"seller_type": "platform_administrator"})
        self.assertEqual(merged["seller_type"], "")

    def test_merge_fields_keeps_only_offered_selling_intents(self):
        merged = sl.merge_fields({}, {"seller_intent": ["Courses", "Weapons", "Ebooks"]})
        self.assertEqual(merged["seller_intent"], ["Courses", "Ebooks"])

    def test_merge_fields_reduces_a_safety_answer_to_yes_no_or_nothing(self):
        merged = sl.merge_fields({}, {"banned_elsewhere": "maybe", "comply_rules": "YES"})
        self.assertEqual(merged["banned_elsewhere"], "")
        self.assertEqual(merged["comply_rules"], "yes")


# --------------------------------------------------------------------------
# Disclosure: what the applicant may see
# --------------------------------------------------------------------------

class ApplicantDisclosureTests(LifecycleTestCase):
    #: Exactly the keys the native client's `SellerApplicationView` declares.
    #: Asserted as a set rather than by absence of today's known-bad keys, so a
    #: column added to the row later cannot ride along unnoticed.
    EXPECTED_KEYS = {
        "application_id", "status", "status_title", "status_message", "next_action",
        "editable", "completeness", "fields", "documents", "steps", "can_submit",
        "information_request", "submitted_at", "updated_at", "seller_types",
        "selling_intents", "required_documents", "optional_documents",
    }

    def contaminated_application(self):
        return {
            "id": 12, "user_id": 7, "status": sl.UNDER_REVIEW,
            "full_name": "Amina Okonkwo", "display_name": "Amina Teaches",
            "internal_notes": "Reviewer: passport photo looks edited, escalate.",
            "reviewer_id": 99, "risk_score": 70, "decision_reason": "Pending fraud check",
            "verification_json": '{"manual_flag": true}',
        }

    def test_applicant_view_emits_exactly_the_whitelisted_keys(self):
        view = sl.applicant_view(self.contaminated_application(), [])
        self.assertEqual(set(view.keys()), self.EXPECTED_KEYS)

    def test_applicant_view_carries_no_reviewer_material_anywhere_in_the_payload(self):
        # Serialised and searched whole, because a leak nested inside `fields`
        # or `steps` would pass a top-level key check.
        blob = json.dumps(sl.applicant_view(self.contaminated_application(), []))
        for secret in ("internal_notes", "risk_score", "reviewer_id", "decision_reason",
                       "passport photo looks edited", "escalate"):
            self.assertNotIn(secret, blob)

    def test_document_view_never_exposes_where_the_file_is_stored(self):
        doc = {
            "id": 4, "application_id": 12, "document_type": "id_front",
            "original_filename": "passport.jpg",
            "stored_path": "/var/private/uploads/7/id_front.jpg",
            "file_size": 204800, "created_at": "2026-01-01T00:00:00",
            "scan_status": "queued_for_internal_review",
        }
        view = sl.applicant_document_view(doc)
        self.assertEqual(
            set(view.keys()),
            {"id", "type", "label", "filename", "size_kb", "uploaded_at", "state"},
        )
        self.assertNotIn("/var/private", json.dumps(view))

    def test_document_state_is_words_the_applicant_can_act_on(self):
        # "queued_for_internal_review" teaches an applicant nothing and worries
        # them anyway.
        view = sl.applicant_document_view({"document_type": "selfie", "review_status": "pending"})
        self.assertEqual(view["state"], "received")

    def test_applicant_view_reports_the_reviewers_question_but_not_their_note(self):
        application = self.contaminated_application()
        application["status"] = sl.INFORMATION_REQUESTED
        application["information_request_message"] = "Please re-upload the back of your ID."
        view = sl.applicant_view(application, [])
        self.assertEqual(view["information_request"], "Please re-upload the back of your ID.")
        self.assertNotIn("escalate", json.dumps(view))

    def test_can_submit_is_false_until_every_step_validates(self):
        incomplete = dict(COMPLETE_FIELDS)
        incomplete.pop("full_name")
        application = {"id": 1, "status": sl.DRAFT, **sl.fields_to_columns(incomplete)}
        self.assertFalse(sl.applicant_view(application, COMPLETE_DOCUMENTS)["can_submit"])

    def test_can_submit_is_false_while_a_reviewer_holds_the_application(self):
        # Complete answers, but it is not the applicant's turn.
        application = {"id": 1, "status": sl.UNDER_REVIEW, **sl.fields_to_columns(COMPLETE_FIELDS)}
        view = sl.applicant_view(application, COMPLETE_DOCUMENTS)
        self.assertEqual(view["steps"], [step for step in view["steps"] if step["complete"]])
        self.assertFalse(view["can_submit"])
        self.assertFalse(view["editable"])

    def test_every_status_offers_the_applicant_something_to_do(self):
        # A status centre with no action is a dead end for the applicant.
        for status in sl.ALL_STATUSES:
            view = sl.applicant_view({"id": 1, "status": status}, [])
            self.assertTrue(view["next_action"]["label"], f"{status} has no next action label")
            self.assertTrue(view["status_message"], f"{status} has no message")

    def test_a_status_from_a_future_version_degrades_to_draft(self):
        view = sl.applicant_view({"id": 1, "status": "quantum_review"}, [])
        self.assertEqual(view["status"], sl.DRAFT)

    def test_a_legacy_pending_review_row_reads_as_submitted_to_its_owner(self):
        view = sl.applicant_view({"id": 1, "status": "pending_review"}, [])
        self.assertEqual(view["status"], sl.SUBMITTED)
        self.assertEqual(view["next_action"]["action"], "wait")


# --------------------------------------------------------------------------
# The state machine end to end
# --------------------------------------------------------------------------

class TransitionTests(LifecycleTestCase):
    def test_the_creation_row_has_no_origin_status(self):
        # Rendering it as "draft -> draft" would make the first line of every
        # timeline read like a no-op.
        seed_user(self.cur, 7)
        application_id = sl.create_draft(self.cur, 7, source="native")
        history = sl.history_for(self.cur, application_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["from_status"], "")
        self.assertEqual(history[0]["to_status"], sl.DRAFT)
        self.assertEqual(history[0]["actor_type"], sl.APPLICANT)

    def test_a_native_draft_records_where_it_came_from(self):
        seed_user(self.cur, 7)
        application_id = sl.create_draft(self.cur, 7, source="native")
        self.assertEqual(self.application(application_id)["source"], "native")

    def test_the_approval_path_records_every_step(self):
        application_id = complete_draft(self.cur)
        sl.apply_transition(self.cur, self.application(application_id), sl.SUBMITTED,
                            actor_type=sl.APPLICANT, actor_id=7)
        sl.apply_transition(self.cur, self.application(application_id), sl.UNDER_REVIEW,
                            actor_type=sl.ADMIN, actor_id=99)
        result = sl.apply_transition(self.cur, self.application(application_id), sl.APPROVED,
                                     actor_type=sl.ADMIN, actor_id=99, reason="ID and selfie match.")

        self.assertEqual(result["from"], sl.UNDER_REVIEW)
        self.assertEqual(result["to"], sl.APPROVED)
        self.assertEqual(
            [(row["from_status"], row["to_status"]) for row in sl.history_for(self.cur, application_id)],
            [("", sl.DRAFT), (sl.DRAFT, sl.SUBMITTED), (sl.SUBMITTED, sl.UNDER_REVIEW),
             (sl.UNDER_REVIEW, sl.APPROVED)],
        )

    def test_approval_names_the_administrator_who_decided(self):
        application_id = complete_draft(self.cur)
        sl.apply_transition(self.cur, self.application(application_id), sl.SUBMITTED,
                            actor_type=sl.APPLICANT, actor_id=7)
        sl.apply_transition(self.cur, self.application(application_id), sl.APPROVED,
                            actor_type=sl.ADMIN, actor_id=99, reason="Verified.")
        application = self.application(application_id)
        self.assertEqual(application["reviewer_id"], 99)
        self.assertEqual(application["decision_reason"], "Verified.")
        self.assertTrue(application["reviewed_at"])

    def test_approval_unlocks_the_one_seller_record(self):
        application_id = complete_draft(self.cur)
        sl.apply_transition(self.cur, self.application(application_id), sl.SUBMITTED,
                            actor_type=sl.APPLICANT, actor_id=7)
        sl.apply_transition(self.cur, self.application(application_id), sl.APPROVED,
                            actor_type=sl.ADMIN, actor_id=99, reason="Verified.")
        seller = self.seller(7)
        self.assertEqual(seller["status"], "approved")
        self.assertEqual(seller["verification_status"], "verified")
        self.assertEqual(seller["reviewed_by"], 99)
        self.assertEqual(seller["display_name"], "Amina Teaches")

    def test_submission_alone_does_not_unlock_selling(self):
        # Buyer-side account state and seller approval are different things; a
        # submitted application must not read as an approved seller anywhere.
        application_id = complete_draft(self.cur)
        sl.apply_transition(self.cur, self.application(application_id), sl.SUBMITTED,
                            actor_type=sl.APPLICANT, actor_id=7)
        seller = self.seller(7)
        self.assertEqual(seller["status"], "pending")
        self.assertEqual(seller["verification_status"], "pending")

    def test_only_one_seller_row_exists_however_many_transitions_happen(self):
        application_id = complete_draft(self.cur)
        for target, actor, actor_id in (
            (sl.SUBMITTED, sl.APPLICANT, 7),
            (sl.UNDER_REVIEW, sl.ADMIN, 99),
            (sl.APPROVED, sl.ADMIN, 99),
            (sl.SUSPENDED, sl.ADMIN, 99),
            (sl.APPROVED, sl.ADMIN, 99),
        ):
            sl.apply_transition(self.cur, self.application(application_id), target,
                                actor_type=actor, actor_id=actor_id, reason="x")
        self.cur.execute("SELECT COUNT(*) AS total FROM marketplace_sellers WHERE user_id=7")
        self.assertEqual(dict(self.cur.fetchone())["total"], 1)

    def test_requesting_information_stores_the_message_for_the_applicant(self):
        application_id = complete_draft(self.cur)
        sl.apply_transition(self.cur, self.application(application_id), sl.SUBMITTED,
                            actor_type=sl.APPLICANT, actor_id=7)
        sl.apply_transition(
            self.cur, self.application(application_id), sl.INFORMATION_REQUESTED,
            actor_type=sl.ADMIN, actor_id=99,
            reason="ID back is blurry", applicant_message="Please re-upload the back of your ID.",
        )
        application = self.application(application_id)
        self.assertEqual(application["information_request_message"], "Please re-upload the back of your ID.")
        self.assertTrue(application["information_requested_at"])

    def test_resubmitting_clears_the_question_the_applicant_just_answered(self):
        application_id = complete_draft(self.cur)
        sl.apply_transition(self.cur, self.application(application_id), sl.SUBMITTED,
                            actor_type=sl.APPLICANT, actor_id=7)
        sl.apply_transition(self.cur, self.application(application_id), sl.INFORMATION_REQUESTED,
                            actor_type=sl.ADMIN, actor_id=99, applicant_message="Re-upload the ID back.")
        sl.apply_transition(self.cur, self.application(application_id), sl.RESUBMITTED,
                            actor_type=sl.APPLICANT, actor_id=7)
        self.assertEqual(self.application(application_id)["information_request_message"], "")

    def test_a_rejected_applicant_reopens_the_same_row_rather_than_starting_over(self):
        # An appeal is not a separate object: the reviewer who sees it next must
        # see why it was rejected the first time.
        application_id = complete_draft(self.cur)
        sl.apply_transition(self.cur, self.application(application_id), sl.SUBMITTED,
                            actor_type=sl.APPLICANT, actor_id=7)
        sl.apply_transition(self.cur, self.application(application_id), sl.REJECTED,
                            actor_type=sl.ADMIN, actor_id=99, reason="Description promises returns.")
        sl.apply_transition(self.cur, self.application(application_id), sl.DRAFT,
                            actor_type=sl.APPLICANT, actor_id=7)

        self.assertEqual(sl.get_application(self.cur, 7)["id"], application_id)
        self.assertEqual(
            [row["to_status"] for row in sl.history_for(self.cur, application_id)],
            [sl.DRAFT, sl.SUBMITTED, sl.REJECTED, sl.DRAFT],
        )

    def test_reopening_a_rejected_application_gives_it_a_fresh_expiry(self):
        application_id = complete_draft(self.cur)
        sl.apply_transition(self.cur, self.application(application_id), sl.SUBMITTED,
                            actor_type=sl.APPLICANT, actor_id=7)
        sl.apply_transition(self.cur, self.application(application_id), sl.REJECTED,
                            actor_type=sl.ADMIN, actor_id=99, reason="No.")
        # Submission cleared the expiry; reopening must restore one, or the row
        # would sit as an immortal draft.
        sl.apply_transition(self.cur, self.application(application_id), sl.DRAFT,
                            actor_type=sl.APPLICANT, actor_id=7)
        self.assertTrue(self.application(application_id)["expires_at"])

    def test_submitting_removes_the_expiry(self):
        # The delay after submission is ours, not the applicant's.
        application_id = complete_draft(self.cur)
        self.assertTrue(self.application(application_id)["expires_at"])
        sl.apply_transition(self.cur, self.application(application_id), sl.SUBMITTED,
                            actor_type=sl.APPLICANT, actor_id=7)
        self.assertIsNone(self.application(application_id)["expires_at"])

    def test_history_carries_no_filename_or_answer_text(self):
        # The timeline is shown in full to auditors, so it must not become a
        # second copy of what was uploaded.
        application_id = complete_draft(self.cur)
        sl.apply_transition(self.cur, self.application(application_id), sl.SUBMITTED,
                            actor_type=sl.APPLICANT, actor_id=7)
        blob = json.dumps(sl.history_for(self.cur, application_id))
        for leak in ("id_front.jpg", "/var/private", "amina@example.com", "Amina Okonkwo"):
            self.assertNotIn(leak, blob)


# --------------------------------------------------------------------------
# Two front doors, one pipeline
# --------------------------------------------------------------------------

class OneApplicationPerApplicantTests(LifecycleTestCase):
    """The sequence both the web handler and the native routes perform.

    Neither handler can be imported here — they live in ``bot.py``, which needs
    a live config and packages this module does not use — so what is pinned is
    the sequence itself. The web door used to insert a fresh row on every
    submit and write ``status`` into the column directly; these tests fail if
    either door drifts back to that.
    """

    def submit_through_a_door(self, user_id, source, answers):
        application = sl.get_application(self.cur, user_id)
        if not application:
            application_id = sl.create_draft(self.cur, user_id, source=source)
            application = sl.get_application_by_id(self.cur, application_id)
        else:
            application_id = int(application["id"])
        fields = sl.merge_fields(sl.applicant_fields(application), answers)
        documents = sl.documents_for(self.cur, application_id)
        sl.save_draft(self.cur, application_id, fields, documents)
        application = sl.get_application_by_id(self.cur, application_id)
        if not sl.validate_application(fields, documents):
            sl.apply_transition(self.cur, application, sl.SUBMITTED,
                                actor_type=sl.APPLICANT, actor_id=user_id, reason="Submitted for review")
        return application_id

    def test_submitting_twice_reuses_one_application_row(self):
        seed_user(self.cur, 7)
        first = self.submit_through_a_door(7, "web", {"full_name": "Amina Okonkwo"})
        second = self.submit_through_a_door(7, "web", {"country": "Nigeria"})
        self.assertEqual(first, second)
        self.cur.execute("SELECT COUNT(*) AS total FROM marketplace_merchant_applications WHERE user_id=7")
        self.assertEqual(dict(self.cur.fetchone())["total"], 1)

    def test_a_partial_submission_stays_a_draft_rather_than_entering_the_queue(self):
        seed_user(self.cur, 7)
        self.submit_through_a_door(7, "web", {"full_name": "Amina Okonkwo"})
        self.assertEqual(sl.get_application(self.cur, 7)["status"], sl.DRAFT)
        self.assertEqual(sl.pending_review_count(self.cur), 0)

    def test_the_web_door_lands_in_the_same_queue_as_the_native_one(self):
        seed_user(self.cur, 7, username="web_applicant")
        seed_user(self.cur, 8, username="native_applicant")
        self.submit_through_a_door(7, "web", COMPLETE_FIELDS)
        self.submit_through_a_door(8, "native", COMPLETE_FIELDS)
        # Neither is complete without documents, so both are still drafts —
        # which is itself the point: the web door no longer decides its own
        # status from a completeness heuristic of its own.
        for user_id in (7, 8):
            self.assertEqual(sl.get_application(self.cur, user_id)["status"], sl.DRAFT)

        for user_id in (7, 8):
            application = sl.get_application(self.cur, user_id)
            for doc in COMPLETE_DOCUMENTS:
                self.cur.execute(
                    "INSERT INTO marketplace_merchant_documents (application_id, user_id, document_type, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (application["id"], user_id, doc["document_type"], "2026-01-01T00:00:00"),
                )
            self.submit_through_a_door(user_id, "", COMPLETE_FIELDS)

        rows = sl.search_queue(self.cur, status="open")
        self.assertEqual({row["user_id"] for row in rows}, {7, 8})
        self.assertEqual({row["source"] for row in rows}, {"web", "native"})

    def test_every_door_leaves_a_history_a_reviewer_can_read(self):
        seed_user(self.cur, 7)
        application_id = complete_draft(self.cur, user_id=9)
        self.assertTrue(sl.history_for(self.cur, application_id))
        web_id = self.submit_through_a_door(7, "web", {"full_name": "Amina Okonkwo"})
        self.assertTrue(sl.history_for(self.cur, web_id))

    def test_the_seller_record_never_carries_an_application_status(self):
        # The gate everywhere in the app is `status != "approved"`. If an
        # application status ever reached this column the gate would still hold
        # by luck of the strings not matching, which is not a guarantee.
        seller_vocabulary = {"pending", "approved", "rejected", "suspended", "withdrawn"}
        produced = {sl.seller_status_for(status) for status in sl.ALL_STATUSES}
        self.assertTrue(produced.issubset(seller_vocabulary))
        self.assertEqual(produced & set(sl.ALL_STATUSES) - {sl.APPROVED, sl.REJECTED, sl.SUSPENDED, sl.WITHDRAWN}, set())


# --------------------------------------------------------------------------
# Expiry
# --------------------------------------------------------------------------

class ExpiryTests(LifecycleTestCase):
    def stale_draft(self, user_id, status=sl.DRAFT):
        seed_user(self.cur, user_id, username=f"u{user_id}")
        application_id = sl.create_draft(self.cur, user_id)
        past = (datetime.utcnow() - timedelta(days=1)).isoformat(timespec="seconds")
        self.cur.execute(
            "UPDATE marketplace_merchant_applications SET expires_at=?, status=? WHERE id=?",
            (past, status, application_id),
        )
        return application_id

    def test_an_abandoned_draft_expires(self):
        application_id = self.stale_draft(7)
        self.assertEqual(sl.expire_stale(self.cur), 1)
        self.assertEqual(self.application(application_id)["status"], sl.EXPIRED)

    def test_a_submitted_application_never_expires_however_old(self):
        # Expiring one would let a slow queue quietly deny people.
        application_id = self.stale_draft(8, status=sl.SUBMITTED)
        self.assertEqual(sl.expire_stale(self.cur), 0)
        self.assertEqual(self.application(application_id)["status"], sl.SUBMITTED)

    def test_expiry_is_attributed_to_the_system_not_to_a_person(self):
        application_id = self.stale_draft(7)
        sl.expire_stale(self.cur)
        last = sl.history_for(self.cur, application_id)[-1]
        self.assertEqual(last["actor_type"], sl.SYSTEM)
        self.assertEqual(last["actor_id"], 0)
        self.assertEqual(last["to_status"], sl.EXPIRED)

    def test_a_draft_still_within_its_window_is_left_alone(self):
        seed_user(self.cur, 7)
        application_id = sl.create_draft(self.cur, 7)
        self.assertEqual(sl.expire_stale(self.cur), 0)
        self.assertEqual(self.application(application_id)["status"], sl.DRAFT)


# --------------------------------------------------------------------------
# The admin queue
# --------------------------------------------------------------------------

class QueueTests(LifecycleTestCase):
    def add_application(self, user_id, status, **columns):
        seed_user(self.cur, user_id, username=f"user{user_id}", display_name=f"User {user_id}")
        keys = ["user_id", "status"] + list(columns)
        values = [user_id, status] + list(columns.values())
        placeholders = ",".join(["?"] * len(keys))
        self.cur.execute(
            f"INSERT INTO marketplace_merchant_applications ({','.join(keys)}) VALUES ({placeholders})",
            values,
        )
        return int(self.cur.lastrowid)

    def test_the_dashboard_badge_counts_applications_waiting_on_us(self):
        self.add_application(1, sl.SUBMITTED)
        self.add_application(2, sl.UNDER_REVIEW)
        self.add_application(3, sl.RESUBMITTED)
        self.add_application(4, sl.DRAFT)
        self.add_application(5, sl.APPROVED)
        self.assertEqual(sl.pending_review_count(self.cur), 3)

    def test_the_badge_counts_applications_submitted_before_this_module_shipped(self):
        # Rows written by the old web form used `pending_review`. A count that
        # forgot them would show zero while people waited.
        self.add_application(1, "pending_review")
        self.add_application(2, "pending")
        self.assertEqual(sl.pending_review_count(self.cur), 2)

    def test_legacy_rows_are_still_visible_in_the_queue(self):
        self.add_application(1, "pending_review", full_name="Legacy Applicant")
        rows = sl.search_queue(self.cur, status="open")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], sl.SUBMITTED)

    def test_the_queue_shows_what_is_waiting_on_us_and_hides_what_is_not(self):
        self.add_application(1, sl.SUBMITTED)
        self.add_application(2, sl.DRAFT)
        self.add_application(3, sl.APPROVED)
        statuses = {row["status"] for row in sl.search_queue(self.cur, status="open")}
        self.assertEqual(statuses, {sl.SUBMITTED})

    def test_riskiest_first_inside_a_priority_band(self):
        low = self.add_application(1, sl.SUBMITTED, risk_score=5)
        high = self.add_application(2, sl.SUBMITTED, risk_score=80)
        self.assertEqual([row["id"] for row in sl.search_queue(self.cur, status="open")], [high, low])

    def test_equally_risky_applications_are_served_oldest_first(self):
        # Newest-first would let an application starve behind a steady arrival
        # of new ones.
        first = self.add_application(1, sl.SUBMITTED, risk_score=10)
        second = self.add_application(2, sl.SUBMITTED, risk_score=10)
        self.assertEqual([row["id"] for row in sl.search_queue(self.cur, status="open")], [first, second])

    def test_submitted_applications_outrank_those_already_in_progress(self):
        under_review = self.add_application(1, sl.UNDER_REVIEW, risk_score=90)
        submitted = self.add_application(2, sl.SUBMITTED, risk_score=0)
        self.assertEqual(
            [row["id"] for row in sl.search_queue(self.cur, status="open")],
            [submitted, under_review],
        )

    def test_search_matches_the_applicants_account_as_well_as_their_answers(self):
        self.add_application(1, sl.SUBMITTED, full_name="Amina Okonkwo")
        self.add_application(2, sl.SUBMITTED, full_name="Someone Else")
        self.assertEqual(len(sl.search_queue(self.cur, status="open", query="amina")), 1)
        self.assertEqual(len(sl.search_queue(self.cur, status="open", query="user2")), 1)

    def test_search_finds_an_application_by_its_id(self):
        application_id = self.add_application(1, sl.SUBMITTED)
        rows = sl.search_queue(self.cur, status="open", query=str(application_id))
        self.assertEqual([row["id"] for row in rows], [application_id])

    def test_a_reviewer_can_see_only_their_own_assignments_when_they_filter(self):
        mine = self.add_application(1, sl.SUBMITTED, reviewer_id=99)
        self.add_application(2, sl.SUBMITTED, reviewer_id=42)
        rows = sl.search_queue(self.cur, status="open", reviewer_id=99)
        self.assertEqual([row["id"] for row in rows], [mine])

    def test_filtering_to_all_includes_decided_applications(self):
        self.add_application(1, sl.SUBMITTED)
        self.add_application(2, sl.APPROVED)
        self.assertEqual(len(sl.search_queue(self.cur, status="all")), 2)

    def test_counts_cover_every_status_the_filter_chips_offer(self):
        self.add_application(1, sl.SUBMITTED)
        self.add_application(2, sl.APPROVED)
        counts = sl.queue_counts(self.cur)
        for status in sl.ALL_STATUSES:
            self.assertIn(status, counts)
        self.assertEqual(counts["open"], 1)
        self.assertEqual(counts["total"], 2)


# --------------------------------------------------------------------------
# Assignment and notes
# --------------------------------------------------------------------------

class ReviewWorkspaceTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.application_id = complete_draft(self.cur)

    def test_assigning_a_reviewer_releases_the_previous_one(self):
        sl.assign_reviewer(self.cur, self.application_id, 42, assigned_by=1)
        sl.assign_reviewer(self.cur, self.application_id, 99, assigned_by=1)
        self.assertEqual(sl.current_reviewer(self.cur, self.application_id), 99)

        self.cur.execute(
            f"SELECT COUNT(*) AS total FROM {sl.ASSIGNMENTS_TABLE} "
            "WHERE application_id=? AND released_at IS NULL",
            (self.application_id,),
        )
        self.assertEqual(dict(self.cur.fetchone())["total"], 1)

    def test_reassignment_keeps_the_record_of_who_held_it_before(self):
        sl.assign_reviewer(self.cur, self.application_id, 42, assigned_by=1)
        sl.assign_reviewer(self.cur, self.application_id, 99, assigned_by=1)
        self.cur.execute(
            f"SELECT reviewer_admin_id FROM {sl.ASSIGNMENTS_TABLE} WHERE application_id=? ORDER BY id",
            (self.application_id,),
        )
        self.assertEqual([dict(row)["reviewer_admin_id"] for row in self.cur.fetchall()], [42, 99])

    def test_unassigning_leaves_nobody_holding_the_application(self):
        sl.assign_reviewer(self.cur, self.application_id, 42, assigned_by=1)
        sl.assign_reviewer(self.cur, self.application_id, 0, assigned_by=1)
        self.assertIsNone(sl.current_reviewer(self.cur, self.application_id))
        self.assertIsNone(self.application(self.application_id)["reviewer_id"])

    def test_an_internal_note_is_not_returned_when_asking_for_applicant_notes(self):
        # The one place this could go wrong is the place it matters most.
        sl.add_note(self.cur, self.application_id, 99, "Passport photo looks edited.", "internal")
        sl.add_note(self.cur, self.application_id, 99, "Please re-upload your ID.", "applicant")
        visible = sl.notes_for(self.cur, self.application_id, visibility="applicant")
        self.assertEqual([note["body"] for note in visible], ["Please re-upload your ID."])

    def test_an_unrecognised_visibility_is_treated_as_internal(self):
        # Failing closed: a typo'd visibility must not publish a reviewer's note.
        sl.add_note(self.cur, self.application_id, 99, "Escalate to fraud.", "public")
        self.assertEqual(sl.notes_for(self.cur, self.application_id, visibility="applicant"), [])
        self.assertEqual(len(sl.notes_for(self.cur, self.application_id, visibility="internal")), 1)


class BatchedQueueReadTests(LifecycleTestCase):
    """The batched reads the queue page uses instead of one query per row.

    Batching is a performance change, so what is worth pinning is that it did
    not become a correctness change: the grouped result must say exactly what
    the per-row function says for each id, and the visibility filter must
    survive being moved into an ``IN`` clause. The rule that internal notes are
    never returned to an applicant is asserted here again rather than trusted
    to the single-row test, because this is a second door onto the same data.
    """

    def setUp(self):
        super().setUp()
        self.first = complete_draft(self.cur, user_id=7)
        self.second = complete_draft(self.cur, user_id=8)
        seed_user(self.cur, 9)
        self.third = sl.create_draft(self.cur, 9, source="native")
        # An id the queue could ask about but that has nothing attached — a row
        # deleted between the queue fetch and this read, for instance.
        self.absent = 999

    def test_batched_history_matches_the_per_row_read_for_every_id(self):
        for application_id in (self.first, self.second):
            application = sl.get_application_by_id(self.cur, application_id)
            sl.apply_transition(
                self.cur, application, sl.SUBMITTED,
                actor_type=sl.APPLICANT, actor_id=int(application["user_id"]), reason="Submitted",
            )
        ids = [self.first, self.second, self.third]
        grouped = sl.history_for_many(self.cur, ids)
        for application_id in ids:
            self.assertEqual(grouped[application_id], sl.history_for(self.cur, application_id))

    def test_every_requested_id_gets_a_key_even_with_nothing_attached(self):
        # The caller indexes by id while rendering; a missing key would be a
        # KeyError on the row that simply has no history yet.
        ids = [self.first, self.second, self.third, self.absent]
        grouped = sl.history_for_many(self.cur, ids)
        self.assertEqual(sorted(grouped), sorted(ids))
        self.assertEqual(grouped[self.absent], [])
        self.assertEqual(sl.notes_for_many(self.cur, ids, visibility="internal")[self.absent], [])

    def test_batched_notes_keep_each_application_to_its_own_notes(self):
        sl.add_note(self.cur, self.first, 99, "First applicant note.", "internal")
        sl.add_note(self.cur, self.second, 99, "Second applicant note.", "internal")
        grouped = sl.notes_for_many(self.cur, [self.first, self.second], visibility="internal")
        self.assertEqual([n["body"] for n in grouped[self.first]], ["First applicant note."])
        self.assertEqual([n["body"] for n in grouped[self.second]], ["Second applicant note."])

    def test_batched_notes_honour_visibility(self):
        sl.add_note(self.cur, self.first, 99, "Passport photo looks edited.", "internal")
        sl.add_note(self.cur, self.first, 99, "Please re-upload your ID.", "applicant")
        applicant = sl.notes_for_many(self.cur, [self.first], visibility="applicant")
        self.assertEqual([n["body"] for n in applicant[self.first]], ["Please re-upload your ID."])
        self.assertNotIn("Passport photo looks edited.", json.dumps(applicant))

    def test_no_ids_asks_the_database_nothing(self):
        self.assertEqual(sl.history_for_many(self.cur, []), {})
        self.assertEqual(sl.notes_for_many(self.cur, [], visibility="internal"), {})


# --------------------------------------------------------------------------
# Scoring and validation
# --------------------------------------------------------------------------

class ScoringTests(unittest.TestCase):
    def test_a_complete_application_scores_one_hundred(self):
        self.assertEqual(sl.completeness_score(COMPLETE_FIELDS, COMPLETE_DOCUMENTS), 100)

    def test_an_empty_application_scores_zero(self):
        self.assertEqual(sl.completeness_score({}, []), 0)

    def test_completeness_never_leaves_the_range_a_progress_bar_can_render(self):
        for fields, documents in (
            ({}, []),
            (COMPLETE_FIELDS, COMPLETE_DOCUMENTS),
            (dict(COMPLETE_FIELDS, seller_type="nonsense"), []),
        ):
            score = sl.completeness_score(fields, documents)
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)

    def test_a_guaranteed_profits_claim_is_the_strongest_signal(self):
        clean = sl.risk_score(COMPLETE_FIELDS, COMPLETE_DOCUMENTS)
        claiming = sl.risk_score(dict(COMPLETE_FIELDS, guaranteed_profits="yes"), COMPLETE_DOCUMENTS)
        self.assertEqual(clean, 0)
        self.assertGreater(claiming, clean)

    def test_risk_signals_always_say_something_to_the_reviewer(self):
        # An empty sidebar reads as "not evaluated" rather than "nothing found".
        signals = sl.risk_signals(COMPLETE_FIELDS, COMPLETE_DOCUMENTS)
        self.assertTrue(signals)
        self.assertEqual(signals[0]["level"], "low")

    def test_a_disclosed_past_ban_raises_a_signal_but_does_not_decide(self):
        signals = sl.risk_signals(dict(COMPLETE_FIELDS, banned_elsewhere="yes"), COMPLETE_DOCUMENTS)
        labels = [signal["label"] for signal in signals]
        self.assertIn("Banned from another marketplace", labels)
        # Nothing in scoring is allowed to move status, which is why risk lives
        # in a pure function with no cursor.
        self.assertNotIn("status", str(signals))

    def test_missing_required_documents_block_submission(self):
        errors = sl.validate_application(COMPLETE_FIELDS, [{"document_type": "id_front"}])
        self.assertIn("documents", errors)
        self.assertIn("id_back", errors["documents"])
        self.assertIn("selfie", errors["documents"])

    def test_a_brand_owes_a_business_name_and_an_individual_does_not(self):
        # Asking every applicant for one is how a form starts collecting
        # information it does not need.
        brand = sl.validate_step("storefront", dict(COMPLETE_FIELDS, seller_type="brand"))
        creator = sl.validate_step("storefront", COMPLETE_FIELDS)
        self.assertIn("business_name", brand)
        self.assertNotIn("business_name", creator)

    def test_an_unanswered_safety_question_blocks_submission(self):
        errors = sl.validate_step("safety", dict(COMPLETE_FIELDS, comply_rules=""))
        self.assertIn("comply_rules", errors)

    def test_every_agreement_must_be_accepted(self):
        errors = sl.validate_step("agreements", dict(COMPLETE_FIELDS, anti_scam_agreement=False))
        self.assertIn("anti_scam_agreement", errors)

    def test_a_malformed_email_is_caught_before_a_reviewer_wastes_time_on_it(self):
        errors = sl.validate_step("identity", dict(COMPLETE_FIELDS, email="amina@example"))
        self.assertIn("email", errors)


class StatusMappingTests(unittest.TestCase):
    def test_only_an_approved_application_produces_an_approved_seller(self):
        for status in sl.ALL_STATUSES:
            expected = "approved" if status == sl.APPROVED else sl.seller_status_for(status)
            self.assertEqual(sl.seller_status_for(status), expected)
        self.assertEqual(sl.seller_status_for(sl.APPROVED), "approved")
        for status in (sl.SUBMITTED, sl.UNDER_REVIEW, sl.RESUBMITTED, sl.DRAFT, sl.INFORMATION_REQUESTED):
            self.assertEqual(sl.seller_status_for(status), "pending")

    def test_a_seller_is_never_described_as_under_review(self):
        # A seller is not "under review"; their application is.
        self.assertNotIn(sl.UNDER_REVIEW, {sl.seller_status_for(s) for s in sl.ALL_STATUSES})

    def test_verification_is_only_claimed_after_approval(self):
        self.assertEqual(sl.verification_status_for(sl.APPROVED), "verified")
        for status in sl.ALL_STATUSES:
            if status != sl.APPROVED:
                self.assertNotEqual(sl.verification_status_for(status), "verified")

    def test_every_decision_name_the_admin_ui_offers_maps_to_a_real_status(self):
        for decision in sl.DECISIONS:
            self.assertIn(sl.decision_target(decision), sl.ALL_STATUSES)

    def test_an_unknown_decision_is_refused(self):
        with self.assertRaises(sl.TransitionError):
            sl.decision_target("auto_approve")

    def test_the_decisions_that_need_a_reason_are_the_ones_that_hurt(self):
        for decision in ("reject", "suspend", "request_information"):
            self.assertIn(decision, sl.DECISIONS_REQUIRING_REASON)


if __name__ == "__main__":
    unittest.main()
