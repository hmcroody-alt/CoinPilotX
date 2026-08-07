"""Marketplace listing types: validation + digital deliverable file storage.

The legacy marketplace surface (``/api/pulse/marketplace/*`` over
``marketplace_listings``) supports five listing types:

    physical, digital, service, event, booking

Each type carries a small, fixed metadata document stored as JSON in
``marketplace_listings.listing_metadata_json``. This module owns:

* the type vocabulary and the derivation rule for legacy rows,
* per-type metadata validation (sanitized, capped, enums enforced),
* storage + retrieval of digital deliverable files (the seller-uploaded
  pdf/zip/etc. artifacts a buyer receives after a paid order), and
* the paid-order access check used by the authenticated download route.

Everything here is deliberately framework-free: routes in ``bot.py`` stay
thin and call into these helpers.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
from datetime import datetime
from pathlib import Path

from werkzeug.utils import secure_filename

from . import media_storage


LISTING_TYPES = ("physical", "digital", "service", "event", "booking")

#: identity mapping listing_type -> product_type (kept explicit so the contract
#: is visible and greppable, even though the values coincide today).
LISTING_TYPE_TO_PRODUCT_TYPE = {
    "physical": "physical",
    "digital": "digital",
    "service": "service",
    "event": "event",
    "booking": "booking",
}

LISTING_METADATA_MAX_BYTES = 8000

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

AVAILABILITY_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: statuses that mean "the buyer has paid" across seller/creator transactions.
PAID_ORDER_STATUSES = {"paid", "checkout_completed", "succeeded"}

DIGITAL_FILE_PREFIX = "marketplace-digital"
DIGITAL_FILE_ALLOWED_TYPES = {
    x.strip().lower()
    for x in os.getenv(
        "MARKETPLACE_DIGITAL_ALLOWED_TYPES",
        "pdf,zip,xlsx,xls,csv,txt,md,doc,docx,ppt,pptx,key,epub,mobi,json,"
        "mp3,wav,m4a,aac,ogg,mp4,mov,webm,png,jpg,jpeg,gif,webp,psd,ai,sketch,fig",
    ).split(",")
    if x.strip()
}


def _digital_file_limit_bytes():
    return int(float(os.getenv("MARKETPLACE_DIGITAL_MAX_MB", "200")) * 1024 * 1024)


# ---------------------------------------------------------------------------
# Sanitizers
# ---------------------------------------------------------------------------

def clean_text(value, limit):
    """Strip markup, collapse whitespace, cap length. Mirrors bot.clean_html."""
    text = _TAG_RE.sub(" ", str(value if value is not None else ""))
    text = _WS_RE.sub(" ", text).strip()
    return text[: int(limit)]


def _as_int(value):
    """Return (ok, int). Booleans and non-integral values are rejected."""
    if isinstance(value, bool):
        return False, 0
    if isinstance(value, int):
        return True, int(value)
    if isinstance(value, float):
        return (True, int(value)) if float(value).is_integer() else (False, 0)
    if isinstance(value, str):
        raw = value.strip()
        if raw.lstrip("-").isdigit():
            return True, int(raw)
    return False, 0


# ---------------------------------------------------------------------------
# Listing type resolution
# ---------------------------------------------------------------------------

def resolve_listing_type(raw_listing_type, product_type):
    """Resolve the effective listing_type at creation time.

    Returns ``(listing_type, error_message)``. ``error_message`` is truthy only
    when the client explicitly sent an invalid listing_type.
    """
    requested = str(raw_listing_type or "").strip().lower()
    if requested:
        if requested not in LISTING_TYPES:
            return "", f"listing_type must be one of: {', '.join(LISTING_TYPES)}."
        return requested, ""
    return effective_listing_type("", product_type), ""


def effective_listing_type(stored_listing_type, product_type):
    """Normalize a stored/legacy row to one of the five listing types."""
    stored = str(stored_listing_type or "").strip().lower()
    if stored in LISTING_TYPES:
        return stored
    fallback = str(product_type or "").strip().lower()
    return fallback if fallback in LISTING_TYPES else "physical"


def product_type_for_listing_type(listing_type):
    return LISTING_TYPE_TO_PRODUCT_TYPE.get(str(listing_type or "").strip().lower(), "")


# ---------------------------------------------------------------------------
# Metadata validation
# ---------------------------------------------------------------------------

def _take_str(raw, cleaned, key, limit):
    if key not in raw or raw[key] is None:
        return ""
    value = raw[key]
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return f"{key} must be text."
    cleaned[key] = clean_text(value, limit)
    return ""


def _take_enum(raw, cleaned, key, allowed):
    if key not in raw or raw[key] is None or raw[key] == "":
        return ""
    value = str(raw[key]).strip().lower() if isinstance(raw[key], str) else None
    if value not in allowed:
        return f"{key} must be one of: {', '.join(sorted(allowed))}."
    cleaned[key] = value
    return ""


def _take_int(raw, cleaned, key, minimum=None, maximum=None, allow_null=False):
    if key not in raw:
        return ""
    value = raw[key]
    if value is None:
        if allow_null:
            cleaned[key] = None
            return ""
        return f"{key} must be a whole number."
    ok, number = _as_int(value)
    if not ok:
        return f"{key} must be a whole number."
    if minimum is not None and number < minimum:
        return f"{key} must be between {minimum} and {maximum}."
    if maximum is not None and number > maximum:
        return f"{key} must be between {minimum} and {maximum}."
    cleaned[key] = number
    return ""


def _validate_physical(raw):
    cleaned = {}
    for error in (
        _take_str(raw, cleaned, "condition", 40),
        _take_enum(raw, cleaned, "delivery_options", {"pickup", "shipping", "both"}),
        _take_str(raw, cleaned, "location", 160),
        _take_str(raw, cleaned, "return_policy", 300),
    ):
        if error:
            return False, error
    if raw.get("variants") is not None:
        variants = raw.get("variants")
        if not isinstance(variants, list):
            return False, "variants must be a list."
        out = []
        for entry in variants[:12]:
            if not isinstance(entry, dict):
                return False, "Each variant must be an object with name and value."
            out.append({
                "name": clean_text(entry.get("name"), 40),
                "value": clean_text(entry.get("value"), 80),
            })
        cleaned["variants"] = out
    return True, cleaned


def _validate_digital(raw):
    cleaned = {}
    if "delivery" in raw and raw.get("delivery") not in (None, "", "automatic"):
        return False, "delivery must be automatic."
    if "delivery" in raw:
        cleaned["delivery"] = "automatic"
    error = _take_str(raw, cleaned, "license", 60)
    if error:
        return False, error
    error = _take_int(raw, cleaned, "download_limit", minimum=0, maximum=1000000000, allow_null=True)
    if error:
        return False, "download_limit must be null or a whole number of 0 or more."
    if raw.get("files") is not None:
        files = raw.get("files")
        if not isinstance(files, list):
            return False, "files must be a list."
        out = []
        for entry in files[:10]:
            if not isinstance(entry, dict):
                return False, "Each file must be an object with file_id, name and size_bytes."
            ok, file_id = _as_int(entry.get("file_id"))
            if not ok or file_id <= 0:
                return False, "Each file needs a valid file_id from the digital file upload endpoint."
            ok_size, size_bytes = _as_int(entry.get("size_bytes") if entry.get("size_bytes") is not None else 0)
            if not ok_size or size_bytes < 0:
                return False, "size_bytes must be a whole number of 0 or more."
            out.append({
                "file_id": file_id,
                "name": clean_text(entry.get("name"), 160),
                "size_bytes": size_bytes,
            })
        cleaned["files"] = out
    return True, cleaned


def _validate_service(raw):
    cleaned = {}
    for error in (
        _take_enum(raw, cleaned, "pricing_mode", {"fixed", "starting_at", "hourly"}),
        _take_int(raw, cleaned, "delivery_time_days", minimum=0, maximum=365),
        _take_enum(raw, cleaned, "service_location", {"remote", "in_person", "both"}),
        _take_str(raw, cleaned, "location", 160),
    ):
        if error:
            return False, error
    if raw.get("included") is not None:
        included = raw.get("included")
        if not isinstance(included, list):
            return False, "included must be a list."
        out = []
        for entry in included[:12]:
            if not isinstance(entry, (str, int, float)) or isinstance(entry, bool):
                return False, "included items must be text."
            out.append(clean_text(entry, 120))
        cleaned["included"] = out
    if raw.get("addons") is not None:
        addons = raw.get("addons")
        if not isinstance(addons, list):
            return False, "addons must be a list."
        out = []
        for entry in addons[:8]:
            if not isinstance(entry, dict):
                return False, "Each addon must be an object with title and price_label."
            out.append({
                "title": clean_text(entry.get("title"), 120),
                "price_label": clean_text(entry.get("price_label"), 40),
            })
        cleaned["addons"] = out
    return True, cleaned


def _validate_event(raw):
    cleaned = {}
    if raw.get("event_date") not in (None, ""):
        value = raw.get("event_date")
        if not isinstance(value, str):
            return False, "event_date must be an ISO date (YYYY-MM-DD)."
        date_text = clean_text(value, 20)
        if not _ISO_DATE_RE.match(date_text):
            return False, "event_date must be an ISO date (YYYY-MM-DD)."
        try:
            datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            return False, "event_date must be a real calendar date (YYYY-MM-DD)."
        cleaned["event_date"] = date_text
    for error in (
        _take_str(raw, cleaned, "start_time", 10),
        _take_str(raw, cleaned, "end_time", 10),
        _take_enum(raw, cleaned, "venue_mode", {"in_person", "online", "pulsesoc_live"}),
        _take_str(raw, cleaned, "location", 200),
        _take_str(raw, cleaned, "online_url", 400),
    ):
        if error:
            return False, error
    if raw.get("tickets") is not None:
        tickets = raw.get("tickets")
        if not isinstance(tickets, list):
            return False, "tickets must be a list."
        out = []
        for entry in tickets[:8]:
            if not isinstance(entry, dict):
                return False, "Each ticket must be an object with name, price_label and capacity."
            ticket = {
                "name": clean_text(entry.get("name"), 60),
                "price_label": clean_text(entry.get("price_label"), 40),
            }
            if entry.get("capacity") is not None:
                ok, capacity = _as_int(entry.get("capacity"))
                if not ok or capacity < 0 or capacity > 100000:
                    return False, "capacity must be between 0 and 100000."
                ticket["capacity"] = capacity
            out.append(ticket)
        cleaned["tickets"] = out
    return True, cleaned


def _validate_booking(raw):
    cleaned = {}
    for error in (
        _take_int(raw, cleaned, "duration_minutes", minimum=5, maximum=1440),
        _take_enum(raw, cleaned, "meeting_mode", {"video", "audio", "in_person"}),
        _take_int(raw, cleaned, "buffer_minutes", minimum=0, maximum=240),
        _take_str(raw, cleaned, "cancellation_policy", 200),
    ):
        if error:
            return False, error
    if raw.get("availability") is not None:
        availability = raw.get("availability")
        if not isinstance(availability, dict):
            return False, "availability must be an object keyed by day (mon..sun)."
        out = {}
        for day in AVAILABILITY_DAYS:
            if day not in availability or availability.get(day) is None:
                continue
            windows = availability.get(day)
            if not isinstance(windows, list):
                return False, f"availability.{day} must be a list of start/end windows."
            day_windows = []
            for window in windows[:4]:
                if not isinstance(window, dict):
                    return False, f"availability.{day} windows must be objects with start and end."
                start = str(window.get("start") or "").strip()
                end = str(window.get("end") or "").strip()
                if not _HHMM_RE.match(start) or not _HHMM_RE.match(end):
                    return False, f"availability.{day} windows must use HH:MM times."
                day_windows.append({"start": start, "end": end})
            out[day] = day_windows
        cleaned["availability"] = out
    return True, cleaned


_VALIDATORS = {
    "physical": _validate_physical,
    "digital": _validate_digital,
    "service": _validate_service,
    "event": _validate_event,
    "booking": _validate_booking,
}


def validate_listing_metadata(listing_type, raw_dict):
    """Validate + sanitize metadata for a listing type.

    Returns ``(True, cleaned_dict)`` or ``(False, error_message)``. Unknown keys
    are ignored, strings are stripped of markup and capped, list sizes are
    capped, and invalid enums / malformed structures are rejected.
    """
    listing_type = str(listing_type or "").strip().lower()
    if listing_type not in LISTING_TYPES:
        return False, f"listing_type must be one of: {', '.join(LISTING_TYPES)}."
    if raw_dict is None:
        return True, {}
    if not isinstance(raw_dict, dict):
        return False, "listing_metadata must be an object."
    ok, cleaned = _VALIDATORS[listing_type](raw_dict)
    if not ok:
        return False, cleaned
    try:
        serialized = json.dumps(cleaned, default=str)
    except (TypeError, ValueError):
        return False, "listing_metadata could not be saved."
    if len(serialized.encode("utf-8")) > LISTING_METADATA_MAX_BYTES:
        return False, "listing_metadata is too large."
    return True, cleaned


def dump_metadata(cleaned_dict):
    """Serialize cleaned metadata for storage; empty dict stores as ''."""
    if not cleaned_dict:
        return ""
    return json.dumps(cleaned_dict, default=str)


def parse_metadata(raw_json):
    """Parse a stored listing_metadata_json value into a dict ({} when empty)."""
    if not raw_json:
        return {}
    if isinstance(raw_json, dict):
        return raw_json
    try:
        parsed = json.loads(raw_json)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# Digital deliverable files
# ---------------------------------------------------------------------------

def verify_digital_files_owned(cur, seller_user_id, cleaned_metadata):
    """Ensure every metadata file_id is a digital file uploaded by this seller.

    Returns ``(True, "")`` or ``(False, error_message)``.
    """
    files = (cleaned_metadata or {}).get("files") or []
    for entry in files:
        file_id = int(entry.get("file_id") or 0)
        cur.execute(
            "SELECT id FROM marketplace_digital_files WHERE id=? AND seller_user_id=? LIMIT 1",
            (file_id, int(seller_user_id or 0)),
        )
        if not cur.fetchone():
            return False, f"Digital file {file_id} was not found in your uploads."
    return True, ""


def store_digital_file(user_id, file_storage):
    """Persist a seller's digital deliverable file.

    Returns ``(result_dict, http_status)``. On success the dict carries
    ``ok, file_name, file_size, storage_url`` where ``storage_url`` is the
    durable storage key under the ``marketplace-digital/`` prefix.
    """
    if not file_storage or not getattr(file_storage, "filename", ""):
        return {"ok": False, "message": "Choose a file to upload."}, 400
    original = secure_filename(file_storage.filename)[:160] or "download"
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in DIGITAL_FILE_ALLOWED_TYPES:
        return {"ok": False, "message": "That file type is not supported for digital products."}, 400
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if not size:
        return {"ok": False, "message": "That file is empty."}, 400
    if size > _digital_file_limit_bytes():
        return {"ok": False, "message": "File too large. Please upload a smaller file."}, 400
    safe_name = media_storage.safe_media_name(original)
    storage_key = f"{DIGITAL_FILE_PREFIX}/{datetime.utcnow().strftime('%Y/%m/%d')}/{safe_name}"
    local_path = media_storage.PRIVATE_UPLOAD_ROOT / storage_key
    local_path.parent.mkdir(parents=True, exist_ok=True)
    file_storage.save(local_path)
    mime_type = (
        getattr(file_storage, "mimetype", "")
        or mimetypes.guess_type(original)[0]
        or "application/octet-stream"
    )
    if media_storage.provider() in {"r2", "s3"}:
        uploaded, upload_error = media_storage._upload_to_object_storage(local_path, storage_key, mime_type)
        if not uploaded:
            try:
                local_path.unlink(missing_ok=True)
            except OSError:
                pass
            return {"ok": False, "message": "Upload could not be saved to durable storage. Please retry."}, 502
    return {
        "ok": True,
        "file_name": original,
        "file_size": int(size),
        "storage_url": storage_key,
        "mime_type": mime_type,
    }, 200


def open_digital_file(storage_url):
    """Resolve a stored digital file for delivery.

    Returns ``{"kind": "local", "path": ...}`` when the file is on local disk,
    ``{"kind": "object", "key": ...}`` when it must stream from R2/S3, or
    ``{"kind": "missing"}``.
    """
    key = str(storage_url or "").strip().replace("\\", "/").lstrip("/")
    if not key or ".." in key.split("/"):
        return {"kind": "missing"}
    local_path = media_storage.PRIVATE_UPLOAD_ROOT / key
    if local_path.exists():
        return {"kind": "local", "path": str(local_path)}
    if media_storage.provider() in {"r2", "s3"}:
        return {"kind": "object", "key": key}
    return {"kind": "missing"}


def listing_ids_referencing_digital_file(cur, seller_user_id, file_id):
    """Listing ids owned by ``seller_user_id`` whose metadata references file_id."""
    file_id = int(file_id or 0)
    if not file_id:
        return []
    cur.execute(
        """
        SELECT id, listing_metadata_json FROM marketplace_listings
        WHERE seller_user_id=? AND COALESCE(listing_type,'')='digital'
          AND COALESCE(listing_metadata_json,'')<>''
        """,
        (int(seller_user_id or 0),),
    )
    matched = []
    for row in cur.fetchall():
        item = dict(row)
        metadata = parse_metadata(item.get("listing_metadata_json"))
        for entry in metadata.get("files") or []:
            try:
                if int(entry.get("file_id") or 0) == file_id:
                    matched.append(int(item.get("id") or 0))
                    break
            except (TypeError, ValueError):
                continue
    return [listing_id for listing_id in matched if listing_id]


def buyer_paid_marketplace_listing_ids(cur, buyer_user_id):
    """Set of marketplace listing ids this buyer has a paid order for."""
    listing_ids = set()
    for table in ("seller_transactions", "creator_transactions"):
        try:
            cur.execute(
                f"SELECT item_id, item_type, status FROM {table} WHERE buyer_user_id=?",
                (int(buyer_user_id or 0),),
            )
            rows = cur.fetchall()
        except Exception:
            continue
        for row in rows:
            item = dict(row)
            if str(item.get("item_type") or "") not in {"marketplace_product", "product"}:
                continue
            if str(item.get("status") or "").lower() not in PAID_ORDER_STATUSES:
                continue
            try:
                listing_ids.add(int(item.get("item_id") or 0))
            except (TypeError, ValueError):
                continue
    listing_ids.discard(0)
    return listing_ids


def buyer_digital_files_payload(metadata, download_url_template="/api/pulse/marketplace/digital-files/{file_id}/download"):
    """Buyer-facing digital file list for a paid order: [{name, download_url}]."""
    files = []
    for entry in (metadata or {}).get("files") or []:
        try:
            file_id = int(entry.get("file_id") or 0)
        except (TypeError, ValueError):
            continue
        if not file_id:
            continue
        files.append({
            "name": entry.get("name") or f"file-{file_id}",
            "download_url": download_url_template.format(file_id=file_id),
        })
    return files
