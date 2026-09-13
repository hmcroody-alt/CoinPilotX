"""Locks for the gate that says the web tokens still match the native theme.

Why this gate needs its own tests
---------------------------------
The parity gate compares two hand-maintained copies of one table: the native
palettes in `mobile-native/src/theme/` and their translation in
`web/src/styles/tokens.css`. It is the only thing standing between "someone
adjusted a surface colour in the app" and "the website quietly stopped being
the same product".

Which makes the gate the same risk one level up, and in the same direction:
every way it can break makes it *quieter*. A regex that stops matching a
palette, a selector resolver that stops recognising `[data-theme]`, a colour
normaliser that starts returning a sentinel for everything -- each one reduces
the number of comparisons performed, and zero comparisons performed reads
exactly like zero problems found. Running it against the real repo proves only
that the tree is currently healthy; it proves nothing about whether the gate
can still detect an unhealthy one.

So these tests drive the gate's own functions against synthesised inputs and
assert it goes red where it must. The real repo is never mutated.

How far that claim was actually verified
----------------------------------------
Asserting "this suite would catch a regression" is the same unfalsifiable claim
the gate makes, so it was measured rather than asserted. 27 plausible
regressions were injected into a throwaway copy of the gate -- a colour
normaliser returning a sentinel, at-rule bodies hoisted into the unconditional
cascade, the high-contrast overlay applied as a full palette, the pin assumed
rather than read, the palette map emptied -- and each had to turn *its named
test* red, not merely some test. Three behaviour-preserving edits were injected
alongside them and had to produce zero failures, because a suite that fails on
everything looks identical to one that detects everything.

Two findings from that run are worth keeping, since both were tests that
claimed a property they did not hold:

  * `test_equivalent_colour_spellings_compare_equal` passed with alpha
    hardcoded to 1.0 -- both sides of an equality degrade together, so a
    function mapping every colour to one value satisfies it. It now asserts the
    channel directly.
  * `test_the_native_pin_is_read_rather_than_assumed` passed against
    `return "dark"`. The property is unobservable while the real file is the
    only input, which is why `read_active_theme` takes source as an argument.

One mutation is a deliberate equivalent: pinning `native_active_theme` to
"dark" is unobservable while the live pin *is* "dark". It is kept, and expected
to detect nothing, so that the day native unpins it the harness starts failing.

Zero-arg tests, no fixtures: this directory runs files as scripts.
"""

import contextlib
import importlib.util
import io
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
GATE_PATH = REPO / "scripts" / "ops" / "native_theme_parity_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("_theme_parity_gate", GATE_PATH)
    assert spec and spec.loader, f"cannot load {GATE_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _load_gate()


# ---------------------------------------------------------------------------
# Colour normalisation
# ---------------------------------------------------------------------------


def test_equivalent_colour_spellings_compare_equal():
    """Same colour, different text, must not be reported as drift.

    This is the property that keeps the gate usable. Native writes
    `rgba(0, 150, 111, 0.10)`; CSS convention writes `0.1`. A gate that called
    those a divergence would be red on a healthy tree from its first day, and a
    permanently-red gate is one nobody reads.
    """
    assert GATE.as_rgba("#FFFFFF") == GATE.as_rgba("#ffffff")
    assert GATE.as_rgba("#fff") == GATE.as_rgba("#ffffff")
    assert GATE.as_rgba("rgba(0, 150, 111, 0.10)") == GATE.as_rgba("rgba(0,150,111,0.1)")
    assert GATE.as_rgba("rgb(5, 9, 16)") == GATE.as_rgba("#050910")

    # The alpha channel is actually carried, not merely carried *consistently*.
    # Mutation testing found that the `0.10`/`0.1` pair above passes unchanged
    # when alpha is hardcoded to 1.0 -- both sides degrade identically, so the
    # comparison still agrees. A test whose subject is equality can be satisfied
    # by a function that maps everything to one value, which is why the
    # specific channel has to be asserted rather than inferred.
    assert GATE.as_rgba("rgba(0, 150, 111, 0.1)")[3] == 0.1
    assert GATE.as_rgba("#00966f")[3] == 1.0
    assert GATE.as_rgba("#00966f80")[3] != 1.0


