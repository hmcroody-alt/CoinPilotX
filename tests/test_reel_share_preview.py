"""That a Reel share card can be drawn without handing out the Reel.

A share preview is the one PulseSoc payload whose destination is *somebody
else's conversation*. It is rendered for a recipient who never asked for it, it
survives forwarding, and it is cached on their device. So the two questions
worth pinning are different from the ones a normal endpoint gets asked:

1. Can the preview say something the Reels screen would not? It must not, and
   the structural half of this file argues it *cannot*, by showing that the
   preview is a projection of `pulse_reel_payload` rather than a second read.
   `pulse_reel_payload` already drops deleted reels, blocked reels and reels
   the viewer may not see; a projection inherits all three for free, and no
   fixture-based test of those three cases would prove as much, because it
   would only prove the three cases somebody thought to write down.

2. Can the preview hand over the video? A card draws a still frame. Anything in
   it that addresses the asset -- a playback url, a Mux id, a storage key -- is
   a durable handle to the media that travels with every forward of the
   message, long after the sender has stopped being able to open the Reel
   themselves. The leak test below is deliberately a *deny* list checked
   against the serialized output, not an equality assertion on the allowed
   keys: an equality assertion passes the day someone adds `playback_url` to
   the allowlist and updates the expected dict in the same edit.
"""

from __future__ import annotations

import json
import os
import re
import tempfile

_bootstrap = tempfile.mktemp(prefix="reel-share-preview-", suffix=".sqlite3")
os.environ["DATABASE_URL"] = f"sqlite:///{_bootstrap}"

from tests.test_live_replay_worker import media_worker  # noqa: E402

bot = media_worker.bot

MUX_STILL = "https://image.mux.com/abc123/thumbnail.jpg?width=640"
MUX_HLS = "https://stream.mux.com/abc123.m3u8"


def full_reel():
    """A reel payload shaped like the real one, with every secret populated.

    Every field named in `PULSE_REEL_PREVIEW_FORBIDDEN_FIELDS` carries a value
    here on purpose. A fixture that left them empty would let the leak test
    pass against a preview that copies them faithfully.
    """
    return {
        "id": 38,
        "reel_id": 38,
        "post_id": 902,
        "title": "Sunset over Port-au-Prince",
        "body": "post body that loses to the caption",
        "caption": "Golden hour from the roof.",
        "duration_seconds": 14.5,
        "video_url": MUX_HLS,
        "author": {
            "user_id": 7,
            "display_name": "Roody Cherie",
            "username": "roody",
            "public_player_id": "pp-000007",
            "avatar_url": "https://cdn.pulsesoc.com/avatars/7.jpg",
            "badge_keys": ["creator"],
            "premium_mark": True,
        },
        "media": [
            {
                "id": 5511,
                "media_type": "video",
                "media_url": "https://r2.internal/raw/5511.mp4",
                "valid_url": "https://r2.internal/raw/5511.mp4",
                "cdn_url": "https://cdn.pulsesoc.com/5511.mp4",
                "playback_url": MUX_HLS,
                "mux_playback_id": "abc123",
                "mux_asset_id": "asset_secret_9f2",
                "mux_hls_url": MUX_HLS,
                "mux_thumbnail_url": MUX_STILL,
                "thumbnail_url": MUX_STILL,
                "poster_url": MUX_STILL,
                "fallback_url": "https://cdn.pulsesoc.com/fallback.png",
                "storage_provider": "r2",
                "storage_key": "reels/2026/09/5511.mp4",
                "source_url": "https://origin.internal/5511.mp4",
                "is_available": True,
            }
        ],
        "audio": {
            "attached_audio_url": "https://cdn.pulsesoc.com/audio/77.m4a",
            "audio_url": "https://cdn.pulsesoc.com/audio/77.m4a",
            "preview_url": "https://cdn.pulsesoc.com/audio/77-preview.m4a",
            "title": "Kompa Loop",
        },
        "attached_audio_url": "https://cdn.pulsesoc.com/audio/77.m4a",
        "can_manage": False,
        "visibility": "public",
    }


