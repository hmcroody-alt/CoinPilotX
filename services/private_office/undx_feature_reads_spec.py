"""The UNDX surface for the five shipped Private Office features.

Documents, people, briefings, shield and the concierge desk each already have
a member-facing engine and an HTTP surface. This module gives the agent a
*read* of each — and only a read — declared once, here, with the registry,
the policy table, the knowledge map and the executor table all deriving from
this vocabulary the same way the Batch C record views derive from
``undx_records_spec``. The reasoning is unchanged: three registration surfaces
that agree by construction cannot drift apart by review.

What the specs commit to
------------------------
* **Read only, all five.** UNDX may look at the Office; it may not act on it.
  Uploading a document, adding a person, generating a briefing, acknowledging
  a finding and filing a concierge request all stay deliberate acts on the
  member's own screen — every one of those writes has provenance and several
  have consequences a model must not be able to trigger from a paraphrase.
* **No fabricated humans, even here.** The concierge read carries the same
  ``desk`` staffing block the HTTP surface carries. An agent asked "what is
  my concierge doing" answers from a payload that says UNSTAFFED when nobody
  is on the roster, so the model cannot imply a human who does not exist.
* **No field names an account.** Owner scope is structural: the executor
  passes the authenticated session id and the argument schema has nowhere to
  put anybody else.
* **Each capability gates on its own feature id.** The documents read refuses
  when document intelligence is dark, not when some sibling is; a kill switch
  turns off exactly the reads it names.
"""

from __future__ import annotations

from typing import Any

from services.private_office import audit as _audit

MAX_LIMIT = 25
DEFAULT_LIMIT = 10

#: One capability per feature. The vocabulary the three registration surfaces
#: derive from — typed exactly once.
#:
#: ``native_route`` is the Private Office hub for all five, because the hub is
#: the only screen that exists today and the knowledge map refuses a deep link
#: with no screen behind it. Repoint each at its own screen when that screen
#: ships — the map's screen table will hold you to it.
CAPABILITIES: tuple[dict, ...] = (
    {
        "capability_id": "private.documents.list",
        "feature_id": "private_office.document.extraction",
        "description": "List the authenticated member's own private documents and their extraction state",
        "intents": ("my documents", "what did i upload", "my private files",
                    "status of my document", "what was extracted"),
        "native_route": "/pulse/private-office",
        "backend_route": "GET /api/private-office/documents",
        "flag_env": "PRIVATE_DOCUMENTS_ENABLED",
        "audit_action": _audit.ACTION_DOCUMENT_READ,
        "object_type": "DOCUMENT_LIST",
    },
    {
        "capability_id": "private.people.list",
        "feature_id": "relationship_intelligence",
        "description": "List the people recorded in the member's own private office directory",
        "intents": ("my people", "who is in my private office",
                    "my relationship directory", "who do i owe something to",
                    "my contacts in the office"),
        "native_route": "/pulse/private-office",
        "backend_route": "GET /api/private-office/relationships",
        "flag_env": "PRIVATE_RELATIONSHIPS_ENABLED",
        "audit_action": _audit.ACTION_GRAPH_READ,
        "object_type": "PERSON_DIRECTORY",
    },
    {
        "capability_id": "private.briefings.list",
        "feature_id": "private_briefings",
        "description": "List the member's own private briefings, newest first",
        "intents": ("my briefings", "my latest briefing", "brief me",
                    "what did my office prepare", "my morning briefing"),
        "native_route": "/pulse/private-office",
        "backend_route": "GET /api/private-office/briefings",
        "flag_env": "PRIVATE_BRIEFINGS_ENABLED",
        "audit_action": _audit.ACTION_BRIEFING_READ,
        "object_type": "BRIEFING_LIST",
    },
    {
        "capability_id": "private.shield.posture",
        "feature_id": "private_shield",
        # "recorded"/"open" language on purpose: the posture reports what the
        # internal scan found and is explicit about what no external provider
        # has checked. It does not reassure.
        "description": "Report the member's own Private Shield posture: open findings and what has not been checked",
        "intents": ("my shield", "am i exposed", "my open findings",
                    "what has my shield found", "my security posture"),
        "native_route": "/pulse/private-office",
        "backend_route": "GET /api/private-office/shield",
        "flag_env": "PRIVATE_SHIELD_ENABLED",
        "audit_action": _audit.ACTION_SHIELD_READ,
        "object_type": "SHIELD_POSTURE",
    },
    {
        "capability_id": "private.concierge.desk",
        "feature_id": "human_concierge",
        "description": "Show the member's own concierge desk: staffing status and their requests",
        "intents": ("my concierge", "my concierge requests", "is anyone on my request",
                    "status of my concierge request", "what is my office handling"),
        "native_route": "/pulse/private-office",
        "backend_route": "GET /api/private-office/concierge",
        "flag_env": "PRIVATE_CONCIERGE_ENABLED",
        "audit_action": _audit.ACTION_CONCIERGE_READ,
        "object_type": "REQUEST_LIST",
    },
)

