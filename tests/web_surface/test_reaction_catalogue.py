"""The reaction catalogue is the only place a key-to-glyph decision may live.

Four independent tables used to make that decision -- two in ``bot.py``, two in
``static/js/pulse_home_core.js`` -- and they disagreed. Two consequences were
reproduced against a running server before these tests were written:

  * a real ``bullish`` reaction displayed as a **rocket**, because the feed's
    summary renderer had assigned ``bullish`` the same glyph the app uses for
    ``rocket``;
  * a real ``whale`` reaction displayed as a **thumbs-up**, because that
    renderer's table had no ``whale`` entry and fell through to a default.

Both are misreports of what a real person sent, which is why these tests exist.
They are deliberately written against *source text* rather than against a
rendered page: the failure mode is a second table being added back, and a
second table is invisible to any test that only checks what one page renders.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

import pytest

from services import pulse_reactions

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME_CORE = os.path.join(REPO, "static", "js", "pulse_home_core.js")
BOT = os.path.join(REPO, "bot.py")

#: Asset fingerprints: relative path -> (the `?v=` token bot.py serves it under,
#: sha256 of the file's bytes under that token).
#:
#: Static assets ship `Cache-Control: public, max-age=31536000, immutable`, so the
#: token *is* the identity of the file a returning browser holds. Editing one of
#: these files without bumping its token means a returning visitor keeps the old
#: copy for a year and never receives the change -- a fix that is live on the
#: server and absent from every existing browser. That has already happened twice
#: to `pulse_home_core.js`, which is why the pin exists.
#:
#: To change a pinned file: edit it, bump the token in bot.py, then update the
#: digest here. Updating the digest *without* bumping the token puts the bug back
#: -- the point of recording them together is that the diff shows you doing it.
CACHE_PINNED_ASSETS = {
    "static/css/admin_comms_ops.css": (
        "comms-ops-20261003c",
        "3d37d773889c1f5417fc4c3856f2397b78202823ff2d04a706046fcc150c7c04",
    ),
    "static/css/admin_ops_center.css": (
        "opsv2-20260914c",
        "7f28cbd39f65dd2753229c00125b2854ca1b622a2ac2c248ff3f75df32e7024f",
    ),
    "static/css/pulse-commerce-attachment.css": (
        "commerce-attachment-20260928a",
        "c06338609b3c3ed5cca95dc9a6afd6abebd0d12721fdc04d0f5e65927ac91c08",
    ),
    "static/css/pulse_app_cta.css": (
        "bare-asset-tokens-20260930a",
        "dd7e7ef3330df4d4e986fe30f730f6066ace7ff0e0f201f094d9e5ed84db6af3",
    ),
    "static/css/pulse_camera_engine.css": (
        "bare-asset-tokens-20260930a",
        "88b6369a0948d7b7dd9b4885820b3c8a801ae2232256735f03fbd322d0d9ecbd",
    ),
    "static/css/pulse_cinematic_media.css": (
        "static-bg-20260806a",
        "98035d84a5d8d6f93044e2a55373bfa63ac2f5e0c915fddabd3f92bfff7ecc71",
    ),
    "static/css/pulse_composer_premium.css": (
        "music-modal-20260621c",
        "55bfe05a13cfe8f72f5cd5f188f239c369fa557eeeebe50497d171bcef15452b",
    ),
    "static/css/pulse_design_system.css": (
        "cache-sweep-20260930a",
        "e2ede95b4f8c395304bcfb42a36582c902a2894f3205804ffe61265e83b7b668",
    ),
    # Joined the pin when the Apps-menu width fix went in. The markup was always
    # correct and only the rendered width was wrong, so a missed bump here is
    # invisible to every other check in this repo -- silent twice over.
    "static/css/pulse_desktop_feed.css": (
        "apps-menu-width-20260927a",
        "94bcf4e6cc11fd9a490bf8c0f41be6fc8be7ea0540e58c40bcdb9eb35ba828db",
    ),
    "static/css/pulse_desktop_shell.css": (
        "reaction-catalogue-20260927h",
        "401375580f970d043ded8b961b8bb9c583a300a5e63498f2300f46dd2a7c5c00",
    ),
    "static/css/pulse_home_os.css": (
        "desktop-dock-20260927a",
        "96fb6cc7716175a17ebe2696f4b61d565c1208557796f371c067264a854b3ca3",
    ),
    "static/css/pulse_live_studio.css": (
        "cache-sweep-20260928a",
        "38a99e03d858dcebb0e5912b74df1cfd96134b75ae0f45a9f3b342b30413d961",
    ),
    "static/css/pulse_messages_v2.css": (
        "messenger-desktop-nav-20260927a",
        "5be3c7b030dfbdf70fc5af7b8dac948328ffeca9426692e5c83b67a74ea966fd",
    ),
    "static/css/pulse_messenger_media_viewer.css": (
        "cache-sweep-20260930a",
        "b1ad05102509b862897b01d31ad05ea9a76eeb6349b8f679405009f4f29af53d",
    ),
    "static/css/pulse_mobile_system.css": (
        "bare-asset-tokens-20260930a",
        "49a26a16c99d2d30c9217f39ee5a33a4452a4fab28e33bd85c7886558a4ba6d2",
    ),
    "static/css/pulse_reaction_system.css": (
        "video-action-fit-20260927i",
        "6ff68ca37cb663b328b315dbdfcd7e63614e2dee96f996946ab2800eb18db50b",
    ),
    "static/css/pulse_reels_experience.css": (
        "reels-desktop-create-20260929a",
        "c95ed00ffbf98d11f7e7a592c8da166c80211caafbc53dfb43c54f05ba0dab9c",
    ),
    "static/css/pulse_sci_fi_system.css": (
        "cache-sweep-20260930a",
        "684f146c3b92f617ebc450763eaf201ff2d310d1eff8e55f1d57407a465663b5",
    ),
    "static/css/pulse_status_system.css": (
        "cache-sweep-20260930a",
        "63b2a0808303d6dc57a73c71f43146ce0c90262377e633bfe53f38e19c9d0921",
    ),
    "static/css/pulse_undx_action_center.css": (
        "undx-actions-20260910a",
        "99b422167935c3861e3e5e007777e6696c6945b4ea656f9a989517675c40e61d",
    ),
    # The worst of the thirteen: 126 insertions of drift, and it was referenced
    # from fifteen templates under a token six weeks older than bot.py's, so
    # browsers held two separately-cached copies and no single bump could reach
    # both. Unified here.
    "static/css/pulsesoc-tokens.css": (
        "cache-sweep-20260930a",
        "f01265e8f32a948729a463d51d1da7b730792541ca445ba574b3902d920d588a",
    ),
    "static/css/pulsesoc_global_call_overlay.css": (
        "fullscreen-incoming-20260704",
        "816191de56d96455e5e48c8fb25742ec46212c938d4b9fc3afb755d9919a82fc",
    ),
    "static/css/pulsesoc_intelligence_center.css": (
        "cache-sweep-20260930a",
        "d3613426dc60ab4cdb6f20857b704b271007b0f1134dff09c46d903757d2f79f",
    ),
    "static/css/pulsesoc_promotions.css": (
        "bare-asset-tokens-20260930a",
        "7313b11bdba2508a2069bc82ccbd314a905d946410023c1eb2d9caeb0bed6e1c",
    ),
    "static/js/admin_comms_ops.js": (
        "comms-ops-20261003b",
        "6ea9fbeefaf0143452021a80617396e3f45984cb4b14ba03aa72b36413a611cb",
    ),
    "static/js/admin_ops_center.js": (
        "opsv2-20260722i",
        "9c1dfbbef0332f46212a76565e1df6c60d9df078aac126982a8d6de7616e2de0",
    ),
    "static/js/pulse_ads_hooks.js": (
        "pulse-sci-fi-ads-20260626b",
        "edb876c680772f4296b970d31d82c70e17c9247110b261af9ff70e22644012df",
    ),
    "static/js/pulse_camera_engine.js": (
        "bare-asset-tokens-20260930a",
        "4b5988ca8dd27e0226bcf55031f852dbe1a9bfdf814ed0e9c7fa038df9ebcbb5",
    ),
    "static/js/pulse_commerce_card.js": (
        "commerce-i18n-20260929a",
        "49170d75d69795fcdfdf93a8d81ebe837414a0472987f9d0a64e0aa19f0a68c7",
    ),
    "static/js/pulse_delivery.js": (
        "1",
        "41c3decd708304c0868f0b6f4f99d2d308e0562ff59de4c9f78ed77678b0ae64",
    ),
    "static/js/pulse_emoji.js": (
        "emoji-primitive-20260927b",
        "289f19e250bba16f1c5e64c7a55333f9252f4a8de87b0c96831856cac1ffd0c7",
    ),
    "static/js/pulse_environment_engine.js": (
        "static-bg-20260806a",
        "34016c93804f65bfa1f23c117757f2c049aaa117b1982f13f08af92f85be7f11",
    ),
    # The file the pin exists for: it shipped undeliverable twice. Re-pinned for
    # the commerce-attachment web render (6f6526e9d), which did the
    # delivery-critical half right -- it moved bot.py to
    # ?v=commerce-attachment-20260928a in the same commit as the edit.
    #
    # Re-pinned again at `reaction-plural-20261001a` for the forward-port merge,
    # which routed the live reaction-count update through `reactionTotalLabel`
    # instead of a hand-built `${n} Reactions`. Worth the token on its own: the
    # bug it fixes is only visible to a browser that already holds this file,
    # which is exactly the population a stale token strands. Note the previous
    # token name was shared with `pulse-commerce-attachment.css` above -- that
    # file is unchanged here, so it deliberately keeps the old one. The two
    # assets are pinned independently; a shared name was a coincidence of the
    # commit that introduced them, not a coupling.
    "static/js/pulse_home_core.js": (
        "reaction-plural-20261001a",
        "b404254d904ecc7ce00ddab881aea04e51b23454df3837680e8d3bbeeffbe731",
    ),
    # `pulse_i18n.js` *is* the catalogue, so a returning browser holding last
    # week's copy has last week's words and renders an English chip beside a
    # French one from the same card. The only symptom is text in the wrong
    # language, which nothing else here can see.
    "static/js/pulse_i18n.js": (
        "commerce-i18n-20260929a",
        "347e07cf3b5c6df9815d12fc26f40add5c9d4efcdceb7b6afe3b55bb418d9b45",
    ),
    "static/js/pulse_media_picker.js": (
        "bare-asset-tokens-20260930a",
        "d4576671086e394f0b646502d2500fb13edb473c39e78e8a49d408a72b9f5827",
    ),
    "static/js/pulse_media_renderer.js": (
        "cache-sweep-20260930a",
        "2b8f78a12d5d938c110234ea9b96f3e0237489234bc4af1995ff3d8dcfca59e3",
    ),
    "static/js/pulse_messages_v2.js": (
        "emoji-primitive-20260927a",
        "ee9be24bd54cbea1ed20c18c09efa7fc6827c51ab90ec45b3076284a9faa94e0",
    ),
    "static/js/pulse_messenger_media_viewer.js": (
        "media-viewer-20260704a",
        "8773815b3881a23e7fc3a0fd8bdc807c988e0b3c1bd2a501ab6c19ad5a25488b",
    ),
    "static/js/pulse_pwa_install.js": (
        "cache-sweep-20260930a",
        "1075e13753e32b324ef746a0eb1ba1bf37716b188053bc1e9703a901b63b3a0a",
    ),
    "static/js/pulse_radio.js": (
        "pulse-radio-20260623a",
        "86cc0e5cdadfd0c6e082a4061ddc648d1aa34f2b1df8c3b22d814f2503c62f65",
    ),
    "static/js/pulse_reaction_system.js": (
        "cache-sweep-20260928a",
        "0ce98ae4b1ab16da5578e154253841dd2d7a4b348600a5f7d17701cff9b4ecbb",
    ),
    "static/js/pulse_realtime.js": (
        "cache-sweep-20260930a",
        "bbe5d8fcbfd94c90f299ebd91a17e2fec96aa831ea2945927d5f24d64d53b196",
    ),
    "static/js/pulse_search_bridge.js": (
        "cache-sweep-20260930a",
        "dd7d53183767b6e7f177a49032415aa7e74191d05442f01c69951b1c2b3d6d71",
    ),
    "static/js/pulse_status_viewer.js": (
        "status-v4-20260703b",
        "c1a109a8a7a7098e511ee97da03eafdc27ad37cf96a72445d24b8afd42419485",
    ),
    "static/js/pulse_upload_manager.js": (
        "cache-sweep-20260930a",
        "b711b0816cbdbadcdf1e51d78792e7156ba034c4203b01d01e261d5e6952f94c",
    ),
    "static/js/pulseshell_bridge.js": (
        "cache-sweep-20260930a",
        "d6dfa4bd27c573000815964c04e1adf076386c202912454582e4db8230c6b16b",
    ),
    "static/js/pulsesoc_cart.js": (
        "checkout-cta-verdict-20261003a",
        "2741ab267cf03b35b43bae1a5295961d8848cd0b4817a4caace22088e3e5682e",
    ),
    "static/js/pulsesoc_intelligence_center.js": (
        "cache-sweep-20260930a",
        "96f793669f3c28fd7f23469a1b00685d719750cef5ca1094a7fbd9a2db99f595",
    ),
    "static/js/pulsesoc_promotions.js": (
        "bare-asset-tokens-20260930a",
        "bf311ed0148573c65b44f4ab2dfdbb442d713168c478537a12723161272f5dfc",
    ),
    "static/js/time.js": (
        "bare-asset-tokens-20260930a",
        "1a6493a59ca495ce0c0a1cab1d966a642d91706df5c59a9c12c4a9f8bb765921",
    ),
}


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


TEMPLATES = os.path.join(REPO, "templates")
# Only an `src=`/`href=` attribute actually delivers a file. The same path also
# appears in `"..." not in html` dedup guards and in `script[src*="..."]` selectors,
# which are deliberately token-agnostic and must not be read as a bare-URL delivery.
ASSET_REF = re.compile(
    r"""(?:src|href)\s*=\s*['"]"""
    r"/static/((?:css|js)/[A-Za-z0-9_.-]+\.(?:css|js))(?:\?v=([\w.-]+))?"
)


