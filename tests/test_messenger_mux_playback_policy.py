"""That a private conversation video does not become a public URL.

Every Mux asset-creation call site in this repo asked for ``playback_policy:
["public"]`` -- ``media_service`` twice, ``mux_live_service`` for live and its
replay. For a reel or a livestream replay that is correct: the content's whole
purpose is to be reachable by a bare URL.

Messenger is the exception, and it became one the moment conversation video
started being ingested at all. ``_attachment_payload`` serves ``playback_url`` as
a video's ``url``, so a ready public asset makes a private conversation video
retrievable by anyone holding the playback id -- no membership check, no expiry,
outliving the conversation, the membership and the block list. The rest of the
messenger media path re-checks conversation membership on *every single request*
and hands out 900-second single-attachment credentials. Video must not be the one
attachment type that does neither.

So messenger asks Mux for ``signed`` playback, and the consequence runs through
three files:

  * ``media_service.create_mux_asset_from_url`` takes a policy, defaulting to
    ``"public"`` so that nothing else in the product changes behaviour.
  * the ingest records which policy the returned playback id was minted under,
    because it is not derivable later and getting it wrong is a 403.
  * ``_attachment_payload`` mints a tokenised manifest URL per request instead of
    reading a stored one, behind the membership check that got the caller there.

The direction of every failure here is the thing to keep in view. Signing a
public URL is a wasted query parameter. *Failing* to sign a signed URL is a 403,
and a 403 manifest paints black with no error -- indistinguishable from the
original defect this whole mission exists to remove. Hence the repeated
assertions that the fallback is the progressive URL: playback degrades to slow,
never to broken.
"""

import json
import os
import sqlite3
import unittest

os.environ.setdefault("DATABASE_URL", "")

from pulse_communications_v2 import service as comm_v2  # noqa: E402
from services import media_service  # noqa: E402
from services import messenger_media_foundation as foundation  # noqa: E402
from tests.test_messenger_mux_ingest import (  # noqa: E402
    ATTACHMENT_ID,
    COMM_V2_ID,
    PROGRESSIVE,
    MuxIngestHarness,
)

SIGNED_TOKEN_URL = "https://stream.mux.com/vod601.m3u8?token=abc.def.ghi"
BARE_HLS = "https://stream.mux.com/vod601.m3u8"


class _FakeResponse:
    def __init__(self, payload, status=201):
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class AssetCreationCarriesThePolicyItWasAskedFor(unittest.TestCase):
    """The kwarg, and specifically that its default did not move."""

    def setUp(self):
        self.sent = []
        self.playback_ids = [{"id": "vod601", "policy": "signed"}]

        self._real_auth = media_service._mux_auth_header
        self._real_inspect = media_service.inspect_mux_source_url
        self._real_urlopen = media_service.urlopen
        media_service._mux_auth_header = lambda: "Basic test"
        media_service.inspect_mux_source_url = lambda url: {"ok": True, "status": 200, "content_type": "video/mp4"}
        media_service.urlopen = self._fake_urlopen

    def tearDown(self):
        media_service._mux_auth_header = self._real_auth
        media_service.inspect_mux_source_url = self._real_inspect
        media_service.urlopen = self._real_urlopen

    def _fake_urlopen(self, request, timeout=None):
        self.sent.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse({"data": {"id": "asset-601", "status": "preparing", "playback_ids": list(self.playback_ids)}})

    def _create(self, **kwargs):
        return media_service.create_mux_asset_from_url("https://r2.example.com/clip.mov?signed=1", **kwargs)

    def test_the_default_is_still_public(self):
        """Reels, live replay and the chat_media_uploads path must not move.

        They call this function positionally with no policy. If the default ever
        flips to signed, every one of them starts serving 403s -- a far larger
        outage than the problem being fixed, and one with no test of its own
        because those callers never mention a policy at all.
        """
        self.playback_ids = [{"id": "pub1", "policy": "public"}]

        self._create()

        self.assertEqual(self.sent[0]["playback_policy"], ["public"])

    def test_messenger_can_ask_for_signed(self):
        self._create(playback_policy="signed")

        self.assertEqual(self.sent[0]["playback_policy"], ["signed"])

    def test_an_unrecognised_policy_degrades_to_public_rather_than_being_forwarded(self):
        """Mux rejects an unknown policy with a 400, i.e. the video never ingests.

        A typo in a caller should cost that caller the stronger guarantee, not
        cost the user the video.
        """
        self.playback_ids = [{"id": "pub1", "policy": "public"}]

        result = self._create(playback_policy="Signed-Playback")

        self.assertEqual(self.sent[0]["playback_policy"], ["public"])
        self.assertTrue(result["ok"])

    def test_the_playback_id_chosen_is_the_one_matching_the_request(self):
        """An asset can carry more than one playback id.

        The original loop preferred ``policy == "public"`` unconditionally, which
        for a signed ask would hand back the public id -- quietly undoing the
        privacy decision while every other assertion still passed.
        """
        self.playback_ids = [
            {"id": "public-id", "policy": "public"},
            {"id": "signed-id", "policy": "signed"},
        ]

        result = self._create(playback_policy="signed")

        self.assertEqual(result["playback_id"], "signed-id")
        self.assertEqual(result["playback_policy"], "signed")

    def test_the_reported_policy_is_muxs_answer_not_our_request(self):
        """Read back, not echoed.

        If Mux ever returns a public id for a signed ask, the row has to say
        public. Echoing the request would leave the database claiming a privacy
        guarantee the asset does not have, and the read path would append a
        useless token instead of anyone noticing.
        """
        self.playback_ids = [{"id": "public-id", "policy": "public"}]

        result = self._create(playback_policy="signed")

        self.assertEqual(result["playback_policy"], "public")


