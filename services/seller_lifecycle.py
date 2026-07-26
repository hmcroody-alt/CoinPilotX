"""
The seller application lifecycle: one pipeline, two front doors.

PulseSoc already had a serious merchant application — identity documents, selfie
verification, trust-and-safety attestations, completeness and risk scoring, and
an admin review queue with a document viewer. It existed only as an HTML form at
``/pulse/merchant/apply``. The native app had its own "application": two fields,
``display_name`` and ``bio``, posted to an endpoint that inserted straight into
``marketplace_sellers`` with ``status='pending'``.

So a native applicant never appeared in the review queue, uploaded no documents,
carried no completeness or risk score, and left an admin with nothing to review.
That is not a second application system; it is a hole in the first one.

This module is the shared middle. Both front doors — the web form and the native
multi-step flow — write the same ``marketplace_merchant_applications`` row, move
through the same state machine, and land in the same queue. There is exactly one
seller record (``marketplace_sellers``) and this module never creates a second
seller type.

Three rules the rest of the file exists to keep:

1. **Approval is never automatic.** Every transition into ``approved`` requires
   an admin actor id. ``apply_transition`` refuses otherwise, so no amount of
   applicant-supplied input can reach the approved state.
2. **The applicant never sees the reviewer's side.** ``applicant_view`` is a
   whitelist, not a blocklist: internal notes, risk score, reviewer identity and
   raw document paths are absent by construction rather than removed by hand.
3. **Nothing sensitive is logged.** Audit and history rows carry document *type*
   and id. They never carry filenames, stored paths, or document bytes.

Every function here takes a cursor rather than opening its own connection, so a
route can do the whole of a decision — status write, seller mirror, history row,
audit row, notification — inside one transaction and have it roll back as a unit.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# States
# --------------------------------------------------------------------------- #

DRAFT = "draft"
SUBMITTED = "submitted"
UNDER_REVIEW = "under_review"
INFORMATION_REQUESTED = "information_requested"
RESUBMITTED = "resubmitted"
APPROVED = "approved"
REJECTED = "rejected"
WITHDRAWN = "withdrawn"
EXPIRED = "expired"
SUSPENDED = "suspended"

ALL_STATUSES = (
    DRAFT, SUBMITTED, UNDER_REVIEW, INFORMATION_REQUESTED, RESUBMITTED,
    APPROVED, REJECTED, WITHDRAWN, EXPIRED, SUSPENDED,
)

# Rows written before this module existed used `pending_review` for what is now
# `submitted`. Normalising on read rather than migrating means a deployed
# database keeps working untouched and no historical row is rewritten.
LEGACY_STATUS_ALIASES = {
    "pending_review": SUBMITTED,
    "pending": SUBMITTED,
    "more_info": INFORMATION_REQUESTED,
    "": DRAFT,
}

#: Statuses an admin queue should surface first, in order.
QUEUE_PRIORITY = (SUBMITTED, RESUBMITTED, UNDER_REVIEW, INFORMATION_REQUESTED, DRAFT)

#: Statuses that mean "an admin still owes this applicant an answer".
OPEN_FOR_REVIEW = (SUBMITTED, RESUBMITTED, UNDER_REVIEW)

#: Statuses from which the applicant may still edit their answers.
APPLICANT_EDITABLE = (DRAFT, INFORMATION_REQUESTED, REJECTED)

TERMINAL_STATUSES = (WITHDRAWN, EXPIRED)


def normalize_status(value: Any) -> str:
    """The canonical name for a status that may have been written years ago."""
    text = str(value or "").strip().lower()
    if text in LEGACY_STATUS_ALIASES:
        return LEGACY_STATUS_ALIASES[text]
    return text if text in ALL_STATUSES else DRAFT


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #

APPLICANT = "applicant"
ADMIN = "admin"
SYSTEM = "system"

#: (from, to) -> the actor types allowed to make that move.
#:
#: Written as data rather than as branches in a handler so that the security
#: property — who may approve — is one table anyone can read, and so the tests
#: can enumerate it rather than guess at it.
TRANSITIONS: Dict[Tuple[str, str], Tuple[str, ...]] = {
    (DRAFT, SUBMITTED): (APPLICANT,),
    (DRAFT, WITHDRAWN): (APPLICANT,),
    (DRAFT, EXPIRED): (SYSTEM,),

    (SUBMITTED, UNDER_REVIEW): (ADMIN,),
    (SUBMITTED, INFORMATION_REQUESTED): (ADMIN,),
    (SUBMITTED, APPROVED): (ADMIN,),
    (SUBMITTED, REJECTED): (ADMIN,),
    (SUBMITTED, WITHDRAWN): (APPLICANT,),
    (SUBMITTED, EXPIRED): (SYSTEM,),

    (UNDER_REVIEW, INFORMATION_REQUESTED): (ADMIN,),
    (UNDER_REVIEW, APPROVED): (ADMIN,),
    (UNDER_REVIEW, REJECTED): (ADMIN,),
    (UNDER_REVIEW, WITHDRAWN): (APPLICANT,),
    (UNDER_REVIEW, EXPIRED): (SYSTEM,),

    (INFORMATION_REQUESTED, RESUBMITTED): (APPLICANT,),
    (INFORMATION_REQUESTED, WITHDRAWN): (APPLICANT,),
    (INFORMATION_REQUESTED, EXPIRED): (SYSTEM,),

    (RESUBMITTED, UNDER_REVIEW): (ADMIN,),
    (RESUBMITTED, INFORMATION_REQUESTED): (ADMIN,),
    (RESUBMITTED, APPROVED): (ADMIN,),
    (RESUBMITTED, REJECTED): (ADMIN,),
    (RESUBMITTED, WITHDRAWN): (APPLICANT,),
    (RESUBMITTED, EXPIRED): (SYSTEM,),

    # An appeal is not a separate object. A rejected applicant reopens the same
    # application as a draft, fixes what was wrong, and submits again — so the
    # reviewer sees the whole history rather than a fresh row with no past.
    (REJECTED, DRAFT): (APPLICANT,),

    (APPROVED, SUSPENDED): (ADMIN,),
    (SUSPENDED, APPROVED): (ADMIN,),
    (SUSPENDED, REJECTED): (ADMIN,),
}


class TransitionError(ValueError):
    """A move the state machine does not allow, with a sentence for the caller."""


def allowed_transitions(current: str, actor_type: str) -> List[str]:
    current = normalize_status(current)
    return sorted(
        to for (frm, to), actors in TRANSITIONS.items()
        if frm == current and actor_type in actors
    )


def can_transition(current: str, target: str, actor_type: str) -> bool:
    return actor_type in TRANSITIONS.get((normalize_status(current), normalize_status(target)), ())


def assert_transition(current: str, target: str, actor_type: str, actor_id: Any = None) -> Tuple[str, str]:
    """
    Raise unless this actor may make this move.

    The approval guard is stated twice on purpose. The transition table already
    restricts ``approved`` to ``ADMIN``, but the identity of the admin is what
    makes the decision auditable, so an admin transition with no admin id is
    refused here rather than silently recorded as actor 0.

    The target is checked *before* normalisation. ``normalize_status`` exists to
    read a status written years ago and falls back to ``draft`` for anything it
    does not recognise, which is right when reading a row and wrong when writing
    one: normalising first would turn a misspelled or injected target into a
    silent move to ``draft`` rather than a refusal, and the "unknown status"
    message below would be unreachable.
    """
    current_norm = normalize_status(current)
    requested = str(target or "").strip().lower()
    if requested not in ALL_STATUSES and requested not in LEGACY_STATUS_ALIASES:
        raise TransitionError(f"Unknown application status: {target}")
    if not requested:
        raise TransitionError("A transition needs a target status.")
    target_norm = normalize_status(target)
    if current_norm == target_norm:
        raise TransitionError(f"Application is already {target_norm.replace('_', ' ')}.")
    if not can_transition(current_norm, target_norm, actor_type):
        article = "An" if str(actor_type or "")[:1].lower() in "aeiou" else "A"
        raise TransitionError(
            f"{article} {actor_type} cannot move an application from "
            f"{current_norm.replace('_', ' ')} to {target_norm.replace('_', ' ')}."
        )
    if actor_type == ADMIN and not int(actor_id or 0):
        raise TransitionError("An administrator decision requires an identified administrator.")
    return current_norm, target_norm


# --------------------------------------------------------------------------- #
# The form
# --------------------------------------------------------------------------- #

SELLER_TYPES = ("individual", "creator", "teacher", "brand", "digital_seller", "physical_seller", "agency")

SELLER_TYPE_LABELS = {
    "individual": "Individual",
    "creator": "Creator",
    "teacher": "Teacher",
    "brand": "Brand",
    "digital_seller": "Digital Seller",
    "physical_seller": "Physical Seller",
    "agency": "Agency",
}

#: Seller types that are a business rather than a person, and therefore owe a
#: business name. Asking every applicant for one is how a form starts collecting
#: information it does not need.
BUSINESS_SELLER_TYPES = ("brand", "agency")

SELLING_INTENTS = (
    "Digital Products", "Courses", "Coaching", "Ebooks", "Trading Education",
    "Templates", "AI Tools", "Physical Products", "Livestream Selling", "Services",
)

REQUIRED_DOCUMENTS = ("id_front", "id_back", "selfie")
OPTIONAL_DOCUMENTS = ("business_registration", "tax_certificate", "ownership_proof")
ALL_DOCUMENT_TYPES = REQUIRED_DOCUMENTS + OPTIONAL_DOCUMENTS

DOCUMENT_LABELS = {
    "id_front": "Government ID — front",
    "id_back": "Government ID — back",
    "selfie": "Selfie verification",
    "business_registration": "Business registration",
    "tax_certificate": "Tax or resale certificate",
    "ownership_proof": "Proof of ownership",
}

#: The steps, in order, with the fields each one owns.
#:
#: The client renders from this and the server validates against it, so a step
#: cannot drift out of agreement with the rule that gates it.
STEPS: Tuple[Dict[str, Any], ...] = (
    {
        "key": "seller_type",
        "title": "What are you selling as?",
        "summary": "This decides what we need to ask you for. Most people are an individual or a creator.",
        "fields": ("seller_type", "seller_intent"),
    },
    {
        "key": "identity",
        "title": "Who you are",
        "summary": "Your legal name and where you are. We verify this against the ID you upload.",
        "fields": ("full_name", "country", "state_region", "email", "phone"),
    },
    {
        "key": "storefront",
        "title": "Your storefront",
        "summary": "The name buyers will see, and what you sell.",
        "fields": ("display_name", "business_name", "website", "social_links", "years_experience", "business_description"),
    },
    {
        "key": "safety",
        "title": "Trust and safety",
        "summary": "Short questions about how you will sell. Answer honestly — a past ban is not automatically disqualifying, but a hidden one is.",
        "fields": ("sold_online_before", "banned_elsewhere", "guaranteed_profits", "comply_rules", "understand_claims"),
    },
    {
        "key": "documents",
        "title": "Verification documents",
        "summary": "Private, encrypted at rest, and visible only to authorised reviewers. Never shown on your storefront.",
        "fields": (),
    },
    {
        "key": "agreements",
        "title": "Agreements",
        "summary": "The three rules that get sellers removed if broken.",
        "fields": ("marketplace_rules", "anti_scam_agreement", "no_profit_guarantees"),
    },
    {
        "key": "review",
        "title": "Review and submit",
        "summary": "Check everything, then send it to review.",
        "fields": (),
    },
)

STEP_KEYS = tuple(step["key"] for step in STEPS)

#: Columns on ``marketplace_merchant_applications`` the applicant may write.
#: `status`, `risk_score`, `reviewer_id`, `internal_notes` and every timestamp
#: are deliberately absent: an autosave must not be able to move its own state.
APPLICANT_WRITABLE_FIELDS = (
    "full_name", "display_name", "country", "state_region", "email", "phone",
    "pulse_username", "business_name", "seller_type", "website", "social_links",
    "years_experience", "business_description",
)

SAFETY_FIELDS = ("sold_online_before", "banned_elsewhere", "guaranteed_profits", "comply_rules", "understand_claims")
AGREEMENT_FIELDS = ("marketplace_rules", "anti_scam_agreement", "no_profit_guarantees")

_YES_NO = ("yes", "no")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def _text(value: Any, limit: int = 1200) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _loads(value: Any, fallback):
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def validate_step(step_key: str, fields: Dict[str, Any], *, documents: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, str]:
    """
    Field-keyed errors for one step, empty when the step is complete.

    Per-step rather than whole-form so the native flow can let someone past step
    two without knowing yet whether they will upload a tax certificate, and so an
    error lands next to the input that caused it.
    """
    errors: Dict[str, str] = {}
    seller_type = _text(fields.get("seller_type"), 40).lower().replace(" ", "_")

    if step_key == "seller_type":
        if seller_type not in SELLER_TYPES:
            errors["seller_type"] = "Choose how you are selling."
        if not _loads(fields.get("seller_intent") or fields.get("seller_intent_json"), []):
            errors["seller_intent"] = "Choose at least one thing you plan to sell."

    elif step_key == "identity":
        if len(_text(fields.get("full_name"), 120)) < 2:
            errors["full_name"] = "Enter your full legal name as it appears on your ID."
        if not _text(fields.get("country"), 80):
            errors["country"] = "Select your country."
        email = _text(fields.get("email"), 160)
        if not email:
            errors["email"] = "Enter an email we can reach you at."
        elif not _EMAIL_RE.match(email):
            errors["email"] = "That email address does not look right."

    elif step_key == "storefront":
        if len(_text(fields.get("display_name"), 80)) < 2:
            errors["display_name"] = "Enter the name buyers will see."
        if seller_type in BUSINESS_SELLER_TYPES and not _text(fields.get("business_name"), 120):
            errors["business_name"] = "A brand or agency needs its registered business name."
        description = _text(fields.get("business_description"), 4000)
        if len(description) < 40:
            errors["business_description"] = "Describe what you sell and who it helps, in at least a couple of sentences."
        website = _text(fields.get("website"), 300)
        if website and not re.match(r"^https?://[^\s]+\.[^\s]+$", website):
            errors["website"] = "Enter a full web address, starting with https://."

    elif step_key == "safety":
        for field in SAFETY_FIELDS:
            if _text(fields.get(field), 10).lower() not in _YES_NO:
                errors[field] = "Answer this question."

    elif step_key == "documents":
        present = {str(doc.get("document_type") or "") for doc in (documents or [])}
        for doc_type in REQUIRED_DOCUMENTS:
            if doc_type not in present:
                errors[doc_type] = f"Upload your {DOCUMENT_LABELS[doc_type].lower()}."

    elif step_key == "agreements":
        for field in AGREEMENT_FIELDS:
            if not _truthy(fields.get(field)):
                errors[field] = "You must agree to this to sell on PulseSoc."

    return errors


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def validate_application(fields: Dict[str, Any], documents: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """Every incomplete step, keyed by step. Empty means the form may be submitted."""
    documents = list(documents)
    result: Dict[str, Dict[str, str]] = {}
    for step in STEPS:
        errors = validate_step(step["key"], fields, documents=documents)
        if errors:
            result[step["key"]] = errors
    return result


def completeness_score(fields: Dict[str, Any], documents: Iterable[Dict[str, Any]]) -> int:
    """
    How far through the form the applicant is, 0–100.

    Weighted by step so the progress bar moves at a believable rate: a single
    text field should not jump it a fifth of the way.
    """
    documents = list(documents)
    weights = {
        "seller_type": 10, "identity": 20, "storefront": 25,
        "safety": 15, "documents": 20, "agreements": 10,
    }
    earned = sum(
        weight for key, weight in weights.items()
        if not validate_step(key, fields, documents=documents)
    )
    return max(0, min(100, earned))


def risk_score(fields: Dict[str, Any], documents: Iterable[Dict[str, Any]]) -> int:
    """
    A reviewer's prompt, never a decision.

    Nothing here approves or rejects anything. It orders the queue and tells a
    reviewer where to look first. It is admin-only: ``applicant_view`` never
    carries it, because publishing a risk score teaches applicants how to
    answer around it.
    """
    score = 0
    if _text(fields.get("guaranteed_profits"), 10).lower() == "yes":
        score += 45
    if _text(fields.get("banned_elsewhere"), 10).lower() == "yes":
        score += 25
    if _text(fields.get("comply_rules"), 10).lower() != "yes":
        score += 20
    if _text(fields.get("understand_claims"), 10).lower() != "yes":
        score += 20
    if not all(_truthy(fields.get(field)) for field in AGREEMENT_FIELDS):
        score += 15
    present = {str(doc.get("document_type") or "") for doc in documents}
    if not present.issuperset(REQUIRED_DOCUMENTS):
        score += 10
    if _text(fields.get("sold_online_before"), 10).lower() == "no":
        score += 5
    return max(0, min(100, score))


def risk_signals(fields: Dict[str, Any], documents: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """The reasons behind the score, in words, for the reviewer's sidebar."""
    signals: List[Dict[str, str]] = []

    def add(level: str, label: str, detail: str) -> None:
        signals.append({"level": level, "label": label, "detail": detail})

    if _text(fields.get("guaranteed_profits"), 10).lower() == "yes":
        add("high", "Promises guaranteed profits", "Guaranteed-return claims are prohibited outright.")
    if _text(fields.get("banned_elsewhere"), 10).lower() == "yes":
        add("medium", "Banned from another marketplace", "Disclosed by the applicant. Ask what for before deciding.")
    if _text(fields.get("comply_rules"), 10).lower() != "yes":
        add("high", "Will not confirm rule compliance", "")
    if _text(fields.get("understand_claims"), 10).lower() != "yes":
        add("medium", "Does not confirm understanding of claim limits", "")
    present = {str(doc.get("document_type") or "") for doc in documents}
    missing = [DOCUMENT_LABELS[d] for d in REQUIRED_DOCUMENTS if d not in present]
    if missing:
        add("medium", "Missing required documents", ", ".join(missing))
    for doc in documents:
        if str(doc.get("review_status") or "") == "suspicious":
            add("high", "Document flagged suspicious", DOCUMENT_LABELS.get(str(doc.get("document_type")), "Document"))
    if not signals:
        add("low", "No automated signals raised", "Review the documents and description as usual.")
    return signals


