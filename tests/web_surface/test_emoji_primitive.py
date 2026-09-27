"""One emoji system, two platforms — enforced, not asserted in a comment.

The mission is that the website's emoji experience *is* the app's, not a
second thing that looks like it. That claim decays the moment someone adds a
convenient little array of eight glyphs to a new surface, which is exactly how
the website got where it was: a comment box that appended a hardcoded 🔥 and a
messenger panel with eight hand-written buttons.

The native side already defends itself
(mobile-native/src/emoji/__tests__/emojiEngineGuard.test.ts). This is the web
half of the same guard, and it checks four things:

1. **The two dataset files are byte-identical.** They must be two files —
   `.railwayignore` drops `mobile-native/` from the backend deploy and
   `.easignore` drops `static/` from the native build, so neither platform can
   read the other's path in the artifact that actually ships. One generator
   writes both (mobile-native/scripts/generate-emoji-data.mjs); this test is
   what keeps them equal.

2. **The dataset is real.** A byte-identity check between two files passes
   vacuously if both are empty, truncated or replaced by a stub, so identity
   alone proves nothing. The dataset is parsed and its shape asserted.

3. **There is one picker.** No second implementation in `static/` or
   `templates/`, measured by emoji-literal density with a baseline that can
   only shrink.

4. **The web picker really mirrors the app.** The taxonomy, the search score
   ladder, the storage keys and the grid geometry are read out of both
   sources and compared. A drift in any of them means a user who learned the
   app has to re-learn the website, which is the thing the mission forbids.

Run: python3 -m pytest tests/web_surface/test_emoji_primitive.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NATIVE_DATA = os.path.join(ROOT, "mobile-native", "src", "emoji", "data", "emoji.json")
WEB_DATA = os.path.join(ROOT, "static", "emoji", "emoji.json")
GENERATOR = os.path.join(ROOT, "mobile-native", "scripts", "generate-emoji-data.mjs")
WEB_PICKER = os.path.join(ROOT, "static", "js", "pulse_emoji.js")
NATIVE_PICKER = os.path.join(ROOT, "mobile-native", "src", "emoji", "EmojiPicker.tsx")
NATIVE_DATA_TS = os.path.join(ROOT, "mobile-native", "src", "emoji", "emojiData.ts")
NATIVE_TYPES = os.path.join(ROOT, "mobile-native", "src", "emoji", "types.ts")
NATIVE_RECENTS = os.path.join(ROOT, "mobile-native", "src", "emoji", "recents.ts")

#: Emoji literals that predate the shared picker and are NOT a second emoji
#: dataset: reaction-type icon maps and i18n sample strings. The number is a
#: ceiling, so these files may lose emoji and may not gain them. Do NOT add a
#: file here to make a new hardcoded emoji list pass — build the surface on
#: window.PulseEmoji instead, which is the entire point of this test.
LEGACY_EMOJI_CEILING = {
    "static/js/pulse_messages_v2.js": 31,
    "static/js/pulse_home_core.js": 29,
    "static/js/pulse_i18n.js": 20,
}

#: A file is suspected of being a second emoji dataset at this density. The
#: canonical 19 reaction types plus a stray or two sit comfortably below it.
DENSITY_THRESHOLD = 20

EMOJI_LITERAL = re.compile("[\U0001f300-\U0001faff]")


def read(path: str) -> str:
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def test_the_two_dataset_files_are_byte_identical():
    """One dataset. Two files only because two deploys exclude each other."""
    with open(NATIVE_DATA, "rb") as handle:
        native = handle.read()
    with open(WEB_DATA, "rb") as handle:
        web = handle.read()
    assert native == web, (
        "mobile-native/src/emoji/data/emoji.json and static/emoji/emoji.json have "
        "diverged. Regenerate BOTH with `node mobile-native/scripts/"
        "generate-emoji-data.mjs` rather than hand-editing either one."
    )


def test_the_dataset_is_not_a_stub():
    """Identity between two empty files is still identity. Check the content."""
    artifact = json.loads(read(WEB_DATA))
    assert set(artifact) >= {"version", "count", "emojis"}, sorted(artifact)
    assert isinstance(artifact["emojis"], list)
    # RGI emoji minus the component group is ~1,900. A number far below this
    # means a truncated or hand-written file, which is what we are guarding
    # against; the check is deliberately not an equality so a Unicode release
    # can move it.
    assert len(artifact["emojis"]) >= 1500, len(artifact["emojis"])
    assert artifact["count"] == len(artifact["emojis"])
    assert artifact["version"].strip(), "dataset provenance string is empty"
    first = artifact["emojis"][0]
    assert set(first) == {
        "emoji", "name", "keywords", "category", "subgroup",
        "skin_tone_capable", "variants",
    }, sorted(first)
    # Every entry is a native Unicode string, never an image path or a vendor
    # id. This is the rule both platforms store by.
    for entry in artifact["emojis"][:200]:
        assert entry["emoji"] and "/" not in entry["emoji"], entry
        assert entry["name"].strip(), entry


def test_one_generator_writes_both_files():
    source = read(GENERATOR)
    assert "src\", \"emoji\", \"data\", \"emoji.json\"" in source.replace("'", '"')
    assert "\"static\", \"emoji\", \"emoji.json\"" in source.replace("'", '"')


def test_the_generator_is_deterministic():
    """The two artifacts are held identical by a test, and that test is only
    meaningful if regenerating produces the same bytes.

    The generator used to stamp `version` with `new Date()`. Both files still
    matched -- they are written in one run -- so byte-identity stayed green
    while the output changed every single run: the protected native artifact
    churned on each regeneration, and regenerating one file alone produced a
    diff that looked like a dataset change and was only the calendar. A stamp
    derived from the dataset keeps `git status` honest, so a diff here always
    means upstream Unicode actually moved.
    """
    # Code only. The comment above the fix names `new Date()` to explain what
    # was wrong, and a guard that its own subject line can satisfy is not a
    # guard -- the same reason `function_body()` exists further down.
    source = strip_js_comments(read(GENERATOR))
    assert "new Date(" not in source, (
        "the version stamp must not read the clock -- it makes the artifact "
        "irreproducible and churns mobile-native/ on every run"
    )
    assert "createHash(" in source, "version stamp should be a content digest"
    version = json.loads(read(WEB_DATA))["version"]
    assert "sha256:" in version, version
    # And the digest must describe THIS file, not a constant someone pasted in.
    payload = json.dumps(json.loads(read(WEB_DATA))["emojis"], ensure_ascii=False, separators=(",", ":"))
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    assert version.endswith(expected), f"digest {version!r} does not describe the payload ({expected})"


def test_no_second_emoji_picker_on_the_web():
    """Density scan: a file full of emoji literals is a hardcoded emoji list."""
    offenders = []
    for root in ("static", "templates"):
        base = os.path.join(ROOT, root)
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                if not name.endswith((".js", ".css", ".html")):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, ROOT)
                if rel in ("static/js/pulse_emoji.js", "static/css/pulse_emoji.css"):
                    continue
                try:
                    count = len(EMOJI_LITERAL.findall(read(path)))
                except (OSError, UnicodeDecodeError):
                    continue
                ceiling = LEGACY_EMOJI_CEILING.get(rel, DENSITY_THRESHOLD - 1)
                if count > ceiling:
                    offenders.append((rel, count, ceiling))
    assert not offenders, (
        "These web files carry more emoji literals than allowed, which is what a "
        "second hardcoded emoji list looks like. Use window.PulseEmoji "
        "(static/js/pulse_emoji.js) instead of writing your own list: "
        + repr(sorted(offenders))
    )


def test_no_surface_fetches_the_dataset_directly():
    """Only the primitive may load emoji.json; everyone else goes through it."""
    offenders = []
    for root in ("static", "templates"):
        base = os.path.join(ROOT, root)
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                if not name.endswith((".js", ".html")):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, ROOT)
                if rel == "static/js/pulse_emoji.js":
                    continue
                try:
                    source = read(path)
                except (OSError, UnicodeDecodeError):
                    continue
                if "emoji/emoji.json" in source:
                    offenders.append(rel)
    assert not offenders, offenders


def test_web_picker_mirrors_the_app_taxonomy():
    web = read(WEB_PICKER)
    types = read(NATIVE_TYPES)
    native_categories = re.findall(r'"([A-Z][A-Z &]+)"', types.split("EMOJI_CATEGORIES")[1])
    assert "RECENT" in native_categories and "FLAGS" in native_categories, native_categories
    for category in native_categories:
        assert f'"{category}"' in web, f"web picker is missing category {category!r}"
    # Order matters: the tab strip and the scroll order are the same reading.
    web_block = web.split("var EMOJI_CATEGORIES = [")[1].split("]")[0]
    web_order = re.findall(r'"([A-Z][A-Z &]+)"', web_block)
    assert web_order == native_categories, (web_order, native_categories)

    picker = read(NATIVE_PICKER)
    for glyph in re.findall(r'"([\U0001f300-\U0001faff️\U0001f1e6-\U0001f1ff]+)"',
                            picker.split("CATEGORY_ICONS")[1].split("}")[0]):
        assert glyph in web, f"web picker is missing category icon {glyph!r}"


def strip_js_comments(source: str) -> str:
    """Drop `//` line comments and `/* */` blocks.

    Same motive as `function_body` below: a guard asserting an API is absent
    must not be satisfied by a comment explaining why it was removed.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in source.splitlines())


