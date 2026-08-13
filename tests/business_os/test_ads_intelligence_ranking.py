"""Explainable ranker v1 — no pay-to-win, no silent failure, no frozen leaderboard.

The properties defended here:

* **Money is absent.** Advertisers do not bid on this platform; prices come
  from a published rate card. A ranker that read a price would be introducing
  pay-to-win rather than bounding it, so there is a test asserting no money
  concept reaches this module at all.

* **A rejected creative is dropped, not scored zero.** Zero still wins an
  auction with one entrant.

* **Fatigue multiplies rather than adds**, so a strong score elsewhere cannot
  outweigh it.

* **Unmeasured is neutral, not zero**, or a new creative could never earn the
  impressions that would let it be measured.

* **It never fails an ad request** — any error degrades to the canonical
  rotation strategy.

    python tests/business_os/test_ads_intelligence_ranking.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ads_intel_rank_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)
os.environ.setdefault("ADS_INTEL_SUBJECT_SALT", "test-salt-ranking")

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import inspect  # noqa: E402

from services.business_os.ads_intelligence import ranking, taxonomy  # noqa: E402


def _assert(cond, detail=""):
    if not cond:
        raise AssertionError(detail)


def _candidate(creative_id, campaign_id="camp-1", eligible=True):
    return {"creative_id": creative_id, "campaign_id": campaign_id,
            "eligible": eligible}


def _ctx(**kwargs):
    from services.business_os.ads_intelligence import context as _context
    base = {"surface": "feed", "content_category": "fitness"}
    base.update(kwargs)
    return _context.describe(base)


class _FakeConn:
    """Stands in for the database so ranking rules can be tested exhaustively."""

    def __init__(self, *, affinities=None, categories=None, trends=None):
        self.affinities = affinities or {}
        self.categories = categories or {}
        self.trends = trends or {}


def _patched(monkey, *, affinities=None, categories=None, trends=None):
    """Swap the three reads `rank` performs. Returns a restore callable."""
    from services.business_os.ads_intelligence import (
        interest as _interest, performance as _performance)
    originals = (_interest.affinities_for, _interest.campaign_category,
                 _performance.creative_trend)

    _interest.affinities_for = lambda conn, subject, **kw: dict(affinities or {})
    _interest.campaign_category = lambda conn, cid: (categories or {}).get(cid)

    def _trend(conn, creative_id, **kw):
        return (trends or {}).get(creative_id, {
            "recent": {}, "state": "INSUFFICIENT_DATA"})
    _performance.creative_trend = _trend

    def _restore():
        (_interest.affinities_for, _interest.campaign_category,
         _performance.creative_trend) = originals
    return _restore


# --------------------------------------------------------------------------- #
# No pay-to-win
# --------------------------------------------------------------------------- #

def test_no_money_concept_reaches_the_ranker():
    """Advertisers do not bid; a price component would create the problem."""
    source = inspect.getsource(ranking)
    banned = ("bid", "price", "cpm", "cpc", "budget", "spend_cents",
              "unit_price", "revenue", "advertiser_tier")
    body = "\n".join(
        line for line in source.splitlines()
        if not line.strip().startswith("#")
    )
    # The module docstring explains why these are absent, so scan code only.
    code = body.split('"""', 2)[-1]
    for word in banned:
        _assert(word not in code.lower(),
                f"{word!r} appears in ranking code — money must not affect rank")


def test_the_weights_are_declared_and_sum_to_one():
    _assert(abs(sum(ranking.WEIGHTS.values()) - 1.0) < 1e-9, ranking.WEIGHTS)
    for name, weight in ranking.WEIGHTS.items():
        _assert(0.0 < weight < 1.0, f"{name}={weight}")


def test_every_score_is_bounded():
    """An unbounded component would let one signal dominate all the others."""
    extreme = ranking.score_candidate(
        _candidate("c-1"), described=_ctx(), affinities={"fitness": 10_000.0},
        campaign_category="fitness",
        summary={"ctr_on_viewable": 5.0, "viewable": 0},
        fatigue_state="HEALTHY")
    _assert(0.0 <= extreme["score"] <= 1.0, extreme["score"])


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #

def test_an_unmeasured_creative_is_neutral_not_zero():
    """Zero would stop it ever earning the data that would let it be measured."""
    part = ranking.quality_component(None)
    _assert(part["value"] == ranking.NEUTRAL, part)
    thin = ranking.quality_component({"ctr_on_viewable": None})
    _assert(thin["value"] == ranking.NEUTRAL, thin)


