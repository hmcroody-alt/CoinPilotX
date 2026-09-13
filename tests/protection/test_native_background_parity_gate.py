"""Locks for the gate that says the web backdrop still matches the native one.

Why this gate needs its own tests
---------------------------------
`web/src/theme/pulseBackground.ts` is a hand-maintained copy of native's table:
fourteen node coordinates, seven lines, two gradient surfaces, four variants
and a set of opacity ceilings. The gate is the only thing between "a node moved
three percent in the app" and "the website is quietly a different composition".

Which makes the gate the same risk one level up, and in the same direction.
Both sides are TypeScript here, so the gate reads them with one parser -- and a
parser that stops matching returns an empty table from *both* files, which
compares equal. Every failure mode of the reader therefore makes the gate
greener, and a green run against the real repo proves only that the tree is
healthy today.

So these tests feed the gate's own functions synthetic sources with known
divergences and assert it goes red for each. The real repo is never mutated.

A second property matters as much as detection: the gate must be quiet about
things that are not divergences. `0.2` and `0.20`, `1` and `1.0`, and a colour
written as `PULSE_BACKGROUND_COLORS.base` on one side and `"#101A4A"` on the
other are all the same value. A gate that reported those would be red on a
healthy tree from its first run, and a gate that is red on a healthy tree gets
switched off -- at which point it detects nothing at all, which is a worse
outcome than never having written it.

How far that claim was actually verified
----------------------------------------
"These tests would catch a regression" is the same unfalsifiable claim the gate
itself makes, so it was measured. Twenty plausible regressions were injected
into a throwaway copy of the gate -- never the real tree; the caller hashed
`git status` and `git diff` either side of every run -- and each had to turn
*its named test* red, not merely some test. Three behaviour-preserving edits
ran alongside them and had to produce zero failures, because a harness that
reports "everything fails" looks like perfect coverage. Final score 43/43.

Two real defects came out of it, both invisible to a passing suite:

  * `_describe` routed booleans through the numeric branch, because `bool`
    subclasses `int` in Python. `pulse: true` and `enabled: false` -- the flags
    deciding whether a node breathes and whether White gets a backdrop at all
    -- were being compared with a 1e-9 float tolerance, and the branch that
    claims to check non-numeric leaves never saw them. Verdicts happened to
    come out right, so only the harness could see it: killing the numeric
    comparison turned the two boolean tests red and killing the equality
    comparison did not. `_is_number` now excludes bool.
  * The empty-profile-table guard had no test. The one that looked like its
    test deletes `galacticProfileFor` outright, which raises in the body reader
    long before the guard is reached, so removing the guard left every test
    green. `test_a_profile_function_whose_arms_do_not_parse_is_could_not_check`
    covers the shape that actually reaches it: a function that still exists and
    still answers the question, rewritten so the arm regex no longer matches.

Three mutations are deliberate equivalents rather than misses. Reverting
`_is_number` changes no verdict (Python's `True == 1` sees to that) and is
observable only as the OVER/MISS pair it reproduces in the harness. The other
two -- ignoring a key present on one side only, and reporting a divergence as
success -- need a tree that is already divergent before they are reachable.

Zero-arg tests, no fixtures: this directory runs files as scripts.
"""

import contextlib
import importlib.util
import io
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
GATE_PATH = REPO / "scripts" / "ops" / "native_background_parity_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("_background_parity_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _load_gate()


def _sides():
    """The three real sources, comment-stripped, as the gate reads them."""
    return (
        GATE._strip_comments(GATE.NATIVE_TOKENS.read_text(encoding="utf-8")),
        GATE._strip_comments(GATE.WEB_TOKENS.read_text(encoding="utf-8")),
        GATE._strip_comments(GATE.NATIVE_CONTEXT.read_text(encoding="utf-8")),
    )


def _compare_with_web(web_source):
    native, _, context = _sides()
    return GATE.compare(native, web_source, context)[0]


# --- The reader ----------------------------------------------------------


