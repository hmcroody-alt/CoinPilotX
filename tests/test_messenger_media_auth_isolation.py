"""Media delivery is not authentication.

The incident this suite exists to prevent: sending or opening a Messenger photo
signed the user out of PulseSoc. Nothing in the media code did that. The native
image loader cannot send an Authorization header, so a GET of the protected
download route fell through `account_user_id()` into
`restore_account_from_persistent_cookie()`, which rotates the mobile refresh
token. Rendering several thumbnails at once then presented the same token
concurrently and read as `refresh_token_reuse`; the image loader's distinct
User-Agent read as `device_mismatch`. Both revoke the whole session family, so
the next ordinary API call 401'd and the app signed the user out.

Production proof, deployment e978db26 (commit b459f992):

    GET /api/messages/media/67/download  status=401 user_id=0
      slowest_query = UPDATE mobile_security_sessions SET status='revoked'
                      ... revoked_reason='refresh_token_reuse'
    GET /api/messages/media/67/download  status=401 user_id=0
      slowest_query = UPDATE mobile_security_sessions SET status='revoked'
                      ... revoked_reason='device_mismatch'

These tests hold the boundary, not the symptom. The reuse and device-mismatch
defences are correct and are deliberately left alone -- they are what exposed
this. What is fixed is that media delivery no longer enters that machinery.
"""

import ast
import os
import re
import time
import unittest
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "")

from services import messenger_media_foundation as foundation  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_SOURCE = open(os.path.join(REPO_ROOT, "bot.py"), encoding="utf-8").read()
BOT_TREE = ast.parse(BOT_SOURCE)

FOUNDATION_PATH = Path(REPO_ROOT) / "services" / "messenger_media_foundation.py"
FOUNDATION_SOURCE = FOUNDATION_PATH.read_text(encoding="utf-8")
FOUNDATION_TREE = ast.parse(FOUNDATION_SOURCE)
MOBILE_SRC = Path(REPO_ROOT) / "mobile-native" / "src"

SECRET = "test-secret-not-a-real-key"

#: Every helper that mutates or consumes mobile login session state. A media
#: byte request must reach none of them.
SESSION_MUTATING_CALLS = {
    "restore_account_from_persistent_cookie",
    "rotate_mobile_refresh_token",
    "issue_mobile_session_tokens",
    "revoke_mobile_session_family",
}


def _function_def(name, tree=None):
    for node in ast.walk(tree if tree is not None else BOT_TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"source no longer defines {name}()")


def _media_error_table():
    for node in ast.walk(BOT_TREE):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "MESSENGER_MEDIA_ERRORS" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("bot.py no longer defines MESSENGER_MEDIA_ERRORS")


def _called_names(node):
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


class MediaAccessTokenTest(unittest.TestCase):
    """The credential is bounded to one attachment, one viewer, one window."""

    def test_round_trip_authorizes_the_minted_viewer(self):
        token, expires_at = foundation.mint_access_token(SECRET, 67, 4)
        self.assertGreater(expires_at, int(time.time()))
        self.assertEqual(foundation.access_token_user_id(SECRET, 67, token), 4)

    def test_token_does_not_authorize_a_different_attachment(self):
        token, _ = foundation.mint_access_token(SECRET, 67, 4)
        self.assertEqual(foundation.access_token_user_id(SECRET, 68, token), 0)

    def test_expired_token_is_refused(self):
        token, _ = foundation.mint_access_token(SECRET, 67, 4, ttl_seconds=60)
        aged = token.split(".")
        aged[2] = str(int(time.time()) - 5)
        # Re-sign so the test exercises expiry, not signature failure.
        body = ".".join(aged[:3])
        forged = body + "." + foundation._access_token_signature(SECRET, body)
        self.assertEqual(foundation.access_token_user_id(SECRET, 67, forged), 0)

    def test_tampered_signature_is_refused(self):
        token, _ = foundation.mint_access_token(SECRET, 67, 4)
        parts = token.split(".")
        parts[1] = "999"
        self.assertEqual(foundation.access_token_user_id(SECRET, 67, ".".join(parts)), 0)

    def test_token_from_a_different_secret_is_refused(self):
        token, _ = foundation.mint_access_token("other-secret", 67, 4)
        self.assertEqual(foundation.access_token_user_id(SECRET, 67, token), 0)

    def test_malformed_tokens_are_refused_without_raising(self):
        for candidate in ["", "   ", "abc", "1.2.3", "1.2.3.4.5", "x.y.z.w", None]:
            self.assertEqual(foundation.access_token_user_id(SECRET, 67, candidate), 0)

    def test_token_carries_no_refresh_material(self):
        """A media credential must never be exchangeable for a session."""
        token, _ = foundation.mint_access_token(SECRET, 67, 4)
        self.assertNotIn(SECRET, token)
        self.assertRegex(token, r"^\d+\.\d+\.\d+\.[0-9a-f]{64}$")