class TheIngestRecordsWhichPolicyItGot(MuxIngestHarness):
    def test_messenger_video_is_ingested_signed(self):
        self._attachment()
        self._comm_v2_row()

        self._run()

        self.assertEqual(self.mux_calls[0]["playback_policy"], "signed")

    def test_the_policy_is_written_down(self):
        """Not derivable at read time, and guessing wrong is a 403."""
        self._attachment()
        self._comm_v2_row()

        self._run()

        self.assertEqual(self._fetch()["mux_playback_policy"], "signed")

    def test_a_public_answer_is_recorded_as_public(self):
        self._attachment()
        self._comm_v2_row()
        self.mux_result = {**self.mux_result, "playback_policy": "public"}

        self._run()

        self.assertEqual(self._fetch()["mux_playback_policy"], "public")

    def test_a_forwarded_video_inherits_the_policy_with_the_ids(self):
        """The ids and the policy are one fact, and splitting them is a 403.

        Forwarding reuses the asset rather than paying for a second encode. A
        copy that inherited a signed playback id but not the record of it being
        signed would be served unsigned -- black in the new conversation, fine in
        the original.
        """
        self._attachment()
        self._comm_v2_row(COMM_V2_ID, asset_id="asset-601", playback_id="vod601", mux_status="ready", policy="signed")
        self._comm_v2_row(700)

        result = self._run()

        self.assertEqual(result["reason"], "reused_existing_asset")
        forwarded = self._fetch(700)
        self.assertEqual(forwarded["mux_playback_id"], "vod601")
        self.assertEqual(forwarded["mux_playback_policy"], "signed")

    def test_without_signing_keys_no_asset_is_created_at_all(self):
        """Refusal, not a fallback to public.

        Without keys the read path cannot mint a token, so it serves the
        progressive URL -- meaning a signed asset created here would be an encode
        billed for a rendition no viewer is ever shown. Asking for ``public``
        instead to 'make it work' is precisely the exposure this file exists to
        prevent. So: no asset, and the video keeps the progressive path it is on
        today.
        """
        self._attachment()
        self._comm_v2_row()
        os.environ["MUX_SIGNING_KEY_ID"] = ""

        result = self._run()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "signed_playback_unavailable")
        self.assertEqual(self.mux_calls, [])
        self.assertEqual(self._fetch()["mux_asset_id"], "")

    def test_a_deploy_window_without_the_policy_column_still_ingests(self):
        """`media_worker` can reach a video before the web process adds the column.

        On Postgres naming a missing column aborts the transaction, taking the
        thumbnail and duration this job already wrote with it. Missing column
        degrades to 'policy unrecorded', which the read path treats as public --
        progressive playback rather than a 403.
        """
        self._attachment()
        self._comm_v2_row()
        self.cur.executescript(
            """
            CREATE TABLE tmp_att AS SELECT id, message_id, conversation_id, media_upload_id,
                media_type, storage_provider, url, playback_url, thumbnail_url, mime_type,
                mux_asset_id, mux_playback_id, mux_status, created_at FROM comm_v2_attachments;
            DROP TABLE comm_v2_attachments;
            ALTER TABLE tmp_att RENAME TO comm_v2_attachments;
            """
        )
        self.conn.commit()

        result = self._run()

        self.assertEqual(result["status"], "processed")
        self.assertEqual(self._fetch()["mux_asset_id"], "asset-601")


