#!/usr/bin/env python3
"""Measure candidate lexical matchers against the frozen holdout before changing any.

The lexical retriever leaks two of four negative controls, and the two leaks have
different causes that a single knob cannot fix:

* ``recipe for beef bourguignon`` -> ``platform_fee_rules`` is *unanchored substring
  matching*. The only term that matched was ``for``, as a substring of "plat**for**m"
  and "per**for**mance". Across the corpus ``for`` hits 12 entries as a substring and
  2 as a whole word. There is no signal here at all.

* ``who won the 1998 world cup`` -> ``arena_world_events`` is a genuine whole-word hit
  on ``world``: one content term out of five. Tokenisation does not touch it. What is
  missing is any requirement that a match explain a meaningful share of the query.

So this sweeps the two fixes independently and together, and reports the cost of each
against the frozen control's positive recall. A negative-control fix that quietly costs
five points of recall is not an improvement, and the only way to know is to measure both
sides of the trade at once.

Writes nothing. Prints a table. The frozen control file is never touched.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault("UNDX_SEMANTIC_RETRIEVAL_STAGE", "off")

from services import undx_platform_knowledge as lex  # noqa: E402
import undx_semantic_retrieval_benchmark as bench  # noqa: E402


#: Function words that carry no retrieval signal. The existing list is English-only and
#: missing ``for``, which is the single token responsible for the beef-bourguignon leak.
#: The holdout is multilingual, so the additions cover es/fr/ht function words too --
#: omitting them would silently penalise exactly the languages semantic retrieval is
#: supposed to help.
EXTRA_STOP_WORDS = {
    # English
    "and", "are", "can", "for", "how", "its", "not", "was", "were", "who", "why",
    "you", "any", "all", "get", "got", "did", "has", "had", "why", "our", "out",
    # Spanish
    "que", "por", "para", "con", "los", "las", "una", "del", "como", "mis", "mi",
    # French
    "les", "des", "une", "pour", "dans", "mon", "ma", "mes", "est", "sur", "avec",
    "comment", "quel", "quelle",
    # Haitian Creole
    "mwen", "nan", "yon", "pou", "kijan", "sa", "ki", "ak", "yo", "an", "la",
}


def make_terms(stop_words: set[str]) -> Callable[[str], list[str]]:
    def _terms(query: str) -> list[str]:
        return [
            term for term in re.findall(r"[a-z0-9]{3,}", str(query or "").lower())
            if term not in stop_words
        ][:24]
    return _terms


def tokens_of(haystack: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", haystack))


def matches_substring(term: str, haystack: str, toks: set[str]) -> bool:
    return term in haystack


def matches_word(term: str, haystack: str, toks: set[str]) -> bool:
    """Whole-word match with a tight morphological tolerance.

    Pure equality would lose ``reel`` -> ``reels``, which is a real and common query
    shape. Arbitrary prefixes would let ``for`` back in through the front door, so the
    tolerance only applies from four characters up: at that length a shared prefix is
    evidence, below it is coincidence.
    """
    if term in toks:
        return True
    if len(term) >= 4:
        for token in toks:
            if token.startswith(term) or (len(token) >= 4 and term.startswith(token)):
                return True
    return False


def build_retrieve(terms_fn, match_fn, coverage: float):
    """A stand-in for ``lex.retrieve`` that returns names, so scoring stays comparable."""
    manifest = lex.load_manifest()
    entries = [e for e in (manifest.get("entries") or [])
               if isinstance(e, dict) and e.get("public") is not False]
    prepared = [(e, str(e.get("search_text") or e.get("name") or "").lower()) for e in entries]
    prepared = [(e, h, tokens_of(h)) for e, h in prepared]

    def retrieve(query: str) -> list[str]:
        terms = terms_fn(query)
        if not terms:
            return []
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for item, haystack, toks in prepared:
            name = str(item.get("name") or "").lower()
            exact = sum(4 for term in terms if term == name)
            hit_terms = {term for term in terms if match_fn(term, haystack, toks)}
            if not hit_terms and not exact:
                continue
            # Coverage is over DISTINCT query terms explained, not raw hit count. A
            # document that matches one term five times has still explained one term.
            if not exact and (len(hit_terms) / len(set(terms))) < coverage:
                continue
            scored.append((exact + len(hit_terms), str(item.get("id") or ""), item))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [str(i.get("name") or "") for _, _, i in scored[: lex.MAX_RESULTS]]

    return retrieve


def evaluate(retrieve, positives, negatives) -> dict[str, Any]:
    hits1 = hits3 = hits5 = 0
    rr = 0.0
    by_lang: dict[str, list[int]] = {}
    by_cat: dict[str, list[int]] = {}
    for case in positives:
        names = retrieve(str(case.get("query") or ""))
        targets = {str(t).lower() for t in (case.get("targets") or [])}
        rank = None
        for i, name in enumerate(names[:5], start=1):
            if name.lower() in targets:
                rank = i
                break
        hit5 = 1 if rank else 0
        hits1 += 1 if rank == 1 else 0
        hits3 += 1 if rank and rank <= 3 else 0
        hits5 += hit5
        rr += (1.0 / rank) if rank else 0.0
        by_lang.setdefault(str(case.get("language") or "?"), []).append(hit5)
        by_cat.setdefault(str(case.get("category") or "?"), []).append(hit5)
    n = len(positives) or 1
    leaks = [c.get("id") for c in negatives if retrieve(str(c.get("query") or ""))]
    return {
        "recall_at_1": hits1 / n,
        "recall_at_3": hits3 / n,
        "recall_at_5": hits5 / n,
        "mrr": rr / n,
        "leaks": leaks,
        "by_language": {k: sum(v) / len(v) for k, v in sorted(by_lang.items())},
        "by_category": {k: sum(v) / len(v) for k, v in sorted(by_cat.items())},
    }


def main() -> int:
    holdout = bench.load_holdout(bench.HOLDOUT_PATH)
    positives = [c for c in holdout.get("cases") or [] if c.get("targets")]
    negatives = holdout.get("negative_cases") or []
    frozen = json.loads((ROOT / "data/undx/baseline_lexical_results.json").read_text())["headline"]

    base_stop = set(lex.STOP_WORDS)
    wide_stop = base_stop | EXTRA_STOP_WORDS

    variants: list[tuple[str, Any]] = [
        ("A control (substring, base stops, cov 0)",
         build_retrieve(make_terms(base_stop), matches_substring, 0.0)),
        ("B +stopwords only",
         build_retrieve(make_terms(wide_stop), matches_substring, 0.0)),
        ("C +word-boundary only",
         build_retrieve(make_terms(base_stop), matches_word, 0.0)),
        ("D word-boundary +stopwords",
         build_retrieve(make_terms(wide_stop), matches_word, 0.0)),
    ]
    for cov in (0.20, 0.25, 0.30, 0.34, 0.40, 0.50):
        variants.append((f"E word+stops, coverage>={cov:.2f}",
                         build_retrieve(make_terms(wide_stop), matches_word, cov)))
    # The decisive comparison: coverage WITHOUT the word-boundary change. Variant E moves
    # two levers at once, and D shows the boundary lever alone costs 7 points of real
    # recall -- so if coverage on its own closes ng-04, the boundary change is a cost with
    # no remaining benefit to justify it.
    for cov in (0.20, 0.25, 0.30, 0.34, 0.40, 0.50):
        variants.append((f"F substring+stops, coverage>={cov:.2f}",
                         build_retrieve(make_terms(wide_stop), matches_substring, cov)))

    print(f"FROZEN CONTROL  R@5={frozen['recall_at_5']:.4f} MRR={frozen['mrr']:.4f} leaks=2/4\n")
    header = f"{'variant':<38} {'R@1':>6} {'R@3':>6} {'R@5':>6} {'MRR':>6} {'leaks':>6}  {'dR@5':>7}"
    print(header)
    print("-" * len(header))
    results = {}
    for label, retrieve in variants:
        r = evaluate(retrieve, positives, negatives)
        results[label] = r
        delta = r["recall_at_5"] - float(frozen["recall_at_5"])
        print(f"{label:<38} {r['recall_at_1']:>6.4f} {r['recall_at_3']:>6.4f} "
              f"{r['recall_at_5']:>6.4f} {r['mrr']:>6.4f} {len(r['leaks']):>4}/4  {delta:>+7.4f}")

    print("\nControl variant A must reproduce the frozen control, or this harness is lying:")
    a = results["A control (substring, base stops, cov 0)"]
    ok = (round(a["recall_at_5"], 4) == round(float(frozen["recall_at_5"]), 4)
          and round(a["mrr"], 4) == round(float(frozen["mrr"]), 4)
          and len(a["leaks"]) == 2)
    print(f"  HARNESS REPRODUCES FROZEN CONTROL: {ok}")

    print("\nPer-language R@5 for the leading candidates:")
    for label in list(results):
        if label.startswith(("A ", "D ", "E ")):
            print(f"  {label:<38} {results[label]['by_language']}")
    print("\nRemaining leaks per variant:")
    for label, r in results.items():
        print(f"  {label:<38} {r['leaks']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
