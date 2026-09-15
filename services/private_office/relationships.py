"""Relationship Intelligence — people, read out of the substrate they live in.

What this module is
-------------------
The Private Office's view of *people*: the advisors, family members, partners
and providers a member's affairs actually involve. It is deliberately a
composition layer, not a store:

* A person **is a PERSON node** in the private graph, created through
  ``graph.upsert_node`` — the only sanctioned node creator.
* What is known about a person — their name, their role, anything else — **is
  a private fact** about that node, written through ``facts.record_fact`` with
  ``USER_ASSERTED`` provenance. There is no ``people`` table with a ``name``
  column, because the moment a person's details live outside the fact store
  they stop carrying provenance, sensitivity and staleness, and "why does
  PulseSoc know this?" stops having an answer for exactly the data it matters
  most for.
* A person's **commitments** are the OBLIGATION and REQUEST records whose
  ``related_entity_ids`` cite the person's node ref; their **timeline** is the
  merge of their facts, their edges and every record that cites them. Nothing
  here is inferred, scored, or guessed — every line traces to a row the member
  (or a reviewed extraction) put there, and every payload carries the evidence
  refs to prove it.

This module therefore contains **no INSERT statements at all**. The write
boundary test keeps that true structurally: a people-store that grew its own
tables would be the second fact store the package's standing rules exist to
prevent.

``prepare_briefing`` is the deterministic "before you meet them" aggregation:
identity, open commitments, recent activity, connections — each section built
from rows, each row cited. It is a *view*; the Private Briefings capability
may later persist one, but preparing it asserts nothing and writes nothing.
"""

from __future__ import annotations

from typing import Any

from services.private_office import audit
from services.private_office import evidence
from services.private_office import facts as facts_mod
from services.private_office import graph as graph_mod
from services.private_office import model
from services.private_office import records as records_mod

#: Identity facts. Closed vocabulary so a directory read is a handful of fact
#: types, not a scan; anything else a member records about a person is still
#: shown on the profile, just not treated as identity.
#:
#: ``relationship_role`` carries what the member calls the relationship —
#: "lawyer", "sister", "contractor". There is deliberately no second
#: ``relationship_type`` field beside it: two columns for one idea is how a
#: screen ends up showing one and a search ends up reading the other.
FACT_NAME = "name"
FACT_ROLE = "relationship_role"
FACT_PHONE = "contact_phone"
FACT_EMAIL = "contact_email"
FACT_USERNAME = "pulsesoc_username"
FACT_PHOTO = "contact_photo_media_id"
FACT_NOTES = "contact_notes"
FACT_SOURCE = "contact_source"
FACT_FAVORITE = "contact_favorite"
IDENTITY_FACT_TYPES: tuple[str, ...] = (
    FACT_NAME, FACT_ROLE, FACT_PHONE, FACT_EMAIL, FACT_USERNAME,
    FACT_PHOTO, FACT_SOURCE, FACT_FAVORITE,
)

#: The single-valued contact fields, in the order the resolver and the editor
#: both read them. ``contact_notes`` is absent on purpose — notes accumulate.
CONTACT_FACT_TYPES: tuple[str, ...] = (
    FACT_NAME, FACT_ROLE, FACT_PHONE, FACT_EMAIL, FACT_USERNAME,
    FACT_PHOTO, FACT_SOURCE, FACT_FAVORITE,
)

#: How a person came to be in the directory. Provenance for the *record*, as
#: distinct from the fact store's provenance for each value: both matter, and
#: "the member typed this" and "this arrived because they scheduled a meeting"
#: are the same provenance to the fact store and very different to a member
#: looking at a name they do not remember adding.
SOURCE_MANUAL = "MANUAL"
SOURCE_MEETING_INVITEE = "PRIVATE_MEETING_INVITEE"
SOURCE_PULSESOC_USER = "PULSESOC_USER"
SOURCE_IMPORTED_CONTACT = "IMPORTED_CONTACT"
SOURCE_OTHER = "OTHER"
SOURCES: tuple[str, ...] = (
    SOURCE_MANUAL, SOURCE_MEETING_INVITEE, SOURCE_PULSESOC_USER,
    SOURCE_IMPORTED_CONTACT, SOURCE_OTHER,
)

#: The record primitives a person can be committed through, and the evidence
#: kind each serializes to.
RECORD_KINDS: dict[str, str] = {
    records_mod.TYPE_OBLIGATION: "obligation",
    records_mod.TYPE_EVENT: "event",
    records_mod.TYPE_DECISION: "decision",
    records_mod.TYPE_REQUEST: "request",
    records_mod.TYPE_RISK: "risk",
    records_mod.TYPE_OPPORTUNITY: "opportunity",
}
COMMITMENT_TYPES: tuple[str, ...] = (records_mod.TYPE_OBLIGATION, records_mod.TYPE_REQUEST)

