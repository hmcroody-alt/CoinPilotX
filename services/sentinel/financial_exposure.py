"""Sentinel Mission 5 — financial exposure model (Stage 20).

Exposure classes are kept structurally apart: CONFIRMED, POTENTIAL,
DISPUTED, and UNKNOWN live in separate columns/fields, and nothing in this
module (or the schema) can sum POTENTIAL into CONFIRMED. Reporting potential
losses as confirmed losses is lying, and lying is a bug.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from services.sentinel import store
from services.sentinel.constitution import CONSTITUTION_VERSION

EXPOSURE_CLASSES = ("CONFIRMED", "POTENTIAL", "DISPUTED", "UNKNOWN")


class ExposureError(ValueError):
    pass


def estimate(basis_items: List[Dict[str, Any]],
             *, currency: str = "usd") -> Dict[str, Any]:
    """Aggregate basis items into a class-separated exposure estimate.

    Each basis item: {exposure_class, amount_cents (int, optional for
    UNKNOWN), ref, note}. Items with an unknown class or CONFIRMED items
    without a concrete basis ref are REJECTED — confirmed loss requires
    confirmed evidence (FIN-015).
    """
    confirmed = potential = disputed = 0
    unknown_items = 0
    basis: List[Dict[str, Any]] = []
    for item in basis_items or []:
        cls = str(item.get("exposure_class") or "").upper()
        if cls not in EXPOSURE_CLASSES:
            raise ExposureError(f"unknown exposure class {cls!r}")
        ref = str(item.get("ref") or "").strip()
        amount = item.get("amount_cents")
        if cls == "UNKNOWN":
            unknown_items += 1
            basis.append({"exposure_class": cls, "ref": ref,
                          "note": str(item.get("note") or "")})
            continue
        if amount is None:
            raise ExposureError(f"{cls} basis item requires amount_cents")
        amount = int(amount)
        if amount < 0:
            raise ExposureError("amount_cents must be >= 0")
        if cls == "CONFIRMED":
            if not ref:
                raise ExposureError(
                    "CONFIRMED exposure requires a concrete basis ref — "
                    "confirmed loss without confirmed evidence is forbidden")
            confirmed += amount
        elif cls == "POTENTIAL":
            potential += amount
        elif cls == "DISPUTED":
            disputed += amount
        basis.append({"exposure_class": cls, "amount_cents": amount,
                      "ref": ref, "note": str(item.get("note") or "")})
    return {
        "currency": str(currency or "usd").lower(),
        "confirmed_cents": confirmed,
        "potential_cents": potential,
        "disputed_cents": disputed,
        "unknown_items": unknown_items,
        "basis": basis,
        "note": ("classes are never summed together — potential is not "
                 "confirmed, and unknown is counted, not priced"),
    }


def record(incident_key: str, estimate_result: Dict[str, Any],
           conn=None) -> int:
    """Persist an exposure estimate for an incident."""
    if not str(incident_key or "").strip():
        raise ExposureError("incident_key is required")
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            """INSERT INTO sentinel_financial_exposure
               (incident_key, currency, confirmed_cents, potential_cents,
                disputed_cents, unknown_items, basis_json, policy_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (incident_key,
             str(estimate_result.get("currency") or "usd"),
             int(estimate_result.get("confirmed_cents") or 0),
             int(estimate_result.get("potential_cents") or 0),
             int(estimate_result.get("disputed_cents") or 0),
             int(estimate_result.get("unknown_items") or 0),
             json.dumps(estimate_result.get("basis") or [], default=str),
             CONSTITUTION_VERSION))
        return int(cur.lastrowid)


def for_incident(incident_key: str, conn=None) -> Optional[Dict[str, Any]]:
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT currency, confirmed_cents, potential_cents, disputed_cents, "
            "unknown_items, basis_json, recorded_at "
            "FROM sentinel_financial_exposure WHERE incident_key = ? "
            "ORDER BY id DESC LIMIT 1", (incident_key,))
        row = cur.fetchone()
    if not row:
        return None
    return {"incident_key": incident_key, "currency": row[0],
            "confirmed_cents": int(row[1]), "potential_cents": int(row[2]),
            "disputed_cents": int(row[3]), "unknown_items": int(row[4]),
            "basis": json.loads(row[5] or "[]"), "recorded_at": row[6]}


def totals(conn=None) -> Dict[str, Any]:
    """Platform-wide exposure totals, classes still separate. Uses only the
    LATEST estimate per incident (re-estimates supersede, never add)."""
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT e.confirmed_cents, e.potential_cents, e.disputed_cents, "
            "e.unknown_items FROM sentinel_financial_exposure e "
            "WHERE e.id = (SELECT MAX(id) FROM sentinel_financial_exposure "
            "              WHERE incident_key = e.incident_key)")
        rows = cur.fetchall()
    return {
        "confirmed_cents": sum(int(r[0]) for r in rows),
        "potential_cents": sum(int(r[1]) for r in rows),
        "disputed_cents": sum(int(r[2]) for r in rows),
        "unknown_items": sum(int(r[3]) for r in rows),
        "incidents_with_estimates": len(rows),
        "note": "potential is never reported as confirmed",
    }
