"""The commerce attachment card renders the same on every web surface.

``services/pulse_commerce_card.py`` and ``static/js/pulse_commerce_card.js`` are
the same renderer written twice, because ``/pulse/post/<id>`` is the SEO and
link-unfurl target and needs its card in the response body, while the home feed
and the reels lane build theirs in the browser from a paginated fetch loop. That
duplication is a standing invitation to drift: a field added to one, a gate
loosened in the other, and two surfaces start disagreeing about one listing in
one session — which is precisely the failure the shared availability vocabulary
exists to prevent.

This file is what makes the duplication safe, in three layers of decreasing
cleverness and increasing certainty:

1. **Structural parity.** Both sources are parsed — Python by ``ast``, JS by a
   quote-aware scanner — and the *set of payload fields each one reads* is
   compared. Both must also emit the same ``pulse-commerce-*`` class names and
   the same ``data-*`` attributes. Adding ``product.discount_label`` to one file
   fails here without anyone having to remember to extend a list.
2. **Differential execution.** When ``node`` is on the box, both renderers are
   run over the same fixtures and their output compared *byte for byte*. This is
   the real proof; the static checks are the part that still runs when it isn't.
3. **Behaviour.** The three gates (price visibility, state chip, CTA) asserted
   directly against the Python renderer over every availability code, plus the
   refusals: no price for a withdrawn product, no link for an unroutable one, no
   scheme in an ``href``, and no exception for a malformed overlay.

The field-name extraction is deliberately **default-deny**. An unrecognised
property access in the JS fails the test rather than being ignored, because the
alternative — an allowlist of payload keys maintained by hand — is a list that
goes stale in exactly the situation this file exists to catch.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

from services import pulse_commerce_card as card

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY_PATH = os.path.join(REPO, "services", "pulse_commerce_card.py")
JS_PATH = os.path.join(REPO, "static", "js", "pulse_commerce_card.js")
CSS_PATH = os.path.join(REPO, "static", "css", "pulse-commerce-attachment.css")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


PY_SOURCE = _read(PY_PATH)
JS_SOURCE = _read(JS_PATH)


# ---------------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------------


#: A ``/`` following one of these (ignoring whitespace) opens a regular
#: expression rather than being a division sign. The renderer contains
#: ``/[&<>"']/g``, whose character class holds both quote characters: read as
#: division, the scanner below enters a string at that ``"`` and every quote
#: for the rest of the file is parsed with inverted parity.
_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%~^<>") | {""}

#: How a reassembled span is re-delimited, so the output is still parseable
#: text rather than a run of bare bodies.
_DELIMITED = {"string": '"%s"', "regex": "/%s/"}


def _js_scan(source: str) -> list[tuple[str, str]]:
    """``(kind, text)`` spans of ``code``, ``string``, ``regex`` and ``comment``.

    One tokeniser for all three things this file needs to know about the JS,
    because each of them was previously done with a regex and each regex was
    wrong in a way that made an assertion weaker rather than louder.

    Comment removal cannot be a regex: the renderer's ``safeRoute`` compares
    against the literal ``"//"`` to reject protocol-relative URLs, so a naive
    line-comment strip deletes the second half of the anti-XSS gate and the
    test silently stops checking it. Finding string literals cannot be a regex
    either: alternating ``"..."|'...'`` mis-pairs the moment an apostrophe
    appears inside a double-quoted string, and the renderer is full of
    single-quoted HTML attributes inside double-quoted JS. And a regular
    expression literal has to be recognised as one, because the renderer holds
    ``/[&<>"']/g`` — read as division, the scanner opens a string at that
    ``"`` and every quote for the rest of the file is parsed with inverted
    parity.

    ``string`` and ``regex`` texts are the body, without the delimiters.
    """
    spans: list[tuple[str, str]] = []
    index = 0
    length = len(source)
    previous = ""
    code_start = 0

    def flush(end: int) -> None:
        if end > code_start:
            spans.append(("code", source[code_start:end]))

    while index < length:
        char = source[index]
        if char in ("'", '"', "`"):
            flush(index)
            closing = char
            index += 1
            body_start = index
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == closing:
                    break
                index += 1
            spans.append(("string", source[body_start:index]))
            index += 1
            code_start = index
            previous = closing
            continue
        if char == "/" and index + 1 < length and source[index + 1] == "/":
            flush(index)
            while index < length and source[index] != "\n":
                index += 1
            spans.append(("comment", ""))
            code_start = index
            continue
        if char == "/" and index + 1 < length and source[index + 1] == "*":
            flush(index)
            end = source.find("*/", index + 2)
            index = length if end == -1 else end + 2
            spans.append(("comment", ""))
            code_start = index
            continue
        if char == "/" and previous in _REGEX_PRECEDERS:
            flush(index)
            index += 1
            body_start = index
            in_class = False
            while index < length:
                inner = source[index]
                if inner == "\\":
                    index += 2
                    continue
                if inner == "[":
                    in_class = True
                elif inner == "]":
                    in_class = False
                elif inner == "/" and not in_class:
                    break
                index += 1
            spans.append(("regex", source[body_start:index]))
            index += 1
            code_start = index
            previous = "/"
            continue
        if not char.isspace():
            previous = char
        index += 1
    flush(length)
    return spans


JS_SPANS = _js_scan(JS_SOURCE)

#: Comments gone, literals intact. For the checks that read the emitted markup:
#: class names, ``data-`` attributes, the availability vocabulary.
JS_CODE = "".join(
    text if kind == "code" else _DELIMITED.get(kind, "") % text
    for kind, text in JS_SPANS
    if kind != "comment"
)

#: Comments gone and literals emptied. For property-name extraction only. A
#: property access never lives inside a string, but a string very much contains
#: text that looks like one: ``".pulse-commerce-chip-label"`` and
#: ``"commerce:pulsedrop.seller.visitStore"`` both read as ``.<name>`` to any
#: regex, and the extractor is default-deny, so each fails the parity assertion
#: as a field the Python twin does not read.
JS_CODE_NO_LITERALS = "".join(
    text if kind == "code" else _DELIMITED.get(kind, "") % ""
    for kind, text in JS_SPANS
    if kind != "comment"
)

#: Every string literal body in the renderer.
JS_STRINGS = frozenset(text for kind, text in JS_SPANS if kind == "string")


def test_the_javascript_scanner_sees_code_and_not_prose_or_literals():
    """Guard the guard: every assertion below is only as good as this scanner.

    Three ways it has been wrong or could be. It must not delete the ``"//"``
    inside ``safeRoute``; it must not mistake the ``"`` inside a regex character
    class for the start of a string; and the literal-blanking pass must actually
    empty literals while leaving real property accesses alone.
    """
    assert '"//"' in JS_CODE, "the comment stripper ate the protocol-relative gate"
    assert "Visit store" in JS_CODE, "a real string literal went missing"
    # Comments are gone in both passes.
    assert "the browser twin of" not in JS_CODE.lower()
    # ...and only comments. The regex literal's own body survives the code pass.
    assert "&amp;" in JS_CODE

    # The blanking pass keeps code and drops literal contents.
    assert "cover_image_url" in JS_CODE_NO_LITERALS, "a real property read was blanked"
    for literal in ("Visit store", "visitStore", "pulse-commerce-chip-label", "&amp;"):
        assert literal not in JS_CODE_NO_LITERALS, f"{literal!r} survived blanking"
    # Quote parity held to the end of the file, which is what the regex-literal
    # handling is for: a single missed one inverts everything after it.
    assert JS_CODE_NO_LITERALS.rstrip().endswith("})();")


def _python_payload_fields(source: str) -> set[str]:
    """Every ``<mapping>.get("name")`` in the module, from the AST.

    Parsed rather than grepped so the prose in the docstrings — which quotes
    ``post.get("commerce")`` verbatim — cannot contribute a field the code does
    not actually read.
    """
    fields: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "get":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            fields.add(first.value)
    return fields


#: Property accesses in the JS that are JavaScript, not payload. Anything not
#: here and not a payload key fails the test — see the module docstring on why
#: this list and not the inverse one.
_JS_HOST_PROPERTIES = frozenset(
    {
        "addEventListener",
        "appendChild",
        "charAt",
        "console",
        "createElement",
        "filter",
        "firstElementChild",
        "indexOf",
        "innerHTML",
        "join",
        "length",
        "push",
        "querySelector",
        "querySelectorAll",
        "readyState",
        "replace",
        "setAttribute",
        "slice",
        "textContent",
        "toLowerCase",
        "translateMarkedNodes",
        "trim",
        "warn",
    }
)


def _js_payload_fields(source: str) -> tuple[set[str], set[str]]:
    """``(payload fields, unrecognised accesses)`` for the stripped JS."""
    accessed = set(re.findall(r"\.([a-z][A-Za-z0-9_]*)\b", source))
    unknown = {name for name in accessed if name not in _JS_HOST_PROPERTIES}
    return unknown, accessed


def test_both_renderers_read_the_same_payload_fields():
    """A field added to one renderer and not the other fails here."""
    python_fields = _python_payload_fields(PY_SOURCE)
    js_fields, _ = _js_payload_fields(JS_CODE_NO_LITERALS)

    missing_from_js = python_fields - js_fields
    missing_from_python = js_fields - python_fields

    assert not missing_from_js, (
        "read by services/pulse_commerce_card.py but not by its JS twin: "
        f"{sorted(missing_from_js)}"
    )
    assert not missing_from_python, (
        "read by static/js/pulse_commerce_card.js but not by its Python twin "
        f"(or a new JS builtin needing to join _JS_HOST_PROPERTIES): "
        f"{sorted(missing_from_python)}"
    )
    # Guards the extraction itself: a regex that matched nothing would make the
    # two assertions above pass vacuously for the rest of time.
    assert "cover_image_url" in python_fields
    assert "cover_image_url" in js_fields
    assert len(python_fields) > 20


# ---------------------------------------------------------------------------
# The wire contract: clients may only read fields the server emits
# ---------------------------------------------------------------------------
#
# This is the class of bug that produced the missing thumbnail. The server emits
# ``product.cover_image_url``; the app's type declared ``image_url`` and the
# component read that, so the card rendered an empty square for every product
# PulseDrop has ever published — and the suite stayed green, because the
# fixtures invented the field name along with the value. A test written against
# a fixture cannot catch a fixture that is wrong about the wire.
#
# So the expected shape here is not written down. It is obtained by calling
# ``hydration.overlay()``, the function that actually builds the payload.

#: Renderer locals and the overlay container each one holds. ``post`` is absent
#: on purpose — it is the post dict, not part of the overlay.
_CONTAINER_ALIASES = {
    "product": "product",
    "seller": "seller",
    "label": "label",
    "cta": "cta",
    "availability": "availability",
    "attribution": "attribution",
    "declared": "availability",
    "commerce": "",
    "value": "",
    "item": "",
}

#: Fields a renderer may read that the current server does not emit, each with
#: the reason it is allowed to survive. Anything not listed here fails.
_DOCUMENTED_LEGACY_ALIASES = {
    ("product", "image_url"): "pre-2026-09-28 alias for cover_image_url; read as a fallback only",
}


def _resolve_receiver(node: ast.AST) -> str | None:
    """Which overlay container a ``.get()`` was called on, or ``None``.

    Handles the ``(commerce.get("cta") or {}).get("route")`` idiom as well as a
    plain local, because that spelling appears in the renderer and silently
    dropping it would exempt the CTA's route from the whole contract.
    """
    if isinstance(node, ast.Name):
        return _CONTAINER_ALIASES.get(node.id)
    if isinstance(node, ast.BoolOp) and node.values:
        return _resolve_receiver(node.values[0])
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        if node.args and isinstance(node.args[0], ast.Constant):
            parent = _resolve_receiver(node.func.value)
            if parent == "":
                return _CONTAINER_ALIASES.get(node.args[0].value, None)
    return None


def _fields_by_container(source: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "get" or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        container = _resolve_receiver(func.value)
        if container is None:
            continue
        result.setdefault(container, set()).add(first.value)
    return result


def _server_overlay() -> dict:
    """A real overlay, from the function that builds the real ones."""
    from services.pulsedrop import hydration

    return hydration.overlay(
        {
            "publication_id": 7,
            "post_id": 2510,
            "surface": "signal",
            "ref_listing_id": 112,
            "ref_seller_user_id": 1,
            "published_at": "2026-09-28T01:00:39",
            "label_key": "TRENDING",
            "id": 112,
            "title": "Aurora Desk Lamp",
            "price_minor": 4900,
            "currency": "USD",
            "quantity": 3,
            "product_type": "physical",
            "category": "home",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "buyer_visible": 1,
            "inventory_state": "in_stock",
            "store_name": "Northlight Studio",
            "username": "northlight",
        }
    )


def test_the_web_renderer_only_reads_fields_the_server_emits():
    """The missing-thumbnail bug, generalised into a gate.

    Every field the Python renderer reads out of the overlay must be a field
    ``hydration.overlay`` puts there. A typo, a guess or a stale name fails
    here, against the real builder rather than against a fixture.
    """
    payload = _server_overlay()
    read = _fields_by_container(PY_SOURCE)

    # Anti-vacuity: the extractor must have found the containers at all.
    assert {"product", "seller", "cta", "availability", ""} <= set(read)
    assert "cover_image_url" in read["product"]

    unknown: list[str] = []
    for container, fields in read.items():
        emitted = payload if container == "" else payload.get(container) or {}
        assert isinstance(emitted, dict), container
        for field in sorted(fields):
            if field in emitted:
                continue
            if (container, field) in _DOCUMENTED_LEGACY_ALIASES:
                continue
            unknown.append(f"{container or 'overlay'}.{field}")
    assert not unknown, (
        "the renderer reads fields hydration.overlay() does not emit -- this is "
        f"the cover_image_url bug happening again: {unknown}"
    )


def test_the_documented_legacy_aliases_are_still_only_aliases():
    """An alias must never be the only way a value is read.

    ``image_url`` is allowed to survive as a fallback. It is not allowed to
    become the primary read again, which is what the original bug was.
    """
    payload = _server_overlay()
    for (container, field), reason in _DOCUMENTED_LEGACY_ALIASES.items():
        assert field not in (payload.get(container) or {}), (
            f"{container}.{field} is emitted by the server now -- it is no "
            f"longer a legacy alias, so remove it from the allowlist ({reason})"
        )
    # The real name is read first: the fallback only applies when it is empty.
    item = overlay(
        product={"cover_image_url": "https://cdn.example/new.jpg", "image_url": "https://cdn.example/old.jpg"}
    )
    markup = card.card_html(item)
    assert "new.jpg" in markup
    assert "old.jpg" not in markup


def test_the_app_type_declares_the_shape_the_server_actually_sends():
    """The same contract for the React Native client.

    The app's ``PulseCommerceProduct`` declared ``image_url: string`` as a
    required field the server has never sent. Required fields in the type must
    exist in a real payload; optional ones (``?:``) are where a legacy alias is
    allowed to live.
    """
    ts_path = os.path.join(REPO, "mobile-native", "src", "api", "pulseCommerceOverlay.ts")
    if not os.path.exists(ts_path):
        pytest.skip("mobile-native is not checked out")
    source = _read(ts_path)
    payload = _server_overlay()

    blocks = {
        "product": "PulseCommerceProduct",
        "seller": "PulseCommerceSeller",
        "label": "PulseCommerceLabel",
        "cta": "PulseCommerceCta",
        "availability": "PulseCommerceAvailability",
    }
    missing: list[str] = []
    for container, type_name in blocks.items():
        match = re.search(
            r"export type " + type_name + r" = \{(.*?)\n\};", source, re.S
        )
        assert match, f"{type_name} not found in pulseCommerceOverlay.ts"
        body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)
        body = re.sub(r"//[^\n]*", "", body)
        emitted = payload.get(container) or {}
        for name, optional in re.findall(r"^\s{2}([a-z_]+)(\??):", body, re.M):
            if optional == "?":
                continue
            if name not in emitted:
                missing.append(f"{type_name}.{name}")
    assert not missing, (
        "the app declares required fields the server does not send, which is "
        f"how the thumbnail went missing: {missing}"
    )


#: A translation key the renderers are allowed to name: the overlay's own
#: namespace, dotted, with a leaf. ``commerce:pulsedrop`` alone is not one.
_I18N_NAMESPACE = re.compile(r"^commerce:pulsedrop(?:\.[A-Za-z][A-Za-z0-9]*){2,}$")


def _class_names(source: str) -> set[str]:
    return set(re.findall(r"pulse-commerce-[a-z-]+", source))


def test_both_renderers_emit_the_same_class_names():
    python_classes = _class_names(PY_SOURCE)
    js_classes = _class_names(JS_CODE)
    assert python_classes == js_classes, (
        f"only in Python: {sorted(python_classes - js_classes)}; "
        f"only in JS: {sorted(js_classes - python_classes)}"
    )
    assert "pulse-commerce-card" in python_classes
    assert len(python_classes) >= 10


def test_every_emitted_class_name_is_styled():
    """A class the renderers emit that the stylesheet never mentions.

    The card is invisible without its stylesheet, and the stylesheet is a third
    file that no compiler connects to the other two.
    """
    css = _read(CSS_PATH)
    styled = _class_names(css)
    emitted = _class_names(PY_SOURCE)
    unstyled = emitted - styled
    assert not unstyled, f"emitted but never styled: {sorted(unstyled)}"


def _data_attributes(source: str) -> set[str]:
    return set(re.findall(r"data-[a-z-]+", source))


def test_both_renderers_emit_the_same_data_attributes():
    python_attrs = _data_attributes(PY_SOURCE)
    js_attrs = _data_attributes(JS_CODE)
    assert python_attrs == js_attrs
    assert {"data-pulse-commerce", "data-listing-id", "data-availability"} <= python_attrs


def test_both_renderers_share_the_marketplace_availability_vocabulary():
    """Neither file may invent a third spelling of "sold out"."""
    for code in ("OUT_OF_STOCK", "UNAVAILABLE", "NOT_PRICED", "REMOVED"):
        assert code in PY_SOURCE, code
        assert code in JS_CODE, code
    # The Python side gets its stockless set from the lifecycle module rather
    # than re-spelling it; the JS cannot import, so its copy is checked here.
    from services.marketplace_listing_lifecycle import STOCKLESS_TYPES

    js_stockless = re.search(r"var STOCKLESS = \[([^\]]*)\]", JS_CODE)
    assert js_stockless, "STOCKLESS list not found in the JS renderer"
    assert set(json.loads("[" + js_stockless.group(1).replace("'", '"') + "]")) == set(
        STOCKLESS_TYPES
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def overlay(**changes):
    """A complete, routable overlay in the shape ``hydration.py`` emits."""
    base = {
        "pulsedrop": True,
        "surface": "signal",
        "publication_id": 7,
        "attribution": {
            "token": "pd1.7.signal.112",
            "publisher_role": "publisher",
            "merchant_role": "merchant",
            "listing_id": 112,
            "seller_user_id": 1,
            "surface": "signal",
        },
        "product": {
            "listing_id": 112,
            "title": "Aurora Desk Lamp",
            "price_label": "$49.00",
            "currency": "USD",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "buyer_visible": True,
            "inventory_state": "in_stock",
            "quantity": 4,
            "product_type": "physical",
            "denial_code": "",
            "route": "/pulse/marketplace/112",
            "screen": "MarketplaceListing",
        },
        "seller": {
            "user_id": 1,
            "store_name": "Northlight Studio",
            "username": "northlight",
            "route": "/pulse/store/northlight",
            "screen": "Storefront",
        },
        "label": {"key": "TRENDING", "i18n_key": "x", "fallback": "Trending"},
        "cta": {
            "code": "VIEW_PRODUCT",
            "i18n_key": "x",
            "fallback": "View product",
            "enabled": True,
            "route": "/pulse/marketplace/112",
            "screen": "MarketplaceListing",
            "url": "https://pulsesoc.com/pulse/marketplace/112",
        },
        "availability": {"code": "", "i18n_key": "x", "fallback": "Available", "purchasable": True},
        "accessibility_text": "Trending. Aurora Desk Lamp, $49.00, from Northlight Studio.",
    }
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


def _blocked(code, *, fallback="Unavailable"):
    """An overlay in state ``code``, shaped the way the server would ship it.

    The server withholds the route for every state but available, so the
    fixture does too — otherwise the CTA assertions would be testing the
    availability gate while a route sat there making them pass for the wrong
    reason.
    """
    purchasable = code == ""
    return overlay(
        availability={"code": code, "fallback": fallback, "purchasable": purchasable},
        cta={"enabled": purchasable, "route": "/pulse/marketplace/112" if purchasable else ""},
    )


# ---------------------------------------------------------------------------
# The three gates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,price,state_chip,routable",
    [
        ("", True, False, True),
        ("OUT_OF_STOCK", True, True, False),
        ("NOT_PRICED", True, True, False),
        ("UNAVAILABLE", False, True, False),
        ("REMOVED", False, True, False),
    ],
)
def test_the_three_gates_for_every_availability_code(code, price, state_chip, routable):
    item = _blocked(code, fallback="State text")
    assert card.availability_block(item) == code
    assert card.price_visible(item) is price
    assert card.cta_enabled(item) is routable

    markup = card.card_html(item)
    assert ("$49.00" in markup) is price
    assert ("State text" in markup) is state_chip
    assert ("pulse-commerce-cta" in markup) is routable
    # Unroutable states are static information, never a link to a 404.
    assert ("<a class='pulse-commerce-main'" in markup) is routable
    assert ("is-static" in markup) is not routable


def test_a_withdrawn_product_never_shows_a_price_even_when_one_is_shipped():
    """The disclosure rule, mirrored client-side rather than trusted.

    The server omits the price for a withdrawn listing. This asserts the
    renderer would still not show one if a future server regressed and sent it,
    because a price under a verified badge for a product the seller took off
    sale is the one thing this surface must not do.
    """
    for code in ("UNAVAILABLE", "REMOVED"):
        item = _blocked(code)
        assert item["product"]["price_label"] == "$49.00"
        markup = card.card_html(item)
        assert "$49.00" not in markup
        assert "49.00" not in markup


def test_the_accessible_name_cannot_leak_what_the_pixels_hide():
    """The disclosure rule is mirrored into ``aria-label``, not trusted there.

    This is the regression a reviewer cannot see. The card looked right — no
    price span for a withdrawn listing — while the ``aria-label`` it carried was
    the server's full sentence, price and all, so a screen-reader user was told
    the price of a product the seller had taken off sale and a sighted user
    could not. The renderer now rebuilds the sentence from the parts it is
    actually showing whenever the price is withheld.
    """
    for code in ("UNAVAILABLE", "REMOVED"):
        item = _blocked(code, fallback="No longer available")
        item["accessibility_text"] = "Trending. Aurora Desk Lamp, $49.00, from Northlight Studio."
        markup = card.card_html(item)
        assert "$49.00" not in markup
        label = re.search(r"aria-label='([^']*)'", markup).group(1)
        assert "49" not in label
        # Still a usable sentence, not an empty attribute.
        assert "Aurora Desk Lamp" in label
        assert "No longer available" in label


def test_the_servers_sentence_is_kept_when_the_price_is_disclosed():
    """The rebuild is a fallback for withheld states, not a replacement.

    The server writes a better sentence than a join of three fields, so it is
    still the one used everywhere it is allowed to be.
    """
    for code in ("", "OUT_OF_STOCK", "NOT_PRICED"):
        item = _blocked(code, fallback="State text")
        markup = card.card_html(item)
        label = re.search(r"aria-label='([^']*)'", markup).group(1)
        assert label == item["accessibility_text"]


def test_an_unknown_availability_code_is_derived_and_not_trusted():
    """A newer or older server must not get a sold-out product an enabled button."""
    item = overlay(
        availability={"code": "SOME_FUTURE_STATE"},
        product={"quantity": 0, "inventory_state": "out_of_stock"},
    )
    assert card.availability_block(item) == "OUT_OF_STOCK"
    assert card.cta_enabled(item) is False


def test_an_available_cta_with_no_route_is_still_not_a_link():
    """The route gate, isolated from the availability gate.

    The server declines to emit a route for every unroutable state, so in any
    realistic payload these two gates agree and either one alone would look
    sufficient. This is the case where they disagree — the server says available
    and enabled but ships no route — and it exists because ``cta_enabled``
    claims to refuse to trust that the server always will. Without it a mutation
    deleting the route check survives the whole suite.
    """
    item = overlay(cta={"enabled": True, "route": ""})
    assert card.availability_block(item) == ""
    assert card.cta_enabled(item) is False
    markup = card.card_html(item)
    assert "<a class='pulse-commerce-main'" not in markup
    assert "is-static" in markup
    assert "pulse-commerce-cta" not in markup


def test_an_available_cta_flagged_disabled_is_not_a_link():
    """The flag gate, likewise isolated from the other two."""
    item = overlay(cta={"enabled": False, "route": "/pulse/marketplace/112"})
    assert card.availability_block(item) == ""
    assert card.cta_enabled(item) is False
    assert "is-static" in card.card_html(item)


def test_a_stockless_product_type_is_not_sold_out_at_quantity_zero():
    item = overlay(
        availability={"code": "SOME_FUTURE_STATE"},
        product={"quantity": 0, "product_type": "digital", "inventory_state": ""},
    )
    assert card.availability_block(item) == ""


def test_a_label_with_no_amount_derives_not_priced():
    item = overlay(availability={"code": "?"}, product={"price_label": "Ask for a quote"})
    assert card.availability_block(item) == "NOT_PRICED"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route",
    ["javascript:alert(1)", "//evil.example/x", "https://evil.example/x", "", "   ", None],
)
def test_a_route_with_a_scheme_never_reaches_an_href(route):
    assert card._safe_route(route) == ""
    item = overlay(cta={"route": route}, seller={"route": route})
    markup = card.card_html(item)
    assert "javascript:" not in markup
    assert "evil.example" not in markup
    assert "pulse-commerce-seller" not in markup


def test_markup_from_a_hostile_listing_title_is_escaped():
    item = overlay(product={"title": "<script>alert('x')</script>"})
    markup = card.card_html(item)
    assert "<script>" not in markup
    assert "&lt;script&gt;" in markup


def test_an_attribute_value_cannot_break_out_of_its_quotes():
    """The markup uses single-quoted attributes, so ``'`` must be escaped."""
    item = overlay(attribution={"token": "a' onmouseover='alert(1)"})
    markup = card.card_html(item)
    assert "onmouseover='alert" not in markup
    assert "&#x27;" in markup


@pytest.mark.parametrize(
    "value",
    [None, {}, [], "", 0, {"pulsedrop": True}, {"product": {}}, {"pulsedrop": 1, "product": {}}],
)
def test_a_post_without_a_usable_overlay_renders_nothing_and_does_not_raise(value):
    """Section 77: a post whose commerce hydration failed still renders as a post."""
    assert card.card_html(value) == ""
    assert card.post_card_html({"commerce": value}) == ""


def test_post_card_html_tolerates_a_post_that_is_not_a_mapping():
    assert card.post_card_html(None) == ""
    assert card.post_card_html("a post") == ""


def test_an_overlay_missing_every_optional_branch_still_renders():
    item = {"pulsedrop": True, "product": {"listing_id": 9}}
    markup = card.card_html(item)
    assert markup.startswith("<section class='pulse-commerce-card")
    assert "is-empty" in markup  # no image -> placeholder, not a broken <img>


def test_the_legacy_image_key_is_read_as_a_fallback():
    item = overlay(product={"cover_image_url": "", "image_url": "https://cdn.example/old.jpg"})
    assert "old.jpg" in card.card_html(item)


def test_the_reel_variant_is_opt_in_and_anything_unknown_reads_as_a_signal():
    assert "pulse-commerce-reel" in card.card_html(overlay(), surface="reel")
    assert "pulse-commerce-signal" in card.card_html(overlay(), surface="carousel")
    assert "pulse-commerce-signal" in card.card_html(overlay())


def test_the_renderer_never_reformats_a_price():
    """A client-side ``toFixed`` renders 4900 yen as $49.00 for a Japanese seller."""
    item = overlay(
        product={"price_label": "¥4,900", "currency": "JPY"},
        accessibility_text="Trending. Aurora Desk Lamp, ¥4,900, from Northlight Studio.",
    )
    markup = card.card_html(item)
    assert "¥4,900" in markup
    assert "49.00" not in markup
    assert "$" not in markup


_DOC_OWNERS = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _python_code_only(source: str) -> str:
    """The module with its comments and docstrings gone.

    The prose in this module discusses PulseDrop, hashtags and image URLs at
    length, and should — explaining what the renderer deliberately does not do
    is most of why it is trustworthy. What would be a bug is the *code* naming
    any of them, because that is how a renderer stops being a renderer and
    becomes a special case. ``ast.unparse`` drops comments for free; the
    docstring nodes are removed explicitly.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, _DOC_OWNERS) and ast.get_docstring(node) is not None:
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def _python_code_strings(source: str) -> set[str]:
    """Every string literal in the module except the docstrings."""
    return {
        node.value
        for node in ast.walk(ast.parse(_python_code_only(source)))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_the_renderer_is_not_pulsedrop_specific():
    """Section 4: forbidden is ``if author == "PulseDrop": renderProductCard()``.

    The card must render from the stored attachment identity, so that the day a
    human seller can attach a product to their own post it renders with no
    change here. Association by author, hashtag, title or image URL is exactly
    what section 47/48 rules out.
    """
    code_strings = _python_code_strings(PY_SOURCE)
    # Two permitted occurrences, neither of which is an identity test.
    #
    # ``pulsedrop`` alone is the payload's own discriminator key -- a field
    # name. ``commerce:pulsedrop.*`` is a translation-key namespace: the server
    # chose it, ships those keys inside the overlay, and the native card renders
    # through them, so the web renderer naming one is the renderer agreeing with
    # the payload rather than branching on who wrote the post. The ban this test
    # exists for -- ``if author == "PulseDrop"`` -- is unaffected, and any other
    # mention still fails.
    def permitted(value: str) -> bool:
        return value == "pulsedrop" or _I18N_NAMESPACE.match(value) is not None

    offenders = {
        value
        for value in code_strings
        if "pulsedrop" in value.lower() and not permitted(value)
    }
    assert not offenders, f"the code names PulseDrop outside a payload key: {offenders}"
    # Anti-vacuity: the exemption must not be so wide it permits an identity
    # test that merely happens to sit in a string.
    assert not permitted("PulseDrop")
    assert not permitted("pulsedrop_publications")
    assert not permitted("commerce:pulsedrop")
    assert permitted("commerce:pulsedrop.seller.visitStore")

    fields = _python_payload_fields(PY_SOURCE)
    for identifying in ("author", "author_name", "username", "caption", "hashtags", "body"):
        if identifying == "username":
            continue  # the seller's own handle, a display fallback for store_name
        assert identifying not in fields, f"renderer reads post identity field {identifying!r}"

    # No table, hashtag or URL matching in the code — prose may discuss all
    # three, and does.
    code_only = _python_code_only(PY_SOURCE)
    for banned in ("pulsedrop_publications", "hashtag", "author", "#"):
        assert banned not in code_only, f"the code references {banned!r}"
    # Anti-vacuity: the stripper must not have returned an empty module.
    assert "def card_html" in code_only
    assert "pulse-commerce-card" in code_only

    # And the JS twin holds the same line. ``pulsedrop`` itself is a property
    # read there (``value.pulsedrop === true``) and so is not among the string
    # literals at all; the anti-vacuity check is therefore on the scan finding
    # literals, not on finding that one.
    assert "Visit store" in JS_STRINGS, "the literal scan found nothing -- it has rotted"
    js_offenders = {
        value
        for value in JS_STRINGS
        if "pulsedrop" in value.lower() and not permitted(value)
    }
    assert not js_offenders, f"the JS twin names PulseDrop outside a payload key: {js_offenders}"
    assert "pulsedrop" in _js_payload_fields(JS_CODE_NO_LITERALS)[1], (
        "the discriminator is no longer read as a property -- if it moved into "
        "a string, the exemption above stopped being about a field name"
    )


def test_the_renderer_invents_no_shipping_rating_or_discount():
    """Section 114/115: nothing fabricated, and no ETA until Pulse ETA is trusted."""
    markup = card.card_html(overlay())
    for invented in ("Free Shipping", "delivery", "★", "rating", "% off", "Arrives"):
        assert invented.lower() not in markup.lower()


# ---------------------------------------------------------------------------
# Differential execution
# ---------------------------------------------------------------------------

_DIFFERENTIAL_CASES = [
    ("available", overlay(), "signal"),
    ("reel", overlay(), "reel"),
    ("out_of_stock", _blocked("OUT_OF_STOCK", fallback="Sold out"), "signal"),
    ("not_priced", _blocked("NOT_PRICED", fallback="Price on request"), "signal"),
    ("unavailable", _blocked("UNAVAILABLE"), "signal"),
    ("removed", _blocked("REMOVED", fallback="No longer available"), "reel"),
    ("derived", overlay(availability={"code": "FUTURE"}, product={"quantity": 0}), "signal"),
    ("hostile", overlay(product={"title": "<b>'x'</b>"}, cta={"route": "javascript:x"}), "signal"),
    ("protocol_relative", overlay(cta={"route": "//evil.example/x"}, seller={"route": "//evil.example"}), "signal"),
    ("leaky_sentence", _blocked("REMOVED", fallback="Gone"), "signal"),
    ("available_no_route", overlay(cta={"enabled": True, "route": ""}), "signal"),
    ("available_disabled", overlay(cta={"enabled": False}), "reel"),
    ("no_image", overlay(product={"cover_image_url": "", "image_url": ""}), "signal"),
    ("legacy_image", overlay(product={"cover_image_url": "", "image_url": "/i/old.jpg"}), "signal"),
    ("minimal", {"pulsedrop": True, "product": {"listing_id": 9}}, "signal"),
    ("empty", {}, "signal"),
]

_DRIVER = """
const fs = require("fs");
global.window = {};
global.document = undefined;
new Function(fs.readFileSync(process.argv[2], "utf8"))();
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = {};
for (const [name, commerce, surface] of cases) {
  out[name] = window.PulseCommerceCard.html(commerce, { surface: surface });
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_two_renderers_emit_identical_markup():
    """The real proof: same fixtures through both, compared byte for byte.

    The static checks above survive a box without node; this is the one that
    would catch a space in the wrong place or an attribute in a different order.
    """
    with tempfile.TemporaryDirectory() as workdir:
        driver = os.path.join(workdir, "driver.js")
        payload = os.path.join(workdir, "cases.json")
        with open(driver, "w", encoding="utf-8") as handle:
            handle.write(_DRIVER)
        with open(payload, "w", encoding="utf-8") as handle:
            json.dump(_DIFFERENTIAL_CASES, handle)
        result = subprocess.run(
            ["node", driver, JS_PATH, payload],
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == 0, result.stderr
    js_output = json.loads(result.stdout)

    assert set(js_output) == {name for name, _, _ in _DIFFERENTIAL_CASES}
    for name, commerce, surface in _DIFFERENTIAL_CASES:
        expected = card.card_html(commerce, surface=surface)
        assert js_output[name] == expected, f"renderers disagree on the {name!r} case"

    # Anti-vacuity: the comparison above is worthless if every case rendered "".
    assert sum(1 for value in js_output.values() if value) >= len(_DIFFERENTIAL_CASES) - 1
