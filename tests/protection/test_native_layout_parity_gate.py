"""Locks for the gate that says the web shell still has the native app's shape.

Why this gate needs its own tests
---------------------------------
Five numbers -- the shell's max width and padding, the column gap, and the two
rail widths -- exist once in `mobile-native/src/screens/HomeScreen.tsx` and
again in `web/src/styles/tokens.css`. A sixth, the feed column's width, is
derived from those five. Together they decide the proportions of every desktop
page, and they can drift without anything failing: the app widens a rail, the
website does not, and the two products quietly become different shapes.

The gate is the only thing watching, which makes the gate the same risk one
level up. It reads native with regexes against a 3,000-line screen file, and
every way those regexes can fail -- a renamed style key, a reformatted
StyleSheet, a value that became a computed expression -- yields *no* values
rather than wrong ones. A comparison over no values passes. So a green run
against the real repo is evidence about the repo, not about the gate.

These tests feed the gate's own functions synthetic sources with known
divergences and assert it goes red for each, and assert could-not-check for
each way the reading can fail. The real files are never mutated.

The derived value gets its own attention. `--feed-max` is not compared to
anything in native -- there is nothing to compare it to, because native's feed
is `flex: 1` and has no declared width. It is recomputed from the CSS's own
numbers, so the test that matters is that bumping a rail without re-deriving
the feed is caught.

How far that claim was actually verified
----------------------------------------
The same way as the two gates before it: 21 plausible regressions were injected
into a throwaway copy of the gate -- never the real tree, which was hashed
either side of every run -- and each had to turn *its named test* red, with
three behaviour-preserving controls required to stay silent. Final score 45/45.
See `test_native_background_parity_gate.py` for why this matters: asserting a
suite would catch something is the same unfalsifiable claim the gate makes.

What it changed here:

  * `test_the_native_threshold_is_read_rather_than_assumed` asserted the gate
    reported *no* problems at all on its synthetic input, so it failed for an
    unrelated change to the feed derivation -- a property it has no business
    holding. It now asserts only that no breakpoint problem is raised.
  * A "do the three columns fit inside the shell" check was removed from the
    gate rather than tested. Substituting the derivation reduces it to
    `2*pad + cmd + gap >= 0`, so it could never be the only thing to go red.
    `test_the_three_columns_always_fit_once_the_derivation_holds` records that
    reasoning so it is not re-added on a hunch.
  * Two of the injected regressions were wrecking balls that broke every read
    at once and turned half the suite red, which says nothing about which test
    holds what. Both were narrowed until they attacked one property each.

Three mutations are deliberate equivalents: widening the media-query reader to
match `max-width` (the stylesheet has none for it to pick up), reporting a
short comparison as verified (unreachable while every earlier guard raises),
and reporting a divergence as success (needs an already-divergent tree).

Zero-arg tests, no fixtures: this directory runs files as scripts.
"""

import contextlib
import importlib.util
import io
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
GATE_PATH = REPO / "scripts" / "ops" / "native_layout_parity_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("_layout_parity_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _load_gate()


# A minimal stand-in for the native screen. Indented two spaces and closed at
# two spaces, because that is the shape `StyleSheet.create` produces and the
# shape the block regex anchors on.
NATIVE = """
  const wideCanvas = width >= 900;

  content: {
    alignSelf: "center",
    maxWidth: 1480,
    padding: 12,
    width: "100%"
  },
  homeCanvasWide: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 16
  },
  commandRail: {
    gap: 9,
    width: 226
  },
  sideRail: {
    gap: 11,
    width: 314
  },
"""

CSS = """
:root {
  --shell-max: 1480px;
  --shell-pad: 12px;
  --rail-gap: 16px;
  --rail-command: 226px;
  --rail-side: 314px;
  --feed-max: 884px;
}
"""

LAYOUT = """
@media (min-width: 900px) { .pulse-shell { gap: 0; } }
@media (min-width: 1480px) { .pulse-shell { gap: 0; } }
"""


