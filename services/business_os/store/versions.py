"""Business OS — STOREFRONT VERSIONS: draft-vs-published for the store shell.

Phase 5 of the Store OS plan. The storefront row in
``business_os_store_storefront`` is status-only: editing "About" on a live shop
changes what shoppers see mid-keystroke. This module makes the row the WORKING
DRAFT and adds an append-only version table so the public sees exactly one
explicitly published snapshot:

  * one new table (``business_os_store_storefront_versions``) — the existing
    storefront/product/collection tables keep their shapes;
  * ``publish_version`` snapshots the draft into a new immutable version and
    makes it live; re-publishing an unchanged draft returns the live version
    flagged ``unchanged: True`` (no junk rows);
  * ``restore_version`` never edits history: it creates a NEW live version from
    an old snapshot and copies that snapshot back onto the draft, so the next
    publish doesn't silently revert the restore;
  * ``draft_status`` answers "do I have unpublished changes?" with the exact
    field names — a checklist, not a boolean shrug;
  * ``published_storefront`` is the shopper read: published LIFECYCLE status
    (suspend still takes the shop dark), approved seller (revocation is
    immediate), and the live SNAPSHOT — never the draft row.

RBAC is S1's, unchanged: ``store.read`` to look, ``store.publish`` to make
things live. Flag-gated by ``BUSINESS_OS_STORE``. Additive only.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from services import db
from services.business_os.store import service as _svc
from services.business_os.store.service import StoreError


NOTE_MAX = 500

# The presentation fields a version freezes — exactly the client-writable
# storefront settings, never status/timestamps.
SNAPSHOT_FIELDS = ("name", "slug", "headline", "about", "theme", "currency")


def ensure_schema() -> None:
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_store_storefront_versions ("
            "version_id TEXT PRIMARY KEY, "
            "business_id TEXT NOT NULL, "
            "storefront_id TEXT NOT NULL, "
            "version_no INTEGER NOT NULL, "
            "snapshot_json TEXT NOT NULL, "
            "note TEXT, "
            "status TEXT NOT NULL, "          # published | superseded
            "created_by TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "UNIQUE (business_id, version_no))")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_busos_sf_versions_live "
            "ON business_os_store_storefront_versions (business_id, status)")
        conn.commit()
    finally:
        conn.close()


# --- internal ----------------------------------------------------------------
def _snapshot_of(sf_row: dict) -> dict:
    theme = None
    if sf_row.get("theme_json"):
        try:
            theme = json.loads(sf_row["theme_json"])
        except Exception:
            theme = None
    return {"name": sf_row.get("name"), "slug": sf_row.get("slug"),
            "headline": sf_row.get("headline"), "about": sf_row.get("about"),
            "theme": theme, "currency": sf_row.get("currency")}


def _canon(snapshot: dict) -> str:
    return json.dumps(snapshot, sort_keys=True)


def _live_row(conn, business_id: str) -> Optional[dict]:
    return _svc._row(conn.execute(
        "SELECT * FROM business_os_store_storefront_versions "
        "WHERE business_id = ? AND status = 'published'",
        (_svc._sid(business_id),)).fetchone())


def _version_public(row: dict, *, with_snapshot: bool = False) -> dict:
    out = {"version_id": row.get("version_id"),
           "version_no": row.get("version_no"),
           "status": row.get("status"),
           "note": row.get("note"),
           "created_by": row.get("created_by"),
           "created_at": row.get("created_at")}
    if with_snapshot:
        try:
            out["snapshot"] = json.loads(row.get("snapshot_json") or "null")
        except Exception:
            out["snapshot"] = None
    return out


def _insert_live(conn, *, business_id, storefront_id, snapshot, note, actor) -> dict:
    """Supersede the current live version (if any) and insert a new one."""
    prev = _live_row(conn, business_id)
    nxt = _svc._row(conn.execute(
        "SELECT COALESCE(MAX(version_no), 0) AS n "
        "FROM business_os_store_storefront_versions WHERE business_id = ?",
        (_svc._sid(business_id),)).fetchone())["n"] + 1
    if prev is not None:
        conn.execute(
            "UPDATE business_os_store_storefront_versions "
            "SET status = 'superseded' WHERE version_id = ?",
            (prev["version_id"],))
    vid = "sfv_" + _svc._uid()
    now = _svc._now_iso()
    conn.execute(
        "INSERT INTO business_os_store_storefront_versions "
        "(version_id, business_id, storefront_id, version_no, snapshot_json, "
        "note, status, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'published', ?, ?)",
        (vid, _svc._sid(business_id), storefront_id, nxt, _canon(snapshot),
         note, _svc._sid(actor), now))
    return {"version_id": vid, "version_no": nxt, "status": "published",
            "note": note, "created_by": _svc._sid(actor), "created_at": now,
            "prev_version_no": None if prev is None else prev["version_no"]}


# --- verbs -------------------------------------------------------------------
def publish_version(business_id: str, actor_user_id: Any, *,
                    note: Optional[str] = None,
                    context: Optional[dict] = None) -> dict:
    """Freeze the current draft into a new live version. Idempotent against an
    unchanged draft: returns the live version with ``unchanged: True``."""
    _svc._require_enabled()
    _svc._require_not_held(context)
    note = _svc._clean_str(note, field="note", max_len=NOTE_MAX)
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id,
                                     "store.publish")
        sf = _svc._get_storefront_row(conn, business_id)
        if sf is None:
            raise StoreError("Storefront not found.", 404, "not_found")
        _svc._require_seller_approved(conn, business_id, "storefront")
        snapshot = _snapshot_of(sf)
        live = _live_row(conn, business_id)
        if live is not None and live.get("snapshot_json") == _canon(snapshot):
            out = _version_public(live)
            out["unchanged"] = True
            return out
        created = _insert_live(conn, business_id=business_id,
                               storefront_id=sf.get("storefront_id"),
                               snapshot=snapshot, note=note,
                               actor=actor_user_id)
        _svc._audit(conn, business_id=business_id, subject_type="storefront",
                    subject_ref=created["version_id"],
                    action="storefront.publish_version", actor=actor_user_id,
                    before={"version_no": created.pop("prev_version_no")},
                    after={"version_no": created["version_no"]})
        conn.commit()
        created["unchanged"] = False
        return created
    finally:
        conn.close()


def restore_version(business_id: str, actor_user_id: Any, version_id: str, *,
                    note: Optional[str] = None,
                    context: Optional[dict] = None) -> dict:
    """Make an old snapshot live again — as a NEW version (history is
    append-only), with the draft row rewritten to match so publish-after-restore
    is a no-op rather than a silent revert."""
    _svc._require_enabled()
    _svc._require_not_held(context)
    note = _svc._clean_str(note, field="note", max_len=NOTE_MAX)
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id,
                                     "store.publish")
        sf = _svc._get_storefront_row(conn, business_id)
        if sf is None:
            raise StoreError("Storefront not found.", 404, "not_found")
        _svc._require_seller_approved(conn, business_id, "storefront")
        src = _svc._row(conn.execute(
            "SELECT * FROM business_os_store_storefront_versions "
            "WHERE version_id = ? AND business_id = ?",
            (str(version_id), _svc._sid(business_id))).fetchone())
        if src is None:
            raise StoreError("Version not found.", 404, "not_found")
        if src.get("status") == "published":
            raise StoreError("That version is already live.", 409, "conflict")
        try:
            snapshot = json.loads(src["snapshot_json"])
        except Exception:
            raise StoreError("That version's snapshot is unreadable.",
                             409, "conflict")

        # The restored slug must still be free (unique index is global).
        slug = snapshot.get("slug")
        if slug is not None:
            clash = _svc._row(conn.execute(
                "SELECT business_id FROM business_os_store_storefront "
                "WHERE slug = ?", (slug,)).fetchone())
            if clash and _svc._sid(clash["business_id"]) != _svc._sid(business_id):
                raise StoreError(
                    "That version's slug has since been taken.", 409, "conflict")

        created = _insert_live(conn, business_id=business_id,
                               storefront_id=sf.get("storefront_id"),
                               snapshot=snapshot, note=note,
                               actor=actor_user_id)
        now = _svc._now_iso()
        theme = snapshot.get("theme")
        conn.execute(
            "UPDATE business_os_store_storefront SET name = ?, slug = ?, "
            "headline = ?, about = ?, theme_json = ?, currency = ?, "
            "updated_at = ? WHERE business_id = ?",
            (snapshot.get("name"), slug, snapshot.get("headline"),
             snapshot.get("about"),
             None if theme is None else json.dumps(theme, sort_keys=True),
             snapshot.get("currency"), now, _svc._sid(business_id)))
        _svc._audit(conn, business_id=business_id, subject_type="storefront",
                    subject_ref=created["version_id"],
                    action="storefront.restore_version", actor=actor_user_id,
                    before={"version_no": created.pop("prev_version_no")},
                    after={"version_no": created["version_no"],
                           "restored_from": src["version_no"]})
        conn.commit()
        created["restored_from"] = src["version_no"]
        return created
    finally:
        conn.close()


# --- reads -------------------------------------------------------------------
def list_versions(business_id: str, actor_user_id: Any, *,
                  limit: int = 50) -> list:
    _svc._require_enabled()
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id,
                                     "store.read")
        try:
            lim = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            lim = 50
        rows = conn.execute(
            "SELECT * FROM business_os_store_storefront_versions "
            "WHERE business_id = ? ORDER BY version_no DESC LIMIT ?",
            (_svc._sid(business_id), lim)).fetchall()
        return [_version_public(r) for r in _svc._rows(rows)]
    finally:
        conn.close()


def get_version(business_id: str, actor_user_id: Any, version_id: str) -> dict:
    _svc._require_enabled()
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id,
                                     "store.read")
        row = _svc._row(conn.execute(
            "SELECT * FROM business_os_store_storefront_versions "
            "WHERE version_id = ? AND business_id = ?",
            (str(version_id), _svc._sid(business_id))).fetchone())
        if row is None:
            raise StoreError("Version not found.", 404, "not_found")
        return _version_public(row, with_snapshot=True)
    finally:
        conn.close()


def draft_status(business_id: str, actor_user_id: Any) -> dict:
    """"Do I have unpublished changes?" — named fields, not a shrug. With no
    version yet, everything is unpublished and ``live_version_no`` is None."""
    _svc._require_enabled()
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id,
                                     "store.read")
        sf = _svc._get_storefront_row(conn, business_id)
        if sf is None:
            raise StoreError("Storefront not found.", 404, "not_found")
        draft = _snapshot_of(sf)
        live = _live_row(conn, business_id)
        if live is None:
            changed = [f for f in SNAPSHOT_FIELDS if draft.get(f) is not None]
            return {"live_version_no": None, "dirty": True,
                    "changed_fields": changed}
        try:
            live_snap = json.loads(live["snapshot_json"])
        except Exception:
            live_snap = {}
        changed = [f for f in SNAPSHOT_FIELDS
                   if _canon({f: draft.get(f)}) != _canon({f: live_snap.get(f)})]
        return {"live_version_no": live["version_no"],
                "dirty": bool(changed), "changed_fields": changed}
    finally:
        conn.close()


def published_storefront(business_id: str) -> Optional[dict]:
    """The shopper read. Serves the live SNAPSHOT, never the draft row; still
    honors the lifecycle status (suspended shops are dark) and re-checks seller
    approval on every read, exactly like ``service.public_storefront``. Returns
    None when there is nothing published to show — an unversioned shop is an
    unpublished shop on this surface."""
    _svc._require_enabled()
    conn = db.connect()
    try:
        sf = _svc._get_storefront_row(conn, business_id)
        if sf is None or str(sf.get("status")) != "published":
            return None
        if _svc._seller_status(conn, business_id)[0] != "approved":
            return None
        live = _live_row(conn, business_id)
        if live is None:
            return None
        try:
            snapshot = json.loads(live["snapshot_json"])
        except Exception:
            return None
        rows = conn.execute(
            "SELECT * FROM business_os_store_products "
            "WHERE business_id = ? AND status = 'active' "
            "ORDER BY position ASC, created_at ASC",
            (_svc._sid(business_id),)).fetchall()
        out = dict(snapshot)
        out.update({"storefront_id": sf.get("storefront_id"),
                    "business_id": sf.get("business_id"),
                    "version_no": live["version_no"],
                    "published_at": live["created_at"],
                    "products": [_svc._product_public(r)
                                 for r in _svc._rows(rows)]})
        return out
    finally:
        conn.close()