def test_a_viewer_with_no_history_is_not_penalised():
    """A new viewer must not be systematically shown worse ads."""
    part = ranking.affinity_component({}, "fitness")
    _assert(part["value"] == ranking.NEUTRAL, part)


def test_a_suppressed_category_floors_at_zero_rather_than_going_negative():
    """A negative component would drag down a creative's unrelated strengths."""
    part = ranking.affinity_component({"fitness": -40.0}, "fitness")
    _assert(part["value"] == 0.0, part)
    _assert(part["detail"] == "SUPPRESSED", part)


def test_exploration_favours_the_under_sampled_and_expires():
    fresh = ranking.exploration_component({"viewable": 0})
    partly = ranking.exploration_component(
        {"viewable": taxonomy.MIN_IMPRESSIONS_FOR_CTR // 2})
    done = ranking.exploration_component(
        {"viewable": taxonomy.MIN_IMPRESSIONS_FOR_CTR})
    _assert(fresh["value"] > partly["value"] > done["value"], (fresh, partly, done))
    _assert(done["value"] == 0.0, "exploration must stop once data exists")


def test_the_exploration_weight_respects_its_bound():
    """exploration <= quality * (1 - NEUTRAL).

    An unmeasured creative collects NEUTRAL quality *and* full exploration. If
    those stack higher than a perfect measured creative's quality, the newest
    creative wins forever and the ranker permanently prefers churn to quality.
    Pinned as arithmetic because it is a relationship between weights, and a
    retune of either one can break it without touching any logic.
    """
    bound = ranking.WEIGHTS["quality"] * (1.0 - ranking.NEUTRAL)
    _assert(ranking.WEIGHTS["exploration"] <= bound + 1e-9,
            f"exploration={ranking.WEIGHTS['exploration']} exceeds its bound "
            f"of {bound}: a brand-new creative would outrank a proven one")


def test_exploration_still_beats_a_merely_average_creative():
    """The other side of the bound — exploration has to actually do something."""
    unproven = ranking.score_candidate(
        _candidate("new"), described=_ctx(content_category=None), affinities={},
        campaign_category=None, summary={"viewable": 0})
    average = ranking.score_candidate(
        _candidate("avg"), described=_ctx(content_category=None), affinities={},
        campaign_category=None,
        summary={"viewable": 10_000, "ctr_on_viewable": 0.005})
    _assert(unproven["score"] > average["score"],
            "exploration cannot lift an untested creative past a mediocre one, "
            "so nothing new will ever be measured")


def test_exploration_cannot_beat_a_proven_creative_on_its_own():
    """Otherwise every impression goes to whatever is newest."""
    unproven = ranking.score_candidate(
        _candidate("new"), described=_ctx(content_category=None), affinities={},
        campaign_category=None, summary={"viewable": 0})
    proven = ranking.score_candidate(
        _candidate("old"), described=_ctx(content_category=None), affinities={},
        campaign_category=None,
        summary={"viewable": 10_000, "ctr_on_viewable": 0.02})
    _assert(proven["score"] > unproven["score"],
            f"exploration overtook proven performance: "
            f"{proven['score']} vs {unproven['score']}")


def test_context_and_affinity_both_move_the_score():
    base = ranking.score_candidate(
        _candidate("c"), described=_ctx(content_category="automotive"),
        affinities={}, campaign_category="fitness")
    better_context = ranking.score_candidate(
        _candidate("c"), described=_ctx(content_category="fitness"),
        affinities={}, campaign_category="fitness")
    better_affinity = ranking.score_candidate(
        _candidate("c"), described=_ctx(content_category="automotive"),
        affinities={"fitness": 25.0}, campaign_category="fitness")
    _assert(better_context["score"] > base["score"], "context did not matter")
    _assert(better_affinity["score"] > base["score"], "affinity did not matter")


# --------------------------------------------------------------------------- #
# Fatigue
# --------------------------------------------------------------------------- #

def test_fatigue_multiplies_so_it_cannot_be_outweighed():
    strong = {"described": _ctx(content_category="fitness"),
              "affinities": {"fitness": 25.0}, "campaign_category": "fitness",
              "summary": {"viewable": 10_000, "ctr_on_viewable": 0.02}}
    healthy = ranking.score_candidate(_candidate("c"), fatigue_state="HEALTHY",
                                      **strong)
    fatigued = ranking.score_candidate(_candidate("c"), fatigue_state="FATIGUED",
                                       **strong)
    _assert(fatigued["score"] < healthy["score"] * 0.5,
            f"a fatigued creative kept its score: {fatigued['score']} vs "
            f"{healthy['score']}")


def test_rejected_is_not_a_multiplier():
    """REJECTED must be handled by dropping, so a multiplier would be a bug."""
    _assert("REJECTED" not in ranking.FATIGUE_MULTIPLIERS,
            "REJECTED has a multiplier — it should be dropped, not scaled")


def test_a_rejected_creative_is_dropped_not_scored_zero():
    """A zero-scored candidate still wins an auction with one entrant."""
    restore = _patched(None, categories={"camp-1": "fitness"},
                       trends={"c-bad": {"recent": {}, "state": "REJECTED"}})
    try:
        result = ranking.rank(_FakeConn(), [_candidate("c-bad")],
                              subject_ref="s-1",
                              request_ctx={"surface": "feed"})
    finally:
        restore()
    _assert(result["ranked"] == [],
            "a rejected creative survived into the ranked set")
    _assert(len(result["dropped"]) == 1, result["dropped"])
    _assert(result["dropped"][0]["reason"] == "CREATIVE_REJECTED")


def test_the_strategy_returns_no_fill_rather_than_serving_a_rejected_creative():
    """The fallback must not rescue what the ranker deliberately dropped."""
    restore = _patched(None, trends={"c-bad": {"recent": {}, "state": "REJECTED"}})
    try:
        ranker = ranking.ExplainableRanker(_FakeConn(),
                                           request_ctx={"surface": "feed"})
        winner = ranker.select([_candidate("c-bad")], subject_ref="s-1",
                               placement="feed")
    finally:
        restore()
    _assert(winner is None,
            "the rejected creative was served after all — showing nothing is "
            "better than showing something people are reporting")


# --------------------------------------------------------------------------- #
# Ranking behaviour
# --------------------------------------------------------------------------- #

def test_only_eligible_candidates_are_ranked():
    """Gates are eligibility's job; the ranker must not resurrect a failure."""
    restore = _patched(None)
    try:
        result = ranking.rank(
            _FakeConn(),
            [_candidate("c-ok"), _candidate("c-no", eligible=False)],
            subject_ref="s-1", request_ctx={"surface": "feed"})
    finally:
        restore()
    ids = [r["creative_id"] for r in result["ranked"]]
    _assert(ids == ["c-ok"], ids)


def test_ranking_is_deterministic():
    restore = _patched(None, categories={"camp-1": "fitness"})
    try:
        runs = [
            [r["creative_id"] for r in ranking.rank(
                _FakeConn(),
                [_candidate(f"c-{i}") for i in range(6)],
                subject_ref="s-1", request_ctx={"surface": "feed"})["ranked"]]
            for _ in range(4)
        ]
    finally:
        restore()
    _assert(all(run == runs[0] for run in runs), runs)


def test_equal_creatives_still_spread_across_viewers():
    """Without the rotation tiebreak, identical creatives sort by id forever."""
    restore = _patched(None)
    try:
        winners = set()
        for viewer in range(30):
            result = ranking.rank(
                _FakeConn(), [_candidate(f"c-{i}") for i in range(4)],
                subject_ref=f"s-{viewer}", request_ctx={"surface": "feed"})
            winners.add(result["ranked"][0]["creative_id"])
    finally:
        restore()
    _assert(len(winners) > 1,
            f"every viewer got the same creative: {winners}")


def test_a_refused_context_ranks_nothing():
    restore = _patched(None)
    try:
        result = ranking.rank(_FakeConn(), [_candidate("c-1")],
                              subject_ref="s-1",
                              request_ctx={"surface": "messages"})
    finally:
        restore()
    _assert(result["ranked"] == [], "an ad was ranked for a private surface")
    _assert(result["dropped"][0]["reason"] == "PRIVATE_SURFACE")


def test_ranking_writes_nothing():
    """Shadow mode depends on this being literally true."""
    source = inspect.getsource(ranking)
    for sql in ("INSERT ", "UPDATE ", "DELETE ", "commit()"):
        _assert(sql not in source, f"ranking.py performs {sql.strip()}")


# --------------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------------- #

def test_a_broken_ranker_falls_back_instead_of_failing_the_request():
    """A ranking layer that can 500 an ad request is worse than none."""
    from services.business_os.ads_intelligence import interest as _interest
    original = _interest.affinities_for

    def _explode(*a, **k):
        raise RuntimeError("interest graph is down")

    _interest.affinities_for = _explode
    try:
        ranker = ranking.ExplainableRanker(_FakeConn(),
                                           request_ctx={"surface": "feed"})
        winner = ranker.select([_candidate("c-1")], subject_ref="s-1",
                               placement="feed")
    finally:
        _interest.affinities_for = original
    # The interest read is itself guarded, so this must still produce a winner.
    _assert(winner is not None, "a failing interest graph blocked delivery")


def test_a_totally_broken_ranker_degrades_to_the_legacy_strategy():
    original = ranking.rank

    def _explode(*a, **k):
        raise RuntimeError("ranker is down")

    ranking.rank = _explode
    try:
        ranker = ranking.ExplainableRanker(_FakeConn(),
                                           request_ctx={"surface": "feed"})
        winner = ranker.select(
            [_candidate("c-1"), _candidate("c-2")],
            subject_ref="s-1", placement="feed")
    finally:
        ranking.rank = original
    _assert(winner is not None, "a broken ranker produced a no-fill")
    _assert(winner.get("creative_id") in {"c-1", "c-2"}, winner)


def test_the_ranker_implements_the_canonical_strategy_interface():
    """It must be injectable into the existing selector, not replace it."""
    from services.business_os.advertising import selection as _selection
    canonical = inspect.signature(_selection.SelectionStrategy.select).parameters
    ours = inspect.signature(ranking.ExplainableRanker.select).parameters
    _assert(set(canonical) == set(ours),
            f"signature drift: {set(canonical)} vs {set(ours)}")


def test_shadow_mode_reports_agreement_without_deciding_anything():
    restore = _patched(None, categories={"camp-1": "fitness"})
    try:
        verdict = ranking.compare(
            _FakeConn(), [_candidate("c-1"), _candidate("c-2")],
            subject_ref="s-1", placement="feed",
            request_ctx={"surface": "feed", "content_category": "fitness"})
    finally:
        restore()
    _assert(verdict["agreed"] in (True, False), verdict)
    _assert(verdict["legacy_creative_id"] in {"c-1", "c-2"}, verdict)
    _assert(verdict["proposed_creative_id"] in {"c-1", "c-2"}, verdict)
    _assert(verdict["explanation"], "shadow mode must explain its proposal")


def test_shadow_mode_survives_a_broken_ranker():
    original = ranking.rank
    ranking.rank = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    try:
        verdict = ranking.compare(_FakeConn(), [_candidate("c-1")],
                                  subject_ref="s-1", placement="feed")
    finally:
        ranking.rank = original
    _assert(verdict["agreed"] is None and verdict["error"] is True, verdict)


# --------------------------------------------------------------------------- #
# Explainability
# --------------------------------------------------------------------------- #

def test_every_score_decomposes_into_named_weighted_parts():
    scored = ranking.score_candidate(
        _candidate("c"), described=_ctx(), affinities={"fitness": 10.0},
        campaign_category="fitness")
    _assert(set(scored["components"]) == set(ranking.WEIGHTS), scored["components"])
    for name, part in scored["components"].items():
        _assert(part.get("reason"), f"{name} has no reason")
        _assert(part.get("weight") == ranking.WEIGHTS[name], name)
        _assert(0.0 <= part["value"] <= 1.0, f"{name}={part['value']}")


def test_the_explanation_names_the_reasons_that_actually_dominated():
    scored = ranking.score_candidate(
        _candidate("c"), described=_ctx(content_category="fitness"),
        affinities={"fitness": 25.0}, campaign_category="fitness",
        summary={"viewable": 10_000, "ctr_on_viewable": 0.02})
    text = ranking.explain_score(scored)
    _assert("fitness" in text, text)
    _assert(str(round(scored["base_score"], 3))[:4] in text, text)


def test_a_reduced_score_says_why_it_was_reduced():
    scored = ranking.score_candidate(
        _candidate("c"), described=_ctx(), affinities={},
        campaign_category="fitness", fatigue_state="WEARING")
    text = ranking.explain_score(scored)
    _assert("WEARING" in text, text)


def test_the_score_is_versioned():
    """A ranking change must be attributable after the fact."""
    scored = ranking.score_candidate(
        _candidate("c"), described=_ctx(), affinities={})
    _assert(scored["ranking_version"] == taxonomy.RANKING_VERSION)
    _assert(scored["ranking_mode"] == ranking.RANKING_MODE)
    _assert(ranking.RANKING_MODE != "legacy",
            "the new mode must be distinguishable from the legacy one")


def _main():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except Exception as exc:  # noqa: BLE001 — standalone runner
            failed += 1
            print(f"  FAIL {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
