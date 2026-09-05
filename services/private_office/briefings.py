"""Private Briefings — the Office's own engine, every line cited.

What this module is
-------------------
An on-demand, deterministic composition of what already needs the member's
attention: open obligations (overdue first), open risks, requests, pending
decisions, opportunities, document claims awaiting review, people with open
commitments, and the newest recorded facts. Each briefing is persisted with
its items so "what did my Office tell me on Tuesday" has an answer, and every
item carries evidence refs back to the rows it quotes — the Ask Why path is
:func:`evidence.resolve_refs` over exactly those refs.

What this module is *not*
-------------------------
* Not the Pulse Briefings engine. The earlier plan — a Private Office fact
  provider inside the shared engine — would have changed every existing
  briefing fingerprint and paged users on the first cycle. This engine is
  member-triggered, schedules nothing, and pushes nothing.
* Not an inference layer. Nothing here summarises, scores, or guesses. A
  briefing with zero items means the composed reads found nothing open — a
  true statement, rendered as one.

Writes: this module owns ``private_office_briefings`` and
``private_office_briefing_items`` (registered in ``evidence.KINDS``), the same
way the vault owns its document tables. Actions created *from* a briefing go
through ``records.create_record`` — the canonical writer — citing the briefing
they came from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.private_office import audit
from services.private_office import documents as documents_mod
from services.private_office import evidence
from services.private_office import facts as facts_mod
from services.private_office import jobs
from services.private_office import records as records_mod
from services.private_office import relationships as relationships_mod

BRIEFINGS_TABLE = "private_office_briefings"
ITEMS_TABLE = "private_office_briefing_items"

SECTION_OBLIGATIONS = "obligations"
SECTION_RISKS = "risks"
SECTION_REQUESTS = "requests"
SECTION_DECISIONS = "decisions"
SECTION_OPPORTUNITIES = "opportunities"
SECTION_CLAIMS = "claims_pending"
SECTION_PEOPLE = "people"
SECTION_RECENT = "recent_facts"

#: Render order. Fixed so two briefings generated a minute apart differ only
#: where the data differs — a stable shape is what makes the diff legible.
SECTIONS: tuple[str, ...] = (
    SECTION_OBLIGATIONS, SECTION_RISKS, SECTION_REQUESTS, SECTION_DECISIONS,
    SECTION_OPPORTUNITIES, SECTION_CLAIMS, SECTION_PEOPLE, SECTION_RECENT,
)

MAX_ITEMS_PER_SECTION = 8
MAX_LABEL_CHARS = 160
MAX_BRIEFINGS = 50

#: The record primitives a briefing action may create. Obligations and
#: requests are the two "do something about this" shapes; letting a briefing
#: mint risks or decisions would be the engine asserting judgments.
ACTION_TYPES: dict[str, str] = {
    "obligation": records_mod.TYPE_OBLIGATION,
    "request": records_mod.TYPE_REQUEST,
}

LIFECYCLE_ACTIVE = "ACTIVE"

BRIEFINGS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {BRIEFINGS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    generated_at TEXT NOT NULL,
    job_id INTEGER NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    evidence_refs TEXT NOT NULL DEFAULT '',
    lifecycle_state TEXT NOT NULL DEFAULT '{LIFECYCLE_ACTIVE}',
    created_at TEXT NOT NULL
)
"""