MAX_DIRECTORY = 200
MAX_TIMELINE = 50
MAX_NAME_CHARS = 120
MAX_NOTES_CHARS = 2000
MAX_HANDLE_CHARS = 64

#: A PulseSoc account id, written as a graph external reference. The scheme is
#: spelled out rather than assembled ad hoc because ``node_key`` hashes it: one
#: caller writing ``pulsesoc:user:7`` and another ``user:7`` would produce two
#: nodes for one account, and nothing would ever notice.
EXTERNAL_REF_SCHEME = "pulsesoc:user:"


class PrivateRelationshipRejected(ValueError):
    """A person write or read this module refuses."""


# ---------------------------------------------------------------------------
# Normalization — the shapes identity is compared in
# ---------------------------------------------------------------------------
#
# Matching happens on normalized values only. "Dana@Example.COM " and
# "dana@example.com" are one identifier written twice, and a directory that
# treats them as two is a duplicate generator with extra steps. The normalizers
# are deliberately conservative: they fold away formatting, never meaning.

def normalize_email(raw: object) -> str:
    """Lowercased, trimmed, and only if it is plausibly an address.

    No deliverability check and no lookup — see §34 of the product rules and
    the module docstring. Nothing is *asked* about anybody; this only decides
    whether two strings the member typed are the same string.
    """
    text = str(raw or "").strip().lower()
    if not text or " " in text:
        return ""
    local, sep, domain = text.partition("@")
    if not sep or not local or "." not in domain or domain.startswith(".") \
            or domain.endswith("."):
        return ""
    return text[:MAX_NAME_CHARS]


