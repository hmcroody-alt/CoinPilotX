"""The shared evidence-reference contract for Private Office capabilities.

Every capability that *derives* something — a briefing line, a shield finding,
a relationship summary, a filed claim — must be able to answer "why does the
platform say this?" with pointers back to the member's own primary rows. This
module is the one vocabulary for those pointers, so a briefing, a finding and
a concierge receipt all cite evidence the same way and a single resolver can
verify any of them.

A reference is identity, never content: ``fact:382``, ``obligation:12``,
``document:7``. The same rule the audit table enforces structurally applies
here — a ref names a row, and reading the row goes back through the owning
module's own gated readers. Resolution in this module confirms only that the
row exists *for this owner* and returns a type-level label, so a screen can
render "based on 3 facts and 1 document" without this module becoming a second,
ungated read path.

Refs are stored as a JSON array of strings (``pack_refs``/``unpack_refs``) so
the column stays queryable with LIKE for a single ref and survives round-trips
without a delimiter ambiguity ("fact:1" vs "fact:12").
"""

from __future__ import annotations

import json
import re
from typing import Any

#: One row per kind: (table, label_column). The table is named here rather than
#: imported from each feature module to keep this module import-light — it is
#: imported by briefings, shield, documents and relationships, and a cycle
#: between any two of them would be resolved by whoever hits it first copying
#: the vocabulary, which is how vocabularies fork. The write-boundary guard
#: does not apply: nothing in this module writes.
KINDS: dict[str, tuple[str, str]] = {
    "fact": ("private_facts", "fact_type"),
    "node": ("private_graph_nodes", "node_type"),
    "edge": ("private_graph_edges", "relation_type"),
    "obligation": ("private_obligations", "title"),
    "event": ("private_domain_events", "title"),
    "decision": ("private_decisions", "title"),
    "request": ("private_requests", "title"),
    "risk": ("private_risks", "title"),
    "opportunity": ("private_opportunities", "title"),
    "document": ("private_documents", "title"),
    "finding": ("private_shield_findings", "title"),
    "briefing": ("private_office_briefings", "title"),
}

#: Refs per object are capped. Twenty is generous for honest citation; an
#: object citing more than that is a dump, not evidence, and unbounded lists
#: turn the resolver into an amplification vector.
MAX_REFS = 20

_REF_PATTERN = re.compile(r"^([a-z_]{1,32}):([1-9][0-9]{0,17})$")


def format_ref(kind: str, row_id: object) -> str:
    """A canonical ref, or ``""`` for anything that is not one."""
    kind = str(kind or "").strip().lower()
    try:
        number = int(row_id)
    except (TypeError, ValueError):
        return ""
    if kind not in KINDS or number <= 0:
        return ""
    return f"{kind}:{number}"


def parse_ref(value: object) -> tuple[str, int] | None:
    """``("fact", 382)`` for a well-formed known ref, else ``None``.

    Unknown kinds parse to ``None`` rather than passing through: a ref this
    module cannot resolve is a ref no reader can verify, and an unverifiable
    citation stored today is a lie waiting to render.
    """
    match = _REF_PATTERN.match(str(value or "").strip().lower())
    if not match or match.group(1) not in KINDS:
        return None
    return match.group(1), int(match.group(2))


def normalize_refs(values: object) -> list[str]:
    """Parse, dedupe (order-preserving) and cap a caller-supplied ref list."""
    if isinstance(values, str):
        values = unpack_refs(values)
    if not isinstance(values, (list, tuple)):
        return []
    seen: list[str] = []
    for value in values:
        parsed = parse_ref(value)
        if parsed is None:
            continue
        ref = f"{parsed[0]}:{parsed[1]}"
        if ref not in seen:
            seen.append(ref)
        if len(seen) >= MAX_REFS:
            break
    return seen


def pack_refs(values: object) -> str:
    """Storage form: a JSON array of canonical refs, ``""`` when empty."""
    refs = normalize_refs(values)
    return json.dumps(refs) if refs else ""


def unpack_refs(stored: object) -> list[str]:
    """The inverse of :func:`pack_refs`. Malformed storage reads as empty —
    a corrupt citation list must degrade to "no evidence shown", never crash
    the object it decorates."""
    text = str(stored or "").strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except ValueError:
        return []
    if not isinstance(loaded, list):
        return []
    out: list[str] = []
    for item in loaded:
        parsed = parse_ref(item)
        if parsed is not None:
            out.append(f"{parsed[0]}:{parsed[1]}")
    return out


def resolve_refs(cur, owner_user_id: int, refs: object) -> list[dict[str, Any]]:
    """Owner-checked existence for each ref.

    Returns one entry per normalized ref: ``{"ref", "kind", "id", "exists",
    "label"}``. The owner predicate is in the WHERE clause of every probe, so a
    ref naming another member's row resolves to ``exists=False`` — identical to
    a ref naming nothing, which is the Stage 14 non-leak shape.

    A table that does not exist yet (a feature's schema not ensured on this
    deployment) also reads as ``exists=False``: the citation is unverifiable
    here and now, and this resolver reports what it can prove, not what was
    probably meant.
    """
    owner = int(owner_user_id or 0)
    resolved: list[dict[str, Any]] = []
    for ref in normalize_refs(refs):
        kind, row_id = parse_ref(ref)  # normalize_refs guarantees parseability
        table, label_column = KINDS[kind]
        exists, label = False, ""
        if owner > 0:
            try:
                cur.execute(
                    f"SELECT {label_column} FROM {table} WHERE id=? AND owner_user_id=?",
                    (row_id, owner),
                )
                row = cur.fetchone()
                if row is not None:
                    exists = True
                    value = row[label_column] if isinstance(row, dict) else row[0]
                    label = str(value or "")[:80]
            except Exception:
                exists, label = False, ""
        resolved.append(
            {"ref": ref, "kind": kind, "id": row_id, "exists": exists, "label": label}
        )
    return resolved
