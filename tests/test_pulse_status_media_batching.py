"""The Status rail must hydrate a whole lane on one connection.

`pulse_status_payload` used to open its own `db()` connection and run its own
`SELECT ... FROM chat_media_uploads` for every single Status it built. One rail
request builds two lanes, so a user opening the Status tray could check out
dozens of connections from a pool of 8 with 8 overflow and a three second
timeout.

The failure that causes is worth naming, because nothing about it points here:
the Status rail keeps working, and *other* requests start timing out while they
wait for a connection that a rail request is holding. So these tests assert on
the resource shape -- connections opened, statements issued -- rather than on the
payload, because the payload looked perfectly correct the entire time the pool
was draining.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot


def status_row(status_id, media_ids, user_id=4):
    return {
        "id": status_id,
        "user_id": user_id,
        "status_type": "photo",
        "body": "",
        "media_ids_json": json.dumps(media_ids),
        "created_at": f"2026-09-14T10:{status_id:02d}:00",
        "author_name": "Maria Cherie",
    }


def media_row(media_id):
    return {
        "id": media_id,
        "media_type": "image",
        "mime_type": "image/jpeg",
        "media_url": f"https://cdn.coinpilotx.app/pulse_media/{media_id}.jpg",
        "moderation_status": "approved",
        "processing_status": "ready",
        "is_available": 1,
    }


class RecordingCursor:
    """Answers the media query and remembers every statement put through it."""

    def __init__(self, media_rows):
        self._media_rows = {int(row["id"]): row for row in media_rows}
        self.statements = []
        self._result = []

    def execute(self, sql, params=()):
        flat = " ".join(str(sql).split())
        bound = list(params or [])
        self.statements.append((flat, bound))
        wanted = {int(value) for value in bound if str(value).isdigit()}
        self._result = [row for media_id, row in self._media_rows.items() if media_id in wanted]

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None

    def media_statements(self):
        return [sql for sql, _ in self.statements if "chat_media_uploads" in sql]


class RecordingConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = 0
        self.row_factory = None

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed += 1


class PulseStatusMediaBatchingTests(unittest.TestCase):
    def test_a_whole_lane_costs_one_connection_and_one_media_query(self):
        rows = [status_row(index, [700 + index]) for index in range(1, 13)]
        cursor = RecordingCursor([media_row(700 + index) for index in range(1, 13)])
        connection = RecordingConnection(cursor)

        with patch.object(bot, "db", return_value=connection) as db_factory, patch.object(
            bot, "pulse_status_active_rows", return_value=rows
        ):
            items = bot.pulse_status_items_for_lane(viewer_user_id=4, lane="for_you", limit=40)

        self.assertEqual(len(items), 12)
        # One connection for the lane, not one per Status. This is the assertion
        # that would have caught the original: twelve Statuses, thirteen
        # connections, and a pool that only holds sixteen.
        self.assertEqual(db_factory.call_count, 1)
        self.assertEqual(connection.closed, 1)
        self.assertEqual(len(cursor.media_statements()), 1)

    def test_every_status_still_gets_its_own_media(self):
        rows = [status_row(1, [701]), status_row(2, [702]), status_row(3, [703])]
        cursor = RecordingCursor([media_row(701), media_row(702), media_row(703)])
        connection = RecordingConnection(cursor)

        with patch.object(bot, "db", return_value=connection), patch.object(
            bot, "pulse_status_active_rows", return_value=rows
        ):
            items = bot.pulse_status_items_for_lane(viewer_user_id=4, lane="for_you", limit=40)

        # Batching is only a win if it does not cross-contaminate: a shared
        # lookup hands every Status the same dict unless each one is indexed
        # back by its own ids.
        by_status = {int(item["id"]): item for item in items}
        for status_id in (1, 2, 3):
            media = by_status[status_id].get("media") or []
            self.assertEqual([int(entry["id"]) for entry in media], [700 + status_id])

    def test_multi_photo_status_keeps_the_order_the_author_posted(self):
        # The media ids are deliberately out of ascending order. The old code
        # took whatever order the database returned, which for an `IN` list is
        # not promised to be the order asked for.
        rows = [status_row(1, [703, 701, 702])]
        cursor = RecordingCursor([media_row(701), media_row(702), media_row(703)])
        connection = RecordingConnection(cursor)

        with patch.object(bot, "db", return_value=connection), patch.object(
            bot, "pulse_status_active_rows", return_value=rows
        ):
            items = bot.pulse_status_items_for_lane(viewer_user_id=4, lane="for_you", limit=40)

        self.assertEqual([int(entry["id"]) for entry in items[0]["media"]], [703, 701, 702])

    def test_a_status_whose_media_is_gone_does_not_take_the_lane_with_it(self):
        # 702 is missing from the media table -- blocked, swept, or never
        # finished uploading. That Status loses its photo; the others must not.
        rows = [status_row(1, [701]), status_row(2, [702]), status_row(3, [703])]
        cursor = RecordingCursor([media_row(701), media_row(703)])
        connection = RecordingConnection(cursor)

        with patch.object(bot, "db", return_value=connection), patch.object(
            bot, "pulse_status_active_rows", return_value=rows
        ):
            items = bot.pulse_status_items_for_lane(viewer_user_id=4, lane="for_you", limit=40)

        by_status = {int(item["id"]): item for item in items}
        self.assertEqual(by_status[2].get("media") or [], [])
        self.assertEqual(len(by_status[1]["media"]), 1)
        self.assertEqual(len(by_status[3]["media"]), 1)
        self.assertEqual(len(cursor.media_statements()), 1)

    def test_a_lane_with_no_media_at_all_asks_the_media_table_nothing(self):
        rows = [status_row(1, []), status_row(2, [])]
        cursor = RecordingCursor([])
        connection = RecordingConnection(cursor)

        with patch.object(bot, "db", return_value=connection), patch.object(
            bot, "pulse_status_active_rows", return_value=rows
        ):
            items = bot.pulse_status_items_for_lane(viewer_user_id=4, lane="for_you", limit=40)

        self.assertEqual(len(items), 2)
        self.assertEqual(cursor.media_statements(), [])

    def test_a_failing_media_lookup_leaves_a_trace_instead_of_a_silent_gap(self):
        # The block this replaces was a bare `except Exception: media_items = []`.
        # A Status with lost media and a Status whose lookup *crashed* rendered
        # identically, so the second case was undiagnosable from production.
        class ExplodingCursor(RecordingCursor):
            def execute(self, sql, params=()):
                raise RuntimeError("relation chat_media_uploads does not exist")

        cursor = ExplodingCursor([])
        with self.assertLogs("bot", level="ERROR") as captured:
            resolved = bot.pulse_status_media_map([status_row(1, [701])], cur=cursor)

        self.assertEqual(resolved, {})
        self.assertIn("PULSE_STATUS_MEDIA_HYDRATION_FAILED", "\n".join(captured.output))

    def test_the_standalone_path_returns_its_connection_even_when_the_query_fails(self):
        # No cursor passed, so the map opens its own connection. The version
        # this replaces closed only on the success path: the pool drained under
        # exactly the failure that also hid the media.
        class ExplodingCursor(RecordingCursor):
            def execute(self, sql, params=()):
                raise RuntimeError("connection reset")

        connection = RecordingConnection(ExplodingCursor([]))
        with patch.object(bot, "db", return_value=connection):
            with self.assertLogs("bot", level="ERROR"):
                resolved = bot.pulse_status_media_map([status_row(1, [701])])

        self.assertEqual(resolved, {})
        self.assertEqual(connection.closed, 1)

    def test_media_ids_survive_the_junk_a_json_column_can_hold(self):
        self.assertEqual(bot.pulse_status_media_ids({"media_ids_json": "[701, 702]"}), [701, 702])
        # Strings are what the mobile client actually posts.
        self.assertEqual(bot.pulse_status_media_ids({"media_ids_json": '["701","702"]'}), [701, 702])
        self.assertEqual(bot.pulse_status_media_ids({"media_ids_json": "[701, 701]"}), [701])
        self.assertEqual(bot.pulse_status_media_ids({"media_ids_json": "[0, null, 701]"}), [701])
        self.assertEqual(bot.pulse_status_media_ids({"media_ids_json": "not json"}), [])
        self.assertEqual(bot.pulse_status_media_ids({"media_ids_json": '{"a":1}'}), [])
        self.assertEqual(bot.pulse_status_media_ids({}), [])
        self.assertEqual(bot.pulse_status_media_ids(None), [])
        # The eight-item cap the original applied before querying is kept.
        self.assertEqual(len(bot.pulse_status_media_ids({"media_ids_json": json.dumps(list(range(1, 20)))})), 8)

    def test_the_grouped_lane_is_batched_too(self):
        # `api_pulse_status_rail` calls both lane builders, so leaving either one
        # unbatched leaves the per-request connection count roughly where it was.
        # One author per Status, because this lane collapses a creator's Statuses
        # into a single card -- give them all the same author and the lane builds
        # one payload and the batching has nothing to prove.
        rows = [status_row(index, [700 + index], user_id=index) for index in range(1, 9)]
        cursor = RecordingCursor([media_row(700 + index) for index in range(1, 9)])
        connection = RecordingConnection(cursor)

        with patch.object(bot, "db", return_value=connection) as db_factory, patch.object(
            bot, "pulse_status_active_rows", return_value=rows
        ):
            items = bot.pulse_status_grouped_items_for_lane(viewer_user_id=4, lane="for_you", limit=12)

        self.assertEqual(len(items), 8)
        self.assertEqual(db_factory.call_count, 1)
        self.assertEqual(len(cursor.media_statements()), 1)


if __name__ == "__main__":
    unittest.main()
