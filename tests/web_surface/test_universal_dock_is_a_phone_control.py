"""The floating phone dock must not ride along onto a desktop page.

## What was wrong

`static/css/pulse_home_os.css` opens the dock with
`body.pulse-home-os .mobile-bottom-nav.pulse-universal-dock { display: grid
!important }` and no media query at all. The `pulse-universal-dock` class is
added at runtime by `bot.pulse_universal_dock_runtime_script`, on every page, so
that unconditional `!important` outranks the shell's own conditional `display:
grid` inside `@media (max-width: 900px)` at *every* width. The shell's
breakpoint never reached this element and the dock rode from 390px to 1920px.

`pulse_desktop_shell.css` already reverses it, and
`test_desktop_shell_breakpoint_scope.py` already guards that reversal. But only
Home loads that sheet. The Marketplace pages are rendered into
`bot.pulse_social_shell`, which loads `pulse_home_os.css` and not
`pulse_desktop_shell.css` -- so the fix never reached them, and a product page
at 1440px drew the desktop top bar, the left navigation rail *and* the floating
phone dock: three navigations carrying overlapping destinations, which is the
duplication the brief called out by name.

## What these tests hold

The cure went into the sheet that carries the cause, so every page that loads
the unconditional rule now also loads its suppression rather than depending on
a second sheet it may never request. That is a structural claim, not a
per-sheet one, so the central test below is written as a sweep: *any* stylesheet
that opens the dock ungated must also close it. A third sheet doing the same
thing tomorrow fails here rather than in a screenshot nobody takes.

Both directions matter and the second is the dangerous one. The suppression is
`display: none !important` on a selector nothing in the page can outrank -- if
it ever escaped its query it would delete the mobile navigation bar outright,
which is a far worse failure than the one this file exists to prevent.

## Why the floor is 1024 and not 901

The shell reveals the dock only up to 900px, so 901 would look like the tighter
number, and the band from 901 to 1023 does still show both the dock and the
shell's `.nav` strip. It is deliberately left alone. `pulse_desktop_feed.css`
hides `.pulse-desktop-topbar` below 1024 with `!important`, so `.nav` is the
only other navigation in that band and a 901px floor here would sit one edit
away from a page with no way out -- the failure
`test_shell_nav_parity.test_the_top_bar_appears_exactly_where_the_stylesheet_stops_hiding_it`
warns about in as many words. 1024 is the number `pulse_desktop_shell.css`
already chose and that its own test already pins; two sheets agreeing beats a
third opinion.

Run: python3 -m pytest tests/web_surface/test_universal_dock_is_a_phone_control.py
"""

from __future__ import annotations

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CSS_DIR = os.path.join(REPO, "static", "css")

DOCK = ".mobile-bottom-nav.pulse-universal-dock"

#: The floor `pulse_desktop_shell.css` chose and `pulse_desktop_feed.css`
#: corroborates. Restated rather than imported so that a change to it has to be
#: made in both places on purpose.
DESKTOP_FLOOR = 1024


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _rules(css: str):
    """Yield (selector_text, declarations, media_conditions) for every rule.

    Hand-rolled for the same reason the sibling scanner in
    `test_desktop_shell_breakpoint_scope.py` is: the only structure that matters
    here is whether a rule sits inside an `@media`, and a CSS parser is a
    dependency this repository does not otherwise need. Vendored rather than
    imported because a test that breaks when a *different* test file is renamed
    is a worse trade than twenty duplicated lines.
    """
    css = _strip_comments(css)
    stack: list[str] = []
    buf = ""
    index = 0
    while index < len(css):
        ch = css[index]
        if ch == "{":
            prelude = buf.strip()
            buf = ""
            if prelude.startswith("@"):
                stack.append(prelude)
            else:
                close = css.find("}", index)
                yield prelude, css[index + 1:close if close != -1 else len(css)], tuple(stack)
                index = close if close != -1 else len(css)
        elif ch == "}":
            buf = ""
            if stack:
                stack.pop()
        else:
            buf += ch
        index += 1


def _sheets():
    return sorted(
        name for name in os.listdir(CSS_DIR) if name.endswith(".css")
    )


def _floors(conditions) -> list[int]:
    return [
        int(match.group(1))
        for condition in conditions
        for match in [re.search(r"min-width:\s*(\d+)px", condition)]
        if match
    ]


#: The body class the dock's own rules are scoped by. Any *other* class on the
#: body is a page mode rather than a width.
DOCK_SCOPE = "pulse-home-os"


def _body_modes(selector: str) -> set[str]:
    """Body classes in a selector, minus the one the dock is always scoped by.

    `pulse_live_studio.css` hides the dock at every width on purpose:
    `body.pulse-home-os.pulse-live-shell-mode > .mobile-bottom-nav...`. The
    studio takes the whole viewport and a floating dock over a broadcast is not
    a navigation, it is an obstruction -- so that rule is right, and a test that
    demanded a min-width floor from it would be demanding a bug.

    The distinction is the extra class. Hidden because the page is in a takeover
    mode is a different claim from hidden because the window is wide, and only
    the second one can delete the phone navigation from an ordinary page.
    """
    modes: set[str] = set()
    for run in re.finditer(r"\bbody((?:\.[\w-]+)+)", selector):
        modes.update(run.group(1).lstrip(".").split("."))
    return modes - {DOCK_SCOPE}