class PersistentCookieRestorationBoundaryTest(unittest.TestCase):
    """The refusal lives at the mutation point, not at one call site.

    `pulse_security_core_guard` resolves `account_user_id()` on every request
    before any route runs, so guarding only the media route would have left the
    rotation firing. This is the test that would have failed against the
    shipped build.
    """

    def test_restore_refuses_media_byte_paths(self):
        source = ast.get_source_segment(BOT_SOURCE, _function_def("restore_account_from_persistent_cookie"))
        self.assertIn("is_media_byte_delivery_path", source)
        guard_index = source.index("is_media_byte_delivery_path")
        rotate_index = source.index("rotate_mobile_refresh_token")
        self.assertLess(guard_index, rotate_index, "the media guard must precede token rotation")

    def test_media_byte_path_matcher_covers_the_download_route(self):
        pattern = None
        for node in ast.walk(BOT_TREE):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "MEDIA_BYTE_PATH_RE" for t in node.targets
            ):
                pattern = node.value.args[0].value
        self.assertIsNotNone(pattern, "bot.py no longer defines MEDIA_BYTE_PATH_RE")
        matcher = re.compile(pattern)
        for path in ["/api/messages/media/67/download", "/api/messages/media/1/download/"]:
            self.assertTrue(matcher.match(path), path)
        for path in [
            "/api/messages/media/67",
            "/api/messages/media/67/access",
            "/api/pulse/messages/conversations",
            "/api/messages/media/abc/download",
        ]:
            self.assertFalse(matcher.match(path), path)


class MediaRouteAuthIsolationTest(unittest.TestCase):
    """Byte-serving routes resolve a viewer without entering session machinery."""

    def test_download_route_does_not_use_the_session_refreshing_resolver(self):
        source = ast.get_source_segment(BOT_SOURCE, _function_def("api_messages_media_download"))
        self.assertIn("_messenger_media_viewer", source)
        self.assertNotIn("_messenger_media_user", source)
        self.assertNotIn("api_account_user", source)

    def test_attachment_metadata_route_is_isolated_too(self):
        source = ast.get_source_segment(BOT_SOURCE, _function_def("api_messages_media_get"))
        self.assertIn("_messenger_media_viewer", source)
        self.assertNotIn("_messenger_media_user", source)

    def test_viewer_resolver_never_calls_session_mutating_helpers(self):
        called = _called_names(_function_def("_messenger_media_viewer"))
        self.assertEqual(called & SESSION_MUTATING_CALLS, set())
        self.assertNotIn("account_user_id", called, "account_user_id() falls through to cookie restoration")
        self.assertNotIn("api_account_user", called)
        self.assertNotIn("require_account", called)

    def test_viewer_resolver_accepts_media_token_session_and_bearer(self):
        called = _called_names(_function_def("_messenger_media_viewer"))
        self.assertIn("messenger_media_token_state", called)
        self.assertIn("account_user_id_from_mobile_access_token", called)
        self.assertIn("load_account_by_id", called)

    def test_denial_is_a_media_error_not_a_login_error(self):
        """`login_required` is what the client turns into a sign-out prompt."""
        source = ast.get_source_segment(BOT_SOURCE, _function_def("_messenger_media_viewer"))
        self.assertNotIn("login_required", source)
        self.assertIn("_messenger_media_denied", source)
        denied = ast.get_source_segment(BOT_SOURCE, _function_def("_messenger_media_denied"))
        self.assertNotIn("login_required", denied)
        for _code, message, _status in _media_error_table().values():
            self.assertEqual(message, "Image unavailable")