def test_comments_are_stripped_without_eating_string_contents():
    """A regex that deletes `//` to end-of-line also deletes half of a URL."""
    source = '''
    const a = "https://pulsesoc.com/app"; // trailing comment
    /* block */
    const b = 'keep // this';
    '''
    stripped = GATE._strip_comments(source)
    assert "https://pulsesoc.com/app" in stripped
    assert "keep // this" in stripped
    assert "trailing comment" not in stripped
    assert "block" not in stripped


def test_a_table_reads_back_as_structured_values():
    source = 'export const T = { a: 1, b: "x", c: false, d: [1, 2], e: { f: 0.5 } };'
    assert GATE.read_table(source, "T") == {
        "a": 1,
        "b": "x",
        "c": False,
        "d": [1, 2],
        "e": {"f": 0.5},
    }


def test_an_array_of_objects_reads_back_in_order():
    source = "export const T = [{ x: 1 }, { x: 2 }, { x: 3 }];"
    assert [entry["x"] for entry in GATE.read_table(source, "T")] == [1, 2, 3]


def test_a_missing_table_is_could_not_check_not_empty():
    """An absent declaration must not reach the comparison as `{}`.

    Two empty tables compare equal, so returning a default here would make a
    typo in a table name read as "no problems found" -- the exact shape of
    silent pass this gate is built to prevent.
    """
    try:
        GATE.read_table("export const OTHER = { a: 1 };", "MISSING")
    except GATE.CouldNotCheck:
        return
    raise AssertionError("a missing table must raise CouldNotCheck")


def test_an_empty_table_is_could_not_check():
    try:
        GATE.read_table("export const T = {};", "T")
    except GATE.CouldNotCheck:
        return
    raise AssertionError("an empty table must raise CouldNotCheck")


def test_a_non_literal_declaration_is_could_not_check():
    try:
        GATE.read_table("export const T = buildTable();", "T")
    except GATE.CouldNotCheck:
        return
    raise AssertionError("a computed declaration must raise CouldNotCheck")


def test_colour_references_resolve_to_the_literal():
    colours = {"base": "#101A4A"}
    resolved = GATE.resolve_refs(
        {"g": [GATE._Ref("PULSE_BACKGROUND_COLORS.base")]}, colours
    )
    assert resolved == {"g": ["#101A4A"]}


def test_a_reference_and_its_literal_are_not_a_divergence():
    """Native writes `COLORS.base`; the web copy may write either spelling.

    Reporting that pair would be a false divergence on a healthy tree, which is
    how a gate earns the reputation that gets it disabled.
    """
    native, web, context = _sides()
    inlined = web.replace(
        "PULSE_BACKGROUND_COLORS.base,\n        PULSE_BACKGROUND_COLORS.navy",
        '"#101A4A",\n        "#13235C"',
        1,
    )
    assert inlined != web, "the substitution did not apply; the anchor moved"
    assert GATE.compare(native, inlined, context)[0] == []


# --- Detection -----------------------------------------------------------


def test_the_gate_passes_on_the_committed_tree():
    native, web, context = _sides()
    problems, compared = GATE.compare(native, web, context)
    assert problems == [], problems
    assert compared > 0, "a pass over zero values is not a pass"


def test_a_moved_node_is_reported():
    _, web, _ = _sides()
    moved = web.replace("{ x: 12, y: 14, size: 3", "{ x: 15, y: 14, size: 3", 1)
    assert moved != web, "the substitution did not apply; the anchor moved"
    problems = _compare_with_web(moved)
    assert any("PULSE_BACKGROUND_NODES[0].x" in problem for problem in problems), problems


def test_a_raised_opacity_ceiling_is_reported():
    """The ceilings are the reason body text stays readable over the field."""
    _, web, _ = _sides()
    raised = web.replace("node: 0.2,", "node: 0.5,", 1)
    assert raised != web, "the substitution did not apply; the anchor moved"
    problems = _compare_with_web(raised)
    assert any("CEILINGS.node" in problem for problem in problems), problems