def test_different_colours_compare_unequal():
    """The other half of the property, and the half worth asserting.

    A normaliser that collapsed everything to one value would satisfy the test
    above perfectly. Stated alone, "equivalent spellings are equal" is passed by
    `return 0`.
    """
    assert GATE.as_rgba("#050910") != GATE.as_rgba("#050911")
    assert GATE.as_rgba("rgba(1, 2, 3, 0.5)") != GATE.as_rgba("rgba(1, 2, 3, 0.6)")
    assert GATE.as_rgba("#ffffff") != GATE.as_rgba("rgba(255, 255, 255, 0.99)")


def test_an_unparseable_colour_raises_rather_than_returning_a_sentinel():
    """Two unreadable values must never compare equal to each other.

    If `as_rgba` returned `None` or `(0,0,0,0)` on failure, a change that made
    BOTH sides unparseable would compare equal and pass. Raising means the
    comparison is reported as a problem instead.
    """
    for bad in ("", "teal", "var(--nope)", "#12345", "rgba(1, 2)", "rgb(a, b, c)"):
        raised = False
        try:
            GATE.as_rgba(bad)
        except ValueError:
            raised = True
        assert raised, f"as_rgba({bad!r}) did not raise"


# ---------------------------------------------------------------------------
# TypeScript reading
# ---------------------------------------------------------------------------


def test_spreads_resolve_in_declaration_order():
    """`{ ...BASE, key: override }` must yield the override, not the base."""
    source = 'const X: Palette = {\n  ...BASE,\n  accent: "#111111"\n};'
    entries, spreads = GATE.parse_ts_object(source, "const X: Palette")
    assert spreads == ["BASE"], spreads
    assert entries == {"accent": "#111111"}, entries


def test_a_nested_object_does_not_truncate_the_literal():
    """Brace counting, not a lazy match to the first `}`.

    A regex stopping at the first closing brace would silently drop every key
    after a nested object -- which downstream reads as "the CSS declares values
    native does not have", sending the reader to the wrong file entirely.
    """
    source = (
        'const X: Palette = {\n'
        '  first: "#010101",\n'
        '  nested: { inner: "#020202" },\n'
        '  last: "#030303"\n'
        '};'
    )
    entries, _ = GATE.parse_ts_object(source, "const X: Palette")
    assert entries.get("last") == "#030303", entries


def test_a_missing_declaration_is_could_not_check_not_empty():
    """Absent input must raise, never return `{}`.

    An empty dict would flow into the comparison as "no keys to check" and the
    gate would report success having compared nothing.
    """
    raised = False
    try:
        GATE.parse_ts_object("const OTHER = {};", "const MISSING: Palette")
    except GATE.CouldNotCheck:
        raised = True
    assert raised, "a missing declaration returned instead of raising"


def test_the_native_pin_is_read_rather_than_assumed():
    """The pin must come out of the source text, not out of a constant.

    Driven with synthetic source, because that is the only way the property is
    observable. Mutation testing proved the point: replacing the extraction with
    `return "dark"` passed an earlier version of this test, which asserted only
    that the real file yields `"dark"` -- true of both the working gate and the
    broken one. The whole job of this function is to notice the day native's pin
    changes, and a hardcoded answer never notices anything.
    """
    other = 'const activeTheme: ThemeMode = "light_futuristic";'
    assert GATE.read_active_theme(other) == "light_futuristic"

    system = 'const activeTheme: ThemeMode = "system";'
    assert GATE.read_active_theme(system) == "system"


def test_a_removed_native_pin_is_could_not_check():
    """If native drops the pin, the gate's premise is gone.

    That must be loud. A missing pin silently defaulting to `"dark"` would keep
    the web pinned while native had opened theme selection -- the exact
    divergence this gate exists to announce, hidden by the gate itself.
    """
    raised = False
    try:
        GATE.read_active_theme("const activeTheme = resolveFromPreferences();")
    except GATE.CouldNotCheck:
        raised = True
    assert raised, "a missing activeTheme pin did not raise CouldNotCheck"


