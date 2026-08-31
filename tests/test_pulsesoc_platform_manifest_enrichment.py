"""Proof that corpus enrichment describes capabilities without borrowing the holdout.

Enrichment rewrites ``public_summary`` -- the body of every canonical semantic document --
from an identifier restatement into a description of what the capability is for. That is a
change to the corpus the benchmark is measured against, which is exactly the kind of change
that can flatter itself: add the holdout's own wording to the documents and every metric
improves without retrieval improving at all.

These tests remove the possibility rather than arguing against it. The lexical matcher
scores an entry by counting how many query terms occur as substrings of its ``search_text``,
so if no holdout term's match set changes, no entry's score changes, no ranking changes, and
no wording can have been copied -- a copied word would have to match somewhere new.

The consequence is deliberate and worth stating plainly: enrichment cannot improve the
lexical column, and does not. Nine of the ten indirect holdout cases share no term at all
with their targets, so lexical recall on them is structurally zero. Any gain has to appear
in the semantic and hybrid columns, and is measured there.
"""

import importlib.util
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from services import undx_platform_knowledge as knowledge  # noqa: E402


def _generator():
    path = os.path.join(ROOT, "scripts/generate_pulsesoc_platform_manifest.py")
    spec = importlib.util.spec_from_file_location("pulsesoc_manifest_generator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _holdout_terms():
    with open(os.path.join(ROOT, "data/undx/semantic_retrieval_holdout.json"), encoding="utf-8") as handle:
        holdout = json.load(handle)
    queries = [case["query"] for case in holdout["cases"]]
    queries += [case["query"] for case in holdout.get("negative_cases") or []]
    terms = set()
    for query in queries:
        terms.update(re.findall(r"[a-z0-9]{3,}", query.lower()))
    return {term for term in terms if term not in knowledge.STOP_WORDS}


def _match_sets(manifest, terms):
    """Which entries each holdout term matches, under the retriever's own matching rule."""
    sets = {term: set() for term in terms}
    for entry in manifest["entries"]:
        if entry.get("public") is False:
            continue
        haystack = str(entry.get("search_text") or entry.get("name") or "").lower()
        for term in terms:
            if term in haystack:
                sets[term].add(entry["id"])
    return sets


def test_enrichment_changes_no_holdout_term_match_set():
    generator = _generator()
    terms = _holdout_terms()
    plain = _match_sets(generator.build_manifest(enriched=False), terms)
    enriched = _match_sets(generator.build_manifest(enriched=True), terms)
    changed = sorted(term for term in terms if plain[term] != enriched[term])
    assert not changed, f"enrichment altered lexical matching for: {changed[:10]}"


def test_enriched_summaries_stay_inside_the_prompt_budget():
    """A longer summary is a retrieval change wearing a wording change's clothes.

    ``retrieve`` drops any result that would overrun ``MAX_CONTEXT_CHARS``, so summaries
    that grew without limit would silently shorten the result list.
    """
    manifest = _generator().build_manifest(enriched=True)
    longest = max(len(entry["public_summary"]) for entry in manifest["entries"])
    assert longest <= 480
    results = knowledge.retrieve("How do I change notification preferences?")
    assert len(results) == knowledge.MAX_RESULTS
    assert sum(len(r["title"]) + len(r["body"]) for r in results) <= knowledge.MAX_CONTEXT_CHARS


def test_enrichment_replaces_the_boilerplate_it_exists_to_remove():
    """The defect: 100 native surfaces shared one sentence with the identifier swapped.

    An embedder given ``"NotificationPreferences is a navigable PulseSoc app surface"``
    can place the identifier and nothing else, because the document never says what the
    surface is for.
    """
    manifest = _generator().build_manifest(enriched=True)
    surfaces = [e for e in manifest["entries"] if e["kind"] == "native_surface"]
    assert not [e for e in surfaces if "navigable PulseSoc app surface" in e["public_summary"]]
    described = [e for e in surfaces if "deals with" in e["public_summary"]]
    assert len(described) >= len(surfaces) // 2
    entry = next(e for e in surfaces if e["name"] == "NotificationPreferences")
    assert "Notification Preferences" in entry["public_summary"]
    assert "quiet hours" in entry["public_summary"]


def test_source_paths_still_never_reach_a_prompt():
    """Enrichment adds wording; it must not add provenance."""
    manifest = _generator().build_manifest(enriched=True)
    for entry in manifest["entries"]:
        summary = entry["public_summary"]
        assert ".py" not in summary
        assert ".tsx" not in summary
        assert "/src/" not in summary


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
