"""One-shot repair for supplier stock that never landed, and the bindings it blocked.

This is deliberately **not** a second reconciler. Everything that decides what a
listing becomes already exists and is correct: ``worker._read_job`` asks the
adapter, ``revisions.plan_stock_revision`` decides the new state, and
``revisions.apply_supplier_read`` writes it. What never existed is a way to make
that pipeline run over a backlog *once*, on purpose, with the result inspected
before it is committed.

The gap this closes is operational, not algorithmic. ``supplier_worker.run_tick``
is gated on ``CJ_RECONCILIATION_ENABLED`` and no deployed service runs it, so in
production ``business_os_supplier_sync_jobs`` holds zero rows and
``marketplace_product_sources.last_synced_at`` is NULL on every row. A transient
lease collision at import time voided inventory for 683 of 719 variants, and
because the repair path was never switched on, that transient failure became
permanent. Turning the worker on fixes the *next* import; this fixes the ones
already on the shelf.

Four modes, in increasing order of consequence:

``audit``
    Database only. No network call, no write. Answers "what is actually wrong"
    from stored state alone, which is the only mode safe to run anywhere.
``dry-run``
    Reads the supplier, plans every change through the same pure planner the
    writer uses, and writes nothing. The plan it prints is the plan ``apply``
    would execute, because it is produced by the same function.
``canary``
    ``apply``, bounded to ``limit`` products, checkpointed.
``apply``
    The whole backlog, checkpointed.

Every mutating mode writes a restore checkpoint *before* touching anything, and
:func:`restore` puts it back. The checkpoint records stock, sync provenance, the
binding and the listing's unit count — the exact four things this module writes
and nothing else, so restoring cannot revert an unrelated edit made in between.

No provider payload, credential or cost reaches the returned report. The report
is designed to be pasted into a mission document, and a report you cannot paste
is a report nobody reads.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Optional

from services import db
from services import marketplace_variants as variants
from services.marketplace_supplier_schema import (
    MODE_DROPSHIP,
    STOCK_IN_STOCK,
    STOCK_OUT_OF_STOCK,
    STOCK_UNKNOWN,
)

AUDIT = "audit"
DRY_RUN = "dry-run"
CANARY = "canary"
APPLY = "apply"
MODES = (AUDIT, DRY_RUN, CANARY, APPLY)

WRITING_MODES = frozenset({CANARY, APPLY})
READING_MODES = frozenset({DRY_RUN, CANARY, APPLY})

# A pid this integration could not have imported. CJ product ids are numeric
# strings; three production rows carry UUID-shaped ids from an earlier import
# path. They are reported rather than attempted, because sending one to
# `get_inventory` spends a call from a quota shared with live fulfilment to
# learn something the id's own shape already said.
def _cj_shaped(pid: Any) -> bool:
    return str(pid or "").strip().isdigit()


# ---------------------------------------------------------------------------
# Reading the backlog
# ---------------------------------------------------------------------------


def _rows(cur) -> list[dict]:
    return [dict(row) for row in cur.fetchall()]


def targets(conn, *, connection_id: Optional[str] = None,
            provider: str = "cj") -> list[dict]:
    """Every supplier-backed listing this repair could touch, with its variants.

    Scoped the same way ``worker._seed_jobs`` scopes its seeding — a source row
    with no ``supplier_connection_id`` has no connection to sync against — so a
    product this returns is a product the ordinary reconciler would also pick
    up. A repair that reached further than the thing it is standing in for would
    be doing something nobody could later verify by turning the worker on.
    """
    where = ["s.supplier_connection_id IS NOT NULL", "s.business_id IS NOT NULL",
             "s.store_id IS NOT NULL", "LOWER(s.provider)=?"]
    args: list[Any] = [str(provider or "").strip().lower()]
    if connection_id:
        where.append("s.supplier_connection_id=?")
        args.append(str(connection_id))
    cur = conn.cursor()
    cur.execute(
        f"SELECT s.* FROM {variants.SOURCE_TABLE} s "
        f"WHERE {' AND '.join(where)} ORDER BY s.listing_id",
        tuple(args))
    sources = _rows(cur)
    if not sources:
        return []

    listing_ids = [int(s["listing_id"]) for s in sources]
    marks = ",".join("?" for _ in listing_ids)
    cur.execute(
        f"SELECT * FROM {variants.VARIANT_TABLE} WHERE listing_id IN ({marks}) "
        f"ORDER BY listing_id, position, id", tuple(listing_ids))
    by_listing: dict[int, list[dict]] = {}
    for row in _rows(cur):
        by_listing.setdefault(int(row["listing_id"]), []).append(row)

    cur.execute(
        f"SELECT id, title, status, quantity, price_label FROM marketplace_listings "
        f"WHERE id IN ({marks})", tuple(listing_ids))
    listings = {int(row["id"]): row for row in _rows(cur)}

    out = []
    for source in sources:
        listing_id = int(source["listing_id"])
        out.append({"source": source,
                    "listing": listings.get(listing_id) or {},
                    "variants": by_listing.get(listing_id, [])})
    return out


def census(*, connection_id: Optional[str] = None, conn=None) -> dict:
    """What is wrong right now, from stored state alone. No network, no write."""
    owned = conn is None
    conn = conn or db.connect()
    try:
        found = targets(conn, connection_id=connection_id)
    finally:
        if owned:
            conn.close()

    report = {"sources": len(found), "variants": 0, "unknown_stock": 0,
              "in_stock": 0, "out_of_stock": 0, "unbound": 0, "never_synced": 0,
              "unreachable_pid": 0, "listings": []}
    for item in found:
        source, rows = item["source"], item["variants"]
        unknown = sum(1 for r in rows if _state(r) == STOCK_UNKNOWN)
        in_stock = sum(1 for r in rows if _state(r) == STOCK_IN_STOCK)
        out = sum(1 for r in rows if _state(r) == STOCK_OUT_OF_STOCK)
        unbound = source.get("provider_variant_id") in (None, "")
        reachable = _cj_shaped(source.get("provider_product_id"))
        report["variants"] += len(rows)
        report["unknown_stock"] += unknown
        report["in_stock"] += in_stock
        report["out_of_stock"] += out
        report["unbound"] += 1 if unbound else 0
        report["never_synced"] += 1 if not source.get("last_synced_at") else 0
        report["unreachable_pid"] += 0 if reachable else 1
        report["listings"].append({
            "listing_id": int(source["listing_id"]),
            "title": str(item["listing"].get("title") or "")[:60],
            "status": item["listing"].get("status"),
            "provider_product_id": source.get("provider_product_id"),
            "pid_reachable": reachable,
            "variants": len(rows),
            "unknown": unknown, "in_stock": in_stock, "out_of_stock": out,
            "bound": not unbound,
            "sync_state": source.get("sync_state"),
            "last_synced_at": source.get("last_synced_at"),
            "last_sync_error": source.get("last_sync_error"),
        })
    return report


def _state(row) -> str:
    return str(row.get("stock_state") or STOCK_UNKNOWN).strip().upper()


# ---------------------------------------------------------------------------
# Planning — pure, and the same arithmetic the writer performs
# ---------------------------------------------------------------------------


def plan_product(source: dict, rows: list, readings: dict) -> dict:
    """What one supplier read would change, without changing it.

    Every per-variant decision is delegated to
    ``revisions.plan_stock_revision``. That function is the writer's own
    planner, so this cannot forecast a repair the writer would not perform —
    which is the entire value of a dry run, and the property a re-implementation
    here would quietly destroy.

    A variant the read did not mention is reported as ``unmentioned`` rather
    than as a change, matching ``revisions._apply_stock``: providers routinely
    return only the warehouses holding rows, so a short list is a short list and
    not a sell-out.
    """
    from . import revisions

    changes, unmentioned = [], 0
    for row in rows:
        reference = str(row.get("provider_variant_id") or "").strip()
        reading = readings.get(reference) if reference else None
        if reading is None:
            unmentioned += 1
            continue
        observed_state, observed_quantity = reading
        decided = revisions.plan_stock_revision(observed_state=observed_state,
                                                observed_quantity=observed_quantity)
        before_state = _state(row)
        before_units = revisions._stored_units(row)
        after_units = decided["units"] if decided["write_quantity"] else before_units
        if (decided["stock_state"] == before_state and after_units == before_units):
            continue
        changes.append({
            "variant_id": int(row["id"]),
            "provider_variant_id": reference,
            "stock_state": {"before": before_state, "after": decided["stock_state"]},
            "units": {"before": before_units, "after": after_units},
            "attention": decided["attention"],
        })
    return {"listing_id": int(source["listing_id"]), "changes": changes,
            "unmentioned": unmentioned,
            "binding": resolve_binding(source, rows, readings)}


def resolve_binding(source: dict, rows: list, readings: dict) -> Optional[str]:
    """The variant this listing would become bindable to, or ``None``.

    The tie ``importer._sole_orderable`` refused to break at import time is not
    a permanent property of the listing — it was a consequence of every variant
    being ``UNKNOWN``, which is exactly what this repair fixes. So the same
    question is asked again against what the supplier says now, using the same
    function, so a repair cannot bind something an import would have left alone.

    Returns ``None`` for an already-bound listing. ``link_source`` would refuse
    to re-point one anyway, and asking it to is how a repair script turns into a
    thing that can substitute a buyer's colour.
    """
    from . import importer, normalize

    if source.get("provider_variant_id") not in (None, ""):
        return None
    chosen = []
    for row in rows:
        reference = str(row.get("provider_variant_id") or "").strip()
        if not reference:
            continue
        reading = readings.get(reference)
        if reading is None:
            state, quantity = _state(row), row.get("stock_quantity")
        else:
            state, quantity = reading
        chosen.append({"external_variant_id": reference,
                       "stock_state": normalize.storage_stock_state(state),
                       "stock_quantity": quantity})
    if not chosen:
        return None
    return importer._sole_orderable(chosen)


# ---------------------------------------------------------------------------
# Checkpoint and restore
# ---------------------------------------------------------------------------


def checkpoint(found: list) -> dict:
    """Exactly the four things this module writes, recorded so they can be put back.

    Deliberately not a table dump. A checkpoint wide enough to restore anything
    is wide enough to revert a merchant edit made between the repair and the
    rollback, and the merchant would have no way to know it happened.
    """
    payload = {"created_at": time.time(), "sources": [], "variants": [],
               "listings": []}
    for item in found:
        source, listing = item["source"], item["listing"]
        payload["sources"].append({
            "id": int(source["id"]),
            "listing_id": int(source["listing_id"]),
            "seller_user_id": int(source["seller_user_id"]),
            "provider_variant_id": source.get("provider_variant_id"),
            "sync_state": source.get("sync_state"),
            "last_synced_at": source.get("last_synced_at"),
            "last_sync_error": source.get("last_sync_error"),
            "attention_json": source.get("attention_json"),
        })
        if listing:
            payload["listings"].append({"id": int(listing["id"]),
                                        "quantity": listing.get("quantity")})
        for row in item["variants"]:
            payload["variants"].append({
                "id": int(row["id"]),
                "seller_user_id": int(row["seller_user_id"]),
                "stock_state": row.get("stock_state"),
                "stock_quantity": row.get("stock_quantity"),
                "stock_synced_at": row.get("stock_synced_at"),
            })
    return payload


def restore(payload: dict, *, conn=None) -> dict:
    """Put a checkpoint back. Idempotent; safe to run twice."""
    owned = conn is None
    conn = conn or db.connect()
    counts = {"variants": 0, "sources": 0, "listings": 0}
    try:
        cur = conn.cursor()
        for row in payload.get("variants") or ():
            cur.execute(
                f"UPDATE {variants.VARIANT_TABLE} SET stock_state=?, stock_quantity=?, "
                f"stock_synced_at=? WHERE id=? AND seller_user_id=?",
                (row["stock_state"], row["stock_quantity"], row["stock_synced_at"],
                 int(row["id"]), int(row["seller_user_id"])))
            counts["variants"] += 1
        for row in payload.get("sources") or ():
            cur.execute(
                f"UPDATE {variants.SOURCE_TABLE} SET provider_variant_id=?, sync_state=?, "
                f"last_synced_at=?, last_sync_error=?, attention_json=? "
                f"WHERE id=? AND seller_user_id=?",
                (row["provider_variant_id"], row["sync_state"], row["last_synced_at"],
                 row["last_sync_error"], row["attention_json"],
                 int(row["id"]), int(row["seller_user_id"])))
            counts["sources"] += 1
        for row in payload.get("listings") or ():
            cur.execute("UPDATE marketplace_listings SET quantity=? WHERE id=?",
                        (row["quantity"], int(row["id"])))
            counts["listings"] += 1
        conn.commit()
    finally:
        if owned:
            conn.close()
    return counts


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _read_inventory(adapter, pid: str):
    """``(payload, readings, error)``. The error is a code, never a provider body.

    The untouched payload is carried alongside the normalized readings because
    the writer normalizes it *again* for itself. Handing ``apply_supplier_read``
    a dict rebuilt from the readings would mean the repair wrote through a
    payload shape no provider ever sends, and the one thing a dry run has to
    guarantee is that what it planned is what the writer will see.
    """
    from . import normalize
    from .errors import SupplierError

    try:
        payload = adapter.get_inventory(pid)
    except SupplierError as exc:
        return None, None, str(getattr(exc, "code", None) or exc)[:120]
    except Exception as exc:  # pragma: no cover - provider shapes vary
        return None, None, type(exc).__name__[:120]
    try:
        return payload, normalize.inventory("cj", payload) or {}, None
    except Exception as exc:
        return None, None, f"normalize:{type(exc).__name__}"[:120]


def run(*, mode: str, connection_id: Optional[str] = None,
        limit: Optional[int] = None,
        checkpoint_path: Optional[str] = None,
        adapter_factory: Optional[Callable] = None,
        now: Optional[float] = None) -> dict:
    """Repair the stock backlog, at the consequence level ``mode`` names.

    ``checkpoint_path`` is written, and flushed to the platter, *before* the
    first write — not returned for a caller to save afterwards. A checkpoint
    that only exists once the run finishes is precisely no use in the one
    situation a checkpoint is for, which is a run that did not.

    ``adapter_factory`` takes ``(connection_id, business_id, store_id)`` and
    returns something with ``get_inventory``. Its only production value is
    ``None``, which resolves the real connection; it exists so the tests can
    drive every branch of this function without a credential or a network.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    now = time.time() if now is None else now

    if mode == AUDIT:
        return {"mode": mode, "census": census(connection_id=connection_id)}

    from . import policy
    policy.require_enabled()
    policy.require_network()

    conn = db.connect()
    try:
        found = targets(conn, connection_id=connection_id)
    finally:
        conn.close()

    before = census(connection_id=connection_id)
    reachable = [i for i in found if _cj_shaped(i["source"].get("provider_product_id"))]
    skipped = [{"listing_id": int(i["source"]["listing_id"]),
                "provider_product_id": i["source"].get("provider_product_id"),
                "reason": "provider_product_id_not_cj_shaped"}
               for i in found if i not in reachable]
    if limit is not None:
        reachable = reachable[:max(0, int(limit))]

    saved = None
    if mode in WRITING_MODES:
        saved = checkpoint(reachable)
        if checkpoint_path:
            with open(checkpoint_path, "w", encoding="utf-8") as handle:
                json.dump(saved, handle, indent=2, default=str)
                handle.flush()
                os.fsync(handle.fileno())

    adapters: dict[tuple, Any] = {}
    planned, applied, blocked = [], [], []
    bound_now = []

    for item in reachable:
        source = item["source"]
        scope = (source["supplier_connection_id"], source["business_id"],
                 source["store_id"])
        adapter = adapters.get(scope)
        if adapter is None:
            try:
                adapter = (adapter_factory(*scope) if adapter_factory
                           else _live_adapter(*scope))
            except Exception as exc:
                blocked.append({"listing_id": int(source["listing_id"]),
                                "reason": f"connection:{type(exc).__name__}"})
                continue
            adapters[scope] = adapter

        payload, readings, error = _read_inventory(adapter,
                                                   source["provider_product_id"])
        if error is not None:
            blocked.append({"listing_id": int(source["listing_id"]),
                            "reason": error})
            continue

        plan = plan_product(source, item["variants"], readings)
        planned.append(plan)
        if mode not in WRITING_MODES:
            continue

        written = _apply_one(source, payload, now=now)
        applied.append({"listing_id": plan["listing_id"], **written})
        vid = plan["binding"]
        if vid and _bind(source, vid):
            bound_now.append({"listing_id": plan["listing_id"],
                              "provider_variant_id": vid})

    report = {
        "mode": mode,
        "now": now,
        "before": before,
        "considered": len(reachable),
        "skipped": skipped,
        "planned": planned,
        "blocked": blocked,
        "variants_changed": sum(len(p["changes"]) for p in planned),
        "bindings_resolvable": sum(1 for p in planned if p["binding"]),
    }
    if mode in WRITING_MODES:
        report["applied"] = applied
        report["bound"] = bound_now
        report["checkpoint"] = saved
        report["after"] = census(connection_id=connection_id)
    return report


