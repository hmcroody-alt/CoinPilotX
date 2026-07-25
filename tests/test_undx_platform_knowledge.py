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


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
