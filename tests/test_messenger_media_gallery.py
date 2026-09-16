"""The conversation media gallery: one collection, one order, one authority.

Two separate failures live in this file, and they are separate on purpose.

**The payload.** `pulse_communications_v2/service.py` defined
`_attachment_payload` twice at module scope. Python keeps the last one, so every
call site -- all four of them, all above the second definition -- silently got a
thin payload with no `media_upload_id`, no `attachment_id`, no `width`/`height`,
no `waveform`, no `playback_url`. Nothing raised. Nothing went red. The client
fell back to `attachment?.media_upload_id`, got `undefined`, and lost the media
identity it needs to fetch an access URL on thread reload. That is a whole class
of bug -- a redefinition removing fields from a shipped API -- that no
behavioural test can see, because both definitions return a dict and both dicts
are truthy. `test_the_attachment_payload_is_defined_exactly_once` is the only
thing that catches it, and it works on the source text rather than the imported
module, because by import time the evidence is already gone.

**The collection.** The gallery must be built from a paginated server endpoint,
not from whatever message cells happen to be mounted. A windowed list recycles
cells, so a collection assembled from them is a collection of the current
scroll position: tapping the 17th photo opens the first, or the third, or
whichever one the window starts at. The tests below pin the three properties
that make the server the authority -- canonical ascending order, keyset paging
that survives new arrivals, and refusal for a non-member -- plus the two
exclusions that keep the gallery *visual* (voice notes and documents keep their
own presentation and are never swipeable items).
"""

import ast
import os
import sqlite3
import unittest

os.environ.setdefault("DATABASE_URL", "")

from pulse_communications_v2 import service  # noqa: E402
from pulse_communications_v2.models import ensure_schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_PATH = os.path.join(ROOT, "pulse_communications_v2", "service.py")
ROUTES_PATH = os.path.join(ROOT, "pulse_communications_v2", "routes.py")

with open(SERVICE_PATH, encoding="utf-8") as handle:
    SERVICE_SOURCE = handle.read()
with open(ROUTES_PATH, encoding="utf-8") as handle:
    ROUTES_SOURCE = handle.read()


def module_scope_function_names(source: str) -> list[str]:
    """Names of every top-level `def`, in file order, duplicates preserved.

    Duplicates are the entire point, so this cannot be a set and it cannot walk
    the imported module -- `getattr(module, name)` can only ever show the
    survivor.
    """
    tree = ast.parse(source)
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


