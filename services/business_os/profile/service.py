"""The business profile: storage, validation, and the two views built from it.

This module answers four questions the screen could not previously answer honestly.

**Which state is the business actually in?** The screen used to render the seller
application's status and the account's verification status side by side as though they
were one fact, which is how it came to display "Locked while your application is in
review" directly above "Verification · Approved". They are two different state
machines belonging to two different subsystems, and a third — ``users.verified_badge``
— was being consulted as a tiebreak. ``resolve_verification`` collapses them into one
authoritative state, and records which of the inputs decided it so the disagreement is
visible instead of arbitrary.

**What may the owner edit right now?** Previously: nothing, once the application was
submitted, because editability was read off the application. That is far too broad. A
submitted application freezes the *application*; it must not freeze the shop's opening
hours. ``IDENTITY_SENSITIVE_FIELDS`` is the short list of fields whose change on a
verified business genuinely needs another look. Everything else stays writable in every
state, and ``update_profile`` saves field by field so one rejected field cannot discard
the rest of the form.

**What can a buyer see?** ``public_profile`` builds its result key by key from an
allowlist. It never starts from the owner dict and removes things. A redaction is one
forgotten ``del`` away from leaking a payout account; a whitelist is one forgotten line
away from omitting a shipping summary. Only one of those failures is acceptable.

**What is still missing, and what would that cost the seller?** ``completeness``
returns the itemised breakdown rather than a bare percentage, because "65%" tells a
seller nothing they can act on.

Money, counts and reputation figures are not computed here. This module is identity;
the Insights and Orders surfaces own measurement, and duplicating their arithmetic here
would create exactly the reconciliation problem that ``METRICS.md`` exists to prevent.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from services import db
from services.business_os.profile import schema

# --------------------------------------------------------------------------- #
# Vocabularies
# --------------------------------------------------------------------------- #

WEEKDAYS: Tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

WEEKDAY_LABELS: Dict[str, str] = {
    "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}

#: How a contact field may be exposed. Ordered least- to most-public; the order is
#: relied on by ``_visibility_at_least``.
CONTACT_VISIBILITY: Tuple[str, ...] = ("private", "after_purchase", "public")

#: Phone defaults to private and email defaults to private. A seller publishes a
#: phone number by choosing to, never by filling in an application form that happened
#: to ask for one.
DEFAULT_VISIBILITY = "private"

PREFERRED_CONTACT: Tuple[str, ...] = ("message", "email", "phone")

#: ``unset`` is distinct from ``weekly``-with-no-rows: the first means the seller has
#: never told us, the second means they deliberately said "closed every day".
HOURS_MODES: Tuple[str, ...] = ("unset", "weekly", "by_appointment", "temporarily_closed")

LINK_KINDS: Tuple[str, ...] = (
    "website", "instagram", "tiktok", "youtube", "facebook", "x", "custom",
)

#: Operational addresses only. See the note in ``schema`` on why ``legal`` is absent.
ADDRESS_KINDS: Tuple[str, ...] = ("pickup", "shipping_origin")

#: Buyer-facing categories. Deliberately not the seller-type vocabulary: "Individual"
#: is a classification a reviewer applied to the *account*, and printing it where a
#: buyer expects to read "Electronics" tells them nothing about what is on sale.
BUSINESS_CATEGORIES: Tuple[str, ...] = (
    "electronics", "fashion", "home", "beauty", "food_drink", "services",
    "creator", "retail", "health", "sports", "toys_games", "automotive",
    "art_collectibles", "digital_goods", "other",
)

BUSINESS_CATEGORY_LABELS: Dict[str, str] = {
    "electronics": "Electronics",
    "fashion": "Fashion & apparel",
    "home": "Home & garden",
    "beauty": "Beauty & personal care",
    "food_drink": "Food & drink",
    "services": "Services",
    "creator": "Creator & media",
    "retail": "General retail",
    "health": "Health & wellness",
    "sports": "Sports & outdoors",
    "toys_games": "Toys & games",
    "automotive": "Automotive",
    "art_collectibles": "Art & collectibles",
    "digital_goods": "Digital goods",
    "other": "Other",
}

#: The one authoritative verification vocabulary. Every surface that shows a
#: verification state shows one of these and nothing else.
VERIFICATION_STATES: Tuple[str, ...] = (
    "not_started", "draft", "submitted", "needs_information", "under_review",
    "approved", "rejected", "suspended", "expired", "revoked",
)

#: Whatever a row in ``verification_requests`` happens to say, mapped onto the
#: vocabulary above. Rows predating the current writer use ``pending``.
_VERIFICATION_ALIASES: Dict[str, str] = {
    "": "not_started",
    "pending": "submitted",
    "pending_review": "submitted",
    "submitted": "submitted",
    "in_review": "under_review",
    "under_review": "under_review",
    "reviewing": "under_review",
    "needs_more_info": "needs_information",
    "more_info": "needs_information",
    "information_requested": "needs_information",
    "appealed": "under_review",
    "approved": "approved",
    "verified": "approved",
    "rejected": "rejected",
    "denied": "rejected",
    "suspended": "suspended",
    "expired": "expired",
    "revoked": "revoked",
    "draft": "draft",
}

#: Fields whose change on an already-approved business is a change to the identity a
#: reviewer signed off on. Editing one is allowed; it re-opens review for that field
#: alone. Everything not in this set is always writable.
IDENTITY_SENSITIVE_FIELDS = frozenset({"business_name", "legal_name"})

#: Live-sync states the *server* can assert. ``saving``, ``offline`` and ``sync_failed``
#: are properties of the client's own request, so the client owns those three.
SERVER_SYNC_STATES: Tuple[str, ...] = ("synced", "changes_pending", "review_required")

NAME_MAX = 160
TAGLINE_MAX = 200
ABOUT_MAX = 4000
SHORT_MAX = 400
URL_MAX = 500

_URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ProfileError(ValueError):
    """A rejection decided before anything was written."""

    def __init__(self, message: str, http_status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.http_status = int(http_status)
        self.code = str(code)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _now_iso() -> str:
    """ISO-8601 UTC to microsecond precision.

    The fractional part is not decoration. ``sync_state`` decides "is what is on the
    public profile the same as what is saved?" by comparing ``updated_at`` against
    ``published_at`` as strings, and at second resolution an edit made in the same
    second as the publish compares equal — so the badge would report "Synced" over a
    change no buyer can see. Every column in this module's tables is written here, so
    the format is uniform and the string comparison stays valid on SQLite and
    PostgreSQL alike.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(cursor) -> Dict[str, Any]:
    row = cursor.fetchone()
    if row is None:
        return {}
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _rows(cursor) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in cursor.fetchall() or []:
        try:
            out.append(dict(row))
        except (TypeError, ValueError):
            continue
    return out


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _json_list(raw: Any) -> List[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip()[:80] for item in parsed if str(item or "").strip()][:20]


