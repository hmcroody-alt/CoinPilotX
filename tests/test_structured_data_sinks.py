"""Every place a JSON-LD string is written into HTML, and what makes it safe.

This file is keyed on the *sink* -- an `application/ld+json` element in the
source -- rather than on the emitters that feed one. That choice is the whole
point of the file, and it was made the expensive way: three passes that started
from the schema functions and followed them outward each missed a site, and the
one they all missed (`templates/index.html`) is a hand-written node that no
schema function reaches. A source-first search cannot tell you when it is
finished. Enumerating the sinks can, because the sink is where the risk is: a
graph is only dangerous once it is a string inside a raw-text element.

So `SINKS` below is a census, not an allowlist of blessed files. Adding an
`application/ld+json` element anywhere fails `test_the_sink_census_is_complete`
until it is written down here with a classification, and the classifications are
a closed set:

* `SERIALISER` -- every value interpolated into the element comes from
  `seo.schema.serialise_graph`, which owns the escaping contract.
* `LITERAL` -- the element's content is fixed text with nothing interpolated
  into it, so there is no value for anyone to supply.

There is deliberately no third category. `services/marketplace_storefront.py`
used to need one: it held a correct, independently written copy of the escaping.
Correct or not, a security property with two implementations has two chances to
be dropped by a refactor, so it now calls `serialise_graph` too.

Run: python3 -m pytest tests/test_structured_data_sinks.py
"""

import os
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

SERIALISER = "serialiser"
LITERAL = "literal"

# path -> one classification per `application/ld+json` element in that file, in
# source order. The count matters as much as the classification: a second
# element added to a file that already has one has to appear here.
SINKS = {
    "bot.py": [SERIALISER, SERIALISER, LITERAL],
    "services/marketplace_storefront.py": [SERIALISER],
    "templates/_public_shell.html": [SERIALISER],
    "templates/index.html": [SERIALISER],
    "templates/privacy.html": [SERIALISER],
    "templates/seo_page.html": [SERIALISER],
    "templates/terms.html": [SERIALISER],
}

# The exact interpolations a `SERIALISER` sink may contain. Exact strings rather
# than a pattern, because "looks like it came from the serialiser" is the
# judgement this test exists to replace. Each one is either a direct call or a
# local whose only assignment is a call.
SERIALISER_BACKED = {
    "seo_schema.serialise_graph(schema)",          # bot.py, ads landing
    "schema_json",                                 # bot.py, arena page
    "encoded",                                     # marketplace_storefront
    "schema_json|safe",                            # _public_shell.html, seo_page.html
    "organization_ld | safe",                      # index.html, privacy, terms
    "mobile_app_ld | safe",                        # index.html
}

# Directories a sink would be a finding in but not this file's finding: test
# fixtures assert on the markup rather than serving it.
SEARCHED = ("bot.py", "seo", "services", "templates")

# `[^>]*` rather than nothing before the `>`: an element that acquires a `nonce`
# or a `data-` attribute is still a sink, and a census that stopped matching it
# would report the file as having one fewer and read as a removal.
_SINK = re.compile(r"""application/ld\+json['"]?[^>]*>(.*?)</script>""", re.S)
_JINJA = re.compile(r"\{\{(.*?)\}\}", re.S)
_FSTRING = re.compile(r"(?<!\{)\{([^{}]+)\}(?!\})")


def _interpolations(path, body):
    """What a reader of the rendered page could be made to see.

    Jinja and f-strings have to be read differently, and the difference is not
    cosmetic: in an f-string `{{` is an escaped literal brace, so the JSON object
    written out by hand in `bot.py` is *not* an interpolation even though it is
    full of braces. Reading it as one would classify a static literal as
    attacker-reachable and hide the fact that nothing reaches it.
    """

    if path.endswith(".html"):
        return {m.strip() for m in _JINJA.findall(body)}
    return {m.strip() for m in _FSTRING.findall(body.replace("{{", "").replace("}}", ""))}


