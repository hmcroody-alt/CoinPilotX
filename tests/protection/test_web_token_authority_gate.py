"""Locks for the gate that stops the website's two token files contradicting each other.

Why this gate needs its own tests
---------------------------------
The website carries two design-token stylesheets that both load on some of the
same pages: `static/css/pulsesoc-tokens.css` (server-rendered marketing, legal,
and the 151 routes that build HTML inline in `bot.py`) and
`web/src/styles/tokens.css` (the new client). Where they declare the same custom
property with different values, *nothing fails*. The one that loads last wins,
per document. The observable symptom is a modal rendering beneath a toast on
whichever subset of pages happens to link both, in whichever order the template
happens to link them -- not reproducible from either file alone.

Three such collisions were live when the gate was written: `--radius-sm` (8
against 12), `--z-modal` (40 against 888), `--z-toast` (48 against 8888). The
legacy file also held a second, entirely ungated copy of the native palette --
`native_theme_parity_gate.py` covers only the client file.

So the gate is now the only thing watching, which makes the gate the same risk
one level up. It reads a TypeScript object literal with a regex, strips at-rule
blocks by brace matching, and resolves `var()` chains. Every way any of that can
break yields *fewer* values, not wrong ones, and a comparison over no values
reports no problems. A green run against the real repo is evidence about the
repo, not about the gate.

These tests feed `compare()` synthetic sources with known divergences and assert
it goes red for each by name, and assert could-not-check for each way the
reading can fail. The real files are never mutated.

The at-rule trap gets its own section
-------------------------------------
The first throwaway probe written against these files used
`([^{}]*)\\{([^{}]*)\\}` to find blocks, and reported `--pulse-muted` as a
divergence. It was not one: that regex matches the *inner* `:root { ... }` of
`@media (prefers-contrast: more)`, so a conditional override was read as the
default scope. The gate strips at-rule blocks by brace matching first and gets
it right. `test_a_prefers_contrast_override_is_not_default_scope` is the lock on
that, and it is a real historical bug rather than a hypothetical one.

Zero-arg tests, no fixtures: this directory runs files as scripts.
"""

import contextlib
import importlib.util
import io
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
GATE_PATH = REPO / "scripts" / "ops" / "web_token_authority_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("_web_token_authority_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _load_gate()


# --- Synthetic sources ---------------------------------------------------
#
# The palette half is generated from GATE.PALETTE rather than hand-written,
# because a hand-written fixture that fell one key behind the gate would leave
# that key untested while looking complete. The generation is safe here only
# because `test_the_palette_fixture_covers_every_mapped_key` asserts the fixture
# is not empty and the real-tree tests assert the live count -- without those, a
# mutation that emptied PALETTE would empty the fixture too and pass 30 tests.
#
# Values are deliberately distinct per key so a reader that returned the wrong
# entry produces a mismatch rather than an accidental match. Two are non-opaque
# so the RGBA path is exercised, not just hex.

_COLOUR_OVERRIDES = {
    "text": "#f4f7fb",
    "accent": "#32e6b3",
    "border": "#203746",
    "glass": "rgba(11, 24, 34, 0.82)",
    "glassStrong": "rgba(7, 16, 23, 0.94)",
}


def _synthetic_colours():
    colours = {}
    for index, key in enumerate(GATE.PALETTE):
        if key in _COLOUR_OVERRIDES:
            colours[key] = _COLOUR_OVERRIDES[key]
        else:
            # #01xxxx, #02xxxx, ... -- unique per key, never a legal duplicate.
            colours[key] = f"#{index + 1:02x}{index + 1:02x}{index + 1:02x}"
    return colours


COLOURS = _synthetic_colours()

NATIVE = (
    "export const colors = {\n"
    + "".join(f'  {key}: "{value}",\n' for key, value in COLOURS.items())
    + "};\n"
)