def _visibility(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in CONTACT_VISIBILITY else DEFAULT_VISIBILITY


def _visibility_at_least(value: str, floor: str) -> bool:
    return CONTACT_VISIBILITY.index(_visibility(value)) >= CONTACT_VISIBILITY.index(floor)


# --------------------------------------------------------------------------- #
# Verification: one state, from three disagreeing inputs
# --------------------------------------------------------------------------- #

def normalize_verification_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in _VERIFICATION_ALIASES:
        return _VERIFICATION_ALIASES[text]
    return text if text in VERIFICATION_STATES else "not_started"


def resolve_verification(
    *,
    business_request: Optional[Mapping[str, Any]] = None,
    application_status: Any = None,
    verified_badge: Any = None,
) -> Dict[str, Any]:
    """Collapse the three sources into one state, and say which one decided.

    Precedence, and the reasoning behind it:

    1. **A business-track row in ``verification_requests``.** This is the only input
       that is actually about *this business's* verification. If one exists it wins,
       whatever the other two say.
    2. **The seller application.** With no verification request on file, an approved
       seller application is the closest thing to a verified business the platform
       has; anything earlier in that lifecycle maps to the matching pre-approval
       state.
    3. **``users.verified_badge``.** Consulted last and only to *raise* the state to
       ``approved``, never to lower it. The badge is granted by several unrelated
       admin paths (identity checks, manual grants, founder bootstrap) and is not
       evidence about a business.

    The old screen effectively used the reverse order — badge first — which is how an
    identity check on the owner's personal account came to print "Verification ·
    Approved" on a business whose application had not been looked at yet.
    """
    if business_request:
        state = normalize_verification_status(business_request.get("status"))
        return {
            "state": state,
            "source": "verification_request",
            "request_id": int(business_request.get("id") or 0),
            "decided_at": str(business_request.get("reviewed_at") or "") or None,
            "note": str(business_request.get("notes") or "")[:500] or None,
        }

    app_state = _verification_from_application(application_status)
    if app_state != "not_started":
        return {
            "state": app_state,
            "source": "seller_application",
            "request_id": 0,
            "decided_at": None,
            "note": None,
        }

    if _truthy(verified_badge):
        return {
            "state": "approved",
            "source": "verified_badge",
            "request_id": 0,
            "decided_at": None,
            "note": None,
        }

    return {"state": "not_started", "source": "none", "request_id": 0,
            "decided_at": None, "note": None}


def _verification_from_application(status: Any) -> str:
    text = str(status or "").strip().lower()
    mapping = {
        "draft": "draft",
        "submitted": "submitted",
        "pending_review": "submitted",
        "pending": "submitted",
        "under_review": "under_review",
        "resubmitted": "under_review",
        "information_requested": "needs_information",
        "more_info": "needs_information",
        "approved": "approved",
        "rejected": "rejected",
        "suspended": "suspended",
        "expired": "expired",
        "withdrawn": "not_started",
    }
    return mapping.get(text, "not_started")


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "verified", "approved"}
    return bool(value)


def verification_locks(state: str) -> Dict[str, Any]:
    """Which fields a given verification state actually restricts.

    Never the whole profile. An approved business changing its trading name is the
    one case that genuinely needs another look, and even then the change is accepted
    and queued rather than blocked — refusing the edit outright just teaches sellers
    that the field is broken.
    """
    state = normalize_verification_status(state)
    if state == "approved":
        return {
            "requires_review": sorted(IDENTITY_SENSITIVE_FIELDS),
            "blocked": [],
            "explainer": "You can update this field. Major identity changes may "
                         "temporarily require verification review.",
        }
    if state in {"suspended", "revoked"}:
        return {
            "requires_review": [],
            "blocked": sorted(IDENTITY_SENSITIVE_FIELDS),
            "explainer": "Identity fields are locked while this business is under "
                         "enforcement review. Everything else stays editable.",
        }
    return {"requires_review": [], "blocked": [], "explainer": ""}


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

