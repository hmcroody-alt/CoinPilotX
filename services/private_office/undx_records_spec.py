"""Batch C — the UNDX surface for the record primitives, declared but not wired.

Status: **UNDX_WIRING_DEFERRED_DUE_TO_CONCURRENT_SECURITY_WORK.**

Nothing in this module registers anything. It declares what the six read
capabilities *will* be, and it provides the one service hook they will call, so
that when the Private Office security boundary stops moving the wiring is three
edits in the files that own registration — ``undx_capability_registry``,
``undx_policy``, ``undx_knowledge_map`` — and not a design conversation.

Why a spec module rather than a registration
--------------------------------------------
The registry, the policy table and the knowledge map are the authorization
surface, and they are cross-checked against each other precisely so that a
capability cannot exist in one and be missing from another. Adding six entries
to three files that another mission is actively editing is how that cross-check
gets resolved by whoever merges last. Declaring them here instead costs one
follow-up commit and cannot produce a half-registered capability.

The alternative that must not happen is a second route or a second executor
table "just for now". A temporary parallel surface is a surface nobody gates,
and it outlives the temporary.

What the specs commit to
------------------------
* **Read only, all six.** There is no companion write capability, for the same
  reason ``private.facts.list`` has none: a record written by an agent from a
  conversation has a model's paraphrase as its provenance, and every row in
  this store must be able to answer "why does PulseSoc know this?". An
  obligation is worse than a fact in this respect, because it also carries a
  date somebody may act on.
* **No field names an account.** Owner scope is structural, not enforced: the
  only owner these capabilities can reach is the caller, because there is
  nothing in the schema to put another id into.
* **The view name is a closed enum.** The model may name ``obligations``; it
  may not name a table. :data:`VIEW_CHOICES` is derived from
  ``retrieval.RECORD_VIEWS`` rather than restated, because a vocabulary typed
  twice eventually disagrees with itself and the failure is quiet — a view the
  model may ask for that the reader then resolves to nothing, indistinguishable
  from an empty office.
* **Bounds are in the schema.** ``limit`` is capped in the field spec as well as
  in the service, so an over-large request is refused at the edge rather than
  silently trimmed in the middle.
"""

from __future__ import annotations

from services.private_office import retrieval as _retrieval

#: Set when the capabilities are registered. Read by the test that keeps this
#: module honest: while it is False, the suite asserts the capabilities are
#: *absent* from the registry, so "deferred" cannot quietly become "forgotten"
#: or "half-done".
WIRING_COMPLETE = False

DEFERRAL_REASON = "UNDX_WIRING_DEFERRED_DUE_TO_CONCURRENT_SECURITY_WORK"

#: Derived, never restated. See the module docstring.
VIEW_CHOICES: tuple[str, ...] = tuple(sorted(_retrieval.RECORD_VIEWS))

MAX_LIMIT = 25
DEFAULT_LIMIT = 10