class TestThePreviewCarriesNoWayToFetchTheVideo:
    def test_no_forbidden_field_survives_the_projection(self):
        blob = json.dumps(bot.pulse_reel_share_preview(full_reel()))
        leaked = [f for f in bot.PULSE_REEL_PREVIEW_FORBIDDEN_FIELDS if f'"{f}"' in blob]
        assert leaked == [], (
            f"the share preview emits {leaked}. These name the asset rather than "
            "describe it, and the preview travels to people who cannot open the "
            "Reel and forwards onward from there."
        )

    def test_no_forbidden_value_survives_either(self):
        """The names are the cheap check; the values are the real one.

        A field renamed on the way out -- `playback_url` copied into `src` --
        sails past the key check while leaking exactly the same url. This
        asserts on the secrets themselves, so the only way to pass is to not
        carry them.

        The Mux *playback id* is pointedly not in this list, and the omission is
        a finding rather than an oversight. It is embedded in the thumbnail URL
        by construction -- `image.mux.com/<playback_id>/thumbnail.jpg` -- so a
        card that shows a Mux still necessarily discloses it. That is safe only
        because PulseSoc mints assets with `playback_policy="public"`
        (`media_service.create_mux_asset_from_url`), which makes the playback id
        public by design: it is already in the `<video>` src on the web page.
        The *asset* id is the private one, being the Mux API handle used to
        delete and re-encode, and it is asserted against below.

        If a signed playback policy is ever adopted for reels, this comment is
        where the assumption is written down, and the playback id becomes a
        secret that the poster URL is no longer allowed to carry.
        """
        blob = json.dumps(bot.pulse_reel_share_preview(full_reel()))
        for secret in (
            MUX_HLS,
            ".m3u8",
            "asset_secret_9f2",
            "r2.internal",
            "reels/2026/09/5511.mp4",
            "origin.internal",
            "cdn.pulsesoc.com/audio",
        ):
            assert secret not in blob, (
                f"{secret!r} reached the share preview. Renaming a field does not "
                "make its value safe to put in a forwardable message."
            )

    def test_the_still_frame_does_survive(self):
        """Guard the guard: a preview that leaked nothing by drawing nothing.

        Every assertion above is satisfied by returning an empty dict, so the
        one field the card exists to show has to be pinned separately or the
        leak suite silently becomes a test of `{}`.
        """
        preview = bot.pulse_reel_share_preview(full_reel())
        assert preview["poster_url"] == MUX_STILL
        assert preview["author"]["display_name"] == "Roody Cherie"
        assert preview["caption"] == "Golden hour from the roof."


class TestThePosterIsAStillAndNeverTheVideo:
    def test_a_video_url_in_a_still_field_is_skipped(self):
        """`.m3u8` in `poster_url` is a real shape, and it draws nothing.

        An `<Image>` handed a playlist reports no error and renders a black
        rectangle, so the card believes it has a picture and draws its play
        badge over emptiness. Falling through to the next still is the only
        outcome that lets the card know it has none.
        """
        reel = full_reel()
        reel["media"][0]["poster_url"] = MUX_HLS
        assert bot.pulse_reel_share_preview(reel)["poster_url"] == MUX_STILL

    def test_a_reel_with_no_still_anywhere_yields_no_poster(self):
        reel = full_reel()
        for field in ("poster_url", "thumbnail_url", "mux_thumbnail_url"):
            reel["media"][0][field] = MUX_HLS
        assert bot.pulse_reel_share_preview(reel)["poster_url"] == ""

    def test_the_reel_level_still_is_the_last_resort(self):
        reel = full_reel()
        reel["media"] = []
        reel["thumbnail_url"] = "https://cdn.pulsesoc.com/covers/38.jpg"
        assert bot.pulse_reel_share_preview(reel)["poster_url"] == "https://cdn.pulsesoc.com/covers/38.jpg"

    def test_media_type_is_video_even_when_the_record_forgot_to_say_so(self):
        # The play indicator is drawn from this. A record with a missing type
        # must not make a reel render as a photo -- it is a reel either way.
        reel = full_reel()
        reel["media"][0].pop("media_type")
        assert bot.pulse_reel_share_preview(reel)["media_type"] == "video"


class TestTheAuthorRow:
    def test_the_machine_handle_backs_up_a_missing_username(self):
        # An account that never chose a handle still has one, and a card with
        # an empty author row reads as a rendering fault rather than as privacy.
        reel = full_reel()
        reel["author"]["username"] = ""
        assert bot.pulse_reel_share_preview(reel)["author"]["username"] == "pp-000007"

    def test_the_at_sign_is_the_card_s_to_add(self):
        reel = full_reel()
        reel["author"]["username"] = "@roody"
        assert bot.pulse_reel_share_preview(reel)["author"]["username"] == "roody"

    def test_nothing_private_about_the_author_comes_along(self):
        # `_public_author` carries badge keys, premium marks and a user_id. A
        # card shows a name, a handle and a face; the rest is the profile
        # screen's to disclose, on a request the viewer actually made.
        author = bot.pulse_reel_share_preview(full_reel())["author"]
        assert set(author) == {"display_name", "username", "avatar_url"}


class TestTheCanonicalUrl:
    def test_it_is_absolute_because_an_sms_cannot_resolve_a_path(self):
        preview = bot.pulse_reel_share_preview(full_reel())
        assert preview["canonical_url"] == f"{bot.APP_BASE_URL}/pulse/reels/38"
        assert preview["canonical_url"].startswith("http")

    def test_the_in_app_path_is_the_one_the_router_claims(self):
        # `navigation/linking.ts` claims `pulse/reels/:reelId` and nothing else.
        # `/open/reel/38` is a public interstitial that deliberately does not
        # open the app, so it must never be handed out as a Reel's identity.
        assert bot.pulse_reel_share_preview(full_reel())["path"] == "/pulse/reels/38"