def _dock_rules(name):
    with open(os.path.join(CSS_DIR, name), encoding="utf-8") as handle:
        return [rule for rule in _rules(handle.read()) if DOCK in rule[0]]


#: Sheets that say anything at all about the dock. Computed once so the sweep
#: below and its own anti-vacuity check read the same set.
SHEETS_WITH_DOCK_RULES = [name for name in _sheets() if _dock_rules(name)]


def test_the_scanner_found_the_sheets_this_file_is_about():
    """A sweep over an empty set passes forever.

    Both sheets are named because the whole point of this file is that the
    second one was missing its half of the pair; a scanner that quietly found
    only the first would report exactly the state that was broken.
    """
    assert "pulse_home_os.css" in SHEETS_WITH_DOCK_RULES
    assert "pulse_desktop_shell.css" in SHEETS_WITH_DOCK_RULES


@pytest.mark.parametrize("sheet", SHEETS_WITH_DOCK_RULES)
def test_a_sheet_that_reveals_the_dock_also_suppresses_it(sheet):
    """The structural rule, swept rather than asserted per sheet.

    A sheet that opens the dock with an unconditional `display: grid
    !important` has made the dock unconditional for every page that loads it,
    whatever the shell's own breakpoints say. It therefore owes that page the
    matching suppression: depending on a *different* stylesheet to undo it is
    how the Marketplace ended up with three navigations at once.
    """
    reveals = [
        rule for rule in _dock_rules(sheet)
        if re.search(r"display:\s*grid", rule[1]) and not _floors(rule[2])
    ]
    if not reveals:
        pytest.skip(f"{sheet} does not reveal the dock unconditionally")
    suppressions = [
        rule for rule in _dock_rules(sheet) if re.search(r"display:\s*none", rule[1])
    ]
    assert suppressions, (
        f"{sheet} makes the dock `display: grid !important` with no media query "
        "and never takes it back. `pulse-universal-dock` is added by script on "
        "every page, so that rule outranks the shell's own "
        "`@media (max-width: 900px)` at every width -- any page loading this "
        "sheet now draws the phone dock on top of its desktop navigation. Add "
        f"the `display: none !important` suppression inside a "
        f"`min-width: {DESKTOP_FLOOR}px` query in this same sheet; do not rely "
        "on pulse_desktop_shell.css, which most pages never load."
    )


@pytest.mark.parametrize("sheet", SHEETS_WITH_DOCK_RULES)
def test_the_suppression_never_escapes_its_desktop_query(sheet):
    """The dangerous direction.

    `display: none !important` on this selector is unoutrankable. Ungated it
    does not reposition the mobile navigation bar, it deletes it, at every
    width, on every page that loads the sheet.
    """
    checked = 0
    for selector, declarations, conditions in _dock_rules(sheet):
        if not re.search(r"display:\s*none", declarations):
            continue
        if _body_modes(selector):
            continue
        checked += 1
        floors = _floors(conditions)
        assert floors, (
            f"{sheet}: `{selector.strip()[:90]}` hides the dock with no "
            "min-width floor, which removes the phone navigation bar entirely."
        )
        assert min(floors) >= DESKTOP_FLOOR, (
            f"{sheet}: the dock is hidden from {min(floors)}px up. Below "
            f"{DESKTOP_FLOOR}px pulse_desktop_feed.css still hides the desktop "
            "top bar with !important, so the shell's `.nav` strip is the only "
            "other navigation -- and a page whose `.nav` is empty would have "
            "none at all."
        )
    if sheet == "pulse_home_os.css":
        assert checked == 1, (
            "pulse_home_os.css is the sheet that both reveals and suppresses "
            f"the dock, and exactly one width-scoped suppression is expected; "
            f"found {checked}. Two would mean two floors to keep in agreement."
        )


def test_the_marketplace_shell_loads_the_sheet_that_now_carries_both_halves():
    """The premise, pinned against the page that exposed it.

    These tests are about a sheet, but the reason the defect existed is about
    which sheets a page requests. `pulse_social_shell` renders the Marketplace
    and loads `pulse_home_os.css` without `pulse_desktop_shell.css`; if that
    ever inverts, the suppression guarded above is still correct and no longer
    reaches the page it was written for.
    """
    with open(os.path.join(REPO, "bot.py"), encoding="utf-8") as handle:
        source = handle.read()
    shell = source.index("def pulse_social_shell")
    end = source.index("\ndef ", shell + 1)
    body = source[shell:end]
    assert "pulse_home_os.css" in body, (
        "pulse_social_shell no longer loads pulse_home_os.css, so the dock "
        "suppression guarded here no longer reaches the Marketplace. Check what "
        "reveals the dock on that page now."
    )