def function_body(source: str, marker: str) -> str:
    """The text between a function's signature and its closing brace.

    Scoped deliberately: this file's own prose explains the score ladder, and a
    guard that can be satisfied by a comment mentioning the right number is not
    a guard. Everything below reads function bodies only.
    """
    tail = source.split(marker)[1]
    return tail[: tail.index("\n}")]


SCORE_LITERAL = re.compile(r"(?:s = |Math\.max\(s, )(\d+)")


def test_web_picker_mirrors_the_app_search_ranking():
    """Same query, same order, same first result — on both platforms."""
    web_body = function_body(read(WEB_PICKER), "function searchEmoji")
    native_body = function_body(read(NATIVE_DATA_TS), "export function searchEmoji")
    # The ladder, in source order: name exact 100, name prefix 80, name
    # substring 60, then keyword exact 70, keyword prefix 50, substring 30.
    expected = ["0", "100", "80", "60", "70", "50", "30"]
    assert SCORE_LITERAL.findall(native_body) == expected, SCORE_LITERAL.findall(native_body)
    assert SCORE_LITERAL.findall(web_body) == expected, (
        "the web search ranking has drifted from mobile-native/src/emoji/"
        "emojiData.ts; the same query must return the same first result on both "
        "platforms: " + repr(SCORE_LITERAL.findall(web_body))
    )
    assert "limit * 4" in native_body and "limit * 4" in web_body
    assert "limit = limit || 120" in web_body
    assert "limit = 120" in read(NATIVE_DATA_TS)