def _profile_row(conn, user_id: int) -> Dict[str, Any]:
    return _row(conn.execute(
        "SELECT * FROM business_os_seller_profile WHERE user_id=?", (int(user_id),)
    ))


def _hours_rows(conn, user_id: int) -> List[Dict[str, Any]]:
    return _rows(conn.execute(
        "SELECT * FROM business_os_seller_profile_hours WHERE user_id=?", (int(user_id),)
    ))


def _override_rows(conn, user_id: int) -> List[Dict[str, Any]]:
    return _rows(conn.execute(
        "SELECT * FROM business_os_seller_profile_hours_overrides WHERE user_id=? "
        "ORDER BY on_date ASC", (int(user_id),)
    ))


def _link_rows(conn, user_id: int) -> List[Dict[str, Any]]:
    return _rows(conn.execute(
        "SELECT * FROM business_os_seller_profile_links WHERE user_id=? "
        "ORDER BY position ASC, kind ASC", (int(user_id),)
    ))


def _address_rows(conn, user_id: int) -> List[Dict[str, Any]]:
    return _rows(conn.execute(
        "SELECT * FROM business_os_seller_profile_addresses WHERE user_id=?",
        (int(user_id),)
    ))


def _business_verification_request(conn, user_id: int) -> Dict[str, Any]:
    """The newest business-track request, if the table exists at all.

    Wrapped because ``verification_requests`` is created by ``bot.init_db`` rather
    than by this module's ``ensure_schema``, and a profile read must not fail on a
    database where verification has never been initialised.
    """
    try:
        return _row(conn.execute(
            "SELECT * FROM verification_requests WHERE user_id=? AND verification_type=? "
            "ORDER BY id DESC LIMIT 1", (int(user_id), "business")
        ))
    except Exception:
        return {}


def _application_row(conn, user_id: int) -> Dict[str, Any]:
    try:
        return _row(conn.execute(
            "SELECT * FROM marketplace_merchant_applications WHERE user_id=? "
            "ORDER BY id DESC LIMIT 1", (int(user_id),)
        ))
    except Exception:
        return {}


def _user_row(conn, user_id: int) -> Dict[str, Any]:
    try:
        return _row(conn.execute(
            "SELECT * FROM users WHERE user_id=? LIMIT 1", (int(user_id),)
        ))
    except Exception:
        return {}


def hours_view(rows: Iterable[Mapping[str, Any]], mode: str) -> List[Dict[str, Any]]:
    """Seven entries, always, in week order.

    A weekday with no stored row is ``"unset"`` rather than ``"closed"``. Collapsing
    the two would make a brand-new seller look permanently shut.
    """
    by_day = {str(row.get("weekday") or "").lower(): row for row in rows}
    out: List[Dict[str, Any]] = []
    for day in WEEKDAYS:
        row = by_day.get(day)
        if not row:
            out.append({"weekday": day, "label": WEEKDAY_LABELS[day], "state": "unset",
                        "opens": None, "closes": None})
            continue
        if _truthy(row.get("closed")):
            out.append({"weekday": day, "label": WEEKDAY_LABELS[day], "state": "closed",
                        "opens": None, "closes": None})
            continue
        out.append({
            "weekday": day,
            "label": WEEKDAY_LABELS[day],
            "state": "open",
            "opens": str(row.get("opens") or "") or None,
            "closes": str(row.get("closes") or "") or None,
        })
    return out


# --------------------------------------------------------------------------- #
# Completeness — itemised, because a percentage is not an instruction
# --------------------------------------------------------------------------- #

#: (key, label, section). Order is the order the screen lists them in.
COMPLETION_ITEMS: Tuple[Tuple[str, str, str], ...] = (
    ("business_name", "Business name", "identity"),
    ("business_category", "Business category", "identity"),
    ("contact", "Contact information", "contact"),
    ("public_location", "Location", "contact"),
    ("about", "Public description", "presentation"),
    ("hours", "Opening hours", "availability"),
    ("shipping", "Shipping or service area", "availability"),
    ("logo", "Business logo", "identity"),
    ("policies", "Store policies", "policies"),
    ("links", "Social links", "presentation"),
)


def completeness(
    profile: Mapping[str, Any],
    *,
    hours: Iterable[Mapping[str, Any]] = (),
    links: Iterable[Mapping[str, Any]] = (),
    has_logo: bool = False,
    has_policies: bool = False,
) -> Dict[str, Any]:
    """Which of the ten items are done, which are not, and the resulting percentage.

    The percentage is derived from the list rather than stored, so the headline and
    the checklist under it cannot drift apart.
    """
    hours_list = list(hours)
    mode = str(profile.get("hours_mode") or "unset")
    hours_done = mode in {"by_appointment", "temporarily_closed"} or any(
        entry.get("state") in {"open", "closed"} for entry in hours_list
    )

    done: Dict[str, bool] = {
        "business_name": bool(_text(profile.get("business_name"), NAME_MAX)),
        "business_category": str(profile.get("business_category") or "") in BUSINESS_CATEGORIES,
        "contact": bool(_text(profile.get("support_email"), 200)
                        or _text(profile.get("support_phone"), 60)),
        "public_location": bool(_text(profile.get("public_country"), 100)),
        "about": bool(_text(profile.get("about"), ABOUT_MAX)),
        "hours": hours_done,
        "shipping": bool(_text(profile.get("shipping_summary"), SHORT_MAX)
                         or _text(profile.get("service_area"), SHORT_MAX)),
        "logo": bool(has_logo),
        "policies": bool(has_policies),
        "links": bool(list(links)),
    }

    completed = [
        {"key": key, "label": label, "section": section}
        for key, label, section in COMPLETION_ITEMS if done.get(key)
    ]
    missing = [
        {"key": key, "label": label, "section": section}
        for key, label, section in COMPLETION_ITEMS if not done.get(key)
    ]
    total = len(COMPLETION_ITEMS)
    percent = int(round(100.0 * len(completed) / total)) if total else 0
    return {
        "percent": percent,
        "completed": completed,
        "missing": missing,
        "total": total,
        # The single most useful next step, so the primary button has somewhere to go.
        "next_key": missing[0]["key"] if missing else None,
        "next_label": missing[0]["label"] if missing else None,
    }


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #

