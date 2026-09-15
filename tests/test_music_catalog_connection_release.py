"""The music catalog must return its connection even when its query fails.

`_load_db_tracks` and `_load_db_track_by_id` both closed their connection on the
success path only, under a `try` whose `except` swallowed the error and returned
an empty list. So a failing catalog query did not just hide the music -- it
permanently cost one slot out of a pool of 8 with 8 overflow.

That ordering is the nasty part. The query most likely to fail is the one under
load, and every failure made the next one likelier, so the pool degrades fastest
exactly when it is needed most. Nothing in the logs pointed here either: the
callers degrade quietly to "no tracks found".

`pulse_audio_tracks` holds roughly 21k rows in production and `search_tracks` is
reachable from the global search route, so this is a live path, not a theoretical
one.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import music_service


class CountingConnection:
    """A connection whose cursor refuses to work, and that counts its closes."""

    def __init__(self, failure):
        self.closed = 0
        self._failure = failure
        self.row_factory = None

    def cursor(self):
        return self

    def execute(self, *_args, **_kwargs):
        raise self._failure

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def close(self):
        self.closed += 1


class MusicCatalogConnectionReleaseTests(unittest.TestCase):
    def test_a_failing_catalog_query_still_returns_its_connection(self):
        connection = CountingConnection(RuntimeError("server closed the connection unexpectedly"))
        with patch.object(music_service, "_connection", return_value=connection):
            tracks = music_service._load_db_tracks(query="midnight")

        self.assertEqual(tracks, [])
        self.assertEqual(connection.closed, 1)

    def test_a_failing_single_track_lookup_still_returns_its_connection(self):
        connection = CountingConnection(RuntimeError("server closed the connection unexpectedly"))
        with patch.object(music_service, "_connection", return_value=connection):
            track = music_service._load_db_track_by_id("4821")

        self.assertEqual(track, {})
        self.assertEqual(connection.closed, 1)

    def test_repeated_failures_do_not_accumulate_checked_out_connections(self):
        # The shape of the original bug: not one lost connection, but one per
        # failure until the pool is gone.
        connections = []

        def factory():
            connection = CountingConnection(RuntimeError("deadlock detected"))
            connections.append(connection)
            return connection

        with patch.object(music_service, "_connection", side_effect=factory):
            for _ in range(20):
                music_service._load_db_tracks()

        self.assertEqual(len(connections), 20)
        self.assertTrue(all(connection.closed == 1 for connection in connections))

    def test_the_success_path_still_closes_exactly_once(self):
        # A `finally` on top of an existing `conn.close()` would double-close.
        class WorkingConnection(CountingConnection):
            def __init__(self):
                super().__init__(RuntimeError("unused"))

            def execute(self, *_args, **_kwargs):
                return None

        connection = WorkingConnection()
        with patch.object(music_service, "_connection", return_value=connection):
            music_service._load_db_tracks()
            music_service._load_db_track_by_id("1")

        self.assertEqual(connection.closed, 2)

    def test_a_pool_that_cannot_hand_out_a_connection_does_not_crash_the_caller(self):
        # `conn` is None here, so the `finally` must not try to close it. Getting
        # this wrong swaps a degraded search for a 500.
        with patch.object(music_service, "_connection", side_effect=RuntimeError("QueuePool limit reached")):
            self.assertEqual(music_service._load_db_tracks(), [])
            self.assertEqual(music_service._load_db_track_by_id("1"), {})


if __name__ == "__main__":
    unittest.main()
