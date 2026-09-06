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

Evidence availability is a third axis
-------------------------------------
A claim carries three independent properties and this module owns exactly one
of them:

* **Provenance** — where the claim came from. Owned by ``model``/``facts``.
* **Verification** — what validation has happened. Owned by ``facts``.
* **Availability** — whether the cited source is resolvable *right now*. Owned
  here.

They are independent, and collapsing any pair loses information the member
needs. A fact can be ``VERIFIED`` (verification) from a ``DOCUMENT_EXTRACTED``
source (provenance) whose document the member has since deleted
(availability = ``SOURCE_UNAVAILABLE``). None of those three statements
contradicts the others, and none can be inferred from another. Encoding
"the document is gone" as a provenance value would rewrite history; encoding
it as a verification value would retroactively un-verify a check that really
did happen. So it is stored in neither, and computed here from the source
row's own lifecycle at read time.

The rule that makes the axis load-bearing: a source that is not currently
available may not *establish* a fresh verification (:func:`may_verify`), while
a verification that already happened stays attributable — the fact keeps its
``verification_state``, its ``last_verified_at`` and its provenance ref, and
this module reports the citation as ``SOURCE_UNAVAILABLE`` beside them rather
than deleting or downgrading them.
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

#: The lifecycle column for each kind, or ``None`` where the owning table has
#: no lifecycle concept. ``finding`` is the one ``None``: shield findings carry
#: a *status* (open / dismissed / resolved), which is a workflow position and
#: not a statement about whether the row still stands as a source. Mapping
#: status onto availability would let "the member dismissed this finding" read
#: as "the evidence is gone", which is a different and false sentence.
KIND_LIFECYCLE: dict[str, str | None] = {
    "fact": "lifecycle_state",
    "node": "lifecycle_state",
    "edge": "lifecycle_state",
    "obligation": "lifecycle_state",
    "event": "lifecycle_state",
    "decision": "lifecycle_state",
    "request": "lifecycle_state",
    "risk": "lifecycle_state",
    "opportunity": "lifecycle_state",
    "document": "lifecycle_state",
    "finding": None,
    "briefing": "lifecycle_state",
}

# ---------------------------------------------------------------------------
# The availability vocabulary
# ---------------------------------------------------------------------------
#: The source stands and its content is reachable through its owning module.
AVAILABILITY_AVAILABLE = "AVAILABLE"
#: The source was retired by the member but the row is still there, so the
#: citation still resolves and still names something real.
AVAILABILITY_ARCHIVED = "ARCHIVED"
#: A newer version took its place. The cited version itself is unchanged, which
#: is exactly why superseded sources stay resolvable: a claim made against
#: revision 3 is not a claim about revision 4.
AVAILABILITY_SUPERSEDED = "SUPERSEDED"
#: The source stopped holding as of a date. Historically resolvable, and the
#: expiry is usually the interesting part of the citation rather than a defect.
AVAILABILITY_EXPIRED = "EXPIRED"
#: The source was deleted or revoked. The row may survive for referential
#: honesty, but its content is no longer served, so nothing new may be checked
#: against it.
AVAILABILITY_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
#: No such row for this owner. Cross-owner refs land here too, deliberately
#: indistinguishable from a ref that names nothing at all.
AVAILABILITY_NOT_FOUND = "NOT_FOUND"
#: The probe could not be completed — schema not ensured on this deployment, a
#: driver error, an unrecognised lifecycle value. Not a synonym for absent: it
#: means this resolver declines to state either way, which is the only honest
#: answer when it does not know.
AVAILABILITY_UNKNOWN = "UNKNOWN"

AVAILABILITY_STATES: tuple[str, ...] = (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_ARCHIVED,
    AVAILABILITY_SUPERSEDED,
    AVAILABILITY_EXPIRED,
    AVAILABILITY_SOURCE_UNAVAILABLE,
    AVAILABILITY_NOT_FOUND,
    AVAILABILITY_UNKNOWN,
)

#: Lifecycle value (as stored by any owning module) → availability. Both
#: vocabularies are covered: ``model.LIFECYCLE_STATES`` for the ledger tables
#: and ``documents.LIFECYCLE_DELETED`` for the document store, which does not
#: use the model vocabulary. Values are matched case-insensitively after strip.
_AVAILABILITY_BY_LIFECYCLE: dict[str, str] = {
    "ACTIVE": AVAILABILITY_AVAILABLE,
    "ARCHIVED": AVAILABILITY_ARCHIVED,
    "SUPERSEDED": AVAILABILITY_SUPERSEDED,
    "EXPIRED": AVAILABILITY_EXPIRED,
    "REVOKED": AVAILABILITY_SOURCE_UNAVAILABLE,
    "DELETED": AVAILABILITY_SOURCE_UNAVAILABLE,
}