def _discover():
    found = {}
    for target in SEARCHED:
        base = ROOT / target
        paths = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in paths:
            if path.suffix not in (".py", ".html") or not path.is_file():
                continue
            text = path.read_text(errors="replace")
            if "application/ld+json" not in text:
                continue
            sinks = [
                (text[: m.start()].count("\n") + 1, m.group(1))
                for m in _SINK.finditer(text)
            ]
            # A file can name the media type without being a sink -- the
            # serialiser's own docstring does, explaining why these elements are
            # not executable script. Prose is not an element.
            if sinks:
                found[path.relative_to(ROOT).as_posix()] = sinks
    return found


DISCOVERED = _discover()


def test_the_sink_census_is_complete():
    """Red here means a new `application/ld+json` element exists.

    That is not by itself a defect -- it is an unclassified one. Decide which of
    the two categories it is, make it true if it is not, and write it down. The
    failure is the point at which someone has to decide, which is the thing a
    grep of the schema module cannot force.
    """

    assert {p: len(v) for p, v in DISCOVERED.items()} == \
        {p: len(v) for p, v in SINKS.items()}, (
            "the JSON-LD sinks in the source no longer match the census in this "
            "file; classify the new one as SERIALISER or LITERAL"
        )


def test_an_unterminated_sink_is_not_silently_counted():
    """The regex spans to the next `</script>`, so a block someone forgot to
    close would swallow the rest of the file and still be counted as one sink.
    A sink body long enough to contain another one is that mistake."""

    for path, sinks in DISCOVERED.items():
        for line, body in sinks:
            assert "application/ld+json" not in body, \
                f"{path}:{line} is not closed before the next ld+json element begins"


@pytest.mark.parametrize("path", sorted(SINKS))
def test_each_sink_carries_only_what_its_classification_allows(path):
    for (line, body), kind in zip(DISCOVERED[path], SINKS[path]):
        interpolated = _interpolations(path, body)
        if kind == LITERAL:
            assert not interpolated, (
                f"{path}:{line} is classified as a static literal but now "
                f"interpolates {sorted(interpolated)}; reclassify it and route "
                "it through seo.schema.serialise_graph"
            )
        else:
            unknown = interpolated - SERIALISER_BACKED
            assert not unknown, (
                f"{path}:{line} interpolates {sorted(unknown)}, which is not a "
                "known serialiser-backed value. Either it comes from "
                "seo.schema.serialise_graph -- in which case add it to "
                "SERIALISER_BACKED -- or it is a second serialiser."
            )


def test_the_serialiser_is_the_only_implementation_of_the_escaping():
    """A `json.dumps` whose result reaches a script element is the shape of the
    bug four commits closed, so the pattern is forbidden rather than audited.

    This checks the two files that write a sink from Python. It cannot see a
    `json.dumps` in a third file that has not been written yet -- which is what
    the census above is for.
    """

    for path in ("bot.py", "services/marketplace_storefront.py"):
        text = (ROOT / path).read_text(errors="replace")
        for match in re.finditer(r"json\.dumps\(", text):
            window = text[match.start(): match.start() + 400]
            assert "application/ld+json" not in window, (
                f"{path} serialises JSON within 400 characters of an ld+json "
                "element; use seo.schema.serialise_graph"
            )


def test_the_serialiser_still_escapes_the_one_character_that_matters():
    """Both modes, because the storefront asks for the other one and a change
    that fixed only the default would leave the marketplace open."""

    from seo import schema as seo_schema

    hostile = {"name": "</script <svg onload=alert(1)", "text": "café  "}
    for ensure_ascii in (False, True):
        out = seo_schema.serialise_graph(hostile, ensure_ascii=ensure_ascii)
        assert "<" not in out, f"ensure_ascii={ensure_ascii} let a literal < through"
        import json as _json
        assert _json.loads(out) == hostile, \
            f"ensure_ascii={ensure_ascii} changed the payload it was given"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
