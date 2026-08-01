"""Every environment variable the Brain reads, declared once, with its meaning.

This module exists because of a specific failure mode rather than a preference for
tidiness. Configuration that is read inline — ``os.getenv("UNDX_BRAIN_ENABLED") == "1"``
at the point of use — has three properties that are individually tolerable and jointly
fatal for a system that is about to be configured from a deployment dashboard by
somebody who cannot read the source:

* the set of variables that exist is not knowable without grepping, so the dashboard
  and the code drift apart silently and in both directions;
* what a *missing* variable means is decided independently at each call site, so the
  same absence can fail open in one place and closed in another;
* a typo in a variable name is indistinguishable from a deliberate absence.

So: one declaration per variable, in :data:`CATALOG`, carrying the metadata PART 15 of
the directive requires — purpose, default, whether it is required, whether it holds a
secret, which environment uses it, whether changing it forces a redeploy, and its
rollback behaviour. :func:`flags` resolves them. :func:`unknown_undx_brain_vars` reports
environment variables that look like they were meant for this module and match nothing,
which is the typo case made visible instead of silent.

**Secrets.** No variable declared here holds one, and :func:`describe_for_report`
enforces that by refusing to print the value of anything marked ``secret``. The Brain
needs activation flags, bounds, and rollout percentages; it does not need credentials.
Provider keys stay where they already are.

**Fail-closed is not the default for everything, and that is deliberate.** Two different
questions get two different answers:

* *May UNDX do this?* — absence means no. Writes, brain activation, rollout, and every
  authorisation-shaped flag default off. A variable that is missing because somebody
  deleted it must never be the reason a write happened.
* *Can UNDX still hold a conversation?* — absence means yes. A missing or unreadable
  corpus must degrade retrieval to empty, not crash the request. Refusing to answer
  "how do I mute a chat?" because a YAML file moved is a self-inflicted outage, and the
  answer to that question was never authorisation-bearing in the first place.

The ``fail`` field on each flag records which of the two a variable is, so the
distinction is auditable rather than remembered.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

Kind = Literal["bool", "int", "str", "csv"]
Fail = Literal["closed", "open", "n/a"]


@dataclass(frozen=True)
class Flag:
    """One environment variable, and everything PART 15 asks to be defined about it."""

    name: str
    kind: Kind
    default: str
    purpose: str
    #: What an absent or unparseable value means. ``closed`` = the capability is denied;
    #: ``open`` = the feature degrades but the request survives; ``n/a`` = a bound or a
    #: label with no authorisation content.
    fail: Fail
    #: Absent variables are legal for everything here — that is the point of defaults.
    #: ``required`` marks the ones where running on the default is a misconfiguration
    #: worth reporting, not a working state.
    required: bool = False
    secret: bool = False
    #: Railway restarts the service when a variable changes; this records the ones where
    #: that restart is load-bearing rather than incidental.
    redeploy: bool = True
    environments: tuple[str, ...] = ("production", "staging", "local")
    rollback: str = "Delete the variable to return to the documented default."
    #: Bounds for ``int`` flags. A value outside them is a misconfiguration, and the
    #: clamp is reported rather than applied silently — see :func:`resolve`.
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()


#: Recognised boolean spellings, listed on both sides so a value that is *neither* can
#: be told apart from one that is falsy. Without that distinction every typo reads as
#: off, and the flags where off is the unsafe answer — verification, audit, fail-closed
#: — are exactly the ones a typo would disable.
_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "y", "t", "enabled"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", "n", "f", "disabled", ""})


def _bool(raw: str) -> bool:
    return str(raw).strip().lower() in _TRUE_WORDS


def _ascii_int(text: str) -> int | None:
    """An integer from a string, accepting only ASCII ``0``–``9``, else ``None``.

    ``int()`` is wider than an environment variable ever needs, in the one direction
    that matters. ``int("٩٩")`` is 99, ``int("１００")`` is 100 and ``int("𝟵𝟵")`` is 99:
    Python accepts every Unicode decimal digit, and ``str.isdigit`` agrees with it. So a
    value that a reviewer reading the dashboard cannot decipher at all parses cleanly
    into a large number. For ``UNDX_BRAIN_ROLLOUT_PERCENT`` that means a full production
    rollout configured by something nobody can read.

    ``int("1_0_0")`` is 100 for a different reason with the same consequence: underscore
    separators are a Python *literal* convenience with no business in an environment
    variable, and ``1_0`` beside ``10`` in a dashboard is not a difference anybody
    notices.

    Refusing both puts them on the documented default and writes a note, which is the
    same treatment an unrecognised boolean already gets — an unreadable value must not
    resolve to the permissive reading.
    """
    body = text[1:] if text.startswith(("+", "-")) else text
    if not body or not all("0" <= character <= "9" for character in body):
        return None
    value = int(body)
    return -value if text.startswith("-") else value


#: Declared in the order a request meets them: activation, then the corpus, then
#: retrieval, then execution, then planning, then response, then memory, then rollout,
#: then observability. The grouping is by ``family`` on the catalog entries below.
CATALOG: tuple[Flag, ...] = (
    # ---------------------------------------------------------------- activation ----
    Flag(
        "UNDX_BRAIN_ENABLED", "bool", "0",
        "Master switch for the Brain layer. Off means every request takes the existing "
        "verified runtime path exactly as it does today; the Brain is loaded but never "
        "consulted. This is the flag that makes the whole mission reversible.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_KNOWLEDGE_ENABLED", "bool", "0",
        "Allow the Product Knowledge Brain to contribute retrieved source-derived "
        "records to a response. Off means retrieval returns empty and the response is "
        "built from live evidence alone, which is the current behaviour.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_SKILLS_ENABLED", "bool", "0",
        "Allow skill selection to consult the Skill Brain. Off means capability "
        "selection stays entirely inside undx_agent_runtime, unchanged.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_MEMORY_ENABLED", "bool", "0",
        "Allow the Memory Brain to read and write its memory classes. Off means no "
        "Brain-owned persistence occurs at all.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_REASONING_ENABLED", "bool", "0",
        "Allow the bounded planner to build multi-step plans. Off means one step per "
        "request, which is the current behaviour.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_RESPONSE_ENABLED", "bool", "0",
        "Allow the Communication Brain to shape the final response. Off means "
        "undx_response_intelligence renders exactly as it does today.",
        fail="closed",
    ),
    # -------------------------------------------------------------------- corpus ----
    Flag(
        "UNDX_SOURCE_CORPUS_ENABLED", "bool", "1",
        "Load the source-derived corpus at all. On by default because loading it is "
        "read-only, bounded, and authorises nothing; the authorisation-bearing switch "
        "is UNDX_BRAIN_KNOWLEDGE_ENABLED, which gates whether anything retrieved may "
        "reach a response.",
        fail="open",
    ),
    Flag(
        "UNDX_SOURCE_CORPUS_PATH", "str",
        "backend/undx/config/undx_training_v6_source_corpus.yaml",
        "Repository-relative path to the corpus. Declared so a deployment can point at "
        "a pinned artifact; an absolute path outside the repository root is rejected by "
        "the ingester rather than followed.",
        fail="open", redeploy=True,
    ),
    Flag(
        "UNDX_SOURCE_CORPUS_SCHEMA_VERSION", "str", "6.0",
        "The schema version this build knows how to read. A corpus declaring anything "
        "else is refused whole — a partially-understood corpus is worse than none, "
        "because the records it does parse look authoritative.",
        fail="closed",
    ),
    Flag(
        "UNDX_SOURCE_CORPUS_MAX_RECORDS", "int", "5000",
        "Upper bound on ingested records. Guards against a regenerated corpus growing "
        "without anybody noticing the memory cost.",
        fail="n/a", minimum=1, maximum=200_000,
    ),
    Flag(
        "UNDX_SOURCE_CORPUS_MAX_CONTEXT_RECORDS", "int", "8",
        "Hard ceiling on records that may enter a single model prompt, independent of "
        "what retrieval asks for. This is the variable that stops the corpus becoming "
        "one enormous prompt.",
        fail="n/a", minimum=0, maximum=64,
    ),
    Flag(
        "UNDX_SOURCE_CORPUS_STRICT_AUDIT", "bool", "1",
        "Refuse to serve retrieval from a corpus whose audit did not pass. On by "
        "default: the audit is what checks for secret-shaped and private-data-shaped "
        "content, and serving from an unaudited corpus is the one failure this whole "
        "subsystem exists to prevent.",
        fail="closed",
    ),
    # ----------------------------------------------------------------- retrieval ----
    Flag(
        "UNDX_KNOWLEDGE_RETRIEVAL_ENABLED", "bool", "1",
        "Allow bounded retrieval to run. Distinct from the Brain knowledge flag: this "
        "one governs whether the index is queried, that one governs whether results may "
        "influence a response.",
        fail="open",
    ),
    Flag(
        "UNDX_KNOWLEDGE_MAX_RESULTS", "int", "6",
        "Maximum knowledge records returned by one retrieval call, before the context "
        "budget is applied.",
        fail="n/a", minimum=0, maximum=50,
    ),
    Flag(
        "UNDX_KNOWLEDGE_MAX_CONTEXT_CHARS", "int", "4000",
        "Character budget for all retrieved knowledge in one request. Characters rather "
        "than tokens because characters are what this process can count exactly, and a "
        "budget that is estimated is a budget that is occasionally exceeded.",
        fail="n/a", minimum=0, maximum=100_000,
    ),
    Flag(
        "UNDX_KNOWLEDGE_MIN_TRUST_LEVEL", "str", "source_mapped",
        "Lowest trust level retrieval may return. Records below it are indexed but not "
        "served, so raising this narrows what UNDX will say without regenerating "
        "anything.",
        fail="closed",
        choices=(
            "blocked", "deprecated", "source_discovered", "source_mapped",
            "documented", "tested", "live_verified", "runtime_canonical",
        ),
    ),
    Flag(
        "UNDX_KNOWLEDGE_ALLOW_SOURCE_DISCOVERED", "bool", "0",
        "Permit records that were found in source but mapped to no canonical domain. "
        "Off by default: an unmapped record is a filename and a summary, which is "
        "enough to sound authoritative and not enough to be right.",
        fail="closed",
    ),
    # ----------------------------------------------------------------- execution ----
    Flag(
        "UNDX_AGENT_REQUIRE_VERIFICATION", "bool", "1",
        "Require independent read-back before any write is reported as done. On by "
        "default and fail-closed on an unparseable value: a missing verification "
        "setting must never be the reason verification was skipped.",
        fail="closed", required=True,
    ),
    Flag(
        "UNDX_AGENT_REQUIRE_AUDIT", "bool", "1",
        "Require an audit receipt for every governed mutation.",
        fail="closed", required=True,
    ),
    Flag(
        "UNDX_AGENT_FAIL_CLOSED", "bool", "1",
        "When a policy, verification, or audit component is unavailable, deny rather "
        "than proceed. Turning this off is a deliberate act with no legitimate "
        "production use; it exists so the fail-open path can be tested.",
        fail="closed", required=True,
    ),
    # ----------------------------------------------------------- working context ----
    Flag(
        "UNDX_BRAIN_WORKSPACE_ENABLED", "bool", "0",
        "Allow a bounded working context to be assembled for one request. Off means the "
        "workspace accepts nothing and every caller sees an empty one, which is the "
        "current behaviour: context is assembled by each call site as it is today.",
        fail="closed",
    ),
    Flag(
        "UNDX_WORKSPACE_MAX_ITEMS", "int", "24",
        "Hard ceiling on entries in one working context, across every slot. The "
        "twenty-fifth entry is refused rather than displacing the first, because the "
        "entry most likely to be displaced by a busy retrieval is the constraint the "
        "person stated at the start.",
        fail="n/a", minimum=1, maximum=200,
    ),
    Flag(
        "UNDX_WORKSPACE_MAX_CHARS", "int", "8000",
        "Character budget for everything held in one working context. Counted rather "
        "than estimated, for the same reason the retrieval budget is.",
        fail="n/a", minimum=256, maximum=60_000,
    ),
    Flag(
        "UNDX_WORKSPACE_TTL_SECONDS", "int", "300",
        "How long a working context stays usable. Past it the context is abandoned, not "
        "resumed: what it observed about the account is no longer evidence of anything "
        "current, and a stale observation is how UNDX describes a state that has since "
        "changed.",
        fail="n/a", minimum=5, maximum=3600,
    ),
    # ----------------------------------------------------------------- attention ----
    Flag(
        "UNDX_BRAIN_ATTENTION_ENABLED", "bool", "0",
        "Allow salience routing to decide which parts of the product a request is about. "
        "Off means every focus is empty and each call site selects context the way it "
        "does today, which is the current behaviour.",
        fail="closed",
    ),
    Flag(
        "UNDX_ATTENTION_MAX_AREAS", "int", "6",
        "How many product areas one request may activate. Configuration may lower this "
        "and may not raise it: a mistyped value must be able to narrow attention, never "
        "turn the router into a system that opens everything.",
        fail="n/a", minimum=1, maximum=6,
    ),
    Flag(
        "UNDX_ATTENTION_MAX_CAPABILITIES", "int", "6",
        "How many capabilities one request may carry forward, out of the eighty "
        "registered. Additionally clamped to the working context's skill-slot ceiling, "
        "so attention can never offer the workspace more than it will accept.",
        fail="n/a", minimum=1, maximum=6,
    ),
    # --------------------------------------------------------------------- goals ----
    Flag(
        "UNDX_BRAIN_GOALS_ENABLED", "bool", "0",
        "Allow goal understanding to say that a request names no operation. Off means "
        "every goal reads as unknown and each call site takes the intent matcher's best "
        "capability as the goal, which is the current behaviour and the one that lets "
        "\"fix my alert\" become a write.",
        fail="closed",
    ),
    # ------------------------------------------------------------------ planning ----
    Flag(
        "UNDX_PLANNER_MAX_STEPS", "int", "6",
        "Maximum steps in one plan. A plan that wants more is refused, not truncated: a "
        "truncated plan is a plan that stops halfway through a multi-write goal.",
        fail="n/a", minimum=1, maximum=32,
    ),
    Flag(
        "UNDX_PLANNER_MAX_TOOL_CALLS", "int", "8",
        "Maximum governed tool calls across one plan, counting retries.",
        fail="n/a", minimum=1, maximum=64,
    ),
    Flag(
        "UNDX_PLANNER_MAX_RETRIES", "int", "1",
        "Retries per step. Writes are never retried by the planner regardless of this "
        "value — idempotency is enforced at the gateway, and a planner that retries a "
        "write is a planner that duplicates one.",
        fail="n/a", minimum=0, maximum=5,
    ),
    Flag(
        "UNDX_PLANNER_TASK_TIMEOUT_SECONDS", "int", "120",
        "Wall-clock bound on one plan, after which it expires rather than resuming.",
        fail="n/a", minimum=5, maximum=3600,
    ),
    # ------------------------------------------------------------------ response ----
    Flag(
        "UNDX_RESPONSE_FACTUALITY_CHECK", "bool", "1",
        "Validate that the rendered response claims nothing the evidence does not "
        "support. Fail-closed: an unparseable value leaves the check on.",
        fail="closed",
    ),
    Flag(
        "UNDX_RESPONSE_MAX_REGENERATIONS", "int", "64",
        "How many drafts rejected by the factuality check may be discarded and "
        "re-rendered before UNDX stops searching and answers with the honest boundary "
        "instead. The default sits above the whole search space, so this ceiling only "
        "ever narrows; lowering it makes UNDX give up sooner and say less. It was "
        "declared 1/max 3 while the loop could build forty-four drafts, and read by "
        "nothing, so every value it could hold described behaviour the system did not "
        "have.",
        fail="n/a", minimum=0, maximum=64,
    ),
    # -------------------------------------------------------------------- memory ----
    Flag(
        "UNDX_MEMORY_FAIL_CLOSED", "bool", "1",
        "When a memory class cannot establish its owner scope, refuse to read or write "
        "it. This is the flag that stops one account's memory reaching another's "
        "response when a lookup degrades.",
        fail="closed", required=True,
    ),
    Flag(
        "UNDX_MEMORY_USER_PREFERENCES_ENABLED", "bool", "0",
        "Allow long-term persistence of user-approved preferences.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_FACTS_ENABLED", "bool", "0",
        "Allow stored facts to age and to be compared across time. Off means a "
        "remembered claim is returned with the same weight on the day it was recorded "
        "and six weeks later, and a new observation that disagrees with a stored one "
        "lands beside it unremarked — which is the current behaviour. On means a fact "
        "past its trust level's horizon may only be cited \"as of\" when it was "
        "observed, and a disagreement is reported rather than silently overwritten or "
        "silently kept.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_LEARNING_ENABLED", "bool", "0",
        "Allow the accumulated learning-event log to be read as evidence rather than "
        "counted. Off means ``pulse_ai_learning_events`` keeps being written by eleven "
        "call sites and read by one ``SELECT COUNT(*)``, which is the current "
        "behaviour. On means an owner's own events can be loaded through the memory "
        "scope and asked questions a count cannot answer — which capability fails "
        "most, whether one kind of event tends to follow another — with every answer "
        "carrying how many events it rests on and refusing to generalise below a "
        "floor.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_PREDICTION_ENABLED", "bool", "0",
        "Allow a write to be described before it happens, and the description to be "
        "checked afterwards. Off means ``simulate_operation`` keeps returning one of "
        "two constant strings chosen by whether the caller passed a failure, which is "
        "the current behaviour: it reads no resource and answers identically for a "
        "capability that can be undone and one that cannot. On means a proposed call "
        "is read against the capability registry's undo graph, so the answer states "
        "what the verifier should read back afterwards, whether the call can be "
        "reversed at all, whether reversal needs an id that will not exist until the "
        "write verifies, and which prior values will be destroyed unless they are read "
        "first — and the prediction can then be compared against what actually "
        "happened, which is the part that makes it a prediction rather than a "
        "description.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_SELECTION_ENABLED", "bool", "0",
        "Allow more than one candidate operation to be held at once, and compared. Off "
        "means ``match_capability`` keeps returning a single best-scoring capability "
        "and discarding the rest, which is the current behaviour: a runner-up one point "
        "behind is indistinguishable at the call site from no runner-up at all. On "
        "means the same scoring is kept as a ranked list, candidates within a near-tie "
        "of the leader are named, and a contested band is separated on declared data — "
        "a read is preferred to a write, a reversible write to an irreversible one, a "
        "narrower blast radius to a wider one. Two contested writes that none of those "
        "rules separate are returned undecided rather than guessed between. The "
        "ranking is the matcher's own and not a second opinion: a test holds the top of "
        "the list against ``match_capability`` over every registered phrasing.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_EXECUTOR_ENABLED", "bool", "0",
        "Allow a plan to run as more than one step, spending against the ceilings "
        "``UNDX_PLANNER_MAX_TOOL_CALLS``, ``UNDX_PLANNER_MAX_RETRIES`` and "
        "``UNDX_PLANNER_TASK_TIMEOUT_SECONDS``. Off means those three numbers are "
        "declared and unread, which is the current behaviour: ``bounds.Ledger`` "
        "enforces them and nothing runs a plan through it, because execution is one "
        "step per request. On means a plan is admitted against the step ceiling first "
        "and then walked step by step through a ledger that spends and never refunds — "
        "a call is spent per attempt so retries cannot buy their way past the tool-call "
        "budget, a write is never retried because a timeout does not say whether the "
        "first attempt landed, and expiry stops the run rather than resuming it. The "
        "executor performs nothing itself; it calls back into whatever the caller "
        "supplies, so it cannot become a second path to the gateway. A run that stops "
        "part-way reports ``ok=False`` and names the writes that already landed and "
        "the ones whose outcome is unknown, because half of a multi-write goal is a "
        "state nobody asked for and reporting it as success is the failure this whole "
        "layer exists to prevent.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_CALIBRATION_ENABLED", "bool", "0",
        "Allow the Brain to observe whether its own past answers were judged right, by "
        "joining ``agent_action`` and ``message_answered`` to ``feedback_recorded`` on "
        "the ``message_id`` all three carry. Off means nothing spans turns: uncertainty "
        "can be reported within one response and no pattern in the mistakes is ever "
        "noticed, which is the current behaviour. On means a correctness rate is "
        "computed per account and per capability, reported as a Wilson interval rather "
        "than a bare percentage and never below twelve judged answers, with the "
        "unjudged count stated beside it because voluntary feedback is heavily "
        "selection-biased and silence is not approval. Requires "
        "UNDX_BRAIN_LEARNING_ENABLED in practice, since the window it reads comes from "
        "``learning.load``. The result is reported and never acted on: nothing here "
        "reaches ``selection``, because a capability that is often corrected has not "
        "been shown to have caused the correction, and down-ranking it on that evidence "
        "would be a causal claim made where nobody would see it.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_ENVELOPE_ENABLED", "bool", "0",
        "Allow the Brain to seal untrusted text into one uniform envelope before it "
        "reaches a prompt, instead of each source being fenced by whatever module "
        "happens to handle it. Off means today's arrangement: native context is "
        "key-allowlisted and clamped but unfenced, corpus excerpts get their own fence, "
        "the user turn is classified by ``pulse_ai_safety``, and live web search results "
        "get no fence at all while being rendered into the system message under the "
        "heading ``Approved PulseSoc knowledge``. On means every source is wrapped the "
        "same way, with a declaration naming its provenance and denying it authority "
        "placed before it and a reassertion placed after it, so a payload never has the "
        "last word. Breakout does not depend on the payload's cooperation: reserved tags "
        "inside it are escaped before rendering, so the closing fence appears exactly "
        "once whatever the payload contains. This flag gates the envelope *discipline*; "
        "it does not gate the escaping, which ``corpus.prompt_block`` applies "
        "unconditionally because it closes a confirmed breakout and changes nothing for "
        "any payload that was not attempting one. The envelope stops text escaping its "
        "position and does not stop it arguing from inside — persuasion within the fence "
        "remains a model-behaviour problem and is not claimed here.",
        fail="closed",
    ),
    # ------------------------------------------------------------ QA and rollout ----
    Flag(
        "UNDX_BRAIN_QA_ONLY", "bool", "1",
        "Restrict the Brain path to the QA cohort. On by default so that enabling the "
        "Brain in production does not, by itself, expose it to anybody.",
        fail="closed",
    ),
    Flag(
        "UNDX_BRAIN_ROLLOUT_PERCENT", "int", "0",
        "Percentage of non-QA users eligible for the Brain read path. Ignored entirely "
        "while UNDX_BRAIN_QA_ONLY is on.",
        fail="closed", minimum=0, maximum=100,
    ),
    Flag(
        "UNDX_BRAIN_WRITES_ROLLOUT_PERCENT", "int", "0",
        "Percentage eligible for Brain-planned writes. Kept separate from the read "
        "rollout because a read that is wrong is a bad answer and a write that is wrong "
        "is a changed account.",
        fail="closed", minimum=0, maximum=100,
    ),
    # ------------------------------------------------------------- observability ----
    Flag(
        "UNDX_BRAIN_METRICS_ENABLED", "bool", "1",
        "Emit Brain stage counters and durations to the existing log stream.",
        fail="open",
    ),
    Flag(
        "UNDX_DEGRADATION_TRACKING_ENABLED", "bool", "1",
        "Record when a knowledge source, tool, or provider answered in a degraded "
        "state, so a response built on partial evidence can say so.",
        fail="open",
    ),
)

BY_NAME: dict[str, Flag] = {flag.name: flag for flag in CATALOG}

# The deliberately small production contract. This is the operator-facing set that
# must travel with a release; the larger catalog above contains optional tuning knobs.
# No entry here is a credential, and missing authorization-shaped controls deny work.
MINIMUM_PRODUCTION_CONTRACT: tuple[dict[str, Any], ...] = (
    {"name": "UNDX_AGENT_ENABLED", "type": "bool", "default": "0", "required": True, "secret": False, "consumer": "services.undx_agent_policy.user_enabled", "safe_missing": "agent disabled", "health_field": "agent.enabled", "rollout_stage": "qa_reads"},
    {"name": "UNDX_AGENT_READS_ENABLED", "type": "bool", "default": "0", "required": True, "secret": False, "consumer": "services.undx_agent_policy.evaluate", "safe_missing": "reads disabled", "health_field": "agent.reads", "rollout_stage": "qa_reads"},
    {"name": "UNDX_AGENT_WRITES_ENABLED", "type": "bool", "default": "0", "required": True, "secret": False, "consumer": "services.undx_agent_policy.writes_available", "safe_missing": "writes disabled", "health_field": "agent.writes", "rollout_stage": "post_write_gate"},
    {"name": "UNDX_AGENT_DISABLE_WRITES", "type": "bool", "default": "1", "required": True, "secret": False, "consumer": "services.undx_agent_policy.writes_available", "safe_missing": "writes remain disabled because writes_enabled defaults off", "health_field": "agent.disable_writes", "rollout_stage": "initial"},
    {"name": "UNDX_AGENT_REQUIRE_VERIFICATION", "type": "bool", "default": "1", "required": True, "secret": False, "consumer": "services.undx_brain.config", "safe_missing": "verification remains required", "health_field": "verification.required", "rollout_stage": "initial"},
    {"name": "UNDX_AGENT_REQUIRE_AUDIT", "type": "bool", "default": "1", "required": True, "secret": False, "consumer": "services.undx_brain.config", "safe_missing": "audit remains required", "health_field": "audit.required", "rollout_stage": "initial"},
    {"name": "UNDX_AGENT_FAIL_CLOSED", "type": "bool", "default": "1", "required": True, "secret": False, "consumer": "services.undx_brain.config", "safe_missing": "fail closed", "health_field": "agent.fail_closed", "rollout_stage": "initial"},
    {"name": "UNDX_MEMORY_FAIL_CLOSED", "type": "bool", "default": "1", "required": True, "secret": False, "consumer": "services.undx_brain.memory", "safe_missing": "memory unavailable", "health_field": "brain.memory_fail_closed", "rollout_stage": "initial"},
    {"name": "UNDX_BRAIN_ENABLED", "type": "bool", "default": "0", "required": True, "secret": False, "consumer": "services.undx_brain.config.brain_available", "safe_missing": "Brain disabled", "health_field": "brain.enabled", "rollout_stage": "qa_reads"},
    {"name": "UNDX_BRAIN_QA_ONLY", "type": "bool", "default": "1", "required": True, "secret": False, "consumer": "services.undx_brain.rollout", "safe_missing": "QA-only", "health_field": "brain.qa_only", "rollout_stage": "qa_reads"},
    {"name": "UNDX_AGENT_QA_USER_IDS", "type": "csv_int", "default": "", "required": True, "secret": False, "consumer": "services.undx_agent_policy.user_enabled", "safe_missing": "empty cohort", "health_field": "agent.qa_cohort_configured", "rollout_stage": "qa_reads"},
)

#: Variables owned by other, older parts of UNDX that the Brain reads but does not
#: declare. Listed so :func:`unknown_undx_brain_vars` does not report them as typos.
FOREIGN_PREFIXES: tuple[str, ...] = (
    "UNDX_AGENT_", "UNDX_V2_", "UNDX_V4_", "UNDX_V5_", "UNDX_KERNEL_",
    "UNDX_IDENTITY_", "UNDX_DESKTOP_", "UNDX_ASSISTANT_", "UNDX_CANDIDATE",
    "UNDX_DISPLAY_", "UNDX_DESCRIPTION", "UNDX_CONVERSATION_", "UNDX_SYSTEM_",
    "UNDX_ACTIONS", "UNDX_ACTIVE_", "UNDX_PROCESS_", "UNDX_UNAVAILABLE_",
)


@dataclass(frozen=True)
class Resolution:
    """A resolved value plus how it was arrived at.

    ``notes`` is the part that matters. A value that was clamped, or fell back to a
    default because it did not parse, is still a usable value — but the difference
    between "the operator asked for 8" and "the operator asked for 800 and got 64" is
    exactly the kind of thing that is invisible in a dashboard and obvious in a report.
    """

    values: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()

    def __getitem__(self, name: str) -> Any:
        return self.values[name]

    def get(self, name: str, fallback: Any = None) -> Any:
        return self.values.get(name, fallback)


def _coerce(flag: Flag, raw: str, notes: list[str]) -> Any:
    if flag.kind == "bool":
        text = str(raw).strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
        # Neither spelling. Reading it as ``False`` would be the cheap answer and the
        # wrong one: ``UNDX_AGENT_REQUIRE_VERIFICATION=treu`` would then turn *off* the
        # requirement that a write is read back before it is called done, and the
        # operator's evidence that it worked would be that nothing complained. The
        # documented default is the safe reading here for the same reason it is for
        # ints and choices — it is the value chosen deliberately.
        notes.append(
            f"{flag.name}={raw!r} is not a boolean; using the default {flag.default!r} "
            f"({flag.fail}-by-default) rather than reading an unrecognised value as off"
        )
        return _bool(flag.default)
    if flag.kind == "csv":
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    if flag.kind == "int":
        value = _ascii_int(str(raw).strip())
        if value is None:
            notes.append(
                f"{flag.name}={raw!r} is not an integer; using the default {flag.default}"
            )
            return int(flag.default)
        low, high = flag.minimum, flag.maximum
        if low is not None and value < low:
            notes.append(f"{flag.name}={value} is below the minimum {low}; clamped")
            return low
        if high is not None and value > high:
            notes.append(f"{flag.name}={value} is above the maximum {high}; clamped")
            return high
        return value
    text = str(raw).strip()
    if flag.choices and text not in flag.choices:
        notes.append(
            f"{flag.name}={text!r} is not one of {', '.join(flag.choices)}; "
            f"using the default {flag.default!r}"
        )
        return flag.default
    return text


def resolve(env: dict[str, str] | None = None) -> Resolution:
    """Read every declared flag from ``env`` (default :data:`os.environ`).

    Never raises. A configuration error becomes a note and a documented default,
    because the alternative — a process that will not boot because somebody typed
    ``UNDX_PLANNER_MAX_STEPS=six`` — turns a cosmetic mistake into an outage.
    """
    source = os.environ if env is None else env
    notes: list[str] = []
    values: dict[str, Any] = {}
    for flag in CATALOG:
        raw = source.get(flag.name)
        if raw is None or str(raw).strip() == "":
            values[flag.name] = _coerce(flag, flag.default, [])
            if flag.required and raw is None:
                notes.append(
                    f"{flag.name} is unset; running on the documented default "
                    f"{flag.default!r} ({flag.fail}-by-default)"
                )
            continue
        values[flag.name] = _coerce(flag, str(raw), notes)
    return Resolution(values=values, notes=tuple(notes), unknown=unknown_undx_brain_vars(source))


def unknown_undx_brain_vars(env: dict[str, str] | None = None) -> tuple[str, ...]:
    """``UNDX_``-prefixed variables that match no declaration and no foreign prefix.

    This is the typo detector. ``UNDX_BRAIN_ENABLE`` set to 1 in a dashboard looks
    exactly like a working configuration until somebody notices the Brain never ran.
    """
    source = os.environ if env is None else env
    out = []
    for name in source:
        if not name.startswith("UNDX_") or name in BY_NAME:
            continue
        if any(name.startswith(prefix) for prefix in FOREIGN_PREFIXES):
            continue
        out.append(name)
    return tuple(sorted(out))


def flags(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Resolved values only, for call sites that do not care how they were reached."""
    return resolve(env).values