class MediaFailureClassificationTest(unittest.TestCase):
    """Stage 8. A media failure and an authentication failure are different
    kinds of event, and only the latter may reach session recovery.

    The client turns a 401 on an API path into a refresh attempt, and a failed
    refresh into a sign-out. So the status code IS the classification: if a
    media denial answers 401, an expired thumbnail credential can still end a
    login. None of the media categories may be 401.
    """

    def test_every_media_category_is_distinct_and_non_401(self):
        table = _media_error_table()
        self.assertEqual(set(table), {"expired", "denied", "not_found"})
        codes = [entry[0] for entry in table.values()]
        self.assertEqual(sorted(codes), ["media_access_denied", "media_access_expired", "media_not_found"])
        self.assertEqual(len(set(codes)), 3, "categories must be distinguishable by the client")
        for kind, (code, _message, status) in table.items():
            self.assertNotEqual(status, 401, f"{kind} must not be answerable as an auth failure")
            self.assertIn(status, (403, 404))

    def test_expired_credential_is_reported_as_expiry_not_as_denial(self):
        source = ast.get_source_segment(BOT_SOURCE, _function_def("_messenger_media_denied"))
        self.assertIn('"expired"', source)
        self.assertIn("token_state", source)

    def test_token_state_separates_expiry_from_forgery(self):
        source = ast.get_source_segment(
            FOUNDATION_SOURCE, _function_def("access_token_state", tree=FOUNDATION_TREE)
        )
        for state in ('"absent"', '"invalid"', '"expired"', '"valid"'):
            self.assertIn(state, source)

    def test_client_never_refreshes_the_session_for_a_media_path(self):
        """`pulseApi` escalates a 401 into refresh-then-possibly-sign-out.

        Media paths are excluded from that path outright, so even a
        misclassified server response cannot cost the user their login.
        """
        source = (MOBILE_SRC / "api" / "pulseApi.ts").read_text(encoding="utf-8")
        self.assertIn("/api/messages/media/", source)
        self.assertIn("isMessengerMediaPath", source)
        should_refresh = source.split("function shouldRefresh", 1)[1].split("}", 1)[0]
        self.assertIn("isMessengerMediaPath", should_refresh)


class InstalledBuildCompatibilityTest(unittest.TestCase):
    """Handsets already running build 21/22 send tokenless download URLs.

    They must keep rendering media, and they must do so without any write to
    mobile_security_sessions. Refusing them would break shipped clients;
    rotating for them is the defect itself.
    """

    def test_cookie_path_is_read_only(self):
        source = ast.get_source_segment(BOT_SOURCE, _function_def("messenger_media_cookie_user_id"))
        upper = source.upper()
        for verb in ["UPDATE ", "INSERT ", "DELETE ", "COMMIT"]:
            self.assertNotIn(verb, upper, f"media cookie lookup must not {verb.strip().lower()}")
        self.assertIn("SELECT user_id", source)

    def test_cookie_path_never_rotates_or_revokes(self):
        called = _called_names(_function_def("messenger_media_cookie_user_id"))
        self.assertEqual(called & SESSION_MUTATING_CALLS, set())

    def test_cookie_path_rejects_revoked_and_expired_sessions(self):
        source = ast.get_source_segment(BOT_SOURCE, _function_def("messenger_media_cookie_user_id"))
        self.assertIn("COALESCE(revoked_at,'')=''", source)
        self.assertIn("refresh_expires_at", source)
        self.assertIn("status IN ('active','rotated')", source)

    def test_cookie_path_is_the_last_resort(self):
        """A media token or a live access token is always preferred."""
        source = ast.get_source_segment(BOT_SOURCE, _function_def("_messenger_media_viewer"))
        self.assertLess(source.index("messenger_media_token_state"), source.index("messenger_media_cookie_user_id"))
        self.assertLess(
            source.index("account_user_id_from_mobile_access_token"), source.index("messenger_media_cookie_user_id")
        )


class MediaAuthorizationTest(unittest.TestCase):
    """A signed URL is transport authorization, never an authorization bypass."""

    def test_download_target_still_checks_conversation_membership(self):
        source = ast.get_source_segment(
            open(os.path.join(REPO_ROOT, "services", "messenger_media_foundation.py"), encoding="utf-8").read(),
            next(
                node
                for node in ast.walk(
                    ast.parse(
                        open(
                            os.path.join(REPO_ROOT, "services", "messenger_media_foundation.py"), encoding="utf-8"
                        ).read()
                    )
                )
                if isinstance(node, ast.FunctionDef) and node.name == "attachment_download_target"
            ),
        )
        self.assertIn("_require_attachment_access", source)

    def test_access_route_authorizes_before_minting(self):
        source = ast.get_source_segment(BOT_SOURCE, _function_def("api_messages_media_access"))
        authorize_index = source.index("get_attachment")
        mint_index = source.index("mint_messenger_media_token")
        self.assertLess(authorize_index, mint_index, "authorization must precede minting")
        self.assertIn("status != 200", source)

    def test_access_route_requires_a_real_authenticated_session(self):
        source = ast.get_source_segment(BOT_SOURCE, _function_def("api_messages_media_access"))
        self.assertIn("_messenger_media_user", source)