def _asset_reference_sources() -> "dict[str, dict[str, set[str]]]":
    """Every `/static/...` css/js reference, mapped path -> token -> where it came from.

    bot.py is not the only place that links these files: `templates/` links them
    too, and the two drifted. `pulsesoc-tokens.css` was referenced 15 times from
    templates under a token six weeks older than the one bot.py served, which
    means two separately-cached copies of one file and no way to bump both at
    once. A token audit that reads only bot.py cannot see that.
    """
    sources: dict[str, dict[str, set[str]]] = {}
    files = [BOT]
    for root, _dirs, names in os.walk(TEMPLATES):
        files.extend(
            os.path.join(root, n)
            for n in names
            if n.endswith((".html", ".jinja", ".j2"))
        )
    for path in files:
        try:
            text = read(path)
        except (OSError, UnicodeDecodeError):
            continue
        where = os.path.relpath(path, REPO)
        for rel, token in ASSET_REF.findall(text):
            sources.setdefault("static/" + rel, {}).setdefault(token or "", set()).add(where)
    return sources


def test_no_two_reactions_share_a_glyph():
    """A shared glyph is indistinguishable to whoever reads the post.

    This is the exact shape of the rocket bug: ``bullish`` and ``rocket`` both
    rendered as U+1F680, so a reader could not tell which of the two a sender
    had actually chosen -- and the count they were shown belonged to the other.
    """
    by_glyph: dict[str, list[str]] = {}
    for key, emoji, _label in pulse_reactions.REACTION_CATALOG:
        by_glyph.setdefault(emoji, []).append(key)
    collisions = {emoji: keys for emoji, keys in by_glyph.items() if len(keys) > 1}
    assert not collisions, (
        "these reactions share a glyph, so the reader cannot tell them apart: "
        f"{collisions}"
    )


