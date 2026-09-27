"""The one reaction catalogue.

Every reaction a PulseSoc user can send is named here, exactly once, together
with the glyph and the label that must be shown for it on every surface.

Before this module existed the same server-side reaction key rendered as
different emoji depending on which renderer happened to draw it. There were
four independent tables:

  * ``bot.py``'s ``reactionIcons`` (19 keys, the legacy inline feed runtime)
  * ``bot.py``'s ``pulseReactionEmoji`` (12 keys, defaulting to a heart)
  * ``pulse_home_core.js``'s ``feedReactionChoices`` (11 keys, the live tray)
  * ``pulse_home_core.js``'s ``reactionEmojis`` (14 keys, the summary chip)

They disagreed, so a real reaction was displayed as a different feeling than
the one the sender chose. Two examples were reproduced against a running
server before this file was written: a ``bullish`` reaction rendered as a
rocket, because two different keys had both been assigned to the same glyph;
and a ``whale`` reaction rendered as a thumbs-up, because the summary chip's
table simply had no entry for it and fell through to a default.

The catalogue is therefore the *only* place a key-to-glyph decision is
allowed to be made. ``REACTIONS`` -- the wire allowlist that decides whether a
reaction is accepted at all -- is derived from it rather than written out a
second time, so a key can never be sendable but undisplayable, nor displayable
but rejected.

Two rules hold and are enforced by ``tests/web_surface/test_reaction_catalogue.py``:

1. **No two keys share a glyph.** A shared glyph is indistinguishable to the
   person reading the post, which is the bug that produced the rocket above.
2. **Every key the server accepts has an entry.** Otherwise a renderer has to
   invent a fallback, which is the bug that produced the thumbs-up above.

The emoji column matches the native app (``mobile-native/src/components/PostCard.tsx``)
wherever the app expresses an opinion; the web previously assigned ``bullish``
the app's rocket glyph, and the app wins that conflict.
"""

from __future__ import annotations

# key, emoji, label. Order is the display order: the first `TRAY_SIZE` entries
# are the compact hover tray, so the six most human reactions lead and the
# market-signal keys follow.
REACTION_CATALOG: tuple[tuple[str, str, str], ...] = (
    ("like", "\N{THUMBS UP SIGN}", "Like"),
    # U+FE0F is load-bearing on this one and on `shield` below: both glyphs
    # default to *text* presentation, so without the variation selector they
    # render as a thin monochrome outline next to 17 full-colour emoji.
    ("love", "\N{HEAVY BLACK HEART}\N{VARIATION SELECTOR-16}", "Love"),
    ("funny", "\N{FACE WITH TEARS OF JOY}", "Funny"),
    ("wow", "\N{FACE WITH OPEN MOUTH}", "Wow"),
    ("brutal", "\N{CRYING FACE}", "Sad"),
    ("scam_alert", "\N{POUTING FACE}", "Angry"),
    ("fire", "\N{FIRE}", "Fire"),
    ("fast_signal", "\N{HIGH VOLTAGE SIGN}", "Genius"),
    ("elite", "\N{GEM STONE}", "Valuable"),
    ("rocket", "\N{ROCKET}", "Rocket"),
    ("clap", "\N{CLAPPING HANDS SIGN}", "Clap"),
    ("hundred", "\N{HUNDRED POINTS SYMBOL}", "Hundred"),
    ("target", "\N{DIRECT HIT}", "Target"),
    ("smart", "\N{BRAIN}", "Smart"),
    ("shield", "\N{SHIELD}\N{VARIATION SELECTOR-16}", "Shield"),
    ("whale", "\N{SPOUTING WHALE}", "Whale"),
    ("bullish", "\N{CHART WITH UPWARDS TREND}", "Bullish"),
    ("bearish", "\N{CHART WITH DOWNWARDS TREND}", "Bearish"),
)

# The compact tray shown on hover. Deliberately short: a tray long enough to
# hold all 18 is a wall of glyphs nobody reads. The rest stay reachable through
# the full picker.
TRAY_SIZE = 7

#: Wire allowlist. Derived, never written twice -- see the module docstring.
REACTIONS = frozenset(key for key, _emoji, _label in REACTION_CATALOG)

EMOJI = {key: emoji for key, emoji, _label in REACTION_CATALOG}
LABELS = {key: label for key, _emoji, label in REACTION_CATALOG}
TRAY_KEYS = tuple(key for key, _emoji, _label in REACTION_CATALOG[:TRAY_SIZE])


def emoji_for(key: str) -> str:
    """Glyph for ``key``, or ``""`` when the key is not a real reaction.

    The empty string is deliberate. Callers must render *nothing* for a key
    they do not recognise rather than substituting a plausible-looking glyph:
    showing a thumbs-up for an unknown key is how a whale became a thumbs-up.
    An absent glyph is a visible gap that gets reported; a wrong glyph is
    silently believed.
    """
    return EMOJI.get(str(key or ""), "")


def label_for(key: str) -> str:
    """Human label for ``key``, falling back to the key itself."""
    key = str(key or "")
    return LABELS.get(key, key)


def catalog_payload() -> list[dict[str, str]]:
    """The catalogue in the shape the browser runtime consumes."""
    return [{"key": key, "emoji": emoji, "label": label}
            for key, emoji, label in REACTION_CATALOG]
