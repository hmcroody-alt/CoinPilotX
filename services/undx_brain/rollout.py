"""Who the Brain is actually on for — the flags that said "QA only" and nothing asked.

:mod:`services.undx_brain.config` declares three variables about reach:

``UNDX_BRAIN_QA_ONLY``
    On by default, and its own docstring says why: "so that enabling the Brain in
    production does not, by itself, expose it to anybody."
``UNDX_BRAIN_ROLLOUT_PERCENT`` / ``UNDX_BRAIN_WRITES_ROLLOUT_PERCENT``
    Separate dials for reading and for writing, "because a read that is wrong is a bad
    answer and a write that is wrong is a changed account."

All three were read by nothing. That is a worse failure than a flag that does the wrong
thing, because a flag that does nothing looks exactly like a flag that is working: an
operator sets ``UNDX_BRAIN_QA_ONLY=1``, sees no errors, and now believes the Brain is
fenced off. It is the same shape as the trust floor that fell back to its default and
the planner ceilings that were declared and never consulted — a value that is stated,
believed, and not load-bearing.

This module is the thing that asks. It answers two questions and nothing else:
:func:`may_read` and :func:`may_write`.

Four decisions here are the *less* convenient option, and each is a real failure rather
than a hypothetical:

**The bucket is a digest, not ``hash()``.** Python salts string hashing per process,
so ``hash(f"brain:{user_id}") % 100`` puts the same person inside the rollout on one
gunicorn worker and outside it on the next. The symptom is not an error; it is a person
whose account behaves differently on alternate requests, which is close to
undiagnosable from a log. :func:`bucket` uses SHA-256, so the answer is the same in
every process, on every host, forever.

**Writes are capped by reads.** ``UNDX_BRAIN_WRITES_ROLLOUT_PERCENT=50`` beside
``UNDX_BRAIN_ROLLOUT_PERCENT=10`` reads like an obvious operator mistake, and taken
literally it hands forty percent of users a Brain that may change their account but may
not look at it first. The effective write percentage is ``min(write, read)``, so the
write cohort is always a subset of the read cohort, and the reduction is reported in
:func:`surface` rather than applied silently.

**QA-only means the percentages are not consulted.** Not "consulted and then
overridden" — an operator who has left ``UNDX_BRAIN_ROLLOUT_PERCENT=25`` from a previous
experiment and switches ``UNDX_BRAIN_QA_ONLY`` back on is asking for the experiment to
stop, and the honest reading of that is that the percentage stops mattering.

**Nobody is a member by default.** An absent, zero, float or non-numeric user id is
outside every cohort. The Brain's cohort is the existing ``UNDX_AGENT_QA_USER_IDS``
list, reused rather than duplicated: a second QA list is a second thing to keep in sync,
and the failure mode of two lists that disagree is that somebody is in QA for half the
system.

This module decides *reach*. It does not decide whether an action is permitted — that
is ``undx_agent_policy`` — and it never decides whether something worked, which is
:mod:`services.undx_brain.evidence`.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import config as brain_config
from . import memory

__all__ = [
    "Eligibility",
    "BUCKETS",
    "QA_USERS_ENV",
    "bucket",
    "in_qa_cohort",
    "may_read",
    "may_write",
    "surface",
]

#: How finely the population is divided. One hundred, because the flags are percentages
#: and a bucket count that is not 100 makes "25 percent" mean something other than 25
#: percent of accounts.
BUCKETS = 100

#: The cohort list. Owned by ``undx_agent_policy``; named here so the reuse is visible
#: at the top of the file rather than buried in a function, and held as a literal so a
#: failed import of that module cannot quietly change which variable is read.
QA_USERS_ENV = "UNDX_AGENT_QA_USER_IDS"

#: Salt for the bucket digest. Fixed and versioned in its own name: changing this string
#: reshuffles every account into a different bucket, which during a live rollout means
#: swapping who is in and who is out. That is occasionally what you want and never what
#: you want by accident, so it is a constant somebody has to edit deliberately.
_BUCKET_SALT = "undx-brain-rollout-v1"


@dataclass(frozen=True)
class Eligibility:
    """Whether one account reaches one Brain surface, and the reasoning behind it.

    ``allowed`` is the answer. The rest is here so that an operator asking "why is this
    account not seeing it" gets the actual reason instead of a boolean and a guess.
    """

    allowed: bool = False
    surface: str = ""
    #: ``""`` when allowed. Otherwise the specific gate that said no, so a log line can
    #: distinguish "the Brain is off" from "this person is outside a 5% rollout".
    reason: str = ""
    #: ``None`` when no bucket was computed — the master switch being off, or an
    #: unusable user id. ``None`` is not bucket zero, and conflating them would make
    #: every unidentified caller a member of every non-empty rollout.
    bucket: int | None = None
    percent: int = 0
    qa_only: bool = True
    qa_member: bool = False
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        return self.allowed


def bucket(user_id: Any) -> int | None:
    """Which of :data:`BUCKETS` slices this account falls in, or ``None``.

    Stable across processes, hosts and restarts, which is the whole requirement. The
    obvious implementations are both wrong in ways that do not raise:

    * ``hash(str(user_id)) % 100`` is randomised per process by ``PYTHONHASHSEED``, so
      an account moves in and out of the rollout between workers.
    * ``int(user_id) % 100`` is stable but sequential — account ids are handed out in
      order, so a one percent rollout selects a thin regular comb through the signup
      timeline, and every percentage-gated feature selects *the same* comb. Two
      independent one-percent experiments would then run on one identical person.

    ``None`` for anything that is not a usable account id. See :func:`_account_id`.
    """
    account = _account_id(user_id)
    if account is None:
        return None
    digest = hashlib.sha256(f"{_BUCKET_SALT}:{account}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % BUCKETS


def in_qa_cohort(user_id: Any, env: Mapping[str, str] | None = None) -> bool:
    """Whether this account is on the explicit QA list.

    Empty means nobody. There is deliberately no "an empty list means everyone"
    reading, because that turns a variable somebody forgot to set into a full
    production rollout — the exact accident the QA gate exists to prevent.
    """
    account = _account_id(user_id)
    if account is None:
        return False
    source = os.environ if env is None else env
    members = {
        part.strip()
        for part in str(source.get(QA_USERS_ENV, "") or "").split(",")
        if part.strip().isdigit()
    }
    return str(account) in members


def may_read(user_id: Any, env: Mapping[str, str] | None = None) -> Eligibility:
    """Whether this account may be answered through the Brain's read path."""
    return _decide(user_id, env, which="read")