# --------------------------------------------------------------------------- #
# Applicant-facing shape
# --------------------------------------------------------------------------- #

#: What the applicant is told, per state, and what they can do about it.
STATUS_COPY = {
    DRAFT: ("Draft", "Your application is saved. Finish the remaining steps and submit it when you are ready."),
    SUBMITTED: ("Submitted", "Your application is in the review queue. We will let you know as soon as a reviewer has looked at it."),
    UNDER_REVIEW: ("Under review", "A reviewer is looking at your application now. Nothing is needed from you."),
    INFORMATION_REQUESTED: ("More information needed", "A reviewer needs something else from you before they can decide."),
    RESUBMITTED: ("Resubmitted", "Your updated answers are back with the reviewer."),
    APPROVED: ("Approved", "You are an approved seller. Your selling tools are unlocked."),
    REJECTED: ("Not approved", "Your application was not approved this time. You can update it and apply again."),
    WITHDRAWN: ("Withdrawn", "You withdrew this application. You can start a new one whenever you like."),
    EXPIRED: ("Expired", "This application expired before it was completed. You can start a new one."),
    SUSPENDED: ("Suspended", "Your seller access is suspended. Contact support for the next step."),
}

#: The one thing to do next, per state. Drives the single button in the status
#: centre, so the applicant is never shown a screen with nothing actionable.
NEXT_ACTION = {
    DRAFT: {"action": "continue", "label": "Continue application"},
    SUBMITTED: {"action": "wait", "label": "Waiting for review"},
    UNDER_REVIEW: {"action": "wait", "label": "Waiting for review"},
    INFORMATION_REQUESTED: {"action": "respond", "label": "Provide the requested information"},
    RESUBMITTED: {"action": "wait", "label": "Waiting for review"},
    APPROVED: {"action": "open_seller_tools", "label": "Open seller tools"},
    REJECTED: {"action": "reapply", "label": "Update and apply again"},
    WITHDRAWN: {"action": "restart", "label": "Start a new application"},
    EXPIRED: {"action": "restart", "label": "Start a new application"},
    SUSPENDED: {"action": "contact_support", "label": "Contact support"},
}


