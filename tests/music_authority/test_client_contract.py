"""The phone's copy of the server's vocabulary, pinned to the server's.

The native client keeps a `switch` over `error_code` so that a refusal reads as
the thing it is -- a legal hold, a stale state, a missing step-up -- rather than
as "something went wrong". That switch is the only place in the app where those
distinctions exist, and it is also the easiest thing in the codebase to get
silently wrong: a `case` for a code the server never emits is dead, a code the
server does emit and the client does not handle falls to the default branch, and
neither shows up in a typecheck, a jest run, or any Python test. Both languages
stay green while the owner reads a generic error.

This is not hypothetical. The first draft of `musicAuthority.ts` shipped
`music_reason_required` and `music_note_required`; the server emits
`music_reason_code_invalid` and `music_reason_note_required`. Three of the ten
branches were dead on arrival and nothing anywhere said so.

So the two sides are compared here, in the one place that can read both. The
assertion runs in both directions on purpose:

* every code the client handles must be emitted by the server -- catches the
  invented case;
* every code the server emits must be handled by the client -- catches the new
  refusal that ships without a message.

The permission and reason-code lists get the same treatment, for the same
reason: the client declares them so its types are useful, and a client list that
has drifted from the server's produces a takedown reason the owner can select
and the server rejects.
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLIENT_PATH = os.path.join(REPO_ROOT, "mobile-native", "src", "api", "musicAuthority.ts")
AUTHORITY_PATH = os.path.join(REPO_ROOT, "services", "music_authority.py")
BOT_PATH = os.path.join(REPO_ROOT, "bot.py")

from services import music_authority  # noqa: E402


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _client_source():
    return _read(CLIENT_PATH)


def _client_handled_codes():
    """The `error_code` values the client's switch has a branch for."""
    body = _client_source()
    start = body.index("export function describeMusicAuthorityError")
    return set(re.findall(r'case "(music_[a-z_]+)"', body[start:]))


def _server_emitted_codes():
    """Every `music_*` error code the server can put on the wire.

    Two shapes, because the server raises refusals both ways: `AuthorityError`
    inside the policy module, and `api_error(..., error_code=...)` at the Flask
    edge. Reading only one of them would leave half the contract unchecked --
    and it is the `api_error` half that carries the step-up and confirmation
    refusals, which are the ones a person is most likely to hit.
    """
    codes = set()
    for path in (AUTHORITY_PATH, BOT_PATH):
        source = _read(path)
        codes |= set(re.findall(r'AuthorityError\(\s*\n?\s*"(music_[a-z_]+)"', source))
        codes |= set(re.findall(r'error_code="(music_[a-z_]+)"', source))
    return codes


# Codes that exist but are deliberately not given a client message, each with
# the reason. An allowlist rather than a loosened assertion: an entry here is a
# decision someone can argue with later, whereas a weaker test is invisible.
CLIENT_UNHANDLED_CODES = {
    # A 500. It means the *server* asked for a permission that does not exist,
    # which is a bug in the server rather than anything the owner can act on;
    # the generic fallback is the honest answer.
    "music_permission_unknown",
}


class ErrorCodeContractTests(unittest.TestCase):
    def test_the_client_handles_no_code_the_server_cannot_send(self):
        """A `case` for an invented code is dead and looks like coverage."""
        handled = _client_handled_codes()
        emitted = _server_emitted_codes()
        self.assertTrue(handled, "the client switch was not found -- the regex is stale")
        self.assertEqual(
            handled - emitted,
            set(),
            "the client has branches for codes the server never sends",
        )

    def test_the_server_sends_no_code_the_client_drops_to_generic(self):
        handled = _client_handled_codes()
        emitted = _server_emitted_codes()
        self.assertTrue(emitted, "no server error codes were found -- the regex is stale")
        self.assertEqual(
            emitted - handled - CLIENT_UNHANDLED_CODES,
            set(),
            "the server can refuse with a code the owner will see as a generic error",
        )

    def test_the_regexes_actually_find_something(self):
        """Both sides of the comparison above are empty if a regex goes stale.

        An empty-vs-empty set difference is `set()`, so the two tests above
        would pass while reading nothing at all. This is the control.
        """
        self.assertGreaterEqual(len(_client_handled_codes()), 8)
        self.assertGreaterEqual(len(_server_emitted_codes()), 8)
        self.assertIn("music_legal_hold", _server_emitted_codes())
        self.assertIn("music_step_up_required", _server_emitted_codes())