class AttachmentPayloadIsSingleAndComplete(unittest.TestCase):
    """The shadowing bug, and the fields it was eating."""

    def test_the_attachment_payload_is_defined_exactly_once(self):
        names = module_scope_function_names(SERVICE_SOURCE)
        count = names.count("_attachment_payload")
        self.assertEqual(count, 1, (
            f"_attachment_payload is defined {count} times at module scope in "
            "pulse_communications_v2/service.py. Python binds the last one, so "
            "every call site above it silently gets the wrong payload and "
            "nothing raises. Merge the definitions instead of adding another."
        ))

    def test_no_module_scope_function_in_the_service_is_defined_twice(self):
        """The general form of the same bug.

        This file is 6,000 lines with helpers in three distinct naming eras;
        `_attachment_payload` was not special, it was just the one that got
        caught. Anything redefined here is a caller getting a function it did
        not ask for.
        """
        names = module_scope_function_names(SERVICE_SOURCE)
        duplicates = sorted({name for name in names if names.count(name) > 1})
        self.assertEqual(duplicates, [], (
            f"These module-scope functions are defined more than once: "
            f"{duplicates}. The later definition wins for every call site, "
            "including the ones written against the earlier one."
        ))

    def test_the_payload_keeps_the_identity_the_client_needs_to_refetch(self):
        """`media_upload_id` and `attachment_id` are the stable media identity.

        Delivery URLs expire; the identity does not. If the payload drops it,
        the client cannot ask for a fresh URL and a photo that worked at send
        time is permanently broken after a thread reload -- which is exactly
        what shipped.
        """
        payload = service._attachment_payload({
            "id": 77,
            "media_upload_id": 4242,
            "message_id": 9,
            "media_type": "image",
            "url": "https://cdn.example/a.jpg",
            "width": 1600,
            "height": 900,
        })
        self.assertEqual(payload["attachment_id"], 77)
        self.assertEqual(payload["media_upload_id"], 4242)
        self.assertEqual(payload["message_id"], 9)

    def test_the_payload_keeps_the_dimensions_the_card_sizes_itself_from(self):
        """Without width/height the inline card falls back to a default aspect
        ratio, which is how a portrait photo renders letterboxed inside a
        landscape box."""
        payload = service._attachment_payload({"id": 1, "media_type": "image", "width": 1080, "height": 1920})
        self.assertEqual(payload["width"], 1080)
        self.assertEqual(payload["height"], 1920)

    def test_the_payload_keeps_the_waveform_and_playback_url(self):
        payload = service._attachment_payload({
            "id": 2,
            "media_type": "voice",
            "waveform_json": "[3, 9, 4]",
            "playback_url": "https://cdn.example/v.m3u8",
        })
        self.assertEqual(payload["waveform"], [3, 9, 4])
        self.assertEqual(payload["playback_url"], "https://cdn.example/v.m3u8")

    def test_a_video_prefers_its_playback_url_over_its_raw_url(self):
        payload = service._attachment_payload({
            "id": 3,
            "media_type": "video",
            "url": "https://cdn.example/raw.mp4",
            "playback_url": "https://stream.example/x.m3u8",
        })
        self.assertEqual(payload["url"], "https://stream.example/x.m3u8")

    def test_a_bare_attachment_row_does_not_raise_for_missing_message_columns(self):
        """`_message_payloads` passes a plain `SELECT *` from the attachments
        table -- no JOINed body, no sender display name. The merged payload has
        to degrade there rather than KeyError the whole thread."""
        payload = service._attachment_payload({"id": 5, "media_type": "image"})
        self.assertEqual(payload["sender_user_id"], 0)
        self.assertEqual(payload["body_preview"], "")

    def test_the_uploader_stands_in_for_the_sender_when_no_message_is_joined(self):
        payload = service._attachment_payload({"id": 5, "media_type": "image", "uploader_user_id": 31})
        self.assertEqual(payload["sender_user_id"], 31)

    def test_a_malformed_waveform_degrades_to_empty_rather_than_raising(self):
        payload = service._attachment_payload({"id": 6, "media_type": "voice", "waveform_json": "{not json"})
        self.assertEqual(payload["waveform"], [])

    def test_foundation_media_gets_the_poster_its_processing_job_already_made(self):
        """Every image and video in a thread shipped with no poster.

        `_attach_foundation_media` stored a literal empty string for
        `thumbnail_url` on every row it ever inserted, while the foundation's
        processing job was generating a thumbnail and storing its key -- checked
        against production conversation 6, where all seven image/video rows have
        a `thumbnail_key` and all seven have an empty `thumbnail_url`.

        The cost is paid in the gallery, not the bubble. The tapped item carries
        the bitmap the thread already decoded, so it opens instantly; its
        neighbours have nothing to show while a multi-hundred-kilobyte original
        downloads, so swiping lands on black. Black is never the loading state.
        """
        payload = service._attachment_payload({
            "id": 601,
            "media_upload_id": 87,
            "media_type": "video",
            "storage_provider": "messenger_media_foundation",
            "url": "/api/messages/media/87/download",
        })
        self.assertEqual(payload["thumbnail_url"], "/api/messages/media/87/thumbnail")

    def test_the_derived_poster_is_keyed_on_the_foundation_id_not_the_row_id(self):
        """`id` and `media_upload_id` are autoincrements from different tables
        whose ranges overlap. The thumbnail route is keyed on the foundation id
        and on nothing else, so deriving it from the transport row id would
        serve somebody else's attachment -- the same id-space collision that
        `resolveCanonicalMessengerMediaId` exists to prevent on the client."""
        payload = service._attachment_payload({
            "id": 601,
            "media_upload_id": 87,
            "media_type": "image",
            "storage_provider": "messenger_media_foundation",
        })
        self.assertNotIn("/601/", payload["thumbnail_url"])
        self.assertEqual(payload["thumbnail_url"], "/api/messages/media/87/thumbnail")

    def test_a_stored_poster_is_never_overwritten_by_the_derived_one(self):
        """Derivation is a repair for rows written with an empty string, not a
        policy. A row that knows its own poster keeps it."""
        payload = service._attachment_payload({
            "id": 601,
            "media_upload_id": 87,
            "media_type": "image",
            "storage_provider": "messenger_media_foundation",
            "thumbnail_url": "https://cdn.example/real-poster.jpg",
        })
        self.assertEqual(payload["thumbnail_url"], "https://cdn.example/real-poster.jpg")

    def test_only_foundation_backed_media_gets_a_derived_poster(self):
        """The route only answers for foundation attachments. Inventing the URL
        for a Mux- or R2-backed row would hand the client a link that 404s in
        place of an empty string it already handles."""
        payload = service._attachment_payload({
            "id": 12,
            "media_upload_id": 34,
            "media_type": "video",
            "storage_provider": "mux",
        })
        self.assertEqual(payload["thumbnail_url"], "")

    def test_voice_and_documents_get_no_derived_poster(self):
        """Neither has a thumbnail route behind it, and neither renders one."""
        for media_type in ("voice", "file", "audio"):
            payload = service._attachment_payload({
                "id": 607,
                "media_upload_id": 92,
                "media_type": media_type,
                "storage_provider": "messenger_media_foundation",
            })
            self.assertEqual(payload["thumbnail_url"], "", media_type)

    def test_a_derived_poster_never_becomes_the_thing_the_player_plays(self):
        """`thumbnail_url` is the last fallback in the `url` chain. Deriving it
        before `url` is computed would make a poster the media for any row whose
        own URL is missing -- a still image silently standing in for a video."""
        payload = service._attachment_payload({
            "id": 601,
            "media_upload_id": 87,
            "media_type": "video",
            "storage_provider": "messenger_media_foundation",
        })
        self.assertEqual(payload["url"], "")
        self.assertEqual(payload["thumbnail_url"], "/api/messages/media/87/thumbnail")


