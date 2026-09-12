"""Why a request would go where it goes, and where the benchmark disagrees.

The question this answers
-------------------------
"Why did that request go to Groq?" is, before this module, unanswerable. The
response envelope carries `attempts`, which is what *happened* — the providers
tried and how each one ended. It does not carry why that list existed in that
order, and the transformations that produced it are invisible from outside:
`provider_priority` collapses to a single provider when multi-model mode is off,
prepends the configured default, appends OpenAI as a universal fallback, and
silently drops anything disabled. Four rules, none of them recorded, all of them
capable of making a routing table's stated order irrelevant.

The trap in writing an explainer
--------------------------------
The obvious implementation re-derives the order and describes it. That produces
a module which, the first time someone edits `provider_priority` without editing
this file, explains a decision that was never taken — fluently, with reasons,
and with no way for a reader to tell. An explanation that cannot be wrong is not
evidence; it is narration.

So `explain()` reconstructs the plan *and* calls the real `provider_priority`,
and publishes `consistent`. When the two disagree the reconstruction is
discarded and the record says the explainer is stale, because a wrong reason is
worse than no reason. `RouterDriftTest` fails the build on the same condition,
so the disagreement is normally caught long before a reader sees it.

The lane table itself now lives in `undx_router.LANE_PRIORITIES` — moved there
rather than copied here, for the reason there is one price table and one
privacy ladder.

A dry run, not a prophecy
-------------------------
The gates are evaluated exactly as the routing loop evaluates them — privacy
ceiling, then budget, then credential, then breaker, in that order, using the
router's own predicates rather than reimplementations. Nothing is called: no
provider is contacted and no money is spent, which is what makes this safe to
run from an admin surface.

What it cannot promise is that the request will go this way. Breaker state is
shared across nine processes and moves between the explanation and the call.
The record is what the router would do now.

Where the benchmark gets to argue
---------------------------------
`contradictions()` takes a `undx_benchmark` run and reports every place the
routing table ranks a provider above one the benchmark *separated* above it.
Only separated pairs count — an ordering that failed the significance test is
noise and has no standing here.

It reports; it does not act. §1 forbids promoting a provider to the global
default, and a module that rewrote the routing table from last night's
benchmark would be doing exactly that, with a cron job's judgement.
"""

from __future__ import annotations

from typing import Any

from services import undx_benchmark, undx_eval_corpus, undx_privacy

#: Lanes with fewer cases than this are flagged in `coverage()`.
#:
#: Six is the sign test's floor on *discordant pairs*, and a lane cannot produce
#: six of those from fewer than six cases — so this is a necessary threshold and
#: nowhere near a sufficient one. A lane would need six cases on which the two
#: providers actually disagreed, which for similar providers means many times
#: six. Every lane in this deployment currently falls short, `coverage()` says
#: so, and `contradictions()` is corpus-wide as a result rather than pretending
#: the lane-scoped question is answerable today.
MIN_LANE_CASES = 6

#: Reasons a provider is absent from the plan, or present but gated. The first
#: four come from `provider_priority`, the rest from the routing loop's gates,
#: in the order the loop applies them.
EXCLUSION_REASONS = (
    "unknown_provider", "disabled", "single_provider_mode",
    "privacy_refused", "budget_exceeded", "not_configured", "circuit_open",
)


def _router():
    import undx_router  # noqa: PLC0415

    return undx_router


def _reconstruct(router, category: str) -> tuple[list[str], dict[str, str]]:
    """Replay `provider_priority`'s four rules, keeping a reason per provider.

    Deliberately a replay and not a refactor of the router. Making
    `provider_priority` return its own reasons would be tidier and would put an
    explanation-shaped object on the hot path of every request, where its only
    consumer is an admin screen. The cost of a replay is that it can drift; that
    cost is paid by `consistent` below, which detects the drift instead of
    hiding it.
    """
    reasons: dict[str, str] = {}
    lane = category if category in router.LANE_PRIORITIES else router.DEFAULT_LANE
    table = list(router.LANE_PRIORITIES[lane])
    preferred = router.default_provider()

    if router.multi_model_mode():
        ordered = list(table)
        for index, name in enumerate(ordered):
            reasons[name] = "lane_leader" if index == 0 else f"lane_rank_{index + 1}"
    else:
        ordered = [preferred]
        reasons[preferred] = "single_provider_mode"

    if preferred not in ordered:
        ordered = [preferred, *ordered]
        reasons[preferred] = "configured_default"
    if "openai" not in ordered:
        ordered.append("openai")
        reasons["openai"] = "universal_fallback"

    plan = []
    for name in dict.fromkeys(ordered):
        if name in router.PROVIDERS and router.provider_enabled(name):
            plan.append(name)
    return plan, reasons


