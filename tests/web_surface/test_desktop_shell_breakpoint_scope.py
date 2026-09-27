"""Narrowing the window must not swap Home back to the pre-rearchitecture product.

`static/css/pulse_desktop_shell.css` originally put every rule it owned inside a
single `@media (min-width: 1024px)`. That reads as conservative -- nothing it
does can reach a phone -- and it is, for the rules that are genuinely about
having a desktop's width. But the same block also held the decisions about what
Home *is*: lead with people rather than an aggregate, keep the composer a prompt
until someone writes, don't print a row of zeroes under a post with no activity,
calm the animated city behind the feed.

None of those are claims about width, and gating them on one meant the old Home
was still shipping -- reachable by dragging a window edge. Measured at 900px
before the fix: the demoted 266px globe back as the first element on the page,
the composer open to eight post types, and the first post 1064px down instead of
454. It was reported as "when the screen is minimized it shows the old version",
which is exactly what it was.

The fix moved those rules to the top level of the file. This test is what keeps
them there, because the regression is silent in every way that matters: the file
still parses, the selectors still exist, every desktop screenshot is unchanged,
and the only symptom is at a width nobody re-checks after a CSS edit.

Two directions, both asserted:

  - the product decisions must NOT be inside a min-width query;
  - the desktop *layout* must stay inside one -- most importantly the rule that
    suppresses the phone dock, which the brief called non-negotiable. Ungating
    that would delete the mobile navigation bar, a far worse failure than the
    one this file exists to prevent.

Deliberately a source test and not a rendering one. A browser can only answer
"is this rule active at width W", so proving the negative would mean rendering
at a spread of widths and hoping the regression lands on one of them; the source
answers it exactly and at every width at once.

Run: python3 -m pytest tests/web_surface/test_desktop_shell_breakpoint_scope.py
"""

from __future__ import annotations

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SHEET = os.path.join(REPO, "static", "css", "pulse_desktop_shell.css")

#: Selectors whose rule must sit at the top level of the sheet. Each is keyed by
#: the decision it carries, because the failure message has to say what broke
#: rather than which line moved -- whoever hits this is likelier to have
#: re-wrapped the file than to have touched this selector on purpose.
UNGATED = {
    "the animated city stays calm behind the feed":
        ".pulse-environment-engine",
    "the composer stays a prompt until someone writes":
        ".pulse-publisher-card:not(.is-expanded) .composer-type-row",
    "a post with no activity prints no zeroes":
        ".post-summary-metric.is-zero",
    "author names are not shouted in uppercase":
        ".post-card-name",
    "the comment box waits to be asked for":
        ":not(.is-commenting) .pulse-comment-composer-v2",
    "the empty status rail is one row, not two tiles":
        "[data-status-empty]:not([hidden])",
    "the composer's metadata chips wait for focus":
        "[data-composer-rail]",
}

#: The other half. These are about having a desktop's width, and the dock rule
#: is the one the brief called non-negotiable: it must never apply to a phone.
GATED = {
    "the phone dock is suppressed ONLY on desktop":
        ".mobile-bottom-nav.pulse-universal-dock",
    "the three-zone grid needs the width":
        ".pulse-desktop-layout",
    "the global header needs the width":
        ".pulse-desktop-topbar",
    "the left navigation rail needs the width":
        ".desktop-rail-link",
}


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _blocks(css: str):
    """Yield (selector_text, depth, conditions) for every style rule.

    A hand-rolled scanner rather than a CSS parser because the only structure
    this test cares about is nesting inside `@media`, and adding a dependency to
    answer "is this rule inside a query" would be the larger risk. `conditions`
    is the stack of `@media` prelude strings enclosing the rule, so a rule at
    the top level has an empty one.
    """
    css = _strip_comments(css)
    stack: list[str] = []
    buf = ""
    for ch in css:
        if ch == "{":
            prelude = buf.strip()
            buf = ""
            if prelude.startswith("@"):
                stack.append(prelude)
            else:
                yield prelude, len(stack), tuple(stack)
                stack.append("")          # a style rule's own braces still nest
        elif ch == "}":
            buf = ""
            if stack:
                stack.pop()
        else:
            buf += ch


@pytest.fixture(scope="module")
def rules():
    with open(SHEET, encoding="utf-8") as handle:
        parsed = list(_blocks(handle.read()))
    # A scanner that silently matched nothing would make every assertion below
    # vacuously true, which is the classic way a guard like this rots.
    assert len(parsed) > 40, f"only parsed {len(parsed)} rules out of {SHEET}"
    return parsed


def _matching(rules, needle):
    return [r for r in rules if needle in r[0]]


def _min_width_conditions(conditions) -> list:
    return [c for c in conditions if "min-width" in c]