#: One capability per view. Six capabilities rather than one with a ``view``
#: argument, because the model routes on intent and "what do I owe" and "what
#: has my office flagged as a risk" are different questions a member asks in
#: different words. The executor is shared; only the bound view differs.
CAPABILITIES: tuple[dict, ...] = (
    {
        "capability_id": "private.obligations.list",
        "view": _retrieval.VIEW_OBLIGATIONS,
        "description": "List the authenticated member's own recorded obligations",
        "intents": ("what do i owe", "my obligations", "what is due",
                    "upcoming deadlines", "what do i have to pay"),
        "native_route": "/pulse/private-office/obligations",
    },
    {
        "capability_id": "private.events.list",
        "view": _retrieval.VIEW_EVENTS,
        "description": "List the authenticated member's own recorded domain events",
        "intents": ("what has happened", "my recorded events", "my history",
                    "what did my office record"),
        "native_route": "/pulse/private-office/events",
    },
    {
        "capability_id": "private.decisions.list",
        "view": _retrieval.VIEW_DECISIONS,
        "description": "List the authenticated member's own open and past decisions",
        "intents": ("what am i deciding", "my open decisions",
                    "what have i decided", "my decision log"),
        "native_route": "/pulse/private-office/decisions",
    },
    {
        "capability_id": "private.requests.list",
        "view": _retrieval.VIEW_REQUESTS,
        "description": "List the authenticated member's own concierge requests",
        "intents": ("my requests", "what did i ask for",
                    "status of my request", "my concierge queue"),
        "native_route": "/pulse/private-office/requests",
    },
    {
        "capability_id": "private.risks.list",
        "view": _retrieval.VIEW_RISKS,
        "description": "List the authenticated member's own recorded risks",
        "intents": ("my risks", "what is exposed", "what should i worry about",
                    "what has my office flagged"),
        "native_route": "/pulse/private-office/risks",
    },
    {
        "capability_id": "private.opportunities.list",
        "view": _retrieval.VIEW_OPPORTUNITIES,
        # Deliberately "recorded", not "recommended". This capability reports
        # that something exists and was flagged as possibly relevant by a named
        # source. It does not rank, endorse, or suggest acting.
        "description": "List opportunities recorded in the member's own private office",
        "intents": ("my opportunities", "what has been flagged for me",
                    "what is in my opportunity list"),
        "native_route": "/pulse/private-office/opportunities",
    },
)

#: The shape every one of the six declares. No field here names an account, a
#: table, or a record id: this is a list capability over the caller's own
#: office, and widening it is a schema change somebody has to make on purpose.
FIELDS: tuple[dict, ...] = (
    {"name": "status", "type": "string", "required": False, "default": ""},
    {"name": "limit", "type": "int", "required": False,
     "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT},
)

#: Constants the registration commit will need, kept here so the three files it
#: touches agree by construction rather than by review.
RISK = "read_only"
CONFIRMATION = "never"
PERMISSION = "self_account_only"
AUDIT_CATEGORY = "private_records_read"
EXECUTOR = "private_records_list"
SERVICE_ROUTE = "services.private_office.retrieval.retrieve_records"


def tool_name(capability_id: str) -> str:
    """``private.risks.list`` → ``pulsesoc.private_risks.list``.

    The same transformation ``private.facts.list`` already uses, expressed once
    so the registry entry and the policy table cannot spell it differently.
    """
    head, _, tail = str(capability_id).rpartition(".")
    return "pulsesoc." + head.replace(".", "_") + "." + tail


def capability_for_view(view: str) -> dict | None:
    wanted = str(view or "").strip().lower()
    for spec in CAPABILITIES:
        if spec["view"] == wanted:
            return spec
    return None


def execute_view(
    cur,
    *,
    capability_id: str,
    owner_user_id: int,
    arguments: dict | None = None,
    intent: str = _retrieval.INTENT_GENERAL,
) -> dict:
    """The service hook the future executor will call. One line of real work.

    ``owner_user_id`` is the authenticated session's id and is not reachable
    from ``arguments`` — the executor will pass the session, and this signature
    makes it impossible to pass anything else by accident. Everything else is
    the gate that ``retrieve_records`` already runs; this function adds no
    authorization of its own, because a second gate is a second place for the
    two to disagree.
    """
    args = dict(arguments or {})
    spec = None
    for candidate in CAPABILITIES:
        if candidate["capability_id"] == str(capability_id):
            spec = candidate
            break
    if spec is None:
        # Refused rather than defaulted, for the same reason an unknown view is:
        # substituting a different collection for the one that was asked for
        # hides the bug and the probe alike.
        return {"ok": False, "denied": "unknown_capability", "records": [],
                "counts": {"returned": 0}}

    limit = args.get("limit", DEFAULT_LIMIT)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    result = _retrieval.retrieve_records(
        cur,
        owner_user_id=int(owner_user_id or 0),
        view=spec["view"],
        actor_user_id=int(owner_user_id or 0),
        intent=intent,
        purpose="undx_context",
        statuses=args.get("status") or None,
        limit=limit,
    )
    return {"ok": not result["denied"], **result}