def applicant_view(application: Dict[str, Any], documents: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Everything the applicant may see, and nothing else.

    A whitelist rather than a redaction pass. ``internal_notes``, ``risk_score``,
    ``reviewer_id`` and ``stored_path`` are not removed here — they are never
    added, so a new admin-only column added later cannot leak by being forgotten.
    """
    application = dict(application or {})
    documents = list(documents)
    status = normalize_status(application.get("status"))
    fields = applicant_fields(application)
    title, message = STATUS_COPY.get(status, STATUS_COPY[DRAFT])
    step_errors = validate_application(fields, documents)

    return {
        "application_id": int(application.get("id") or 0) or None,
        "status": status,
        "status_title": title,
        "status_message": message,
        "next_action": dict(NEXT_ACTION.get(status, NEXT_ACTION[DRAFT])),
        "editable": status in APPLICANT_EDITABLE,
        "completeness": completeness_score(fields, documents),
        "fields": fields,
        "documents": [applicant_document_view(doc) for doc in documents],
        "steps": [
            {
                "key": step["key"],
                "title": step["title"],
                "summary": step["summary"],
                "fields": list(step["fields"]),
                "complete": step["key"] not in step_errors,
                "errors": step_errors.get(step["key"], {}),
            }
            for step in STEPS
        ],
        "can_submit": not step_errors and status in APPLICANT_EDITABLE,
        # The reviewer's message, when there is one. This is the only reviewer
        # text an applicant ever receives, and it is written deliberately for
        # them — never the internal note.
        "information_request": _text(application.get("information_request_message"), 1200) or "",
        "submitted_at": _text(application.get("submitted_at"), 40) or None,
        "updated_at": _text(application.get("updated_at"), 40) or None,
        "seller_types": [{"value": value, "label": SELLER_TYPE_LABELS[value]} for value in SELLER_TYPES],
        "selling_intents": list(SELLING_INTENTS),
        "required_documents": [{"type": d, "label": DOCUMENT_LABELS[d], "required": True} for d in REQUIRED_DOCUMENTS],
        "optional_documents": [{"type": d, "label": DOCUMENT_LABELS[d], "required": False} for d in OPTIONAL_DOCUMENTS],
    }


def applicant_document_view(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    An uploaded document as its owner sees it: that it arrived, and nothing more.

    No stored path and no URL. The applicant does not need to re-read their own
    passport through our servers, and a URL that could return it would be one
    more thing to get the authorisation right on.
    """
    doc = dict(doc or {})
    return {
        "id": int(doc.get("id") or 0),
        "type": _text(doc.get("document_type"), 60),
        "label": DOCUMENT_LABELS.get(_text(doc.get("document_type"), 60), "Document"),
        "filename": _text(doc.get("original_filename"), 160),
        "size_kb": int(doc.get("file_size") or 0) // 1024,
        "uploaded_at": _text(doc.get("created_at"), 40),
        # "received" rather than the raw scan status: an applicant reading
        # "queued_for_internal_review" learns nothing and worries anyway.
        "state": "received" if _text(doc.get("review_status"), 40) in ("", "pending") else _text(doc.get("review_status"), 40),
    }


def applicant_fields(application: Dict[str, Any]) -> Dict[str, Any]:
    """The applicant's own answers, flattened out of the JSON columns."""
    application = dict(application or {})
    fields: Dict[str, Any] = {key: _text(application.get(key), 4000) for key in APPLICANT_WRITABLE_FIELDS}
    fields["seller_type"] = _text(application.get("seller_type"), 40).lower().replace(" ", "_")
    fields["seller_intent"] = _loads(application.get("seller_intent_json"), [])
    safety = _loads(application.get("safety_answers_json"), {})
    for key in SAFETY_FIELDS:
        fields[key] = _text(safety.get(key), 10).lower()
    agreements = _loads(application.get("agreements_json"), {})
    for key in AGREEMENT_FIELDS:
        fields[key] = bool(agreements.get(key))
    return fields


def merge_fields(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply an autosave patch.

    Only keys the client actually sent are touched, so a step-two autosave from
    a client that has not loaded step three cannot blank step three. Anything not
    on the applicant whitelist is dropped without comment — a client that sends
    ``status`` or ``risk_score`` is not told which of its keys were ignored.
    """
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if key in APPLICANT_WRITABLE_FIELDS:
            merged[key] = _text(value, 4000)
        elif key == "seller_intent":
            intents = value if isinstance(value, list) else _loads(value, [])
            merged["seller_intent"] = [_text(item, 60) for item in intents if _text(item, 60) in SELLING_INTENTS][:12]
        elif key in SAFETY_FIELDS:
            answer = _text(value, 10).lower()
            merged[key] = answer if answer in _YES_NO else ""
        elif key in AGREEMENT_FIELDS:
            merged[key] = _truthy(value)
    merged["seller_type"] = _text(merged.get("seller_type"), 40).lower().replace(" ", "_")
    if merged["seller_type"] not in SELLER_TYPES:
        merged["seller_type"] = ""
    return merged


def fields_to_columns(fields: Dict[str, Any]) -> Dict[str, Any]:
    """The applicant's answers as the columns of ``marketplace_merchant_applications``."""
    columns: Dict[str, Any] = {key: _text(fields.get(key), 4000) for key in APPLICANT_WRITABLE_FIELDS}
    columns["seller_intent_json"] = json.dumps(list(fields.get("seller_intent") or []), default=str)
    columns["safety_answers_json"] = json.dumps({key: _text(fields.get(key), 10).lower() for key in SAFETY_FIELDS}, default=str)
    columns["agreements_json"] = json.dumps({key: bool(fields.get(key)) for key in AGREEMENT_FIELDS}, default=str)
    return columns


# --------------------------------------------------------------------------- #
# Admin-facing shape
# --------------------------------------------------------------------------- #

DECISIONS = {
    "start_review": UNDER_REVIEW,
    "request_information": INFORMATION_REQUESTED,
    "approve": APPROVED,
    "reject": REJECTED,
    "suspend": SUSPENDED,
    "reinstate": APPROVED,
}

#: Decisions that must carry a reason the applicant or the audit trail will read.
DECISIONS_REQUIRING_REASON = ("request_information", "reject", "suspend")


def decision_target(decision: str) -> str:
    target = DECISIONS.get(str(decision or "").strip().lower())
    if not target:
        raise TransitionError(f"Unknown review decision: {decision}")
    return target


def seller_status_for(application_status: str) -> str:
    """
    The seller record's status, given the application's.

    ``marketplace_sellers`` is the one authority on whether someone may sell.
    Application states that are not decisions leave it at ``pending``: a seller
    is not "under review", their application is.
    """
    status = normalize_status(application_status)
    if status == APPROVED:
        return "approved"
    if status == REJECTED:
        return "rejected"
    if status == SUSPENDED:
        return "suspended"
    if status in (WITHDRAWN, EXPIRED):
        return "withdrawn"
    return "pending"


def verification_status_for(application_status: str) -> str:
    status = normalize_status(application_status)
    if status == APPROVED:
        return "verified"
    if status in (REJECTED, SUSPENDED):
        return "rejected"
    if status in OPEN_FOR_REVIEW:
        return "pending"
    return "unverified"


def queue_sort_key(row: Dict[str, Any]) -> Tuple[int, int, int]:
    """
    Queue order: what is waiting on us first, riskiest first, oldest first.

    Oldest-first inside a priority band rather than newest-first, so an
    application cannot starve behind a steady arrival of new ones.
    """
    status = normalize_status(row.get("status"))
    band = QUEUE_PRIORITY.index(status) if status in QUEUE_PRIORITY else len(QUEUE_PRIORITY)
    return (band, -int(row.get("risk_score") or 0), int(row.get("id") or 0))


def is_open_for_review(status: Any) -> bool:
    return normalize_status(status) in OPEN_FOR_REVIEW


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

STATUS_HISTORY_TABLE = "seller_application_status_history"
NOTES_TABLE = "seller_application_notes"
ASSIGNMENTS_TABLE = "seller_application_assignments"

APPLICATION_EXTRA_COLUMNS = (
    ("agreements_json", "TEXT"),
    ("submitted_at", "TEXT"),
    ("information_requested_at", "TEXT"),
    ("information_request_message", "TEXT"),
    ("withdrawn_at", "TEXT"),
    ("expires_at", "TEXT"),
    ("last_autosaved_at", "TEXT"),
    ("source", "TEXT DEFAULT 'web'"),
    ("decision_reason", "TEXT"),
)

#: How long an untouched application stays alive before ``expire_stale`` closes
#: it. Long enough that someone gathering a tax certificate is not punished.
DRAFT_TTL_DAYS = 90


def ensure_schema(cur) -> None:
    """
    Create the additive tables. Never alters or drops an existing one.

    Called from ``init_db`` alongside every other ``CREATE TABLE IF NOT EXISTS``
    in this codebase, which is why it is idempotent and why the extra columns go
    through the caller's ``add_columns_if_missing``.
    """
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {STATUS_HISTORY_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL,
            user_id INTEGER,
            from_status TEXT,
            to_status TEXT NOT NULL,
            actor_type TEXT NOT NULL,
            actor_id INTEGER DEFAULT 0,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_seller_app_history ON {STATUS_HISTORY_TABLE} (application_id, id)"
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {NOTES_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL,
            author_admin_id INTEGER DEFAULT 0,
            visibility TEXT NOT NULL DEFAULT 'internal',
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_seller_app_notes ON {NOTES_TABLE} (application_id, id)"
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ASSIGNMENTS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL,
            reviewer_admin_id INTEGER NOT NULL,
            assigned_by INTEGER DEFAULT 0,
            assigned_at TEXT NOT NULL,
            released_at TEXT
        )
        """
    )
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_seller_app_assignment ON {ASSIGNMENTS_TABLE} (application_id, released_at)"
    )


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def record_transition(cur, application_id: int, user_id: Any, from_status: str, to_status: str,
                      actor_type: str, actor_id: Any = 0, reason: str = "") -> None:
    """
    Append one row to the application's history. Never updates one.

    ``reason`` is a reviewer's sentence or an applicant's. It is never a document
    filename and never a field value, so the history can be shown in full to an
    auditor without exposing what was uploaded.

    An empty ``from_status`` is kept empty rather than normalized. Only the
    creation row has no origin, and rendering it as ``draft → draft`` would make
    the first line of every timeline read like a no-op.
    """
    origin = normalize_status(from_status) if str(from_status or "").strip() else ""
    cur.execute(
        f"""
        INSERT INTO {STATUS_HISTORY_TABLE}
            (application_id, user_id, from_status, to_status, actor_type, actor_id, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(application_id or 0), int(user_id or 0),
            origin, normalize_status(to_status),
            str(actor_type or SYSTEM)[:20], int(actor_id or 0),
            _text(reason, 1200), _now(),
        ),
    )


def history_for(cur, application_id: int) -> List[Dict[str, Any]]:
    cur.execute(
        f"SELECT * FROM {STATUS_HISTORY_TABLE} WHERE application_id=? ORDER BY id ASC",
        (int(application_id or 0),),
    )
    return [dict(row) for row in cur.fetchall()]


def history_for_many(cur, application_ids: Iterable[Any]) -> Dict[int, List[Dict[str, Any]]]:
    """
    Timelines for a page of applications, in one round trip.

    The queue renders every row's timeline inline, so calling ``history_for``
    per row costs one query per application — five hundred of them on the "all"
    filter, which is the exact page a busy reviewer opens. The grouping is done
    here rather than at the call site so that both this and ``notes_for_many``
    return the same always-present-key shape and the caller never has to guard
    a missing id.
    """
    ids = [int(app_id or 0) for app_id in application_ids]
    grouped: Dict[int, List[Dict[str, Any]]] = {app_id: [] for app_id in ids}
    if not ids:
        return grouped
    placeholders = ",".join(["?"] * len(ids))
    cur.execute(
        f"SELECT * FROM {STATUS_HISTORY_TABLE} WHERE application_id IN ({placeholders}) ORDER BY id ASC",
        ids,
    )
    for row in cur.fetchall():
        entry = dict(row)
        grouped.setdefault(int(entry.get("application_id") or 0), []).append(entry)
    return grouped


def notes_for_many(cur, application_ids: Iterable[Any], *, visibility: Optional[str] = None) -> Dict[int, List[Dict[str, Any]]]:
    """
    Notes for a page of applications, in one round trip.

    ``visibility`` carries the same rule as ``notes_for``: it is passed
    explicitly at every call site, and omitting it returns internal notes. This
    function is only ever reached from admin surfaces, but it does not assume
    that — the filter is applied in SQL so a future applicant-facing caller
    that passes ``"applicant"`` gets the same guarantee the single-row version
    already provides.
    """
    ids = [int(app_id or 0) for app_id in application_ids]
    grouped: Dict[int, List[Dict[str, Any]]] = {app_id: [] for app_id in ids}
    if not ids:
        return grouped
    placeholders = ",".join(["?"] * len(ids))
    if visibility:
        cur.execute(
            f"SELECT * FROM {NOTES_TABLE} WHERE application_id IN ({placeholders}) AND visibility=? ORDER BY id ASC",
            ids + [str(visibility)],
        )
    else:
        cur.execute(
            f"SELECT * FROM {NOTES_TABLE} WHERE application_id IN ({placeholders}) ORDER BY id ASC",
            ids,
        )
    for row in cur.fetchall():
        entry = dict(row)
        grouped.setdefault(int(entry.get("application_id") or 0), []).append(entry)
    return grouped


def add_note(cur, application_id: int, admin_id: Any, body: str, visibility: str = "internal") -> int:
    visibility = "applicant" if str(visibility or "").lower() == "applicant" else "internal"
    cur.execute(
        f"INSERT INTO {NOTES_TABLE} (application_id, author_admin_id, visibility, body, created_at) VALUES (?, ?, ?, ?, ?)",
        (int(application_id or 0), int(admin_id or 0), visibility, _text(body, 4000), _now()),
    )
    return int(cur.lastrowid or 0)


def notes_for(cur, application_id: int, *, visibility: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Notes on an application.

    ``visibility`` is required at every applicant-facing call site. There is no
    default that returns internal notes to a non-admin, because the one place
    that would go wrong is the place it matters most.
    """
    if visibility:
        cur.execute(
            f"SELECT * FROM {NOTES_TABLE} WHERE application_id=? AND visibility=? ORDER BY id ASC",
            (int(application_id or 0), str(visibility)),
        )
    else:
        cur.execute(
            f"SELECT * FROM {NOTES_TABLE} WHERE application_id=? ORDER BY id ASC",
            (int(application_id or 0),),
        )
    return [dict(row) for row in cur.fetchall()]


def assign_reviewer(cur, application_id: int, reviewer_admin_id: Any, assigned_by: Any) -> None:
    """
    Give the application to one reviewer, releasing whoever held it.

    Assignment is a table rather than a column so that "who has looked at this,
    and when did they pick it up" survives reassignment. ``marketplace_
    applications.reviewer_id`` is still mirrored, because the existing admin
    page and its queries read it.
    """
    now = _now()
    cur.execute(
        f"UPDATE {ASSIGNMENTS_TABLE} SET released_at=? WHERE application_id=? AND released_at IS NULL",
        (now, int(application_id or 0)),
    )
    if int(reviewer_admin_id or 0):
        cur.execute(
            f"INSERT INTO {ASSIGNMENTS_TABLE} (application_id, reviewer_admin_id, assigned_by, assigned_at) VALUES (?, ?, ?, ?)",
            (int(application_id or 0), int(reviewer_admin_id), int(assigned_by or 0), now),
        )
    cur.execute(
        "UPDATE marketplace_merchant_applications SET reviewer_id=?, updated_at=? WHERE id=?",
        (int(reviewer_admin_id or 0) or None, now, int(application_id or 0)),
    )


def current_reviewer(cur, application_id: int) -> Optional[int]:
    cur.execute(
        f"SELECT reviewer_admin_id FROM {ASSIGNMENTS_TABLE} WHERE application_id=? AND released_at IS NULL ORDER BY id DESC LIMIT 1",
        (int(application_id or 0),),
    )
    row = cur.fetchone()
    return int(dict(row or {}).get("reviewer_admin_id") or 0) or None


def get_application(cur, user_id: Any) -> Dict[str, Any]:
    """
    The applicant's current application.

    One live application per user. A rejected applicant reopens this same row as
    a draft rather than creating a second one, so the reviewer who sees it next
    sees why it was rejected the first time.
    """
    cur.execute(
        "SELECT * FROM marketplace_merchant_applications WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (int(user_id or 0),),
    )
    return dict(cur.fetchone() or {})


def get_application_by_id(cur, application_id: Any) -> Dict[str, Any]:
    cur.execute(
        "SELECT * FROM marketplace_merchant_applications WHERE id=? LIMIT 1",
        (int(application_id or 0),),
    )
    return dict(cur.fetchone() or {})


def documents_for(cur, application_id: Any) -> List[Dict[str, Any]]:
    cur.execute(
        "SELECT * FROM marketplace_merchant_documents WHERE application_id=? ORDER BY id ASC",
        (int(application_id or 0),),
    )
    return [dict(row) for row in cur.fetchall()]


def create_draft(cur, user_id: Any, *, source: str = "native") -> int:
    now = _now()
    expires = (datetime.utcnow() + timedelta(days=DRAFT_TTL_DAYS)).isoformat(timespec="seconds")
    cur.execute(
        """
        INSERT INTO marketplace_merchant_applications
            (user_id, status, completeness, risk_score, source, expires_at, created_at, updated_at, last_autosaved_at)
        VALUES (?, ?, 0, 0, ?, ?, ?, ?, ?)
        """,
        (int(user_id or 0), DRAFT, str(source or "native")[:20], expires, now, now, now),
    )
    application_id = int(cur.lastrowid or 0)
    record_transition(cur, application_id, user_id, "", DRAFT, APPLICANT, user_id, "Application started")
    return application_id


def save_draft(cur, application_id: int, fields: Dict[str, Any], documents: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Persist an autosave.

    Writes the answers and the derived scores, and nothing else — in particular
    not ``status``. An autosave is the single most frequent write in this
    system and the one an attacker has the easiest access to, so it is the one
    that must not be able to move the application forward.
    """
    documents = list(documents)
    columns = fields_to_columns(fields)
    columns["completeness"] = completeness_score(fields, documents)
    columns["risk_score"] = risk_score(fields, documents)
    columns["last_autosaved_at"] = _now()
    columns["updated_at"] = columns["last_autosaved_at"]
    assignments = ", ".join(f"{key}=?" for key in columns)
    cur.execute(
        f"UPDATE marketplace_merchant_applications SET {assignments} WHERE id=?",
        (*columns.values(), int(application_id or 0)),
    )
    return columns


def apply_transition(cur, application: Dict[str, Any], target: str, *, actor_type: str,
                     actor_id: Any = 0, reason: str = "", applicant_message: str = "") -> Dict[str, Any]:
    """
    Move an application, mirror the seller record, and write the history row.

    All three happen here rather than at the call sites so that no route can
    approve someone without leaving a trace: the audit row is not a courtesy the
    caller remembers, it is part of the transition.
    """
    application_id = int(application.get("id") or 0)
    user_id = int(application.get("user_id") or 0)
    current, target = assert_transition(application.get("status"), target, actor_type, actor_id)
    now = _now()

    updates: Dict[str, Any] = {"status": target, "updated_at": now}
    if target in (SUBMITTED, RESUBMITTED):
        updates["submitted_at"] = now
        updates["expires_at"] = None
    if target == INFORMATION_REQUESTED:
        updates["information_requested_at"] = now
        updates["information_request_message"] = _text(applicant_message or reason, 1200)
    if target in (SUBMITTED, RESUBMITTED, DRAFT):
        # A new submission clears the last request, so an applicant who has
        # already answered is not still being shown the question.
        updates["information_request_message"] = ""
    if target == WITHDRAWN:
        updates["withdrawn_at"] = now
    if target in (APPROVED, REJECTED, SUSPENDED):
        updates["reviewed_at"] = now
        updates["reviewer_id"] = int(actor_id or 0)
        updates["decision_reason"] = _text(reason, 1200)
    if target == DRAFT:
        updates["expires_at"] = (datetime.utcnow() + timedelta(days=DRAFT_TTL_DAYS)).isoformat(timespec="seconds")

    assignments = ", ".join(f"{key}=?" for key in updates)
    cur.execute(
        f"UPDATE marketplace_merchant_applications SET {assignments} WHERE id=?",
        (*updates.values(), application_id),
    )

    mirror_seller_record(cur, user_id, application, target, actor_id=actor_id, reason=reason)
    record_transition(cur, application_id, user_id, current, target, actor_type, actor_id, reason)
    return {"from": current, "to": target, "at": now}


def mirror_seller_record(cur, user_id: Any, application: Dict[str, Any], status: str,
                         *, actor_id: Any = 0, reason: str = "") -> None:
    """
    Keep ``marketplace_sellers`` — the one authority on who may sell — in step.

    Upsert rather than insert-if-missing because the pre-existing native stub
    endpoint may already have created a row for this user, and because the web
    form has always upserted here. ``review_notes`` receives the decision reason,
    which is the reviewer's own sentence, not an internal note.
    """
    now = _now()
    fields = applicant_fields(application)
    seller_status = seller_status_for(status)
    verification = verification_status_for(status)
    cur.execute(
        """
        INSERT INTO marketplace_sellers
            (user_id, display_name, bio, status, seller_type, business_name, website, country,
             state_region, phone, seller_intent_json, verification_status, risk_score,
             reviewed_by, reviewed_at, review_notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            display_name=excluded.display_name, bio=excluded.bio, status=excluded.status,
            seller_type=excluded.seller_type, business_name=excluded.business_name,
            website=excluded.website, country=excluded.country, state_region=excluded.state_region,
            phone=excluded.phone, seller_intent_json=excluded.seller_intent_json,
            verification_status=excluded.verification_status, risk_score=excluded.risk_score,
            reviewed_by=excluded.reviewed_by, reviewed_at=excluded.reviewed_at,
            review_notes=excluded.review_notes, updated_at=excluded.updated_at
        """,
        (
            int(user_id or 0), fields.get("display_name") or "", fields.get("business_description") or "",
            seller_status, fields.get("seller_type") or "", fields.get("business_name") or "",
            fields.get("website") or "", fields.get("country") or "", fields.get("state_region") or "",
            fields.get("phone") or "", json.dumps(list(fields.get("seller_intent") or []), default=str),
            verification, int(application.get("risk_score") or 0),
            int(actor_id or 0) or None, now if seller_status in ("approved", "rejected", "suspended") else None,
            _text(reason, 1200), now, now,
        ),
    )


def expire_stale(cur, *, now: Optional[datetime] = None) -> int:
    """
    Close drafts nobody came back to. Only drafts.

    A submitted application never expires: the delay there is ours, not the
    applicant's, and expiring it would let a slow queue quietly deny people.
    """
    cutoff = (now or datetime.utcnow()).isoformat(timespec="seconds")
    cur.execute(
        "SELECT id, user_id, status FROM marketplace_merchant_applications "
        "WHERE status=? AND expires_at IS NOT NULL AND expires_at < ?",
        (DRAFT, cutoff),
    )
    rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        cur.execute(
            "UPDATE marketplace_merchant_applications SET status=?, updated_at=? WHERE id=?",
            (EXPIRED, cutoff, int(row["id"])),
        )
        record_transition(cur, int(row["id"]), row.get("user_id"), DRAFT, EXPIRED, SYSTEM, 0, "Draft expired")
    return len(rows)


def pending_review_count(cur) -> int:
    """The number on the admin dashboard's glowing control."""
    placeholders = ",".join(["?"] * len(OPEN_FOR_REVIEW))
    legacy = [name for name, canonical in LEGACY_STATUS_ALIASES.items() if canonical in OPEN_FOR_REVIEW]
    values = list(OPEN_FOR_REVIEW) + legacy
    placeholders = ",".join(["?"] * len(values))
    cur.execute(
        f"SELECT COUNT(*) AS total FROM marketplace_merchant_applications WHERE status IN ({placeholders})",
        values,
    )
    return int(dict(cur.fetchone() or {}).get("total") or 0)


def queue_counts(cur) -> Dict[str, int]:
    """Per-status counts for the queue's filter chips."""
    cur.execute("SELECT status, COUNT(*) AS total FROM marketplace_merchant_applications GROUP BY status")
    counts = {status: 0 for status in ALL_STATUSES}
    for row in cur.fetchall():
        row = dict(row)
        counts[normalize_status(row.get("status"))] = counts.get(normalize_status(row.get("status")), 0) + int(row.get("total") or 0)
    counts["open"] = sum(counts.get(status, 0) for status in OPEN_FOR_REVIEW)
    counts["total"] = sum(counts.get(status, 0) for status in ALL_STATUSES)
    return counts


def search_queue(cur, *, status: str = "open", query: str = "", reviewer_id: Any = None,
                 limit: int = 120) -> List[Dict[str, Any]]:
    """
    The review queue, filtered.

    Filtering in Python after a bounded fetch rather than in SQL because the
    status vocabulary has legacy aliases that ``normalize_status`` resolves, and
    a ``WHERE status IN (...)`` that forgot ``pending_review`` would silently
    hide every application submitted before this module shipped.
    """
    cur.execute(
        "SELECT ma.*, u.username, u.display_name AS account_name "
        "FROM marketplace_merchant_applications ma "
        "LEFT JOIN users u ON u.user_id = ma.user_id "
        "ORDER BY ma.id DESC LIMIT 500"
    )
    rows = [dict(row) for row in cur.fetchall()]
    needle = str(query or "").strip().lower()
    wanted = str(status or "open").strip().lower()

    filtered = []
    for row in rows:
        row_status = normalize_status(row.get("status"))
        if wanted == "open" and row_status not in OPEN_FOR_REVIEW:
            continue
        if wanted not in ("open", "all") and row_status != wanted:
            continue
        if reviewer_id is not None and int(row.get("reviewer_id") or 0) != int(reviewer_id or 0):
            continue
        if needle:
            haystack = " ".join(str(row.get(key) or "") for key in (
                "display_name", "full_name", "business_name", "email", "username", "account_name", "country", "seller_type",
            )).lower()
            if needle not in haystack and needle != str(row.get("id") or ""):
                continue
        row["status"] = row_status
        filtered.append(row)

    filtered.sort(key=queue_sort_key)
    return filtered[: max(1, min(500, int(limit or 120)))]
