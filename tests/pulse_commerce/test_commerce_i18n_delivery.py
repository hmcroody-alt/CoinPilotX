"""The commerce card must speak the reader's language on the web too.

The overlay ships a translation key beside every string it supplies and the
native card renders through the key. The web card read the English fallback and
nothing else, so a French member scrolling a French feed met an English badge
and an English button on the one element that was trying to sell them
something. There are four ways that stays broken after the renderer is fixed,
and each has a test here:

1. the key is emitted but absent from ``static/js/pulse_i18n.js``;
2. it is present but says something other than what the app says, so the same
   listing reads two ways depending on which surface opened it;
3. the English fallback baked into the markup drifts from the ``en`` catalogue
   entry, which makes the *untranslated* page the odd one out;
4. everything is translated and the composed ``aria-label`` is not, so the card
   shows one thing and announces another -- the half a sighted reviewer cannot
   see.

The last one is proved by running the two real files under ``node`` against a
minimal DOM (``commerce_i18n_harness.mjs``); the rest are read off the sources.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

from services import pulse_commerce_card as card
from services.pulsedrop import editorial, hydration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
I18N_PATH = os.path.join(REPO, "static", "js", "pulse_i18n.js")
CARD_JS_PATH = os.path.join(REPO, "static", "js", "pulse_commerce_card.js")
HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "commerce_i18n_harness.mjs")
NATIVE_CATALOGS = os.path.join(REPO, "mobile-native", "src", "i18n", "catalogs")

#: Key -> the English the renderer bakes into the markup as a fallback. Derived
#: from the modules that emit them, never restated: a key renamed in
#: ``hydration.py`` must fail here rather than quietly stop being checked.
EXPECTED: dict[str, str] = {
    **{label.i18n_key: label.fallback for label in editorial.LABELS.values()},
    **{
        hydration.CTA_I18N[code]: hydration.CTA_FALLBACK[code]
        for code in hydration.CTA_I18N
        if hydration.CTA_I18N[code]
    },
    **{
        hydration.AVAILABILITY_I18N[code]: hydration.AVAILABILITY_FALLBACK[code]
        for code in hydration.AVAILABILITY_I18N
        if hydration.AVAILABILITY_I18N[code]
    },
    card.SELLER_I18N_KEY: card.SELLER_FALLBACK,
}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _web_catalog() -> dict[str, dict[str, str]]:
    """``{language: {key: value}}`` as ``pulse_i18n.js`` ships it.

    Read by brace matching rather than by a flat regex over the file: the same
    key appears once per language and a flat scan would silently collapse the
    four blocks into one, which is exactly the shape of the bug -- a key present
    in ``en`` and missing everywhere else.
    """
    source = _read(I18N_PATH)
    start = source.index("const messages = {")
    catalog: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"^    ([a-z]{2}(?:-[a-z]+)?): \{$", source[start:], re.MULTILINE):
        opened = start + match.end() - 1
        depth = 0
        for index in range(opened, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    break
        block = source[opened : index + 1]
        catalog[match.group(1)] = dict(
            re.findall(r'"([^"\\]+)":\s*"((?:[^"\\]|\\.)*)"', block)
        )
    return catalog


def _native_catalog(language: str) -> dict[str, str]:
    """The app's ``extended`` catalogue for ``language``, keyed the wire's way.

    The app stores these nested under a top-level namespace; the wire key is
    ``namespace:dotted.path`` and the web catalogue stores that string flat.
    Flattening here is what lets the two be compared at all.
    """
    path = os.path.join(NATIVE_CATALOGS, language, "extended.json")
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    flat: dict[str, str] = {}

    def walk(node, namespace: str, prefix: str) -> None:
        for name, value in node.items():
            path_ = f"{prefix}.{name}" if prefix else name
            if isinstance(value, dict):
                walk(value, namespace, path_)
            elif isinstance(value, str):
                flat[f"{namespace}:{path_}"] = value

    for namespace, node in data.items():
        if isinstance(node, dict):
            walk(node, namespace, "")
    return flat


WEB_CATALOG = _web_catalog()


def test_the_key_set_under_test_is_derived_and_not_empty():
    """Anti-vacuity: every test below is a loop over ``EXPECTED``.

    If a rename emptied it, or the label table moved, the loops would pass by
    iterating over nothing.
    """
    assert len(EXPECTED) == 11, f"the emitted key set changed: {sorted(EXPECTED)}"
    assert EXPECTED["commerce:pulsedrop.seller.visitStore"] == "Visit store"
    assert EXPECTED["commerce:marketplace.outOfStock"] == "Out of stock"
    assert all(key.startswith("commerce:") and value for key, value in EXPECTED.items())
    assert set(WEB_CATALOG) >= {"en", "es", "fr", "ht"}, sorted(WEB_CATALOG)


@pytest.mark.parametrize("language", sorted(WEB_CATALOG))
def test_every_emitted_key_is_in_every_web_language(language):
    """A key the renderer emits and the catalogue lacks renders as the fallback.

    Silently, and in English -- ``t()`` returns the fallback rather than
    throwing, so the page looks finished.
    """
    missing = sorted(key for key in EXPECTED if key not in WEB_CATALOG[language])
    assert not missing, (
        f"static/js/pulse_i18n.js has no {language!r} copy for keys the commerce "
        f"card emits, so they render in English there: {missing}"
    )


def test_the_baked_in_fallback_matches_the_english_catalogue():
    """The markup's English and the catalogue's English must be one string.

    The fallback is what a crawler indexes and what an untranslated browser
    shows; the catalogue entry is what ``en`` readers get after the sweep. When
    they differ the text changes under a reader who never changed language.
    """
    drift = {
        key: (fallback, WEB_CATALOG["en"][key])
        for key, fallback in EXPECTED.items()
        if WEB_CATALOG["en"].get(key) != fallback
    }
    assert not drift, f"baked-in fallback vs en catalogue: {drift}"


@pytest.mark.parametrize("language", ["en", "es", "fr", "ht"])
def test_the_web_and_the_app_say_the_same_words(language):
    """Section 30. One listing must not read two ways across two surfaces.

    These are not two translations of one idea -- the web values were copied
    from the app's catalogue precisely so a member who saw a card in the app and
    then on the web sees the same sentence.
    """
    native = _native_catalog(language)
    disagreements = {
        key: (WEB_CATALOG[language][key], native[key])
        for key in EXPECTED
        if key in native and WEB_CATALOG[language].get(key) != native[key]
    }
    assert not disagreements, (
        f"the web and the app disagree in {language!r} (web, app): {disagreements}"
    )
    covered = [key for key in EXPECTED if key in native]
    assert len(covered) == len(EXPECTED), (
        "a key the web card emits is not in the app's catalogue at all, so the "
        f"comparison above skipped it: {sorted(set(EXPECTED) - set(covered))}"
    )


# ---------------------------------------------------------------------------
# Execution: the two real files, under node, against a minimal DOM
# ---------------------------------------------------------------------------


def _overlay(*, availability: str = "", price: bool = True) -> dict:
    """An overlay carrying the real keys, in the shape ``hydration.py`` emits."""
    label = editorial.LABELS[editorial.TRENDING]
    routable = availability == ""
    return {
        "pulsedrop": True,
        "surface": "signal",
        "publication_id": 7,
        "attribution": {"token": "pd1.7.signal.112", "listing_id": 112, "surface": "signal"},
        "product": {
            "listing_id": 112,
            "title": "Aurora Desk Lamp",
            "price_label": "$49.00" if price else "",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "route": "/pulse/marketplace/112",
        },
        "seller": {"store_name": "Northlight Studio", "route": "/pulse/store/northlight"},
        "label": {"key": label.key, "i18n_key": label.i18n_key, "fallback": label.fallback},
        "cta": {
            "code": hydration.CTA_VIEW_PRODUCT,
            "i18n_key": hydration.CTA_I18N[hydration.CTA_VIEW_PRODUCT],
            "fallback": hydration.CTA_FALLBACK[hydration.CTA_VIEW_PRODUCT],
            "enabled": routable,
            "route": "/pulse/marketplace/112" if routable else "",
        },
        "availability": {
            "code": availability,
            "i18n_key": hydration.AVAILABILITY_I18N.get(availability, ""),
            "fallback": hydration.AVAILABILITY_FALLBACK.get(availability, ""),
            "purchasable": routable,
        },
        # Absent on purpose for the withdrawn states: that is what makes the
        # renderer compose the sentence out of the chips, which is the case the
        # aria assertions are about.
        "accessibility_text": "Trending. Aurora Desk Lamp, $49.00." if routable else "",
    }


def _sold_out() -> dict:
    """Composed *and* showing a price -- the only shape where the two collide.

    A withdrawn listing has no price node to reach for, so it cannot prove the
    rebuild leaves the price out. Out-of-stock keeps its price, and an overlay
    that shipped no sentence of its own makes the renderer compose one, so this
    is the fixture where reaching for the wrong node is visible.
    """
    overlay = _overlay(availability=hydration.OUT_OF_STOCK)
    overlay["accessibility_text"] = ""
    return overlay


def _case(name: str, scenario: str, language: str, overlay: dict, surface: str) -> list:
    """A harness case. ``markup`` is the server's own render of the overlay.

    The two renderers are proved byte-identical next door, so handing the
    Python one's output to the harness is not a shortcut around the JS -- it is
    the served permalink's actual bytes, which is the thing the ``served``
    scenario is about.
    """
    return [name, scenario, language, overlay, surface, card.card_html(overlay, surface=surface)]


_WITHDRAWN = _overlay(availability=hydration.UNAVAILABLE, price=False)

_CASES = [
    _case("available_fr", "inserted", "fr", _overlay(), "signal"),
    _case("unavailable_fr", "inserted", "fr", _WITHDRAWN, "reel"),
    _case("unavailable_served_fr", "served", "fr", _WITHDRAWN, "signal"),
    _case("unavailable_switched_fr", "relanguage", "fr", _WITHDRAWN, "signal"),
    _case("sold_out_fr", "inserted", "fr", _sold_out(), "signal"),
    _case("available_en", "inserted", "en", _overlay(), "signal"),
]


def _run_harness() -> dict:
    with tempfile.TemporaryDirectory() as workdir:
        payload = os.path.join(workdir, "cases.json")
        with open(payload, "w", encoding="utf-8") as handle:
            json.dump(_CASES, handle)
        result = subprocess.run(
            ["node", HARNESS, I18N_PATH, CARD_JS_PATH, payload],
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def rendered():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    return _run_harness()


def test_the_harness_reaches_the_real_renderer(rendered):
    """Anti-vacuity: a DOM shim too weak to render would report ``None`` for all."""
    assert set(rendered) == {case[0] for case in _CASES}
    assert rendered["available_en"]["title"] == "Aurora Desk Lamp", (
        "the harness did not parse the renderer's markup -- every assertion "
        "below would be comparing None to None"
    )
    assert rendered["available_en"]["label"] == "Trending"
    assert rendered["available_en"]["price"] == "$49.00"
    assert rendered["unavailable_fr"]["price"] is None, (
        "the withdrawn card started showing a price, so the disclosure "
        "assertions below stopped being about anything"
    )


def test_an_inserted_card_is_translated(rendered):
    """The feed and reels path: appended after the sweep, translated by `localize`."""
    french = WEB_CATALOG["fr"]
    available = rendered["available_fr"]
    assert available["label"] == french["commerce:pulsedrop.label.trending"]
    assert available["cta"] == french["commerce:pulsedrop.cta.viewProduct"]
    assert available["seller"] == french[card.SELLER_I18N_KEY]
    assert available["title"] == "Aurora Desk Lamp", "the product's own name must not be translated"

    withdrawn = rendered["unavailable_fr"]
    assert withdrawn["state"] == french["commerce:pulsedrop.availability.unavailable"]
    assert withdrawn["cta"] is None, "a withdrawn listing must not offer a CTA"


@pytest.mark.parametrize("case", ["unavailable_fr", "unavailable_served_fr", "unavailable_switched_fr"])
def test_the_composed_sentence_is_announced_in_the_same_language(rendered, case):
    """What the card shows and what it announces must be one language.

    All three ways a card reaches a reader: appended into a live feed, served
    in the permalink's HTML and swept before this file even loads, and already
    on screen when the reader switches language.
    """
    french = WEB_CATALOG["fr"]
    result = rendered[case]
    expected = ". ".join(
        [
            french["commerce:pulsedrop.label.trending"],
            "Aurora Desk Lamp",
            french["commerce:pulsedrop.availability.unavailable"],
        ]
    )
    assert result["aria"] == expected
    for english in (EXPECTED["commerce:pulsedrop.label.trending"], "No longer available"):
        assert english not in result["aria"], (
            f"{case}: the card shows French and announces English -- {result['aria']!r}"
        )


def test_a_rebuilt_sentence_announces_exactly_what_the_card_shows(rendered):
    """The rebuild reads the card, so it cannot announce more or less than it.

    The price is the case that matters. ``card_html`` emits
    ``.pulse-commerce-price`` if and only if ``show_price``, so the node's
    presence *is* the disclosure decision and reading it back can never leak a
    withheld price. Both directions are asserted, because each is a real bug:
    a sold-out card shows its price and must say so, and a withdrawn one shows
    none and must not -- an accessible surface that discloses what the visual
    one hides is not more accessible, it is a second surface with a second
    contract.
    """
    french = WEB_CATALOG["fr"]

    sold_out = rendered["sold_out_fr"]
    assert sold_out["cta"] is None
    assert sold_out["price"] == "$49.00"
    assert sold_out["aria"] == ". ".join(
        [
            french["commerce:pulsedrop.label.trending"],
            "Aurora Desk Lamp",
            "$49.00",
            french["commerce:marketplace.outOfStock"],
        ]
    )

    for case in ("unavailable_fr", "unavailable_served_fr", "unavailable_switched_fr"):
        result = rendered[case]
        assert result["price"] is None, f"{case}: the fixture stopped withholding the price"
        assert "49" not in (result["aria"] or ""), (
            f"{case} announces a price the card itself withholds"
        )
        assert "Northlight" not in (result["aria"] or ""), (
            f"{case} reached a node wider than the card announces"
        )


def test_a_priced_card_does_not_keep_the_servers_english_sentence(rendered):
    """The common case: nothing is withheld, so the server shipped its prose.

    That sentence comes from ``editorial.accessibility_text``, which is English
    by construction -- its own docstring says it "cannot know the reader's
    language". Left alone it produces a card reading "Tendances / Voir le
    produit" that announces "Trending. Aurora Desk Lamp, $49.00." to VoiceOver,
    which is the same defect as an untranslated chip and invisible to everyone
    who can see the screen.
    """
    french = WEB_CATALOG["fr"]
    available = rendered["available_fr"]
    assert available["aria"] == ". ".join(
        [
            french["commerce:pulsedrop.label.trending"],
            "Aurora Desk Lamp",
            "$49.00",
        ]
    )
    assert EXPECTED["commerce:pulsedrop.label.trending"] not in available["aria"]