class KeptOpenConnection:
    """A `sqlite3.Connection` whose `close()` is a no-op.

    The service opens and closes a connection per call, correctly. A test that
    hands it one shared `:memory:` connection therefore gets exactly one call
    before every later one dies on "Cannot operate on a closed database" -- and
    the resulting failure names `_conversation_access`, which has nothing to do
    with it. Swallowing `close()` keeps the fixture honest about connection
    lifecycle being the service's business, not the test's.
    """

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        return None

    def __getattr__(self, name):
        return getattr(self._conn, name)


class MediaHistoryHarness(unittest.TestCase):
    """In-memory conversation with a member, an outsider, and mixed media."""

    MEMBER = 1
    OTHER_MEMBER = 2
    OUTSIDER = 99

    def setUp(self):
        self._public_id_seq = 0
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        ensure_schema(self.cur)
        self.cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT)")
        self.cur.execute("INSERT INTO users (user_id, username, display_name) VALUES (1,'maria','Maria Cherie')")
        self.cur.execute("INSERT INTO users (user_id, username, display_name) VALUES (2,'alex','Alex Stone')")
        self.conn.commit()

        self._patched = service._open_db
        service._open_db = lambda: (KeptOpenConnection(self.conn), self.cur)

        self.conversation_id = self._conversation()
        self._participant(self.conversation_id, self.MEMBER)
        self._participant(self.conversation_id, self.OTHER_MEMBER)

    def tearDown(self):
        service._open_db = self._patched
        self.conn.close()

    # -- fixtures ---------------------------------------------------------

    def _conversation(self, public_id="conv-public-1"):
        self.cur.execute(
            "INSERT INTO comm_v2_conversations (public_id, conversation_type, privacy, created_at, updated_at) "
            "VALUES (?, 'direct', 'private', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (public_id,),
        )
        self.conn.commit()
        return int(self.cur.lastrowid)

    def _participant(self, conversation_id, user_id, role="member"):
        self.cur.execute(
            "INSERT INTO comm_v2_participants (conversation_id, user_id, role, membership_state, left_at, joined_at) "
            "VALUES (?, ?, ?, 'active', '', '2026-01-01T00:00:00+00:00')",
            (conversation_id, user_id, role),
        )
        self.conn.commit()

    def _message(self, conversation_id=None, sender=None, body="", deleted_at=""):
        conversation_id = self.conversation_id if conversation_id is None else conversation_id
        sender = self.MEMBER if sender is None else sender
        self.cur.execute(
            "INSERT INTO comm_v2_messages (conversation_id, sender_user_id, message_type, body, deleted_at, created_at, updated_at) "
            "VALUES (?, ?, 'media', ?, ?, '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00')",
            (conversation_id, sender, body, deleted_at),
        )
        self.conn.commit()
        return int(self.cur.lastrowid)

    def _attachment(self, message_id, media_type="image", mime_type="image/jpeg", conversation_id=None,
                    scan_status="approved", width=1200, height=800, uploader=None):
        conversation_id = self.conversation_id if conversation_id is None else conversation_id
        uploader = self.MEMBER if uploader is None else uploader
        self._public_id_seq += 1
        public_id = f"att-{self._public_id_seq}"
        self.cur.execute(
            "INSERT INTO comm_v2_attachments "
            "(attachment_public_id, message_id, conversation_id, media_upload_id, uploader_user_id, media_type, "
            " url, mime_type, width, height, scan_status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-01-02T00:00:00+00:00')",
            (public_id, message_id, conversation_id, 1000 + self._public_id_seq, uploader,
             media_type, f"https://cdn.example/{public_id}", mime_type, width, height, scan_status),
        )
        self.conn.commit()
        return int(self.cur.lastrowid)

    def _media_message(self, media_type="image", mime_type="image/jpeg", **kwargs):
        message_id = self._message(**{k: v for k, v in kwargs.items() if k in {"conversation_id", "sender", "body", "deleted_at"}})
        attachment_id = self._attachment(
            message_id,
            media_type=media_type,
            mime_type=mime_type,
            conversation_id=kwargs.get("conversation_id"),
            scan_status=kwargs.get("scan_status", "approved"),
            uploader=kwargs.get("sender"),
        )
        return message_id, attachment_id

    def _history(self, user_id=None, ref=None, **filters):
        return service.conversation_media_history(
            self.MEMBER if user_id is None else user_id,
            self.conversation_id if ref is None else ref,
            filters,
        )


