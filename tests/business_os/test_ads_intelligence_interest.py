"""Interest graph — decay, suppression, and the audience prohibition.

The tests here defend three things that are easy to lose in a refactor:

* **The graph ranks, it does not target.** ``interests`` is a prohibited
  targeting field in the canonical advertising layer. An interest graph is
  precisely the component that could quietly undo that, so the module offers no
  category-to-subjects lookup and there is a test asserting it stays that way.

* **"Not interested" actually works.** An explicit negative has to be able to
  push a category down, or the button is decoration. The matching safety rule —
  that it can only ever push *down* — is tested alongside it, because the
  privacy argument for acting on complaints at all depends on that asymmetry.

* **Fraud cannot buy relevance.** Invalid traffic is excluded from the rebuild.
  If it were not, purchasing fake engagement would purchase audience affinity.

    python tests/business_os/test_ads_intelligence_interest.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ads_intel_int_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)
os.environ.setdefault("ADS_INTEL_SUBJECT_SALT", "test-salt-interest")

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import json  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from services import db  # noqa: E402
from services.business_os.ads_intelligence import (  # noqa: E402
    interest, privacy, taxonomy)
from services.business_os.ads_intelligence.schema import ensure_schema  # noqa: E402

_FLAG = "BUSINESS_OS_ADS_INTELLIGENCE_MEASUREMENT"
_SUBJECT = "subject-interest-1"


def _assert(cond, detail=""):
    if not cond:
        raise AssertionError(detail)


def _now():
    return datetime.now(timezone.utc)


def _iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def setup_module(module=None):
    ensure_schema()
    os.environ[_FLAG] = "on"
    conn = db.connect()
    try:
        # A campaign that declares what it is about. Category resolution reads
        # the canonical campaign row, never the event.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_ad_campaigns ("
            "campaign_id TEXT PRIMARY KEY, advertiser_user_id TEXT, name TEXT, "
            "objective TEXT, status TEXT, destination_url TEXT, created_by TEXT, "
            "metadata_json TEXT, created_at TEXT, updated_at TEXT)")
        for cid, category in (("camp-fit", "fitness"), ("camp-tech", "technology"),
                              ("camp-none", None)):
            meta = json.dumps({"ads_category": category}) if category else None
            conn.execute(
                "INSERT OR REPLACE INTO business_os_ad_campaigns "
                "(campaign_id, advertiser_user_id, name, objective, status, "
                "metadata_json, created_at, updated_at) "
                "VALUES (?, '1', 'n', 'traffic', 'active', ?, '2026-01-01', '2026-01-01')",
                (cid, meta))
        conn.commit()
    finally:
        conn.close()


def teardown_module(module=None):
    os.environ.pop(_FLAG, None)


def _seed(subject, events):
    """events: (event_name, campaign_id, age_days, validity)"""
    conn = db.connect()
    try:
        conn.execute("DELETE FROM ads_intel_events WHERE subject_ref = ?", (subject,))
        for i, (name, campaign_id, age_days, validity) in enumerate(events):
            when = _iso(_now() - timedelta(days=age_days))
            conn.execute(
                "INSERT INTO ads_intel_events "
                "(event_id, dedup_key, event_name, event_family, occurred_at, "
                "received_at, subject_ref, campaign_id, validity, billable, "
                "quality_status, ingest_source, created_at) "
                "VALUES (?, ?, ?, 'engagement', ?, ?, ?, ?, ?, 0, 'ok', 'client', ?)",
                (f"ev-{subject}-{i}", f"dk-{subject}-{i}", name, when, when,
                 subject, campaign_id, validity, when))
        conn.commit()
    finally:
        conn.close()


def _rebuild(subject):
    conn = db.connect()
    try:
        return interest.rebuild_subject(conn, subject)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# The audience prohibition
# --------------------------------------------------------------------------- #

def test_no_function_maps_a_category_to_subjects():
    """The graph must not become the interest targeting the platform refuses.

    ``interests`` is in AUDIENCE_PROHIBITED_FIELDS. Ranking asks "what does this
    person like"; targeting asks "who likes this". Only the second one lets an
    advertiser address a group, so only the first direction exists here.
    """
    banned = ("subjects_in_category", "subjects_for_category", "audience_for",
              "members_of", "list_subjects", "enumerate_subjects",
              "subjects_with_affinity")
    for name in banned:
        _assert(not hasattr(interest, name),
                f"{name} would reintroduce prohibited interest targeting")


def test_category_reach_returns_a_count_and_never_identifiers():
    _seed(_SUBJECT, [("ad_click", "camp-fit", 1, "valid")])
    _rebuild(_SUBJECT)
    conn = db.connect()
    try:
        reach = interest.category_reach(conn, "fitness", window_days=30)
    finally:
        conn.close()
    _assert(isinstance(reach, int), f"reach must be a count, got {type(reach)}")
    _assert(reach >= 1, reach)


def test_a_thin_category_is_not_a_safe_audience():
    conn = db.connect()
    try:
        ok = interest.meets_minimum_audience(conn, "fitness", window_days=30)
    finally:
        conn.close()
    _assert(ok is False,
            "one person must not clear the minimum audience size")


# --------------------------------------------------------------------------- #
# Category resolution comes from the server
# --------------------------------------------------------------------------- #

def test_category_is_read_from_the_campaign_not_the_event():
    conn = db.connect()
    try:
        _assert(interest.campaign_category(conn, "camp-fit") == "fitness")
        _assert(interest.campaign_category(conn, "camp-none") is None)
        _assert(interest.campaign_category(conn, "no-such-campaign") is None)
    finally:
        conn.close()


def test_an_unknown_category_is_none_rather_than_a_default():
    for raw in ("crypto", "", None, "FITNESS ", "religion"):
        got = interest.normalise_category(raw)
        _assert(got in (None, "fitness"), (raw, got))
    _assert(interest.normalise_category("FITNESS ") == "fitness")
    _assert(interest.normalise_category("religion") is None,
            "a sensitive category must not be mintable")


# --------------------------------------------------------------------------- #
# Decay
# --------------------------------------------------------------------------- #

def test_a_signal_halves_after_one_half_life():
    _assert(abs(interest.decay_factor(0, 14) - 1.0) < 1e-9)
    _assert(abs(interest.decay_factor(14, 14) - 0.5) < 1e-9)
    _assert(abs(interest.decay_factor(28, 14) - 0.25) < 1e-9)


def test_the_same_event_scores_lower_in_a_longer_window():
    """Short windows catch intent, long windows catch taste.

    A day-old click is nearly undecayed against a 3-day half-life and noticeably
    decayed against a 45-day one only in relative terms — what matters is that
    the windows are genuinely different projections, not copies.
    """
    _seed(_SUBJECT, [("ad_add_to_cart", "camp-fit", 10, "valid")])
    built = _rebuild(_SUBJECT)
    seven = built[7].get("fitness", 0.0)
    ninety = built[90].get("fitness", 0.0)
    _assert(ninety > seven,
            f"a 10-day-old signal should survive better in 90d: {seven} vs {ninety}")


def test_an_event_older_than_the_window_does_not_contribute():
    _seed(_SUBJECT, [("ad_click", "camp-fit", 40, "valid")])
    built = _rebuild(_SUBJECT)
    _assert(built[7].get("fitness", 0.0) == 0.0,
            "a 40-day-old event contributed to the 7-day window")
    _assert(built[90].get("fitness", 0.0) > 0.0, built[90])


# --------------------------------------------------------------------------- #
# Negative signals
# --------------------------------------------------------------------------- #

def test_not_interested_actually_suppresses_the_category():
    """The button has to do something, or it is decoration."""
    _seed(_SUBJECT, [
        ("ad_click", "camp-fit", 1, "valid"),
        ("ad_click", "camp-fit", 1, "valid"),
    ])
    before = _rebuild(_SUBJECT)[30].get("fitness", 0.0)

    _seed(_SUBJECT, [
        ("ad_click", "camp-fit", 1, "valid"),
        ("ad_click", "camp-fit", 1, "valid"),
        ("ad_not_interested", "camp-fit", 1, "valid"),
    ])
    after = _rebuild(_SUBJECT)[30].get("fitness", 0.0)
    _assert(after < before,
            f"'not interested' did not suppress the category: {before} -> {after}")


def test_an_explicit_negative_is_allowed_to_shape_delivery():
    for name in taxonomy.EXPLICIT_NEGATIVE_EVENTS:
        cls = privacy.classify_event(name)
        _assert(privacy.allows(cls, "targeting"),
                f"{name} is classified {cls}, so the control would do nothing")


def test_an_inferred_negative_is_measurement_only():
    """A fast scroll is ambiguous, so it counts in reports and nowhere else."""
    for name in taxonomy.INFERRED_NEGATIVE_EVENTS:
        cls = privacy.classify_event(name)
        _assert(cls == "measurement_only", f"{name} -> {cls}")
        _assert(not privacy.allows(cls, "targeting"),
                f"inferred dislike ({name}) must not shape delivery")
        _assert(privacy.allows(cls, "analytics"),
                f"{name} must still be countable")


def test_an_explicit_negative_can_never_raise_an_affinity():
    """The asymmetry the privacy argument rests on.

    Even if a weight were mis-signed in the taxonomy, a complaint must not be
    able to become a positive profile attribute.
    """
    horizon = _now()
    rows = [(name, _iso(horizon - timedelta(hours=1)), "fitness")
            for name in taxonomy.EXPLICIT_NEGATIVE_EVENTS]
    scored = interest.score_events(rows, window_days=30, now=horizon)
    _assert(scored["fitness"]["score"] <= 0.0, scored)


def test_only_negative_signals_still_produce_a_negative_score():
    _seed(_SUBJECT, [("ad_hide", "camp-tech", 1, "valid")])
    built = _rebuild(_SUBJECT)
    _assert(built[30].get("technology", 0.0) < 0.0, built[30])


# --------------------------------------------------------------------------- #
# Fraud and privacy filters
# --------------------------------------------------------------------------- #

def test_invalid_traffic_cannot_train_the_graph():
    """Otherwise buying fake engagement buys audience relevance."""
    _seed(_SUBJECT, [
        ("ad_add_to_cart", "camp-tech", 1, "invalid"),
        ("ad_add_to_cart", "camp-tech", 1, "suspect"),
    ])
    built = _rebuild(_SUBJECT)
    _assert(built[30].get("technology", 0.0) == 0.0,
            f"invalid traffic shaped the interest graph: {built[30]}")


def test_a_stored_privacy_class_beats_a_later_reclassification():
    """A signal keeps the promise it was collected under.

    The class is derivable from the event name, so a reader could recompute it.
    It is stored precisely so that widening a classification later cannot
    retroactively grant permission to shape delivery for signals gathered while
    the narrower rule was in force. Here a click carries a stored
    ``measurement_only`` and must be ignored, even though ``ad_click`` would
    derive as ``product_signal`` today.
    """
    horizon = _now()
    when = _iso(horizon - timedelta(hours=1))
    scored = interest.score_events(
        [("ad_click", when, "fitness", "measurement_only")],
        window_days=30, now=horizon)
    _assert(scored == {},
            f"a narrowly-collected signal was used for delivery: {scored}")

    # ...and the same row without a stored class falls back to derivation.
    scored_derived = interest.score_events(
        [("ad_click", when, "fitness", None)], window_days=30, now=horizon)
    _assert(scored_derived.get("fitness", {}).get("score", 0) > 0, scored_derived)


def test_ingest_pins_the_privacy_class_on_the_row():
    from services.business_os.ads_intelligence import events as _events
    result = _events.record_event({
        "event_name": "ad_click", "dedup_key": "interest-privacy-pin",
        "occurred_at": _iso(_now()), "user_id": 424242,
        "decision_id": "dec-interest", "campaign_id": "camp-fit",
        "creative_id": "cre-interest",
    }, ingest_source="server")
    # `ok` only means the batch was well-formed; `accepted` is what proves the
    # row exists. Asserting the weaker one is how a rejected event slips past.
    _assert(result.get("accepted"), result)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT privacy_class FROM ads_intel_events WHERE dedup_key = ?",
            ("interest-privacy-pin",)).fetchone()
    finally:
        conn.close()
    _assert(row and row[0] == "product_signal",
            f"privacy class was not pinned at write time: {row}")


def test_an_event_with_no_resolvable_category_contributes_nothing():
    _seed(_SUBJECT, [("ad_add_to_cart", "camp-none", 1, "valid")])
    built = _rebuild(_SUBJECT)
    _assert(built[30] == {}, built[30])


def test_scores_are_clamped_to_the_taxonomy_bounds():
    horizon = _now()
    rows = [("ad_repeat_purchase", _iso(horizon), "fitness")] * 200
    scored = interest.score_events(rows, window_days=30, now=horizon)
    _assert(scored["fitness"]["score"] <= taxonomy.AFFINITY_MAX, scored)


# --------------------------------------------------------------------------- #
# Rebuild semantics
# --------------------------------------------------------------------------- #

def test_a_rebuild_removes_an_affinity_that_no_longer_scores():
    """Decay only takes effect if stale rows actually go away.

    A leftover high score is indistinguishable from a current one to every
    reader, so leaving it behind would mean the graph never forgets.
    """
    _seed(_SUBJECT, [("ad_add_to_cart", "camp-tech", 1, "valid")])
    _rebuild(_SUBJECT)
    conn = db.connect()
    try:
        _assert(interest.affinities_for(conn, _SUBJECT, window_days=30)
                .get("technology", 0) > 0)
    finally:
        conn.close()

    _seed(_SUBJECT, [])  # every event gone
    _rebuild(_SUBJECT)
    conn = db.connect()
    try:
        _assert(interest.affinities_for(conn, _SUBJECT, window_days=30) == {},
                "a stale affinity survived a rebuild")
    finally:
        conn.close()


def test_rebuild_is_idempotent():
    _seed(_SUBJECT, [("ad_click", "camp-fit", 1, "valid")])
    first = _rebuild(_SUBJECT)
    second = _rebuild(_SUBJECT)
    _assert(first[30].keys() == second[30].keys(), (first, second))
    for cat in first[30]:
        _assert(abs(first[30][cat] - second[30][cat]) < 0.5, (first, second))


def test_top_categories_never_returns_a_suppressed_one():
    _seed(_SUBJECT, [
        ("ad_add_to_cart", "camp-fit", 1, "valid"),
        ("ad_report", "camp-tech", 1, "valid"),
    ])
    _rebuild(_SUBJECT)
    conn = db.connect()
    try:
        top = interest.top_categories(conn, _SUBJECT, window_days=30)
    finally:
        conn.close()
    names = [row["category"] for row in top]
    _assert("fitness" in names, top)
    _assert("technology" not in names,
            "a reported category was offered as a top interest")


def test_explain_affinity_shows_how_thin_the_evidence_is():
    _seed(_SUBJECT, [("ad_click", "camp-fit", 1, "valid")])
    _rebuild(_SUBJECT)
    conn = db.connect()
    try:
        explained = interest.explain_affinity(conn, _SUBJECT, "fitness")
    finally:
        conn.close()
    _assert(explained["known"] is True, explained)
    _assert(explained["signal_count"] == 1, explained)
    _assert(explained["last_signal_at"], explained)


def test_forgetting_a_subject_erases_the_projection_too():
    """Deletion has to reach derived rows, not only the event log."""
    _seed(_SUBJECT, [("ad_click", "camp-fit", 1, "valid")])
    _rebuild(_SUBJECT)
    conn = db.connect()
    try:
        removed = interest.forget_subject(conn, _SUBJECT)
        _assert(removed > 0, removed)
        _assert(interest.affinities_for(conn, _SUBJECT, window_days=30) == {})
        _assert(interest.affinities_for(conn, _SUBJECT, window_days=7) == {})
    finally:
        conn.close()


def test_reads_degrade_rather_than_raise_when_unavailable():
    """Relevance is an enhancement; it must never fail an ad request."""
    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("table gone")
    broken = _Broken()
    _assert(interest.affinities_for(broken, "s") == {})
    _assert(interest.top_categories(broken, "s") == [])
    _assert(interest.category_reach(broken, "fitness") == 0)
    _assert(interest.explain_affinity(broken, "s", "fitness")["known"] is False)


def _main():
    setup_module()
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
    teardown_module()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