LEGACY = (
    "/* A comment containing --pulse-palette-accent: #ff0000; which must be\n"
    "   stripped before anything is read out of it. */\n"
    ":root {\n"
    "  --pulse-base-unit: 8px;\n"
    + "".join(f"  {GATE.PALETTE[key]}: {value};\n" for key, value in COLOURS.items())
    + "  --text-primary: var(--pulse-palette-text);\n"
    "  --pulse-text:   var(--text-primary);\n"
    "  --radius-lg:    calc(var(--pulse-base-unit) * 3);\n"
    "  --radius-pill:  999px;\n"
    "  --measure-feed-max:      884px;\n"
    "  --measure-container-max: 1480px;\n"
    "}\n"
    "\n"
    "@media (prefers-contrast: more) {\n"
    "  :root {\n"
    "    --pulse-text: #ffffff;\n"
    "  }\n"
    "}\n"
)

CLIENT = (
    ":root,\n"
    '[data-theme="dark"] {\n'
    "  --pulse-text:   #f4f7fb;\n"
    "  --radius-lg:    24px;\n"
    "  --radius-pill:  999px;\n"
    "  --feed-max:     884px;\n"
    "  --shell-max:    1480px;\n"
    "}\n"
    "\n"
    '[data-theme="black"] {\n'
    "  --pulse-text: #ffffff;\n"
    "}\n"
)

#: --pulse-text, --radius-lg, --radius-pill.
SHARED_COUNT = 3


def _problems(native=NATIVE, legacy=LEGACY, client=CLIENT):
    return GATE.compare(native, legacy, client)[0]


def _expect_could_not_check(
    message, native=NATIVE, legacy=LEGACY, client=CLIENT, because=None
):
    """Assert compare() refuses -- and, when it matters, assert *which* guard.

    `because` is not decoration. The gate has several independent refusals and
    they backstop each other, so a test that only asks "did something raise
    CouldNotCheck?" can keep passing after the guard it was written for is gone.
    That is not hypothetical: deleting the empty-file guard in
    default_scope_declarations left this suite green, because a client file that
    parses to nothing also produces an empty shared-name overlap, and *that*
    guard raised instead. The test was green for a reason it never asserted.

    So where a second guard could plausibly cover for the first, pass a
    substring of the intended diagnosis.
    """
    try:
        GATE.compare(native, legacy, client)
    except GATE.CouldNotCheck as exc:
        if because is not None and because not in str(exc):
            raise AssertionError(
                f"{message}: refused, but for the wrong reason -- expected a "
                f"message containing {because!r}, got {str(exc)!r}"
            ) from None
        return
    raise AssertionError(message)


# --- The healthy case ----------------------------------------------------


def test_agreeing_token_files_report_no_problems():
    """The negative control. Without it every test below could pass vacuously."""
    problems, checked, shared = GATE.compare(NATIVE, LEGACY, CLIENT)
    assert problems == [], problems
    assert shared == SHARED_COUNT, shared
    assert checked == len(GATE.PALETTE) + SHARED_COUNT + len(GATE.MEASURES), checked


def test_the_palette_fixture_covers_every_mapped_key():
    """The fixture is generated, so its completeness is itself a claim to check."""
    assert len(COLOURS) == len(GATE.PALETTE) == 23, (len(COLOURS), len(GATE.PALETTE))
    assert set(COLOURS) == set(GATE.PALETTE)


# --- The palette is the app's, read from the app -------------------------


def test_a_changed_native_colour_is_reported():
    """Native moves, the stylesheet does not. This direction is the point.

    The comparison reads `colors.ts` rather than the other stylesheet, so a
    chain of web files agreeing with each other while all drifting from the app
    cannot pass.
    """
    native = NATIVE.replace('accent: "#32e6b3"', 'accent: "#11ffaa"')
    problems = _problems(native=native)
    assert any("--pulse-palette-accent" in p for p in problems), problems


def test_a_changed_stylesheet_colour_is_reported():
    legacy = LEGACY.replace("--pulse-palette-border: #203746;", "--pulse-palette-border: #204746;")
    problems = _problems(legacy=legacy)
    assert any("--pulse-palette-border" in p for p in problems), problems


