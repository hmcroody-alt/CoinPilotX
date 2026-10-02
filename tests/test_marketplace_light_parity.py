"""The web storefront's palette must stay equal to the native app's.

Why this file exists
====================
The web marketplace used to be dark and the app's Store has always been light.
They were not two drifted tunings of one palette -- they were opposite
polarities, which is why no amount of adjusting values could have closed the
gap and why `pulse_marketplace.css` now resolves its colour from a `--store-*`
block transcribed out of the native theme instead of from the dark sheet.

A transcription is a copy, and a copy rots. `static/css/pulse_marketplace.css`
names this file in its header as the reason that cannot happen quietly, so this
is that guarantee: every `--store-*` token is checked against the value in
`mobile-native/src/theme/storeLight.ts` or `marketplaceLight.ts` that it claims
to come from, and a token that belongs to neither has to be declared here as a
deliberate web-only derivation before the suite will go green.

The direction is one-way on purpose. The app is the authority; the web copies
it. If these disagree, the CSS is what changed by mistake.

Two failure modes this is written against
=========================================
1. **A parser that silently shrinks.** The existing native-parity gates read
   their theme file with a flat regex for string literals, so a key whose value
   stops being a literal is dropped from the comparison rather than failing it
   -- the gate keeps passing while checking less. That is not a hypothetical
   here: `accent.brand` is `STORE_CTA_PULSESOC.from`, `badge.featuredText` is
   `storeLight.accent.brand`, and `cta` is the whole `STORE_CTA` object. A
   literal-only reader would skip all three, including both halves of the
   primary CTA. So this file resolves references, and then asserts that it
   resolved the specific keys known to need it (`REFERENCE_VALUED`) -- if the
   resolver regresses to literals-only, those assertions fail loudly.

2. **A gate that checks a subset.** Every token in the stylesheet must be
   accounted for, so `EXPECTED_TOKEN_COUNT` and the "unmapped" assertion below
   make adding a 39th token without classifying it a failure.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_PATH = os.path.join(ROOT, "static", "css", "pulse_marketplace.css")
STORE_LIGHT = os.path.join(ROOT, "mobile-native", "src", "theme", "storeLight.ts")
MARKETPLACE_LIGHT = os.path.join(
    ROOT, "mobile-native", "src", "theme", "marketplaceLight.ts")


# --------------------------------------------------------------------------- #
# Parsing the native theme
# --------------------------------------------------------------------------- #

def _strip_comments(source):
    """Remove block and line comments.

    The theme files carry long JSDoc blocks whose prose contains hex values and
    token names (`NOT STORE_CTA_PULSESOC.to, which ... measures 2.25:1`), so
    parsing without stripping these would read documentation as data.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _object_body(source, name):
    """The text between the braces of `export const <name> = { ... }`."""
    match = re.search(r"export\s+const\s+%s\b[^=]*=\s*" % re.escape(name), source)
    assert match, "%s is not declared in the native theme" % name
    start = source.index("{", match.end())
    depth, i = 0, start
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1:i]
        i += 1
    raise AssertionError("unbalanced braces reading %s" % name)


def _parse_object(body, prefix=""):
    """Flatten `key: "value"` and `key: { ... }` into a dotted dict.

    Values that are not string literals are kept verbatim so a later pass can
    resolve them; dropping them is failure mode 1 in this module's docstring.
    """
    out = {}
    i = 0
    while i < len(body):
        match = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*").search(body, i)
        if not match:
            break
        key = match.group(1)
        path = "%s%s" % (prefix, key)
        j = match.end()
        if body[j] == "{":
            depth, k = 0, j
            while k < len(body):
                if body[k] == "{":
                    depth += 1
                elif body[k] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            out.update(_parse_object(body[j + 1:k], path + "."))
            i = k + 1
        else:
            end = j
            depth = 0
            while end < len(body):
                char = body[end]
                if char in "[(":
                    depth += 1
                elif char in "])":
                    depth -= 1
                elif char in ",\n" and depth <= 0:
                    break
                end += 1
            out[path] = body[j:end].strip().rstrip(",").strip()
            i = end + 1
    return out