ITEMS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {ITEMS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    briefing_id INTEGER NOT NULL,
    section TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    label TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    evidence_refs TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
"""

INDEX_DDL: tuple[str, ...] = (
    f"CREATE INDEX IF NOT EXISTS idx_po_briefings_owner "
    f"ON {BRIEFINGS_TABLE} (owner_user_id, lifecycle_state, id)",
    f"CREATE INDEX IF NOT EXISTS idx_po_briefing_items_owner "
    f"ON {ITEMS_TABLE} (owner_user_id, briefing_id, section, position)",
)

_SCHEMA_READY = False


class PrivateBriefingRejected(ValueError):
    """A briefing request this module refuses."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reset_briefings_schema_cache() -> None:
    global _SCHEMA_READY
    _SCHEMA_READY = False


def ensure_briefings_schema(cur, *, force: bool = False) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return
    cur.execute(BRIEFINGS_TABLE_DDL)
    cur.execute(ITEMS_TABLE_DDL)
    for ddl in INDEX_DDL:
        cur.execute(ddl)
    # Generation runs under a job record, so this schema is not ready until
    # the jobs table is too.
    jobs.ensure_jobs_schema(cur)
    _SCHEMA_READY = True


# ---------------------------------------------------------------------------
# Composition — reads only, through each store's own gated readers
# ---------------------------------------------------------------------------

def _clip(text: object) -> str:
    return " ".join(str(text or "").split())[:MAX_LABEL_CHARS]


def _open_records(cur, owner: int, record_type: str) -> list[dict]:
    spec = records_mod.SPECS[record_type]
    open_statuses = [s for s in spec["statuses"] if s not in spec["closing"]]
    return records_mod.list_records(
        cur, record_type=record_type, owner_user_id=owner,
        statuses=open_statuses, limit=records_mod.MAX_LIMIT)


def _due_detail(due_at: object, now_iso: str) -> str:
    due = str(due_at or "").strip()
    if not due:
        return "no due date"
    return f"overdue since {due}" if due < now_iso else f"due {due}"


def _record_items(cur, owner: int, record_type: str, kind: str,
                  now_iso: str) -> list[dict]:
    rows = _open_records(cur, owner, record_type)
    if record_type == records_mod.TYPE_OBLIGATION:
        # Overdue first, then nearest due date; undated last. Deterministic —
        # the same rows always brief in the same order.
        rows.sort(key=lambda r: (not str(r.get("due_at") or ""),
                                 str(r.get("due_at") or ""), -int(r["id"])))
    items = []
    for row in rows[:MAX_ITEMS_PER_SECTION]:
        label = _clip(row.get("title") or row.get("question")
                      or row.get("summary") or kind)
        detail = (_due_detail(row.get("due_at"), now_iso)
                  if record_type == records_mod.TYPE_OBLIGATION
                  else _clip(row.get("status") or ""))
        items.append({"label": label, "detail": detail,
                      "refs": [evidence.format_ref(kind, int(row["id"]))]})
    return items


def _claim_items(cur, owner: int) -> list[dict]:
    claims = documents_mod.list_claims(
        cur, owner_user_id=owner, status=documents_mod.CLAIM_PROPOSED)
    items = []
    for claim in claims[:MAX_ITEMS_PER_SECTION]:
        items.append({
            "label": _clip(f"{claim.get('fact_type')}: {claim.get('proposed_value')}"),
            "detail": "awaiting your review",
            "refs": [evidence.format_ref("document", int(claim.get("document_id") or 0))],
        })
    return items


def _people_items(cur, owner: int) -> list[dict]:
    people = relationships_mod.directory(cur, owner_user_id=owner)
    busy = [p for p in people if int(p.get("open_commitments") or 0) > 0]
    items = []
    for person in busy[:MAX_ITEMS_PER_SECTION]:
        count = int(person["open_commitments"])
        items.append({
            "label": _clip(person.get("name") or "Unnamed person"),
            "detail": f"{count} open commitment{'s' if count != 1 else ''}",
            "refs": [person["ref"]],
        })
    return items


def _recent_fact_items(cur, owner: int) -> list[dict]:
    rows = facts_mod.list_facts(cur, owner_user_id=owner, limit=MAX_ITEMS_PER_SECTION)
    items = []
    for row in rows:
        items.append({
            "label": _clip(f"{row.get('fact_type')}: {row.get('typed_value')}"),
            "detail": _clip(row.get("provenance_type") or ""),
            "refs": [evidence.format_ref("fact", int(row["id"]))],
        })
    return items


def _compose(cur, owner: int) -> list[dict]:
    """Every item of every section, in render order, each line cited."""
    now_iso = _now_iso()
    sections: list[tuple[str, list[dict]]] = [
        (SECTION_OBLIGATIONS, _record_items(
            cur, owner, records_mod.TYPE_OBLIGATION, "obligation", now_iso)),
        (SECTION_RISKS, _record_items(
            cur, owner, records_mod.TYPE_RISK, "risk", now_iso)),
        (SECTION_REQUESTS, _record_items(
            cur, owner, records_mod.TYPE_REQUEST, "request", now_iso)),
        (SECTION_DECISIONS, _record_items(
            cur, owner, records_mod.TYPE_DECISION, "decision", now_iso)),
        (SECTION_OPPORTUNITIES, _record_items(
            cur, owner, records_mod.TYPE_OPPORTUNITY, "opportunity", now_iso)),
        (SECTION_CLAIMS, _claim_items(cur, owner)),
        (SECTION_PEOPLE, _people_items(cur, owner)),
        (SECTION_RECENT, _recent_fact_items(cur, owner)),
    ]
    out: list[dict] = []
    for section, items in sections:
        for position, item in enumerate(items):
            out.append({"section": section, "position": position, **item})
    return out


# ---------------------------------------------------------------------------
# Generation — persisted, job-wrapped, audited
# ---------------------------------------------------------------------------

def generate_briefing(cur, *, owner_user_id: int,
                      actor_user_id: int | None = None) -> dict[str, Any]:
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateBriefingRejected("owner_user_id is required")
    ensure_briefings_schema(cur)

    job_id = jobs.create_job(cur, owner_user_id=owner,
                             job_type=jobs.JOB_BRIEFING_GENERATION)
    jobs.start_job(cur, owner_user_id=owner, job_id=job_id)
    try:
        items = _compose(cur, owner)
    except Exception:
        jobs.fail_job(cur, owner_user_id=owner, job_id=job_id,
                      outcome_note="composition failed")
        raise

    now = _now_iso()
    all_refs = evidence.normalize_refs(
        [ref for item in items for ref in item["refs"]])
    cur.execute(
        f"""INSERT INTO {BRIEFINGS_TABLE}
        (owner_user_id, title, generated_at, job_id, item_count,
         evidence_refs, lifecycle_state, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (owner, f"Private briefing — {now[:10]}", now, job_id, len(items),
         evidence.pack_refs(all_refs), LIFECYCLE_ACTIVE, now),
    )
    briefing_id = int(cur.lastrowid)
    for item in items:
        cur.execute(
            f"""INSERT INTO {ITEMS_TABLE}
            (owner_user_id, briefing_id, section, position, label, detail,
             evidence_refs, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (owner, briefing_id, item["section"], item["position"],
             item["label"], item["detail"], evidence.pack_refs(item["refs"]), now),
        )

    jobs.finish_job(cur, owner_user_id=owner, job_id=job_id,
                    result_ref=evidence.format_ref("briefing", briefing_id))
    audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=audit.ACTION_BRIEFING_GENERATED, object_type="BRIEFING",
        object_id=str(briefing_id), purpose="user_request",
        result_count=len(items),
    )
    return get_briefing(cur, owner_user_id=owner, briefing_id=briefing_id)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _project(row) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": int(data.get("id") or 0),
        "ref": evidence.format_ref("briefing", int(data.get("id") or 0)),
        "title": data.get("title") or "",
        "generated_at": data.get("generated_at") or "",
        "item_count": int(data.get("item_count") or 0),
        "evidence": evidence.unpack_refs(data.get("evidence_refs")),
    }


