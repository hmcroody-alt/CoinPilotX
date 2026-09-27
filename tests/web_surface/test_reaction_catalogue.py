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
    "static/js/pulse_home_core.js": (
        "reaction-catalogue-20260927h",
        "1aeeccee65767efcd3210bfab59b4ef827a01b42fb05abe6f50132e23fa38c24",
    ),
    "static/css/pulse_desktop_shell.css": (
        "reaction-catalogue-20260927h",
        "401375580f970d043ded8b961b8bb9c583a300a5e63498f2300f46dd2a7c5c00",
    ),
    "static/css/pulse_reaction_system.css": (
        "video-action-fit-20260927i",
        "6ff68ca37cb663b328b315dbdfcd7e63614e2dee96f996946ab2800eb18db50b",
    ),
}


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


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

    filename = relpath.rsplit("/", 1)[-1]
    tokens = set(re.findall(re.escape(filename) + r"\?v=([\w.-]+)", read(BOT)))
    assert tokens, f"{relpath} is no longer served with a ?v= token by bot.py"
    assert len(tokens) == 1, (
        f"{relpath} is served under more than one token {sorted(tokens)}; the copies "
        "would be cached separately and one of them would be stale"
    )

    with open(os.path.join(REPO, relpath), "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    token = tokens.pop()

    if digest != expected_digest and token == expected_token:
        pytest.fail(
            f"{relpath} changed but still ships as ?v={token}. Every browser that "
            "has already loaded that token keeps its old copy for a year, so this "
            "change would never reach a returning visitor. Bump the token in bot.py "
            f"and record the new digest in CACHE_PINNED_ASSETS: {digest}"
        )
    assert (token, digest) == (expected_token, expected_digest), (
        f"{relpath} is pinned to ?v={expected_token} in CACHE_PINNED_ASSETS but "
        f"bot.py now serves ?v={token}. Update the pin to "
        f"({token!r}, {digest!r})."
    )


def test_catalog_payload_is_json_serialisable_and_complete():
    """It is injected with `json.dumps`, so it has to survive the round trip."""
    payload = json.loads(json.dumps(pulse_reactions.catalog_payload()))
    assert [entry["key"] for entry in payload] == [key for key, _e, _l in pulse_reactions.REACTION_CATALOG]
    assert all(entry["emoji"] and entry["label"] for entry in payload)