def test_every_accepted_reaction_has_a_glyph_and_label():
    """No key may be sendable but undisplayable.

    When a key reaches a renderer that has no entry for it, the renderer must
    invent something -- and inventing is how ``whale`` became a thumbs-up.
    """
    for key in sorted(pulse_reactions.REACTIONS):
        assert pulse_reactions.emoji_for(key), f"{key!r} is accepted on the wire but has no glyph"
        assert pulse_reactions.label_for(key) != key, f"{key!r} has no human label"


def test_the_wire_allowlist_is_derived_from_the_catalogue():
    """`REACTIONS` must not be a second, hand-maintained list.

    ``pulse_feed_engine.REACTIONS`` is what decides whether a reaction is
    accepted at all. If it is ever written out again as a literal it can drift
    from the catalogue, and the two failure directions are both silent: a key
    accepted with no glyph, or a key offered in the tray that the API rejects.
    """
    from services import pulse_feed_engine

    assert pulse_feed_engine.REACTIONS is pulse_reactions.REACTIONS, (
        "pulse_feed_engine.REACTIONS is no longer the catalogue's own frozenset. "
        "It must stay derived (`REACTIONS = pulse_reactions.REACTIONS`) so the "
        "allowlist and the display table cannot disagree."
    )


def test_glyphs_that_default_to_text_presentation_carry_fe0f():
    """Without U+FE0F these render as a thin monochrome outline.

    ``love`` is the one that matters -- it is the second entry in the tray and
    the most-used reaction on the platform. The catalogue was briefly written
    with a bare U+2764, which renders as a small black heart beside seventeen
    full-colour emoji.
    """
    text_default = {"❤", "\U0001f6e1"}
    for key, emoji, _label in pulse_reactions.REACTION_CATALOG:
        if emoji[0] in text_default:
            assert "️" in emoji, (
                f"{key!r} uses {emoji[0]!r}, whose default presentation is text. "
                "Append \\N{VARIATION SELECTOR-16} or it renders monochrome."
            )