def may_write(user_id: Any, env: Mapping[str, str] | None = None) -> Eligibility:
    """Whether this account may have a change planned for it by the Brain.

    Strictly narrower than :func:`may_read`. An account that cannot be *read* for by the
    Brain cannot be *written* for by it either — a plan built without the Brain's own
    retrieval is a plan built on something else, and shipping the write half of a
    feature ahead of the read half is not a rollout, it is a different product.
    """
    return _decide(user_id, env, which="write")


def surface(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """A snapshot of the rollout dials, safe to log and to show an admin.

    Reports ``writes_percent_effective`` alongside the configured value so that a
    write percentage that was capped by the read percentage is visible. A cap applied
    silently is indistinguishable, from the outside, from a cap that was never applied.
    """
    values = brain_config.resolve(env).values
    qa_only = bool(values.get("UNDX_BRAIN_QA_ONLY", True))
    reads = _percent(values.get("UNDX_BRAIN_ROLLOUT_PERCENT"))
    writes = _percent(values.get("UNDX_BRAIN_WRITES_ROLLOUT_PERCENT"))
    source = os.environ if env is None else env
    return {
        "brain_enabled": bool(values.get("UNDX_BRAIN_ENABLED", False)),
        "qa_only": qa_only,
        "qa_cohort_configured": bool(
            [p for p in str(source.get(QA_USERS_ENV, "") or "").split(",") if p.strip().isdigit()]
        ),
        "reads_percent": reads,
        "writes_percent": writes,
        "writes_percent_effective": min(writes, reads),
        "writes_percent_capped_by_reads": writes > reads,
        "percentages_consulted": not qa_only,
        "buckets": BUCKETS,
    }


def _decide(user_id: Any, env: Mapping[str, str] | None, *, which: str) -> Eligibility:
    # ``which`` rather than ``surface`` so the module-level :func:`surface` stays
    # reachable from inside this function. Shadowing it would work today and break the
    # first time somebody adds a call to it here.
    resolution = brain_config.resolve(env)
    values = resolution.values
    qa_only = bool(values.get("UNDX_BRAIN_QA_ONLY", True))
    qa_member = in_qa_cohort(user_id, env)
    notes: list[str] = []

    reads = _percent(values.get("UNDX_BRAIN_ROLLOUT_PERCENT"))
    if which == "write":
        configured = _percent(values.get("UNDX_BRAIN_WRITES_ROLLOUT_PERCENT"))
        percent = min(configured, reads)
        if configured > reads:
            notes.append(
                f"UNDX_BRAIN_WRITES_ROLLOUT_PERCENT={configured} exceeds "
                f"UNDX_BRAIN_ROLLOUT_PERCENT={reads}; the write cohort is capped at "
                f"{percent} so that nobody is given writes without reads"
            )
    else:
        percent = reads

    def answer(allowed: bool, reason: str, slot: int | None) -> Eligibility:
        return Eligibility(
            allowed=allowed,
            surface=which,
            reason="" if allowed else reason,
            bucket=slot,
            percent=percent,
            qa_only=qa_only,
            qa_member=qa_member,
            notes=tuple(notes),
        )

    if not bool(values.get("UNDX_BRAIN_ENABLED", False)):
        # Checked before the cohort, so that a QA member on a host where the Brain is
        # off is told the Brain is off rather than told they are in QA. Which of the
        # two is reported is the difference between checking a flag and checking a list.
        return answer(False, "UNDX_BRAIN_ENABLED is off", None)

    account = _account_id(user_id)
    if account is None:
        return answer(False, "no usable account id, so no cohort membership", None)

    if qa_member:
        return answer(True, "", bucket(account))

    if qa_only:
        # Note, deliberately, that the percentage is not consulted here at all rather
        # than consulted and overridden. An operator who leaves a percentage set from a
        # previous experiment and turns QA-only back on is asking for the experiment to
        # stop.
        return answer(False, "UNDX_BRAIN_QA_ONLY restricts the Brain to the QA cohort", None)

    slot = bucket(account)
    if slot is None:  # pragma: no cover - _account_id already guaranteed this
        return answer(False, "no usable account id, so no cohort membership", None)
    if percent <= 0:
        return answer(False, f"the {which} rollout is at 0 percent", slot)
    # ``<`` not ``<=``: buckets are 0..99, so ``slot < 100`` is everybody and
    # ``slot < 1`` is one bucket. Using ``<=`` would make 0 percent include bucket zero
    # and 100 percent impossible to express.
    if slot < percent:
        return answer(True, "", slot)
    return answer(
        False,
        f"bucket {slot} is outside the {percent} percent {which} rollout",
        slot,
    )


def _digits(text: str) -> int | None:
    """An integer from a string, or ``None``, accepting only ASCII ``0``–``9``.

    ``int()`` is wider than it looks, in the one direction that matters here.

    * ``int("٩٩")`` is 99 and ``int("１００")`` is 100. Python accepts every Unicode
      decimal digit, and ``str.isdigit`` agrees with it, so a percentage a reviewer
      cannot read at all resolves to a full rollout. That is the recurring shape of
      this whole package — a value that cannot be read resolving to the permissive
      reading — arriving through the standard library rather than through our own
      code.
    * ``int("1_0_0")`` is 100. Underscore separators are a Python literal convenience
      that has no business in an environment variable, and ``1_0`` beside ``10`` in a
      dashboard is not a difference anybody would notice.

    A percentage and an account id are ASCII in every system that produces them. A
    value that is not means something upstream mangled it, and the answer to a mangled
    number is to refuse it, not to interpret it.
    """
    body = text[1:] if text.startswith(("+", "-")) else text
    if not body or not all("0" <= character <= "9" for character in body):
        return None
    value = int(body)
    return -value if text.startswith("-") else value


def _percent(raw: Any) -> int:
    """A rollout percentage, clamped to 0..100, defaulting to nobody.

    ``config.resolve`` already clamps declared ints to their catalog bounds. This is the
    second belt: a caller who builds a value dict by hand, or a flag whose declaration
    later loses its ``maximum``, must not be able to produce a negative percentage that
    compares as "everyone" or a 4000 that reads as a working configuration.
    """
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return max(0, min(100, raw))
    value = _digits(str(raw).strip()) if raw is not None else None
    return 0 if value is None else max(0, min(100, value))


#: An account id, or ``None``. Never a guess.
#:
#: This used to be a second implementation of :func:`services.undx_brain.memory.owner_id`
#: — same rules, same reasoning, written twice. It is now the same function, because two
#: copies of an identity rule is two places for it to be subtly different, and the
#: difference would show up as one account being inside the rollout and outside its own
#: memory scope. The consequence here is milder than in memory (the wrong person is put
#: in or out of a rollout, rather than shown somebody else's data) but the rule is the
#: same rule, and sharing it is worth more than the independence.
_account_id = memory.owner_id
