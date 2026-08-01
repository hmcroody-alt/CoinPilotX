"""Owner-scoped memory: the isolation rule, owned by a module instead of a habit.

The Foundation map recorded ``memory_isolation`` as the one UNOWNED responsibility, and
was specific about why. PulseSoc already persists everything PART 7 calls memory, and
every one of those tables already carries an owner column that every existing query
already filters on. Nothing is broken. What was missing is that the guarantee lived in
the *discipline of each call site* — roughly a hundred hand-written ``WHERE user_id=?``
clauses, two of which use ``owner_user_id`` instead, any one of which is one careless
edit away from returning somebody else's history.

This module does not add storage. It adds the one thing a convention cannot have: a
place where forgetting is impossible.

The mechanism is small. A caller writes the owner clause as the literal marker
``{owner}`` and never supplies its value::

    memory.read(scope, MemoryKind.PREFERENCE, cur,
                "SELECT body FROM pulse_ai_user_memory WHERE user_id = {owner} "
                "AND status = 'active'")

The layer renders the marker to a placeholder and binds ``scope.owner_id`` itself. A
statement with no marker is refused, so an unfiltered read is not a bug that reaches
production — it is a statement this module declines to run. A caller cannot pass an
owner id of their choosing, because there is no parameter for one.

Three deliberate non-goals:

*No new tables.* Every kind below names a table that exists today, with the owner
column it actually has. Inventing a parallel store is how a system ends up with two
answers to "what does this account remember".

*No cross-kind reach.* A scope opened for preferences cannot address the messages
table, even with a correct owner clause, because a JOIN is exactly how a narrow grant
becomes a wide one.

*No configurable isolation.* ``UNDX_MEMORY_FAIL_CLOSED`` chooses between two safe
outcomes — fail the request, or answer without memory — and there is no third setting
that returns the data anyway. A flag that can be set to "leak" is a flag that will be.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from . import config as brain_config

__all__ = [
    "MemoryClass",
    "MemoryKind",
    "MemoryResult",
    "Scope",
    "CLASSES",
    "forget",
    "open_scope",
    "owner_id",
    "read",
    "write",
]


class MemoryKind(str, Enum):
    """What is remembered, in the terms the storage already uses.

    Seven kinds, each mapped to a table this repository writes today. Naming a kind
    that no table backs would make this an aspiration rather than a boundary.
    """

    #: Turn-by-turn conversation. The largest and the most obviously personal.
    CONVERSATION = "conversation"
    #: Durable, user-approved preferences — the only kind held indefinitely.
    PREFERENCE = "preference"
    #: Plans and their steps: what UNDX intended to do and how far it got.
    TASK_STATE = "task_state"
    #: Claims with a source and a confidence, which is what makes them citable.
    FACT = "fact"
    #: Edges between entities the owner can see. Access policy lives on the edge.
    RELATIONSHIP = "relationship"
    #: Confirmations and their redemption — the record of what was authorised.
    APPROVAL = "approval"
    #: What happened, for later analysis. Retained because corrections need a subject.
    LEARNING_EVENT = "learning_event"


@dataclass(frozen=True)
class MemoryClass:
    """One kind, bound to the storage and the flag that govern it."""

    kind: MemoryKind
    table: str
    #: Two spellings exist in the schema. Callers should not have to remember which,
    #: and a caller who guesses wrong writes a statement that silently matches nothing
    #: — or, on a table where both columns exist, the wrong rows.
    owner_column: str
    purpose: str
    #: Extra flag beyond ``UNDX_BRAIN_MEMORY_ENABLED``. Empty means the master switch
    #: is the only gate.
    flag: str = ""


CLASSES: tuple[MemoryClass, ...] = (
    MemoryClass(
        MemoryKind.CONVERSATION, "pulse_ai_messages", "user_id",
        "Prior turns, used to make a reply continuous rather than amnesiac.",
    ),
    MemoryClass(
        MemoryKind.PREFERENCE, "pulse_ai_user_memory", "user_id",
        "Standing preferences the owner explicitly approved keeping.",
        flag="UNDX_MEMORY_USER_PREFERENCES_ENABLED",
    ),
    MemoryClass(
        MemoryKind.TASK_STATE, "pulse_ai_missions", "user_id",
        "Objectives and their progress, so a resumed request is not restarted.",
    ),
    MemoryClass(
        MemoryKind.FACT, "pulse_ai_truth_facts", "owner_user_id",
        "Claims with a source and a confidence, retained so they can be cited.",
    ),
    MemoryClass(
        MemoryKind.RELATIONSHIP, "pulse_ai_knowledge_edges", "owner_user_id",
        "Edges between entities, carrying their own access policy.",
    ),
    MemoryClass(
        MemoryKind.APPROVAL, "pulse_ai_confirmations", "user_id",
        "What was authorised, by whom, and whether it has been spent.",
    ),
    MemoryClass(
        MemoryKind.LEARNING_EVENT, "pulse_ai_learning_events", "user_id",
        "What happened, kept so a correction has something to attach to.",
    ),
)

BY_KIND: dict[MemoryKind, MemoryClass] = {item.kind: item for item in CLASSES}

#: Every table this module governs. Used to reject a statement that reaches a memory
#: table other than the one its kind names — the JOIN case.
GOVERNED_TABLES: frozenset[str] = frozenset(item.table for item in CLASSES)

OWNER_MARKER = "{owner}"

_READ_VERBS = ("select", "with")
_WRITE_VERBS = ("insert", "update", "delete")
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
#: ``--`` and ``/* */`` can hide a second clause from a human reviewer while the driver
#: still sees it. Nothing legitimate in this layer needs a comment inside a statement.
_COMMENT = re.compile(r"--|/\*|\*/")


@dataclass(frozen=True)
class Scope:
    """Permission to touch one account's memory, and nothing else.

    There is no way to widen a scope after it is opened, and no scope that covers more
    than one owner. A request that needs two accounts' memory needs two scopes and an
    explicit reason, which is the point at which somebody should be asked.
    """

    owner_id: int = 0
    ok: bool = False
    #: Whether an operation that cannot proceed should fail the request or degrade it.
    #: Never whether the data is returned anyway — see the module docstring.
    fail_closed: bool = True
    enabled: frozenset[MemoryKind] = frozenset()
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        return self.ok

    def allows(self, kind: MemoryKind) -> bool:
        return self.ok and kind in self.enabled


@dataclass(frozen=True)
class MemoryResult:
    """The outcome of one memory operation. Never an exception, never a bare list.

    ``denied`` and a failed read are deliberately distinguishable. "There is nothing
    remembered" and "I was not allowed to look" produce very different honest replies,
    and a caller handed an empty list cannot tell them apart.
    """

    ok: bool = False
    kind: MemoryKind | None = None
    rows: tuple[dict[str, Any], ...] = ()
    rowcount: int = 0
    denied: bool = False
    reason: str = ""
    #: True when the caller should fail the request rather than answer without this
    #: memory. Set from the scope's ``fail_closed``.
    fatal: bool = False

    def __bool__(self) -> bool:
        return self.ok


def open_scope(owner_id: Any, *, env: Mapping[str, str] | None = None) -> Scope:
    """Establish the one owner every subsequent operation is confined to.

    Refuses rather than defaults. An owner id that is absent, zero, negative, or not an
    integer is not a request to read everything — it is a lookup that went wrong
    upstream, and the correct response to "whose memory?" being unanswerable is to
    stop.
    """
    values = brain_config.resolve(env).values
    fail_closed = bool(values.get("UNDX_MEMORY_FAIL_CLOSED", True))

    resolved = _owner_id(owner_id)
    if resolved is None:
        return Scope(
            ok=False,
            fail_closed=fail_closed,
            reason=f"owner scope could not be established from {owner_id!r}",
        )

    if not bool(values.get("UNDX_BRAIN_ENABLED", False)):
        return Scope(
            owner_id=resolved, ok=False, fail_closed=fail_closed,
            reason="the Brain layer is disabled",
        )
    if not bool(values.get("UNDX_BRAIN_MEMORY_ENABLED", False)):
        return Scope(
            owner_id=resolved, ok=False, fail_closed=fail_closed,
            reason="Brain-owned memory is disabled",
        )

    enabled = {
        item.kind for item in CLASSES
        if not item.flag or bool(values.get(item.flag, False))
    }
    notes = [
        f"{item.kind.value} withheld: {item.flag} is off"
        for item in CLASSES
        if item.flag and not bool(values.get(item.flag, False))
    ]
    return Scope(
        owner_id=resolved,
        ok=True,
        fail_closed=fail_closed,
        enabled=frozenset(enabled),
        notes=tuple(notes),
    )


def read(
    scope: Scope,
    kind: MemoryKind,
    cur: Any,
    sql: str,
    params: Sequence[Any] = (),
) -> MemoryResult:
    """Run one owner-bound SELECT and return its rows."""
    return _run(scope, kind, cur, sql, params, verbs=_READ_VERBS, writing=False)


def write(
    scope: Scope,
    kind: MemoryKind,
    cur: Any,
    sql: str,
    params: Sequence[Any] = (),
) -> MemoryResult:
    """Run one owner-bound INSERT, UPDATE, or DELETE.

    An INSERT carries its owner in the values rather than a WHERE clause; the marker
    requirement is the same either way, which is why it is a marker rather than a
    generated clause.
    """
    return _run(scope, kind, cur, sql, params, verbs=_WRITE_VERBS, writing=True)


def forget(scope: Scope, kind: MemoryKind, cur: Any) -> MemoryResult:
    """Delete everything of one kind for this owner.

    Present as a first-class operation because deletion written by hand at a call site
    is the single most dangerous statement in this file: an owner clause omitted from a
    SELECT shows somebody the wrong data, and the same omission in a DELETE destroys
    everybody's.
    """
    item = BY_KIND.get(kind)
    if item is None:
        return _deny(scope, kind, f"{kind!r} is not a governed memory kind")
    return write(
        scope, kind, cur,
        f"DELETE FROM {item.table} WHERE {item.owner_column} = {OWNER_MARKER}",
    )


# ---------------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------------


def owner_id(raw: Any) -> int | None:
    """Resolve an owner id, or refuse. Never guess.

    Public because it is the isolation rule in its smallest form, and because every
    other module in this package that has to answer "whose account is this?" was
    answering it separately. One implementation means one place for this to be wrong.

    Deliberately narrower than ``int(raw)``. Three inputs coerce to a *different real
    account* rather than to an error, which is the failure mode that matters here:

    * ``bool`` is an ``int`` subclass, so a stray truthy flag resolves to owner ``1`` —
      an account that exists and whose memory is not the caller's.
    * ``float`` truncates. ``3.7`` becomes owner ``3``. An owner id arriving as a float
      means something upstream did arithmetic on an identity, and the right answer is
      to stop, not to round.
    * A string of non-ASCII decimal digits parses. ``int("٩٩")`` is ``99`` and
      ``"٩٩".isdigit()`` agrees, so an earlier version of this function accepted it:
      account 99 is a real person, reached through a string that does not spell their
      id in any character a reviewer would recognise. ``int("1_0")`` is ``10`` for the
      same reason with a different cause — underscore separators are a convenience for
      Python *literals* and have no business in an identifier arriving from outside.

    So: a genuine ``int``, or a string of ASCII ``0``-``9`` with an optional sign.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, str):
        text = raw.strip()
        body = text[1:] if text.startswith(("+", "-")) else text
        if not body or not all("0" <= character <= "9" for character in body):
            return None
        value = int(body)
        if text.startswith("-"):
            return None
        return value if value > 0 else None
    return None