def test_the_tray_offers_only_reactions_the_server_accepts():
    """Every tray key must survive the API's allowlist.

    The tray is what a click actually sends. A tray key outside `REACTIONS`
    produces a 400 on a control the product presents as working.
    """
    unknown = [key for key in pulse_reactions.TRAY_KEYS if key not in pulse_reactions.REACTIONS]
    assert not unknown, f"tray offers reactions the API will reject: {unknown}"
    assert len(pulse_reactions.TRAY_KEYS) == pulse_reactions.TRAY_SIZE


def test_the_server_renders_the_catalogue_into_the_page():
    """The browser must receive the catalogue, not carry its own copy.

    The placeholder has to be substituted in ``pulse_social_shell``'s replace
    chain. If the tag ships with the literal placeholder still in it, every
    client-side lookup silently returns nothing and every glyph disappears.
    """
    source = read(BOT)
    assert "window.PULSE_REACTION_CATALOG=__PULSE_REACTION_CATALOG__" in source, (
        "the reaction-catalogue <script> is gone from the shell"
    )
    assert '.replace("__PULSE_REACTION_CATALOG__", json.dumps(pulse_reactions.catalog_payload()))' in source, (
        "__PULSE_REACTION_CATALOG__ is emitted but never substituted, so the page "
        "would ship the literal placeholder and every reaction glyph would vanish"
    )
    # The `core` and `shell_only` boot profiles delete the whole
    # `data-pulse-shell-runtime` block. pulse_home_core.js -- installed by the
    # `core` profile and the renderer users actually get -- reads the catalogue,
    # so the catalogue must not be inside the block that gets deleted.
    runtime = re.search(r"<script data-pulse-shell-runtime>.*?</script>", source, re.S)
    assert runtime, "could not locate the shell runtime block"
    assert "PULSE_REACTION_CATALOG=__PULSE_REACTION_CATALOG__" not in runtime.group(0), (
        "the catalogue was moved inside <script data-pulse-shell-runtime>, which the "
        "`core` and `shell_only` boot profiles strip. pulse_home_core.js would then "
        "boot with no catalogue at all."
    )