def _function_source(name):
    """Slice a function out of bot.py by text, not by AST line index.

    `ast` and `str.splitlines()` disagree about U+2028/U+2029 and bot.py has
    held a raw one, which shifts every AST-to-source index after it -- a test
    like this then reads the wrong function and passes.

    The docstring is cut off the front. Every assertion below is about the
    *order* things happen in, and a docstring that explains the order by naming
    the same functions puts each of those names earlier in the string than the
    line that calls it. This bit the ordering test the day it was written: the
    prose "handed through `pulse_reel_payload`" outranked the call.
    """
    source = open(bot.__file__, encoding="utf-8").read()
    start = source.index(f"\ndef {name}(")
    end = source.find("\ndef ", start + 1)
    body = source[start:end if end > 0 else len(source)]
    opened = body.find('"""')
    if opened != -1:
        closed = body.find('"""', opened + 3)
        if closed != -1:
            body = body[closed + 3:]
    return body


class TestTheReadIsTheSameReadATapMakes:
    """The argument that the card cannot outrank the screen.

    None of this is about a fixture. It is about there being exactly one query,
    so the preview has nothing of its own to get wrong. A test that stood up a
    deleted Reel and asserted a 404 would prove less: it would prove the one
    case, on the day it was written, and stay green if a second read were added
    beside the first.
    """

    def test_the_get_arm_is_routed_on_both_rules(self):
        rules = {
            r.rule: r.methods
            for r in bot.webhook_app.url_map.iter_rules()
            if r.rule in {"/api/reels/<int:reel_id>", "/api/pulse/reels/<int:reel_id>"}
        }
        assert set(rules) == {"/api/reels/<int:reel_id>", "/api/pulse/reels/<int:reel_id>"}, (
            "a Reel rich card needs a by-id read on the path the app already "
            f"calls; found only {sorted(rules)}"
        )
        for rule, methods in rules.items():
            assert "GET" in methods, f"{rule} still has no by-id read"

    def test_the_handler_performs_no_read_of_its_own(self):
        source = _function_source("api_pulse_reel_manage")
        get_arm = source[source.index('if request.method == "GET"'):]
        get_arm = get_arm[: get_arm.index("if not reel.get(\"can_manage\")")]
        assert "pulse_reel_payload" not in get_arm, (
            "the GET arm re-reads the Reel. It must project the payload the "
            "route already authorized -- a second read is a second WHERE clause."
        )
        assert "SELECT" not in get_arm.upper(), "the GET arm queries the database directly"

    def test_the_read_is_gated_before_the_arm_is_reached(self):
        source = _function_source("api_pulse_reel_manage")
        auth = source.index("api_account_user")
        payload = source.index("pulse_reel_payload")
        not_found = source.index('return api_error("Reel not found.", 404')
        get_arm = source.index('if request.method == "GET"')
        assert auth < payload < not_found < get_arm, (
            "the GET arm must sit after the login check, after the viewer-scoped "
            "read and after the 404 -- in that order. Reordering any of them "
            "makes the preview more permissive than the Reels screen."
        )

    def test_reading_is_not_gated_on_owning(self):
        """A reader is not a manager, and the owner check must not move above GET.

        This is the inverse risk of the test above: the 403 exists to stop a
        non-owner *editing*, and sliding it up one line would stop everyone but
        the creator seeing a card for a public Reel.
        """
        source = _function_source("api_pulse_reel_manage")
        assert source.index('if request.method == "GET"') < source.index('if not reel.get("can_manage")')

    def test_absence_and_refusal_are_the_same_sentence(self):
        """A deleted Reel and a Reel you may not see must be indistinguishable.

        `pulse_reel_payload` returns `None` for both, and the route has exactly
        one branch for `None`. Giving the two their own messages would let a
        card confirm that a private Reel exists.
        """
        source = _function_source("api_pulse_reel_manage")
        head = source[: source.index('if request.method == "GET"')]
        assert len(re.findall(r"return api_error\(", head)) == 2, (
            "the pre-GET section should refuse exactly twice -- 401 for no "
            "session and one 404 covering missing, deleted, blocked and "
            "invisible. A third refusal is a disclosure."
        )
        assert head.count("Reel not found.") == 1


class TestTheAllowlistAndTheDenylistDisagreeAboutNothing:
    def test_every_forbidden_field_is_a_field_the_payload_really_has(self):
        """Guard the guard: a denylist of fields nobody produces tests nothing.

        Each name below has to appear in the real reel payload builders, or the
        leak test is checking for ghosts and would stay green through a rename
        that reintroduced the real one.
        """
        source = open(bot.__file__, encoding="utf-8").read()
        feed = open(
            os.path.join(os.path.dirname(bot.__file__), "services", "pulse_feed_engine.py"),
            encoding="utf-8",
        ).read()
        haystack = source + feed
        absent = [f for f in bot.PULSE_REEL_PREVIEW_FORBIDDEN_FIELDS if f'"{f}"' not in haystack]
        assert absent == [], (
            f"{absent} are guarded against but are not produced anywhere. Either "
            "they were renamed -- in which case the guard now misses the real "
            "field -- or they should come off the list."
        )
