"""The runtime half of §12, and the reasons a zero here is worth anything.

`tests/test_undx_config_drift.py` proves the source contains no unrouted chat
call. This proves that if one ran anyway, something would say so — which is a
different claim, and the one §43 asks for when it says the guard must exist at
runtime and not only in tests.

The hard part of testing a counter whose correct value is zero is that almost
every mistake produces a zero. A guard that was never installed reports zero. A
guard whose URL classifier never matches reports zero. A guard wrapping a
function nobody calls reports zero. So every test below that asserts zero is
paired with one that makes the same call and asserts one, with a single thing
changed — and the two that matter most,
`test_a_routed_call_is_not_counted` and its `_because_of_the_frame_walk`
companion, run the real `undx_router` against a mocked transport rather than a
stub, because the frame walk is the only reason that zero happens and a stub
router would have a different stack.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from services import undx_call_guard as guard  # noqa: E402

CHAT_URL = "https://api.openai.com/v1/chat/completions"
EMBED_URL = "https://api.perplexity.ai/v1/embeddings"
IMAGE_URL = "https://api.openai.com/v1/images/generations"


class _Answer:
    """Enough of a `requests.Response` for the router to read a reply."""

    status_code = 200
    headers: dict[str, str] = {}
    text = '{"choices": [{"message": {"content": "hi"}}]}'

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "hi"}}], "usage": {}}


class GuardTestCase(unittest.TestCase):

    def setUp(self) -> None:
        guard.install()
        guard.reset()
        self.addCleanup(guard.reset)

    def chat(self) -> int:
        return guard.counters()[guard.UNROUTED_CHAT]

    def capability(self) -> int:
        return guard.counters()[guard.UNMETERED_CAPABILITY]

    def send(self, *args, **kwargs):
        with mock.patch.object(requests.sessions.Session, "send",
                               return_value=_Answer()):
            return requests.post(*args, timeout=1, **kwargs)


class ClassificationTest(GuardTestCase):

    def test_a_chat_call_outside_the_router_is_counted(self):
        self.send(CHAT_URL, json={})
        self.assertEqual(self.chat(), 1)

    def test_a_call_to_anything_else_is_not(self):
        """The guard sits in front of every outbound request in the process —
        Stripe, Telegram, LiveKit, Mux, Brevo, R2, CoinGecko. If it charged any
        of those to this counter the number would be noise within a second of
        boot."""
        self.send("https://api.stripe.com/v1/charges", json={})
        self.send("https://api.telegram.org/bot123/sendMessage", json={})
        self.send("https://pulsesoc.com/api/pulse/feed", json={})
        self.assertEqual(guard.counters(),
                         {guard.UNROUTED_CHAT: 0, guard.UNMETERED_CAPABILITY: 0})

    def test_embeddings_and_images_are_charged_to_the_other_counter(self):
        """Both bypass the router because the router has no adapter for them —
        there is nothing to route them *to* yet. Folded into the headline counter
        they would make it permanently non-zero, and a number that is never zero
        cannot be watched for becoming non-zero, which is the only thing anyone
        wants from this one."""
        self.send(EMBED_URL, json={})
        self.send(IMAGE_URL, json={})
        self.assertEqual(self.chat(), 0)
        self.assertEqual(self.capability(), 2)

    def test_one_call_is_counted_once(self):
        """`requests.post` reaches the network through `Session.request`, and the
        guard wraps both, so the naive version counted every call twice. That
        would make a metric whose target is zero depend on which of two
        equivalent spellings the caller happened to use."""
        self.send(CHAT_URL, json={})
        self.assertEqual(self.chat(), 1)

    def test_a_caller_holding_its_own_session_is_still_seen(self):
        """The reason `Session.request` is wrapped at all: a caller that keeps a
        session never touches the module-level `requests.post`, so wrapping only
        the convenience functions would miss it — and a connection-pooling
        caller is exactly the shape a high-volume provider call takes."""
        session = requests.Session()
        with mock.patch.object(requests.sessions.Session, "send",
                               return_value=_Answer()):
            session.post(CHAT_URL, json={}, timeout=1)
        self.assertEqual(self.chat(), 1)

    def test_a_urllib_request_object_is_read_for_its_url(self):
        """`services/pulse_ai/automated_image_pipeline.py` builds a
        `urllib.request.Request` and hands the object to `urlopen`, so the URL is
        never an argument the guard can read positionally. Checking the receiver
        rather than the verb is the general version of this."""
        import urllib.request

        request = urllib.request.Request(IMAGE_URL, data=b"{}")
        with self.assertRaises(Exception):
            urllib.request.urlopen(request, timeout=0.001)
        self.assertEqual(self.capability(), 1)


class RoutedCallTest(GuardTestCase):
    """The only two tests here that can distinguish a working guard from an
    absent one."""

    def route(self):
        os.environ.setdefault("OPENAI_API_KEY", "sk-not-a-real-key-frame-walk-only")
        import undx_router

        with mock.patch.object(requests.sessions.Session, "send",
                               return_value=_Answer()):
            return undx_router.route_structured_request(
                None, "system", "hello", timeout=5, privacy_class="PUBLIC",
                call_domain="GENERAL", providers=["openai"])

    def test_a_routed_call_is_not_counted(self):
        answer = self.route()
        self.assertTrue(answer.get("ok"), answer)
        self.assertEqual(self.chat(), 0)

    def test_and_that_zero_is_because_of_the_frame_walk(self):
        """The negative control for the test above, and the reason it is worth
        writing. A guard that never installed, or whose classifier never matched
        `api.openai.com`, or that wrapped a function the router does not use,
        reports zero there just as convincingly. Neutering only `_routed` and
        nothing else must turn that zero into a one — otherwise the guard was
        not the thing producing it."""
        with mock.patch.object(guard, "_routed", lambda adapters: False):
            answer = self.route()
        self.assertTrue(answer.get("ok"), answer)
        self.assertEqual(self.chat(), 1)

    def test_an_adapter_that_is_not_imported_cannot_have_been_used(self):
        """`_adapter_files` reads `sys.modules` rather than importing the router,
        because importing it from a module the router's own callers import is a
        cycle. The consequence is that "not imported" reads as "not routed
        through", which is the conservative direction and also simply true."""
        with mock.patch.dict(sys.modules, {"undx_router": None}):
            self.assertEqual(guard._adapter_files(), frozenset())
        self.send(CHAT_URL, json={})
        self.assertEqual(self.chat(), 1)

    def test_the_adapter_is_matched_by_path_not_by_filename(self):
        """The same mistake `_ADAPTER_ALLOWLIST` had. A guard that accepted any
        frame from a file *named* `undx_router.py` would accept a frame from one
        a contributor added anywhere on the path."""
        import undx_router

        self.assertIn(os.path.realpath(undx_router.__file__),
                      guard._adapter_files())
        impostor = mock.Mock()
        impostor.__file__ = "/tmp/vendor/undx_router.py"
        with mock.patch.dict(sys.modules, {"undx_router": impostor}):
            adapters = guard._adapter_files()
            self.assertNotIn(os.path.realpath(undx_router.__file__), adapters)
            # `realpath`, not the literal: on macOS `/tmp` is a symlink to
            # `/private/tmp`, and resolving it is the whole point — a frame
            # reached through a symlinked path has to compare equal to the file
            # it actually is, or the sandbox trap in `build_sandbox` has a twin
            # here.
            self.assertEqual(adapters,
                             frozenset({os.path.realpath("/tmp/vendor/undx_router.py")}))