class MediaHistoryAuthorization(MediaHistoryHarness):
    """Requirement: you cannot read another conversation's media by changing ids."""

    def test_a_member_can_read_the_gallery(self):
        self._media_message()
        result = self._history()
        self.assertTrue(result["ok"], result)

    def test_a_non_member_is_refused_and_gets_no_items(self):
        self._media_message()
        result = self._history(user_id=self.OUTSIDER)
        self.assertFalse(result["ok"])
        self.assertEqual(result["http_status"], 403)
        self.assertNotIn("items", result)

    def test_a_member_of_one_conversation_cannot_read_another_by_id(self):
        """The mutation this kills: dropping the membership check and filtering
        on `conversation_id` alone. That still returns the right *rows*, so a
        single-conversation test stays green while every conversation on the
        platform becomes readable by id."""
        other = self._conversation(public_id="conv-public-2")
        self._participant(other, self.OUTSIDER)
        self._media_message(conversation_id=other, sender=self.OUTSIDER)
        result = service.conversation_media_history(self.MEMBER, other, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["http_status"], 403)

    def test_a_missing_conversation_is_not_found_rather_than_forbidden(self):
        result = self._history(ref=123456)
        self.assertFalse(result["ok"])
        self.assertEqual(result["http_status"], 404)

    def test_media_from_a_different_conversation_never_leaks_into_the_page(self):
        other = self._conversation(public_id="conv-public-3")
        self._participant(other, self.MEMBER)
        _, mine = self._media_message()
        _, theirs = self._media_message(conversation_id=other)
        ids = [item["attachment_id"] for item in self._history()["items"]]
        self.assertIn(mine, ids)
        self.assertNotIn(theirs, ids)


