"""The shared type normalizer, which is the thing that was actually broken.

``resolve_media_class`` and ``sniff_media_class`` are the whole of §2-3: one
authority that turns (filename, declared MIME) into a media class, so Messenger,
posts and Reels stop each guessing separately about the same bytes. Both were
written without a single test, which is how the original defect survived --
"That file type is not supported for Messenger media." for an ordinary iPhone
video was one missing allowlist row, and nothing in the suite could have noticed
it being missing or being removed again.

The tests here are written from the two directions the normalizer can fail:

* too strict -- a real container spelled a way the table does not carry, which
  is the reported bug, and
* too loose -- an unrecognised or lying type getting through, which is worse
  than the bug because it opens a rendering path for bytes nobody identified.
"""

import os
import unittest

os.environ.setdefault("DATABASE_URL", "")

from services import messenger_media_foundation as foundation  # noqa: E402

MessengerMediaError = foundation.MessengerMediaError


class AnIPhoneVideoIsAVideo(unittest.TestCase):
    """The reported bug, stated as the class of input that triggered it.

    A photo-library video on iOS is QuickTime. The picker reports it honestly,
    and every spelling below has been seen in the wild for the same bytes, so
    each has to land on the same media class rather than on a 415.
    """

    def test_the_honest_picker_spelling_resolves_to_video(self):
        resolved = foundation.resolve_media_class("IMG_4021.MOV", "video/quicktime")
        self.assertEqual(resolved["media_type"], "video")
        self.assertEqual(resolved["mime_type"], "video/quicktime")
        self.assertEqual(resolved["extension"], "mov")

    def test_the_alternate_spellings_resolve_to_the_same_entry(self):
        for declared in ("video/x-quicktime", "video/mov", "VIDEO/QUICKTIME"):
            resolved = foundation.resolve_media_class("clip.mov", declared)
            self.assertEqual(resolved["mime_type"], "video/quicktime", declared)
            self.assertEqual(resolved["media_type"], "video", declared)

    def test_a_mov_whose_type_was_lost_is_rescued_by_its_name(self):
        """Pickers and desktop browsers both send octet-stream for .mov.

        The extension is the only evidence left at that point, and refusing here
        is the same user-visible failure as not allowlisting the type at all.
        """
        for declared in ("", "application/octet-stream"):
            resolved = foundation.resolve_media_class("holiday.mov", declared)
            self.assertEqual(resolved["mime_type"], "video/quicktime", declared)

    def test_the_upload_request_for_one_is_accepted_end_to_end(self):
        validated = foundation.validate_media_request({
            "conversation_id": 88,
            "media_type": "video",
            "mime_type": "video/quicktime",
            "filename": "IMG_4021.MOV",
            "size_bytes": 40 * 1024 * 1024,
        })
        self.assertEqual(validated["mime_type"], "video/quicktime")
        self.assertEqual(validated["media_type"], "video")

    def test_the_other_container_families_still_resolve(self):
        for filename, declared, expected in (
            ("a.mp4", "video/mp4", "video"),
            ("a.m4v", "video/x-m4v", "video"),
            ("a.webm", "video/webm", "video"),
            ("a.jpg", "image/jpeg", "photo"),
            ("a.heic", "image/heic", "photo"),
            ("a.pdf", "application/pdf", "file"),
            ("a.m4a", "audio/x-m4a", "voice"),
        ):
            self.assertEqual(foundation.resolve_media_class(filename, declared)["media_type"], expected, filename)


class AnUnrecognisedTypeIsRefusedRatherThanTrusted(unittest.TestCase):
    """The other direction. A normalizer that answers something for everything
    is not a normalizer -- it is a bypass with a docstring.
    """

    def test_a_type_and_extension_nobody_allowlists_is_refused(self):
        for filename, declared in (
            ("payload.bin", "application/x-msdownload"),
            ("script.sh", "text/x-shellscript"),
            ("page.html", "text/html"),
            ("unknown", "application/octet-stream"),
            ("unknown", ""),
        ):
            with self.assertRaises(MessengerMediaError, msg=f"{filename} {declared}") as caught:
                foundation.resolve_media_class(filename, declared)
            self.assertEqual(caught.exception.error, "unsupported_mime_type")
            self.assertEqual(caught.exception.status_code, 415)

    def test_an_extension_rescue_can_only_land_on_an_allowlisted_type(self):
        """The rescue path reads a table, so it cannot invent an entry."""
        with self.assertRaises(MessengerMediaError):
            foundation.resolve_media_class("thing.exe", "application/octet-stream")

    def test_an_extension_cannot_promote_a_document_into_a_video(self):
        """Correcting a MIME is allowed only inside one media class.

        A .mov name on a PDF must not buy the video size ceiling and the inline
        disposition for bytes that are a document. The rescue is scoped to the
        declared class, so it never fires here, and the disagreement is then
        refused outright rather than resolved in either direction.
        """
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.resolve_media_class("invoice.mov", "application/pdf")
        self.assertEqual(caught.exception.error, "unsupported_extension")
        self.assertEqual(caught.exception.status_code, 415)

    def test_a_video_request_cannot_carry_a_document(self):
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.validate_media_request({
                "conversation_id": 88,
                "media_type": "video",
                "mime_type": "application/pdf",
                "filename": "invoice.pdf",
                "size_bytes": 2048,
            })
        self.assertEqual(caught.exception.error, "media_type_mismatch")


class TheBytesGetTheLastWord(unittest.TestCase):
    """``sniff_media_class`` exists because a declared type is a claim.

    Its "" answer is load-bearing and easy to mistake for a pass: the ISO
    base-media containers genuinely cannot be classified from their header, so ""
    means "no evidence" and the allowlist decides. A sniffer that answered a
    class for those would reject correct uploads; one that answered "" for an
    executable would accept a renamed binary.
    """

    def test_an_executable_is_forbidden_whatever_it_claims_to_be(self):
        for header in (b"MZ\x90\x00", b"\x7fELF\x02\x01", b"\xcf\xfa\xed\xfe\x07", b"#!/bin/sh\n"):
            self.assertEqual(foundation.sniff_media_class(header), "forbidden", header)

    def test_an_iso_container_is_undecided_not_wrong(self):
        # 'ftyp' at offset 4 covers mp4, m4v, mov and m4a alike; the brand does
        # not separate audio-only from video, so answering a class here would
        # reject one of them.
        self.assertEqual(foundation.sniff_media_class(b"\x00\x00\x00\x18ftypqt  \x00\x00\x00\x00"), "")
        self.assertEqual(foundation.sniff_media_class(b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00"), "")

    def test_the_classes_it_can_decide_it_decides(self):
        for header, expected in (
            (b"\xff\xd8\xff\xe0", "photo"),
            (b"\x89PNG\r\n\x1a\n", "photo"),
            (b"%PDF-1.7", "file"),
            (b"PK\x03\x04\x14", "file"),
            (b"OggS\x00\x02", "voice"),
        ):
            self.assertEqual(foundation.sniff_media_class(header), expected, header)

    def test_no_bytes_is_no_evidence(self):
        self.assertEqual(foundation.sniff_media_class(b""), "")


if __name__ == "__main__":
    unittest.main()