#: Everything a client may set on the profile row, with its validator.
_TEXT_FIELDS: Dict[str, int] = {
    "business_name": NAME_MAX,
    "legal_name": NAME_MAX,
    "tagline": TAGLINE_MAX,
    "about": ABOUT_MAX,
    "what_you_sell": SHORT_MAX,
    "service_area": SHORT_MAX,
    "shipping_summary": SHORT_MAX,
    "return_summary": SHORT_MAX,
    "response_expectations": SHORT_MAX,
    "response_hours": SHORT_MAX,
    "public_city": 120,
    "public_region": 120,
    "public_country": 120,
}

WRITABLE_FIELDS = frozenset(
    set(_TEXT_FIELDS)
    | {"business_category", "support_email", "support_phone",
       "support_email_visibility", "support_phone_visibility",
       "preferred_contact", "languages", "accessibility", "hours_mode"}
)


def _validate_field(field: str, value: Any) -> Any:
    if field in _TEXT_FIELDS:
        return _text(value, _TEXT_FIELDS[field])

    if field == "business_category":
        text = str(value or "").strip().lower()
        if text and text not in BUSINESS_CATEGORIES:
            raise ProfileError("Choose a category from the list.", 400, "invalid_category")
        return text

    if field == "support_email":
        text = _text(value, 200)
        if text and not _EMAIL_RE.match(text):
            raise ProfileError("Enter a valid email address.", 400, "invalid_email")
        return text

    if field == "support_phone":
        text = _text(value, 60)
        if text and not re.fullmatch(r"[0-9+()\-.\s]{5,60}", text):
            raise ProfileError("Enter a valid phone number.", 400, "invalid_phone")
        return text

    if field in {"support_email_visibility", "support_phone_visibility"}:
        text = str(value or "").strip().lower()
        if text not in CONTACT_VISIBILITY:
            raise ProfileError("Unknown visibility setting.", 400, "invalid_visibility")
        return text

    if field == "preferred_contact":
        text = str(value or "").strip().lower()
        if text not in PREFERRED_CONTACT:
            raise ProfileError("Unknown contact preference.", 400, "invalid_contact")
        return text

    if field == "hours_mode":
        text = str(value or "").strip().lower()
        if text not in HOURS_MODES:
            raise ProfileError("Unknown opening-hours mode.", 400, "invalid_hours_mode")
        return text

    if field in {"languages", "accessibility"}:
        if isinstance(value, str):
            items = [part.strip() for part in value.split(",")]
        elif isinstance(value, (list, tuple)):
            items = [str(part).strip() for part in value]
        else:
            raise ProfileError("Expected a list.", 400, "invalid")
        return json.dumps([item[:80] for item in items if item][:20])

    raise ProfileError("That field cannot be edited here.", 400, "unknown_field")


#: ``languages`` and ``accessibility`` are sent as lists but stored as JSON text.
_COLUMN_FOR_FIELD = {"languages": "languages_json", "accessibility": "accessibility_json"}


def ensure_profile(conn, user_id: int) -> Dict[str, Any]:
    """Return the profile row, creating and seeding it on first touch.

    Seeding copies the buyer-facing subset of an approved seller application across
    once. It is a copy, not a live read: from this moment the profile is the source
    of truth and the application stays frozen as the review artifact it is.
    """
    existing = _profile_row(conn, user_id)
    if existing:
        return existing

    application = _application_row(conn, user_id)
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO business_os_seller_profile
            (user_id, business_name, about, support_email, support_phone,
             public_region, public_country, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            _text(application.get("business_name"), NAME_MAX),
            _text(application.get("business_description"), ABOUT_MAX),
            _text(application.get("email"), 200),
            _text(application.get("phone"), 60),
            _text(application.get("state_region"), 120),
            _text(application.get("country"), 120),
            now,
            now,
        ),
    )
    # A website on the application is the seller's one known link; carrying it over
    # means the Links row is not empty for someone who already gave us one.
    website = _text(application.get("website"), URL_MAX)
    if website and _URL_RE.match(website):
        conn.execute(
            "INSERT INTO business_os_seller_profile_links "
            "(user_id, kind, url, label, position, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (int(user_id), "website", website, None, 0, now),
        )
    return _profile_row(conn, user_id)