@pytest.mark.parametrize("decision,selector", sorted(UNGATED.items()))
def test_product_decisions_are_not_gated_on_width(rules, decision, selector):
    found = _matching(rules, selector)
    assert found, (
        f"no rule in pulse_desktop_shell.css selects {selector!r}. This test "
        f"guards a decision -- {decision} -- so if the rule was renamed rather "
        "than removed, update the selector here; if it was removed, that is the "
        "thing to explain"
    )
    for sel, _depth, conditions in found:
        gates = _min_width_conditions(conditions)
        assert not gates, (
            f"{decision}\n"
            f"  selector: {sel.strip()[:110]}\n"
            f"  is inside: {gates}\n"
            "This is not a layout rule, so gating it on width means a narrower "
            "window silently serves the product this sheet replaced. That is "
            "the exact regression this file exists to prevent -- it was "
            "reported as 'when the screen is minimized it shows the old "
            "version'. If this rule genuinely needs a floor, the honest fix is "
            "a matching max-width rule that states what narrow gets, not a "
            "min-width gate that leaves narrow with the old behaviour."
        )


@pytest.mark.parametrize("reason,selector", sorted(GATED.items()))
def test_desktop_layout_stays_behind_a_min_width_query(rules, reason, selector):
    found = _matching(rules, selector)
    assert found, f"no rule selects {selector!r} ({reason})"
    for sel, _depth, conditions in found:
        assert _min_width_conditions(conditions), (
            f"{reason}\n"
            f"  selector: {sel.strip()[:110]}\n"
            "  is at the top level of the sheet, so it now applies to phones "
            "too. For the dock rule in particular this deletes the mobile "
            "navigation bar at every width, which the brief called "
            "non-negotiable."
        )


def test_the_dock_is_only_ever_suppressed_at_desktop_widths(rules):
    """Worth its own test, not just a parametrised case.

    The dock rule is `display: none !important`, which nothing else in the page
    can outrank. Its blast radius if it escaped the query is the whole mobile
    product, so the floor is asserted numerically rather than by "some
    min-width" -- 1024 is where `pulse_desktop_feed.css` starts providing the
    topbar and rails that replace it, and below that there is nothing to
    navigate with.
    """
    found = _matching(rules, ".mobile-bottom-nav.pulse-universal-dock")
    assert found, "the dock suppression rule is gone from pulse_desktop_shell.css"
    for sel, _depth, conditions in found:
        floors = [
            int(m.group(1))
            for c in conditions
            for m in [re.search(r"min-width:\s*(\d+)px", c)]
            if m
        ]
        assert floors, f"{sel.strip()[:80]} has no min-width floor: {conditions}"
        assert min(floors) >= 1024, (
            f"the dock is suppressed from {min(floors)}px up. Below 1024px "
            "there is no desktop topbar or rail -- pulse_desktop_feed.css hides "
            "all three zones with !important -- so this leaves those widths "
            "with no navigation at all."
        )


def test_narrow_widths_are_addressed_rather_than_left_to_the_old_product(rules):
    """The positive half: ungating alone would have been half a fix.

    Below 1024px `pulse_desktop_feed.css` hides the topbar and both rails, so
    the Pulse Network card is the only thing carrying the community mood, Pulse
    Radio, /pulse/live and /scam-shield. Reusing the desktop rule (`display:
    none`) there would not reposition Curious, it would delete it and three
    destinations with it -- the one thing the brief ruled out. So the sheet has
    to say something specific about narrow, and this asserts it still does.
    """
    narrow = [
        r for r in rules
        if any("max-width" in c and "min-width" not in c for c in r[2])
    ]
    assert narrow, (
        "pulse_desktop_shell.css no longer has any max-width block. The Pulse "
        "Network card is a 266px illustrated globe by default and is first on "
        "the page at narrow widths; without a block that compacts it, ungating "
        "the rest of this sheet leaves the aggregate leading again."
    )
    selectors = " ".join(r[0] for r in narrow)
    assert "pulse-network-globe" in selectors, (
        "the narrow block stopped addressing the globe, which is the element "
        "that makes this card 266px tall"
    )
    assert "order" in _strip_comments(open(SHEET, encoding="utf-8").read()), (
        "the `order` declarations that put the status tiles above the Pulse "
        "Network card are gone, so the aggregate leads the page again at "
        "narrow widths"
    )


def test_the_globe_link_survives_every_width():
    """`.pulse-network-globe-hit` is the only route to the intelligence page.

    The narrow block scales the globe rather than hiding it for exactly this
    reason, and the distinction is easy to lose in a later cleanup -- `display:
    none` on the stage would look like a tidier way to write the same
    compaction. It is not: it is a dead destination, which is the failure mode
    this mission was told to eliminate.
    """
    css = _strip_comments(open(SHEET, encoding="utf-8").read())
    hit = re.search(
        r"\.pulse-network-globe-hit\s*\{([^}]*)\}", css)
    assert hit, "the globe link rule is gone"
    assert "display: none" not in hit.group(1), (
        "the only link to /pulse/premium/intelligence on this page is being "
        "hidden rather than resized"
    )
    for stage in re.finditer(r"\.pulse-network-globe-stage\s*\{([^}]*)\}", css):
        assert "display: none" not in stage.group(1), (
            "hiding the globe stage takes its link with it; scale it instead"
        )
