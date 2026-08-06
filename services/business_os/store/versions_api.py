"""Business OS — Storefront VERSIONS: framework-agnostic HTTP controller.

Same contract as the sibling controllers: ``(status, body)`` tuples, DARK (404)
when ``BUSINESS_OS_STORE`` is off, field allowlists, curated errors only.

Intended mount (when bot.py is quiet enough to touch):

    POST /api/business-os/store/<biz>/storefront/versions/publish -> post_publish
    POST /api/business-os/store/<biz>/storefront/versions/<id>/restore -> post_restore
    GET  /api/business-os/store/<biz>/storefront/versions          -> get_list
    GET  /api/business-os/store/<biz>/storefront/versions/<id>     -> get_one
    GET  /api/business-os/store/<biz>/storefront/draft-status      -> get_draft_status
    GET  /api/business-os/store/<biz>/storefront/published         -> get_published

No schema of its own beyond ``versions.ensure_schema``.
"""

from __future__ import annotations

from typing import Any, Optional

from services.business_os.store import service as _svc
from services.business_os.store import versions as ver
from services.business_os.store.service import StoreError


PUBLISH_FIELDS = {"note"}
RESTORE_FIELDS = {"note"}


def _dark():
    return (404, {"ok": False, "error": "Not found.", "code": "not_found"})


def _err(exc: StoreError):
    return (exc.http_status, {"ok": False, "error": str(exc), "code": exc.code})


def _check_fields(body, allowed):
    if body is None:
        return None
    if not isinstance(body, dict):
        return (400, {"ok": False, "error": "Body must be a JSON object.",
                      "code": "bad_body"})
    unknown = sorted(set(body) - allowed)
    if unknown:
        return (400, {"ok": False,
                      "error": f"Unknown field(s): {', '.join(unknown)}.",
                      "code": "unknown_field"})
    return None


def post_publish(business_id: str, actor_user_id: Any,
                 body: Optional[dict] = None, *,
                 context: Optional[dict] = None):
    if not _svc.is_enabled():
        return _dark()
    bad = _check_fields(body, PUBLISH_FIELDS)
    if bad:
        return bad
    try:
        v = ver.publish_version(business_id, actor_user_id,
                                note=(body or {}).get("note"), context=context)
        return (200 if v.get("unchanged") else 201, {"ok": True, "version": v})
    except StoreError as exc:
        return _err(exc)


def post_restore(business_id: str, actor_user_id: Any, version_id: str,
                 body: Optional[dict] = None, *,
                 context: Optional[dict] = None):
    if not _svc.is_enabled():
        return _dark()
    bad = _check_fields(body, RESTORE_FIELDS)
    if bad:
        return bad
    try:
        v = ver.restore_version(business_id, actor_user_id, version_id,
                                note=(body or {}).get("note"), context=context)
        return (201, {"ok": True, "version": v})
    except StoreError as exc:
        return _err(exc)


def get_list(business_id: str, actor_user_id: Any, *, limit: int = 50):
    if not _svc.is_enabled():
        return _dark()
    try:
        return (200, {"ok": True,
                      "versions": ver.list_versions(business_id, actor_user_id,
                                                    limit=limit)})
    except StoreError as exc:
        return _err(exc)


def get_one(business_id: str, actor_user_id: Any, version_id: str):
    if not _svc.is_enabled():
        return _dark()
    try:
        return (200, {"ok": True,
                      "version": ver.get_version(business_id, actor_user_id,
                                                 version_id)})
    except StoreError as exc:
        return _err(exc)


def get_draft_status(business_id: str, actor_user_id: Any):
    if not _svc.is_enabled():
        return _dark()
    try:
        return (200, {"ok": True,
                      "draft": ver.draft_status(business_id, actor_user_id)})
    except StoreError as exc:
        return _err(exc)


def get_published(business_id: str):
    """Public shopper read — no auth. None from the engine is a plain 404 here:
    an unpublished shop and a nonexistent shop answer identically."""
    if not _svc.is_enabled():
        return _dark()
    try:
        sf = ver.published_storefront(business_id)
    except StoreError as exc:
        return _err(exc)
    if sf is None:
        return (404, {"ok": False, "error": "Storefront not found.",
                      "code": "not_found"})
    return (200, {"ok": True, "storefront": sf})