def test_whites_disabled_backdrop_cannot_be_quietly_enabled():
    """White renders no backdrop at all. That is a decision, not a default."""
    _, web, _ = _sides()
    enabled = web.replace(
        'if (mode === "white") return { enabled: false',
        'if (mode === "white") return { enabled: true',
        1,
    )
    assert enabled != web, "the substitution did not apply; the anchor moved"
    problems = _compare_with_web(enabled)
    assert any("galacticProfileFor.white" in problem for problem in problems), problems


def test_a_changed_intensity_is_reported():
    """Black's 0.55 is how it gets the same composition at half strength."""
    _, web, _ = _sides()
    changed = web.replace("intensity: 0.55", "intensity: 0.85", 1)
    assert changed != web, "the substitution did not apply; the anchor moved"
    problems = _compare_with_web(changed)
    assert any("galacticProfileFor.black.intensity" in problem for problem in problems), problems


def test_a_gradient_stop_change_is_reported():
    _, web, _ = _sides()
    moved = web.replace(
        "locations: [0, 0.22, 0.46, 0.62, 0.84, 1],",
        "locations: [0, 0.25, 0.46, 0.62, 0.84, 1],",
        1,
    )
    assert moved != web, "the substitution did not apply; the anchor moved"
    problems = _compare_with_web(moved)
    assert any("gradient.locations[1]" in problem for problem in problems), problems


def test_a_changed_gradient_colour_is_reported():
    _, web, _ = _sides()
    changed = web.replace('"#F6F7FC"', '"#FFFFFF"', 1)
    assert changed != web, "the substitution did not apply; the anchor moved"
    problems = _compare_with_web(changed)
    assert any("light.gradient.colors[0]" in problem for problem in problems), problems


def test_a_dropped_node_is_reported_as_a_length_difference():
    """Losing a node must be louder than the field simply looking sparser."""
    _, web, _ = _sides()
    dropped = web.replace(
        '  { x: 27, y: 9, size: 2, opacity: 0.13, tone: "lavender", pulse: true, tier: "full" },\n',
        "",
        1,
    )
    assert dropped != web, "the substitution did not apply; the anchor moved"
    problems = _compare_with_web(dropped)
    assert any("PULSE_BACKGROUND_NODES:" in problem for problem in problems), problems


def test_a_changed_cycle_time_is_reported():
    _, web, _ = _sides()
    faster = web.replace("drift: 30000,", "drift: 12000,", 1)
    assert faster != web, "the substitution did not apply; the anchor moved"
    problems = _compare_with_web(faster)
    assert any("CYCLES.drift" in problem for problem in problems), problems


def test_a_changed_variant_scale_is_reported():
    _, web, _ = _sides()
    changed = web.replace("opacityScale: 0.7, cycleScale: 1.15", "opacityScale: 0.9, cycleScale: 1.15", 1)
    assert changed != web, "the substitution did not apply; the anchor moved"
    problems = _compare_with_web(changed)
    assert any("VARIANTS.quiet.opacityScale" in problem for problem in problems), problems


def test_a_flipped_pulse_flag_is_reported():
    """Which nodes breathe is part of the composition, not a rendering detail."""
    _, web, _ = _sides()
    flipped = web.replace(
        '{ x: 27, y: 9, size: 2, opacity: 0.13, tone: "lavender", pulse: true',
        '{ x: 27, y: 9, size: 2, opacity: 0.13, tone: "lavender", pulse: false',
        1,
    )
    assert flipped != web, "the substitution did not apply; the anchor moved"
    problems = _compare_with_web(flipped)
    assert any("[1].pulse" in problem for problem in problems), problems


def test_a_changed_tier_is_reported():
    """Tier decides what the quiet variant drops, so it changes composition."""
    _, web, _ = _sides()
    changed = web.replace(
        '{ x: 27, y: 9, size: 2, opacity: 0.13, tone: "lavender", pulse: true, tier: "full" }',
        '{ x: 27, y: 9, size: 2, opacity: 0.13, tone: "lavender", pulse: true, tier: "core" }',
        1,
    )
    assert changed != web, "the substitution did not apply; the anchor moved"
    problems = _compare_with_web(changed)
    assert any("[1].tier" in problem for problem in problems), problems