def test_an_alpha_drift_is_reported():
    """The channel most likely to be nudged by eye and least likely to be noticed."""
    legacy = LEGACY.replace("rgba(11, 24, 34, 0.82)", "rgba(11, 24, 34, 0.80)")
    problems = _problems(legacy=legacy)
    assert any("--pulse-palette-glass" in p for p in problems), problems


def test_every_palette_entry_is_compared_and_not_just_the_first():
    """A loop that broke after one iteration would still pass the tests above."""
    legacy = LEGACY
    for key, value in COLOURS.items():
        legacy = legacy.replace(f"{GATE.PALETTE[key]}: {value};", f"{GATE.PALETTE[key]}: #123456;")
    problems = _problems(legacy=legacy)
    named = {token for token in GATE.PALETTE.values() if any(token in p for p in problems)}
    # `text` is legitimately #123456 on both sides after this rewrite only if the
    # rewrite missed it, so every token must appear.
    assert named == set(GATE.PALETTE.values()), sorted(set(GATE.PALETTE.values()) - named)


def test_equivalent_spellings_are_not_a_divergence():
    """A gate that called these a collision is a gate somebody switches off.

    `rgb(244, 247, 251)` is `#f4f7fb`, and `0.820` is `0.82`. Comparing
    declaration text would force one file to be written in the other's shape.
    """
    legacy = LEGACY.replace(
        "--pulse-palette-text: #f4f7fb;", "--pulse-palette-text: rgb(244, 247, 251);"
    ).replace("rgba(11, 24, 34, 0.82)", "rgba(11, 24, 34, 0.820)")
    assert _problems(legacy=legacy) == []


def test_the_native_side_is_resolved_through_the_var_chain():
    """`--pulse-text` reaches its value through two hops, and must still compare.

    Legacy writes `--pulse-text: var(--text-primary)` and
    `--text-primary: var(--pulse-palette-text)`; the client writes the literal.
    A resolver that stopped at the first hop would compare the string
    "var(--text-primary)" against "#f4f7fb" and report a permanent divergence.
    """
    legacy = GATE.default_scope_declarations(LEGACY, "fixture")
    assert GATE.resolve("--pulse-text", legacy) == "#f4f7fb"


def test_the_calc_shape_is_reduced_rather_than_compared_as_text():
    legacy = GATE.default_scope_declarations(LEGACY, "fixture")
    assert GATE.resolve("--radius-lg", legacy) == "24px"


# --- Nothing is declared twice with two different values -----------------


def test_a_shared_name_with_two_values_is_reported():
    client = CLIENT.replace("--radius-lg:    24px;", "--radius-lg:    20px;")
    problems = _problems(client=client)
    assert any("--radius-lg" in p for p in problems), problems


def test_the_pre_fix_z_modal_collision_would_be_caught_again():
    """888 against 40 was live in the tree before this gate existed.

    Pinned by its real name and its real two values, because the way this comes
    back is someone re-adding an unprefixed `--z-modal` to the legacy file
    without knowing the client already owns that name.
    """
    legacy = LEGACY.replace("  --radius-pill:  999px;", "  --radius-pill:  999px;\n  --z-modal: 888;")
    client = CLIENT.replace("  --radius-pill:  999px;", "  --radius-pill:  999px;\n  --z-modal: 40;")
    problems = GATE.compare(NATIVE, legacy, client)[0]
    assert any("--z-modal" in p for p in problems), problems


def test_the_pre_fix_radius_sm_collision_would_be_caught_again():
    """8px against 12px -- a control 4px rounder depending on stylesheet order."""
    legacy = LEGACY.replace(
        "  --radius-pill:  999px;", "  --radius-pill:  999px;\n  --radius-sm: 12px;"
    )
    client = CLIENT.replace(
        "  --radius-pill:  999px;", "  --radius-pill:  999px;\n  --radius-sm: 8px;"
    )
    problems = GATE.compare(NATIVE, legacy, client)[0]
    assert any("--radius-sm" in p for p in problems), problems