def _live_adapter(connection_id, business_id, store_id):
    from . import connections
    adapter = connections.worker_adapter(connection_id, business_id, store_id)
    # Background reads are what the quota controller de-prioritises. A repair is
    # by definition not urgent, and must never take a slot from a buyer's order.
    adapter.background = True
    return adapter


def _apply_one(source, payload, *, now) -> dict:
    """Hand one read to the reconciler that already owns writing it."""
    from . import revisions

    return revisions.apply_supplier_read(
        connection_id=source["supplier_connection_id"],
        business_id=source["business_id"], store_id=source["store_id"],
        kind="inventory", resource_id=source["provider_product_id"],
        payload=payload, now=now)


def _bind(source, provider_variant_id: str) -> bool:
    """Fill an empty binding. Can never re-point a full one — ``link_source`` refuses."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        variants.link_source(
            cur, listing_id=int(source["listing_id"]),
            seller_user_id=int(source["seller_user_id"]),
            provider=str(source["provider"]),
            provider_product_id=str(source["provider_product_id"]),
            fulfillment_mode=str(source.get("fulfillment_mode")
                                 or MODE_DROPSHIP),
            provider_variant_id=provider_variant_id,
            supplier_connection_id=source["supplier_connection_id"],
            business_id=source["business_id"], store_id=source["store_id"])
        conn.commit()
        return True
    except variants.VariantRejected:
        conn.rollback()
        return False
    finally:
        conn.close()


def summarize(report: dict) -> str:
    """The report as a human reads it. No cost, no credential, no provider body."""
    lines = [f"mode: {report['mode']}"]
    if report["mode"] == AUDIT:
        c = report["census"]
        lines += [
            f"  sources               {c['sources']}",
            f"  variants              {c['variants']}",
            f"  stock UNKNOWN         {c['unknown_stock']}",
            f"  stock IN_STOCK        {c['in_stock']}",
            f"  stock OUT_OF_STOCK    {c['out_of_stock']}",
            f"  unbound sources       {c['unbound']}",
            f"  never synced          {c['never_synced']}",
            f"  unreachable pid       {c['unreachable_pid']}",
        ]
        return "\n".join(lines)

    before = report["before"]
    lines += [
        f"  considered            {report['considered']}",
        f"  skipped (bad pid)     {len(report['skipped'])}",
        f"  blocked               {len(report['blocked'])}",
        f"  variants changed      {report['variants_changed']}",
        f"  bindings resolvable   {report['bindings_resolvable']}",
        f"  UNKNOWN before        {before['unknown_stock']}",
    ]
    after = report.get("after")
    if after:
        lines += [
            f"  UNKNOWN after         {after['unknown_stock']}",
            f"  IN_STOCK after        {after['in_stock']}",
            f"  unbound after         {after['unbound']}",
            f"  bound this run        {len(report.get('bound') or [])}",
        ]
    for entry in report["blocked"]:
        lines.append(f"  BLOCKED listing {entry['listing_id']}: {entry['reason']}")
    for entry in report["skipped"]:
        lines.append(f"  SKIPPED listing {entry['listing_id']}: {entry['reason']}")
    return "\n".join(lines)


def dumps(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True, default=str)
