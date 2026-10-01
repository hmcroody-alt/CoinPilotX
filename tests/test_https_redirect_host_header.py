"""The http->https upgrade must not echo the client-supplied Host header.

`enforce_https()` used to build its 301 target from `request.url`, which is
assembled from the Host header. A request carrying `Host: evil.example.com` and
`X-Forwarded-Proto: http` therefore got a 301 to `https://evil.example.com/...`
on any path, with no marker or special input needed.

Railway's edge currently rejects an unrecognised Host with its own 404 before
gunicorn sees it, so the deployed app was not reachable through this. The tests
below pin the behaviour at the application layer anyway, because the edge is the
only thing that was stopping it and nothing in this repo enforces that.

The redirect target must come from a fixed origin. The two hosts this app is
actually served on stay on their own origin so the upgrade is not also a
cross-host hop; anything else lands on the canonical origin.

Run: python3 -m pytest tests/test_https_redirect_host_header.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="https_redirect_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402


FORGED_HOSTS = [
    "evil.example.com",
    "attacker.test",
    "pulsesoc.com.evil.example.com",
    "evilpulsesoc.com",
    "user@evil.example.com",
]


class EnforceHttpsHostHeaderTests(unittest.TestCase):
    def setUp(self):
        self.client = bot.app.test_client()

    def _upgrade(self, path, host):
        return self.client.get(
            path,
            headers={"Host": host, "X-Forwarded-Proto": "http"},
        )

    def test_forged_host_never_reaches_the_redirect_target(self):
        for host in FORGED_HOSTS:
            for path in ("/", "/privacy", "/pulse"):
                with self.subTest(host=host, path=path):
                    response = self._upgrade(path, host)
                    self.assertEqual(response.status_code, 301)
                    location = response.headers["Location"]
                    self.assertEqual(location, f"https://pulsesoc.com{path}")
                    self.assertNotIn(host, location)

    def test_forged_host_with_query_string_keeps_path_and_query(self):
        response = self._upgrade("/pulse?tab=live&page=2", "evil.example.com")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"],
            "https://pulsesoc.com/pulse?tab=live&page=2",
        )

    def test_served_hosts_upgrade_on_their_own_origin(self):
        for host in ("pulsesoc.com", "www.pulsesoc.com"):
            with self.subTest(host=host):
                response = self._upgrade("/privacy", host)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(
                    response.headers["Location"], f"https://{host}/privacy"
                )

    def test_served_host_match_is_case_insensitive(self):
        response = self._upgrade("/privacy", "PulseSoc.COM")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"], "https://pulsesoc.com/privacy"
        )

    def test_port_is_stripped_before_the_host_is_matched(self):
        response = self._upgrade("/privacy", "pulsesoc.com:8080")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"], "https://pulsesoc.com/privacy"
        )

    def test_local_hosts_are_exempt(self):
        for host in ("localhost:5000", "127.0.0.1:8080", "0.0.0.0:8080"):
            with self.subTest(host=host):
                response = self._upgrade("/privacy", host)
                self.assertNotEqual(response.status_code, 301)

    def test_https_requests_are_not_redirected(self):
        response = self.client.get(
            "/privacy",
            headers={"Host": "evil.example.com", "X-Forwarded-Proto": "https"},
        )
        self.assertNotEqual(response.status_code, 301)


class WwwApexRedirectTests(unittest.TestCase):
    """The sibling hook already used a fixed origin. Pin it so it stays that way."""

    def setUp(self):
        self.client = bot.app.test_client()

    def test_legacy_www_host_redirects_to_the_canonical_origin(self):
        response = self.client.get(
            "/privacy",
            headers={"Host": "www.coinpilotx.app", "X-Forwarded-Proto": "https"},
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"], "https://pulsesoc.com/privacy"
        )


if __name__ == "__main__":
    unittest.main()
