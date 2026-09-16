"""
The conversation wallpaper default is declared twice, in two languages, and the
two declarations must agree.

`pulse_communications_v2.service` owns the product default: `_merge_control_settings`
layers a viewer's stored row over `CONTROL_SETTING_DEFAULTS`, so the value there is
what a conversation nobody has ever customised reports. The native client declares
its own default in `mobile-native/src/theme/chatWallpaper.ts`, because it has to
paint a background in the first layout commit — before the control-centre read has
resolved — and that is the whole no-flash guarantee.

Neither side can see the other, and the failure when they disagree is quiet rather
than loud: the conversation opens on the client's default and then visibly swaps to
the server's a moment later. Nothing throws, no test goes red, and the background
changes under the reader on every single chat open.

That is not hypothetical. Production ran `deep_space` here while the client shipped
`pulsesoc_cosmic`, and the observable result was that the new default never appeared
for any conversation — the client painted it, then the server's gap-filler replaced
it. These tests exist so that mismatch fails in CI instead of on a phone.
"""
import json
import pathlib
import re
import unittest

from pulse_communications_v2.service import CONTROL_SETTING_ALLOWED, CONTROL_SETTING_DEFAULTS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CHAT_WALLPAPER_TS = REPO_ROOT / "mobile-native" / "src" / "theme" / "chatWallpaper.ts"


def _client_source() -> str:
    return CHAT_WALLPAPER_TS.read_text(encoding="utf-8")


def _client_default_wallpaper(source: str) -> str:
    """The id in `export const DEFAULT_CHAT_WALLPAPER = "..."`."""
    match = re.search(
        r"""export\s+const\s+DEFAULT_CHAT_WALLPAPER\s*(?::[^=]+)?=\s*["']([a-z0-9_]+)["']""",
        source,
    )
    assert match, "DEFAULT_CHAT_WALLPAPER not found in chatWallpaper.ts"
    return match.group(1)


def _client_wallpaper_ids(source: str) -> set[str]:
    """
    The ids in the `ChatWallpaperId` union.

    This is the right set to read rather than `CHAT_WALLPAPER_IDS`, which is
    `Object.keys(SPECS)` and so has no literals to parse. The union and `SPECS`
    cannot drift apart: `SPECS` is declared `Record<ChatWallpaperId, ...>`, so
    typecheck fails if a key is missing or extra.
    """
    match = re.search(
        r"export\s+type\s+ChatWallpaperId\s*=\s*(.*?);",
        source,
        re.DOTALL,
    )
    assert match, "the ChatWallpaperId union was not found in chatWallpaper.ts"
    ids = set(re.findall(r"""["']([a-z0-9_]+)["']""", match.group(1)))
    assert ids, "the ChatWallpaperId union parsed empty — its shape must have changed"
    return ids


class WallpaperDefaultContractTest(unittest.TestCase):
    """The client default and the server gap-filler are the same wallpaper."""

    def setUp(self):
        self.source = _client_source()

    def test_the_client_file_is_where_we_think_it_is(self):
        # A parse that silently reads nothing would make every assertion below
        # vacuous, so fail loudly on a moved or renamed file instead.
        self.assertTrue(CHAT_WALLPAPER_TS.is_file(), f"missing {CHAT_WALLPAPER_TS}")

    def test_server_default_matches_the_client_default(self):
        server_default = CONTROL_SETTING_DEFAULTS["appearance"]["wallpaper"]
        self.assertEqual(
            server_default,
            _client_default_wallpaper(self.source),
            "The server's gap-filler and the native client's first-paint default have "
            "drifted. Every conversation with no stored choice will paint the client "
            "default and then visibly swap to the server's.",
        )

    def test_the_default_is_a_wallpaper_the_client_can_actually_draw(self):
        # A server default the client does not recognise is treated as "no choice"
        # by `useConversationWallpaper`, which silently resurrects the client
        # default and makes the server's value dead.
        self.assertIn(
            CONTROL_SETTING_DEFAULTS["appearance"]["wallpaper"],
            _client_wallpaper_ids(self.source),
        )

    def test_the_default_is_an_accepted_value_for_a_write(self):
        # The control centre writes the whole merged appearance section back, so a
        # default outside the allowlist would make an unrelated setting change fail
        # validation or strip the wallpaper.
        self.assertIn(
            CONTROL_SETTING_DEFAULTS["appearance"]["wallpaper"],
            CONTROL_SETTING_ALLOWED["appearance"]["wallpaper"],
        )

    def test_every_wallpaper_the_client_draws_can_be_stored(self):
        # The picker offers whatever the client knows; anything it offers must
        # survive a round trip through `_coerce_control_value`.
        allowed = CONTROL_SETTING_ALLOWED["appearance"]["wallpaper"]
        missing = sorted(_client_wallpaper_ids(self.source) - set(allowed))
        self.assertEqual([], missing, f"client offers ids the server rejects: {missing}")


class WallpaperDefaultMergeTest(unittest.TestCase):
    """The default fills a gap and never displaces a stored choice."""

    def test_an_untouched_conversation_reports_the_default(self):
        from pulse_communications_v2.service import _merge_control_settings

        merged = _merge_control_settings(None)
        self.assertEqual(
            CONTROL_SETTING_DEFAULTS["appearance"]["wallpaper"],
            merged["appearance"]["wallpaper"],
        )

    def test_a_stored_choice_survives_the_merge(self):
        from pulse_communications_v2.service import _merge_control_settings

        merged = _merge_control_settings({"appearance_json": json.dumps({"wallpaper": "star_tunnel"})})
        self.assertEqual("star_tunnel", merged["appearance"]["wallpaper"])

    def test_a_stored_row_without_a_wallpaper_still_reports_the_default(self):
        # Rows predating the wallpaper setting exist; they must not read as a
        # choice of "nothing".
        from pulse_communications_v2.service import _merge_control_settings

        merged = _merge_control_settings({"appearance_json": json.dumps({"font_size": "large"})})
        self.assertEqual(
            CONTROL_SETTING_DEFAULTS["appearance"]["wallpaper"],
            merged["appearance"]["wallpaper"],
        )
        self.assertEqual("large", merged["appearance"]["font_size"])


if __name__ == "__main__":
    unittest.main()
