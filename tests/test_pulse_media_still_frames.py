"""A field named after a picture must contain a picture.

Every PulseSoc post shared into Messenger rendered as a black rectangle with a
white "Video" badge floating on it. The badge was the tell: the card only draws
it inside the branch that *has* a thumbnail, so the client was not failing to
find one -- it had been handed a thumbnail and drawn it, and the thumbnail was
the video.

Two layers made the same mistake, and the second would have hidden a fix to the
first:

  * ``media_service.resolve_media`` blanks a video URL out of ``poster_url``
    (``if kind == "video" and _is_video_url(poster_value)``) and then returns
    ``"thumbnail_url": thumb or source`` on the very next line. ``thumb`` had
    been correctly emptied further up, so the fallback put the asset straight
    back under the name of its own thumbnail.
  * ``pulse_feed_engine._canonical_media_payload`` then did
    ``thumb = ... or valid_url``, which refilled the field even for a resolver
    that had emptied it honestly.

Neither is detectable by asking "is there a thumbnail URL". There always was
one, it was always well-formed, and an ``<img>``/``<Image>`` pointed at an
``.m3u8`` reports no error at all -- the decode never starts, ``onError`` never
fires, and the element just occupies its box. So every assertion here is about
the *shape* of the URL rather than its presence. A truthiness check passed
throughout the entire life of the bug.

``poster_url`` is included in the sweep even though it was already guarded. It
is guarded by one ``if`` that no test pinned, which is the same position
``thumbnail_url`` was in yesterday.
"""

import unittest

from services import media_service
from services import pulse_feed_engine


CDN = "https://cdn.coinpilotx.app/u"

STILL_FIELDS = ("thumbnail_url", "poster_url", "mux_thumbnail_url")


def payload_for(item):
    """The media object a client actually receives, through both layers."""
    return pulse_feed_engine._canonical_media_payload(item, media_service.resolve_media(item))


class StillFieldsNeverCarryTheVideo(unittest.TestCase):
    def assert_no_video_in_stills(self, payload):
        for field in STILL_FIELDS:
            value = str(payload.get(field) or "")
            self.assertFalse(
                media_service.is_video_url(value),
                f"{field} carries the asset ({value!r}) instead of a picture of it",
            )

    def test_video_with_a_mux_asset_resolves_to_its_mux_frame(self):
        # The common case in production: nothing stored on the row, but the
        # asset has been transcoded, so a real frame exists and is free.
        payload = payload_for(
            {
                "id": 1,
                "media_type": "video",
                "media_url": f"{CDN}/clip.mp4",
                "mux_playback_id": "PLAY123",
                "mux_status": "ready",
            }
        )
        self.assertEqual(payload["thumbnail_url"], "https://image.mux.com/PLAY123/thumbnail.jpg")
        self.assertEqual(payload["poster_url"], "https://image.mux.com/PLAY123/thumbnail.jpg")
        self.assert_no_video_in_stills(payload)

    def test_video_with_no_still_anywhere_says_so(self):
        # "" is the answer that lets a renderer draw no media frame. The old
        # behaviour returned the mp4, which is what a renderer cannot refuse:
        # it reserves the box, starts nothing, and reports nothing.
        payload = payload_for({"id": 2, "media_type": "video", "media_url": f"{CDN}/clip.mp4"})
        self.assertEqual(payload["thumbnail_url"], "")
        self.assertEqual(payload["poster_url"], "")

    def test_a_stored_still_is_preferred_over_the_mux_frame(self):
        # An uploader-chosen or pipeline-generated thumbnail is a better picture
        # of the post than Mux's arbitrary frame, so the guard must skip videos
        # without also discarding the answer.
        payload = payload_for(
            {
                "id": 3,
                "media_type": "video",
                "media_url": f"{CDN}/clip.mp4",
                "thumbnail_url": f"{CDN}/clip-thumb.jpg",
                "mux_playback_id": "PLAY123",
                "mux_status": "ready",
            }
        )
        self.assertEqual(payload["thumbnail_url"], f"{CDN}/clip-thumb.jpg")
        self.assert_no_video_in_stills(payload)

    def test_a_stored_still_that_is_really_a_video_falls_through_to_mux(self):
        # The rows this bug already wrote. Skipping a bad candidate rather than
        # blanking the field is what lets them recover on the next read instead
        # of needing a backfill.
        payload = payload_for(
            {
                "id": 4,
                "media_type": "video",
                "media_url": f"{CDN}/clip.mp4",
                "thumbnail_url": f"{CDN}/clip.mp4",
                "mux_playback_id": "PLAY123",
                "mux_status": "ready",
            }
        )
        self.assertEqual(payload["thumbnail_url"], "https://image.mux.com/PLAY123/thumbnail.jpg")
        self.assert_no_video_in_stills(payload)

    def test_an_hls_manifest_is_not_a_still_either(self):
        payload = payload_for(
            {
                "id": 5,
                "media_type": "video",
                "media_url": f"{CDN}/clip.mp4",
                "playback_url": "https://stream.mux.com/PLAY123.m3u8",
                "thumbnail_url": "https://stream.mux.com/PLAY123.m3u8",
            }
        )
        self.assert_no_video_in_stills(payload)

    def test_a_photo_is_still_its_own_thumbnail(self):
        # The guard is about video. A photo with no derived thumbnail must keep
        # falling back to itself, or the fix trades one blank card for another.
        payload = payload_for({"id": 6, "media_type": "image", "media_url": f"{CDN}/photo.jpg"})
        self.assertEqual(payload["thumbnail_url"], f"{CDN}/photo.jpg")
        self.assertEqual(payload["poster_url"], f"{CDN}/photo.jpg")

    def test_a_gif_is_not_mistaken_for_a_video(self):
        payload = payload_for({"id": 7, "media_type": "image", "media_url": f"{CDN}/loop.gif"})
        self.assertEqual(payload["thumbnail_url"], f"{CDN}/loop.gif")


class TheSkippingHelperIsNotAnOrChain(unittest.TestCase):
    """`_first_still` exists because `or` cannot express "skip a bad answer".

    An `or`-chain stops at the first *truthy* candidate, and a video URL in a
    thumbnail field is extremely truthy. That single property is the whole
    difference between the old behaviour and the new one.
    """

    def test_skips_a_video_candidate_and_keeps_looking(self):
        self.assertEqual(
            pulse_feed_engine._first_still(f"{CDN}/clip.mp4", f"{CDN}/frame.jpg"),
            f"{CDN}/frame.jpg",
        )

    def test_skips_blanks_and_whitespace(self):
        self.assertEqual(pulse_feed_engine._first_still("", "   ", None, f"{CDN}/f.jpg"), f"{CDN}/f.jpg")

    def test_returns_empty_when_every_candidate_is_the_asset(self):
        self.assertEqual(pulse_feed_engine._first_still(f"{CDN}/a.mp4", f"{CDN}/b.mov"), "")

    def test_ignores_query_strings_when_judging_the_extension(self):
        # Signed CDN and Mux URLs always carry one; an extension test that read
        # the whole string would call every signed asset a picture.
        self.assertEqual(pulse_feed_engine._first_still(f"{CDN}/clip.mp4?token=abc", ""), "")
        self.assertEqual(
            pulse_feed_engine._first_still(f"{CDN}/frame.jpg?width=640"), f"{CDN}/frame.jpg?width=640"
        )


if __name__ == "__main__":
    unittest.main()