# --- Quiet where it should be quiet --------------------------------------


def test_reformatting_a_number_is_not_a_divergence():
    """`0.2` and `0.20` are one ceiling. Text comparison would call them two."""
    _, web, _ = _sides()
    reformatted = web.replace("node: 0.2,", "node: 0.20,", 1).replace(
        "opacityScale: 1,", "opacityScale: 1.0,", 1
    )
    assert reformatted != web, "the substitution did not apply; the anchor moved"
    assert _compare_with_web(reformatted) == []


def test_a_reordered_object_key_is_not_a_divergence():
    """Key order carries no meaning in either language."""
    _, web, _ = _sides()
    reordered = web.replace(
        "  node: 0.2,\n  line: 0.12,",
        "  line: 0.12,\n  node: 0.2,",
        1,
    )
    assert reordered != web, "the substitution did not apply; the anchor moved"
    assert _compare_with_web(reordered) == []


# --- The profile table ---------------------------------------------------


def test_the_profile_table_is_read_rather_than_assumed():
    """Fed a table with different values, the reader must return those values.

    Asserting only against the real file would be satisfied by a function that
    returns native's table as a constant, which is the failure this split into
    a source-taking function exists to make observable.
    """
    source = """
    function galacticProfileFor(mode, scheme) {
      if (mode === "white") return { enabled: true, intensity: 0.9, variant: "dark" };
      if (mode === "black") return { enabled: false, intensity: 0.1, variant: "light" };
      return scheme === "light"
        ? { enabled: true, intensity: 0.2, variant: "light" }
        : { enabled: true, intensity: 0.3, variant: "dark" };
    }
    """
    table = GATE.read_profiles(source)
    assert table["white"] == {"enabled": True, "intensity": 0.9, "variant": "dark"}
    assert table["black"] == {"enabled": False, "intensity": 0.1, "variant": "light"}
    assert table["system:light"]["intensity"] == 0.2
    assert table["system:dark"]["intensity"] == 0.3


def test_a_removed_profile_table_is_could_not_check():
    try:
        GATE.read_profiles("function somethingElse() { return 1; }")
    except GATE.CouldNotCheck:
        return
    raise AssertionError("a missing profile table must raise CouldNotCheck")


def test_a_profile_function_whose_arms_do_not_parse_is_could_not_check():
    """Present but unreadable is the dangerous case, not absent.

    Deleting `galacticProfileFor` raises in the body reader long before the
    emptiness check, so the test above never reaches that guard -- mutation
    testing found it by removing the guard and watching every test stay green.
    The shape that actually reaches it is a function that still exists and still
    answers the same question, rewritten as something the arm regex does not
    recognise. Then the table parses as empty, an empty table compares equal to
    an empty table, and the gate reports a clean pass over nothing at all.
    """
    source = """
    function galacticProfileFor(mode, scheme) {
      const table = { white: { enabled: false }, dark: { enabled: true } };
      return table[mode] ?? table.dark;
    }
    """
    try:
        GATE.read_profiles(source)
    except GATE.CouldNotCheck:
        return
    raise AssertionError(
        "a profile function that parses as an empty table must raise "
        "CouldNotCheck rather than compare nothing to nothing"
    )


def test_the_profile_reader_stops_at_the_end_of_its_own_function():
    """A later function's `return {...}` must not be read as another arm."""
    source = """
    function galacticProfileFor(mode, scheme) {
      if (mode === "white") return { enabled: false, intensity: 0, variant: "light" };
      return scheme === "light"
        ? { enabled: true, intensity: 0.35, variant: "light" }
        : { enabled: true, intensity: 1, variant: "dark" };
    }

    function somethingElse(mode) {
      if (mode === "white") return { enabled: true, intensity: 99, variant: "dark" };
    }
    """
    table = GATE.read_profiles(source)
    assert table["white"]["intensity"] == 0, table["white"]


# --- The live tree -------------------------------------------------------