def test_the_live_native_pin_is_still_dark():
    """The tree as it stands. Distinct from the tests above on purpose.

    Those assert the gate *reads*; this asserts what it currently reads. When
    native activates theme selection this is the test that goes red, and the
    failure message should send the reader to web/src/theme/themes.ts.
    """
    assert GATE.native_active_theme() == "dark"


def test_native_metrics_capture_both_arms_of_the_compact_ternary():
    """`compact ? 46 : 56` yields two values the web must carry, not one."""
    metrics = GATE.native_metrics()
    assert metrics.get("rowMinHeight") == 56, metrics
    assert metrics.get("rowMinHeightCompact") == 46, metrics
    assert metrics.get("radius") == 14, metrics


# ---------------------------------------------------------------------------
# CSS reading
# ---------------------------------------------------------------------------


def test_at_rule_bodies_are_not_hoisted_into_the_unconditional_cascade():
    """The defect this gate shipped with, locked so it cannot return.

    The block regex cannot match a body containing braces, so for
    `@media (...) { :root { ... } }` it skipped the `@media` line and matched
    the inner `:root` block alone -- applying a media query's contents
    unconditionally, and (since it sits last) overriding every palette above it.
    """
    css = """
    :root { --pulse-accent: #111111; }
    @media (prefers-reduced-transparency: reduce) {
      :root { --pulse-glass: #999999; }
    }
    """
    blocks = GATE.parse_css_blocks(css)
    resolved = GATE.resolve_css_palette(blocks, "dark", high_contrast=False)
    assert resolved.get("--pulse-accent") == "#111111", resolved
    assert "--pulse-glass" not in resolved, (
        "a media query's declaration leaked into the unconditional cascade"
    )


def test_an_unmodelled_property_inside_an_at_rule_is_could_not_check():
    """The gate must refuse to compare a value some media query overrides.

    Silently ignoring at-rules would be the quiet failure: the palette would be
    compared against a stylesheet whose real computed value differs.
    """
    css = '@media (min-width: 900px) { :root { --pulse-accent: #123456; } }'
    raised = False
    try:
        GATE.parse_css_blocks(css)
    except GATE.CouldNotCheck as exc:
        raised = "--pulse-accent" in str(exc)
    assert raised, "an unmodelled at-rule property did not raise CouldNotCheck"


def test_var_references_resolve_to_their_literal():
    """`--pulse-glass: var(--pulse-surface)` must compare as the surface colour.

    Without this the reduced-transparency collapse could only be expressed by
    copying a literal, which is the spelling that stops tracking the day the
    surface changes.
    """
    css = """
    :root { --pulse-surface: #0b141c; }
    [data-hc="1"] { --pulse-glass: var(--pulse-surface); }
    """
    blocks = GATE.parse_css_blocks(css)
    resolved = GATE.resolve_css_palette(blocks, "dark", high_contrast=True)
    assert resolved.get("--pulse-glass") == "#0b141c", resolved


def test_a_cyclic_var_reference_terminates_and_stays_visible():
    """A cycle must not hang, and must not be silently dropped."""
    css = ':root { --a: var(--b); --b: var(--a); }'
    blocks = GATE.parse_css_blocks(css)
    resolved = GATE.resolve_css_palette(blocks, "dark", high_contrast=False)
    assert set(resolved) == {"--a", "--b"}, resolved
    assert all(v.startswith("var(") for v in resolved.values()), resolved


def test_theme_blocks_only_apply_to_their_own_theme():
    """`[data-theme="white"]` must not contribute to the dark palette."""
    css = """
    :root { --pulse-bg: #050910; }
    [data-theme="white"] { --pulse-bg: #ffffff; }
    """
    blocks = GATE.parse_css_blocks(css)
    dark = GATE.resolve_css_palette(blocks, "dark", high_contrast=False)
    white = GATE.resolve_css_palette(blocks, "white", high_contrast=False)
    assert dark.get("--pulse-bg") == "#050910", dark
    assert white.get("--pulse-bg") == "#ffffff", white