def test_an_alias_repointed_at_the_wrong_token_is_reported():
    """The legacy file is mostly aliases, so this is how it actually drifts.

    Nothing about `--pulse-text: var(--pulse-palette-muted)` looks wrong in a
    diff; it is one word, and the value it resolves to is a real colour from the
    same palette.
    """
    legacy = LEGACY.replace(
        "--pulse-text:   var(--text-primary);", "--pulse-text:   var(--pulse-palette-muted);"
    )
    problems = _problems(legacy=legacy)
    assert any("--pulse-text" in p for p in problems), problems


def test_the_collision_message_names_the_load_order_consequence():
    """The message has to say why it matters, or it reads as pedantry and gets waived."""
    client = CLIENT.replace("--radius-lg:    24px;", "--radius-lg:    20px;")
    problems = _problems(client=client)
    assert any("loads last wins" in p for p in problems), problems


# --- The two surfaces bound themselves at the same widths ----------------


def test_the_plans_disproven_feed_width_is_reported():
    """680 was the phase plan's guess; 884 is what the shell leaves.

    Pinned because it is the specific wrong number most likely to be
    reintroduced by someone reading the old plan instead of the app.
    """
    legacy = LEGACY.replace("--measure-feed-max:      884px;", "--measure-feed-max:      680px;")
    problems = _problems(legacy=legacy)
    assert any("reading column" in p for p in problems), problems


def test_the_plans_disproven_shell_width_is_reported():
    legacy = LEGACY.replace(
        "--measure-container-max: 1480px;", "--measure-container-max: 1288px;"
    )
    problems = _problems(legacy=legacy)
    assert any("outer shell" in p for p in problems), problems


def test_a_client_that_widens_alone_is_reported():
    """The measures are compared across files, not each against a constant."""
    client = CLIENT.replace("--shell-max:    1480px;", "--shell-max:    1600px;")
    problems = _problems(client=client)
    assert any("outer shell" in p for p in problems), problems


# --- At-rules are conditional, not default scope -------------------------


def test_a_prefers_contrast_override_is_not_default_scope():
    """The real bug the brace matcher exists to prevent.

    The fixture's `@media (prefers-contrast: more)` block redeclares
    `--pulse-text` as #ffffff. A block reader using `\\{[^}]*\\}` reads that as
    the default scope and reports a divergence against the client's #f4f7fb --
    which is exactly what the first throwaway probe did.
    """
    legacy = GATE.default_scope_declarations(LEGACY, "fixture")
    assert GATE.resolve("--pulse-text", legacy) == "#f4f7fb"
    assert _problems() == []


def test_a_new_media_block_redefining_a_shared_name_is_ignored():
    legacy = LEGACY + "\n@media (min-width: 900px) {\n  :root { --pulse-text: #ff0000; }\n}\n"
    assert _problems(legacy=legacy) == []


def test_a_nested_at_rule_does_not_swallow_the_rest_of_the_file():
    """`@supports` inside `@media` is two opening braces before the first close.

    A stripper that consumed to the first `}` would leave the tail of the outer
    block looking like top-level CSS, and a stripper that consumed to the last
    would eat every declaration after it -- which reports as could-not-check at
    best and as silence at worst.
    """
    legacy = LEGACY.replace(
        "@media (prefers-contrast: more) {",
        "@media (min-width: 900px) {\n"
        "  @supports (color: color-mix(in srgb, red, blue)) {\n"
        "    :root { --pulse-text: #ff0000; }\n"
        "  }\n"
        "}\n"
        "@media (prefers-contrast: more) {",
    )
    assert _problems(legacy=legacy) == []