def describe_for_report(env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Catalog plus current disposition, with values withheld for secret-marked flags.

    The withholding is structural rather than a convention somebody has to remember:
    a flag marked ``secret`` reports ``"set"`` or ``"unset"`` and never its content, so
    a report generated from this function cannot leak one even if a secret-bearing flag
    is added later by somebody who did not read this docstring.
    """
    source = os.environ if env is None else env
    resolution = resolve(source)
    rows: list[dict[str, Any]] = []
    for flag in CATALOG:
        present = flag.name in source and str(source.get(flag.name, "")).strip() != ""
        rows.append({
            "name": flag.name,
            "purpose": flag.purpose,
            "kind": flag.kind,
            "default": flag.default,
            "required": flag.required,
            "secret": flag.secret,
            "fail": flag.fail,
            "redeploy": flag.redeploy,
            "environments": list(flag.environments),
            "rollback": flag.rollback,
            "set_in_environment": present,
            "effective": "withheld (secret)" if flag.secret else resolution.get(flag.name),
        })
    return rows


def brain_available(env: dict[str, str] | None = None) -> bool:
    """The one question the call sites actually ask.

    Deliberately not a composite of every sub-flag: the sub-flags gate *stages*, and a
    Brain that is enabled with every stage off is a legitimate configuration — it is
    what the first production deployment should look like.
    """
    return bool(flags(env).get("UNDX_BRAIN_ENABLED"))
