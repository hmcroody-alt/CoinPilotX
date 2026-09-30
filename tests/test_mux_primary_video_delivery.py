"""Mux is the primary delivery path for video, including the plain-file fields.

The R2 CDN hostname sits behind a Cloudflare edge rule that challenges video
extensions: a request for ``.mp4``/``.mov``/``.webm`` is answered ``403`` with an
HTML interstitial before it reaches origin, while ``.jpg``/``.png``/``.mp3`` on
that same host return ``200``. Measured against production, every video row on
that host is unplayable and every image and audio row is fine.

``resolve_media`` already preferred Mux for ``playback_url``. It did not for
``media_url``/``valid_url``, so any caller reading those got the one field that
still pointed at a URL which cannot play. These tests pin that the plain-file
fields now agree with ``playback_url``, and pin the two cases where they must
deliberately NOT be swapped.
"""

import services.media_service as media_service


CDN_VIDEO = "https://cdn.coinpilotx.app/pulse_media/2026/06/28/IMG_7650-2400add6a26862be.mov"
PLAYBACK_ID = "Fk2TcTp00YfW2md02PkuRCjysG5msEn5bmqDDYwCw7Enk"


def _video(**overrides):
    item = {
        "id": 4242,
        "media_type": "video",
        "media_url": CDN_VIDEO,
        "mux_playback_id": PLAYBACK_ID,
        "mux_status": "ready",
        # Pinned so the resolver does not reach for the network to decide
        # availability; this suite is about URL selection, not reachability.
        "is_available": 1,
    }
    item.update(overrides)
    return item


class TestMuxUrlHelper:
    def test_mp4_rendition_is_published_for_a_playback_id(self):
        urls = media_service.mux_playback_urls(PLAYBACK_ID)
        assert urls["mp4_url"] == f"https://stream.mux.com/{PLAYBACK_ID}/high.mp4"
        assert urls["hls_url"] == f"https://stream.mux.com/{PLAYBACK_ID}.m3u8"

    def test_empty_and_unusable_ids_still_carry_the_key(self):
        # Callers index this dict directly (``mux_urls["mp4_url"]``), so the
        # early-return paths must carry the key or they raise KeyError rather
        # than degrading to "no Mux URL available".
        for unusable in ("", None, "   ", "!!!"):
            urls = media_service.mux_playback_urls(unusable)
            assert urls["mp4_url"] == ""
            assert urls["hls_url"] == ""


class TestVideoIsServedFromMux:
    def test_media_url_is_the_mux_rendition_not_the_challenged_cdn(self):
        resolved = media_service.resolve_media(_video())
        assert resolved["media_url"] == f"https://stream.mux.com/{PLAYBACK_ID}/high.mp4"
        assert "cdn.coinpilotx.app" not in resolved["media_url"]

    def test_valid_url_follows_media_url(self):
        # valid_url is derived from the same source; a caller gating on it must
        # not be handed the CDN copy after media_url moved to Mux.
        resolved = media_service.resolve_media(_video())
        assert resolved["valid_url"] == f"https://stream.mux.com/{PLAYBACK_ID}/high.mp4"

    def test_playback_url_still_prefers_hls(self):
        # The HLS manifest remains the player-facing URL; this change adds the
        # plain-file equivalent, it does not replace the manifest.
        resolved = media_service.resolve_media(_video())
        assert resolved["playback_url"] == f"https://stream.mux.com/{PLAYBACK_ID}.m3u8"

    def test_cdn_url_still_records_where_the_object_lives(self):
        # cdn_url is the storage pointer, not a delivery URL. Migrating delivery
        # to Mux must not erase the record of where the original object is.
        resolved = media_service.resolve_media(_video(cdn_url=CDN_VIDEO))
        assert resolved["cdn_url"] == CDN_VIDEO


class TestSubstitutionGuards:
    def test_a_signed_playback_id_is_never_served_as_a_bare_url(self):
        # Mux answers 403 for a signed id without a token. Messenger records the
        # policy per attachment precisely so this path can tell the difference.
        resolved = media_service.resolve_media(
            _video(mux_playback_policy="signed")
        )
        assert resolved["media_url"] == CDN_VIDEO
        assert "stream.mux.com" not in resolved["media_url"]

    def test_an_asset_still_ingesting_keeps_the_existing_url(self):
        # A rendition that does not exist yet answers 404. Swapping a 403 for a
        # 404 is not an improvement, so the swap waits for readiness.
        for pending in ("preparing", "processing"):
            resolved = media_service.resolve_media(_video(mux_status=pending))
            assert resolved["media_url"] == CDN_VIDEO, pending

    def test_video_without_a_mux_asset_is_left_alone(self):
        resolved = media_service.resolve_media(
            _video(mux_playback_id="", mux_status="")
        )
        assert resolved["media_url"] == CDN_VIDEO

    def test_images_and_audio_are_untouched(self):
        # Both serve fine from the CDN today -- the edge rule is video-only --
        # so rewriting them would move working traffic onto Mux for no reason.
        image = media_service.resolve_media(
            {
                "id": 7,
                "media_type": "image",
                "media_url": "https://cdn.coinpilotx.app/pulse_media/a.jpg",
                "mux_playback_id": PLAYBACK_ID,
                "mux_status": "ready",
                "is_available": 1,
            }
        )
        assert image["media_url"] == "https://cdn.coinpilotx.app/pulse_media/a.jpg"

        audio = media_service.resolve_media(
            {
                "id": 8,
                "media_type": "audio",
                "media_url": "https://cdn.coinpilotx.app/pulse_media/b.mp3",
                "mux_playback_id": PLAYBACK_ID,
                "mux_status": "ready",
                "is_available": 1,
            }
        )
        assert audio["media_url"] == "https://cdn.coinpilotx.app/pulse_media/b.mp3"