def test_a_theme_block_the_marketing_surface_does_not_have_is_ignored():
    """The client supports themes; the legacy file supports none.

    Comparing `[data-theme="black"]` across the two would report every theme the
    client has as a divergence -- a true statement about the files and a useless
    one about the product.
    """
    client = CLIENT.replace('[data-theme="black"] {\n  --pulse-text: #ffffff;', '[data-theme="black"] {\n  --pulse-text: #abcdef;')
    assert _problems(client=client) == []


# --- Could-not-check, not a pass -----------------------------------------


def test_a_renamed_native_palette_is_could_not_check():
    _expect_could_not_check(
        "a renamed `colors` export must raise CouldNotCheck",
        native=NATIVE.replace("export const colors", "export const palette"),
        because="no `export const colors",
    )


def test_an_empty_native_palette_is_could_not_check():
    """An empty expected side agrees with every possible actual side."""
    _expect_could_not_check(
        "an empty palette must raise CouldNotCheck",
        native="export const colors = {\n};\n",
        because="parsed to no entries",
    )


def test_a_native_key_the_stylesheet_still_declares_is_could_not_check():
    """Deleting a colour from the app does not make the web copy correct."""
    _expect_could_not_check(
        "a missing native key must raise CouldNotCheck",
        native=NATIVE.replace(f'  accent: "{COLOURS["accent"]}",\n', ""),
        because="is gone from",
    )


def test_a_deleted_primitive_is_could_not_check_not_equal():
    _expect_could_not_check(
        "a missing primitive must raise CouldNotCheck",
        legacy=LEGACY.replace(f"  --pulse-palette-focus: {COLOURS['focus']};\n", ""),
        because="is not declared at the default scope",
    )


def test_a_deleted_measure_is_could_not_check():
    _expect_could_not_check(
        "a missing measure must raise CouldNotCheck",
        legacy=LEGACY.replace("  --measure-feed-max:      884px;\n", ""),
        because="layout measure:",
    )


def test_a_client_file_with_no_default_scope_is_could_not_check():
    """A token file the gate reads as empty compares equal to anything."""
    _expect_could_not_check(
        "a client file with no `:root` must raise CouldNotCheck",
        client=CLIENT.replace(':root,\n[data-theme="dark"] {', '[data-theme="black"] {'),
        because="no `:root` custom properties found",
    )


def test_no_shared_names_at_all_is_could_not_check():
    """An empty overlap has no contradictions by definition.

    This is what a wholesale prefix rename looks like, and it is the single most
    likely way this gate goes permanently, silently green.
    """
    legacy = LEGACY
    for name in ("--pulse-text:", "--radius-lg:", "--radius-pill:"):
        legacy = legacy.replace(name, name.replace("--", "--zz-"))
    _expect_could_not_check(
        "an empty overlap must raise CouldNotCheck",
        legacy=legacy,
        because="share no custom property names at all",
    )


def test_a_var_cycle_is_could_not_check_and_not_a_recursion_error():
    """`--a: var(--b); --b: var(--a);` renders nothing and must not crash the gate."""
    legacy = LEGACY.replace(
        "  --text-primary: var(--pulse-palette-text);",
        "  --text-primary: var(--pulse-text);",
    )
    _expect_could_not_check(
        "a var() cycle must raise CouldNotCheck",
        legacy=legacy,
        because="is part of a var() cycle",
    )


def test_a_value_the_resolver_cannot_reduce_is_could_not_check():
    """`color-mix()` is valid CSS and unreadable here. Unreadable is not agreement."""
    legacy = LEGACY.replace(
        f"--pulse-palette-accent: {COLOURS['accent']};",
        "--pulse-palette-accent: color-mix(in srgb, var(--x) 50%, #000);",
    )
    _expect_could_not_check(
        "an unresolvable value must raise CouldNotCheck",
        legacy=legacy,
        because="which this resolver cannot reduce",
    )


def test_an_at_rule_with_no_block_is_could_not_check():
    """A stylesheet that does not parse must not be reported on partially."""
    _expect_could_not_check(
        "an unterminated at-rule must raise CouldNotCheck",
        legacy=LEGACY + "\n@media (min-width: 900px)\n",
        because="has no block",
    )