def _load_theme():
    """The native palette, flattened and with every reference resolved."""
    store_src = _strip_comments(open(STORE_LIGHT, encoding="utf-8").read())
    mkt_src = _strip_comments(open(MARKETPLACE_LIGHT, encoding="utf-8").read())

    ctas = {
        name: _parse_object(_object_body(store_src, name))
        for name in ("STORE_CTA_PULSESOC", "STORE_CTA_REFERENCE")
    }
    # `STORE_CTA` is an alias, and which one it points at is the documented
    # swap between the PulseSoc mint and the reference design's yellow.
    alias = re.search(r"export\s+const\s+STORE_CTA\s*=\s*(\w+)\s*;", store_src)
    assert alias, "STORE_CTA is no longer a simple alias; teach this test the new shape"
    ctas["STORE_CTA"] = ctas[alias.group(1)]

    flat = {}
    flat.update({"storeLight." + k: v
                 for k, v in _parse_object(_object_body(store_src, "storeLight")).items()})
    flat.update({"marketplaceLight." + k: v
                 for k, v in _parse_object(_object_body(mkt_src, "marketplaceLight")).items()})

    # `cta: STORE_CTA` puts an object where a leaf is expected. Expand it so
    # `storeLight.cta.from` is addressable the way the CSS comment claims.
    for path, raw in list(flat.items()):
        if raw in ctas:
            for key, value in ctas[raw].items():
                flat["%s.%s" % (path, key)] = value

    def resolve(path, seen=()):
        assert path not in seen, "reference cycle at %s" % path
        raw = flat[path]
        literal = re.fullmatch(r'"([^"]*)"', raw)
        if literal:
            return literal.group(1)
        dotted = re.fullmatch(r"(\w+)\.([\w.]+)", raw)
        assert dotted, "cannot resolve %s = %r" % (path, raw)
        head, rest = dotted.group(1), dotted.group(2)
        if head in ctas:
            return re.fullmatch(r'"([^"]*)"', ctas[head][rest]).group(1)
        target = "%s.%s" % (head, rest)
        assert target in flat, "%s points at %s, which does not exist" % (path, target)
        return resolve(target, seen + (path,))

    def is_colour_leaf(path):
        raw = flat[path]
        # `cta: STORE_CTA` is an object, expanded into `cta.from` / `cta.to` /
        # `cta.text` just above; the bare parent is not a colour itself.
        if raw in ctas:
            return False
        # Radii, gutters, aspect ratios and border widths live in these files
        # too. They are numbers, not colours, and nothing here maps to them.
        return not re.fullmatch(r"-?[\d.]+", raw)

    return {p: resolve(p) for p in flat if is_colour_leaf(p)}


#: Values in the native theme that are references rather than string literals.
#: A literal-only parser drops these silently; see failure mode 1.
REFERENCE_VALUED = (
    "storeLight.accent.brand",
    "storeLight.accent.brandOnLight",
    "storeLight.cta.from",
    "storeLight.cta.to",
    "storeLight.cta.text",
    "marketplaceLight.badge.featuredBg",
    "marketplaceLight.badge.featuredText",
)

