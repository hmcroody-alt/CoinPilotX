"""Business OS — Advertising vertical, slice-6 creative service (flag-gated).

A creative is the leaf of the canonical hierarchy
``advertiser -> campaign -> ad set -> creative``. It binds to BOTH an ad set and
its campaign, always under the SAME advertiser owner, and carries its own review
lifecycle ``draft|submitted|approved|rejected|archived``.

Two safety-critical validations live here (spec §5):

  * **Media** is validated against the AUTHORITATIVE PulseSoc media ownership
    system (``pulse_media_assets``): the asset must exist, be owned by this
    advertiser, be a supported type matching the creative type, and be fully
    processed. The client only ever supplies a canonical integer id — never a raw
    filesystem path — and can never reference another user's media.
  * **Destination** is either an internal canonical id whose existence is verified
    against the authoritative table (profile/post/reel/marketplace_product) or a
    normalized external HTTPS URL. Non-HTTPS schemes are rejected; the normalized
    URL is stored behind an explicit later-safety-review boundary (no crawler /
    malware scanning is performed by this slice).

Revision integrity (spec §6): a DRAFT/REJECTED creative is edited in place; an
already SUBMITTED/APPROVED creative is immutable — a material revision spawns a
NEW version row (linked via ``supersedes_creative_id``) and never silently mutates
the reviewed one, preserving its review history.

Nothing here delivers, renders, transcodes, spends, or moves money.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from services import db
from services.business_os.advertising import service as _svc
from services.business_os.advertising import ad_sets as _adset
from services.business_os.advertising.service import AdvertisingError

try:  # canonical notification adapters; import defensively (never a precondition).
    from services.business_os.advertising import notifications as _notify
except Exception:  # pragma: no cover
    _notify = None


# --- vocabulary -------------------------------------------------------------
CREATIVE_TYPES = {"image", "video", "reels_video"}
# creative type -> the media_type value(s) accepted from pulse_media_assets.
_TYPE_TO_MEDIA = {
    "image": {"image"},
    "video": {"video"},
    "reels_video": {"video"},
}
DESTINATION_TYPES = {"profile", "post", "reel", "marketplace_product", "external"}
# internal destination type -> (authoritative table, id column). Fixed literals.
_INTERNAL_DEST = {
    "profile": ("users", "id"),
    "post": ("pulse_posts", "id"),
    "reel": ("pulse_reels", "id"),
    "marketplace_product": ("marketplace_listings", "id"),
}

CREATIVE_STATUSES = {"draft", "submitted", "approved", "rejected", "archived"}
CREATIVE_TRANSITIONS = {
    "draft": {"submitted", "archived"},
    "submitted": {"approved", "rejected", "draft", "archived"},
    "approved": {"archived"},
    "rejected": {"draft", "archived"},
    "archived": {"draft"},
}

HEADLINE_MAX = 200
BODY_MAX = 2000
CTA_MAX = 40
ACCESS_TEXT_MAX = 500
URL_MAX = 2048

EDITABLE_FIELDS = {
    "creative_type", "media_asset_id", "thumbnail_asset_id", "headline", "body",
    "call_to_action", "destination_type", "destination_ref", "accessibility_text",
}
# Fields whose change constitutes a *material* revision of a reviewed creative.
MATERIAL_FIELDS = {
    "creative_type", "media_asset_id", "destination_type", "destination_ref",
    "headline", "body", "call_to_action",
}


# --- projection -------------------------------------------------------------
def _creative_public(row: dict) -> dict:
    if row is None:
        return None
    return {
        "creative_id": row.get("creative_id"),
        "ad_set_id": row.get("ad_set_id"),
        "campaign_id": row.get("campaign_id"),
        "advertiser_user_id": row.get("advertiser_user_id"),
        "creative_type": row.get("creative_type"),
        "media_asset_id": row.get("media_asset_id"),
        "thumbnail_asset_id": row.get("thumbnail_asset_id"),
        "headline": row.get("headline"),
        "body": row.get("body"),
        "call_to_action": row.get("call_to_action"),
        "destination_type": row.get("destination_type"),
        "destination_ref": row.get("destination_ref"),
        "accessibility_text": row.get("accessibility_text"),
        "status": row.get("status"),
        "review_reason": row.get("review_reason"),
        "version": row.get("version"),
        "supersedes_creative_id": row.get("supersedes_creative_id"),
        "archived": row.get("status") == "archived",
        "media_ready": bool(row.get("media_asset_id")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# --- media validation (authoritative ownership) -----------------------------
def _coerce_media_id(value: Any, field: str) -> int:
    """A media reference is ALWAYS a positive canonical integer id. Anything that
    smells like a path, url, or non-integer is rejected outright — the client can
    never hand us a raw filesystem path."""
    if isinstance(value, bool):
        raise AdvertisingError(f"Invalid {field}.", 400, "bad_media_ref")
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
    else:
        raise AdvertisingError(
            f"{field} must be a canonical media id.", 400, "bad_media_ref")
    if n <= 0:
        raise AdvertisingError(f"Invalid {field}.", 400, "bad_media_ref")
    return n


def _lookup_media(conn, media_id: int) -> Optional[dict]:
    try:
        return _svc._row_to_dict(conn.execute(
            "SELECT * FROM pulse_media_assets WHERE id = ?", (media_id,)
        ).fetchone())
    except Exception:
        # Table absent (misconfigured env) -> fail safe: treat as not found.
        return None


def _validate_media_asset(conn, value: Any, owner_uid: str, creative_type: str,
                          *, field: str, require_image: bool = False) -> str:
    """Validate one media reference against pulse_media_assets. Returns the
    canonical id as a string. Enforces existence, ownership, type, and readiness.
    """
    media_id = _coerce_media_id(value, field)
    asset = _lookup_media(conn, media_id)
    if asset is None:
        raise AdvertisingError("Media asset not found.", 404, "media_not_found")
    if _svc._sid(asset.get("owner_user_id")) != _svc._sid(owner_uid):
        # Never leak another user's media, and never allow cross-user reference.
        raise AdvertisingError("Media asset not found.", 404, "media_not_found")
    media_type = (asset.get("media_type") or "").strip().lower()
    if require_image:
        allowed = {"image"}
    else:
        allowed = _TYPE_TO_MEDIA.get(creative_type, set())
    if media_type not in allowed:
        raise AdvertisingError(
            f"Media type {media_type!r} does not match creative type "
            f"{creative_type!r}.", 400, "media_type_mismatch")
    if (asset.get("processing_status") or "").strip().lower() not in {"ready", ""}:
        raise AdvertisingError(
            "Media asset is not ready.", 409, "media_not_ready")
    return str(media_id)


# --- destination validation -------------------------------------------------
def _verify_internal_destination(conn, dtype: str, ref: Any) -> str:
    table, col = _INTERNAL_DEST[dtype]
    ref_id = _coerce_media_id(ref, "destination_ref")  # reuse positive-int coercion
    try:
        row = conn.execute(
            f"SELECT {col} FROM {table} WHERE {col} = ?", (ref_id,)
        ).fetchone()
    except Exception:
        # Cannot verify existence -> fail safe: reject rather than assume valid.
        raise AdvertisingError(
            "Destination could not be verified.", 404, "destination_not_found")
    if row is None:
        raise AdvertisingError(
            "Destination does not exist.", 404, "destination_not_found")
    return str(ref_id)


def _normalize_external_destination(ref: Any) -> str:
    if not isinstance(ref, str) or not ref.strip():
        raise AdvertisingError(
            "External destination URL is required.", 400, "bad_destination")
    raw = ref.strip()
    if len(raw) > URL_MAX:
        raise AdvertisingError("Destination URL too long.", 400, "bad_destination")
    parts = urlsplit(raw)
    if parts.scheme.lower() != "https":
        # Reject http/javascript/data/ftp/file and any scheme-relative input.
        raise AdvertisingError(
            "External destination must use https://.", 400, "bad_destination_scheme")
    if not parts.netloc:
        raise AdvertisingError(
            "External destination host is required.", 400, "bad_destination")
    # Normalize: lowercase scheme+host, drop fragment, keep path/query.
    normalized = urlunsplit((
        "https", parts.netloc.lower(), parts.path, parts.query, ""))
    return normalized


def _validate_destination(conn, dtype: Any, ref: Any) -> tuple:
    dtype = (dtype or "").strip().lower() if isinstance(dtype, str) else ""
    if dtype not in DESTINATION_TYPES:
        raise AdvertisingError(
            f"Unknown destination type: {dtype!r}.", 400, "bad_destination")
    if dtype == "external":
        return dtype, _normalize_external_destination(ref)
    return dtype, _verify_internal_destination(conn, dtype, ref)


# --- field validation -------------------------------------------------------
def _clip(value, limit, field) -> Optional[str]:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise AdvertisingError(f"{field} must be text.", 400, "bad_field")
    value = value.strip()
    if len(value) > limit:
        raise AdvertisingError(f"{field} exceeds {limit} characters.", 400, "field_too_long")
    return value or None


def _validate_type(value: Any) -> str:
    t = (value or "").strip().lower() if isinstance(value, str) else ""
    if t not in CREATIVE_TYPES:
        raise AdvertisingError(f"Unknown creative type: {value!r}.", 400, "bad_creative_type")
    return t


def _apply_content_fields(conn, owner_uid, creative_type, fields, target: dict) -> None:
    """Validate + copy allowlisted content fields from `fields` into `target`
    (a dict of column -> value). Media + destination are validated authoritatively.
    """
    if "media_asset_id" in fields:
        val = fields["media_asset_id"]
        target["media_asset_id"] = (
            None if val in (None, "") else
            _validate_media_asset(conn, val, owner_uid, creative_type, field="media_asset_id"))
    if "thumbnail_asset_id" in fields:
        val = fields["thumbnail_asset_id"]
        target["thumbnail_asset_id"] = (
            None if val in (None, "") else
            _validate_media_asset(conn, val, owner_uid, creative_type,
                                  field="thumbnail_asset_id", require_image=True))
    if "headline" in fields:
        target["headline"] = _clip(fields["headline"], HEADLINE_MAX, "headline")
    if "body" in fields:
        target["body"] = _clip(fields["body"], BODY_MAX, "body")
    if "call_to_action" in fields:
        target["call_to_action"] = _clip(fields["call_to_action"], CTA_MAX, "call_to_action")
    if "accessibility_text" in fields:
        target["accessibility_text"] = _clip(
            fields["accessibility_text"], ACCESS_TEXT_MAX, "accessibility_text")
    # Destination: both parts move together.
    if "destination_type" in fields or "destination_ref" in fields:
        dtype = fields.get("destination_type")
        dref = fields.get("destination_ref")
        if dtype in (None, "") and dref in (None, ""):
            target["destination_type"] = None
            target["destination_ref"] = None
        else:
            ntype, nref = _validate_destination(conn, dtype, dref)
            target["destination_type"] = ntype
            target["destination_ref"] = nref


# --- create / read / list ---------------------------------------------------
def create_creative(owner_user_id: Any, ad_set_id: str, payload: dict, *,
                    context: Optional[dict] = None, conn=None) -> dict:
    """Create a DRAFT creative under an owned ad set (whose parent campaign is not
    archived). ``creative_type`` is required; media/destination/content are
    validated when supplied so a partial draft (autosave) is allowed. Submission
    later enforces the full completeness contract."""
    _svc._require_enabled()
    if not isinstance(payload, dict):
        raise AdvertisingError("Body must be an object.", 400, "bad_body")
    unknown = set(payload) - EDITABLE_FIELDS
    if unknown:
        raise AdvertisingError(
            f"Unknown field(s): {', '.join(sorted(unknown))}.", 400, "unknown_field")
    owned = conn is None
    if owned:
        conn = db.connect()
    uid = _svc._sid(owner_user_id)
    try:
        elig = _svc.advertiser_eligibility(owner_user_id, context=context, conn=conn)
        if not elig.get("eligible"):
            raise AdvertisingError(
                f"Not eligible to create creatives ({elig.get('reason')}).",
                403, "ineligible")
        parent = _adset._get_row(conn, ad_set_id, requester_user_id=owner_user_id)
        if parent.get("status") == "archived":
            raise AdvertisingError(
                "Cannot add a creative to an archived ad set.", 409, "parent_archived")
        campaign = _svc.get_campaign(
            parent.get("campaign_id"), requester_user_id=owner_user_id, conn=conn)
        if campaign is None or campaign.get("status") == "archived":
            raise AdvertisingError(
                "Parent campaign is archived.", 409, "parent_archived")

        creative_type = _validate_type(payload.get("creative_type"))
        content: dict = {}
        _apply_content_fields(conn, uid, creative_type, payload, content)

        _svc._begin(conn)
        cid = _svc._uid()
        now = _svc._now_iso()
        conn.execute(
            "INSERT INTO business_os_ad_creatives "
            "(creative_id, ad_set_id, campaign_id, advertiser_user_id, creative_type, "
            "media_asset_id, thumbnail_asset_id, headline, body, call_to_action, "
            "destination_type, destination_ref, accessibility_text, status, "
            "review_reason, version, supersedes_creative_id, archived_at, "
            "created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', NULL, 1, NULL, "
            "NULL, ?, ?, ?)",
            (
                cid, ad_set_id, parent.get("campaign_id"), uid, creative_type,
                content.get("media_asset_id"), content.get("thumbnail_asset_id"),
                content.get("headline"), content.get("body"),
                content.get("call_to_action"), content.get("destination_type"),
                content.get("destination_ref"), content.get("accessibility_text"),
                uid, now, now,
            ),
        )
        _svc._audit(conn, campaign_id=parent.get("campaign_id"), advertiser_user_id=uid,
                    action="creative_create", actor=uid,
                    after={"creative_id": cid, "ad_set_id": ad_set_id,
                           "creative_type": creative_type, "status": "draft"})
        _svc._commit(conn)
        return _creative_public(_get_row(conn, cid, requester_user_id=owner_user_id))
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def _get_row(conn, creative_id: str, *, requester_user_id: Optional[Any]) -> Optional[dict]:
    row = _svc._row_to_dict(conn.execute(
        "SELECT * FROM business_os_ad_creatives WHERE creative_id = ?", (creative_id,)
    ).fetchone())
    if row is None:
        if requester_user_id is not None:
            raise AdvertisingError("Creative not found.", 404, "not_found")
        return None
    if requester_user_id is not None and \
            row.get("advertiser_user_id") != _svc._sid(requester_user_id):
        raise AdvertisingError("Creative not found.", 404, "not_found")
    return row


def get_creative(creative_id: str, *, requester_user_id: Optional[Any] = None,
                 conn=None) -> Optional[dict]:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_row(conn, creative_id, requester_user_id=requester_user_id)
        return _creative_public(row) if row else None
    finally:
        if owned:
            conn.close()


def list_creatives(owner_user_id: Any, *, ad_set_id: Optional[str] = None,
                   conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if ad_set_id is not None:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_creatives "
                "WHERE advertiser_user_id = ? AND ad_set_id = ? "
                "ORDER BY created_at DESC, creative_id DESC",
                (_svc._sid(owner_user_id), ad_set_id))
        else:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_creatives WHERE advertiser_user_id = ? "
                "ORDER BY created_at DESC, creative_id DESC",
                (_svc._sid(owner_user_id),))
        return [_creative_public(_svc._row_to_dict(r)) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()


# --- edit (in place) vs revise (new version) --------------------------------
def update_creative(creative_id: str, *, requester_user_id: Any, fields: dict,
                    conn=None) -> dict:
    """In-place edit of a DRAFT or REJECTED creative (strict allowlist).

    A SUBMITTED or APPROVED creative is IMMUTABLE here (409 ``not_editable``) — it
    must be revised via ``revise_creative`` which spawns a new version. Review
    state is never editable through this path."""
    _svc._require_enabled()
    if not isinstance(fields, dict):
        raise AdvertisingError("Body must be an object.", 400, "bad_body")
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise AdvertisingError(
            f"Unknown field(s): {', '.join(sorted(unknown))}.", 400, "unknown_field")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_row(conn, creative_id, requester_user_id=requester_user_id)
        if row.get("status") not in {"draft", "rejected"}:
            raise AdvertisingError(
                "Submitted or approved creatives cannot be edited in place; "
                "revise to create a new version.", 409, "not_editable")
        creative_type = (
            _validate_type(fields["creative_type"]) if "creative_type" in fields
            else row.get("creative_type"))
        updates: dict = {}
        if "creative_type" in fields:
            updates["creative_type"] = creative_type
        _apply_content_fields(conn, row.get("advertiser_user_id"), creative_type,
                              fields, updates)
        if not updates:
            return _creative_public(row)
        _svc._begin(conn)
        set_clause = ", ".join(f"{k} = ?" for k in updates)  # fixed-literal keys
        params = list(updates.values()) + [_svc._now_iso(), creative_id]
        conn.execute(
            f"UPDATE business_os_ad_creatives SET {set_clause}, "
            "version = version + 1, updated_at = ? WHERE creative_id = ?",
            tuple(params))
        _svc._audit(conn, campaign_id=row.get("campaign_id"),
                    advertiser_user_id=row.get("advertiser_user_id"),
                    action="creative_update", actor=requester_user_id,
                    before={"fields": sorted(updates)},
                    after={"creative_id": creative_id})
        _svc._commit(conn)
        return _creative_public(_get_row(conn, creative_id, requester_user_id=requester_user_id))
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def revise_creative(creative_id: str, *, requester_user_id: Any, fields: dict,
                    conn=None) -> dict:
    """Materially revise a SUBMITTED or APPROVED creative by creating a NEW version.

    The reviewed original is left completely intact (its review history is
    preserved); a new draft row is created carrying the original's fields with the
    requested changes applied, ``version = original.version + 1``, and
    ``supersedes_creative_id`` pointing at the original. Requires at least one
    material field to change."""
    _svc._require_enabled()
    if not isinstance(fields, dict):
        raise AdvertisingError("Body must be an object.", 400, "bad_body")
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise AdvertisingError(
            f"Unknown field(s): {', '.join(sorted(unknown))}.", 400, "unknown_field")
    if not (set(fields) & MATERIAL_FIELDS):
        raise AdvertisingError(
            "A revision must change at least one material field.", 400, "no_material_change")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_row(conn, creative_id, requester_user_id=requester_user_id)
        if row.get("status") not in {"submitted", "approved"}:
            raise AdvertisingError(
                "Only a submitted or approved creative is revised into a new "
                "version; draft/rejected creatives are edited in place.",
                409, "not_revisable")
        # Start from the original's current content, then apply the changes.
        creative_type = (
            _validate_type(fields["creative_type"]) if "creative_type" in fields
            else row.get("creative_type"))
        merged = {
            "media_asset_id": row.get("media_asset_id"),
            "thumbnail_asset_id": row.get("thumbnail_asset_id"),
            "headline": row.get("headline"),
            "body": row.get("body"),
            "call_to_action": row.get("call_to_action"),
            "accessibility_text": row.get("accessibility_text"),
            "destination_type": row.get("destination_type"),
            "destination_ref": row.get("destination_ref"),
        }
        _apply_content_fields(conn, row.get("advertiser_user_id"), creative_type,
                              fields, merged)
        _svc._begin(conn)
        new_id = _svc._uid()
        now = _svc._now_iso()
        conn.execute(
            "INSERT INTO business_os_ad_creatives "
            "(creative_id, ad_set_id, campaign_id, advertiser_user_id, creative_type, "
            "media_asset_id, thumbnail_asset_id, headline, body, call_to_action, "
            "destination_type, destination_ref, accessibility_text, status, "
            "review_reason, version, supersedes_creative_id, archived_at, "
            "created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', NULL, ?, ?, "
            "NULL, ?, ?, ?)",
            (
                new_id, row.get("ad_set_id"), row.get("campaign_id"),
                row.get("advertiser_user_id"), creative_type,
                merged.get("media_asset_id"), merged.get("thumbnail_asset_id"),
                merged.get("headline"), merged.get("body"),
                merged.get("call_to_action"), merged.get("destination_type"),
                merged.get("destination_ref"), merged.get("accessibility_text"),
                int(row.get("version") or 1) + 1, creative_id,
                row.get("advertiser_user_id"), now, now,
            ),
        )
        _svc._audit(conn, campaign_id=row.get("campaign_id"),
                    advertiser_user_id=row.get("advertiser_user_id"),
                    action="creative_revise", actor=requester_user_id,
                    before={"creative_id": creative_id, "version": row.get("version")},
                    after={"creative_id": new_id, "supersedes": creative_id,
                           "status": "draft"})
        _svc._commit(conn)
        return _creative_public(_get_row(conn, new_id, requester_user_id=requester_user_id))
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


# --- lifecycle --------------------------------------------------------------
def _transition(conn, row: dict, new_status: str, *, actor, action: str,
                reason=None, extra_sets: Optional[dict] = None) -> None:
    cur_status = row.get("status")
    if new_status not in CREATIVE_TRANSITIONS.get(cur_status, set()):
        raise AdvertisingError(
            f"Illegal creative transition {cur_status} -> {new_status}.",
            409, "illegal_transition")
    sets = {"status": new_status}
    if extra_sets:
        sets.update(extra_sets)
    sets["updated_at"] = _svc._now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in sets)  # fixed-literal keys only
    conn.execute(
        f"UPDATE business_os_ad_creatives SET {set_clause}, version = version + 1 "
        "WHERE creative_id = ?",
        tuple(list(sets.values()) + [row.get("creative_id")]))
    _svc._audit(conn, campaign_id=row.get("campaign_id"),
                advertiser_user_id=row.get("advertiser_user_id"),
                action=action, actor=actor, reason=reason,
                before={"status": cur_status},
                after={"creative_id": row.get("creative_id"), "status": new_status})


def submit_creative(creative_id: str, *, requester_user_id: Any, conn=None) -> dict:
    """Advertiser submits a DRAFT/REJECTED creative for review. Enforces the
    completeness contract (media + destination present and valid) and a live,
    non-archived, non-rejected parent ad set."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_row(conn, creative_id, requester_user_id=requester_user_id)
        # Parent ad set must be live and itself review-clean.
        parent = _adset._get_row(conn, row.get("ad_set_id"), requester_user_id=requester_user_id)
        if parent.get("status") in {"archived"}:
            raise AdvertisingError(
                "Parent ad set is archived; cannot submit.", 409, "parent_archived")
        if parent.get("status") == "rejected":
            raise AdvertisingError(
                "Parent ad set is rejected; resolve it before submitting creatives.",
                409, "parent_rejected")
        # Completeness contract.
        if not row.get("media_asset_id"):
            raise AdvertisingError("A media asset is required to submit.", 400, "missing_media")
        if not row.get("destination_type") or not row.get("destination_ref"):
            raise AdvertisingError("A destination is required to submit.", 400, "missing_destination")
        # Re-validate media + destination authoritatively at submit time.
        _validate_media_asset(conn, row.get("media_asset_id"),
                              row.get("advertiser_user_id"), row.get("creative_type"),
                              field="media_asset_id")
        _validate_destination(conn, row.get("destination_type"), row.get("destination_ref"))
        _svc._begin(conn)
        _transition(conn, row, "submitted", actor=requester_user_id,
                    action="creative_submit", extra_sets={"review_reason": None})
        _svc._commit(conn)
        return _creative_public(_get_row(conn, creative_id, requester_user_id=requester_user_id))
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def _owner_move(creative_id, requester_user_id, new_status, action, conn=None) -> dict:
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_row(conn, creative_id, requester_user_id=requester_user_id)
        _svc._begin(conn)
        extra = {}
        if new_status == "draft":
            extra["review_reason"] = None
        if new_status == "archived":
            extra["archived_at"] = _svc._now_iso()
        _transition(conn, row, new_status, actor=requester_user_id,
                    action=action, extra_sets=extra)
        _svc._commit(conn)
        return _creative_public(_get_row(conn, creative_id, requester_user_id=requester_user_id))
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def withdraw_creative(creative_id, *, requester_user_id, conn=None) -> dict:
    return _owner_move(creative_id, requester_user_id, "draft", "creative_withdraw", conn=conn)