def test_an_unbalanced_at_rule_block_is_could_not_check():
    _expect_could_not_check(
        "unbalanced braces must raise CouldNotCheck",
        legacy=LEGACY + "\n@media (min-width: 900px) {\n  :root { --x: 1px;\n",
        because="unbalanced braces",
    )


def test_a_missing_file_exits_could_not_check_not_ok():
    original = GATE.CLIENT_TOKENS
    GATE.CLIENT_TOKENS = REPO / "web" / "src" / "styles" / "does_not_exist.css"
    try:
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = GATE.main()
        assert code == GATE.EXIT_NO_DATA, code
        # Not a redundant second assert. The line above compares the returned
        # code against the very constant that a "could-not-check collapses onto
        # ok" regression moves, so it passes tautologically the moment
        # EXIT_NO_DATA becomes 0 -- which is precisely the regression. The name
        # of this test is "not ok", and this is that claim: CI reads the number,
        # and the number must not be the success number.
        assert code != GATE.EXIT_OK, code
        assert "COULD NOT CHECK" in buffer.getvalue()
    finally:
        GATE.CLIENT_TOKENS = original
    assert GATE.CLIENT_TOKENS.exists()


def test_the_exit_codes_stay_distinct():
    """Collapsing could-not-check onto ok is the failure mode that matters."""
    codes = {GATE.EXIT_OK, GATE.EXIT_DIVERGED, GATE.EXIT_NO_DATA}
    assert len(codes) == 3, codes
    assert GATE.EXIT_OK == 0


def test_the_short_comparison_guard_is_a_backstop_and_not_the_only_check():
    """Why `checked < expected` in main() has no test of its own.

    Every path that would shorten the comparison raises CouldNotCheck before it
    can return, so the guard is unreachable through the public entry point --
    the same shape as an assert. It stays because it costs nothing and catches a
    future `continue` added to one of the three loops, but a test that forced it
    would be testing a mutation of the gate rather than the gate. This records
    the reasoning so the guard is not deleted as dead code.
    """
    problems, checked, shared = GATE.compare(NATIVE, LEGACY, CLIENT)
    assert checked == len(GATE.PALETTE) + shared + len(GATE.MEASURES)
    assert problems == []


# --- normalise() ---------------------------------------------------------


def test_colour_spellings_that_mean_one_colour_normalise_together():
    white = GATE.normalise("#ffffff")
    assert GATE.normalise("#fff") == white
    assert GATE.normalise("#FFFFFF") == white
    assert GATE.normalise("rgb(255, 255, 255)") == white
    assert GATE.normalise("rgba(255, 255, 255, 1)") == white
    assert GATE.normalise("#ffffffff") == white


def test_alpha_survives_normalisation():
    """Collapsing alpha would make every glass token compare equal to its opaque twin.

    normalise() reads alpha on two independent branches -- `rgba()` and 8- or
    4-digit hex -- and this test used to exercise only the first. Mutation
    testing caught that: hardcoding the *hex* branch's alpha to 1.0 left the
    whole suite green. Both notations are live here (colors.ts writes `rgba()`,
    hand-authored CSS writes `#rrggbbaa`), so both are asserted -- and so is the
    claim that they agree with each other, because a gate that read `#FF0000CC`
    and `rgba(255,0,0,0.8)` as different colours would report a divergence that
    is only a difference of spelling.
    """
    assert GATE.normalise("rgba(0, 0, 0, 0.5)") != GATE.normalise("rgb(0, 0, 0)")
    assert GATE.normalise("rgba(0, 0, 0, 0.50)") == GATE.normalise("rgba(0, 0, 0, 0.5)")

    assert GATE.normalise("#00000080") != GATE.normalise("#000000")
    assert GATE.normalise("#0000") != GATE.normalise("#000")
    assert GATE.normalise("#FF0000CC") != GATE.normalise("#FF0000")

    assert GATE.normalise("#FF0000CC") == GATE.normalise("rgba(255, 0, 0, 0.8)")
    assert GATE.normalise("#000000") == GATE.normalise("rgb(0, 0, 0)")


