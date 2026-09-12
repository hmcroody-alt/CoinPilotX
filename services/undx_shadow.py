"""Run a candidate provider beside the live one, and let its answer die in here.

What this is for
----------------
§35 of the mission brief says a shadow provider performs no writes, no
confirmations, no external actions and no persistent memory promotion. That is
a list of four things not to do, and a module that merely *intends* not to do
them is worth nothing: the guarantee has to survive the next person who edits
it, and "we check a flag before writing" is one deleted line away from a
shadow answer being executed against a real account.

So the suppression here is not a flag. It is an absence.

The shadow's completion text exists as a local variable inside
:func:`observe`, is compared against the primary's text, and goes out of scope
before the function returns. :class:`ShadowObservation` has no field that can
hold it. Nothing this module returns, logs, or stores contains a single
sentence a model produced. There is no branch to remove, because there is no
path from a shadow answer to anything that acts — the text is not reachable
from the value the caller receives.

That is the same argument `undx_routing_evidence` makes about explanations: a
guarantee that cannot be observed to fail is not a guarantee. Here it can be
observed, and `tests/test_undx_shadow.py` observes it by having the shadow
return a sentinel string and asserting the sentinel appears nowhere in the
serialised observation or in any report built from it.

What a shadow can honestly tell you
-----------------------------------
Live traffic has no right answer. Nobody graded it, and nobody is going to.
So this module **cannot** say the candidate is better, and it does not: every
report carries ``quality_verdict: None`` and a sentence saying why. A
shadow-vs-primary "win rate" computed over ungraded production traffic is a
number with no referent, and it would be believed because it looks like the
benchmark's number, which is derived from a corpus that *does* have right
answers. Correctness belongs to `undx_benchmark` and the golden corpus. This
module deliberately does not compete with it.

What is left is still worth having, because it is decidable:

* **Availability.** Did the candidate answer at all, and with what failure when
  it did not. A provider that 429s on a third of real traffic is disqualified
  before correctness is ever discussed.
* **Latency**, against the primary's on the same request rather than against a
  benchmark run on a quiet afternoon.
* **Numeric disagreement.** When the two answers state different sets of
  numbers, that is a concrete, reviewable disagreement rather than a stylistic
  one — and it is the disagreement that matters, because a wrong number is the
  failure that survives into a decision. It flags requests for a human; it does
  not score them.
* **Token overlap and length ratio**, as weak shape signals. Named weak in the
  report so nobody promotes them to a verdict.

What it costs, and who it exposes
---------------------------------
Every shadow call is a second paid completion of a request the user already
paid for, sent to a vendor the user did not choose. Both halves of that are
controls, not comments:

* Spend goes through the same `undx_cost` ledger as production traffic and the
  same `undx_cost.refusal` is consulted first. Shadow traffic can therefore
  exhaust the budget and cause the *primary* path to be refused. That is
  correct — the money is the same money — and it is why the sample rate
  defaults to zero.
* The privacy ceiling is enforced twice. The candidate must accept the
  request's class like any provider, and separately the class must sit at or
  below ``UNDX_SHADOW_MAX_PRIVACY_CLASS``, which defaults to ``PUBLIC``. The
  second check exists because routing a request to a provider is a decision the
  deployment made for a reason; copying that request to an *additional* vendor
  for the convenience of an experiment is a different decision, and it deserves
  its own, lower, ceiling. An unrecognised class name refuses, as everywhere
  else on this ladder.

Off by default, and off is the whole product until somebody funds it:
``UNDX_SHADOW_ENABLED`` is false and ``UNDX_SHADOW_SAMPLE_RATE`` is 0.0.

The default configuration shadows nothing, on purpose
-----------------------------------------------------
This deserves to be said in the open rather than discovered from an empty
report. ``undx_privacy.normalise(None)`` is ``CONFIDENTIAL`` — an unclassified
request is assumed private, because the router cannot see what it is
forwarding and a user's message to their assistant is private by default. The
shadow ceiling defaults to ``PUBLIC``. So **ordinary chat traffic is never
shadowed until an operator raises the ceiling**, even with the kill switch on
and the sample rate at 1.0.

That combination is not an oversight to be tuned away. Turning on an
experiment should not be the same act as deciding that private user messages
may be copied to a vendor the user never agreed to, so the two are separate
decisions and the second one has to be made explicitly. The safe way to get
signal is to classify the traffic honestly and shadow the part that really is
public; raising ``UNDX_SHADOW_MAX_PRIVACY_CLASS`` to cover everything is a
disclosure decision and should be reviewed as one.

:func:`readiness` exists so that an empty report is legible: it says which
gate is holding, so nobody reads "no observations" as "the candidate had no
problems".

There is a second gate behind that one, and it bites harder. Raising the
shadow ceiling does not grant a provider permission it does not have: under
the ceilings this fabric enforces, only ``openai``, ``claude`` and ``meta``
accept CONFIDENTIAL. The other four stop at PUBLIC. So the cheap providers
most worth evaluating as alternatives are exactly the ones that may not see
the traffic you would evaluate them on, and shadowing them is possible only
against content explicitly classified public.

That is the ceiling working, not a limitation to route around — and it has a
consequence worth stating plainly: **a migration to a cheaper provider has to
be argued from the golden corpus, not from production shadowing.** Which is
where `undx_benchmark` already puts it.

Not on the request's critical path
----------------------------------
:func:`observe` is called *after* the user has their answer. It never raises —
every fault becomes a status string — so a broken candidate cannot turn a
served request into an error. Callers that cannot afford the wall clock should
not call it at all rather than call it in a thread: nine worker processes each
spawning shadow threads is the herd `undx_benchmark` refuses to become.
"""