def archive_creative(creative_id, *, requester_user_id, conn=None) -> dict:
    return _owner_move(creative_id, requester_user_id, "archived", "creative_archive", conn=conn)


def restore_creative(creative_id, *, requester_user_id, conn=None) -> dict:
    return _owner_move(creative_id, requester_user_id, "draft", "creative_restore", conn=conn)


# --- admin review -----------------------------------------------------------
def admin_list_creatives(*, status: Optional[str] = "submitted", conn=None) -> list:
    """Admin review queue. Defaults to creatives awaiting review (submitted)."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if status is not None:
            if status not in CREATIVE_STATUSES:
                raise AdvertisingError(f"Unknown status: {status!r}.", 400, "bad_status")
            cur = conn.execute(
                "SELECT * FROM business_os_ad_creatives WHERE status = ? "
                "ORDER BY created_at DESC, creative_id DESC", (status,))
        else:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_creatives "
                "ORDER BY created_at DESC, creative_id DESC")
        return [_creative_public(_svc._row_to_dict(r)) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()


def admin_get_creative(creative_id: str, *, conn=None) -> dict:
    """Admin read of one creative plus its parent ad-set and campaign context."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_row(conn, creative_id, requester_user_id=None)
        if row is None:
            raise AdvertisingError("Creative not found.", 404, "not_found")
        view = _creative_public(row)
        parent = _adset._get_row(conn, row.get("ad_set_id"), requester_user_id=None)
        view["ad_set"] = None if parent is None else {
            "ad_set_id": parent.get("ad_set_id"),
            "name": parent.get("name"),
            "status": parent.get("status"),
        }
        campaign = _svc.get_campaign(row.get("campaign_id"), conn=conn)
        view["campaign"] = None if campaign is None else {
            "campaign_id": campaign.get("campaign_id"),
            "name": campaign.get("name"),
            "status": campaign.get("status"),
        }
        return view
    finally:
        if owned:
            conn.close()