def test_web_picker_mirrors_the_app_storage_contract():
    web = read(WEB_PICKER)
    native = read(NATIVE_RECENTS)
    for key in ("pulsesoc.emoji.recents.v1", "pulsesoc.emoji.skin_tone.v1"):
        assert key in native, key
        assert key in web, f"web picker does not use the app's storage key {key!r}"
    assert "MAX_RECENTS = 40" in native
    assert "MAX_RECENTS = 40" in web


def test_web_picker_mirrors_the_app_grid_geometry():
    """Same look: 8 columns, 44px cells, 28px glyphs, 40x4 grab handle."""
    web_js = read(WEB_PICKER)
    web_css = read(os.path.join(ROOT, "static", "css", "pulse_emoji.css"))
    picker = read(NATIVE_PICKER)
    assert "const COLUMNS = 8;" in picker and "var COLUMNS = 8;" in web_js
    assert "const CELL = 44;" in picker and "var CELL = 44;" in web_js
    assert "fontSize: 28" in picker and "font-size: 28px" in web_css
    assert "width: 44px" in web_css and "height: 44px" in web_css
    assert "border-radius: 18px" in web_css       # sheet / popover radius
    assert "letter-spacing: 1px" in web_css       # category headers
    assert "justify-content: space-around" in web_css  # tab strip