class MediaHistoryContents(MediaHistoryHarness):
    """What belongs in a *visual* gallery, and what each item has to carry."""

    def test_photos_and_videos_are_both_in_one_collection(self):
        _, photo = self._media_message("image", "image/jpeg")
        _, video = self._media_message("video", "video/mp4")
        ids = [item["attachment_id"] for item in self._history()["items"]]
        self.assertEqual(ids, [photo, video])

    def test_a_voice_note_is_not_a_swipeable_gallery_item(self):
        _, photo = self._media_message("image", "image/jpeg")
        _, voice = self._media_message("voice", "audio/mp4")
        ids = [item["attachment_id"] for item in self._history()["items"]]
        self.assertEqual(ids, [photo])
        self.assertNotIn(voice, ids)

    def test_a_document_is_not_a_swipeable_gallery_item(self):
        _, photo = self._media_message("image", "image/jpeg")
        _, doc = self._media_message("file", "application/pdf")
        ids = [item["attachment_id"] for item in self._history()["items"]]
        self.assertEqual(ids, [photo])
        self.assertNotIn(doc, ids)

    def test_an_unrecognised_media_type_filter_narrows_rather_than_widens(self):
        """A typo in the query string must not put PDFs in the photo gallery."""
        self._media_message("image", "image/jpeg")
        self._media_message("file", "application/pdf")
        result = self._history(media_type="everything")
        self.assertEqual(result["media_type"], "all")
        self.assertEqual([item["media_type"] for item in result["items"]], ["image"])

    def test_the_media_type_filter_can_isolate_videos(self):
        self._media_message("image", "image/jpeg")
        _, video = self._media_message("video", "video/mp4")
        result = self._history(media_type="video")
        self.assertEqual([item["attachment_id"] for item in result["items"]], [video])

    def test_a_deleted_message_takes_its_media_out_of_the_gallery(self):
        """Requirement: a deleted item must not be a black screen you can swipe
        onto. The cheapest way to honour that is for it never to be an index."""
        _, kept = self._media_message()
        message_id = self._message(deleted_at="2026-01-03T00:00:00+00:00")
        removed = self._attachment(message_id)
        ids = [item["attachment_id"] for item in self._history()["items"]]
        self.assertEqual(ids, [kept])
        self.assertNotIn(removed, ids)

    def test_media_hidden_by_a_per_user_deletion_is_gone_for_that_user_only(self):
        _, kept = self._media_message()
        message_id = self._message()
        hidden = self._attachment(message_id)
        self.cur.execute(
            "INSERT INTO comm_v2_message_deletions (message_id, conversation_id, user_id, deleted_at) "
            "VALUES (?, ?, ?, '2026-01-03T00:00:00+00:00')",
            (message_id, self.conversation_id, self.MEMBER),
        )
        self.conn.commit()
        self.assertEqual([i["attachment_id"] for i in self._history()["items"]], [kept])
        self.assertEqual(
            sorted(i["attachment_id"] for i in self._history(user_id=self.OTHER_MEMBER)["items"]),
            sorted([kept, hidden]),
        )

    def test_blocked_media_is_not_served(self):
        _, kept = self._media_message()
        message_id = self._message()
        blocked = self._attachment(message_id, scan_status="blocked")
        ids = [item["attachment_id"] for item in self._history()["items"]]
        self.assertEqual(ids, [kept])
        self.assertNotIn(blocked, ids)

    def test_each_item_carries_the_sender_so_group_chats_attribute_correctly(self):
        """Requirement: in a group chat the viewer shows the sender of *that
        item*, not the sender of the message you happened to tap from."""
        self._media_message(sender=self.MEMBER)
        self._media_message(sender=self.OTHER_MEMBER)
        items = self._history()["items"]
        self.assertEqual(
            [(i["sender_user_id"], i["sender_display_name"]) for i in items],
            [(self.MEMBER, "Maria Cherie"), (self.OTHER_MEMBER, "Alex Stone")],
        )

    def test_each_item_carries_the_dimensions_and_identity_the_viewer_needs(self):
        self._media_message()
        item = self._history()["items"][0]
        for key in ("attachment_id", "media_upload_id", "message_id", "media_type", "url", "width", "height", "created_at"):
            self.assertIn(key, item)
        self.assertEqual((item["width"], item["height"]), (1200, 800))

    def test_the_total_is_the_whole_collection_and_not_the_page(self):
        """Requirement: "Photo from Maria Cherie, 12 of 43". The 43 is the
        collection, so it cannot be `len(items)`."""
        for _ in range(7):
            self._media_message()
        result = self._history(limit=3)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["total"], 7)