def normalize_phone(raw: object) -> str:
    """Digits, with a leading ``+`` kept when the member wrote one.

    Punctuation is presentation: ``+1 (415) 555-0134`` and ``+14155550134``
    are one number. What is *not* folded is the country prefix — a bare
    ``4155550134`` stays distinct from ``+14155550134``, because inventing a
    country code for a member's contact is exactly the kind of helpful guess
    that silently merges two people.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    plus = text.startswith("+")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 6 or len(digits) > 18:
        return ""
    return ("+" if plus else "") + digits


def normalize_username(raw: object) -> str:
    """Lowercased, ``@`` stripped. The handle as PulseSoc stores it."""
    text = str(raw or "").strip().lstrip("@").strip().lower()
    if not text or " " in text:
        return ""
    return text[:MAX_HANDLE_CHARS]


def normalize_source(raw: object) -> str:
    text = str(raw or "").strip().upper()
    return text if text in SOURCES else SOURCE_OTHER


def external_ref_for(pulsesoc_user_id: object) -> str:
    """``pulsesoc:user:<id>`` for a real account id, ``""`` for anything else."""
    try:
        account = int(pulsesoc_user_id or 0)
    except (TypeError, ValueError):
        return ""
    return f"{EXTERNAL_REF_SCHEME}{account}" if account > 0 else ""


def account_id_from_ref(external_ref: object) -> int:
    """The inverse. ``0`` when the node is not linked to an account."""
    text = str(external_ref or "").strip()
    if not text.startswith(EXTERNAL_REF_SCHEME):
        return 0
    try:
        return int(text[len(EXTERNAL_REF_SCHEME):])
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------

def _contact_facts(cur, *, owner_user_id: int, node_ids: list[int]) -> dict[int, dict]:
    """node_id → {fact_type: {"value", "fact_id"}} for the contact vocabulary.

    Rows arrive newest-first, so the first of each type per node is the current
    value; the older ones stay in the store and stay on the timeline, which is
    the point of keeping contact details as facts rather than as columns.
    """
    if not node_ids:
        return {}
    rows = facts_mod.list_facts_for_subjects(
        cur, owner_user_id=int(owner_user_id or 0),
        subject_type=facts_mod.SUBJECT_NODE,
        subject_ids=[str(n) for n in node_ids], fact_types=CONTACT_FACT_TYPES,
    )
    out: dict[int, dict] = {}
    for row in rows:
        try:
            subject = int(str(row.get("subject_id") or "0"))
        except ValueError:
            continue
        kind = str(row.get("fact_type") or "")
        current = out.setdefault(subject, {})
        if kind and kind not in current:
            current[kind] = {"value": str(row.get("typed_value") or ""),
                             "fact_id": int(row.get("id") or 0)}
    return out


def _contact_index(cur, *, owner_user_id: int) -> dict[str, Any]:
    """The owner's people, indexed by every identifier they can be matched on.

    Built by reading the member's own bounded directory rather than by querying
    the fact store for a value. That is a deliberate trade: a value predicate on
    ``list_facts`` would be faster and would also be a way to ask the substrate
    "who has this email address" — a question with an answer even when the
    caller owns nobody, which is the shape of an existence oracle. Here the
    scan is over rows the caller already owns, so an identifier that is not
    theirs simply is not in the index.
    """
    owner = int(owner_user_id or 0)
    nodes = graph_mod.list_nodes(
        cur, owner_user_id=owner, node_types=[model.NODE_PERSON],
        limit=MAX_DIRECTORY)
    node_ids = [int(n["id"]) for n in nodes]
    contact = _contact_facts(cur, owner_user_id=owner, node_ids=node_ids)

    by_account: dict[int, int] = {}
    by_email: dict[str, int] = {}
    by_phone: dict[str, int] = {}
    by_username: dict[str, int] = {}
    for node in nodes:
        node_id = int(node["id"])
        account = account_id_from_ref(node.get("external_ref"))
        if account:
            by_account.setdefault(account, node_id)
        values = contact.get(node_id, {})
        email = normalize_email(values.get(FACT_EMAIL, {}).get("value"))
        if email:
            by_email.setdefault(email, node_id)
        phone = normalize_phone(values.get(FACT_PHONE, {}).get("value"))
        if phone:
            by_phone.setdefault(phone, node_id)
        handle = normalize_username(values.get(FACT_USERNAME, {}).get("value"))
        if handle:
            by_username.setdefault(handle, node_id)
    return {"nodes": nodes, "contact": contact, "by_account": by_account,
            "by_email": by_email, "by_phone": by_phone,
            "by_username": by_username}


#: Why the resolver matched, returned alongside the node id. The screen needs
#: it: "this is already your contact" and "someone here has that phone number"
#: are different sentences, and a member asked to confirm a merge deserves to
#: be told which identifier collided.
MATCH_ACCOUNT = "pulsesoc_user_id"
MATCH_USERNAME = "pulsesoc_username"
MATCH_EMAIL = "email"
MATCH_PHONE = "phone"


def resolve_person(
    cur,
    *,
    owner_user_id: int,
    pulsesoc_user_id: object = 0,
    username: object = "",
    email: object = "",
    phone: object = "",
    index: dict | None = None,
) -> dict | None:
    """Which of the member's people, if any, these identifiers already name.

    Priority, highest first:

    1. the canonical PulseSoc account id,
    2. a linked PulseSoc username,
    3. a normalized email address,
    4. a normalized phone number.

    **A name is not on this list and never will be.** Two people called Dana
    Whitfield are two people; the store has no way to know otherwise and the
    member does. Returns ``{"node_id", "matched_on"}`` or ``None``.
    """
    idx = index if index is not None else _contact_index(
        cur, owner_user_id=int(owner_user_id or 0))
    account = int(pulsesoc_user_id or 0) if str(pulsesoc_user_id or "").lstrip("-").isdigit() else 0
    for value, table, reason in (
        (account, idx["by_account"], MATCH_ACCOUNT),
        (normalize_username(username), idx["by_username"], MATCH_USERNAME),
        (normalize_email(email), idx["by_email"], MATCH_EMAIL),
        (normalize_phone(phone), idx["by_phone"], MATCH_PHONE),
    ):
        if value and value in table:
            return {"node_id": int(table[value]), "matched_on": reason}
    return None


# ---------------------------------------------------------------------------
# Writes — everything through the canonical writers
# ---------------------------------------------------------------------------

#: What ``save_person`` did, so a caller can tell the member the truth.
SAVE_CREATED = "created"
SAVE_UPDATED = "updated"
SAVE_UNCHANGED = "unchanged"


def _text(value: object, limit: int = MAX_NAME_CHARS) -> str:
    return " ".join(str(value or "").split())[:limit]


def _set_field(
    cur,
    *,
    owner_user_id: int,
    node_id: int,
    fact_type: str,
    value: str,
    current: dict,
    domain: object,
    sensitivity: object,
    actor_user_id: int,
) -> bool:
    """Bring one contact field to ``value``. True when anything was written.

    A value equal to the current one writes nothing. That is not an
    optimization — ``record_fact`` would happily accept it and refresh the
    observation time, so a screen that saves an untouched form would march
    every field's "as of" date forward and quietly destroy the answer to "when
    did I last actually check this number".
    """
    held = str((current.get(fact_type) or {}).get("value") or "")
    if value == held:
        return False
    if not value:
        fact_id = int((current.get(fact_type) or {}).get("fact_id") or 0)
        if fact_id:
            facts_mod.retire_fact(
                cur, owner_user_id=owner_user_id, fact_id=fact_id,
                actor_user_id=actor_user_id, reason_code="member_cleared",
                purpose="user_request")
            return True
        return False
    facts_mod.record_fact(
        cur, owner_user_id=owner_user_id, subject_type=facts_mod.SUBJECT_NODE,
        subject_id=str(node_id), fact_type=fact_type, value=value,
        value_type=model.VALUE_STRING,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        domain=domain, sensitivity=sensitivity,
        actor_user_id=actor_user_id, purpose="user_request",
    )
    return True


def save_person(
    cur,
    *,
    owner_user_id: int,
    name: object = None,
    role: object = None,
    phone: object = None,
    email: object = None,
    username: object = None,
    pulsesoc_user_id: object = 0,
    photo_media_id: object = None,
    notes: object = None,
    source: object = SOURCE_MANUAL,
    node_id: object = None,
    domain: object = None,
    sensitivity: object = None,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """Create or update one person, resolving identity first. The write path.

    Three ways in, one behaviour:

    * ``node_id`` given — an edit of a person the member already picked.
    * no ``node_id``, but an identifier that :func:`resolve_person` matches —
      the same person arriving again, from a meeting invite or a re-typed
      form. Updated in place. This is what makes scheduling the same meeting
      twice produce one contact rather than two.
    * nothing matches — a new person.

    ``None`` means "not supplied" and leaves a field alone; ``""`` means
    "clear it", which retires the current fact rather than deleting anything.
    The distinction exists because a partial write is the normal case here: a
    meeting invite knows an account id and nothing else, and must not blank
    the phone number the member typed last week.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateRelationshipRejected("owner_user_id is required")
    actor = int(actor_user_id or owner)

    clean_name = _text(name) if name is not None else None
    clean_role = _text(role) if role is not None else None
    clean_phone = normalize_phone(phone) if phone is not None else None
    clean_email = normalize_email(email) if email is not None else None
    clean_handle = normalize_username(username) if username is not None else None
    clean_photo = _text(photo_media_id, MAX_HANDLE_CHARS) if photo_media_id is not None else None
    clean_notes = _text(notes, MAX_NOTES_CHARS) if notes is not None else None
    account_ref = external_ref_for(pulsesoc_user_id)

    # A field the member filled in that does not survive normalization is a
    # typo, and accepting it silently would store an address nothing can ever
    # match on while the screen shows it as saved.
    if phone is not None and str(phone or "").strip() and not clean_phone:
        raise PrivateRelationshipRejected("that phone number is not usable")
    if email is not None and str(email or "").strip() and not clean_email:
        raise PrivateRelationshipRejected("that email address is not usable")

    index = _contact_index(cur, owner_user_id=owner)
    target: int | None = None
    matched_on = ""

    if node_id is not None:
        person = _person_node(cur, owner_user_id=owner, node_id=node_id)
        if person is None:
            raise PrivateRelationshipRejected("person not found")
        target = int(person["id"])
    else:
        hit = resolve_person(
            cur, owner_user_id=owner, pulsesoc_user_id=pulsesoc_user_id,
            username=clean_handle or "", email=clean_email or "",
            phone=clean_phone or "", index=index)
        if hit:
            target = int(hit["node_id"])
            matched_on = str(hit["matched_on"])

    if target is None:
        if not clean_name:
            raise PrivateRelationshipRejected("a person needs a name")
        node = graph_mod.upsert_node(
            cur, owner_user_id=owner, node_type=model.NODE_PERSON,
            external_ref=account_ref, sensitivity=sensitivity, domain=domain,
            actor_user_id=actor, purpose="user_request",
        )
        target = int(node["node_id"])
        status = SAVE_CREATED
        current: dict = {}
        node_domain, node_sensitivity = node["domain"], node["sensitivity"]
    else:
        person = _person_node(cur, owner_user_id=owner, node_id=target)
        if person is None:  # pragma: no cover - index and store move together
            raise PrivateRelationshipRejected("person not found")
        status = SAVE_UNCHANGED
        current = index["contact"].get(target) or _contact_facts(
            cur, owner_user_id=owner, node_ids=[target]).get(target, {})
        node_domain = person.get("domain")
        node_sensitivity = person.get("sensitivity")
        if account_ref and not str(person.get("external_ref") or "").strip():
            # §45: the account turned up later. One node, one more identifier.
            graph_mod.attach_external_ref(
                cur, owner_user_id=owner, node_id=target,
                external_ref=account_ref, actor_user_id=actor)
            status = SAVE_UPDATED

    wrote = False
    for fact_type, value in (
        (FACT_NAME, clean_name),
        (FACT_ROLE, clean_role),
        (FACT_PHONE, clean_phone),
        (FACT_EMAIL, clean_email),
        (FACT_USERNAME, clean_handle),
        (FACT_PHOTO, clean_photo),
    ):
        if value is None:
            continue
        wrote |= _set_field(
            cur, owner_user_id=owner, node_id=target, fact_type=fact_type,
            value=value, current=current, domain=node_domain,
            sensitivity=node_sensitivity, actor_user_id=actor)

    if status == SAVE_CREATED:
        _set_field(
            cur, owner_user_id=owner, node_id=target, fact_type=FACT_SOURCE,
            value=normalize_source(source), current=current, domain=node_domain,
            sensitivity=node_sensitivity, actor_user_id=actor)

    if clean_notes:
        # Notes accumulate rather than replace: a note is a dated observation,
        # and the one it would overwrite is the one the member wrote down
        # because they did not want to lose it.
        facts_mod.record_fact(
            cur, owner_user_id=owner, subject_type=facts_mod.SUBJECT_NODE,
            subject_id=str(target), fact_type=FACT_NOTES, value=clean_notes,
            value_type=model.VALUE_STRING,
            provenance_type=model.PROVENANCE_USER_ASSERTED,
            domain=node_domain, sensitivity=node_sensitivity,
            actor_user_id=actor, purpose="user_request",
        )
        wrote = True

    if status == SAVE_UNCHANGED and wrote:
        status = SAVE_UPDATED

    summary = person_summary(cur, owner_user_id=owner, node_id=target)
    if summary is None:  # pragma: no cover - just written
        raise PrivateRelationshipRejected("person not found")
    summary["status"] = status
    summary["matched_on"] = matched_on
    return summary