class VocabularyContractTests(unittest.TestCase):
    """The client's declared permission and reason lists, against the server's.

    The client fetches both at runtime from `/capabilities`, so a drift here is
    not immediately fatal -- but the declared constants are what its *types* are
    built from, and a `MusicReasonCode` union missing a code the server accepts
    makes that code unreachable from any typed call site.
    """

    def test_the_permission_list_matches(self):
        declared = set(
            re.findall(r'"(music\.[a-z_]+)"', _client_source())
        )
        self.assertEqual(declared, set(music_authority.MUSIC_PERMISSIONS))

    def test_the_reason_code_union_matches(self):
        body = _client_source()
        start = body.index("export type MusicReasonCode")
        end = body.index(";", start)
        declared = set(re.findall(r'"([A-Z_]+)"', body[start:end]))
        self.assertEqual(declared, set(music_authority.REASON_CODES))

    def test_the_lifecycle_state_union_matches(self):
        body = _client_source()
        start = body.index("export type MusicLifecycleState")
        end = body.index(";", start)
        declared = set(re.findall(r'"([A-Z_]+)"', body[start:end]))
        self.assertEqual(declared, set(music_authority.LIFECYCLE_STATES))


class WireShapeTests(unittest.TestCase):
    """The snake_case keys the client reads must be keys the server writes.

    A renamed response field is the other silent failure in this seam: the
    client's `num()`/`text()` helpers turn a missing key into `0` or `""`
    rather than throwing, so a reference count that stopped arriving would
    render as "0 posts affected" -- an answer that reads as reassuring and is
    simply absent.
    """

    def test_the_capability_keys_the_client_reads_are_the_ones_the_server_sends(self):
        client = _client_source()
        for key in ("music_authority", "permissions", "states", "reason_codes", "step_up_ttl_seconds"):
            self.assertIn(key, client, key)
            self.assertIn('"%s"' % key, _read(BOT_PATH), key)

    def test_the_impact_keys_the_client_reads_are_the_ones_the_server_sends(self):
        client = _client_source()
        server = _read(BOT_PATH)
        for key in (
            "uploader_user_id", "legal_hold", "purge_scheduled_at", "purged_at",
            "open_reports", "play_count", "usage_count", "cached_copies_remain_until_purge",
        ):
            self.assertIn(key, client, key)
            self.assertIn('"%s"' % key, server, key)

    def test_the_mutation_keys_the_client_reads_are_the_ones_the_server_sends(self):
        client = _client_source()
        server = _read(BOT_PATH)
        for key in (
            "request_id", "previous_state", "affected_reference_count",
            "deleted_object_count", "confirm_track_id", "expected_state", "track_ids",
        ):
            self.assertIn(key, client, key)
            self.assertIn('"%s"' % key, server, key)

    def test_the_audit_keys_the_client_reads_are_the_columns_that_exist(self):
        """The audit endpoint returns raw rows, so the keys are column names."""
        client = _client_source()
        server = _read(BOT_PATH)
        for column in (
            "action_id", "previous_state", "new_state", "actor_user_id", "actor_role",
            "reason_code", "reason_note", "affected_reference_count", "request_id",
            "created_at", "restored_at", "related_action_id",
        ):
            self.assertIn(column, client, column)
            self.assertIn(column, server, column)


if __name__ == "__main__":
    unittest.main()