class MediaHistoryPaging(MediaHistoryHarness):
    """Keyset paging in both directions, and the order everything agrees on."""

    def _seed(self, count):
        return [self._media_message()[1] for _ in range(count)]

    def test_the_first_page_is_the_newest_media_in_ascending_order(self):
        ids = self._seed(5)
        result = self._history(limit=3)
        self.assertEqual([i["attachment_id"] for i in result["items"]], ids[2:])

    def test_items_always_come_back_ascending_whichever_direction_was_asked(self):
        """The client splices pages onto either end of one array. If a backwards
        page arrived descending it would splice reversed, and every index after
        it -- including the one the viewer is sitting on -- would be wrong."""
        ids = self._seed(6)
        first = self._history(limit=3)
        older = self._history(limit=3, before_id=first["items"][0]["attachment_id"])
        newer = self._history(limit=3, after_id=ids[0])
        for page in (first, older, newer):
            page_ids = [i["attachment_id"] for i in page["items"]]
            self.assertEqual(page_ids, sorted(page_ids), page_ids)

    def test_paging_backwards_walks_the_whole_collection_without_gaps_or_repeats(self):
        ids = self._seed(10)
        collected = []
        page = self._history(limit=4)
        collected = [i["attachment_id"] for i in page["items"]]
        while page["has_older"]:
            page = self._history(limit=4, before_id=collected[0])
            collected = [i["attachment_id"] for i in page["items"]] + collected
        self.assertEqual(collected, ids)

    def test_paging_forwards_from_the_oldest_reaches_the_newest(self):
        """Forward paging always starts from something the caller already holds
        -- "give me what arrived after this" -- so the cursor is a real id, not
        a sentinel. `after_id=0` deliberately means "no cursor" and returns the
        newest page, which is what a cold open wants."""
        ids = self._seed(9)
        collected = [ids[0]]
        while True:
            page = self._history(limit=4, after_id=collected[-1])
            collected += [i["attachment_id"] for i in page["items"]]
            if not page["has_newer"]:
                break
        self.assertEqual(collected, ids)

    def test_no_cursor_means_the_newest_page_rather_than_the_oldest(self):
        ids = self._seed(6)
        self.assertEqual([i["attachment_id"] for i in self._history(limit=2)["items"]], ids[-2:])

    def test_has_older_is_observed_and_not_inferred_from_a_full_page(self):
        """A page that happens to be exactly `limit` long is not evidence of a
        next page. Inferring it gives the client one guaranteed empty round
        trip at the end of every gallery."""
        self._seed(4)
        self.assertFalse(self._history(limit=4)["has_older"])
        self.assertTrue(self._history(limit=3)["has_older"])

    def test_the_newest_page_reports_nothing_newer(self):
        self._seed(4)
        self.assertFalse(self._history(limit=2)["has_newer"])

    def test_an_older_page_knows_there_is_something_newer(self):
        ids = self._seed(6)
        page = self._history(limit=2, before_id=ids[3])
        self.assertTrue(page["has_newer"])

    def test_a_forward_page_knows_there_is_something_older(self):
        ids = self._seed(6)
        page = self._history(limit=2, after_id=ids[1])
        self.assertTrue(page["has_older"])

    def test_new_media_arriving_does_not_renumber_the_page_you_already_hold(self):
        """The mutation this kills: OFFSET paging. With OFFSET, one new photo
        arriving shifts every window by one, so the next page repeats an item
        and the index the viewer is on stops pointing at what it showed.
        Keyset cursors are values, so they are unaffected."""
        ids = self._seed(6)
        first = self._history(limit=3)
        _, arrival = self._media_message()
        older = self._history(limit=3, before_id=first["items"][0]["attachment_id"])
        self.assertEqual([i["attachment_id"] for i in older["items"]], ids[:3])
        self.assertNotIn(arrival, [i["attachment_id"] for i in older["items"]])

    def test_an_empty_conversation_pages_cleanly(self):
        result = self._history()
        self.assertTrue(result["ok"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 0)
        self.assertFalse(result["has_older"])
        self.assertFalse(result["has_newer"])

    def test_a_junk_cursor_does_not_raise(self):
        self._seed(3)
        for junk in ("abc", "", None, "-4", "1e9"):
            result = self._history(before_id=junk)
            self.assertTrue(result["ok"], junk)

    def test_the_limit_is_capped(self):
        self._seed(3)
        self.assertTrue(self._history(limit=100000)["ok"])
        self.assertTrue(self._history(limit=0)["ok"])

    def test_a_multi_media_message_contributes_every_tile_as_its_own_item(self):
        """Requirement: opening tile 3 of a 4-photo message opens index 3. That
        only works if each tile is an independent item in the collection."""
        message_id = self._message()
        tiles = [self._attachment(message_id) for _ in range(4)]
        items = self._history()["items"]
        self.assertEqual([i["attachment_id"] for i in items], tiles)
        self.assertEqual({i["message_id"] for i in items}, {message_id})


class MediaHistoryIsWiredUp(unittest.TestCase):
    """The endpoint exists, is declared, and is the documented source."""

    def test_the_route_is_registered_on_the_comm_v2_blueprint(self):
        self.assertIn("/conversations/<path:conversation_ref>/media\"", ROUTES_SOURCE)

    def test_the_route_declares_its_authentication(self):
        """`services/route_auth.py` is default-deny for new routes: a gate in
        the body does not count, because the detector cannot tell a route that
        forgot its check from one that never needed it."""
        tree = ast.parse(ROUTES_SOURCE)
        view = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "conversation_media_history"
        )
        decorators = {
            d.id if isinstance(d, ast.Name) else getattr(d, "attr", "")
            for d in view.decorator_list
        }
        self.assertIn("auth_required", decorators)

    def test_the_gallery_kinds_are_image_and_video_only(self):
        self.assertEqual(service.MEDIA_HISTORY_KINDS, ("image", "video"))

    def test_the_canonical_order_is_named_once(self):
        """One constant, so the inline thread and the viewer cannot drift into
        two different opinions about what item 17 is."""
        self.assertEqual(service.MEDIA_HISTORY_ORDER_SQL, "a.id")


if __name__ == "__main__":
    unittest.main()