from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from services import db as platform_db
from services import undx_cost, undx_health, undx_privacy

log = logging.getLogger(__name__)


# --------------------------------------------------------------- vocabulary

#: Why a request was not shadowed. Typed for the same reason
#: `undx_benchmark.UNANSWERED_REASONS` is: "we did not run a shadow" and "the
#: shadow lost" must never collapse into one number, and a free-text reason
#: eventually gets aggregated as though it had.
SKIP_REASONS: tuple[str, ...] = (
    "disabled",            # the kill switch, or the sample rate is zero
    "no_candidate",        # nothing named in UNDX_SHADOW_PROVIDER
    "unknown_provider",    # named, but the router has never heard of it
    "provider_disabled",   # the router has it, the deployment turned it off
    "same_as_primary",     # shadowing a provider against itself measures noise
    "privacy_class",       # above UNDX_SHADOW_MAX_PRIVACY_CLASS
    "privacy_ceiling",     # the candidate itself refuses this class
    "budget",              # undx_cost.refusal said no
    "not_configured",      # no API key
    "circuit_open",        # the breaker is resting this provider
    "not_sampled",         # sampled out, which is the common case
)

#: How a shadow call ended. `empty` is separate from `success` because a
#: provider that returns 200 and no content answered nothing, and folding it
#: into success is the conflation §19 removed from the health states.
SHADOW_STATUSES: tuple[str, ...] = (
    "success", "empty", "timeout", "request_failed", "response_failed",
)

OBSERVATION_TABLE = "undx_shadow_observations"

#: Default ceiling for *copying* a request to an unchosen vendor. Below the
#: ceiling any individual provider carries, on purpose; see the module
#: docstring.
DEFAULT_MAX_PRIVACY_CLASS = undx_privacy.SENSITIVITY_PUBLIC

DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_TOKENS = 900