#: Availabilities where the citation still names a real, readable row, so a
#: screen may render it and a reader may follow it.
RESOLVABLE_AVAILABILITY: frozenset[str] = frozenset(
    {
        AVAILABILITY_AVAILABLE,
        AVAILABILITY_ARCHIVED,
        AVAILABILITY_SUPERSEDED,
        AVAILABILITY_EXPIRED,
    }
)

#: Availabilities from which a *fresh* verification may be established. Only
#: one qualifies, and the narrowness is the point. ``documents.fetch_content``
#: serves bytes for ``ACTIVE`` rows and nothing else, so ``AVAILABLE`` is the
#: only state in which this resolver can honestly say the supporting content
#: is still there to be checked against. Everything else — archived, expired,
#: superseded, deleted, missing, unknown — fails closed. This does not touch
#: verifications already recorded; see the module docstring.
VERIFYING_AVAILABILITY: frozenset[str] = frozenset({AVAILABILITY_AVAILABLE})

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


def availability_for(kind: object, lifecycle_state: object, *, found: bool) -> str:
    """The availability of one source row, from its kind and lifecycle value.

    Kept pure and separate from the query so the decision table is testable
    without a database and so a mutation to it shows up as a behaviour change
    rather than as a query that still returns rows.

    Fail-closed twice over. A row that was not found is ``NOT_FOUND``, never
    available. A row that *was* found but whose lifecycle value is not one this
    module recognises is ``UNKNOWN``, not ``AVAILABLE`` — an unrecognised state
    is a state written by code this module has not been taught about, and
    guessing "probably still fine" is how a withdrawn source keeps being cited.
    """
    if not found:
        return AVAILABILITY_NOT_FOUND
    if KIND_LIFECYCLE.get(str(kind or "").strip().lower()) is None:
        # No lifecycle concept on this table: the row's existence is the whole
        # of what can be said, and it exists.
        return AVAILABILITY_AVAILABLE
    state = str(lifecycle_state or "").strip().upper()
    if not state:
        return AVAILABILITY_UNKNOWN
    return _AVAILABILITY_BY_LIFECYCLE.get(state, AVAILABILITY_UNKNOWN)


def is_resolvable(availability: object) -> bool:
    """May this citation still be rendered and followed?"""
    return str(availability or "") in RESOLVABLE_AVAILABILITY


def may_verify(availability: object) -> bool:
    """May a *fresh* verification be established against this source now?

    This is the honesty gate the third axis exists for. It answers only about
    new verification decisions. It says nothing about verifications already
    recorded, which stay exactly as they were written — see
    :func:`historical_attribution`.
    """
    return str(availability or "") in VERIFYING_AVAILABILITY


def historical_attribution(entry: object) -> dict[str, Any]:
    """What a *past* verification against this citation is still worth saying.

    A verification that happened really happened. When its source later goes
    away the correct rendering is "checked against a document you have since
    deleted", not "unverified" and not silence. This returns the pieces of that
    sentence — the ref, the availability now, and whether the reader can still
    open the source — so callers do not have to reimplement the distinction and
    accidentally reimplement it as a downgrade.
    """
    if not isinstance(entry, dict):
        return {
            "ref": "", "kind": "", "id": 0,
            "availability": AVAILABILITY_UNKNOWN,
            "attributable": False, "openable": False,
        }
    availability = str(entry.get("availability") or AVAILABILITY_UNKNOWN)
    return {
        "ref": str(entry.get("ref") or ""),
        "kind": str(entry.get("kind") or ""),
        "id": int(entry.get("id") or 0),
        "availability": availability,
        # A recorded verification names a specific row. As long as the ref is
        # well formed the attribution stands, even when the row is gone: the
        # claim "this was checked against document 7" does not stop being true
        # because document 7 was deleted afterwards.
        "attributable": bool(entry.get("ref")),
        "openable": is_resolvable(availability),
    }


def summarize_availability(resolved: object) -> dict[str, Any]:
    """Counts a screen can render without re-deriving the vocabulary.

    ``{"total", "resolvable", "unavailable", "missing", "unknown", "by_state"}``.
    ``unavailable`` and ``missing`` are counted apart on purpose: "one of your
    sources was deleted" and "one of your citations points at nothing" are
    different messages and only the first one is the member's own doing.
    """
    entries = [e for e in (resolved or []) if isinstance(e, dict)]
    by_state: dict[str, int] = {state: 0 for state in AVAILABILITY_STATES}
    for entry in entries:
        state = str(entry.get("availability") or AVAILABILITY_UNKNOWN)
        if state not in by_state:
            state = AVAILABILITY_UNKNOWN
        by_state[state] += 1
    return {
        "total": len(entries),
        "resolvable": sum(by_state[s] for s in RESOLVABLE_AVAILABILITY),
        "unavailable": by_state[AVAILABILITY_SOURCE_UNAVAILABLE],
        "missing": by_state[AVAILABILITY_NOT_FOUND],
        "unknown": by_state[AVAILABILITY_UNKNOWN],
        "by_state": by_state,
    }