def explain(message: str = "", *, privacy_class: str | None = None,
            call_domain: str | None = None, require_json: bool = False,
            detail: bool = False) -> dict[str, Any]:
    """The routing decision this request would get, with a reason per provider.

    `detail` adds the refusal sentences from the privacy and budget gates. Those
    quote configuration values — a ceiling name, a dollar figure, a provider's
    month-to-date spend — so they are off by default and this output is safe on
    an operator surface without them. It is *not* safe on an unauthenticated
    one in either mode: the plan itself discloses which vendors this deployment
    uses and which are switched off.

    `privacy_class`, `call_domain` and `require_json` are the three things a
    caller declares about a request, and this function has to accept all three or
    it explains a *different* request than the one the caller will make. It
    previously accepted only the first, and mishandled that:

      * The privacy gate was called only `if privacy_class`. An omitted class
        normalises to CONFIDENTIAL, so the guard that looked like it was tolerating
        a missing value was discarding the default ceiling. With everything keyed,
        this reported Perplexity as the first choice for requests the routing loop
        refuses there — naming a provider the request cannot reach.
      * `require_json` did not exist here, so the `capability_unmet` refusals that
        the loop applies *before reading a credential* were invisible. Both
        `scam_shield` and `undx_capability_planner` route with it.
      * `_domain_ordered` was never applied, so the order shown was the pre-domain
        lane. This one is **latent, not live**, and the distinction is worth keeping:
        `undx_call_domain._PREFERENCE` is currently empty, `routing_preference`
        returns `()` for every domain, and `_domain_ordered` is therefore a no-op
        today — so the omission produced no wrong output. It is wired up here so
        that the first domain to declare a preference does not silently make this
        surface wrong, which is the failure mode an absent call has and a present
        one does not.

    All three are now delegated to `undx_router._gate`, the same function the
    routing loops call, so a predicted chain and an executed chain cannot disagree
    about a gate by construction. That is the point of this change: the ladder was
    transcribed four times and the two copies whose only job was to be accurate
    about the other two were the ones that had drifted.
    """
    router = _router()
    classification = router.classify_request(message or "")
    category = str(classification.get("category") or "")
    lane = category if category in router.LANE_PRIORITIES else router.DEFAULT_LANE

    reconstructed, reasons = _reconstruct(router, category)
    # `_domain_ordered` last, exactly as both routing loops apply it: on a plan the
    # kill switch and any explicit `providers=` list have already settled. The
    # consistency check below compares the *lane* reconstruction, so it is taken
    # before the domain permutation rather than after — a reconstruction that
    # reordered too would agree with itself and say nothing.
    lane_plan = router.provider_priority(classification)
    consistent = reconstructed == lane_plan
    actual = router._domain_ordered(lane_plan, call_domain)

    excluded: list[dict[str, str]] = []
    for name in router.PROVIDERS:
        if name not in actual:
            excluded.append({"provider": name, "reason": (
                "disabled" if not router.provider_enabled(name) else "not_in_lane")})

    steps: list[dict[str, Any]] = []
    would_try: list[str] = []
    normalised = undx_privacy.normalise(privacy_class) if privacy_class else ""
    # One budget read for the whole plan, matching the routing loop: seven
    # providers asking the same question seven times is the shape this control
    # was written to avoid.
    budget = router._budget_snapshot()

    for position, name in enumerate(actual, start=1):
        # One ladder, the router's own. Not a transcription of it: an `if/elif`
        # chain here is how this surface came to disagree with the loop it
        # describes about three separate gates. `privacy_class` is passed straight
        # through, including when it is None — that is the whole fix, not a
        # simplification.
        refused = router._gate(name, privacy_class=privacy_class, budget=budget,
                               require_json=require_json) or {}
        gate = refused.get("status", "")
        note = refused.get("detail", "")
        row: dict[str, Any] = {
            "provider": name,
            "label": router.PROVIDERS[name].label,
            "position": position,
            "reason": reasons.get(name, "unknown"),
            "gate": gate,
        }
        if gate and detail:
            row["detail"] = note
        steps.append(row)
        if not gate:
            would_try.append(name)

    return {
        "category": category,
        "lane": lane,
        "signals": list(classification.get("signals") or []),
        "classification_reason": classification.get("reason", ""),
        "lane_table": list(router.LANE_PRIORITIES[lane]),
        "plan": actual,
        "steps": steps,
        "would_try": would_try,
        "first_choice": would_try[0] if would_try else "",
        # An empty plan is a request that will fail before it reaches a vendor.
        # Named here rather than left to be inferred from an empty list,
        # because the caller that most needs to know is the one not reading
        # carefully.
        "would_fail": not would_try,
        "excluded": excluded,
        "router_enabled": router.router_enabled(),
        "multi_model": router.multi_model_mode(),
        "default_provider": router.default_provider(),
        "privacy_class": normalised,
        # False means the reasons above describe a plan the router did not make.
        # `plan` is still the truth; `steps` is not.
        "consistent": consistent,
        "reconstruction": reconstructed if not consistent else [],
        "note": ("A dry run. Breaker state is shared across processes and can "
                 "change between this record and the call."),
    }