#: Every `--store-*` token in the stylesheet that is a straight transcription,
#: mapped to the native path it was copied from.
TRANSCRIBED = {
    "--store-bg-page": "storeLight.bg.page",
    "--store-bg-card": "storeLight.bg.card",
    "--store-bg-header": "storeLight.bg.headerFrom",
    "--store-bg-strip": "storeLight.bg.strip",
    "--store-bg-warning": "storeLight.bg.warning",
    "--store-bg-skeleton": "storeLight.bg.skeleton",
    "--store-border-hairline": "storeLight.border.hairline",
    "--store-border-secondary": "storeLight.border.secondaryButton",
    "--store-border-warning": "storeLight.border.warning",
    "--store-text-primary": "storeLight.text.primary",
    "--store-text-muted": "storeLight.text.muted",
    "--store-text-link": "storeLight.text.link",
    "--store-text-link-active": "storeLight.text.linkActive",
    "--store-text-on-dark": "storeLight.text.onDark",
    "--store-text-on-dark-muted": "storeLight.text.onDarkMuted",
    "--store-status-success": "storeLight.status.success",
    "--store-status-warning": "storeLight.status.warning",
    "--store-status-error": "storeLight.status.error",
    "--store-accent-brand": "storeLight.accent.brand",
    "--store-accent-on-light": "storeLight.accent.brandOnLight",
    "--store-accent-star": "storeLight.accent.star",
    "--store-cta-from": "storeLight.cta.from",
    "--store-cta-to": "storeLight.cta.to",
    "--store-cta-text": "storeLight.cta.text",
    "--store-select-fill": "storeLight.select.selected",
    "--store-select-border": "storeLight.select.selectedBorder",
    "--store-banner-from": "marketplaceLight.savedSearch.from",
    "--store-badge-featured-bg": "marketplaceLight.badge.featuredBg",
    "--store-badge-featured-text": "marketplaceLight.badge.featuredText",
    "--store-badge-new-bg": "marketplaceLight.badge.newBg",
    "--store-badge-new-text": "marketplaceLight.badge.newText",
    "--store-badge-sold-overlay": "marketplaceLight.badge.soldOverlay",
}

#: Tokens with no native equivalent, each declared as `rgb channels of <native
#: path>` at a stated alpha. React Native has no cascade, so the app builds the
#: same steps from opaque greys instead -- but the *hue* still has to come from
#: the app, which is what this half of the gate pins. A derived token is allowed
#: to exist; it is not allowed to invent a colour.
DERIVED = {
    "--store-wash-faint": ("storeLight.text.primary", 0.03),
    "--store-wash-soft": ("storeLight.text.primary", 0.05),
    "--store-wash": ("storeLight.text.primary", 0.08),
    "--store-wash-strong": ("storeLight.text.primary", 0.16),
    "--store-badge-scrim": ("marketplaceLight.badge.soldOverlay", 0.82),
    "--store-error-wash": ("storeLight.status.error", 0.08),
}

#: Bumping this is the deliberate act of classifying a new token.
EXPECTED_TOKEN_COUNT = 38


def _css_tokens():
    css = open(CSS_PATH, encoding="utf-8").read()
    return dict(re.findall(r"(--store-[a-z0-9-]+)\s*:\s*([^;]+);", css))


def _rgb(value):
    """(r, g, b) from `#rrggbb` or `rgba(r, g, b, a)`."""
    value = value.strip().lower()
    hex_match = re.fullmatch(r"#([0-9a-f]{6})", value)
    if hex_match:
        digits = hex_match.group(1)
        return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))
    rgba = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,[^)]*)?\)", value)
    assert rgba, "cannot read a colour out of %r" % value
    return tuple(int(rgba.group(i)) for i in (1, 2, 3))


def _alpha(value):
    match = re.fullmatch(r"rgba\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\s*\)", value.strip().lower())
    assert match, "%r is not an rgba() with an alpha" % value
    return float(match.group(1))


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #

def test_the_theme_parser_resolves_references_rather_than_dropping_them():
    """Guards the gate itself, not the palette.

    If `_load_theme` regresses to reading string literals only, every
    assertion below would keep passing while silently checking fewer tokens --
    including `--store-cta-from` and `--store-cta-to`, the two halves of the
    primary buy button. These are the keys that are references today.
    """
    theme = _load_theme()
    for path in REFERENCE_VALUED:
        assert path in theme, (
            f"{path} was dropped by the parser. Its value in the native theme "
            "is a reference, not a string literal, so a literals-only regex "
            "skips it -- and a skipped token is one this gate stops checking.")
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", theme[path]), (
            f"{path} resolved to {theme[path]!r}, which is not a colour")