def test_no_renderer_keeps_a_private_reaction_table():
    """Fails if a hardcoded key-to-emoji map reappears in a client renderer.

    This is the regression guard that actually matters. Both historic bugs came
    from a literal map, so the test looks for the *shape* of one: an object
    literal binding a known reaction key straight to an emoji.
    """
    keys = "|".join(sorted(pulse_reactions.REACTIONS))
    literal_map = re.compile(r"(?:%s)\s*:\s*[\"'][^\"']*[\U0001F000-\U0001FAFF☀-➿]" % keys)
    offenders = {}
    for path in (HOME_CORE, BOT):
        hits = sorted(set(literal_map.findall(read(path))))
        if hits:
            offenders[os.path.relpath(path, REPO)] = hits
    assert not offenders, (
        "a hardcoded reaction-to-emoji map is back: "
        f"{offenders}. Read window.PULSE_REACTION_CATALOG instead -- a second "
        "table is how a bullish reaction came to render as a rocket."
    )


def test_the_summary_chip_never_invents_reactions():
    """The zero state must not fabricate a set of reactions.

    ``reactionEmojis`` used to fall back to a six-key placeholder list when a
    post had no reactions. It was hidden by a CSS rule (`.is-zero`), so it was
    not visible in production -- but a display rule is the only thing that stood
    between six invented feelings and a post nobody had reacted to.
    """
    source = read(HOME_CORE)
    body = re.search(r"function reactionEmojis\(post\) \{(.*?)\n  \}", source, re.S)
    assert body, "reactionEmojis is gone or was reshaped; re-check this guard"
    assert "active.length ?" not in body.group(1), (
        "reactionEmojis has a placeholder branch again -- it renders reactions "
        "nobody sent. Show nothing when there are no reactions."
    )
    assert "count(counts[type]) > 0" in body.group(1), (
        "reactionEmojis must only show reactions with a real non-zero count"
    )


