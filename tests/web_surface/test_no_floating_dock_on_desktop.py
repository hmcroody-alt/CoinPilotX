"""No floating phone dock may reach a desktop width -- on any page, under any name.

`test_desktop_shell_breakpoint_scope.py` already pins this for
`.mobile-bottom-nav.pulse-universal-dock` in `pulse_desktop_shell.css`, and that
test is correct. It is also, on its own, insufficient in a way worth writing
down, because the miss was not subtle once found:

`static/css/pulse_messages_v2.css` declares a *second* dock,
`.pulse-messenger-dock`, with `position: fixed; display: grid` and no width
condition whatsoever. Different sheet, different class, same five phone
destinations -- so at 1728px the messenger rendered a 520x62 floating pill over
the conversation, which is exactly what the brief calls non-negotiable. Every
other rule touching it sits inside `@media (max-width: 840px)`, which is why it
read as a mobile-only component to anyone skimming, and why a guard keyed to one
class name sailed past it.

So the unit under test here is the *constraint*, not a selector: any position-
fixed dock in any web stylesheet must be suppressed above the desktop
breakpoint. The registry below is default-deny -- a newly added fixed dock that
nobody listed fails `test_every_fixed_dock_is_registered` rather than silently
inheriting no coverage. That is the same shape as the route-auth gate, and for
the same reason: the failure mode is someone adding a third dock, not someone
editing these two.

The other direction matters just as much and is asserted separately. Suppressing
a dock deletes whatever only the dock could reach, so each entry names the
replacement navigation that must appear at the same breakpoint. On the messenger
that was load-bearing: measured on the rendered page, outside the dock the whole
surface offered exactly one app-level link.

Source-level on purpose, like its sibling. A browser answers "is this rule
active at width W", so proving a negative would mean rendering at a spread of
widths and hoping the regression lands on one. The rendered handoff was verified
separately at 390/840/900/1023/1024/1280/1440/1920; this file is what keeps it
true at every width at once.

Run: python3 -m pytest tests/web_surface/test_no_floating_dock_on_desktop.py
"""

from __future__ import annotations

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CSS_DIR = os.path.join(REPO, "static", "css")

#: The breakpoint the product uses to mean "this is a desktop". Shared with
#: `pulse_desktop_shell.css` deliberately: a dock that left at a different width
#: than the desktop navigation arrived would open a band with both or neither.
DESKTOP = 1024

#: Every floating dock, and the navigation that must replace it on desktop.
#: Keyed by selector; the value is the selector that has to become visible at
#: the same breakpoint, or None where the page's desktop chrome already carries
#: the destinations and no dedicated replacement exists.
DOCKS = {
    ".pulse-messenger-dock": {
        "sheet": "pulse_messages_v2.css",
        # The messenger is a full-bleed three-column app with no global PulseSoc
        # chrome, so hiding the dock alone would have removed Reels, Create and
        # Profile from the page entirely.
        "replacement": ".messenger-app-nav",
    },
    ".mobile-bottom-nav.pulse-universal-dock": {
        "sheet": "pulse_desktop_shell.css",
        # Home's desktop rail carries these destinations; its scope is pinned by
        # test_desktop_shell_breakpoint_scope.py, which owns that assertion.
        "replacement": None,
    },
}

#: Fixed-position elements that sit at the bottom of the screen but are not
#: navigation, so the constraint does not apply. Listed rather than pattern-
#: matched, because "is this a dock" is a judgement and an automated guess here
#: would either leak docks or nag about toasts forever.
NOT_NAVIGATION = {
    ".toast",
    ".pulse-fab",
}


