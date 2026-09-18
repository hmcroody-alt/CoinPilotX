"""The http->https upgrade must not echo the client-supplied Host header.

`enforce_https()` used to build its 301 target from `request.url`, which is
assembled from the Host header. A request carrying `Host: evil.example.com` and
`X-Forwarded-Proto: http` therefore got a 301 to `https://evil.example.com/...`
on any path, with no marker or special input needed.

Railway's edge currently rejects an unrecognised Host with its own 404 before
gunicorn sees it, so the deployed app was not reachable through this. The tests
below pin the behaviour at the application layer anyway, because the edge is the
only thing that was stopping it and nothing in this repo enforces that.

The redirect target must come from a fixed origin. The apex stays on its own
origin so the upgrade is not also a cross-host hop; anything else lands on the
canonical origin. `www` is the exception, and deliberately so: the sibling
`redirect_www_to_apex_domain` hook is registered first, so it collapses the
scheme upgrade and the host consolidation into a single hop.

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

    def test_the_apex_upgrades_on_its_own_origin(self):
        response = self._upgrade("/privacy", "pulsesoc.com")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"], "https://pulsesoc.com/privacy"
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
    """One host, one set of ranking signals.

    The hook used a fixed origin from the start, but it matched only
    `www.coinpilotx.app` -- a host that stopped being served -- so after the
    migration `https://www.pulsesoc.com` answered 200 exactly like the apex.
    Two hosts returning identical 200s is a duplicate-host condition, and the
    `rel=canonical` that was mitigating it is a hint Google may decline.
    """

    def setUp(self):
        self.client = bot.app.test_client()

    def _get(self, path, host, proto="https"):
        return self.client.get(
            path, headers={"Host": host, "X-Forwarded-Proto": proto}
        )

    def test_legacy_www_host_redirects_to_the_canonical_origin(self):
        response = self._get("/privacy", "www.coinpilotx.app")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"], "https://pulsesoc.com/privacy"
        )

    def test_www_pulsesoc_redirects_to_the_apex(self):
        for path in ("/", "/privacy", "/pulse"):
            with self.subTest(path=path):
                response = self._get(path, "www.pulsesoc.com")
                self.assertEqual(response.status_code, 301)
                self.assertEqual(
                    response.headers["Location"], f"https://pulsesoc.com{path}"
                )

    def test_the_query_string_survives_the_hop(self):
        response = self._get("/pulse?tab=live&page=2", "www.pulsesoc.com")
        self.assertEqual(
            response.headers["Location"],
            "https://pulsesoc.com/pulse?tab=live&page=2",
        )

    def test_the_host_match_ignores_case_and_port(self):
        for host in ("WWW.PulseSoc.com", "www.pulsesoc.com:8080"):
            with self.subTest(host=host):
                response = self._get("/privacy", host)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(
                    response.headers["Location"], "https://pulsesoc.com/privacy"
                )

    def test_an_http_www_request_reaches_the_https_apex_in_one_hop(self):
        """Not two.

        `enforce_https` keeps the apex on its own origin, so if it ran first a
        www visitor would take `http://www` -> `https://www` -> `https://apex`.
        Registration order is what makes that one hop, and registration order is
        not visible from either hook alone.
        """

        response = self._get("/privacy", "www.pulsesoc.com", proto="http")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"], "https://pulsesoc.com/privacy"
        )

    def test_the_apex_is_not_redirected_to_itself(self):
        response = self._get("/privacy", "pulsesoc.com")
        self.assertNotEqual(response.status_code, 301)

    def test_the_app_site_association_file_is_still_served_on_www(self):
        """Apple does not follow redirects when it fetches this file.

        App version 1.0.0 shipped `applinks:www.pulsesoc.com` under the same
        bundle id as the current build, so redirecting this prefix would
        silently break universal links for anyone still on it. The file is not
        a ranking surface, so keeping it on both hosts costs nothing.
        """

        response = self._get(
            "/.well-known/apple-app-site-association", "www.pulsesoc.com"
        )
        self.assertNotEqual(response.status_code, 301)


if __name__ == "__main__":
    unittest.main()