def update_profile(user_id: int, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Save the fields that validate; report the ones that did not.

    Deliberately not all-or-nothing. A seller correcting five things should not lose
    four of them because the sixth had a typo in a URL, and a field that needs
    verification review must not hold the other five hostage — which is exactly the
    failure the brief calls out.
    """
    if not isinstance(payload, Mapping):
        raise ProfileError("Expected an object.", 400, "invalid")

    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        current = ensure_profile(conn, user_id)

        verification = resolve_verification(
            business_request=_business_verification_request(conn, user_id),
            application_status=_application_row(conn, user_id).get("status"),
            verified_badge=_user_row(conn, user_id).get("verified_badge"),
        )
        locks = verification_locks(verification["state"])
        blocked = set(locks["blocked"])

        saved: Dict[str, Any] = {}
        rejected: Dict[str, str] = {}
        queued_for_review: List[str] = []
        now = _now_iso()

        for field, raw in payload.items():
            if field not in WRITABLE_FIELDS:
                rejected[field] = "That field cannot be edited here."
                continue
            if field in blocked:
                rejected[field] = ("This field is locked while the business is under "
                                   "enforcement review.")
                continue
            try:
                value = _validate_field(field, raw)
            except ProfileError as exc:
                rejected[field] = str(exc)
                continue

            column = _COLUMN_FOR_FIELD.get(field, field)
            before = current.get(column)
            if str(before or "") == str(value or ""):
                continue

            conn.execute(
                f"UPDATE business_os_seller_profile SET {column}=?, updated_at=? WHERE user_id=?",
                (value, now, int(user_id)),
            )
            needs_review = field in IDENTITY_SENSITIVE_FIELDS and field in set(locks["requires_review"])
            conn.execute(
                "INSERT INTO business_os_seller_profile_audit "
                "(user_id, field, before_value, after_value, requires_review, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (int(user_id), field, str(before or "")[:500], str(value or "")[:500],
                 1 if needs_review else 0, now),
            )
            saved[field] = value
            if needs_review:
                queued_for_review.append(field)

        conn.commit()
        return {
            "saved": saved,
            "rejected": rejected,
            "queued_for_review": queued_for_review,
            "profile": owner_profile(user_id, conn=conn),
        }
    finally:
        conn.close()


def set_hours(user_id: int, mode: str, days: Any = None) -> Dict[str, Any]:
    """Replace the weekly pattern. ``days`` is a list of {weekday, closed, opens, closes}."""
    mode_value = str(mode or "").strip().lower()
    if mode_value not in HOURS_MODES:
        raise ProfileError("Unknown opening-hours mode.", 400, "invalid_hours_mode")

    entries: List[Tuple[str, int, Optional[str], Optional[str]]] = []
    for entry in (days or []):
        if not isinstance(entry, Mapping):
            raise ProfileError("Each day must be an object.", 400, "invalid")
        weekday = str(entry.get("weekday") or "").strip().lower()[:3]
        if weekday not in WEEKDAYS:
            raise ProfileError(f"Unknown weekday: {entry.get('weekday')!r}", 400, "invalid_weekday")
        closed = _truthy(entry.get("closed"))
        opens = str(entry.get("opens") or "").strip() or None
        closes = str(entry.get("closes") or "").strip() or None
        if not closed:
            if not opens or not closes:
                raise ProfileError(f"{WEEKDAY_LABELS[weekday]} needs an opening and a "
                                   f"closing time.", 400, "invalid_hours")
            if not _TIME_RE.match(opens) or not _TIME_RE.match(closes):
                raise ProfileError("Times must look like 09:00.", 400, "invalid_time")
            if opens >= closes:
                # Zero-length and overnight ranges are both rejected rather than
                # guessed at: "18:00–02:00" could mean a late bar or a typo, and
                # picking one silently would publish the wrong answer to buyers.
                raise ProfileError(f"{WEEKDAY_LABELS[weekday]} closes before it opens.",
                                   400, "invalid_range")
        entries.append((weekday, 1 if closed else 0, None if closed else opens,
                        None if closed else closes))

    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        ensure_profile(conn, user_id)
        now = _now_iso()
        conn.execute("DELETE FROM business_os_seller_profile_hours WHERE user_id=?",
                     (int(user_id),))
        for weekday, closed, opens, closes in entries:
            conn.execute(
                "INSERT INTO business_os_seller_profile_hours "
                "(user_id, weekday, closed, opens, closes, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (int(user_id), weekday, closed, opens, closes, now),
            )
        conn.execute(
            "UPDATE business_os_seller_profile SET hours_mode=?, updated_at=? WHERE user_id=?",
            (mode_value, now, int(user_id)),
        )
        conn.commit()
        return owner_profile(user_id, conn=conn)
    finally:
        conn.close()


def set_link(user_id: int, kind: str, url: Any, *, label: Any = None,
             position: int = 0) -> Dict[str, Any]:
    kind_value = str(kind or "").strip().lower()
    if kind_value not in LINK_KINDS:
        raise ProfileError("Unknown link type.", 400, "invalid_link_kind")
    text = _text(url, URL_MAX)
    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        ensure_profile(conn, user_id)
        now = _now_iso()
        if not text:
            conn.execute(
                "DELETE FROM business_os_seller_profile_links WHERE user_id=? AND kind=?",
                (int(user_id), kind_value),
            )
        else:
            if not _URL_RE.match(text):
                raise ProfileError("Links must start with http:// or https://.",
                                   400, "invalid_url")
            conn.execute(
                "DELETE FROM business_os_seller_profile_links WHERE user_id=? AND kind=?",
                (int(user_id), kind_value),
            )
            conn.execute(
                "INSERT INTO business_os_seller_profile_links "
                "(user_id, kind, url, label, position, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (int(user_id), kind_value, text, _text(label, 80) or None,
                 int(position or 0), now),
            )
        conn.execute("UPDATE business_os_seller_profile SET updated_at=? WHERE user_id=?",
                     (now, int(user_id)))
        conn.commit()
        return owner_profile(user_id, conn=conn)
    finally:
        conn.close()


def set_address(user_id: int, kind: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    kind_value = str(kind or "").strip().lower()
    if kind_value not in ADDRESS_KINDS:
        raise ProfileError("Unknown address type.", 400, "invalid_address_kind")
    if not isinstance(payload, Mapping):
        raise ProfileError("Expected an object.", 400, "invalid")
    values = {
        field: _text(payload.get(field), 200)
        for field in ("line1", "line2", "city", "region", "postal_code", "country")
    }
    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        ensure_profile(conn, user_id)
        now = _now_iso()
        conn.execute(
            "DELETE FROM business_os_seller_profile_addresses WHERE user_id=? AND kind=?",
            (int(user_id), kind_value),
        )
        if any(values.values()):
            conn.execute(
                "INSERT INTO business_os_seller_profile_addresses "
                "(user_id, kind, line1, line2, city, region, postal_code, country, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(user_id), kind_value, values["line1"], values["line2"],
                 values["city"], values["region"], values["postal_code"],
                 values["country"], now),
            )
        conn.commit()
        return owner_profile(user_id, conn=conn)
    finally:
        conn.close()


def set_hours_override(user_id: int, on_date: Any, *, closed: Any = True,
                       opens: Any = None, closes: Any = None,
                       label: Any = None) -> Dict[str, Any]:
    """Add, replace or clear a dated exception to the weekly pattern.

    Holiday closures and one-off early closes are the cases a buyer is most likely to
    be burned by, so they are stored as their own dated rows and checked ahead of the
    weekly pattern rather than being folded into it.

    Passing ``closed=False`` with no times clears the override for that date.
    """
    date_value = str(on_date or "").strip()
    if not _DATE_RE.match(date_value):
        raise ProfileError("Dates must look like 2026-12-25.", 400, "invalid_date")

    is_closed = _truthy(closed)
    open_value = str(opens or "").strip() or None
    close_value = str(closes or "").strip() or None

    if not is_closed:
        if open_value or close_value:
            if not open_value or not close_value:
                raise ProfileError("An open day needs both an opening and a closing time.",
                                   400, "invalid_hours")
            if not _TIME_RE.match(open_value) or not _TIME_RE.match(close_value):
                raise ProfileError("Times must look like 09:00.", 400, "invalid_time")
            if open_value >= close_value:
                raise ProfileError("That day closes before it opens.", 400, "invalid_range")

    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        ensure_profile(conn, user_id)
        now = _now_iso()
        conn.execute(
            "DELETE FROM business_os_seller_profile_hours_overrides "
            "WHERE user_id=? AND on_date=?",
            (int(user_id), date_value),
        )
        # "Open as normal" is the absence of an override, not an override saying so.
        if is_closed or (open_value and close_value):
            conn.execute(
                "INSERT INTO business_os_seller_profile_hours_overrides "
                "(user_id, on_date, closed, opens, closes, label, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(user_id), date_value, 1 if is_closed else 0,
                 None if is_closed else open_value,
                 None if is_closed else close_value,
                 _text(label, 80) or None, now),
            )
        conn.execute("UPDATE business_os_seller_profile SET updated_at=? WHERE user_id=?",
                     (now, int(user_id)))
        conn.commit()
        return owner_profile(user_id, conn=conn)
    finally:
        conn.close()


#: The rule the signup form already enforces. Restated here rather than imported so
#: the pre-flight check and the eventual write cannot disagree about what is legal.
HANDLE_RE = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")


def check_handle(user_id: int, candidate: Any) -> Dict[str, Any]:
    """Answer "can I have this handle?" *before* the seller commits to it.

    The existing update endpoint validates and rejects on write, which means the only
    way to discover a handle is taken is to try to take it and fail. That is fine for
    a settings toggle and wrong for an identity change the brief wants gated behind a
    warning, a cooldown and a preview — all of which need the answer up front.
    """
    text = re.sub(r"^@+", "", str(candidate or "").strip())
    result: Dict[str, Any] = {
        "candidate": text,
        "handle": normalize_handle(text),
        "available": False,
        "reason": "",
        "is_current": False,
    }
    if not text:
        result["reason"] = "Enter a handle."
        return result
    if not HANDLE_RE.match(text):
        result["reason"] = ("Handles use 3–40 letters, numbers, dots, dashes or "
                            "underscores.")
        return result

    conn = db.connect()
    try:
        current = str(_user_row(conn, user_id).get("username") or "")
        if current and current.lower() == text.lower():
            result["available"] = True
            result["is_current"] = True
            result["reason"] = "This is already your handle."
            return result
        try:
            taken = _row(conn.execute(
                "SELECT user_id FROM users WHERE lower(username)=lower(?) LIMIT 1", (text,)))
        except Exception:
            # Never answer "available" from a failed lookup. Telling a seller a handle
            # is free and then rejecting the save is worse than admitting we don't know.
            result["reason"] = "Couldn't check that handle right now. Try again."
            return result
        if taken:
            result["reason"] = "That handle is taken."
            return result
        result["available"] = True
        result["reason"] = "Available."
        return result
    finally:
        conn.close()


def publish(user_id: int) -> Dict[str, Any]:
    """Mark the current saved state as the published one.

    Separate from saving so that ``LIVE SYNC`` can tell the truth. Before this exists
    the badge is asserting that whatever is on screen is public, which is false the
    moment anyone types into a field.
    """
    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        ensure_profile(conn, user_id)
        now = _now_iso()
        conn.execute(
            "UPDATE business_os_seller_profile SET published_at=?, updated_at=? WHERE user_id=?",
            (now, now, int(user_id)),
        )
        conn.commit()
        return owner_profile(user_id, conn=conn)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# The owner view
# --------------------------------------------------------------------------- #

def owner_profile(user_id: int, *, conn=None) -> Dict[str, Any]:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        schema.ensure_schema(conn)
        profile = ensure_profile(conn, user_id)
        if owned:
            conn.commit()

        hours_rows = _hours_rows(conn, user_id)
        links = _link_rows(conn, user_id)
        addresses = _address_rows(conn, user_id)
        overrides = _override_rows(conn, user_id)
        application = _application_row(conn, user_id)
        user = _user_row(conn, user_id)

        verification = resolve_verification(
            business_request=_business_verification_request(conn, user_id),
            application_status=application.get("status"),
            verified_badge=user.get("verified_badge"),
        )
        locks = verification_locks(verification["state"])
        hours = hours_view(hours_rows, str(profile.get("hours_mode") or "unset"))

        has_logo = bool(user.get("avatar_url") or user.get("avatar_thumbnail_url"))
        completion = completeness(
            profile, hours=hours, links=links, has_logo=has_logo,
            has_policies=bool(_text(profile.get("return_summary"), SHORT_MAX)),
        )

        return {
            "user_id": int(user_id),
            "handle": normalize_handle(user.get("username") or application.get("pulse_username")),
            "business_name": _text(profile.get("business_name"), NAME_MAX),
            "legal_name": _text(profile.get("legal_name"), NAME_MAX),
            "business_category": str(profile.get("business_category") or ""),
            "business_category_label": BUSINESS_CATEGORY_LABELS.get(
                str(profile.get("business_category") or ""), ""),
            # Kept apart from the category on purpose — see BUSINESS_CATEGORIES.
            "seller_type": str(application.get("seller_type") or ""),
            "tagline": _text(profile.get("tagline"), TAGLINE_MAX),
            "about": _text(profile.get("about"), ABOUT_MAX),
            "what_you_sell": _text(profile.get("what_you_sell"), SHORT_MAX),
            "service_area": _text(profile.get("service_area"), SHORT_MAX),
            "shipping_summary": _text(profile.get("shipping_summary"), SHORT_MAX),
            "return_summary": _text(profile.get("return_summary"), SHORT_MAX),
            "response_expectations": _text(profile.get("response_expectations"), SHORT_MAX),
            "response_hours": _text(profile.get("response_hours"), SHORT_MAX),
            "languages": _json_list(profile.get("languages_json")),
            "accessibility": _json_list(profile.get("accessibility_json")),
            "public_location": {
                "city": _text(profile.get("public_city"), 120),
                "region": _text(profile.get("public_region"), 120),
                "country": _text(profile.get("public_country"), 120),
            },
            "contact": {
                "email": _text(profile.get("support_email"), 200),
                "email_visibility": _visibility(profile.get("support_email_visibility")),
                "phone": _text(profile.get("support_phone"), 60),
                "phone_visibility": _visibility(profile.get("support_phone_visibility")),
                "preferred": str(profile.get("preferred_contact") or "message"),
            },
            "hours_mode": str(profile.get("hours_mode") or "unset"),
            "hours": hours,
            "hours_overrides": [
                {"date": str(row.get("on_date") or ""),
                 "closed": _truthy(row.get("closed")),
                 "opens": str(row.get("opens") or "") or None,
                 "closes": str(row.get("closes") or "") or None,
                 "label": str(row.get("label") or "") or None}
                for row in overrides
            ],
            "links": [
                {"kind": str(row.get("kind") or ""), "url": str(row.get("url") or ""),
                 "label": str(row.get("label") or "") or None,
                 "position": int(row.get("position") or 0)}
                for row in links
            ],
            "addresses": [
                {"kind": str(row.get("kind") or ""),
                 "line1": str(row.get("line1") or ""), "line2": str(row.get("line2") or ""),
                 "city": str(row.get("city") or ""), "region": str(row.get("region") or ""),
                 "postal_code": str(row.get("postal_code") or ""),
                 "country": str(row.get("country") or "")}
                for row in addresses
            ],
            "verification": verification,
            "locks": locks,
            "completion": completion,
            "sync": sync_state(profile, verification),
            "published_at": str(profile.get("published_at") or "") or None,
            "updated_at": str(profile.get("updated_at") or "") or None,
        }
    finally:
        if owned:
            conn.close()


def normalize_handle(value: Any) -> str:
    """One leading ``@``, however many the stored value happens to carry.

    The screen produced ``@@Pilot-8919`` by prefixing ``@`` onto a value the seller
    had typed *with* an ``@`` into the application form. Normalising on read, at the
    single point that builds the handle, is what stops the next caller repeating it.
    """
    text = str(value or "").strip()
    text = re.sub(r"^@+", "", text)
    return f"@{text}" if text else ""


def sync_state(profile: Mapping[str, Any],
               verification: Mapping[str, Any]) -> Dict[str, Any]:
    """What ``LIVE SYNC`` is entitled to claim.

    Only three of the six states are the server's to assert. ``saving``, ``offline``
    and ``sync_failed`` describe the client's own request and are set by the client;
    the server saying "synced" while the phone has no signal would be worse than
    saying nothing.
    """
    published = str(profile.get("published_at") or "")
    updated = str(profile.get("updated_at") or "")
    if verification.get("state") in {"needs_information", "suspended", "revoked"}:
        state = "review_required"
    elif not published:
        state = "changes_pending"
    elif updated and published and updated > published:
        state = "changes_pending"
    else:
        state = "synced"
    return {
        "state": state,
        "published_at": published or None,
        "updated_at": updated or None,
    }


# --------------------------------------------------------------------------- #
# The buyer view — built from an allowlist, never by redaction
# --------------------------------------------------------------------------- #

#: Everything a buyer may ever be shown. This tuple is the security boundary; a test
#: pins it so that adding a field to the owner view cannot silently publish it.
PUBLIC_FIELDS: Tuple[str, ...] = (
    "handle", "business_name", "business_category", "business_category_label",
    "verified", "tagline", "about", "what_you_sell", "location", "shipping_summary",
    "return_summary", "response_expectations", "languages", "accessibility",
    "hours_mode", "hours", "hours_overrides", "links", "contact", "member_since",
    "policies",
)

#: Named so the reason is greppable from the test that asserts their absence.
NEVER_PUBLIC: Tuple[str, ...] = (
    "legal_name",            # review evidence, not a shop sign
    "addresses",             # pickup and warehouse addresses
    "locks",                 # internal review mechanics
    "completion",            # the owner's to-do list
    "sync",                  # publishing mechanics
    "verification",          # the request id, reviewer note and decision timestamp
    "seller_type",           # a reviewer's classification of the account
    "open_orders",           # operational load
    "store_clicks",          # internal metric
    "next_ship_day",         # belongs in shipping settings
    "payouts",               # never, under any circumstance
)


def public_profile(user_id: int, *, viewer_has_purchased: bool = False,
                   conn=None) -> Dict[str, Any]:
    """The buyer-facing profile, assembled key by key.

    ``viewer_has_purchased`` unlocks contact fields whose visibility is
    ``after_purchase``. It is passed in by the caller that actually knows the answer;
    this module never guesses it, and the preview always passes ``False`` so an owner
    previewing their own shop sees the strictest version rather than the most
    flattering one.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        schema.ensure_schema(conn)
        profile = ensure_profile(conn, user_id)
        if owned:
            conn.commit()

        user = _user_row(conn, user_id)
        application = _application_row(conn, user_id)
        verification = resolve_verification(
            business_request=_business_verification_request(conn, user_id),
            application_status=application.get("status"),
            verified_badge=user.get("verified_badge"),
        )

        floor = "after_purchase" if viewer_has_purchased else "public"
        contact: Dict[str, Any] = {"preferred": str(profile.get("preferred_contact") or "message")}
        if _visibility_at_least(profile.get("support_email_visibility"), floor):
            email = _text(profile.get("support_email"), 200)
            if email:
                contact["email"] = email
        if _visibility_at_least(profile.get("support_phone_visibility"), floor):
            phone = _text(profile.get("support_phone"), 60)
            if phone:
                contact["phone"] = phone

        # Coarse by construction. The buyer is told the town, never the doorstep.
        location_parts = [
            _text(profile.get("public_city"), 120),
            _text(profile.get("public_region"), 120),
            _text(profile.get("public_country"), 120),
        ]
        location = ", ".join(part for part in location_parts if part)

        policies = {
            key: value for key, value in (
                ("returns", _text(profile.get("return_summary"), SHORT_MAX)),
                ("shipping", _text(profile.get("shipping_summary"), SHORT_MAX)),
                ("response", _text(profile.get("response_expectations"), SHORT_MAX)),
            ) if value
        }

        return {
            "handle": normalize_handle(user.get("username") or application.get("pulse_username")),
            "business_name": _text(profile.get("business_name"), NAME_MAX),
            "business_category": str(profile.get("business_category") or ""),
            "business_category_label": BUSINESS_CATEGORY_LABELS.get(
                str(profile.get("business_category") or ""), ""),
            "verified": verification["state"] == "approved",
            "tagline": _text(profile.get("tagline"), TAGLINE_MAX),
            "about": _text(profile.get("about"), ABOUT_MAX),
            "what_you_sell": _text(profile.get("what_you_sell"), SHORT_MAX),
            "location": location,
            "shipping_summary": _text(profile.get("shipping_summary"), SHORT_MAX),
            "return_summary": _text(profile.get("return_summary"), SHORT_MAX),
            "response_expectations": _text(profile.get("response_expectations"), SHORT_MAX),
            "languages": _json_list(profile.get("languages_json")),
            "accessibility": _json_list(profile.get("accessibility_json")),
            "hours_mode": str(profile.get("hours_mode") or "unset"),
            "hours": hours_view(_hours_rows(conn, user_id),
                                str(profile.get("hours_mode") or "unset")),
            "hours_overrides": [
                {"date": str(row.get("on_date") or ""),
                 "closed": _truthy(row.get("closed")),
                 "opens": str(row.get("opens") or "") or None,
                 "closes": str(row.get("closes") or "") or None,
                 "label": str(row.get("label") or "") or None}
                for row in _override_rows(conn, user_id)
            ],
            "links": [
                {"kind": str(row.get("kind") or ""), "url": str(row.get("url") or ""),
                 "label": str(row.get("label") or "") or None}
                for row in _link_rows(conn, user_id)
            ],
            "contact": contact,
            "member_since": _year_of(application.get("created_at") or user.get("created_at")),
            "policies": policies,
        }
    finally:
        if owned:
            conn.close()


def _year_of(value: Any) -> str:
    match = re.search(r"\d{4}", str(value or ""))
    return match.group(0) if match else ""