def test_the_legacy_tray_shows_every_reaction_that_has_a_count():
    """A real count must never be unreachable.

    ``reactionHtml`` built its strip from a 12-key list while the server
    accepted 18. A post whose only reactions were among the other six rendered
    a strip of zeroes, and the real counts appeared nowhere on the page.
    """
    source = read(BOT)
    body = re.search(r"function reactionHtml\(p\)\{(.*?)\n", source, re.S)
    assert body, "reactionHtml is gone or was reshaped; re-check this guard"
    assert "Object.keys(counts).filter" in body.group(1), (
        "reactionHtml no longer derives its strip from the counts it was given, "
        "so a reaction outside the tray list has nowhere to be displayed"
    )


@pytest.mark.parametrize("key,expected", [("bullish", "\U0001F4C8"), ("rocket", "\U0001F680"), ("whale", "\U0001F433")])
def test_the_two_reproduced_bugs_stay_fixed(key, expected):
    """Pins the three glyphs from the reproduction, by codepoint.

    ``bullish`` must not be the rocket, ``rocket`` must keep it, and ``whale``
    must have a glyph of its own rather than falling through to a thumbs-up.
    """
    assert pulse_reactions.emoji_for(key) == expected


def test_the_native_app_agrees_with_the_server_catalogue():
    """The app cannot read the injected catalogue, so its copy is checked here.

    ``PostCard.tsx`` has to carry its own table -- it is a React Native bundle,
    there is no page for the server to inject into. That makes it the one place
    a second table is unavoidable, and therefore the one place that needs a
    cross-language test. Without it the app and the web drift apart silently,
    which is the whole class of bug this catalogue exists to end.

    Note this is the *display* map, not the app's six-key tray. The tray being
    short is a deliberate product choice; the display map must be complete,
    because a phone renders posts carrying reactions sent from the web.
    """
    post_card = os.path.join(REPO, "mobile-native", "src", "components", "PostCard.tsx")
    source = read(post_card)
    block = re.search(r"const REACTION_EMOJI: Record<string, string> = \{(.*?)\n\};", source, re.S)
    assert block, "REACTION_EMOJI is gone from PostCard.tsx; the app has no display catalogue"
    native = dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", block.group(1)))

    assert native == pulse_reactions.EMOJI, (
        "mobile-native/src/components/PostCard.tsx has drifted from "
        "services/pulse_reactions.py. The same reaction would render as a "
        "different feeling on the app than on the web.\n"
        f"  only in the app:    {sorted(set(native) - set(pulse_reactions.EMOJI))}\n"
        f"  missing in the app: {sorted(set(pulse_reactions.EMOJI) - set(native))}\n"
        f"  different glyph:    "
        f"{sorted(k for k in set(native) & set(pulse_reactions.EMOJI) if native[k] != pulse_reactions.EMOJI[k])}"
    )