def test_the_picker_is_loaded_lazily_not_in_the_initial_bundle():
    """The 500KB dataset must not be part of any page's initial payload."""
    web = read(WEB_PICKER)
    assert "fetch(DATA_URL" in web, "the dataset must be fetched, not inlined"
    assert len(EMOJI_LITERAL.findall(web)) < 40, (
        "the web picker itself is carrying an emoji list; it should read the "
        "dataset and hold only the category tab glyphs"
    )
    for page in ("static/js/pulse_home_core.js", "static/js/pulse_messages_v2.js"):
        source = read(os.path.join(ROOT, page))
        assert "emoji.json" not in source, page


def test_the_wired_surfaces_use_the_primitive():
    """The two surfaces this landing converted must not have regressed."""
    home = read(os.path.join(ROOT, "static", "js", "pulse_home_core.js"))
    assert "window.PulseEmoji.open({" in home
    assert 'input.value = `${input.value || ""}\U0001f525`' not in home, (
        "the comment composer is appending a hardcoded fire emoji again"
    )
    messages = read(os.path.join(ROOT, "static", "js", "pulse_messages_v2.js"))
    assert "window.PulseEmoji" in messages
    template = read(os.path.join(ROOT, "templates", "pulse_messages_v2.html"))
    assert "data-emoji-value" not in template, (
        "the messenger's hardcoded eight-emoji strip is back"
    )
    assert "pulse_emoji.js" in template
    bot_py = read(os.path.join(ROOT, "bot.py"))
    assert "/static/js/pulse_emoji.js" in bot_py, "the /pulse shell dropped the picker"


def test_neither_platform_fakes_a_feeling_by_editing_the_authors_body():
    """A post has no feeling column, so no ☺ button may invent one.

    The app states the contract in its own error copy -- HomePulseComposer's
    Feeling action answers "Structured feelings are not supported by the
    production post contract yet. PulseSoc will not change what you wrote or
    add a feeling for you." The web used to do exactly that: its ☺ button
    spliced the literal string "Feeling: " into #postBody, fabricating a field
    by rewriting the author's own sentence. The two platforms must agree, and
    the thing a ☺ button actually owes the user is the picker.
    """
    native = read(os.path.join(
        ROOT, "mobile-native", "src", "components", "HomePulseComposer.tsx"
    ))
    assert "not supported by the production post contract" in native, (
        "the app stopped refusing structured feelings; if the post contract "
        "grew a feeling field, this test and both composers need revisiting"
    )

    home = read(os.path.join(ROOT, "static", "js", "pulse_home_core.js"))
    # Slice to the NEXT handler declaration, not to the enclosing function's
    # closing brace: the comment composer opens the same picker a few handlers
    # down, so a wider slice would let this assertion pass on someone else's
    # call site while the composer's button did nothing.
    tail = home.split("[data-composer-emoji]")[1]
    branch = tail[: tail.index("\n    const ")]
    assert "window.PulseEmoji.open({" in branch, (
        "the composer's emoji button must open the shared picker"
    )
    assert 'getElementById("postBody")' in branch, (
        "the picker must insert into the composer body the user is writing in"
    )
    for source, name in ((home, "pulse_home_core.js"), (read(os.path.join(ROOT, "bot.py")), "bot.py")):
        assert '"Feeling: "' not in strip_js_comments(source), (
            f"{name} is typing a structured feeling into the post body again"
        )

    markup = read(os.path.join(ROOT, "bot.py"))
    assert "data-composer-emoji" in markup, "the composer lost its emoji trigger"
    assert 'data-composer-rail="feeling"' not in markup, (
        "the composer's ☺ button is back on the text-splicing rail"
    )
