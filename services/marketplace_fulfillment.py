"""What a buyer must tell PulseSoc before paying, decided per order type.

A marketplace listing is one of five types — physical, digital, service, event,
booking — and each needs a genuinely different answer before a card is charged.
A hoodie needs somewhere to ship to. A PDF needs nothing at all. A consultation
needs a date, a time and a timezone. Asking every buyer the same question is
wrong in both directions: it demands a postal address from someone downloading a
file, and it collects nothing from someone booking a call, leaving the seller to
chase them afterwards.

Before this module the checkout screen recognised four values —
``digital``/``pickup``/``shipping``/``both`` — and anything it did not recognise
fell through to ``shipping``. A ``booking`` listing therefore reached the payment
sheet with address collection switched on, so the one order type that never ships
anywhere was the one asking for a delivery address.

Two things live here, and they are deliberately together:

* :func:`resolve_kind` — the single rule that turns a stored listing row into a
  canonical fulfilment kind. The routes call it against the *database* row, never
  against anything the client sent, so the order type is not something a request
  can assert.
* :func:`required_fields` / :func:`validate_details` — what that kind obliges the
  buyer to supply, and whether a submission satisfies it.

Field *keys* are owned here because they are a validation contract. Field
*labels* are not: those are the client's, so they can be translated.

``mobile-native/src/api/marketplaceFulfillment.ts`` is a port of the same two
rules, so the form the buyer fills in is the form the server will accept.
``tests/test_marketplace_fulfillment.py`` pins the two together.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Canonical fulfilment kinds. `choice` variants mean the seller offers more than
# one and the buyer has not answered yet; they never survive into a charge.
KINDS = (
    "shipping",
    "pickup",
    "shipping_or_pickup",
    "digital",
    "service_remote",
    "service_in_person",
    "service_choice",
    "event_online",
    "event_in_person",
    "booking_remote",
    "booking_in_person",
)

#: Kinds that still need the buyer to pick a lane before anything else is asked.
UNDECIDED_KINDS = frozenset({"shipping_or_pickup", "service_choice"})

#: Kinds that hold no physical stock, so no inventory unit is reserved.
STOCKLESS_KINDS = frozenset({
    "digital", "service_remote", "service_in_person", "service_choice",
    "event_online", "event_in_person", "booking_remote", "booking_in_person",
})

DETAILS_REQUIRED_CODE = "FULFILLMENT_DETAILS_REQUIRED"
LANE_REQUIRED_CODE = "FULFILLMENT_REQUIRED"

_PICKUP_OR_SHIPPING = {"both", "pickup_or_shipping", "shipping_or_pickup"}

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_TZ_RE = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Addresses in these countries are complete without a postal code. Demanding one
# universally is the classic way a checkout built in the US rejects a valid
# address in Ireland or Hong Kong.
_NO_POSTAL_CODE = frozenset({
    "AE", "AO", "AG", "AW", "BS", "BZ", "BJ", "BW", "BF", "BI", "CM", "CF",
    "KM", "CG", "CD", "CK", "CI", "DJ", "DM", "GQ", "ER", "FJ", "TF", "GM",
    "GH", "GD", "GY", "HK", "IE", "JM", "KE", "KI", "KP", "LY", "MO", "MW",
    "ML", "MR", "MU", "MS", "NR", "AN", "NU", "QA", "RW", "KN", "LC", "ST",
    "SC", "SL", "SB", "SO", "SR", "SY", "TZ", "TL", "TK", "TO", "TT", "TV",
    "UG", "VU", "YE", "ZW",
})

# Countries where a carrier will not deliver on street and city alone. Kept
# short on purpose: a region demanded where none exists is a dead end for the
# buyer, and every entry beyond these is an optional line the seller can read.
_REGION_REQUIRED = frozenset({"US", "CA", "AU", "IN", "BR", "MX", "MY", "CN", "AR", "ID"})


def _clean(value: Any, limit: int) -> str:
    text = _TAG_RE.sub(" ", str(value if value is not None else ""))
    return _WS_RE.sub(" ", text).strip()[: int(limit)]


def shipping_countries() -> tuple[str, ...]:
    """Countries the marketplace will accept a delivery address in.

    Shares ``MARKETPLACE_SHIPPING_COUNTRIES`` with the Stripe address-collection
    parameter, so PulseSoc cannot accept an address at review that Stripe would
    have refused at payment.
    """
    raw = os.getenv("MARKETPLACE_SHIPPING_COUNTRIES", "US")
    codes = tuple(c.strip().upper() for c in raw.split(",") if c.strip())
    return codes or ("US",)


# ---------------------------------------------------------------------------
# Kind resolution
# ---------------------------------------------------------------------------

def resolve_kind(listing_type: Any, delivery_type: Any, metadata: Any = None) -> str:
    """Canonical fulfilment kind for a stored listing row.

    Reads the seller's own declarations — the listing type and the type-specific
    metadata they filled in — rather than the delivery column alone, which for a
    service or booking row carries no delivery meaning at all.
    """
    meta = metadata if isinstance(metadata, dict) else {}
    kind = str(listing_type or "").strip().lower()
    delivery = str(delivery_type or "").strip().lower()

    if kind == "digital":
        return "digital"

    if kind == "service":
        location = str(meta.get("service_location") or "").strip().lower()
        if location == "both":
            return "service_choice"
        return "service_in_person" if location == "in_person" else "service_remote"

    if kind == "event":
        venue = str(meta.get("venue_mode") or "").strip().lower()
        return "event_in_person" if venue == "in_person" else "event_online"

    if kind == "booking":
        return "booking_in_person" if str(meta.get("meeting_mode") or "").strip().lower() == "in_person" else "booking_remote"

    # Physical, and anything legacy that never declared a type.
    option = delivery or str(meta.get("delivery_options") or "").strip().lower()
    if option == "digital":
        return "digital"
    if option in _PICKUP_OR_SHIPPING:
        return "shipping_or_pickup"
    if option == "pickup":
        return "pickup"
    return "shipping"


def resolve_choice(kind: str, chosen: Any) -> tuple[str, str]:
    """Settle an undecided kind with the buyer's answer.

    Returns ``(kind, error_code)``. The error is non-empty when the buyer has to
    answer and has not: defaulting on their behalf is how someone collecting in
    person ends up typing an address they never needed.
    """
    if kind not in UNDECIDED_KINDS:
        return kind, ""
    answer = str(chosen or "").strip().lower()
    allowed = {
        "shipping_or_pickup": {"shipping", "pickup"},
        "service_choice": {"service_remote", "service_in_person", "remote", "in_person"},
    }[kind]
    if answer not in allowed:
        return kind, LANE_REQUIRED_CODE
    if kind == "service_choice":
        return ("service_in_person" if answer in {"in_person", "service_in_person"} else "service_remote"), ""
    return answer, ""


def needs_shipping_address(kind: str) -> bool:
    """Whether this kind puts a parcel or a person at a street address."""
    return kind in {"shipping", "service_in_person", "booking_in_person"}


# ---------------------------------------------------------------------------
# Buyer field requirements
# ---------------------------------------------------------------------------

_CONTACT = (("contact_name", "name", True), ("contact_phone", "phone", False))
_CONTACT_WITH_PHONE = (("contact_name", "name", True), ("contact_phone", "phone", True))
_ADDRESS = (
    ("address_line1", "text", True),
    ("address_line2", "text", False),
    ("address_city", "text", True),
    ("address_region", "text", False),
    ("address_postal_code", "text", False),
    ("address_country", "country", True),
)
_WHEN = (("scheduled_date", "date", True), ("scheduled_time", "time", True), ("timezone", "timezone", True))
_NOTES = (("notes", "multiline", False),)

#: kind -> ordered ``(key, type, required)`` triples. Absence is meaningful:
#: ``digital`` asks for nothing because the buyer's account already carries
#: everywhere the file has to go.
_FIELDS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "digital": (),
    "shipping": (*_CONTACT, *_ADDRESS, ("delivery_notes", "multiline", False)),
    "pickup": (*_CONTACT_WITH_PHONE, ("pickup_preference", "text", False)),
    "service_remote": (*_CONTACT, *_WHEN, *_NOTES),
    "service_in_person": (*_CONTACT_WITH_PHONE, *_WHEN, *_ADDRESS, *_NOTES),
    "event_online": (("attendee_name", "name", True), ("ticket_type", "choice", True)),
    "event_in_person": (("attendee_name", "name", True), ("contact_phone", "phone", False), ("ticket_type", "choice", True)),
    "booking_remote": (*_CONTACT, *_WHEN, *_NOTES),
    "booking_in_person": (*_CONTACT_WITH_PHONE, *_WHEN, *_ADDRESS, *_NOTES),
}

_LIMITS = {"name": 80, "phone": 32, "text": 120, "multiline": 400, "country": 2, "choice": 60, "timezone": 64}


def ticket_options(metadata: Any) -> tuple[str, ...]:
    """Ticket names the seller published, for the one ``choice`` field there is."""
    meta = metadata if isinstance(metadata, dict) else {}
    tickets = meta.get("tickets")
    if not isinstance(tickets, list):
        return ()
    names = tuple(_clean(t.get("name"), 60) for t in tickets if isinstance(t, dict) and _clean(t.get("name"), 60))
    return names


def required_fields(kind: str, metadata: Any = None) -> tuple[str, ...]:
    """Keys the buyer must always fill for ``kind``, in the order they are asked.

    Region and postal code are absent even for a shipping order: whether they are
    required depends on the country, which is not known until the buyer picks
    one. :func:`validate_details` settles those two afterwards.
    """
    out = []
    for key, _type, required in _FIELDS.get(kind, ()):
        if not required:
            continue
        # A seller who published no ticket tiers is selling one undifferentiated
        # admission, so there is nothing for the buyer to choose between.
        if key == "ticket_type" and not ticket_options(metadata):
            continue
        out.append(key)
    return tuple(out)


def field_spec(kind: str, metadata: Any = None) -> tuple[dict[str, Any], ...]:
    """Every field for ``kind``, required or not, with its type and options."""
    tickets = ticket_options(metadata)
    spec = []
    for key, field_type, required in _FIELDS.get(kind, ()):
        if key == "ticket_type":
            if not tickets:
                continue
            spec.append({"key": key, "type": "choice", "required": True, "options": list(tickets)})
            continue
        spec.append({"key": key, "type": field_type, "required": required})
    return tuple(spec)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _reject(message: str, field: str = "") -> dict[str, Any]:
    return {"code": DETAILS_REQUIRED_CODE, "status": 400, "message": message, "field": field}


def validate_details(kind: str, raw: Any, metadata: Any = None) -> tuple[bool, Any]:
    """Check a buyer submission against what ``kind`` requires.

    Returns ``(True, cleaned_dict)`` or ``(False, error_dict)``, the error shaped
    like the other pre-flight refusals — ``code``/``status``/``message`` — so the
    three checkout lanes answer it the way they answer everything else.

    Unknown keys are dropped rather than rejected: a client that asks for one
    optional field too many should not be able to fail a buyer's checkout.
    """
    if kind in UNDECIDED_KINDS:
        return False, {"code": LANE_REQUIRED_CODE, "status": 400,
                       "message": "Choose how you want this order fulfilled before you pay.", "field": ""}
    spec = field_spec(kind, metadata)
    if not spec:
        return True, {}
    submitted = raw if isinstance(raw, dict) else {}
    cleaned: dict[str, Any] = {}

    for field in spec:
        key = field["key"]
        value = _clean(submitted.get(key), _LIMITS.get(field["type"], 120))
        if not value:
            if field["required"]:
                return False, _reject(f"{_LABELS[key]} is required before you can pay.", key)
            continue

        field_type = field["type"]
        if field_type == "date":
            if not _ISO_DATE_RE.match(value):
                return False, _reject(f"{_LABELS[key]} must be a calendar date.", key)
        elif field_type == "time":
            if not _HHMM_RE.match(value):
                return False, _reject(f"{_LABELS[key]} must be a time of day.", key)
        elif field_type == "timezone":
            if not _TZ_RE.match(value):
                return False, _reject("Choose a valid timezone.", key)
        elif field_type == "country":
            value = value.upper()
            if not _COUNTRY_RE.match(value):
                return False, _reject("Choose a country.", key)
            if value not in shipping_countries():
                return False, _reject("This seller does not deliver to that country yet.", key)
        elif field_type == "choice":
            if value not in set(field.get("options") or ()):
                return False, _reject(f"Choose {_LABELS[key].lower()}.", key)
        cleaned[key] = value

    if needs_shipping_address(kind):
        country = cleaned.get("address_country", "")
        # Required-ness that depends on the country, resolved after it is known.
        if country in _REGION_REQUIRED and not cleaned.get("address_region"):
            return False, _reject("State or province is required for that country.", "address_region")
        if country not in _NO_POSTAL_CODE and not cleaned.get("address_postal_code"):
            return False, _reject("Postal code is required for that country.", "address_postal_code")

    return True, cleaned


_LABELS = {
    "contact_name": "Full name",
    "contact_phone": "Phone number",
    "attendee_name": "Attendee name",
    "address_line1": "Street address",
    "address_line2": "Apartment or unit",
    "address_city": "City",
    "address_region": "State or province",
    "address_postal_code": "Postal code",
    "address_country": "Country",
    "scheduled_date": "Date",
    "scheduled_time": "Time",
    "timezone": "Timezone",
    "ticket_type": "Ticket type",
    "notes": "Notes",
    "delivery_notes": "Delivery notes",
    "pickup_preference": "Pickup preference",
}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def snapshot(kind: str, cleaned: Any) -> dict[str, Any]:
    """The fulfilment record to freeze onto the transaction.

    Written into ``seller_transactions.metadata_json`` beside the commercial
    quote, so an order read back next year still says where it was going and
    when it was booked for, even if the seller has since edited the listing.
    """
    return {"kind": kind, "details": dict(cleaned or {})}


def stripe_shipping(cleaned: Any) -> dict[str, Any]:
    """Stripe's ``shipping`` object for an address PulseSoc already collected.

    Handing it over is what stops the payment sheet asking a second time for
    something the buyer typed on the review screen a moment earlier.
    """
    details = cleaned if isinstance(cleaned, dict) else {}
    if not details.get("address_line1"):
        return {}
    address = {
        "line1": details.get("address_line1", ""),
        "city": details.get("address_city", ""),
        "country": details.get("address_country", ""),
    }
    for key, target in (("address_line2", "line2"), ("address_region", "state"), ("address_postal_code", "postal_code")):
        if details.get(key):
            address[target] = details[key]
    shipping = {"name": details.get("contact_name") or details.get("attendee_name") or "", "address": address}
    if details.get("contact_phone"):
        shipping["phone"] = details["contact_phone"]
    return shipping if shipping["name"] else {}
