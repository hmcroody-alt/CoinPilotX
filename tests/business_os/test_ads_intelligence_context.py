"""Contextual signals — refusals that outrank bids, and no path to a profile.

The properties defended here:

* **Sensitive and private contexts are refused, not scored low.** A low score
  still wins when nothing else is eligible, so a refusal has to be a different
  kind of answer and has to be evaluated before any scoring.

* **The module structurally cannot profile anyone.** No subject parameter, no
  database handle, no write path. Contextual targeting does not go wrong by
  being used; it goes wrong when somebody starts accumulating it.

* **Unknown context is neutral, not zero.** Scoring uncategorised content zero
  would hand every such impression to the highest bidder, which is the opposite
  of the point.

    python tests/business_os/test_ads_intelligence_context.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import inspect  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from services.business_os.ads_intelligence import context, taxonomy  # noqa: E402


def _assert(cond, detail=""):
    if not cond:
        raise AssertionError(detail)


def _ctx(**kwargs):
    base = {"surface": "feed", "content_category": "fitness"}
    base.update(kwargs)
    return context.describe(base)


# --------------------------------------------------------------------------- #
# The module cannot become a profile store
# --------------------------------------------------------------------------- #

def test_no_function_here_accepts_a_subject():
    """The structural guarantee: it cannot profile what it cannot see."""
    banned = ("subject", "subject_ref", "user_id", "viewer", "viewer_user_id",
              "device_id", "session_ref")
    for name, fn in vars(context).items():
        if name.startswith("_") or not callable(fn):
            continue
        if getattr(fn, "__module__", None) != context.__name__:
            continue
        params = inspect.signature(fn).parameters
        for bad in banned:
            _assert(bad not in params,
                    f"{name}() takes {bad!r} — contextual signals must not be "
                    f"attachable to a person")


def test_the_module_has_no_write_path():
    """No conn, no persistence: accumulation has to be impossible, not discouraged."""
    for name, fn in vars(context).items():
        if name.startswith("_") or not callable(fn):
            continue
        if getattr(fn, "__module__", None) != context.__name__:
            continue
        params = inspect.signature(fn).parameters
        _assert("conn" not in params,
                f"{name}() takes a connection; context must stay request-scoped")
    source = inspect.getsource(context)
    for sql in ("INSERT", "UPDATE ", "DELETE"):
        _assert(sql not in source, f"context.py contains {sql}")


def test_describe_does_not_pass_identifying_fields_through():
    """Whatever the caller hands us, only the closed vocabulary comes out."""
    described = context.describe({
        "surface": "feed", "content_category": "fitness",
        "user_id": "u-1", "subject_ref": "s-1", "email": "a@b.c",
        "ip": "10.0.0.1", "session_ref": "sess-1",
    })
    for leak in ("user_id", "subject_ref", "email", "ip", "session_ref"):
        _assert(leak not in described, f"{leak} survived normalisation")


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #

def test_a_sensitive_adjacency_is_refused_outright():
    for category in ("death", "suicide", "diagnosis", "financial_hardship",
                     "religion", "child_safety"):
        described = _ctx(content_category=category)
        verdict = context.ad_permitted(described)
        _assert(verdict["permitted"] is False,
                f"an ad was permitted next to {category!r}")
        _assert(verdict["reason"] == "SENSITIVE_CONTEXT", verdict)


def test_a_refusal_is_not_merely_a_low_score():
    """The distinction that survives an empty auction."""
    described = _ctx(content_category="death")
    result = context.match_score("fitness", described)
    _assert(result["match"] == "REFUSED",
            f"a sensitive slot produced a bid-comparable score: {result}")
    _assert(result["score"] == context.MATCH_NONE)
    _assert(context.ad_permitted(described)["permitted"] is False,
            "the refusal must be reachable without computing a score at all")


def test_a_perfect_category_match_cannot_rescue_a_sensitive_slot():
    """The dangerous case: the ad is exactly 'relevant' to a medical post."""
    described = _ctx(content_category="medical")
    _assert(context.match_score("medical", described)["match"] == "REFUSED")


def test_private_surfaces_never_carry_ads():
    for surface in ("messages", "dm", "audio_call", "live_audio", "chat",
                    "call_transcript", "private_message"):
        described = _ctx(surface=surface, content_category=None)
        verdict = context.ad_permitted(described)
        _assert(verdict["permitted"] is False, f"{surface} permitted an ad")
        _assert(verdict["reason"] == "PRIVATE_SURFACE", verdict)


def test_the_private_surface_list_cannot_be_narrowed_by_editing_the_taxonomy():
    """Both lists have to be widened together; neither can shrink the other."""
    _assert(taxonomy.FORBIDDEN_SIGNAL_SOURCES <= context.PRIVATE_SURFACES,
            "a forbidden signal source is not a private surface")


def test_an_unknown_surface_is_refused_rather_than_defaulted():
    """A new surface must be added deliberately, not inherit ad load."""
    described = _ctx(surface="brand_new_surface")
    verdict = context.ad_permitted(described)
    _assert(verdict["permitted"] is False, verdict)
    _assert(verdict["reason"] == "SURFACE_NOT_SUPPORTED", verdict)


def test_a_missing_surface_is_refused():
    verdict = context.ad_permitted(context.describe({}))
    _assert(verdict["permitted"] is False, verdict)
    _assert(verdict["reason"] == "CONTEXT_UNAVAILABLE", verdict)


def test_every_refusal_code_is_in_the_closed_set():
    cases = [{}, {"surface": "messages"}, {"surface": "nope"},
             {"surface": "feed", "content_category": "grief"}]
    for case in cases:
        verdict = context.ad_permitted(context.describe(case))
        if not verdict["permitted"]:
            _assert(verdict["reason"] in context.REFUSAL_REASONS,
                    f"{verdict['reason']} is not a declared refusal code")


def test_a_sensitive_context_is_never_recorded_as_a_category():
    """The refusal and the log have to agree, or the log is the leak."""
    described = _ctx(content_category="mental_health")
    _assert(described["content_category"] is None,
            "a sensitive adjacency was written into the context record")
    _assert(described["sensitive"] is True)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

def test_an_exact_category_match_scores_highest():
    result = context.match_score("fitness", _ctx(content_category="fitness"))
    _assert(result["score"] == context.MATCH_EXACT, result)
    _assert(result["match"] == "EXACT")


def test_related_categories_score_between_exact_and_none():
    result = context.match_score("fitness", _ctx(content_category="sports"))
    _assert(result["match"] == "RELATED", result)
    _assert(context.MATCH_NONE < result["score"] < context.MATCH_EXACT)


def test_an_unrelated_category_scores_none_but_is_still_permitted():
    """Not a fit is not a refusal — the slot exists, this ad just does not suit it."""
    described = _ctx(content_category="fitness")
    _assert(context.match_score("automotive", described)["match"] == "NONE")
    _assert(context.ad_permitted(described)["permitted"] is True)


def test_unknown_context_is_neutral_rather_than_zero():
    """Zero here would hand uncategorised inventory to the highest bidder."""
    result = context.match_score("fitness", _ctx(content_category=None))
    _assert(result["score"] == context.MATCH_NEUTRAL, result)
    _assert(result["score"] > context.MATCH_NONE)


def test_an_uncategorised_campaign_is_also_neutral():
    result = context.match_score(None, _ctx(content_category="fitness"))
    _assert(result["score"] == context.MATCH_NEUTRAL, result)


def test_a_category_outside_the_taxonomy_resolves_to_none_not_a_guess():
    for raw in ("crypto_moon_shots", "FITNESSS", "", None, 42, "fit"):
        _assert(context.normalise_content_category(raw) is None, repr(raw))
    _assert(context.normalise_content_category("Fitness") == "fitness",
            "case normalisation should still work")


def test_relatedness_is_symmetric():
    """An asymmetric map means the score depends on which side you ask from.

    Reports every broken edge rather than the first, because these come in
    clusters and fixing them one failure at a time is how half a map ends up
    symmetric.
    """
    broken = [f"{left}->{right}"
              for left, rights in context._RELATED_CATEGORIES.items()
              for right in rights
              if left not in context._RELATED_CATEGORIES.get(right, set())]
    _assert(not broken, f"related in one direction only: {', '.join(broken)}")


def test_related_categories_are_all_in_the_closed_taxonomy():
    for left, rights in context._RELATED_CATEGORIES.items():
        _assert(left in taxonomy.INTEREST_CATEGORY_SET, left)
        for right in rights:
            _assert(right in taxonomy.INTEREST_CATEGORY_SET, right)


def test_no_category_is_related_to_itself():
    for left, rights in context._RELATED_CATEGORIES.items():
        _assert(left not in rights, f"{left} lists itself as related")


def test_every_score_carries_a_reason():
    cases = [("fitness", "fitness"), ("fitness", "sports"),
             ("fitness", "automotive"), ("fitness", None), (None, "fitness")]
    for campaign, content in cases:
        result = context.match_score(campaign, _ctx(content_category=content))
        _assert(result.get("reason"), f"no reason for {campaign}/{content}")


# --------------------------------------------------------------------------- #
# Time and explanations
# --------------------------------------------------------------------------- #

def test_time_buckets_are_coarse_and_closed():
    for hour in range(24):
        bucket = context.derive_time_bucket(
            datetime(2026, 8, 10, hour, 30, tzinfo=timezone.utc))
        _assert(bucket in context.TIME_BUCKETS, f"hour {hour} -> {bucket}")


def test_the_context_record_carries_no_precise_timestamp():
    """A second-accurate time is a routine signal for anyone holding two of them."""
    described = _ctx()
    for key, value in described.items():
        _assert(not isinstance(value, datetime),
                f"{key} carries a raw timestamp")
    _assert(described["time_bucket"] in context.TIME_BUCKETS)


def test_the_explanation_is_true_for_a_viewer_with_no_profile():
    """The whole point of the contextual half of 'why am I seeing this'."""
    explained = context.explain(_ctx(content_category="fitness"), "fitness")
    _assert(explained["shown"] is True)
    _assert("fitness" in explained["reason"])
    _assert(explained["used_content_category"] == "fitness")


def test_the_explanation_admits_when_context_was_not_the_reason():
    """Claiming a contextual reason for a non-contextual match would be a lie."""
    explained = context.explain(_ctx(content_category="fitness"), "automotive")
    _assert(explained["shown"] is True)
    _assert("not chosen because of the content" in explained["reason"], explained)


def test_a_refused_slot_explains_itself_without_naming_the_sensitive_content():
    explained = context.explain(_ctx(content_category="suicide"), "fitness")
    _assert(explained["shown"] is False)
    _assert("suicide" not in explained["reason"].lower(),
            "the refusal reason repeated the sensitive category back")


def test_describe_survives_junk_input():
    for junk in (None, {}, {"surface": 42}, {"content_category": ["a"]},
                 {"surface": None, "placement": None}):
        described = context.describe(junk)
        _assert(isinstance(described, dict), repr(junk))
        _assert(described["time_bucket"] in context.TIME_BUCKETS)


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