def test_every_store_token_is_classified():
    """No token may exist in the stylesheet without being accounted for here."""
    tokens = _css_tokens()
    assert len(tokens) == EXPECTED_TOKEN_COUNT, (
        f"the stylesheet declares {len(tokens)} --store-* tokens, this gate "
        f"expects {EXPECTED_TOKEN_COUNT}. Classify the change as transcribed or "
        "derived and update the tables in this file.")
    unmapped = set(tokens) - set(TRANSCRIBED) - set(DERIVED)
    assert not unmapped, (
        "these tokens are neither transcribed from the native theme nor "
        f"declared as derived: {sorted(unmapped)}")
    overlap = set(TRANSCRIBED) & set(DERIVED)
    assert not overlap, f"classified twice: {sorted(overlap)}"
    missing = (set(TRANSCRIBED) | set(DERIVED)) - set(tokens)
    assert not missing, (
        f"this gate guards tokens the stylesheet no longer declares: {sorted(missing)}")


def test_transcribed_tokens_equal_the_native_theme():
    """The actual parity check: web value == app value, token by token."""
    theme = _load_theme()
    tokens = _css_tokens()
    drift = []
    for token, path in sorted(TRANSCRIBED.items()):
        assert path in theme, (
            f"{token} claims to come from {path}, which the native theme no "
            "longer defines")
        want = " ".join(theme[path].split()).lower()
        got = " ".join(tokens[token].split()).lower()
        if want != got:
            drift.append(f"{token}: css has {got}, {path} is {want}")
    assert not drift, (
        "the web storefront's palette has drifted from the app's. The app is "
        "the authority -- change the CSS, not the theme:\n  " + "\n  ".join(drift))


def test_derived_tokens_take_their_hue_from_the_app():
    """A derived token may add transparency. It may not invent a colour."""
    theme = _load_theme()
    tokens = _css_tokens()
    wrong = []
    for token, (path, alpha) in sorted(DERIVED.items()):
        assert path in theme, f"{token} derives from {path}, which no longer exists"
        if _rgb(tokens[token]) != _rgb(theme[path]):
            wrong.append(
                f"{token} is {tokens[token].strip()} but derives from {path} "
                f"({theme[path]}), whose channels are {_rgb(theme[path])}")
        elif abs(_alpha(tokens[token]) - alpha) > 1e-9:
            wrong.append(
                f"{token} is declared here at alpha {alpha} but the stylesheet "
                f"has {_alpha(tokens[token])}")
    assert not wrong, (
        "a derived token no longer matches what it claims to derive from:\n  "
        + "\n  ".join(wrong))