class PostureTest(GuardTestCase):

    def test_the_guard_counts_and_does_not_block_by_default(self):
        """A stack walk is a heuristic: a decorator, a thread pool or a
        `functools.partial` all move the frame it looks for. Being wrong about
        that would fail a user's request in order to enforce a policy about where
        code lives, which is worse than the thing being enforced."""
        answer = self.send(CHAT_URL, json={})
        self.assertIsNotNone(answer)
        self.assertEqual(self.chat(), 1)

    def test_enforcing_raises_instead(self):
        guard.enforce(True)
        self.addCleanup(guard.enforce, False)
        with self.assertRaises(guard.UnroutedProviderCall):
            guard.observe(CHAT_URL)

    def test_a_broken_guard_does_not_break_the_caller(self):
        """67 `requests.post` call sites in this repository have nothing to do
        with AI. A bug in here must not be able to take out a Stripe webhook."""
        with mock.patch.object(guard, "_classify", side_effect=RuntimeError("boom")):
            answer = self.send(CHAT_URL, json={})
        self.assertIsNotNone(answer)

    def test_the_url_is_never_logged(self):
        """Gemini's endpoint carries the API key in a query parameter. Logging
        the URL would copy a live credential into an aggregator with a different
        retention policy and a different audience than the environment the key
        lives in."""
        secret = "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent?key=sk-LEAKED"
        with self.assertLogs("services.undx_call_guard", level="ERROR") as caught:
            guard.observe(secret)
        joined = "\n".join(caught.output)
        self.assertNotIn("sk-LEAKED", joined)
        self.assertNotIn("generativelanguage", joined)
        self.assertIn(guard.UNROUTED_CHAT, joined)

    def test_witnesses_are_bounded(self):
        """A long-lived worker in a bad state would otherwise accumulate one
        string per call for the life of the process. The counter is the alarm;
        the first few witnesses are as diagnostic as the next thousand."""
        for _ in range(guard.MAX_WITNESSES + 5):
            guard.observe(CHAT_URL)
        self.assertEqual(self.chat(), guard.MAX_WITNESSES + 5)
        self.assertEqual(len(guard.witnesses()[guard.UNROUTED_CHAT]),
                         guard.MAX_WITNESSES)

    def test_install_is_idempotent(self):
        before = requests.post
        guard.install()
        guard.install()
        self.assertIs(requests.post, before)
        self.send(CHAT_URL, json={})
        self.assertEqual(self.chat(), 1)


class SnapshotTest(GuardTestCase):

    def test_ok_is_about_the_chat_counter_alone(self):
        """Embeddings and images bypass the router by design, pending an adapter.
        A health surface that reported "not ok" for a documented, planned gap
        would be reporting the plan as a fault, and a red light that is always on
        is not read."""
        self.send(EMBED_URL, json={})
        self.assertTrue(guard.snapshot()["ok"])
        self.send(CHAT_URL, json={})
        self.assertFalse(guard.snapshot()["ok"])

    def test_the_snapshot_says_whether_it_is_installed(self):
        """Otherwise a zero from a guard that never installed is indistinguishable
        from a zero it earned — the same question this whole file is about, asked
        of the surface an operator actually looks at."""
        self.assertTrue(guard.snapshot()["installed"])

    def test_the_fabric_is_not_ok_while_a_chat_call_is_bypassing_the_router(self):
        from services import undx_fabric_health

        self.send(CHAT_URL, json={})
        whole = undx_fabric_health.snapshot()
        self.assertFalse(whole["ok"])
        self.assertFalse(whole["routing"]["ok"])
        self.assertEqual(whole["routing"]["counters"][guard.UNROUTED_CHAT], 1)


if __name__ == "__main__":
    unittest.main()
