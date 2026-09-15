"""The UNDX surface over the shipped Private Office features.

Documents, people, briefings, shield and the concierge desk each already have
a member-facing engine and an HTTP surface. This module gives the agent a
*read* of each — and only a read — declared once, here, with the registry,
the policy table, the knowledge map and the executor table all deriving from
this vocabulary the same way the Batch C record views derive from
``undx_records_spec``. The reasoning is unchanged: three registration surfaces
that agree by construction cannot drift apart by review.

Document intelligence carries two reads rather than one. "What did I upload?"
and "what did those uploads say, and where does each answer come from?" are
different questions with different payloads, and one read serving both would
have to either put fact values in a file list or leave the citations
unreachable. They share a feature id and a kill switch, so the pairing costs
nothing in gating: darkening document intelligence darkens both and no
sibling. What it does cost is the old one-row-per-feature invariant, which was
a property of the first five capabilities rather than a rule worth keeping.

What the specs commit to
------------------------
* **Read only, every one.** UNDX may look at the Office; it may not act on it.
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

#: The vocabulary every registration surface derives from — typed exactly once.
#: At least one capability per feature; document intelligence has two.
#:
#: ``native_route`` names each feature's own screen; they are literal routes in
#: linking.ts and declared in the knowledge map's screen table, which refuses a
#: deep link with no screen behind it. ``native_screen`` and ``service_module``
#: live here for the same reason everything else does: the knowledge map used
#: to hold both as its own hardcoded dictionaries keyed by capability id, which
#: meant adding a capability raised a ``KeyError`` from an unrelated module at
#: import time — a duplication that announced itself only when someone tripped
#: over it.
CAPABILITIES: tuple[dict, ...] = (
    # Reduced to one. Document intelligence, private briefings, private shield
    # and the concierge desk were withdrawn from the product, and a capability
    # the agent can still describe is a capability the agent will still offer:
    # UNDX discovery is where a retired feature comes back to life as a promise
    # nothing can keep. The refusal in the executor is the backstop, not the
    # plan.
    {
        "capability_id": "private.people.list",
        "feature_id": "relationship_intelligence",
        "description": "List the people recorded in the member's own private office directory",
        "intents": ("my people", "who is in my private office",
                    "my relationship directory", "who do i owe something to",
                    "my contacts in the office"),
        "native_route": "/pulse/private-office/people",
        "backend_route": "GET /api/private-office/relationships",
        "flag_env": "PRIVATE_RELATIONSHIPS_ENABLED",
        "audit_action": _audit.ACTION_GRAPH_READ,
        "object_type": "PERSON_DIRECTORY",
        "native_screen": "PrivatePeople",
        "service_module": "relationships",
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
    """``private.people.list`` → ``pulsesoc.private_people.list``."""
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

    if spec["capability_id"] == "private.people.list":
        from services.private_office import relationships as _relationships
        records = _relationships.directory(cur, owner_user_id=owner, limit=limit)
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