class RefreshSecurityUnweakenedTest(unittest.TestCase):
    """We removed media from the machinery; we did not soften the machinery."""

    def test_reuse_detection_still_revokes_the_session_family(self):
        source = ast.get_source_segment(BOT_SOURCE, _function_def("rotate_mobile_refresh_token"))
        self.assertIn("refresh_token_reuse", source)
        self.assertIn("session_family_id", source)
        self.assertIn("device_mismatch", source)

    def test_reuse_grace_window_was_not_widened(self):
        match = re.search(
            r'PERSISTENT_REFRESH_REUSE_GRACE_SECONDS = max\(30, int\(os\.getenv\("PULSESOC_REFRESH_REUSE_GRACE_SECONDS", "(\d+)"\)\)\)',
            BOT_SOURCE,
        )
        self.assertIsNotNone(match, "the reuse grace constant changed shape")
        self.assertEqual(match.group(1), "180")

    def test_device_fingerprint_check_still_present(self):
        source = ast.get_source_segment(BOT_SOURCE, _function_def("mobile_refresh_reuse_grace_allowed"))
        self.assertIn("device_hash", source)
        self.assertIn("ip_hash", source)


class MediaPrivacyTest(unittest.TestCase):
    """The fix must not have made private Messenger media public."""

    def test_download_response_stays_private_and_uncacheable(self):
        source = ast.get_source_segment(BOT_SOURCE, _function_def("api_messages_media_download"))
        self.assertIn("private, no-store", source)
        self.assertIn("nosniff", source)
        self.assertIn("no-referrer", source)

    def test_no_route_serves_attachments_without_a_viewer(self):
        for name in ["api_messages_media_download", "api_messages_media_get"]:
            source = ast.get_source_segment(BOT_SOURCE, _function_def(name))
            self.assertIn("auth_error", source)
            self.assertIn("return auth_error", source)


class MediaObservabilityTest(unittest.TestCase):
    """Stage 9. Four events, enough to answer "is media still doing this?" in
    production, and not one byte more than that.

    The whole acceptance criterion for this repair is a count that must be
    zero -- session revocations caused by a media path. That count is only
    measurable if the media path says when it grants, when it denies, when a
    credential simply aged out, and when an installed build fell back to the
    legacy cookie route. Without the fourth, there is no way to know when the
    compatibility path is safe to remove (Stage 22).
    """

    REQUIRED_EVENTS = [
        "MESSENGER_MEDIA_ACCESS_SUCCESS",
        "MESSENGER_MEDIA_ACCESS_DENIED",
        "MESSENGER_MEDIA_SIGNATURE_EXPIRED",
        "MESSENGER_MEDIA_LEGACY_COOKIE_ACCESS",
    ]

    def test_all_four_events_exist(self):
        combined = BOT_SOURCE + FOUNDATION_SOURCE
        for event in self.REQUIRED_EVENTS:
            self.assertIn(event, combined, f"{event} is required before deploy")

    def test_legacy_cookie_use_is_reported_where_it_happens(self):
        source = ast.get_source_segment(BOT_SOURCE, _function_def("_messenger_media_viewer"))
        self.assertIn("MESSENGER_MEDIA_LEGACY_COOKIE_ACCESS", source)
        legacy = source.split("MESSENGER_MEDIA_LEGACY_COOKIE_ACCESS", 1)[0]
        self.assertIn("persistent_cookie_readonly", legacy)

    def test_events_never_carry_the_credential_or_the_content(self):
        """Correlate by attachment, path and status -- never by secret."""
        forbidden = re.compile(r"(token=%s|signature=|access_url=%s|body=%s|caption=%s)")
        for line in (BOT_SOURCE + FOUNDATION_SOURCE).splitlines():
            if "MESSENGER_MEDIA_" not in line:
                continue
            self.assertIsNone(forbidden.search(line), f"media log line leaks a credential: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