def test_the_app_summary_is_driven_by_counts_not_by_its_tray():
    """A reaction outside the app's six-key tray must still be displayed.

    ``reactionSummary`` used to filter the server's counts through the tray, so
    a post whose only reactions were ``whale`` or ``bullish`` -- both perfectly
    sendable from the web -- summarised as the empty-state heart on a phone.
    """
    post_card = os.path.join(REPO, "mobile-native", "src", "components", "PostCard.tsx")
    body = re.search(r"function reactionSummary\(counts: Record<string, number>\) \{(.*?)\n\}", read(post_card), re.S)
    assert body, "reactionSummary is gone or was reshaped; re-check this guard"
    assert "REACTIONS.filter" not in body.group(1), (
        "reactionSummary filters the server's counts through the six-key tray "
        "again, so reactions sent from the web read as 'no reactions' on a phone"
    )
    assert "Object.keys(counts" in body.group(1), (
        "reactionSummary must be driven by the counts the server sent"
    )


@pytest.mark.parametrize("relpath", sorted(CACHE_PINNED_ASSETS))
def test_a_changed_asset_must_carry_a_new_cache_token(relpath):
    """A renderer fix nobody receives is not a fix.

    The immutable year-long cache header means the `?v=` token decides which copy
    of this file a returning browser uses. The two are pinned together here so
    that changing one without the other is a red test rather than a silent
    non-delivery.
    """
    expected_token, expected_digest = CACHE_PINNED_ASSETS[relpath]

    by_token = _asset_reference_sources().get(relpath, {})
    assert by_token, f"{relpath} is no longer referenced by bot.py or templates/"
    untokenized = by_token.get("")
    assert not untokenized, (
        f"{relpath} is referenced without any ?v= token from "
        f"{sorted(untokenized)}. That copy is cached under a bare URL with a "
        "year-long immutable header, so no token bump can ever replace it."
    )
    tokens = set(by_token)
    assert len(tokens) == 1, (
        f"{relpath} is served under more than one token "
        + "; ".join(f"{t!r} from {sorted(w)}" for t, w in sorted(by_token.items()))
        + " -- the copies are cached separately, so bumping one leaves the other stale"
    )

    with open(os.path.join(REPO, relpath), "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    token = tokens.pop()

    if digest != expected_digest and token == expected_token:
        pytest.fail(
            f"{relpath} changed but still ships as ?v={token}. Every browser that "
            "has already loaded that token keeps its old copy for a year, so this "
            "change would never reach a returning visitor. Bump the token wherever "
            "it is referenced (bot.py *and* templates/), and record the new digest "
            f"in CACHE_PINNED_ASSETS: {digest}"
        )
    assert (token, digest) == (expected_token, expected_digest), (
        f"{relpath} is pinned to ?v={expected_token} in CACHE_PINNED_ASSETS but "
        f"is now served as ?v={token}. Update the pin to "
        f"({token!r}, {digest!r})."
    )


def test_catalog_payload_is_json_serialisable_and_complete():
    """It is injected with `json.dumps`, so it has to survive the round trip."""
    payload = json.loads(json.dumps(pulse_reactions.catalog_payload()))
    assert [entry["key"] for entry in payload] == [key for key, _e, _l in pulse_reactions.REACTION_CATALOG]
    assert all(entry["emoji"] and entry["label"] for entry in payload)


def test_every_cache_busted_asset_is_pinned():
    """The pin is only worth what it covers.

    It used to list the handful of files someone had already been burned by, so
    every other `?v=`-served asset could be edited under a stale token with CI
    fully green -- which is exactly how a /pulse/videos fix shipped
    undeliverable. Deriving the expected set from what bot.py and templates/
    actually serve means a newly tokenized asset fails here until someone
    records its digest.
    """
    sources = _asset_reference_sources()

    # `served` below selects on `any(tokens)`, and a bare-only asset's token dict
    # is `{"": {...}}` -- `any([""])` is False, so it was filtered out and pinned
    # by nothing. That was the wider hole: /static is sent
    # `max-age=31536000, immutable` by *path prefix*, not by whether a token is
    # present, so a bare URL is cached for a year with no token to bump. It is not
    # stale-until-bumped, it is undeliverable, and this gate could not see it.
    #
    # Eight assets were in that state. Closing the hole and introducing their
    # tokens has to happen together: assert this on the old tree and it fails for
    # eight files nobody has given a token yet, which is why the check arrives
    # with them.
    bare = sorted(path for path, tokens in sources.items() if "" in tokens)
    assert not bare, (
        f"{len(bare)} asset(s) are referenced with no ?v= token at all: "
        + "; ".join(f"{p} from {sorted(sources[p][''])}" for p in bare)
        + ". /static is served `max-age=31536000, immutable` by path prefix, so a "
        "bare URL is frozen in every warm cache with no token to bump -- no "
        "future edit can reach a returning visitor. Give it a token at every "
        "reference (bot.py *and* templates/) and pin it below. If the asset is "
        "stripped from the page by an exact-string `.replace()` in a boot "
        "profile, the token has to go into that string too or the strip silently "
        "stops matching."
    )
    served = {path for path, tokens in sources.items() if any(tokens)}
    unpinned = sorted(served - set(CACHE_PINNED_ASSETS))
    assert not unpinned, (
        f"{len(unpinned)} asset(s) are served with a ?v= token but absent from "
        f"CACHE_PINNED_ASSETS: {unpinned}. Until an asset is pinned, editing it "
        "without bumping its token is invisible to CI and the change never "
        "reaches a returning visitor. Add each with its token and sha256."
    )

    missing = sorted(set(CACHE_PINNED_ASSETS) - served)
    assert not missing, (
        f"CACHE_PINNED_ASSETS pins {missing}, which nothing serves with "
        "a ?v= token. Drop the entry, or restore the token it is guarding."
    )


def test_a_boot_profile_strip_still_matches_the_tag_it_strips():
    """Tokenizing an asset must not orphan the string that removes it.

    `/pulse` renders under a boot profile, and the narrower profiles drop scripts
    by exact-string `.replace()` on the already-rendered HTML rather than by not
    emitting them. So the strip string embeds the whole tag, `?v=` token and all.
    Give a bare asset a token in its declaration and forget the strip, and the
    strip matches nothing: the script starts loading under the very profiles that
    exist to remove it, no test notices, and the page gets heavier for the clients
    least able to afford it.

    That is not hypothetical -- `pulse_media_picker.js` and `time.js` are both
    stripped this way and both were bare, so introducing their tokens had to
    rewrite three `.replace()` calls alongside two declarations.

    This compares the two sides as source text: every tag a profile strips must
    still appear verbatim in the page bot.py builds.
    """
    source = read(BOT)

    # The tag is a quoted literal whose *inside* uses the other quote character
    # (`.replace('<script src="...">', "")`), so the delimiter is captured and
    # back-referenced rather than excluded by a character class.
    stripped = re.findall(
        r"""rendered_html\.replace\(\s*(['"])(<(?:script|link)\b.*?)\1\s*,\s*(['"])\3\s*\)""",
        source,
    )
    assert stripped, (
        "no boot-profile strip calls found. If they moved, point this test at "
        "them; the check is the only thing keeping a tokenized declaration and "
        "the string that removes it in agreement."
    )

    orphans = [tag for _q, tag, _q2 in stripped if source.count(tag) < 2]
    assert not orphans, (
        f"{len(orphans)} boot-profile strip(s) match no tag bot.py emits: "
        + "; ".join(orphans)
        + ". The strip is exact-string, so it now removes nothing and the asset "
        "loads under the profile that exists to drop it. Most likely a `?v=` "
        "token was added or bumped in the declaration but not in the "
        "`.replace()` argument -- they have to move together."
    )