def _problems(native=NATIVE, css=CSS, layout=LAYOUT):
    return GATE.compare(native, css, layout)[0]


# --- The healthy case ----------------------------------------------------


def test_a_matching_shell_reports_no_problems():
    """The negative control. Without it every test below could pass vacuously."""
    problems, checked = GATE.compare(NATIVE, CSS, LAYOUT)
    assert problems == [], problems
    assert checked == len(GATE.COPIED) + 3, checked


# --- The copied values ---------------------------------------------------


def test_a_widened_rail_in_native_is_reported():
    native = NATIVE.replace("width: 226", "width: 240")
    problems = _problems(native=native)
    assert any("--rail-command" in p for p in problems), problems


def test_a_widened_rail_in_css_is_reported():
    css = CSS.replace("--rail-command: 226px;", "--rail-command: 240px;")
    problems = _problems(css=css)
    assert any("--rail-command" in p for p in problems), problems


def test_a_changed_shell_width_is_reported():
    css = CSS.replace("--shell-max: 1480px;", "--shell-max: 1600px;")
    problems = _problems(css=css)
    assert any("--shell-max" in p for p in problems), problems


def test_a_changed_gap_is_reported():
    native = NATIVE.replace("gap: 16", "gap: 24")
    problems = _problems(native=native)
    assert any("--rail-gap" in p for p in problems), problems


def test_a_changed_padding_is_reported():
    native = NATIVE.replace("padding: 12", "padding: 20")
    problems = _problems(native=native)
    assert any("--shell-pad" in p for p in problems), problems


def test_the_side_rail_width_is_compared_too():
    """Every row of COPIED must actually be exercised, not just the first."""
    native = NATIVE.replace("width: 314", "width: 360")
    problems = _problems(native=native)
    assert any("--rail-side" in p for p in problems), problems


def test_each_native_value_is_read_from_its_own_style_block():
    """`width: 226` appears in a file full of widths.

    A reader that searched the whole source would find whichever width came
    first and call it agreement. Here `commandRail` and `sideRail` both declare
    `width`, and swapping them must be caught.
    """
    native = NATIVE.replace("width: 226", "width: 314", 1).replace(
        "    gap: 11,\n    width: 314", "    gap: 11,\n    width: 226"
    )
    problems = _problems(native=native)
    assert any("--rail-command" in p for p in problems), problems
    assert any("--rail-side" in p for p in problems), problems


# --- The derived value ---------------------------------------------------


def test_a_rail_bumped_without_rederiving_the_feed_is_reported():
    """The whole point of deriving rather than copying `--feed-max`."""
    native = NATIVE.replace("width: 226", "width: 250")
    css = CSS.replace("--rail-command: 226px;", "--rail-command: 250px;")
    problems = _problems(native=native, css=css)
    assert any("--feed-max" in p for p in problems), problems


def test_the_plans_guessed_feed_width_would_be_reported():
    """680 was the phase plan's figure and is not what the shell leaves.

    Pinned as a test because it is the specific wrong number most likely to be
    reintroduced by someone reading the old plan instead of the app.
    """
    css = CSS.replace("--feed-max: 884px;", "--feed-max: 680px;")
    problems = _problems(css=css)
    assert any("--feed-max" in p for p in problems), problems


def test_the_derivation_is_arithmetic_and_not_a_hardcoded_884():
    """Fed a different shell, the gate must expect a different feed."""
    values = {
        "--shell-max": 1000,
        "--shell-pad": 10,
        "--rail-gap": 20,
        "--rail-command": 200,
        "--rail-side": 300,
    }
    assert GATE.derive_feed_max(values) == 1000 - 20 - 200 - 300 - 40