def test_the_storefront_palette_is_light_not_dark():
    """The one assertion that would have caught the original divergence.

    Everything above compares the web to the app and would stay green if both
    went dark together. This pins the property the whole rebuild was for: the
    page is lighter than the text drawn on it. `storeLight.ts` opens by calling
    this surface the one deliberately light screen in a dark app, so if this
    ever inverts, the transcription was pointed at the wrong palette.
    """
    tokens = _css_tokens()

    def luminance(value):
        channels = []
        for raw in _rgb(value):
            srgb = raw / 255
            channels.append(srgb / 12.92 if srgb <= 0.04045
                            else ((srgb + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    page = luminance(tokens["--store-bg-page"])
    card = luminance(tokens["--store-bg-card"])
    text = luminance(tokens["--store-text-primary"])
    assert card >= page > text, (
        "the storefront is no longer a light surface: page luminance "
        f"{page:.3f}, card {card:.3f}, primary text {text:.3f}")

    # And the body text on the page still clears the 4.5:1 floor -- the check
    # that caught `--muted` resolving to the shell's dark grey on the light page.
    contrast = (max(page, text) + 0.05) / (min(page, text) + 0.05)
    assert contrast >= 4.5, (
        f"primary text on the page measures {contrast:.2f}:1, under the 4.5:1 "
        "body-text floor")


#: Tokens the native theme defines for a stroke or a fill, never for type.
#:
#: `storeLight.accent.brandOnLight` carries its own reason: it exists so "the
#: active tab underline stays as visible as the orange it replaces without
#: introducing a third green". It is a 2px rule on a white card. As 12px bold
#: text on `bg.card` the same value measures 2.25:1, and on `select.fill`
#: 2.02:1 -- both well under the 4.5:1 floor.
STROKE_ONLY = ("--store-accent-on-light", "--mkt-accent")

#: What to reach for instead. `storeLight.status.success` is commented "In
#: stock, store open, positive trend" and measures 5.11:1 on the card.
STROKE_ONLY_REMEDY = "--store-status-success"


def _declarations(css):
    """(property, value) for every declaration, comments removed first.

    Stripping comments is not optional here: this file's own prose names the
    tokens it bans and quotes `color: var(--mkt-accent)` outright, so a scan of
    the raw text would read the explanation as the thing it warns about. The
    same trap `_strip_comments` exists for above, and the same one that made a
    literal `:root` inside a comment swallow a whole block in
    `tests/web_parity/test_design_tokens.py`.
    """
    return re.findall(r"([a-z-]+)\s*:\s*([^;{}]+);", _strip_comments(css))


#: Properties that describe a box rather than paint one. `background`,
#: `color`, `color-scheme` and the custom properties are deliberately absent:
#: those are the whole reason the document element shares a block with `.mkt`.
BOX_PROPERTIES = (
    "margin", "padding", "width", "height", "display", "gap", "position",
    "top", "right", "bottom", "left", "inset", "float", "grid", "flex",
    "border-radius", "columns", "contain",
)


def _rule_blocks(css):
    """(selector list, declaration body) for every block in the stylesheet.

    An `@media` prelude falls out as a selector of its own, which is harmless
    here: nothing inside one is spelled `body.mkt-public` as a whole selector,
    and a block nested in one still matches on its own because declaration
    bodies contain no braces.
    """
    return re.findall(r"([^{}]*)\{([^{}]*)\}", _strip_comments(css))


def test_the_public_document_element_inherits_the_palette_and_not_the_layout():
    """The bug this rule exists for shipped inside this branch.

    `public_document` sets `mkt-public` on `<body>` so the signed-out
    storefront -- which has no member shell around it, and so no other source
    for `--store-*` -- resolves the same light palette the cards do. The first
    arrangement did that by adding the body to `.mkt`'s selector list, and
    `.mkt` is not only a palette: it is also the content slab, with
    `padding:16px` and, at the time, `margin-inline:-16px`.

    So the document element picked up a -16px inline margin and started at
    x=-16. Measured at 390px the slab then landed at left=-4 with 28px of dead
    space on its right -- and `html,body{overflow-x:hidden}` in the public
    document's own base rules meant no scrollbar appeared to say so. The page
    was simply off-centre at every mobile width, which is the failure mode a
    screenshot shows and a passing suite does not.

    This is deliberately not a check on any particular value. It asserts the
    separation: a document element and a content slab may share colours and
    must not share a box.
    """
    css = open(CSS_PATH, encoding="utf-8").read()
    offenders = []
    for selector, body in _rule_blocks(css):
        parts = [part.strip() for part in selector.split(",")]
        if "body.mkt-public" not in parts:
            continue
        for prop, value in re.findall(r"([a-z-]+)\s*:\s*([^;{}]+);", body):
            if prop.startswith("--"):
                continue
            if any(prop == name or prop.startswith(name + "-")
                   for name in BOX_PROPERTIES):
                offenders.append(f"{selector.strip()} {{ {prop}: {value.strip()} }}")

    assert not offenders, (
        "body.mkt-public is being given layout, not just a palette: %s. The "
        "class exists so the public document can resolve the --store-* tokens; "
        "put box properties on .mkt, which is the slab inside the document, "
        "and not on the document itself." % "; ".join(offenders))


def test_the_public_document_really_does_take_the_palette_from_that_block():
    """Pairs with the test above so it cannot pass by deleting the selector.

    Dropping `body.mkt-public` from the palette block would satisfy the rule
    above perfectly -- and would serve every signed-out shopper a storefront
    with no `--store-*` defined at all, falling back through ~330 `var()`
    fallbacks to whatever the dark layer left behind.
    """
    css = open(CSS_PATH, encoding="utf-8").read()
    declared = [
        body for selector, body in _rule_blocks(css)
        if "body.mkt-public" in [part.strip() for part in selector.split(",")]
    ]
    assert declared, (
        "no block declares anything for body.mkt-public, so the signed-out "
        "document resolves none of the storefront palette")
    tokens = {
        name
        for body in declared
        for name in re.findall(r"(--store-[a-z0-9-]+)\s*:", body)
    }
    assert len(tokens) == EXPECTED_TOKEN_COUNT, (
        "body.mkt-public sees %d of the %d --store-* tokens. It has to see the "
        "whole palette: it is the only declaration the public document gets."
        % (len(tokens), EXPECTED_TOKEN_COUNT))


def test_a_stroke_token_is_never_used_as_a_text_colour():
    """The bug this rule exists for shipped, and only a render caught it.

    `.mkt-card-stock` -- the "In stock" line, the single most load-bearing
    word on a product card -- was `color: var(--mkt-accent)`. On the dark
    storefront that mint was fine. Inverted onto a white card it measured
    2.25:1, the worst contrast on the discovery page, and nothing failed:
    every token still matched the app, because the *value* was never wrong.
    Only the use was. Three `[data-mkt-added]` confirmations had the same
    bug for the same reason.

    So this is deliberately not another value check. It asserts a rule about
    *where* a value may appear, which is the class of defect a palette
    inversion actually produces and the one a token-parity suite cannot see.
    """
    css = open(CSS_PATH, encoding="utf-8").read()
    offenders = sorted({
        f"color: var({token})"
        for prop, value in _declarations(css)
        for token in STROKE_ONLY
        # `-webkit-text-fill-color` and plain `color` both paint glyphs;
        # `border-color`, `background`, `box-shadow` and `outline` do not.
        if prop in ("color", "-webkit-text-fill-color") and f"var({token})" in value
    })
    assert not offenders, (
        "a stroke-only token is painting text: %s. These are defined for a "
        "rule on a light card, not for type -- use var(%s), which the app uses "
        "for exactly this and which clears 4.5:1."
        % (", ".join(offenders), STROKE_ONLY_REMEDY))


def test_the_remedy_token_that_rule_points_at_really_is_readable():
    """Pairs with the test above so it cannot pass by naming a colour nobody checked.

    A ban is only half an instruction. If `--store-status-success` ever drifted
    to something as pale as the token it replaces, the test above would keep
    passing while sending every future author at an equally unreadable green.
    """
    tokens = _css_tokens()
    assert STROKE_ONLY_REMEDY in tokens, (
        "%s is gone, so the rule above now points at nothing" % STROKE_ONLY_REMEDY)

    def luminance(value):
        channels = []
        for raw in _rgb(value):
            srgb = raw / 255
            channels.append(srgb / 12.92 if srgb <= 0.04045
                            else ((srgb + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    remedy = luminance(tokens[STROKE_ONLY_REMEDY])
    # The card is the worst of the two light plates it is drawn on, being the
    # lighter; clearing it clears the page grey too.
    card = luminance(tokens["--store-bg-card"])
    contrast = (max(card, remedy) + 0.05) / (min(card, remedy) + 0.05)
    assert contrast >= 4.5, (
        f"{STROKE_ONLY_REMEDY} measures {contrast:.2f}:1 on --store-bg-card, "
        "so the token this file tells authors to use is itself unreadable")