#: The historical spelling, kept so nothing inside this module has to change shape.
_owner_id = owner_id


def _deny(scope: Scope, kind: MemoryKind | None, reason: str) -> MemoryResult:
    return MemoryResult(
        ok=False, kind=kind, denied=True, reason=reason, fatal=scope.fail_closed
    )


def _run(
    scope: Scope,
    kind: MemoryKind,
    cur: Any,
    sql: str,
    params: Sequence[Any],
    *,
    verbs: tuple[str, ...],
    writing: bool,
) -> MemoryResult:
    if not isinstance(scope, Scope) or not scope.ok:
        reason = getattr(scope, "reason", "") or "no owner scope"
        return _deny(scope if isinstance(scope, Scope) else Scope(), kind, reason)

    item = BY_KIND.get(kind)
    if item is None:
        return _deny(scope, None, f"{kind!r} is not a governed memory kind")
    if kind not in scope.enabled:
        return _deny(scope, kind, f"{kind.value} memory is not enabled for this scope")

    text = str(sql or "")
    problem = _statement_problem(text, item, verbs)
    if problem:
        return _deny(scope, kind, problem)

    rendered, bound = _bind_owner(text, params, scope.owner_id)

    try:
        cur.execute(rendered, bound)
    except Exception as exc:
        # A failed statement is not a failed guarantee, and it is not this module's job
        # to decide what a caller does about a database error. It is this module's job
        # not to raise out of a memory read on the response path.
        return MemoryResult(
            ok=False, kind=kind,
            reason=f"{type(exc).__name__} while reading {item.table}"
            if not writing else f"{type(exc).__name__} while writing {item.table}",
            fatal=scope.fail_closed,
        )

    if writing:
        return MemoryResult(ok=True, kind=kind, rowcount=int(getattr(cur, "rowcount", 0) or 0))

    rows = tuple(dict(row) for row in (cur.fetchall() or ()))
    return MemoryResult(ok=True, kind=kind, rows=rows, rowcount=len(rows))


