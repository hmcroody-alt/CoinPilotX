"""The web Messenger must not caption an attachment for the sender.

Native stopped printing "Voice message" in 3a91e15f. The web client did not: its
optimistic send substituted the literal for an empty body, so a voice note
carried the words "Voice message" above its own player until the next load
dropped them -- the same message rendered two ways either side of a refresh.

Pinned as source rather than through a DOM: `pulse_messages_v2.js` is an IIFE
that binds to `document` and `window.PULSE_*` at load, and standing that up is a
larger apparatus than the properties being asserted. Assertions carry their own
messages because the file is 200KB and a bare assertIn prints all of it.
"""

import re
import unittest
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[2] / "static" / "js" / "pulse_messages_v2.js"


def _function_body(source: str, name: str) -> str:
    """The text of one top-level function inside the IIFE."""
    head = f"function {name}("
    assert head in source, f"{name} is gone from the client"
    return source.split(head, 1)[1].split("\n  function ", 1)[0].split("\n  async function ", 1)[0]


class TheWebBubbleNeverInventsABody(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = CLIENT.read_text(encoding="utf-8")
        cls.bubble = _function_body(source, "messageHtml")
        cls.helper = _function_body(source, "bubbleBodyText")
        cls.send = _function_body(source, "sendMessage")
        cls.retry = _function_body(source, "retryFailedMessage")
        cls.list_row = _function_body(source, "conversationPreview")

    def _absent(self, needle: str, region: str, where: str):
        self.assertNotIn(needle, region, f"{needle!r} is back in {where}")

    def test_the_literal_label_is_gone_from_the_bubble(self):
        """Scoped to the bubble on purpose.

        The conversation list's one-line summary still says "Voice message", and
        should: there is no player in a sidebar row, so the row has nothing else
        to say. What was wrong was printing it *inside* the bubble, beside the
        player that already says it.
        """
        self._absent("Voice message", self.bubble, "the bubble renderer")
        self._absent("Voice message", self.helper, "the body filter")
        self.assertIn("previewMediaLabel", self.list_row, "the sidebar lost its summary label")

    def test_the_optimistic_send_uses_the_typed_body_and_nothing_else(self):
        """MUTATION: restore the `||` stand-in and the bubble labels itself again."""
        self.assertIn("body: body.trim(),", self.send, "the optimistic body is no longer the typed body")
        self._absent("body: body.trim() ||", self.send, "the optimistic send")

    def test_the_renderer_filters_the_body_instead_of_printing_it_raw(self):
        """MUTATION: put `item.body` back here and filenames return to the bubble."""
        self.assertIn("bubbleBodyText(item)", self.bubble, "the renderer stopped filtering the body")
        self._absent("linkifiedMessageHtml(item.body)", self.bubble, "the bubble renderer")

    def test_a_voice_body_and_a_filename_body_both_resolve_to_nothing(self):
        voice_types = re.search(r"\[([^\]]*)\]\.includes\(type\)\) return \"\";", self.helper)
        self.assertIsNotNone(voice_types, "the voice branch is gone from the filter")
        for kind in ("voice", "audio", "voice_note"):
            self.assertIn(f'"{kind}"', voice_types.group(1), f"{kind} no longer drops its body")
        self.assertIn(".test(body)", self.helper, "the filename rule is gone")
        filename_re = re.compile(r"^[^\s/\\]+\.[A-Za-z0-9]{2,5}$")
        self.assertTrue(filename_re.match("pulsesoc-voice-1784432743856.m4a"))
        self.assertTrue(filename_re.match("IMG_5024.jpg"))
        # A typed sentence that happens to contain a dot is a caption, not a file.
        self.assertIsNone(filename_re.match("look at this. amazing"))

    def test_retrying_a_failed_send_restores_the_caption_not_the_placeholder(self):
        self.assertIn("bubbleBodyText(failed)", self.retry, "retry no longer filters the recovered body")
        self._absent('"Attachment"', self.retry, "the retry path")


if __name__ == "__main__":
    unittest.main()