def admin_review_creative(creative_id: str, decision: str, *, actor: Any,
                          reason: Optional[str] = None, conn=None) -> dict:
    """Admin approves or rejects a SUBMITTED creative. Records acting admin,
    previous/new state, version, and reason. Approval does NOT publish/deliver;
    the rejection reason is stored so the owner can see WHY."""
    _svc._require_enabled()
    decision = (decision or "").strip().lower()
    if decision not in {"approve", "reject"}:
        raise AdvertisingError("Decision must be approve or reject.", 400, "bad_decision")
    if decision == "reject":
        reason = (reason or "").strip()
        if not reason:
            raise AdvertisingError("A rejection reason is required.", 400, "reason_required")
        reason = reason[:500]
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_row(conn, creative_id, requester_user_id=None)
        if row is None:
            raise AdvertisingError("Creative not found.", 404, "not_found")
        if row.get("status") != "submitted":
            raise AdvertisingError(
                "Only a submitted creative can be reviewed.", 409, "not_submitted")
        new_status = "approved" if decision == "approve" else "rejected"
        _svc._begin(conn)
        _transition(conn, row, new_status, actor=actor,
                    action=f"creative_{new_status}", reason=reason,
                    extra_sets={"review_reason": reason if decision == "reject" else None})
        _svc._commit(conn)
        reviewed = _get_row(conn, creative_id, requester_user_id=None)
        # Notify the owner AFTER commit — side effect only, emit() never raises.
        if _notify is not None and reviewed is not None:
            advertiser_uid = reviewed.get("advertiser_user_id")
            cid = reviewed.get("campaign_id")
            if new_status == "approved":
                _notify.notify_creative_approved(advertiser_uid, cid, creative_id)
            else:
                _notify.notify_creative_rejected(
                    advertiser_uid, cid, creative_id, reason=reason)
        return _creative_public(reviewed)
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()