def _cell(row: object, index: int, name: str) -> object:
    """One column from a driver row, whether it indexes by position or by name."""
    try:
        return row[index]  # type: ignore[index]
    except (TypeError, KeyError, IndexError):
        pass
    try:
        return row[name]  # type: ignore[index]
    except Exception:
        return None


def resolve_refs(cur, owner_user_id: int, refs: object) -> list[dict[str, Any]]:
    """Owner-checked existence *and* availability for each ref.

    Returns one entry per normalized ref::

        {"ref", "kind", "id", "exists", "label", "lifecycle",
         "availability", "resolvable", "may_verify"}

    The owner predicate is in the WHERE clause of every probe, so a ref naming
    another member's row resolves to ``exists=False`` / ``NOT_FOUND`` —
    identical to a ref naming nothing, which is the Stage 14 non-leak shape. No
    label, no lifecycle and no distinguishable availability escape for a row
    owned by someone else.

    ``exists`` keeps its original meaning: the row is there for this owner, in
    *any* lifecycle state. A deleted document still exists as a row, which is
    what lets its old citations keep resolving; whether it is still a usable
    source is the separate ``availability`` field, and callers that care must
    read that one rather than inferring from ``exists``.

    A table that does not exist yet (a feature's schema not ensured on this
    deployment) reads as ``exists=False`` with availability ``UNKNOWN`` rather
    than ``NOT_FOUND``. The distinction is the whole honesty improvement: this
    resolver could not look, and saying "no such row" would be reporting a
    conclusion it did not reach.

    Probes are grouped by kind — one query per distinct kind rather than one
    per ref — so a fully-populated 20-ref list costs at most as many queries as
    there are kinds in it.
    """
    owner = int(owner_user_id or 0)
    normalized = normalize_refs(refs)
    if not normalized:
        return []

    wanted: dict[str, list[int]] = {}
    for ref in normalized:
        kind, row_id = parse_ref(ref)  # normalize_refs guarantees parseability
        wanted.setdefault(kind, []).append(row_id)

    # kind -> {row_id: (label, lifecycle)}; a kind absent from `probed` is one
    # whose probe could not be run at all.
    found: dict[str, dict[int, tuple[str, str]]] = {}
    probed: set[str] = set()
    for kind, ids in wanted.items():
        if owner <= 0:
            # No owner, no probe. Not an error and not an absence — there was
            # nothing to ask on behalf of.
            continue
        table, label_column = KINDS[kind]
        lifecycle_column = KIND_LIFECYCLE.get(kind)
        select = f"{label_column}, id"
        if lifecycle_column:
            select = f"{label_column}, id, {lifecycle_column}"
        placeholders = ",".join("?" for _ in ids)
        try:
            cur.execute(
                f"SELECT {select} FROM {table} "
                f"WHERE owner_user_id=? AND id IN ({placeholders})",
                tuple([owner] + ids),
            )
            rows = cur.fetchall() or []
        except Exception:
            continue
        probed.add(kind)
        bucket: dict[int, tuple[str, str]] = {}
        for row in rows:
            try:
                row_id = int(_cell(row, 1, "id") or 0)
            except (TypeError, ValueError):
                continue
            label = str(_cell(row, 0, label_column) or "")[:80]
            lifecycle = ""
            if lifecycle_column:
                lifecycle = str(_cell(row, 2, lifecycle_column) or "")
            bucket[row_id] = (label, lifecycle)
        found[kind] = bucket

    resolved: list[dict[str, Any]] = []
    for ref in normalized:
        kind, row_id = parse_ref(ref)
        hit = found.get(kind, {}).get(row_id)
        exists = hit is not None
        label, lifecycle = hit if hit else ("", "")
        if kind in probed:
            availability = availability_for(kind, lifecycle, found=exists)
        else:
            # Could not look: schema missing, driver error, or no owner.
            availability = AVAILABILITY_UNKNOWN
        resolved.append(
            {
                "ref": ref,
                "kind": kind,
                "id": row_id,
                "exists": exists,
                "label": label,
                "lifecycle": lifecycle,
                "availability": availability,
                "resolvable": is_resolvable(availability),
                "may_verify": may_verify(availability),
            }
        )
    return resolved