def test_the_live_tables_are_the_expected_size():
    """Pins the composition's shape so a silent truncation is visible.

    The gate itself only says the two sides agree; two identically truncated
    tables agree perfectly. This is the assertion that says what they should
    contain.
    """
    native, _, _ = _sides()
    assert len(GATE.read_table(native, "PULSE_BACKGROUND_NODES")) == 14
    assert len(GATE.read_table(native, "PULSE_BACKGROUND_LINES")) == 7
    surfaces = GATE.read_table(native, "PULSE_BACKGROUND_SURFACES")
    assert len(surfaces["dark"]["gradient"]["colors"]) == 6
    assert len(surfaces["dark"]["gradient"]["locations"]) == 6
    assert len(GATE.read_table(native, "PULSE_BACKGROUND_VARIANTS")) == 4


def test_the_live_ceilings_are_the_reviewed_values():
    """These are the numbers that keep the backdrop behind the content."""
    native, _, _ = _sides()
    ceilings = GATE.read_table(native, "PULSE_BACKGROUND_CEILINGS")
    assert ceilings["node"] == 0.2
    assert ceilings["line"] == 0.12
    assert ceilings["halo"] == 0.3


def test_the_web_helpers_still_clamp_to_the_ceilings():
    """Data parity says nothing about whether the clamp is applied.

    The gate compares tables, so a web copy whose `nodeOpacity` had become a
    passthrough would still be perfectly in parity while painting nodes over
    the ceiling. The clamp is the reason the ceilings are functions rather than
    constants, so it is asserted directly.
    """
    source = GATE.WEB_TOKENS.read_text(encoding="utf-8")
    assert "Math.min(PULSE_BACKGROUND_CEILINGS.node" in source
    assert "Math.min(PULSE_BACKGROUND_CEILINGS.line" in source
    assert "PULSE_BACKGROUND_CEILINGS.halo" in source


def test_the_real_run_actually_compares_something():
    """Guards the count in the success line against becoming decorative."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = GATE.main()
    message = buffer.getvalue()
    assert code == GATE.EXIT_OK, message
    assert "verified" in message
    # A specific floor, not `> 0`: the tables hold hundreds of values, so a
    # reader that found only a handful of them would still be "more than zero".
    compared = int(message.split("verified ")[1].split()[0])
    assert compared >= 200, message


def test_an_unreadable_side_exits_could_not_check_not_ok():
    """The end-to-end path, not just the constant.

    `EXIT_NO_DATA == 3` being defined proves nothing about whether `main`
    returns it. Point the gate at a file that is not there and it must report
    could-not-check -- a missing web copy is the most likely way this gate ever
    loses a side, and the one case where passing would be worst.
    """
    original = GATE.WEB_TOKENS
    GATE.WEB_TOKENS = REPO / "web" / "src" / "theme" / "does_not_exist.ts"
    try:
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = GATE.main()
        assert code == GATE.EXIT_NO_DATA, code
        assert "COULD NOT CHECK" in buffer.getvalue()
    finally:
        GATE.WEB_TOKENS = original
    # And the restoration actually worked, so this test cannot poison the rest.
    assert GATE.WEB_TOKENS.exists()


def test_the_exit_codes_stay_distinct():
    """Could-not-check must never share a code with a pass."""
    assert GATE.EXIT_OK == 0
    assert GATE.EXIT_DIVERGED == 1
    assert GATE.EXIT_NO_DATA == 3
    assert len({GATE.EXIT_OK, GATE.EXIT_DIVERGED, GATE.EXIT_NO_DATA}) == 3


def test_every_exported_table_in_native_is_actually_compared():
    """A table added to native but not to TABLES would go ungated in silence."""
    import re

    native, _, _ = _sides()
    exported = set(re.findall(r"export const (PULSE_BACKGROUND_[A-Z_]+)", native))
    # Derived from the variants table rather than maintained, so it carries no
    # independent value to compare.
    exported.discard("PULSE_BACKGROUND_VARIANT_CYCLES")
    missing = exported - set(GATE.TABLES)
    assert not missing, f"native exports these tables but the gate ignores them: {sorted(missing)}"


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
