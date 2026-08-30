"""Contract tests for bounded, source-derived UNDX platform knowledge."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import undx_platform_knowledge as knowledge


def test_manifest_has_cross_layer_coverage():
    manifest = knowledge.load_manifest()
    counts = manifest["counts"]
    assert counts["native_surface"] >= 40
    assert counts["native_api"] >= 30
    assert counts["server_route"] >= 100
    assert counts["data_entity"] >= 100


def test_retrieval_is_bounded_and_query_specific():
    results = knowledge.retrieve("How do I change notification preferences?")
    assert 1 <= len(results) <= knowledge.MAX_RESULTS
    assert sum(len(item["title"]) + len(item["body"]) for item in results) <= knowledge.MAX_CONTEXT_CHARS
    assert any("notification" in f"{item['title']} {item['body']}".lower() for item in results)


def test_prompt_results_never_expose_source_paths_or_raw_schema():
    results = knowledge.retrieve("marketplace listings orders database")
    rendered = str(results)
    assert "'source':" not in rendered
    assert ".py" not in rendered
    assert "CREATE TABLE" not in rendered


def test_empty_or_unrelated_queries_do_not_dump_manifest():
    assert knowledge.retrieve("what is this") == []
    assert knowledge.retrieve("xylophone nebula quasar") == []


def test_off_topic_query_does_not_match_through_a_function_word_substring():
    """The ng-03 negative control, pinned.

    Matching is unanchored substring matching, so any function word that survives
    ``STOP_WORDS`` matches inside longer words. ``for`` was the whole reason this query
    returned fee and performance records: it hit ``plat(for)m`` and ``per(for)mance``,
    12 corpus entries as a substring against 2 as a whole word. Nothing in this query
    is about PulseSoc, so anything returned is a false positive.
    """
    assert knowledge.retrieve("recipe for beef bourguignon") == []


def test_multilingual_function_words_carry_no_retrieval_signal_on_their_own():
    """The same defect in the other holdout languages.

    Each query is function words only. A non-empty result means some stop word matched
    as a substring somewhere, which is the ng-03 failure wearing a different language.
    """
    for query in (
        "comment est une pour",          # fr
        "como por que para una",         # es
        "kijan pou yon nan mwen",        # ht
    ):
        assert knowledge.retrieve(query) == [], query


def test_domain_terms_that_look_like_function_words_are_still_searchable():
    """Guards the other side of the trade.

    ``post``, ``get``, ``set``, ``all`` and ``out`` read like noise words but are domain
    vocabulary here -- HTTP methods, accessor prefixes, and the core content noun. Adding
    them to ``STOP_WORDS`` would have been free to measure and expensive in production,
    because the holdout does not happen to probe them.
    """
    for term in ("post", "get", "set", "out", "all"):
        assert term not in knowledge.STOP_WORDS
    assert knowledge.retrieve("post") != []


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