#: The shape all five declare. Nothing here can name an account, a table or a
#: row in someone else's office.
FIELDS: tuple[dict, ...] = (
    {"name": "limit", "type": "int", "required": False,
     "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT},
)

RISK = "read_only"
CONFIRMATION = "never"
PERMISSION = "self_account_only"
AUDIT_CATEGORY = "private_feature_read"


def tool_name(capability_id: str) -> str:
    """``private.shield.posture`` → ``pulsesoc.private_shield.posture``."""
    head, _, tail = str(capability_id).rpartition(".")
    return "pulsesoc." + head.replace(".", "_") + "." + tail


def executor_name(capability_id: str) -> str:
    return str(capability_id).replace(".", "_")


def spec_for(capability_id: str) -> dict | None:
    wanted = str(capability_id or "")
    for entry in CAPABILITIES:
        if entry["capability_id"] == wanted:
            return entry
    return None


def _bounded_limit(arguments: dict | None) -> int:
    args = dict(arguments or {})
    try:
        limit = int(args.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def execute_capability(
    cur,
    *,
    capability_id: str,
    owner_user_id: int,
    arguments: dict | None = None,
) -> dict:
    """The one service hook the executors call.

    ``owner_user_id`` is the authenticated session's id; the argument schema
    has nowhere to carry another one. The feature gate (``access.decide`` on
    the spec's own ``feature_id``) and the office lock both run in the
    executor before this is reached — this function does the read, audits it,
    and adds no second authorization layer for the first one to disagree with.
    """
    spec = spec_for(capability_id)
    if spec is None:
        return {"ok": False, "denied": "unknown_capability",
                "records": [], "counts": {"returned": 0}, "extras": {}}

    owner = int(owner_user_id or 0)
    limit = _bounded_limit(arguments)
    extras: dict[str, Any] = {}

    if spec["capability_id"] == "private.documents.list":
        from services.private_office import documents as _documents
        records = [_documents.public_view(doc) for doc in
                   _documents.list_documents(cur, owner_user_id=owner, limit=limit)]
    elif spec["capability_id"] == "private.people.list":
        from services.private_office import relationships as _relationships
        records = _relationships.directory(cur, owner_user_id=owner, limit=limit)
    elif spec["capability_id"] == "private.briefings.list":
        from services.private_office import briefings as _briefings
        records = _briefings.list_briefings(cur, owner_user_id=owner, limit=limit)
    elif spec["capability_id"] == "private.shield.posture":
        from services.private_office import shield as _shield
        records = _shield.list_findings(
            cur, owner_user_id=owner,
            statuses=list(_shield.OPEN_STATUSES), limit=limit)
        # The posture block carries the external-coverage honesty: what no
        # provider has checked is named, not implied to be clean.
        extras["posture"] = _shield.posture(cur, owner_user_id=owner)
    elif spec["capability_id"] == "private.concierge.desk":
        from services.private_office import concierge as _concierge
        records = _concierge.list_requests(cur, owner_user_id=owner, limit=limit)
        # Staffing truth rides on every payload, agent-facing included.
        extras["desk"] = _concierge.desk_status()
    else:  # pragma: no cover - CAPABILITIES and this dispatch move together
        return {"ok": False, "denied": "unknown_capability",
                "records": [], "counts": {"returned": 0}, "extras": {}}

    _audit.record(
        cur,
        actor_user_id=owner,
        owner_user_id=owner,
        action=spec["audit_action"],
        object_type=spec["object_type"],
        purpose="undx_context",
        result_count=len(records),
    )
    return {"ok": True, "records": records,
            "counts": {"returned": len(records)}, "extras": extras}