#: A number, with optional thousands separators and decimal part. Deliberately
#: a local copy rather than an import of `undx_eval_corpus._NUMBER`: that one is
#: a grading primitive whose behaviour is pinned by the corpus's own
#: `self_check`, and coupling a production comparison to a test fixture's
#: private regex means a corpus change silently changes what production
#: reports. Shared *configuration* has one authority (§57); shared incidental
#: regexes do not.
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[a-z0-9]+")


# ------------------------------------------------------------------- settings


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _flag(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    """The shadow kill switch.

    Separate from ``UNDX_OMNI_ROUTER_ENABLED``: turning off agentic routing
    should not be the only way to stop paying for an experiment, and stopping
    the experiment should not change how anybody's request is routed.
    """
    return _flag("UNDX_SHADOW_ENABLED", False)


def candidate() -> str:
    return _env("UNDX_SHADOW_PROVIDER").lower()


def sample_rate() -> float:
    """Fraction of eligible requests to shadow. Clamped to [0, 1].

    A malformed value reads as 0.0 rather than as 1.0. The failure mode of a
    typo here is a doubled bill against every request in production, so the
    unparseable case has to land on the cheap side.
    """
    raw = _env("UNDX_SHADOW_SAMPLE_RATE")
    if not raw:
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        log.warning("UNDX shadow sample rate is not a number; treating as 0")
        return 0.0
    if value != value:  # NaN compares false against every bound below
        return 0.0
    return max(0.0, min(1.0, value))


def max_privacy_class() -> str:
    """The highest class that may be copied to a second vendor, as written.

    Case and whitespace are normalised; the name is *not* validated here, so
    what an operator set is what an operator reads back. Whether it means
    anything is :func:`_ceiling_rank`'s problem, and the answer for a name off
    the ladder is "no".
    """
    return undx_privacy.normalise(_env("UNDX_SHADOW_MAX_PRIVACY_CLASS",
                                       DEFAULT_MAX_PRIVACY_CLASS))


#: Below `SYNTHETIC`, so that every real class compares as above the ceiling.
#: A ceiling is the one setting where the usual "unknown means SECRET" default
#: is backwards: `undx_privacy.UNKNOWN_CLASS_RANK` is the *highest* rank, which
#: is correct when ranking a request (treat unidentified content as maximally
#: sensitive) and catastrophic when ranking a ceiling (permit everything).
#:
#: This is not hypothetical. `UNDX_SHADOW_MAX_PRIVACY_CLASS=PUBIC` — one
#: missing letter — took the ceiling from PUBLIC to rank 6 and silently
#: cleared CONFIDENTIAL traffic, which is what unclassified chat normalises
#: to, for copying to a second vendor. The typo raised the ceiling instead of
#: lowering it, and nothing in the report would have said so.
_NO_CEILING_RANK = -1


def _ceiling_rank() -> int:
    """The configured ceiling as a rank, or -1 when it names nothing.

    Fail closed, in the direction that means "less new behaviour" — an
    unreadable ceiling permits no shadowing at all rather than all of it.
    """
    return undx_privacy.PRIVACY_RANK.get(max_privacy_class(), _NO_CEILING_RANK)


def timeout_seconds() -> int:
    try:
        return max(1, int(float(_env("UNDX_SHADOW_TIMEOUT_SECONDS",
                                     str(DEFAULT_TIMEOUT_SECONDS)))))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def ledger_enabled() -> bool:
    return _flag("UNDX_SHADOW_LEDGER_ENABLED", True)


# ----------------------------------------------------------------- the plan


@dataclass(frozen=True)
class ShadowPlan:
    """Whether this request gets shadowed, by whom, and why not.

    Frozen, and `reason` is always populated when `run` is false. A plan that
    said only "no" would make a shadow that silently never runs
    indistinguishable from one that is working and sampling out — and those
    demand opposite reactions from an operator looking at an empty report.
    """

    provider: str = ""
    run: bool = False
    reason: str = "disabled"

    def __post_init__(self) -> None:
        if not self.run and self.reason not in SKIP_REASONS:
            raise ValueError(f"unknown shadow skip reason {self.reason!r}")


def plan(router: Any, primary_provider: str, privacy_class: str | None = None,
         *, roll: float | None = None) -> ShadowPlan:
    """Decide, without calling anything, whether to shadow this request.

    `roll` exists so the sampling decision can be made deterministic in a test.
    A caller that passes it is choosing the outcome, which is what a test wants
    and what production must never do; production omits it and gets
    `random.random()`.

    The gate order mirrors `undx_router`'s routing loop — privacy, budget,
    credential, breaker — with the two shadow-specific gates in front. Same
    order, so that a provider which must not see this content is never
    consulted about whether it could have.
    """
    # Two switches, and the omni one is checked first. `UNDX_SHADOW_ENABLED`
    # turns off this experiment; `UNDX_OMNI_ROUTER_ENABLED` turns off every
    # behaviour this mission added. An operator in an incident pulls the second
    # and must not have to discover the first.
    if not getattr(router, "omni_router_enabled", lambda: False)():
        return ShadowPlan(reason="disabled")
    if not enabled():
        return ShadowPlan(reason="disabled")
    name = candidate()
    if not name:
        return ShadowPlan(reason="no_candidate")
    if name not in getattr(router, "PROVIDERS", {}):
        return ShadowPlan(provider=name, reason="unknown_provider")
    if not router.provider_enabled(name):
        return ShadowPlan(provider=name, reason="provider_disabled")
    if name == (primary_provider or "").strip().lower():
        return ShadowPlan(provider=name, reason="same_as_primary")

    declared = undx_privacy.normalise(privacy_class)
    # The two defaults lean opposite ways on purpose: an unrecognised *request*
    # class is treated as maximally sensitive, an unrecognised *ceiling* as
    # permitting nothing. Both readings refuse; using one constant for both
    # would make one of them permit.
    if undx_privacy.PRIVACY_RANK.get(declared, undx_privacy.UNKNOWN_CLASS_RANK) > \
            _ceiling_rank():
        return ShadowPlan(provider=name, reason="privacy_class")

    model = router._model(name)
    if not undx_privacy.provider_accepts(name, declared, model):
        return ShadowPlan(provider=name, reason="privacy_ceiling")
    if undx_cost.refusal(undx_cost.month_snapshot(), name, model):
        return ShadowPlan(provider=name, reason="budget")
    if not router._api_key(name):
        return ShadowPlan(provider=name, reason="not_configured")
    if router._breaker_should_skip(name):
        return ShadowPlan(provider=name, reason="circuit_open")

    rate = sample_rate()
    if rate <= 0.0:
        return ShadowPlan(provider=name, reason="disabled")
    draw = random.random() if roll is None else float(roll)
    if draw >= rate:
        return ShadowPlan(provider=name, reason="not_sampled")
    return ShadowPlan(provider=name, run=True, reason="")


# ----------------------------------------------------------- the observation


@dataclass(frozen=True)
class ShadowObservation:
    """What one shadow call is allowed to leave behind.

    Read the field list as the security boundary it is. There is no `text`, no
    `response`, no `answer`, no `excerpt` and no `sample`, and adding one would
    be the change that turns this module from an experiment into a second,
    ungoverned path by which a model's words reach a person. Everything here is
    a count, a duration, a status or a ratio.

    `request_id` is the caller's own correlation id and nothing else — not a
    user id, not a conversation id. Joining a shadow observation back to a
    person is a thing an operator can do through the caller's own logs if they
    have cause; it is not a thing this table volunteers.
    """

    request_id: str
    lane: str
    primary_provider: str
    shadow_provider: str
    primary_status: str
    shadow_status: str
    primary_latency_ms: int
    shadow_latency_ms: int
    #: len(shadow) / len(primary), rounded. None when either side is empty —
    #: not 0.0, which would read as "the shadow said nothing" for the case
    #: where the *primary* said nothing.
    length_ratio: float | None = None
    #: Jaccard overlap of lowercased word sets. A shape signal, not a quality
    #: one; the report labels it as weak so it does not get promoted.
    token_overlap: float | None = None
    #: True when both answers state the same set of numbers, False when they do
    #: not, None when neither states any. The one comparison here worth waking
    #: a human for: two answers that disagree about a number disagree about a
    #: fact, and the reader who can detect that is the one who already knew.
    numbers_agree: bool | None = None
    shadow_cost_micro_usd: int = 0
    shadow_priced: bool = True
    created_at: str = ""

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


#: The field names above, frozen into a constant so a test can assert the
#: boundary rather than re-describe it. A field added to the dataclass and not
#: to this tuple fails `tests/test_undx_shadow.py`, which is the point: the
#: addition should be a decision somebody made on purpose.
OBSERVATION_FIELDS: tuple[str, ...] = tuple(
    ShadowObservation.__dataclass_fields__.keys())


def _words(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def _numbers(text: str) -> set[float]:
    found: set[float] = set()
    for match in _NUMBER.finditer(text or ""):
        try:
            found.add(float(match.group().replace(",", "")))
        except ValueError:  # pragma: no cover - the regex cannot produce this
            continue
    return found


def _compare(primary_text: str, shadow_text: str) -> dict[str, Any]:
    """Reduce two completions to numbers, so the completions can be discarded.

    Called with both texts and returning none of them. This is the only place
    in the process where a shadow answer and a production answer are in scope
    together, and it is four lines long for that reason.
    """
    primary_text = primary_text or ""
    shadow_text = shadow_text or ""
    ratio: float | None = None
    if primary_text and shadow_text:
        ratio = round(len(shadow_text) / len(primary_text), 3)

    overlap: float | None = None
    left, right = _words(primary_text), _words(shadow_text)
    if left or right:
        union = left | right
        overlap = round(len(left & right) / len(union), 3) if union else None

    left_numbers, right_numbers = _numbers(primary_text), _numbers(shadow_text)
    agree: bool | None = None
    if left_numbers or right_numbers:
        agree = left_numbers == right_numbers
    return {"length_ratio": ratio, "token_overlap": overlap,
            "numbers_agree": agree}


def observe(router: Any, *, request_id: str, lane: str, primary_provider: str,
            primary_text: str, primary_latency_ms: int,
            message: str, history: Any = None, system_prompt: str = "",
            privacy_class: str | None = None,
            plan_override: ShadowPlan | None = None) -> ShadowObservation | None:
    """Call the candidate on the same request, compare, and forget what it said.

    Returns ``None`` when no shadow ran — the plan's reason is the caller's to
    record if it wants one. Never raises: this runs after the user already has
    an answer, and a candidate provider's fault is not the user's problem.

    `primary_text` comes in and does not go out. Neither does the shadow's.
    """
    decision = plan_override or plan(router, primary_provider, privacy_class)
    if not decision.run:
        return None

    name = decision.provider
    started = time.time()
    status = "response_failed"
    shadow_text = ""
    usage: dict[str, Any] = {}
    try:
        result = router.CALLERS[name](
            system_prompt or router.DEFAULT_UNDX_SYSTEM_PROMPT,
            message, history or [], timeout_seconds(),
        )
        shadow_text = str(result.get("text") or "")
        usage = result.get("usage") or {}
        status = "success" if shadow_text.strip() else "empty"
    except Exception as exc:  # noqa: BLE001 - every fault becomes a status
        status = _status_for(exc)
        # Recorded against the provider's health for the same reason a
        # production failure is: a candidate that times out on real traffic is
        # unhealthy, and hiding that would leave the breaker deciding from a
        # sample this module deliberately excluded itself from.
        try:
            router._record_provider_failure(
                name, undx_health.classify_failure(exc), router._safe_error(exc))
        except Exception:  # pragma: no cover - health is best effort here
            pass
        log.warning("UNDX shadow call failed provider=%s status=%s error=%s",
                    name, status, type(exc).__name__)

    latency_ms = int((time.time() - started) * 1000)
    if status == "success":
        try:
            router._record_provider_success(name)
            router._record_usage(usage)
        except Exception:  # pragma: no cover
            log.warning("UNDX shadow bookkeeping failed provider=%s", name)

    comparison = _compare(primary_text, shadow_text) if status == "success" else {
        "length_ratio": None, "token_overlap": None, "numbers_agree": None}
    # `shadow_text` is not referenced past this line, and nothing below can
    # reach it. That is the §35 guarantee, and it is enforced by scope rather
    # than by a condition somebody could delete.

    observation = ShadowObservation(
        request_id=str(request_id or ""),
        lane=str(lane or ""),
        primary_provider=str(primary_provider or ""),
        shadow_provider=name,
        primary_status="success" if (primary_text or "").strip() else "empty",
        shadow_status=status,
        primary_latency_ms=int(primary_latency_ms or 0),
        shadow_latency_ms=latency_ms,
        shadow_cost_micro_usd=undx_cost.to_micro_usd(usage.get("cost_usd")),
        shadow_priced=usage.get("cost_usd") is not None,
        created_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        **comparison,
    )
    _store(observation)
    return observation


def _status_for(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "request" in name or "connection" in name or "http" in name:
        return "request_failed"
    return "response_failed"


# -------------------------------------------------------------- persistence

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    f"""CREATE TABLE IF NOT EXISTS {OBSERVATION_TABLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL DEFAULT '',
        lane TEXT NOT NULL DEFAULT '',
        primary_provider TEXT NOT NULL DEFAULT '',
        shadow_provider TEXT NOT NULL DEFAULT '',
        primary_status TEXT NOT NULL DEFAULT '',
        shadow_status TEXT NOT NULL DEFAULT '',
        primary_latency_ms INTEGER NOT NULL DEFAULT 0,
        shadow_latency_ms INTEGER NOT NULL DEFAULT 0,
        length_ratio REAL,
        token_overlap REAL,
        numbers_agree INTEGER,
        shadow_cost_micro_usd INTEGER NOT NULL DEFAULT 0,
        shadow_priced INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT ''
    )""",
    f"CREATE INDEX IF NOT EXISTS ix_{OBSERVATION_TABLE}_provider "
    f"ON {OBSERVATION_TABLE}(shadow_provider, created_at)",
)

_INSERT_SQL = f"""INSERT INTO {OBSERVATION_TABLE}
    (request_id, lane, primary_provider, shadow_provider, primary_status,
     shadow_status, primary_latency_ms, shadow_latency_ms, length_ratio,
     token_overlap, numbers_agree, shadow_cost_micro_usd, shadow_priced,
     created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

_SELECT_SQL = f"""SELECT lane, primary_provider, shadow_provider, primary_status,
    shadow_status, primary_latency_ms, shadow_latency_ms, length_ratio,
    token_overlap, numbers_agree, shadow_cost_micro_usd, shadow_priced
    FROM {OBSERVATION_TABLE} WHERE shadow_provider = ?"""

_LOCK = threading.Lock()
_schema_ready = False

#: Per-process mirror, for the same reason `undx_cost` keeps one: a storage
#: fault must degrade the report's *completeness*, not silence the experiment.
#: Capped, because an unbounded list in a long-lived worker is a leak.
_MEMORY_LIMIT = 500
_memory: list[ShadowObservation] = []

_STATS: dict[str, Any] = {"writes": 0, "write_failures": 0, "reads": 0,
                          "read_failures": 0, "last_error": ""}


def ensure_schema(conn=None) -> int:
    """Create the observation table. Idempotent.

    Opens and commits its own connection when given none — passing a caller's
    open connection makes the DDL ride that transaction, and a caller that
    never commits leaves the catalog lock held while the next connection blocks
    behind it. That has already happened in this repo.
    """
    own = conn is None
    if own:
        conn = platform_db.connect()
    try:
        cur = conn.cursor()
        for statement in _SCHEMA_STATEMENTS:
            cur.execute(statement)
        if own:
            conn.commit()
        return len(_SCHEMA_STATEMENTS)
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass


def _ensure_schema_once() -> None:
    global _schema_ready
    if _schema_ready:
        return
    ensure_schema()
    with _LOCK:
        _schema_ready = True


def _store(observation: ShadowObservation) -> None:
    with _LOCK:
        _memory.append(observation)
        if len(_memory) > _MEMORY_LIMIT:
            del _memory[:-_MEMORY_LIMIT]
    if not ledger_enabled():
        return
    conn = None
    try:
        _ensure_schema_once()
        conn = platform_db.connect()
        cur = conn.cursor()
        cur.execute(_INSERT_SQL, (
            observation.request_id, observation.lane, observation.primary_provider,
            observation.shadow_provider, observation.primary_status,
            observation.shadow_status, observation.primary_latency_ms,
            observation.shadow_latency_ms, observation.length_ratio,
            observation.token_overlap,
            None if observation.numbers_agree is None else int(observation.numbers_agree),
            observation.shadow_cost_micro_usd, int(observation.shadow_priced),
            observation.created_at,
        ))
        conn.commit()
        with _LOCK:
            _STATS["writes"] += 1
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not raise here
        with _LOCK:
            _STATS["write_failures"] += 1
            _STATS["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
        log.warning("UNDX shadow store failed error=%s", type(exc).__name__)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass


def _from_ledger(provider: str) -> list[ShadowObservation] | None:
    conn = None
    try:
        _ensure_schema_once()
        conn = platform_db.connect()
        cur = conn.cursor()
        cur.execute(_SELECT_SQL, (provider,))
        rows = cur.fetchall() or []
        with _LOCK:
            _STATS["reads"] += 1
        return [ShadowObservation(
            request_id="", lane=str(row[0]), primary_provider=str(row[1]),
            shadow_provider=str(row[2]), primary_status=str(row[3]),
            shadow_status=str(row[4]), primary_latency_ms=int(row[5] or 0),
            shadow_latency_ms=int(row[6] or 0),
            length_ratio=None if row[7] is None else float(row[7]),
            token_overlap=None if row[8] is None else float(row[8]),
            numbers_agree=None if row[9] is None else bool(row[9]),
            shadow_cost_micro_usd=int(row[10] or 0),
            shadow_priced=bool(row[11]),
        ) for row in rows]
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _STATS["read_failures"] += 1
            _STATS["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
        log.warning("UNDX shadow read failed error=%s", type(exc).__name__)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass


def stats() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATS)


def reset_for_tests() -> None:
    global _schema_ready
    with _LOCK:
        _memory.clear()
        for key in ("writes", "write_failures", "reads", "read_failures"):
            _STATS[key] = 0
        _STATS["last_error"] = ""
    _schema_ready = False


# ------------------------------------------------------------------- report

#: Published verbatim on every report, in the field a reader would otherwise
#: fill in themselves. Stated as a property of the data rather than a caveat
#: about the method, because the method is fine — it is the input that has no
#: right answers in it.
NO_VERDICT_REASON = (
    "Live traffic is ungraded, so no correctness verdict is available from "
    "shadow observations. Availability and latency below are measured; "
    "numeric disagreement flags requests for review. Provider quality is "
    "decided by undx_benchmark against the golden corpus, not here."
)


def readiness(router: Any, *, privacy_class: str | None = None) -> dict[str, Any]:
    """Why the shadow is or is not going to see anything. Calls nothing.

    An empty report has two completely different readings — "the candidate was
    never asked" and "the candidate was asked and nothing went wrong" — and a
    reader cannot tell them apart from the report alone. This names the gate
    that is holding, so the first reading is available without an operator
    having to reconstruct it from four environment variables.

    The `unclassified` row is the one most people will be looking for: it
    evaluates the gate against what real chat traffic actually declares, which
    is nothing, rather than against the class the reader happened to pass in.
    """
    declared = plan(router, "__none__", privacy_class, roll=0.0)
    unclassified = plan(router, "__none__", None, roll=0.0)
    return {
        "enabled": enabled(),
        "candidate": candidate(),
        "sample_rate": sample_rate(),
        "max_privacy_class": max_privacy_class(),
        # False means the configured name is not on the ladder, so the ceiling
        # permits nothing. Published because that state and "correctly set to
        # the strictest class" produce the same empty report, and only one of
        # them is somebody's typo.
        "max_privacy_class_is_known": _ceiling_rank() != _NO_CEILING_RANK,
        "default_request_class": undx_privacy.normalise(None),
        "would_run_for_declared_class": declared.run,
        "declared_blocked_by": declared.reason,
        # False under the shipped defaults, and that is the correct answer.
        "would_run_for_unclassified_traffic": unclassified.run,
        "unclassified_blocked_by": unclassified.reason,
    }


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def report(provider: str = "", observations: list[ShadowObservation] | None = None
           ) -> dict[str, Any]:
    """Aggregate what the shadow saw, and decline to call it a winner.

    `quality_verdict` is `None` on every path through this function. There is
    no argument, no threshold and no sample size that makes it anything else,
    because the input is ungraded — see :data:`NO_VERDICT_REASON`. A reader who
    wants a verdict is pointed at the benchmark, which has one and earned it.
    """
    name = (provider or candidate()).strip().lower()
    if observations is None:
        observations = _from_ledger(name) if ledger_enabled() else None
        source = "ledger"
        if observations is None:
            with _LOCK:
                observations = [o for o in _memory if o.shadow_provider == name]
            source = "process"
    else:
        source = "supplied"

    total = len(observations)
    answered = [o for o in observations if o.shadow_status == "success"]
    primary_answered = [o for o in observations if o.primary_status == "success"]
    failures: dict[str, int] = {}
    for row in observations:
        if row.shadow_status != "success":
            failures[row.shadow_status] = failures.get(row.shadow_status, 0) + 1

    comparable = [o for o in answered if o.numbers_agree is not None]
    disagreed = [o for o in comparable if o.numbers_agree is False]
    overlaps = [o.token_overlap for o in answered if o.token_overlap is not None]
    priced = all(o.shadow_priced for o in observations) if observations else True

    return {
        "provider": name,
        "source": source,
        "observations": total,
        # Both numerators are published next to their denominator. A rate with
        # no sample size beside it is the shape of every misleading dashboard
        # this mission has removed.
        "shadow_answered": len(answered),
        "primary_answered": len(primary_answered),
        "availability": round(len(answered) / total, 4) if total else None,
        "failures": dict(sorted(failures.items())),
        "median_shadow_latency_ms": _median([o.shadow_latency_ms for o in answered]),
        "median_primary_latency_ms": _median(
            [o.primary_latency_ms for o in primary_answered]),
        "numeric_comparisons": len(comparable),
        "numeric_disagreements": len(disagreed),
        "numeric_disagreement_rate":
            round(len(disagreed) / len(comparable), 4) if comparable else None,
        "mean_token_overlap":
            round(sum(overlaps) / len(overlaps), 4) if overlaps else None,
        "token_overlap_is_weak": True,
        "cost_micro_usd": sum(o.shadow_cost_micro_usd for o in observations),
        "cost_complete": priced,
        # The point of the module, in the field a dashboard would bind to.
        "quality_verdict": None,
        "quality_verdict_reason": NO_VERDICT_REASON,
    }