def test_high_contrast_blocks_do_not_apply_when_contrast_is_off():
    """The overlay is a state, not a permanent part of the cascade."""
    css = """
    :root { --pulse-text: #f4f7fb; }
    [data-hc="1"][data-theme="dark"] { --pulse-text: #ffffff; }
    """
    blocks = GATE.parse_css_blocks(css)
    plain = GATE.resolve_css_palette(blocks, "dark", high_contrast=False)
    contrast = GATE.resolve_css_palette(blocks, "dark", high_contrast=True)
    assert plain.get("--pulse-text") == "#f4f7fb", plain
    assert contrast.get("--pulse-text") == "#ffffff", contrast


def test_an_unrecognised_selector_contributes_nothing():
    """Conservative direction: an unmodelled selector must not act universal.

    If `_selector_matches` defaulted to True, a typo'd or newly-introduced
    selector shape would supply values to every theme at once and the gate would
    pass by over-matching.
    """
    assert not GATE._selector_matches('.card[data-theme="dark"]', "dark", False)
    assert not GATE._selector_matches("[data-theme]", "dark", False)
    assert not GATE._selector_matches('[data-mystery="1"]', "dark", False)
    assert GATE._selector_matches(":root", "dark", False)
    assert GATE._selector_matches('[data-theme="dark"]', "dark", False)


# ---------------------------------------------------------------------------
# The comparison itself
# ---------------------------------------------------------------------------


def _minimal_sides():
    """A native palette set and a matching CSS block list, both tiny.

    Built by hand rather than by calling the gate's own parsers. Using the
    parsers to construct the expected side is the tautology that has already
    bitten this repo once: a parser that stops seeing a value drops it from both
    sides, so the two still agree and the gate reports parity.
    """
    natives = {
        "DARK": {"accent": "#32e6b3"},
        "BLACK": {"accent": "#32e6b3"},
        "LIGHT_FUTURISTIC": {"accent": "#00966f"},
        "WHITE": {"accent": "#00966f"},
        "HIGH_CONTRAST_DARK": {"accent": "#4dffc8"},
        "HIGH_CONTRAST_LIGHT": {"accent": "#00614a"},
    }
    blocks = [
        ([":root"], {"--pulse-accent": "#32e6b3"}),
        (['[data-theme="light_futuristic"]'], {"--pulse-accent": "#00966f"}),
        (['[data-theme="white"]'], {"--pulse-accent": "#00966f"}),
        (
            ['[data-hc="1"][data-theme="dark"]', '[data-hc="1"][data-theme="black"]'],
            {"--pulse-accent": "#4dffc8"},
        ),
        (
            [
                '[data-hc="1"][data-theme="light_futuristic"]',
                '[data-hc="1"][data-theme="white"]',
            ],
            {"--pulse-accent": "#00614a"},
        ),
    ]
    metrics = {key.replace("--native-", "").replace("-", "_"): 1 for key in []}
    return natives, blocks, metrics


def test_a_matching_pair_reports_no_problems():
    """The positive control. Without it, a gate that always reports a problem
    would pass every negative test in this file."""
    natives, blocks, _ = _minimal_sides()
    problems = GATE.compare(natives, blocks, {}, "dark", "dark")
    colour_problems = [p for p in problems if "--pulse-accent" in p]
    assert colour_problems == [], colour_problems


def test_a_changed_native_colour_is_reported():
    """The whole point: native moves, web does not, gate goes red."""
    natives, blocks, _ = _minimal_sides()
    natives["DARK"] = {"accent": "#00ff00"}
    problems = GATE.compare(natives, blocks, {}, "dark", "dark")
    assert any("--pulse-accent" in p and "dark:" in p for p in problems), problems