def person_summary(cur, *, owner_user_id: int, node_id: object) -> dict[str, Any] | None:
    """The contact card fields for one person, read back from the store.

    Every write returns this rather than an echo of its arguments — §51 wants
    the screen to render what was *stored*, so a value the fact store trimmed,
    lowercased or refused shows up as the stored one and not as the hopeful
    one the client sent.
    """
    owner = int(owner_user_id or 0)
    person = _person_node(cur, owner_user_id=owner, node_id=node_id)
    if person is None:
        return None
    person_id = int(person["id"])
    values = _contact_facts(cur, owner_user_id=owner, node_ids=[person_id]).get(person_id, {})

    def held(fact_type: str) -> str:
        return str((values.get(fact_type) or {}).get("value") or "")

    return {
        "node_id": person_id,
        "person_id": person_id,
        "ref": evidence.format_ref("node", person_id),
        "name": held(FACT_NAME),
        "role": held(FACT_ROLE),
        "phone": held(FACT_PHONE),
        "email": held(FACT_EMAIL),
        "username": held(FACT_USERNAME),
        "pulsesoc_user_id": account_id_from_ref(person.get("external_ref")),
        "photo_media_id": held(FACT_PHOTO),
        "source": held(FACT_SOURCE) or SOURCE_OTHER,
        "favorite": held(FACT_FAVORITE) == "true",
        "domain": person.get("domain") or "",
        "sensitivity": person.get("sensitivity") or "",
        "created_at": person.get("created_at") or "",
        "updated_at": person.get("updated_at") or "",
    }