def coverage() -> dict[str, Any]:
    """How much corpus each routing lane has behind it.

    Published so that a contradiction is never read without its sample size.
    `current_web` appears as uncovered on purpose: the corpus cannot grade
    freshness, so no benchmark can ever speak to the one lane whose ordering
    rests on a structural claim.
    """
    router = _router()
    counts = undx_eval_corpus.lane_counts()
    lanes = {}
    for lane in router.LANE_PRIORITIES:
        cases = counts.get(lane, 0)
        lanes[lane] = {"cases": cases, "sufficient": cases >= MIN_LANE_CASES}
    return {
        "lanes": lanes,
        "uncovered": sorted(name for name, row in lanes.items() if not row["cases"]),
        "thin": sorted(name for name, row in lanes.items()
                       if row["cases"] and not row["sufficient"]),
        "min_lane_cases": MIN_LANE_CASES,
        "corpus_version": undx_eval_corpus.CORPUS_VERSION,
    }


def contradictions(result: dict[str, Any]) -> dict[str, Any]:
    """Where the routing table ranks a provider above one the benchmark beat it with.

    Only pairs `undx_benchmark.compare` actually separated are considered. An
    ordering that failed the significance test says nothing, and letting it
    contribute here would launder noise into a routing argument — which is the
    single thing §1 was written to prevent.

    The comparison is corpus-wide, not lane-specific, and says so in every row.
    No lane holds enough cases for a lane-scoped sign test to reach significance
    (see `coverage`), so a lane-scoped version of this function would be
    permanently empty and would read as "no contradictions" rather than as "this
    question cannot be asked yet". A finding here means the provider ranked
    lower is better at instruction adherence and reasoning *overall*; it is a
    reason to look at the lane, not a fact about it.
    """
    router = _router()
    if not result.get("corpus_version"):
        return {"ok": False, "findings": [], "supported": [],
                "reason": "this run does not record a corpus version, so it "
                          "cannot be attributed to a set of questions"}

    separated: dict[tuple[str, str], dict[str, Any]] = {}
    names = [name for name, row in (result.get("providers") or {}).items()
             if row.get("score") is not None]
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            verdict = undx_benchmark.compare(result, first, second)
            if verdict["better"]:
                loser = second if verdict["better"] == first else first
                separated[(verdict["better"], loser)] = verdict

    counts = undx_eval_corpus.lane_counts()
    findings: list[dict[str, Any]] = []
    supported: list[dict[str, Any]] = []
    for lane, table in router.LANE_PRIORITIES.items():
        for position, ahead in enumerate(table):
            for behind in table[position + 1:]:
                if (behind, ahead) in separated:
                    verdict = separated[(behind, ahead)]
                    findings.append(_row(lane, ahead, behind, counts, verdict,
                                         better=behind))
                elif (ahead, behind) in separated:
                    supported.append(_row(lane, ahead, behind, counts,
                                          separated[(ahead, behind)],
                                          better=ahead))
    return {
        "ok": not findings,
        "findings": findings,
        "supported": supported,
        "separated_pairs": len(separated),
        "corpus_version": result.get("corpus_version"),
        "scope": "corpus-wide",
        "reason": "",
        "note": ("A contradiction is a reason to re-examine a lane, not a "
                 "mandate to reorder it. §1 requires a person."),
    }


def _row(lane: str, ahead: str, behind: str, counts: dict[str, int],
         verdict: dict[str, Any], better: str) -> dict[str, Any]:
    return {
        "lane": lane,
        "ranked_ahead": ahead,
        "ranked_behind": behind,
        "benchmark_favours": better,
        "p_value": verdict["p_value"],
        "shared_cases": verdict["shared_cases"],
        # The number that stops a corpus-wide result being read as a lane fact.
        "lane_cases": counts.get(lane, 0),
        "lane_is_covered": counts.get(lane, 0) >= MIN_LANE_CASES,
    }


__all__ = [
    "MIN_LANE_CASES", "EXCLUSION_REASONS", "explain", "coverage",
    "contradictions",
]