def _project_item(row) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": int(data.get("id") or 0),
        "section": data.get("section") or "",
        "position": int(data.get("position") or 0),
        "label": data.get("label") or "",
        "detail": data.get("detail") or "",
        "evidence": evidence.unpack_refs(data.get("evidence_refs")),
    }


def list_briefings(cur, *, owner_user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    ensure_briefings_schema(cur)
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return []
    bounded = max(1, min(int(limit or 20), MAX_BRIEFINGS))
    cur.execute(
        f"""SELECT * FROM {BRIEFINGS_TABLE}
        WHERE owner_user_id=? AND lifecycle_state=?
        ORDER BY id DESC LIMIT ?""",
        (owner, LIFECYCLE_ACTIVE, bounded),
    )
    return [_project(row) for row in cur.fetchall()]


def get_briefing(cur, *, owner_user_id: int, briefing_id: int) -> dict[str, Any] | None:
    ensure_briefings_schema(cur)
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return None
    cur.execute(
        f"""SELECT * FROM {BRIEFINGS_TABLE}
        WHERE id=? AND owner_user_id=? AND lifecycle_state=?""",
        (int(briefing_id or 0), owner, LIFECYCLE_ACTIVE),
    )
    row = cur.fetchone()
    if row is None:
        return None
    briefing = _project(row)
    cur.execute(
        f"""SELECT * FROM {ITEMS_TABLE}
        WHERE briefing_id=? AND owner_user_id=?
        ORDER BY id ASC""",
        (briefing["id"], owner),
    )
    items = [_project_item(item) for item in cur.fetchall()]
    sections: list[dict[str, Any]] = []
    for name in SECTIONS:
        rows = [i for i in items if i["section"] == name]
        if rows:
            sections.append({"section": name, "items": rows})
    briefing["items"] = items
    briefing["sections"] = sections
    return briefing


def explain(cur, *, owner_user_id: int, refs: object) -> list[dict[str, Any]]:
    """Ask Why: owner-checked resolution of any evidence refs a screen shows."""
    return evidence.resolve_refs(cur, int(owner_user_id or 0), refs)


# ---------------------------------------------------------------------------
# Create Action — through the canonical record writer
# ---------------------------------------------------------------------------

def create_action(
    cur,
    *,
    owner_user_id: int,
    briefing_id: int,
    action_type: str,
    title: str,
    due_at: object = None,
    item_id: int = 0,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """Turn a briefing line into an obligation or request the member owns.

    The new record cites the briefing it came from (and the quoted item's own
    evidence, when an item is named), so "why does this obligation exist" walks
    back to the exact lines that prompted it.
    """
    owner = int(owner_user_id or 0)
    briefing = get_briefing(cur, owner_user_id=owner, briefing_id=briefing_id)
    if briefing is None:
        raise PrivateBriefingRejected("briefing not found")
    kind = str(action_type or "").strip().lower()
    record_type = ACTION_TYPES.get(kind)
    if record_type is None:
        allowed = ", ".join(sorted(ACTION_TYPES))
        raise PrivateBriefingRejected(f"action_type must be one of: {allowed}")
    clean_title = _clip(title)
    if not clean_title:
        raise PrivateBriefingRejected("an action needs a title")

    refs = [briefing["ref"]]
    if int(item_id or 0):
        matched = [i for i in briefing["items"] if i["id"] == int(item_id)]
        if not matched:
            raise PrivateBriefingRejected("briefing item not found")
        refs.extend(matched[0]["evidence"])

    fields: dict[str, Any] = {"title": clean_title, "related_entity_ids": refs}
    if record_type == records_mod.TYPE_OBLIGATION:
        fields["obligation_type"] = "FOLLOW_UP"
        if str(due_at or "").strip():
            fields["due_at"] = str(due_at).strip()
    else:
        fields["category"] = "GENERAL"

    outcome = records_mod.create_record(
        cur, record_type=record_type, owner_user_id=owner,
        actor_user_id=int(actor_user_id or owner), purpose="user_request",
        **fields)
    return {
        "status": outcome["status"],
        "record_id": int(outcome["record_id"]),
        "record_type": record_type,
        "ref": evidence.format_ref(
            "obligation" if record_type == records_mod.TYPE_OBLIGATION else "request",
            int(outcome["record_id"])),
        "cited": refs,
    }