def set_favorite(
    cur, *, owner_user_id: int, node_id: object, favorite: bool,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """Pin or unpin a person. Ordering only; nothing about them changes."""
    owner = int(owner_user_id or 0)
    person = _person_node(cur, owner_user_id=owner, node_id=node_id)
    if person is None:
        raise PrivateRelationshipRejected("person not found")
    person_id = int(person["id"])
    current = _contact_facts(
        cur, owner_user_id=owner, node_ids=[person_id]).get(person_id, {})
    _set_field(
        cur, owner_user_id=owner, node_id=person_id, fact_type=FACT_FAVORITE,
        value="true" if favorite else "", current=current,
        domain=person.get("domain"), sensitivity=person.get("sensitivity"),
        actor_user_id=int(actor_user_id or owner))
    return person_summary(cur, owner_user_id=owner, node_id=person_id) or {}


def remove_person(
    cur, *, owner_user_id: int, node_id: object, actor_user_id: int | None = None,
) -> bool:
    """Take a person out of the directory. Archives the node; deletes nothing.

    Explicitly **not** a cascade. The member's meetings, their messages and the
    PulseSoc account behind the contact are not this feature's to remove, and a
    contact list that could delete a conversation would be a delete button
    wearing a smaller word. The facts stay in the store with their provenance
    intact, so removing the wrong person is recoverable and the audit trail
    still explains what was there.
    """
    owner = int(owner_user_id or 0)
    person = _person_node(cur, owner_user_id=owner, node_id=node_id)
    if person is None:
        return False
    return graph_mod.set_node_lifecycle(
        cur, owner_user_id=owner, node_id=int(person["id"]),
        lifecycle_state=model.LIFECYCLE_ARCHIVED,
        actor_user_id=int(actor_user_id or owner), purpose="user_request")


def add_person(
    cur,
    *,
    owner_user_id: int,
    name: str,
    role: str = "",
    domain: object = None,
    sensitivity: object = None,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """One new PERSON node plus its identity facts. Returns the profile summary.

    A name and a role and nothing else, so every call creates a new person —
    two advisors who share a name are two people, and merging them because
    their names collide would be the graph silently rewriting the member's
    world. :func:`save_person` is the same write with identifiers attached; it
    matches on those, never on this.
    """
    if not _text(name):
        raise PrivateRelationshipRejected("a person needs a name")
    saved = save_person(
        cur, owner_user_id=owner_user_id, name=name, role=role or "",
        domain=domain, sensitivity=sensitivity, actor_user_id=actor_user_id,
        source=SOURCE_MANUAL,
    )
    return {
        "node_id": saved["node_id"],
        "ref": saved["ref"],
        "name": saved["name"],
        "role": saved["role"],
        "domain": saved["domain"],
        "sensitivity": saved["sensitivity"],
    }


def record_person_fact(
    cur,
    *,
    owner_user_id: int,
    node_id: int,
    fact_type: str,
    value: object,
    value_type: str = model.VALUE_STRING,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """A member-asserted fact about one of their own people.

    The person check runs first, so a fact cannot be attached to a node that
    is not the caller's or is not a person — the canonical writer would accept
    any node id, and "facts about node 7" quietly meaning a property would be
    a category error nobody could see on the screen that made it.
    """
    owner = int(owner_user_id or 0)
    person = _person_node(cur, owner_user_id=owner, node_id=node_id)
    if person is None:
        raise PrivateRelationshipRejected("person not found")
    return facts_mod.record_fact(
        cur, owner_user_id=owner, subject_type=facts_mod.SUBJECT_NODE,
        subject_id=str(int(node_id)), fact_type=fact_type, value=value,
        value_type=value_type,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        domain=person.get("domain"), sensitivity=person.get("sensitivity"),
        actor_user_id=actor_user_id or owner, purpose="user_request",
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _person_node(cur, *, owner_user_id: int, node_id: object) -> dict | None:
    """The owner's live PERSON node, or ``None``.

    Absent, foreign, not a person and removed are one answer. The last of those
    is the one worth stating: :func:`remove_person` archives rather than
    deletes, so the row is still there to be fetched by id. If that reached the
    callers, a removed contact would go on answering reads and accepting edits
    while the directory said they were gone — "remove" would be a filter
    wearing a bigger word. The facts keep their provenance in the store; they
    are simply no longer reachable as a contact.
    """
    node = graph_mod.get_node(cur, owner_user_id=int(owner_user_id or 0), node_id=node_id)
    if node is None or str(node.get("node_type")) != model.NODE_PERSON:
        return None
    if str(node.get("lifecycle_state") or "") != model.LIFECYCLE_ACTIVE:
        return None
    return node


def _records_citing(cur, *, owner_user_id: int, node_ref: str,
                    record_types: tuple[str, ...]) -> list[dict]:
    """Active records whose ``related_entity_ids`` cite ``node_ref``.

    A linear filter over the owner's bounded record lists rather than a LIKE
    over the comma-joined column: the lists are already capped, and a LIKE on
    ``node:1`` would also match ``node:12``.
    """
    found: list[dict] = []
    for record_type in record_types:
        for row in records_mod.list_records(
                cur, record_type=record_type, owner_user_id=owner_user_id,
                limit=records_mod.MAX_LIMIT):
            if node_ref in (row.get("related_entity_ids") or []):
                row = dict(row)
                row["record_type"] = record_type
                row["ref"] = evidence.format_ref(RECORD_KINDS[record_type], int(row["id"]))
                found.append(row)
    return found


def _is_open(record: dict) -> bool:
    spec_closing = records_mod.SPECS[record["record_type"]]["closing"]
    return record.get("status") not in spec_closing


#: How the directory may be ordered. Three orderings, all of them stable and
#: all of them derived from rows — there is no "relevance" here, because a
#: contact list that reorders itself by a score nobody can inspect is a contact
#: list the member stops being able to predict.
SORT_RECENT = "recent"
SORT_NAME = "name"
SORT_ACTIVITY = "activity"
SORTS: tuple[str, ...] = (SORT_RECENT, SORT_NAME, SORT_ACTIVITY)


def _matches_query(row: dict, needle: str) -> bool:
    """Substring match over the fields the member can see on the card.

    Notes are excluded. They are the most private thing in the record and the
    member did not ask for them to be a search index; a name surfacing because
    of something written in confidence about them is a leak into the member's
    own screen, and from there into a screenshot.
    """
    if not needle:
        return True
    haystack = " ".join(str(row.get(field) or "") for field in
                        ("name", "role", "email", "phone", "username")).lower()
    return needle in haystack


def directory(
    cur,
    *,
    owner_user_id: int,
    limit: int = MAX_DIRECTORY,
    query: object = "",
    sort: object = SORT_RECENT,
    favorites_only: bool = False,
) -> list[dict[str, Any]]:
    """Every person the member has, with counts that are counts.

    ``open_commitments`` and ``connections`` are computed from the rows this
    module can show on the profile — never an estimate, so tapping through
    always finds exactly what the number promised.

    ``query`` filters; it does not rank. Filtering happens after the
    owner-scoped read, over rows already proven to be the caller's, so a search
    term can never be a probe for somebody else's contact.
    """
    owner = int(owner_user_id or 0)
    nodes = graph_mod.list_nodes(
        cur, owner_user_id=owner, node_types=[model.NODE_PERSON],
        limit=max(1, min(int(limit or MAX_DIRECTORY), MAX_DIRECTORY)))
    # list_nodes returns id ASC; the directory promises newest first.
    nodes = list(reversed(nodes))
    node_ids = [int(n["id"]) for n in nodes]
    contact = _contact_facts(cur, owner_user_id=owner, node_ids=node_ids)

    commitments_by_node: dict[int, int] = {}
    for record_type in COMMITMENT_TYPES:
        for row in records_mod.list_records(
                cur, record_type=record_type, owner_user_id=owner,
                limit=records_mod.MAX_LIMIT):
            row = dict(row)
            row["record_type"] = record_type
            if not _is_open(row):
                continue
            for ref in row.get("related_entity_ids") or []:
                parsed = evidence.parse_ref(ref)
                if parsed and parsed[0] == "node":
                    commitments_by_node[parsed[1]] = commitments_by_node.get(parsed[1], 0) + 1

    needle = str(query or "").strip().lower()[:MAX_NAME_CHARS]
    out = []
    for node in nodes:
        node_id = int(node["id"])
        values = contact.get(node_id, {})

        def held(fact_type: str, _values: dict = values) -> str:
            return str((_values.get(fact_type) or {}).get("value") or "")

        row = {
            "node_id": node_id,
            "person_id": node_id,
            "ref": evidence.format_ref("node", node_id),
            "name": held(FACT_NAME),
            "role": held(FACT_ROLE),
            "phone": held(FACT_PHONE),
            "email": held(FACT_EMAIL),
            "username": held(FACT_USERNAME),
            "pulsesoc_user_id": account_id_from_ref(node.get("external_ref")),
            "photo_media_id": held(FACT_PHOTO),
            "source": held(FACT_SOURCE) or SOURCE_OTHER,
            "favorite": held(FACT_FAVORITE) == "true",
            "domain": node.get("domain") or "",
            "sensitivity": node.get("sensitivity") or "",
            "created_at": node.get("created_at") or "",
            "updated_at": node.get("updated_at") or "",
            "open_commitments": commitments_by_node.get(node_id, 0),
            "connections": len(graph_mod.neighbors(
                cur, owner_user_id=owner, node_id=node_id,
                direction=graph_mod.DIRECTION_BOTH)),
        }
        if favorites_only and not row["favorite"]:
            continue
        if not _matches_query(row, needle):
            continue
        out.append(row)

    order = str(sort or SORT_RECENT).strip().lower()
    if order not in SORTS:
        order = SORT_RECENT
    if order == SORT_NAME:
        out.sort(key=lambda r: (r["name"].lower(), -r["node_id"]))
    elif order == SORT_ACTIVITY:
        out.sort(key=lambda r: (r["open_commitments"], r["connections"],
                                r["node_id"]), reverse=True)
    # Favourites lead every ordering; within them the chosen order still holds.
    out.sort(key=lambda r: 0 if r["favorite"] else 1)
    return out


def _fact_view(row: dict) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "ref": evidence.format_ref("fact", int(row.get("id") or 0)),
        "fact_type": row.get("fact_type") or "",
        "value": str(row.get("typed_value") or ""),
        "value_type": row.get("value_type") or "",
        "provenance_type": row.get("provenance_type") or "",
        "observed_at": row.get("observed_at") or "",
        "freshness": row.get("freshness") or {},
    }


def _record_view(row: dict) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "ref": row.get("ref") or "",
        "record_type": row.get("record_type") or "",
        "title": row.get("title") or "",
        "status": row.get("status") or "",
        "due_at": row.get("due_at") or row.get("deadline_at") or None,
        "created_at": row.get("created_at") or "",
        "open": _is_open(row),
    }


def profile(cur, *, owner_user_id: int, node_id: object) -> dict[str, Any] | None:
    """Everything the Office holds about one person, each line cited."""
    owner = int(owner_user_id or 0)
    person = _person_node(cur, owner_user_id=owner, node_id=node_id)
    if person is None:
        return None
    person_id = int(person["id"])
    node_ref = evidence.format_ref("node", person_id)

    fact_rows = facts_mod.list_facts_for_subjects(
        cur, owner_user_id=owner, subject_type=facts_mod.SUBJECT_NODE,
        subject_ids=[str(person_id)])
    who = person_summary(cur, owner_user_id=owner, node_id=person_id) or {}

    edges = graph_mod.neighbors(
        cur, owner_user_id=owner, node_id=person_id,
        direction=graph_mod.DIRECTION_BOTH)
    citing = _records_citing(
        cur, owner_user_id=owner, node_ref=node_ref,
        record_types=tuple(RECORD_KINDS))
    commitments = [r for r in citing if r["record_type"] in COMMITMENT_TYPES]

    timeline: list[dict[str, Any]] = []
    for row in fact_rows:
        view = _fact_view(row)
        timeline.append({"at": view["observed_at"], "kind": "fact",
                         "ref": view["ref"],
                         "label": f"{view['fact_type']}: {view['value']}"})
    for edge in edges:
        timeline.append({
            "at": str(edge.get("created_at") or ""), "kind": "edge",
            "ref": evidence.format_ref("edge", int(edge.get("id") or 0)),
            "label": f"{edge.get('relation_type') or ''} "
                     f"{edge.get('other_node_type') or ''}".strip(),
        })
    for row in citing:
        timeline.append({"at": str(row.get("created_at") or ""),
                         "kind": row["record_type"].lower(), "ref": row["ref"],
                         "label": row.get("title") or ""})
    timeline.sort(key=lambda item: item["at"], reverse=True)

    audit.record(
        cur, actor_user_id=owner, owner_user_id=owner,
        action=audit.ACTION_GRAPH_READ, object_type=model.NODE_PERSON,
        object_id=str(person_id), purpose="user_request",
        result_count=len(fact_rows) + len(edges) + len(citing),
    )

    return {
        **who,
        "node_id": person_id,
        "ref": node_ref,
        "facts": [_fact_view(row) for row in fact_rows],
        "connections": [{
            "edge_id": int(edge.get("id") or 0),
            "ref": evidence.format_ref("edge", int(edge.get("id") or 0)),
            "relation_type": edge.get("relation_type") or "",
            "other_node_id": int(edge.get("other_node_id") or 0),
            "other_node_type": edge.get("other_node_type") or "",
        } for edge in edges],
        "commitments": [_record_view(row) for row in commitments],
        "records": [_record_view(row) for row in citing],
        "timeline": timeline[:MAX_TIMELINE],
    }


def prepare_briefing(cur, *, owner_user_id: int, node_id: object) -> dict[str, Any] | None:
    """The deterministic "before you meet them" view, every section cited.

    Built entirely from :func:`profile`; persists nothing, asserts nothing.
    The evidence list is the union of every ref the sections quote, so a
    reader can walk from any line to the row behind it.
    """
    owner = int(owner_user_id or 0)
    data = profile(cur, owner_user_id=owner, node_id=node_id)
    if data is None:
        return None

    open_commitments = [c for c in data["commitments"] if c["open"]]
    recent = data["timeline"][:10]
    refs = evidence.normalize_refs(
        [data["ref"]]
        + [f["ref"] for f in data["facts"]]
        + [c["ref"] for c in open_commitments]
        + [item["ref"] for item in recent])

    audit.record(
        cur, actor_user_id=owner, owner_user_id=owner,
        action=audit.ACTION_CONTEXT_RETRIEVED, object_type=model.NODE_PERSON,
        object_id=str(data["node_id"]), purpose="briefing_candidate",
        result_count=len(refs),
    )

    return {
        "person": {
            "node_id": data["node_id"], "ref": data["ref"],
            "name": data["name"], "role": data["role"],
            "domain": data["domain"],
        },
        "known_facts": data["facts"],
        "open_commitments": open_commitments,
        "recent_activity": recent,
        "connections": data["connections"],
        "evidence": refs,
        "generated_from": "private_office_records",
    }
