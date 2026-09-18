"""`clean_html` must not crash a page because a column holds a number.

Sixteen public `/pulse/post/<id>` pages returned HTTP 500 in production on
2026-09-18. The traceback was identical for every one of them::

    File "/app/bot.py", line 88210, in pulse_post_page
      media_id = clean_html(item.get("id") or "")
    File "/app/bot.py", line 124465, in clean_html
      text = re.sub(r"<[^>]+>", " ", text or "")
    TypeError: expected string or bytes-like object, got 'int'

`clean_html` is reached from several hundred call sites with values taken
straight out of row dicts, so "the caller should have cast it" is not a fix that
holds -- the next numeric column reaches it the same way. The sanitizer is the
place that knows it needs text.

The narrow contract this file pins
----------------------------------
The coercion is deliberately limited to *truthy* non-strings, because that is
exactly the set that used to raise. ``None``, ``0``, ``False`` and ``""`` all
already became ``""`` through the existing ``or ""``, and several hundred call
sites depend on that. Widening the fix to ``str(text)`` would turn ``0`` into
the string ``"0"`` and change output on pages that work today.

So this file asserts both halves: the crash is gone, *and* the falsy behaviour
is unchanged. A fix that only satisfied the first half would be a regression
nobody would notice until a "0" appeared in a page title.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402


@pytest.mark.parametrize(
    "value,expected",
    [
        (1378, "1378"),
        (2409, "2409"),
        (12.5, "12.5"),
        (True, "True"),
    ],
    ids=["media-id", "another-media-id", "float-column", "bool-column"],
)
def test_truthy_non_strings_are_coerced_instead_of_raising(value, expected):
    assert bot.clean_html(value) == expected


@pytest.mark.parametrize(
    "value", [None, "", 0, False, [], {}], ids=["none", "empty", "zero", "false", "list", "dict"]
)
def test_falsey_values_still_collapse_to_empty_string(value):
    """The half of the contract a careless fix would break."""

    assert bot.clean_html(value) == ""


def test_string_behaviour_is_untouched():
    assert bot.clean_html("<b>hello</b> world") == "hello world"
    assert bot.clean_html("  spaced\n\nout  ") == "spaced out"
    assert bot.clean_html("<script>alert(1)</script>") == "alert(1)"


def test_the_exact_production_call_shape_no_longer_raises():
    """`media_id = clean_html(item.get("id") or "")` with an integer id.

    Reproduced as the route writes it, including the `or ""`, so that a future
    refactor of either side is checked against the real expression.
    """

    item = {"id": 1378, "media_url": "https://cdn.example/x.jpg"}
    assert bot.clean_html(item.get("id") or "") == "1378"