def test_a_colour_missing_from_the_css_is_reported():
    """A key native has and the CSS does not must be named, not skipped."""
    natives, blocks, _ = _minimal_sides()
    natives["DARK"] = {"accent": "#32e6b3", "surface": "#0b141c"}
    problems = GATE.compare(natives, blocks, {}, "dark", "dark")
    assert any("--pulse-surface" in p for p in problems), problems


def test_a_native_key_with_no_mapping_is_reported_not_ignored():
    """A newly added native colour must surface as work to do.

    Ignoring unmapped keys is the failure that looks like success: the app grows
    a token, the web never gets it, and the gate stays green because it only
    ever checks what it already knows about.
    """
    natives, blocks, _ = _minimal_sides()
    natives["DARK"] = {"accent": "#32e6b3", "brandNewToken": "#123456"}
    problems = GATE.compare(natives, blocks, {}, "dark", "dark")
    assert any("brandNewToken" in p for p in problems), problems


def test_high_contrast_must_not_disturb_unrelated_keys():
    """The overlay is partial. A full-palette overlay must be caught.

    Native applies `{ ...base, ...contrast }`, so a key the overlay does not
    name keeps the base theme's value. An implementation that wrote the dark
    contrast values as a complete palette would put dark surfaces under the
    LIGHT themes -- and a gate checking only the overlay's own keys would call
    that correct.
    """
    natives, blocks, _ = _minimal_sides()
    for name in ("DARK", "BLACK", "LIGHT_FUTURISTIC", "WHITE"):
        natives[name] = dict(natives[name], surface="#aaaaaa")
    blocks = list(blocks) + [([":root"], {"--pulse-surface": "#aaaaaa"})]
    # The overlay names only `accent`, so `surface` must survive from the base.
    clean = GATE.compare(natives, blocks, {}, "dark", "dark")
    assert not any("--pulse-surface" in p for p in clean), clean

    # Now make the CSS overlay wrongly restate surface, and it must be caught.
    broken = list(blocks) + [
        (
            ['[data-hc="1"][data-theme="light_futuristic"]'],
            {"--pulse-surface": "#000000"},
        )
    ]
    problems = GATE.compare(natives, blocks=broken, metrics={}, native_pin="dark", web_pin="dark")
    assert any(
        "--pulse-surface" in p and "light_futuristic + high-contrast" in p
        for p in problems
    ), problems


def test_a_metric_mismatch_is_reported():
    """The off-grid values are exactly the ones a tidy-up would round."""
    natives, blocks, _ = _minimal_sides()
    blocks = list(blocks) + [([":root"], {"--native-radius": "16px"})]
    problems = GATE.compare(natives, blocks, {"radius": 14}, "dark", "dark")
    assert any("--native-radius" in p and "14" in p for p in problems), problems


def test_the_appearance_pin_divergence_is_reported():
    """If native activates theme selection, web must not stay silently pinned."""
    natives, blocks, _ = _minimal_sides()
    problems = GATE.compare(natives, blocks, {}, "system", "dark")
    assert any("ACTIVE_THEME" in p for p in problems), problems


# ---------------------------------------------------------------------------
# End to end, against the real tree
# ---------------------------------------------------------------------------


def test_the_gate_passes_on_the_committed_tree():
    """The tree is healthy right now.

    Worth almost nothing on its own -- it is the claim every test above exists
    to make meaningful -- but it does catch the case where the two files drift
    and nobody ran the gate.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = GATE.main([])
    assert code == GATE.EXIT_OK, out.getvalue() + err.getvalue()


def test_the_real_run_actually_compares_something():
    """A pass that compared zero palettes is indistinguishable from a real one.

    So the success message has to state its own scope, and this asserts the
    numbers in it are non-trivial. Without this, deleting every entry from
    `PALETTE_TO_CSS` would still exit 0.
    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        GATE.main([])
    message = out.getvalue()
    assert "verified 8 resolved palettes" in message, message
    assert "23 colours each" in message, message
    assert "8 metrics" in message, message


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