def test_the_three_columns_always_fit_once_the_derivation_holds():
    """Why there is no separate "do the columns fit" check in the gate.

    Writing one was the first instinct and it was wrong: substituting the
    derivation into `feed + side + gap <= shell` reduces it to
    `2*pad + cmd + gap >= 0`, true for any non-negative geometry. It could only
    fire alongside the derivation failure that caused it, so it was removed
    rather than left in looking like a second guarantee. This test pins the
    reasoning so it is not re-added on a hunch.
    """
    for shell, pad, gap, cmd, side in [
        (1480, 12, 16, 226, 314),
        (1000, 10, 20, 200, 300),
        (800, 0, 0, 100, 100),
    ]:
        feed = GATE.derive_feed_max(
            {
                "--shell-max": shell,
                "--shell-pad": pad,
                "--rail-gap": gap,
                "--rail-command": cmd,
                "--rail-side": side,
            }
        )
        assert feed + side + gap <= shell, (shell, feed)


# --- The breakpoints -----------------------------------------------------


def test_the_shell_must_break_where_native_goes_wide():
    layout = LAYOUT.replace("min-width: 900px", "min-width: 1024px")
    problems = _problems(layout=layout)
    assert any("900" in p for p in problems), problems


def test_the_native_threshold_is_read_rather_than_assumed():
    """Fed a different threshold, the gate must demand that one instead.

    Asserting against the real file would be satisfied by a gate that hardcoded
    900, which is the failure a source-taking comparison exists to make
    observable.
    """
    native = NATIVE.replace("width >= 900", "width >= 1100")

    # Deliberately not `== []`. Asserting the whole gate is quiet here would
    # make this test fail for any unrelated divergence anywhere in the file --
    # mutation testing caught it doing exactly that on a change to the feed
    # derivation, which is a property this test has no business holding.
    layout = LAYOUT.replace("min-width: 900px", "min-width: 1100px")
    matched = _problems(native=native, layout=layout)
    assert not any("1100" in p for p in matched), matched

    problems = _problems(native=native)
    assert any("1100" in p for p in problems), problems


def test_the_side_rail_must_arrive_where_the_shell_caps():
    layout = LAYOUT.replace("min-width: 1480px", "min-width: 1500px")
    problems = _problems(layout=layout)
    assert any("1480" in p for p in problems), problems


# --- Could-not-check, not a pass -----------------------------------------


def test_a_renamed_native_style_block_is_could_not_check():
    native = NATIVE.replace("  commandRail: {", "  commandRailV2: {")
    try:
        GATE.compare(native, CSS, LAYOUT)
    except GATE.CouldNotCheck:
        return
    raise AssertionError("a renamed style block must raise CouldNotCheck")


def test_a_computed_native_value_is_could_not_check():
    """`width: railWidth` is unreadable, and unreadable is not agreement."""
    native = NATIVE.replace("width: 226", "width: railWidth")
    try:
        GATE.compare(native, CSS, LAYOUT)
    except GATE.CouldNotCheck:
        return
    raise AssertionError("a non-numeric native value must raise CouldNotCheck")


def test_a_missing_css_token_is_could_not_check_not_equal():
    css = CSS.replace("  --rail-side: 314px;\n", "")
    try:
        GATE.compare(NATIVE, css, LAYOUT)
    except GATE.CouldNotCheck:
        return
    raise AssertionError("a missing token must raise CouldNotCheck")


def test_a_token_declared_twice_is_could_not_check():
    """The later declaration wins at runtime; the gate must not pick one.

    This is the shape a merge produces, and the one where reading the first
    declaration would have the gate verifying a value the browser never uses.
    """
    css = CSS.replace("--rail-side: 314px;", "--rail-side: 314px;\n  --rail-side: 400px;")
    try:
        GATE.compare(NATIVE, css, LAYOUT)
    except GATE.CouldNotCheck:
        return
    raise AssertionError("a duplicated token must raise CouldNotCheck")


def test_a_token_written_as_a_calc_is_could_not_check():
    css = CSS.replace("--feed-max: 884px;", "--feed-max: calc(var(--shell-max) - 596px);")
    try:
        GATE.compare(NATIVE, css, LAYOUT)
    except GATE.CouldNotCheck:
        return
    raise AssertionError("a token the gate cannot evaluate must raise CouldNotCheck")