class TheReadPathMintsATokenRatherThanReadingOne(unittest.TestCase):
    """`_attachment_payload` is the only place a viewer-facing manifest URL exists.

    It cannot be a stored column for a signed asset: the token expires, and a
    stored bare URL is a 403. Every assertion below is really the same one --
    a signed row's ``url`` is either tokenised or progressive, and never a bare
    ``stream.mux.com`` manifest.
    """

    def setUp(self):
        self.signed_calls = []
        self._real_signed = None
        from services import mux_live_service

        self.mux_live_service = mux_live_service
        self._real_signed = mux_live_service.signed_playback_url
        mux_live_service.signed_playback_url = self._fake_signed
        self.signed_result = SIGNED_TOKEN_URL

    def tearDown(self):
        self.mux_live_service.signed_playback_url = self._real_signed

    def _fake_signed(self, playback_id):
        self.signed_calls.append(playback_id)
        return self.signed_result

    def _row(self, **overrides):
        row = {
            "id": COMM_V2_ID,
            "media_upload_id": ATTACHMENT_ID,
            "media_type": "video",
            "storage_provider": "messenger_media_foundation",
            "url": PROGRESSIVE,
            "playback_url": PROGRESSIVE,
            "thumbnail_url": f"/api/messages/media/{ATTACHMENT_ID}/thumbnail",
            "mux_asset_id": "asset-601",
            "mux_playback_id": "vod601",
            "mux_status": "ready",
            "mux_playback_policy": "signed",
        }
        row.update(overrides)
        return row

    def test_a_ready_signed_video_is_served_a_tokenised_manifest(self):
        payload = comm_v2._attachment_payload(self._row())

        self.assertEqual(payload["playback_url"], SIGNED_TOKEN_URL)
        # `url` is what the player is handed.
        self.assertEqual(payload["url"], SIGNED_TOKEN_URL)
        self.assertEqual(self.signed_calls, ["vod601"])

    def test_nothing_is_signed_before_the_asset_is_ready(self):
        """Mux issues the playback id minutes before a manifest exists.

        A token on a 404 is still a 404, and a 404 manifest paints black.
        """
        payload = comm_v2._attachment_payload(self._row(mux_status="preparing"))

        self.assertEqual(payload["url"], PROGRESSIVE)
        self.assertEqual(self.signed_calls, [])

    def test_signing_being_unavailable_degrades_to_slow_not_to_broken(self):
        """A key that no longer parses returns "" from `signed_playback_url`."""
        self.signed_result = ""

        payload = comm_v2._attachment_payload(self._row())

        self.assertEqual(payload["url"], PROGRESSIVE)
        self.assertNotIn("stream.mux.com", payload["url"])

    def test_a_bare_manifest_url_stored_on_a_signed_row_is_not_served(self):
        """Defence against a future writer, and against the row already existing.

        The webhook is gated on the policy so it should never store one, but this
        payload is the last thing between the database and the player. A bare URL
        on a signed asset is a 403, and the progressive URL beside it works.
        """
        self.signed_result = ""

        payload = comm_v2._attachment_payload(self._row(playback_url=BARE_HLS))

        self.assertEqual(payload["url"], PROGRESSIVE)

    def test_a_stored_bare_url_is_replaced_by_the_token_when_one_can_be_minted(self):
        payload = comm_v2._attachment_payload(self._row(playback_url=BARE_HLS))

        self.assertEqual(payload["url"], SIGNED_TOKEN_URL)

    def test_a_public_asset_is_untouched(self):
        """The narrowness of the change. Reel and chat_media_uploads rows are public."""
        payload = comm_v2._attachment_payload(self._row(mux_playback_policy="public", playback_url=BARE_HLS))

        self.assertEqual(payload["url"], BARE_HLS)
        self.assertEqual(self.signed_calls, [])

    def test_a_row_shape_without_the_policy_key_is_not_served_a_bare_manifest(self):
        """`_attachment_payload` accepts hand-built dicts from the send path.

        Those have no policy key at all, so the payload treats them as public --
        which is safe only because the column holds the progressive URL for
        signed rows. This pins the pairing: absent policy must still not produce
        a bare manifest URL out of a playback id.
        """
        row = self._row()
        row.pop("mux_playback_policy")

        payload = comm_v2._attachment_payload(row)

        self.assertEqual(payload["url"], PROGRESSIVE)
        self.assertNotIn("stream.mux.com", payload["url"])

    def test_an_empty_playback_url_does_not_become_a_bare_manifest_for_a_signed_row(self):
        """The pre-existing derive-from-playback-id branch feeds the same strip.

        `if mux_playback_id and not playback_url` mints an *unsigned* HLS URL.
        It is deliberately NOT gated on the policy -- the strip below catches it,
        and a second gate would be a guard no test could tell apart from the
        strip, i.e. a protection that rots unnoticed. This asserts the outcome
        rather than the mechanism, so the single enforcement point stays single.
        """
        payload = comm_v2._attachment_payload(
            self._row(playback_url="", url=PROGRESSIVE, mux_status="preparing")
        )

        self.assertNotIn("stream.mux.com", payload["playback_url"] or "")
        self.assertEqual(payload["url"], PROGRESSIVE)

    def test_the_same_row_public_does_derive_the_bare_manifest(self):
        """The control for the test above: the branch still works where it is safe."""
        payload = comm_v2._attachment_payload(
            self._row(playback_url="", url="", mux_playback_policy="public", mux_status="preparing")
        )

        self.assertEqual(payload["playback_url"], BARE_HLS)