def _rules(css: str):
    """Yield (selector, body, media_conditions) for every style rule.

    Hand-rolled for the same reason its sibling is: the only structure that
    matters is nesting inside `@media`, and the declaration body, which a
    selector-only scanner does not expose.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    stack: list[str] = []
    buf = ""
    i = 0
    while i < len(css):
        ch = css[i]
        if ch == "{":
            prelude = buf.strip()
            buf = ""
            if prelude.startswith("@"):
                stack.append(prelude)
            else:
                depth, j = 1, i + 1
                while j < len(css) and depth:
                    if css[j] == "{":
                        depth += 1
                    elif css[j] == "}":
                        depth -= 1
                    j += 1
                yield prelude, css[i + 1:j - 1], tuple(stack)
                i = j
                continue
        elif ch == "}":
            buf = ""
            if stack:
                stack.pop()
        else:
            buf += ch
        i += 1


def _sheets():
    for name in sorted(os.listdir(CSS_DIR)):
        if name.endswith(".css"):
            with open(os.path.join(CSS_DIR, name), encoding="utf-8") as handle:
                yield name, handle.read()


@pytest.fixture(scope="module")
def parsed():
    out = {name: list(_rules(css)) for name, css in _sheets()}
    total = sum(len(v) for v in out.values())
    # A scanner that matched nothing would make every assertion below vacuously
    # true, which is how a guard like this rots without anyone noticing.
    assert len(out) > 20, f"only found {len(out)} stylesheets in {CSS_DIR}"
    assert total > 2000, f"only parsed {total} rules across {len(out)} sheets"
    return out


def _targets(sel, selector):
    """True if any comma-branch of `sel` styles the element itself.

    Substring matching alone conflates `.dock` with `.dock a`, and the
    descendant's `display: grid` says nothing about whether the dock is
    visible. Without this distinction the ordering assertion below is
    permanently red for a sheet that is perfectly correct.
    """
    for part in sel.split(","):
        part = part.strip()
        at = part.find(selector)
        while at != -1:
            tail = part[at + len(selector):]
            if not tail or tail[0] not in " \t\n>+~":
                return True
            at = part.find(selector, at + 1)
    return False


def _min_widths(conditions):
    widths = []
    for cond in conditions:
        widths += [int(n) for n in re.findall(r"min-width:\s*(\d+)px", cond)]
    return widths


def _suppressing_indexes(rules, selector, breakpoint_px):
    """Document positions of rules that hide `selector` at >= breakpoint."""
    out = []
    for idx, (sel, body, conditions) in enumerate(rules):
        if not _targets(sel, selector):
            continue
        if not re.search(r"display:\s*none", body):
            continue
        mins = _min_widths(conditions)
        if mins and min(mins) <= breakpoint_px:
            out.append(idx)
    return out


def _unconditional_display_indexes(rules, selector):
    """Positions of width-free rules that give `selector` a visible display.

    These are what a suppression rule has to outrank. A media query contributes
    no specificity, so ordering is the only thing separating them.
    """
    out = []
    for idx, (sel, body, conditions) in enumerate(rules):
        if not _targets(sel, selector):
            continue
        if any("width" in c for c in conditions):
            continue
        if not re.search(r"display:\s*(?!none)\S+", body):
            continue
        out.append(idx)
    return out


def _suppressed_above(rules, selector, breakpoint_px):
    """True if `selector` is hidden at >= breakpoint *and wins the cascade*."""
    hides = _suppressing_indexes(rules, selector, breakpoint_px)
    if not hides:
        return False
    return max(hides) > max(_unconditional_display_indexes(rules, selector) or [-1])


def _shown_above(rules, selector, breakpoint_px):
    for sel, body, conditions in rules:
        if not _targets(sel, selector):
            continue
        if re.search(r"display:\s*none", body):
            continue
        if not re.search(r"display:\s*\S+", body):
            continue
        mins = _min_widths(conditions)
        if mins and min(mins) <= breakpoint_px:
            return True
    return False


@pytest.mark.parametrize("selector", sorted(DOCKS))
def test_a_floating_dock_is_suppressed_at_desktop_width(parsed, selector):
    """The non-negotiable half: no phone dock floats over a desktop page."""
    sheet = DOCKS[selector]["sheet"]
    rules = parsed[sheet]
    assert any(selector in sel for sel, _b, _c in rules), (
        f"no rule in {sheet} selects {selector!r}. If the dock was renamed, "
        "update this registry; if it was removed, say so here -- an entry that "
        "matches nothing is a guard that cannot fail"
    )
    assert _suppressed_above(rules, selector, DESKTOP), (
        f"{selector} is not hidden at >= {DESKTOP}px in {sheet}.\n"
        "  This is the floating phone dock reaching a desktop, which the brief "
        "calls non-negotiable. It was measured at 1728px as a 520x62 centred "
        "pill before this was fixed once already."
    )


@pytest.mark.parametrize("selector", sorted(DOCKS))
def test_the_suppression_outranks_the_rule_that_floats_the_dock(parsed, selector):
    """Presence of a `display:none` is not the same as it taking effect.

    This is not hypothetical tidiness. The first fix for
    `.pulse-messenger-dock` put its `@media (min-width: 1024px)` block *above*
    the unconditional `display: grid`. A media query adds no specificity, so the
    later rule won: the sheet parsed, the assertion above went green, and the
    dock still measured VISIBLE at 1024/1280/1440/1920 on the rendered page.
    Only a browser caught it, which is exactly the dependency this file exists
    to remove.
    """
    sheet = DOCKS[selector]["sheet"]
    rules = parsed[sheet]
    hides = _suppressing_indexes(rules, selector, DESKTOP)
    floats = _unconditional_display_indexes(rules, selector)
    assert hides, f"{selector} has no desktop suppression in {sheet} at all"
    if not floats:
        return
    assert max(hides) > max(floats), (
        f"{selector} is hidden at >= {DESKTOP}px in {sheet}, but that rule sits "
        f"at rule #{max(hides)} while an unconditional rule giving it a visible "
        f"`display` sits later at #{max(floats)}.\n"
        "  Equal specificity, so the later one wins and the dock still renders "
        "on desktop. Move the media query below the base rule."
    )


@pytest.mark.parametrize(
    "selector", sorted(s for s in DOCKS if DOCKS[s]["replacement"]))
def test_suppressing_a_dock_does_not_delete_its_destinations(parsed, selector):
    """The other half, and the one that makes the first half safe.

    A dock is navigation. Hiding it without putting its destinations somewhere
    else trades a layout complaint for dead navigation, which is worse.
    """
    entry = DOCKS[selector]
    replacement, sheet = entry["replacement"], entry["sheet"]
    rules = parsed[sheet]
    assert any(replacement in sel for sel, _b, _c in rules), (
        f"{selector} is hidden on desktop and its replacement {replacement!r} "
        f"has no rule in {sheet} at all"
    )
    assert _shown_above(rules, replacement, DESKTOP), (
        f"{replacement} never becomes visible at >= {DESKTOP}px, but "
        f"{selector} is hidden there.\n"
        "  That leaves the surface with no primary navigation on desktop. On "
        "the messenger this is not hypothetical: outside the dock the page "
        "offers exactly one app-level link."
    )


def test_every_fixed_dock_is_registered(parsed):
    """Default-deny: a third dock must fail here rather than go uncovered.

    The bug this file exists for was not an edit to a known dock -- it was a
    second one nobody had thought to look for. So the guard has to notice new
    ones, not just re-check the two that are already listed.
    """
    known = set(DOCKS) | NOT_NAVIGATION
    unregistered = []
    for sheet, rules in parsed.items():
        for sel, body, conditions in rules:
            if not re.search(r"position:\s*fixed", body):
                continue
            if not re.search(r"\bbottom:", body):
                continue
            if "dock" not in sel and "bottom-nav" not in sel:
                continue
            # Only the base rule can float it everywhere; a declaration made
            # inside a max-width query is already phone-scoped.
            if any("max-width" in c for c in conditions):
                continue
            if any(k in sel for k in known):
                continue
            unregistered.append(f"  {sheet}: {sel.strip()[:100]}")
    assert not unregistered, (
        "these look like floating docks pinned to the bottom of the viewport "
        "with no width condition, and nothing in this file covers them:\n"
        + "\n".join(sorted(set(unregistered)))
        + "\n\nAdd each to DOCKS with the desktop navigation that replaces it, "
        "or to NOT_NAVIGATION if it is not a navigation surface."
    )
