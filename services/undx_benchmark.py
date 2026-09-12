"""Run the golden corpus against real providers, and say when the result is not evidence.

What this is for
----------------
§1 of the mission brief forbids promoting any provider to the global default and
requires benchmark evidence for routing changes. This module produces that
evidence — and, more importantly, refuses to produce it when a run does not
contain any. A benchmark that always returns a ranking is a benchmark that will
eventually be used to justify a change the data does not support, because the
ranking looks the same either way.

So `compare()` has three outcomes, not two, and the third is the common one on a
corpus this size.

Three outcomes, not two
-----------------------
Two providers are compared on the cases *both of them answered*, and only the
cases where they disagreed carry information: if both passed a case, it
separates nobody. That leaves `b` cases A won and `c` cases B won, and an exact
two-sided sign test on those `b + c` discordant pairs.

The arithmetic sets its own floor. With five or fewer disagreements even a clean
sweep gives p = 0.0625, so no run can reach significance without at least six
cases separating the two providers. That is a feature: it means nobody has to
remember a minimum-sample rule, because the test will not clear α on a sample
too small to clear it honestly.

Wrong and unanswered are different numbers
------------------------------------------
A provider that returned 429 on eleven cases did not get eleven answers wrong.
Folding transport failures into the score measures uptime and prints it as
intelligence, which is the same conflation §19 removed from the health states
and the same one `undx_fabric_health` removed from the dashboard. So `score` is
`passed / answered`, `answered` is published next to it, and an unanswered case
is counted and named with its reason.

This means a score can be computed over three cases. It is still published —
hiding it would be another kind of lie — but `answered` sits beside it and
`compare()` will not build a verdict on it.

What it costs
-------------
Every case is a real completion. Twenty-one cases against seven providers is
147 paid calls, so: spend is metered through the same `undx_cost` ledger as
production traffic, `undx_cost.refusal` is consulted per provider before the
first call and the provider is skipped rather than scored if a budget says no,
and `estimate()` exists so an operator can see the bill before agreeing to it.

Determinism is temperature 0, which is not determinism. Providers are free to
return different text for identical input, so two runs will not always agree.
That is why the comparison is a statistical test rather than a subtraction.

It is an operator action, not a request path — the same rule as
`undx_model_audit`, for the same reason: nine workers each running a benchmark
is a 1,323-call herd against providers that may already be failing.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from typing import Any

from services import undx_cost, undx_eval_corpus, undx_health, undx_privacy

log = logging.getLogger(__name__)

#: Significance threshold for `compare`. Not configurable: a benchmark whose
#: bar moves is a benchmark that can be made to agree with a decision already
#: taken.
ALPHA = 0.05

#: Per-case ceiling. Generous — the corpus asks small questions, and a provider
#: that is merely slow should be scored on its answer rather than recorded as
#: unreachable.
TIMEOUT_SECONDS = 45

#: Deterministic-as-available. See the module docstring on why this is not
#: determinism.
TEMPERATURE = 0.0

#: Reasons a case has no answer to mark. Kept as a closed set so the summary
#: can be read without inspecting individual rows.
UNANSWERED_REASONS = ("budget", "privacy", "no_key", "disabled",
                      "unknown_provider", "transport", "empty")


def _router():
    """Lazy, for the same reason `undx_model_audit` is: scripts load this."""
    import undx_router  # noqa: PLC0415

    return undx_router


def _estimated_tokens(text: str) -> int:
    """Characters over four. A rule of thumb, and labelled as one.

    Used only by `estimate()`, never by the ledger — recorded spend always comes
    from the provider's own usage numbers, because a benchmark that guessed its
    own cost would make the month's totals wrong in exactly the way the ledger
    exists to prevent.
    """
    return max(1, len(text or "") // 4)


def estimate(providers: Any = None, *, lane: str | None = None,
             tag: str | None = None) -> dict[str, Any]:
    """What a run would cost, before spending it. No network calls.

    `cost_usd` is a rough upper bound that assumes every case fills its output
    budget. `cost_complete` is false when any provider's model has no verified
    price, in which case the figure is a floor and the real bill is unknown —
    the same distinction `undx_cost` draws everywhere else.

    An unpriced provider estimates to `None`, not to `0.0`. Only two of the
    seven models in this deployment have a price read off a vendor console, so
    the zero would be the common case, and "this benchmark costs $0.008" over a
    table that is mostly unpriced is a worse answer than "this part is unknown".
    """
    router = _router()
    names = _provider_names(router, providers)
    selected = undx_eval_corpus.cases(lane=lane, tag=tag)
    total = 0.0
    complete = True
    rows: dict[str, dict[str, Any]] = {}
    for name in names:
        model = router._model(name) if name in router.PROVIDERS else ""
        priced = undx_cost.is_priced(model)
        spend = 0.0
        for case in selected:
            spend += undx_cost.estimate_cost_usd(
                model,
                _estimated_tokens(case.system) + _estimated_tokens(case.prompt),
                case.max_tokens) or 0.0
        complete = complete and priced
        rows[name] = {"model": model, "calls": len(selected),
                      "cost_usd": round(spend, 6) if priced else None,
                      "priced": priced}
        total += spend
    return {
        "corpus_version": undx_eval_corpus.CORPUS_VERSION,
        "cases": len(selected),
        "providers": rows,
        "calls": len(selected) * len(names),
        "cost_usd": round(total, 6),
        "unpriced_providers": sorted(n for n, r in rows.items() if not r["priced"]),
        "cost_complete": complete,
        "method": "estimate",
    }


def _provider_names(router, providers: Any) -> list[str]:
    if providers:
        return [str(p).strip().lower() for p in providers]
    return list(router.PROVIDERS)


def _skip_reason(router, name: str, budget_snapshot: dict[str, Any]) -> str:
    """Why this provider will not be called at all, or empty.

    Checked once per provider rather than once per case: the answer cannot
    change mid-run for any of these reasons except budget, and re-reading the
    budget after every case would let a run half-complete a provider and then
    publish a score over whichever cases happened to fit. A partial score
    attributed to the provider rather than to the budget is a misattribution,
    so the decision is taken once, before the first call.
    """
    if name not in router.PROVIDERS:
        return "unknown_provider"
    if not router.provider_enabled(name):
        return "disabled"
    if not router._api_key(name):
        return "no_key"
    model = router._model(name)
    # Fail closed. SYNTHETIC is the bottom rung so this should never refuse —
    # which is exactly why it is checked: a ceiling misconfigured to refuse
    # everything must stop the benchmark too, not be waved through because the
    # content is "only" fixtures.
    if not undx_privacy.provider_accepts(name, undx_eval_corpus.PRIVACY_CLASS, model):
        return "privacy"
    if undx_cost.refusal(budget_snapshot, name, model):
        return "budget"
    return ""


def run(providers: Any = None, *, lane: str | None = None, tag: str | None = None,
        timeout: int = TIMEOUT_SECONDS, record: bool = True,
        max_cases: int | None = None) -> dict[str, Any]:
    """Grade every selected case against every selected provider. Never raises.

    Returns per-provider scores and the raw per-case outcomes, keyed so that
    `compare()` can pair them. The transcript of answers is *not* returned:
    twenty-one answers times seven providers is a payload nobody reads, and the
    pass/fail plus the failed assertion kinds are what a disagreement is
    settled with.
    """
    router = _router()
    names = _provider_names(router, providers)
    selected = undx_eval_corpus.cases(lane=lane, tag=tag)
    if max_cases:
        selected = selected[:int(max_cases)]
    started = time.time()
    budget_snapshot = undx_cost.month_snapshot()

    providers_out: dict[str, dict[str, Any]] = {}
    for name in names:
        skip = _skip_reason(router, name, budget_snapshot)
        if skip:
            log.info("UNDX benchmark skipping %s: %s", name, skip)
            providers_out[name] = _empty_provider(router, name, len(selected), skip)
            continue
        providers_out[name] = _run_provider(router, name, selected, timeout, record)

    return {
        "ok": any(p["answered"] for p in providers_out.values()),
        "corpus_version": undx_eval_corpus.CORPUS_VERSION,
        "case_ids": [case.id for case in selected],
        "lane": lane or "",
        "tag": tag or "",
        "providers": providers_out,
        "lane_counts": undx_eval_corpus.lane_counts(),
        "cost_usd": round(sum(p["cost_usd"] or 0.0
                              for p in providers_out.values()), 6),
        "cost_complete": all(p["cost_complete"] for p in providers_out.values()),
        "ran_at": time.time(),
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def _empty_provider(router, name: str, attempted: int, skip: str) -> dict[str, Any]:
    config = router.PROVIDERS.get(name)
    return {
        "provider": name,
        "label": config.label if config else name,
        "model": router._model(name) if config else "",
        # None, not 0.0. A provider that was never called did not score zero,
        # and a dashboard that renders 0.0 for "we did not ask" is the fake
        # green this fabric has spent six phases removing.
        "score": None,
        "passed": 0,
        "answered": 0,
        "attempted": attempted,
        "unanswered": attempted,
        "skipped": skip,
        "unanswered_reasons": {skip: attempted} if attempted else {},
        "lane_scores": {},
        "failed_cases": [],
        "cost_usd": 0.0,
        "cost_complete": True,
        "median_latency_ms": None,
        "outcomes": {},
    }


def _run_provider(router, name: str, selected: tuple, timeout: int,
                  record: bool) -> dict[str, Any]:
    config = router.PROVIDERS[name]
    model = router._model(name)
    out = _empty_provider(router, name, len(selected), "")
    latencies: list[int] = []
    spend = 0.0
    priced = True
    unanswered: dict[str, int] = {}
    by_lane: dict[str, list[bool]] = {}

    for case in selected:
        began = time.time()
        try:
            result = router.CALLERS[name](
                case.system, "", [], int(timeout),
                user_content=case.prompt, temperature=TEMPERATURE,
                max_tokens=case.max_tokens,
            )
            text = router._clean_text(result.get("text"), 4000)
            usage = result.get("usage") or router._normalise_usage(name, model, None)
            if record:
                undx_cost.record(usage)
            spend += usage.get("cost_usd") or 0.0
            priced = priced and usage.get("cost_usd") is not None
            latencies.append(int((time.time() - began) * 1000))
            if not text:
                # An empty completion is not a wrong answer. It is the shape a
                # retired model takes on at least one provider, and
                # `undx_model_audit` already treats it as a fault rather than a
                # response — the two surfaces must not disagree about it.
                unanswered["empty"] = unanswered.get("empty", 0) + 1
                out["outcomes"][case.id] = {"answered": False, "reason": "empty"}
                if record:
                    undx_health.record_failure(name, undx_health.STATUS_RESPONSE,
                                               "empty completion")
                continue
            marked = undx_eval_corpus.grade(case, text)
            out["outcomes"][case.id] = {"answered": True,
                                        "passed": marked["passed"],
                                        "failed": marked["failed"]}
            by_lane.setdefault(case.lane, []).append(marked["passed"])
            if marked["passed"]:
                out["passed"] += 1
            else:
                out["failed_cases"].append(case.id)
            if record:
                undx_health.record_success(name)
        except Exception as exc:  # noqa: BLE001 - one bad case must not end the run
            detail = router._safe_error(exc)
            status = undx_health.classify_failure(exc)
            log.warning("UNDX benchmark %s failed case %s: %s", name, case.id, status)
            unanswered["transport"] = unanswered.get("transport", 0) + 1
            out["outcomes"][case.id] = {"answered": False, "reason": "transport",
                                        "status": status}
            latencies.append(int((time.time() - began) * 1000))
            if record:
                undx_health.record_failure(name, status, detail)

    answered = sum(1 for row in out["outcomes"].values() if row["answered"])
    out.update({
        "answered": answered,
        "unanswered": len(selected) - answered,
        "unanswered_reasons": unanswered,
        "score": round(out["passed"] / answered, 4) if answered else None,
        "lane_scores": {lane: round(sum(marks) / len(marks), 4)
                        for lane, marks in sorted(by_lane.items())},
        "cost_usd": round(spend, 6),
        "cost_complete": priced,
        "median_latency_ms": int(statistics.median(latencies)) if latencies else None,
        "label": config.label,
        "model": model,
    })
    return out


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------

def sign_test(wins_a: int, wins_b: int) -> float:
    """Two-sided exact binomial p-value on discordant pairs.

    Written out rather than pulled from scipy, which this deployment does not
    install. `math.comb` is exact for the counts a corpus this size produces,
    so there is no approximation to go wrong quietly.
    """
    trials = wins_a + wins_b
    if trials <= 0:
        return 1.0
    smaller = min(wins_a, wins_b)
    tail = sum(math.comb(trials, k) for k in range(smaller + 1))
    return min(1.0, 2.0 * tail / (2 ** trials))


def compare(result: dict[str, Any], a: str, b: str,
            alpha: float = ALPHA) -> dict[str, Any]:
    """Is there evidence that one of these two providers is better on this corpus?

    The verdict is one of `a`, `b`, or `""` — and `""` is not a tie. It means the
    run does not settle the question, which is what an operator needs to hear
    before changing a routing table on the strength of it.

    Only cases *both* providers answered are compared, because a case one of
    them never received tells us nothing about which is better at it. That is
    also why the count of dropped cases is returned: a comparison over six
    shared cases out of twenty-one is a different object from one over twenty,
    and the number is the only thing that says so.
    """
    providers = result.get("providers") or {}
    left, right = providers.get(a), providers.get(b)
    if not left or not right:
        return _no_comparison(a, b, "one of these providers is not in this run")
    outcomes_a = left.get("outcomes") or {}
    outcomes_b = right.get("outcomes") or {}

    shared = [case_id for case_id in result.get("case_ids") or []
              if (outcomes_a.get(case_id) or {}).get("answered")
              and (outcomes_b.get(case_id) or {}).get("answered")]
    if not shared:
        return _no_comparison(a, b, "no case was answered by both providers")

    wins_a = [c for c in shared
              if outcomes_a[c]["passed"] and not outcomes_b[c]["passed"]]
    wins_b = [c for c in shared
              if outcomes_b[c]["passed"] and not outcomes_a[c]["passed"]]
    p_value = sign_test(len(wins_a), len(wins_b))
    decisive = p_value < alpha and len(wins_a) != len(wins_b)
    better = ""
    if decisive:
        better = a if len(wins_a) > len(wins_b) else b

    return {
        "a": a,
        "b": b,
        "better": better,
        "p_value": round(p_value, 6),
        "alpha": alpha,
        "shared_cases": len(shared),
        "dropped_cases": len(result.get("case_ids") or []) - len(shared),
        "a_only_wins": sorted(wins_a),
        "b_only_wins": sorted(wins_b),
        "agreed": len(shared) - len(wins_a) - len(wins_b),
        "corpus_version": result.get("corpus_version", ""),
        "reason": "" if better else (
            f"{len(wins_a) + len(wins_b)} of {len(shared)} shared cases separated "
            f"them (p={p_value:.3f}); this run is not evidence of a difference"),
    }


def _no_comparison(a: str, b: str, reason: str) -> dict[str, Any]:
    return {"a": a, "b": b, "better": "", "p_value": 1.0, "alpha": ALPHA,
            "shared_cases": 0, "dropped_cases": 0, "a_only_wins": [],
            "b_only_wins": [], "agreed": 0, "corpus_version": "",
            "reason": reason}


def ranking(result: dict[str, Any], alpha: float = ALPHA) -> dict[str, Any]:
    """Scores in order, plus the pairs the run can actually separate.

    The ordered list is descriptive and is labelled that way. `separated` holds
    only the pairs that cleared the sign test, and it is the sole part of this
    output §1 accepts as evidence for a routing change — an ordering where every
    adjacent pair failed the test is seven providers in a row that this run says
    nothing about.
    """
    scored = [(name, row) for name, row in (result.get("providers") or {}).items()
              if row.get("score") is not None]
    scored.sort(key=lambda item: (-item[1]["score"], item[0]))
    separated = []
    for index, (name, _) in enumerate(scored):
        for other, _ in scored[index + 1:]:
            verdict = compare(result, name, other, alpha=alpha)
            if verdict["better"]:
                separated.append(verdict)
    return {
        "order": [{"provider": name, "score": row["score"],
                   "answered": row["answered"], "attempted": row["attempted"],
                   "median_latency_ms": row["median_latency_ms"],
                   "cost_usd": row["cost_usd"]}
                  for name, row in scored],
        "separated": separated,
        "evidence": bool(separated),
        "corpus_version": result.get("corpus_version", ""),
        "note": ("Order is descriptive. Only pairs in `separated` cleared the "
                 "significance test; the rest of the ordering is noise."),
    }


def comparable(first: dict[str, Any], second: dict[str, Any]) -> str:
    """Empty if two runs may be compared, else why not.

    Refused rather than warned about. A provider that "improved" between two
    runs which graded different questions did not improve, and a warning printed
    above a table is read by nobody.
    """
    if first.get("corpus_version") != second.get("corpus_version"):
        return (f"corpus version {first.get('corpus_version')!r} vs "
                f"{second.get('corpus_version')!r}: different questions")
    if list(first.get("case_ids") or []) != list(second.get("case_ids") or []):
        return "the two runs graded different case sets"
    return ""


__all__ = [
    "ALPHA", "TIMEOUT_SECONDS", "TEMPERATURE", "UNANSWERED_REASONS",
    "estimate", "run", "compare", "ranking", "sign_test", "comparable",
]