class ThePlaybackSourceIsNotTheDownloadableFile(unittest.TestCase):
    """``url`` is what a player streams; ``download_url`` is what Save writes.

    For a ready signed video those are two different resources, and the wire has
    to say so. A manifest is a few hundred bytes of text naming segments -- hand
    it to a downloader and the transfer *succeeds*, a ``.m3u8`` lands in the
    cache, and the photo-library write is the thing that refuses it. The user is
    told their library rejected a video that is playing on their screen, and
    nothing raises anywhere near the cause.

    This is not new with signed playback. ``url`` has been overwritten with
    ``playback_url`` for Mux-backed video since long before messenger used Mux
    at all, so the legacy ``chat_media_uploads`` path has had exactly this bug;
    the signed policy is what would have extended it to every conversation
    video.
    """

    def _row(self, **overrides):
        row = {
            "id": COMM_V2_ID,
            "media_upload_id": ATTACHMENT_ID,
            "media_type": "video",
            "storage_provider": "messenger_media_foundation",
            "url": PROGRESSIVE,
            "cdn_url": "",
            "playback_url": BARE_HLS,
            "mux_playback_id": "vod601",
            "mux_status": "ready",
            "mux_playback_policy": "public",
        }
        row.update(overrides)
        return row

    def test_a_streamed_video_still_reports_the_file_it_was_uploaded_as(self):
        payload = comm_v2._attachment_payload(self._row())

        # The two disagree, which is the entire point of the field.
        self.assertEqual(payload["url"], BARE_HLS)
        self.assertEqual(payload["download_url"], PROGRESSIVE)

    def test_a_photo_reports_the_same_url_for_both(self):
        """The control. Images have one resource and must keep working.

        A `download_url` that were only ever populated for video would make
        every client special-case it, which is how the field would rot.
        """
        payload = comm_v2._attachment_payload(
            self._row(media_type="image", playback_url="", mux_playback_id="", mux_status="")
        )

        self.assertEqual(payload["url"], PROGRESSIVE)
        self.assertEqual(payload["download_url"], PROGRESSIVE)

    def test_a_manifest_is_refused_rather_than_offered_as_a_file(self):
        """Empty beats wrong.

        A client reading "" falls back to ``url`` and fails where the failure
        can be reported. A client handed a playlist saves a playlist.
        """
        payload = comm_v2._attachment_payload(self._row(url=BARE_HLS, cdn_url=""))

        self.assertEqual(payload["download_url"], "")

    def test_a_signed_manifest_is_recognised_through_its_token(self):
        """The trap. A signed manifest is ``.m3u8?token=<jwt>``.

        Anything testing the whole URL rather than its path stops recognising a
        manifest at exactly the moment messenger starts signing them -- so this
        asserts the signed form specifically, not just the bare one above.
        """
        payload = comm_v2._attachment_payload(self._row(url=SIGNED_TOKEN_URL, cdn_url=""))

        self.assertEqual(payload["download_url"], "")

    def test_the_cdn_url_is_used_when_the_row_only_streams(self):
        payload = comm_v2._attachment_payload(self._row(url=BARE_HLS, cdn_url=PROGRESSIVE))

        self.assertEqual(payload["download_url"], PROGRESSIVE)

    def test_the_predicate_names_both_of_muxs_playlist_formats(self):
        """DASH is asserted directly because no row can currently produce one.

        Mux hands messenger an ``.m3u8`` today, so a payload-level test cannot
        reach the ``.mpd`` branch -- and an unreachable branch is one nobody
        would notice being deleted. Pinning it here keeps it honest: this is a
        pure predicate, `.mpd` is unambiguously within the question it answers,
        and the day DASH is switched on the alternative is Save to Photos
        silently writing a playlist again.

        The negatives matter as much as the positives. A predicate that answered
        "yes" too often would strip the download URL off perfectly good files
        and break Save for video that never went near Mux.
        """
        for manifest in (
            "https://stream.mux.com/vod601.m3u8",
            "https://stream.mux.com/vod601.m3u8?token=abc.def.ghi",
            "https://stream.mux.com/vod601.mpd",
            "https://stream.mux.com/vod601.mpd?token=abc.def.ghi",
            "https://stream.mux.com/VOD601.M3U8",
        ):
            with self.subTest(manifest=manifest):
                self.assertTrue(comm_v2._is_adaptive_manifest(manifest))

        for file_url in (
            PROGRESSIVE,
            "https://cdn.example/clip.mp4",
            # The false positive in the other direction: a query parameter that
            # happens to end in a playlist name. A check against the whole URL
            # calls this a manifest and refuses to let the user save an mp4.
            "https://cdn.example/clip.mp4?next=intro.m3u8",
            "",
            None,
        ):
            with self.subTest(file_url=file_url):
                self.assertFalse(comm_v2._is_adaptive_manifest(file_url))


class ThePolicyIsRecordedInTheSchema(unittest.TestCase):
    def test_a_fresh_install_has_the_column(self):
        """`ensure_schema` adds it to existing tables; `models` must create it too.

        Otherwise a brand-new deployment never records a policy, every signed
        asset reads as public, and every conversation video is black.
        """
        from pulse_communications_v2.models import ensure_schema

        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        ensure_schema(cur)
        conn.commit()
        columns = [r[1] for r in cur.execute("PRAGMA table_info(comm_v2_attachments)").fetchall()]
        conn.close()

        self.assertIn("mux_playback_policy", columns)


if __name__ == "__main__":
    unittest.main()