def test_lengths_and_bare_numbers_do_not_collide():
    assert GATE.normalise("24px") == GATE.normalise("24.0px")
    assert GATE.normalise("24px") != GATE.normalise("24")
    assert GATE.normalise("888") == GATE.normalise("888.0")


def test_a_value_that_is_genuinely_text_stays_text():
    assert GATE.normalise("Inter, sans-serif")[0] == "text"
    assert GATE.normalise("  cubic-bezier(0.2, 0, 0, 1)  ")[0] == "text"


# --- The live tree -------------------------------------------------------


def test_the_gate_passes_on_the_committed_tree():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = GATE.main()
    assert code == GATE.EXIT_OK, f"exit {code}\n{buffer.getvalue()}"


def test_the_real_run_actually_compares_something():
    """A gate that verified zero values would also report no problems."""
    problems, checked, shared = GATE.compare(
        GATE.NATIVE_COLORS.read_text(encoding="utf-8"),
        GATE.LEGACY_TOKENS.read_text(encoding="utf-8"),
        GATE.CLIENT_TOKENS.read_text(encoding="utf-8"),
    )
    assert problems == [], problems
    assert shared >= 6, shared
    assert checked == len(GATE.PALETTE) + shared + len(GATE.MEASURES), checked


def test_the_three_historical_collisions_are_actually_gone():
    """Not "would be caught" -- gone. The fix, pinned separately from the gate.

    Each of these was declared unprefixed in the legacy stylesheet and also, at
    a different value, in the client's. The legacy declarations are namespaced
    now (`--pulse-radius-sm`, `--z-pulse-modal`, `--z-pulse-toast`), so the names
    below must not appear in its default scope at all.
    """
    legacy = GATE.default_scope_declarations(
        GATE.LEGACY_TOKENS.read_text(encoding="utf-8"), GATE.LEGACY_TOKENS.name
    )
    for name in ("--radius-sm", "--z-modal", "--z-toast"):
        assert name not in legacy, f"{name} is back in {GATE.LEGACY_TOKENS.name}"


def test_the_namespaced_replacements_exist():
    """Otherwise the test above passes by the tokens having simply been deleted."""
    legacy = GATE.default_scope_declarations(
        GATE.LEGACY_TOKENS.read_text(encoding="utf-8"), GATE.LEGACY_TOKENS.name
    )
    assert GATE.resolve("--pulse-radius-sm", legacy) == "12px"
    assert GATE.resolve("--z-pulse-modal", legacy) == "888"
    assert GATE.resolve("--z-pulse-toast", legacy) == "8888"


def test_the_live_measures_are_the_apps_numbers():
    """Pins the actual widths, so a silent revert to the plan's guess is visible."""
    legacy = GATE.default_scope_declarations(
        GATE.LEGACY_TOKENS.read_text(encoding="utf-8"), GATE.LEGACY_TOKENS.name
    )
    assert GATE.resolve("--measure-feed-max", legacy) == "884px"
    assert GATE.resolve("--measure-container-max", legacy) == "1480px"


def test_the_legacy_palette_is_still_a_full_copy_of_the_apps():
    """The gate compares 23 names; this asserts all 23 are really declared there.

    A palette entry quietly dropped from the stylesheet raises CouldNotCheck
    inside `compare()`, which is correct but only visible when the gate runs.
    Stated here it is visible as a property of the tree.
    """
    legacy = GATE.default_scope_declarations(
        GATE.LEGACY_TOKENS.read_text(encoding="utf-8"), GATE.LEGACY_TOKENS.name
    )
    missing = [token for token in GATE.PALETTE.values() if token not in legacy]
    assert missing == [], missing


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