def _statement_problem(sql: str, item: MemoryClass, verbs: tuple[str, ...]) -> str:
    lowered = sql.lower()

    marker_count = sql.count(OWNER_MARKER)
    if marker_count == 0:
        return (
            f"statement does not bind the owner: expected {OWNER_MARKER} against "
            f"{item.table}.{item.owner_column}"
        )
    if marker_count > 1:
        # Not because two would be unsafe, but because the position the value binds to
        # would stop being obvious, and an isolation rule that needs careful reading is
        # one that gets read carelessly.
        return f"statement uses {OWNER_MARKER} {marker_count} times; exactly one is allowed"

    stripped = lowered.lstrip("( \t\r\n")
    if not stripped.startswith(verbs):
        return f"statement is not one of {', '.join(v.upper() for v in verbs)}"

    if ";" in sql.rstrip().rstrip(";"):
        return "statement contains more than one command"
    if _COMMENT.search(sql):
        return "statement contains a comment"

    words = set(_IDENTIFIER.findall(lowered))
    if item.table not in words:
        return f"statement does not name {item.table}"
    intruders = sorted((words & GOVERNED_TABLES) - {item.table})
    if intruders:
        return (
            f"statement reaches {', '.join(intruders)}, which this "
            f"{item.kind.value} scope does not cover"
        )

    # The marker has to be the owner column's comparison, not an incidental value
    # somewhere else in the statement. For anything with a WHERE clause, require the
    # column name to appear; an INSERT names it in the column list instead.
    if item.owner_column not in words:
        return f"statement does not name {item.owner_column}"

    return ""


def _bind_owner(sql: str, params: Sequence[Any], owner_id: int) -> tuple[str, tuple[Any, ...]]:
    """Render the marker to a placeholder and slot the owner into the right position.

    The caller never passes the owner value, so the count of ``?`` before the marker is
    the only thing that decides where it lands. Computing it here rather than asking the
    caller to order their parameters correctly is the whole point.
    """
    head, _, tail = sql.partition(OWNER_MARKER)
    before = head.count("?")
    supplied = tuple(params or ())
    bound = supplied[:before] + (int(owner_id),) + supplied[before:]
    return head + "?" + tail, bound