def test_a_removed_wide_canvas_rule_is_could_not_check():
    native = NATIVE.replace("const wideCanvas = width >= 900;", "const wideCanvas = useWide();")
    try:
        GATE.compare(native, CSS, LAYOUT)
    except GATE.CouldNotCheck:
        return
    raise AssertionError("a missing wideCanvas rule must raise CouldNotCheck")


def test_a_stylesheet_with_no_breakpoints_is_could_not_check():
    """An empty breakpoint set compares equal to nothing and would pass."""
    try:
        GATE.compare(NATIVE, CSS, "/* no media queries */")
    except GATE.CouldNotCheck:
        return
    raise AssertionError("a stylesheet with no breakpoints must raise CouldNotCheck")


def test_an_unreadable_side_exits_could_not_check_not_ok():
    original = GATE.WEB_TOKENS
    GATE.WEB_TOKENS = REPO / "web" / "src" / "styles" / "does_not_exist.css"
    try:
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = GATE.main()
        assert code == GATE.EXIT_NO_DATA, code
        assert "COULD NOT CHECK" in buffer.getvalue()
    finally:
        GATE.WEB_TOKENS = original
    assert GATE.WEB_TOKENS.exists()


def test_the_exit_codes_stay_distinct():
    """Collapsing could-not-check onto ok is the failure mode that matters."""
    codes = {GATE.EXIT_OK, GATE.EXIT_DIVERGED, GATE.EXIT_NO_DATA}
    assert len(codes) == 3, codes
    assert GATE.EXIT_OK == 0


# --- The live tree -------------------------------------------------------


def test_the_gate_passes_on_the_committed_tree():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = GATE.main()
    assert code == GATE.EXIT_OK, f"exit {code}\n{buffer.getvalue()}"


def test_the_real_run_actually_compares_something():
    """A gate that verified zero values would also report no problems."""
    problems, checked = GATE.compare(
        GATE.NATIVE_HOME.read_text(encoding="utf-8"),
        GATE.WEB_TOKENS.read_text(encoding="utf-8"),
        GATE.WEB_LAYOUT.read_text(encoding="utf-8"),
    )
    assert problems == [], problems
    assert checked == len(GATE.COPIED) + 3, checked


def test_the_live_feed_width_is_the_one_the_shell_leaves():
    """Pins the actual number, so a silent re-derivation is still visible."""
    css = GATE.WEB_TOKENS.read_text(encoding="utf-8")
    assert GATE.css_custom_property(css, "--feed-max") == 884


def test_the_stylesheet_only_breaks_at_widths_the_gate_knows_about():
    """An unexplained third breakpoint is a layout decision nobody recorded."""
    layout = GATE.WEB_LAYOUT.read_text(encoding="utf-8")
    css = GATE.WEB_TOKENS.read_text(encoding="utf-8")
    native = GATE.NATIVE_HOME.read_text(encoding="utf-8")

    threshold = GATE._as_number(GATE.WIDE_CANVAS_RULE.search(native).group(1))
    expected = {threshold, GATE.css_custom_property(css, "--shell-max")}
    assert GATE.media_query_widths(layout) == expected


def test_the_rails_are_removed_and_not_merely_hidden_below_their_breakpoint():
    """A rail that still occupies a track is still tab-stopped and read aloud."""
    layout = GATE.WEB_LAYOUT.read_text(encoding="utf-8")
    assert ".pulse-shell__rail {\n  display: none;\n}" in layout


def test_the_feed_track_can_shrink_below_its_content():
    """Native sets `minWidth: 0`; a grid track defaults to its content instead.

    Without it a long unbroken URL widens the feed past the shell, which is a
    horizontally scrolling page on the one layout that must not have one.
    """
    layout = GATE.WEB_LAYOUT.read_text(encoding="utf-8")
    assert "minmax(0, 1fr)" in layout
    assert "grid-template-columns: 1fr;" not in layout


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
