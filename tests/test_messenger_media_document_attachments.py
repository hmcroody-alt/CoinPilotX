"""Documents are attachments, not a second upload system.

Private Office conversations need to carry a contract, a spreadsheet, a scan.
The Messenger media foundation was always shaped to allow that -- ``MEDIA_TYPES``
has carried ``"file"`` and ``SIZE_LIMIT_ENV`` has carried
``MESSENGER_FILE_MAX_MB`` since it was written -- but no document MIME type was
ever allowlisted, so the branch was unreachable and every document upload died
at ``unsupported_mime_type``. The fix widens the one shared allowlist rather
than giving the Office its own upload path; ordinary Messenger gains the same
capability in the same breath, which is the point.

Widening an allowlist is the kind of change that is easy to get quietly wrong,
so this suite is written around the failure modes rather than the feature:

* a document must not be able to render in the browser on our own origin, on
  either delivery path (local bytes and object-storage redirect),
* the executable/markup types must stay out of the table entirely -- absent, not
  present-and-mitigated,
* and the pre-existing media behaviour must not move at all.
"""

import ast
import os
import unittest
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "")

from services import messenger_media_foundation as foundation  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FOUNDATION_SOURCE = (REPO_ROOT / "services" / "messenger_media_foundation.py").read_text(encoding="utf-8")

DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "text/plain",
    "text/csv",
}

# The media types that existed before documents were allowlisted. Nothing about
# them may change; a test that only checks the new behaviour would not notice
# the allowlist rewrite breaking photos.
LEGACY_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "video/mp4", "video/webm",
    "audio/webm", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav", "audio/ogg",
}


def _request(**overrides):
    payload = {
        "conversation_id": 4242,
        "media_type": "file",
        "mime_type": "application/pdf",
        "filename": "quarterly-report.pdf",
        "size_bytes": 2 * 1024 * 1024,
    }
    payload.update(overrides)
    return payload


class DocumentUploadsAreAccepted(unittest.TestCase):
    def test_every_document_type_is_allowlisted_as_a_file(self):
        for mime in sorted(DOCUMENT_MIME_TYPES):
            entry = foundation.ALLOWED_MIME_TYPES.get(mime)
            self.assertIsNotNone(entry, f"{mime} is not allowlisted")
            self.assertEqual(entry["media_type"], "file", mime)
            self.assertTrue(entry["extensions"], mime)

    def test_a_pdf_validates_and_keeps_the_file_size_limit(self):
        resolved = foundation.validate_media_request(_request())
        self.assertEqual(resolved["media_type"], "file")
        self.assertEqual(resolved["mime_type"], "application/pdf")
        self.assertEqual(resolved["extension"], "pdf")
        self.assertEqual(resolved["max_size_bytes"], foundation.max_size_for("file"))

    def test_an_office_document_validates(self):
        resolved = foundation.validate_media_request(_request(
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="model.xlsx",
        ))
        self.assertEqual(resolved["extension"], "xlsx")

    def test_a_document_over_the_file_limit_is_refused_with_413(self):
        limit = foundation.max_size_for("file")
        with self.assertRaises(foundation.MessengerMediaError) as caught:
            foundation.validate_media_request(_request(size_bytes=limit + 1))
        self.assertEqual(caught.exception.error, "file_too_large")
        self.assertEqual(caught.exception.status_code, 413)

    def test_a_mismatched_extension_is_refused(self):
        """The declared type and the filename have to agree.

        This is the cheap half of the defence against a renamed executable: the
        server never trusts the extension alone, but it also refuses to write
        ``payload.exe`` to disk because a client called it a PDF.
        """
        with self.assertRaises(foundation.MessengerMediaError) as caught:
            foundation.validate_media_request(_request(filename="payload.exe"))
        self.assertEqual(caught.exception.error, "unsupported_extension")
        self.assertEqual(caught.exception.status_code, 415)

    def test_a_document_cannot_masquerade_as_a_photo(self):
        with self.assertRaises(foundation.MessengerMediaError) as caught:
            foundation.validate_media_request(_request(media_type="photo"))
        self.assertEqual(caught.exception.error, "media_type_mismatch")


class ScriptableTypesStayOut(unittest.TestCase):
    def test_markup_and_script_types_are_absent_from_the_allowlist(self):
        """Absent, not present-and-forced-to-download.

        ``text/html`` and ``image/svg+xml`` are stored XSS on our own origin if
        they ever render. A disposition header would mitigate that, but the
        mitigation is one edit away from being lost, so the types are simply not
        acceptable input. If a product need for them appears, it belongs behind
        a sanitizing pipeline, not behind this allowlist.
        """
        for mime in (
            "text/html", "application/xhtml+xml", "image/svg+xml",
            "application/javascript", "text/javascript",
            "application/x-msdownload", "application/x-sh",
            "application/zip", "application/x-zip-compressed",
        ):
            self.assertNotIn(mime, foundation.ALLOWED_MIME_TYPES, mime)
            with self.assertRaises(foundation.MessengerMediaError, msg=mime) as caught:
                foundation.validate_media_request(_request(mime_type=mime, filename="x.bin"))
            self.assertEqual(caught.exception.error, "unsupported_mime_type", mime)

    def test_an_alias_never_widens_the_allowlist(self):
        for alias, target in foundation.MIME_ALIASES.items():
            self.assertIn(target, foundation.ALLOWED_MIME_TYPES, alias)
            self.assertNotIn(alias, foundation.ALLOWED_MIME_TYPES, alias)


class DispositionIsDecidedByTheAllowlist(unittest.TestCase):
    def test_documents_download_and_media_renders(self):
        for mime in sorted(DOCUMENT_MIME_TYPES):
            self.assertEqual(foundation.disposition_for(mime), "attachment", mime)
        for mime in sorted(LEGACY_MIME_TYPES):
            self.assertEqual(foundation.disposition_for(mime), "inline", mime)

    def test_an_unknown_type_defaults_to_attachment(self):
        """The safe answer is the default.

        A future entry whose author forgets the ``disposition`` key, or a row
        carrying a type that has since been removed from the allowlist, must not
        inherit the rendering path.
        """
        self.assertEqual(foundation.disposition_for("application/x-not-real"), "attachment")
        self.assertEqual(foundation.disposition_for(""), "attachment")

    def test_every_entry_declares_a_valid_disposition_and_default_extension(self):
        for mime, entry in foundation.ALLOWED_MIME_TYPES.items():
            self.assertIn(entry.get("disposition"), {"inline", "attachment"}, mime)
            self.assertIn(mime, foundation.DEFAULT_EXTENSION_BY_MIME, mime)
            self.assertIn(foundation.DEFAULT_EXTENSION_BY_MIME[mime], entry["extensions"], mime)

    def test_only_media_formats_are_allowed_to_render_inline(self):
        """Inline is reserved for formats a browser decodes without scripting."""
        for mime, entry in foundation.ALLOWED_MIME_TYPES.items():
            if entry["disposition"] != "inline":
                continue
            self.assertTrue(
                mime.startswith(("image/", "video/", "audio/")),
                f"{mime} renders inline but is not an image, video or audio type",
            )


class BothDeliveryPathsCarryTheDecision(unittest.TestCase):
    """A document must not render on either path.

    There are two ways bytes reach a viewer: the download route streaming local
    files, and a 302 to a presigned object-storage URL. The presigned URL never
    touches our route, so a fix applied only to ``send_file`` would leave every
    R2-backed document rendering inline -- which is to say, the production path.
    """

    def test_the_download_target_reports_a_disposition_on_both_branches(self):
        tree = ast.parse(FOUNDATION_SOURCE)
        target_fn = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "attachment_download_target"
        )
        returned_dicts = [
            node.value for node in ast.walk(target_fn)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
        ]
        self.assertEqual(len(returned_dicts), 2, "expected a local branch and a signed-redirect branch")
        for returned in returned_dicts:
            keys = {k.value for k in returned.keys if isinstance(k, ast.Constant)}
            self.assertIn("disposition", keys)

    def test_the_presigned_url_forces_a_download_for_documents(self):
        captured = {}

        class _FakeClient:
            def generate_presigned_url(self, operation, Params=None, ExpiresIn=None):
                captured["params"] = dict(Params or {})
                return "https://objects.example/signed"

        original = foundation.media_storage.object_client
        foundation.media_storage.object_client = lambda: _FakeClient()
        try:
            url = foundation.signed_or_private_url({
                "id": 1,
                "storage_key": "messenger/1/report.pdf",
                "signed_url_strategy": "r2",
                "mime_type": "application/pdf",
                "original_filename": "quarterly-report.pdf",
            })
            self.assertEqual(url, "https://objects.example/signed")
            disposition = captured["params"].get("ResponseContentDisposition", "")
            self.assertTrue(disposition.startswith("attachment;"), disposition)
            self.assertIn("quarterly-report.pdf", disposition)

            captured.clear()
            foundation.signed_or_private_url({
                "id": 2,
                "storage_key": "messenger/2/photo.jpg",
                "signed_url_strategy": "r2",
                "mime_type": "image/jpeg",
                "original_filename": "photo.jpg",
            })
            self.assertNotIn("ResponseContentDisposition", captured["params"])
        finally:
            foundation.media_storage.object_client = original

    def test_the_download_route_reads_the_disposition_instead_of_hardcoding_it(self):
        """Guards the exact line that used to read ``as_attachment=False``."""
        bot_source = (REPO_ROOT / "bot.py").read_text(encoding="utf-8")
        marker = "def api_messages_media_download("
        start = bot_source.index(marker)
        body = bot_source[start:start + 2000]
        self.assertIn('as_attachment=(target.get("disposition") == "attachment")', body)
        self.assertNotIn("as_attachment=False", body)
        # The other delivery headers are load-bearing and stay.
        self.assertIn('response.headers["X-Content-Type-Options"] = "nosniff"', body)


class LegacyMediaBehaviourIsUnchanged(unittest.TestCase):
    def test_the_original_media_types_are_still_allowlisted_with_their_types(self):
        for mime in sorted(LEGACY_MIME_TYPES):
            self.assertIn(mime, foundation.ALLOWED_MIME_TYPES, mime)
        self.assertEqual(foundation.ALLOWED_MIME_TYPES["image/jpeg"]["media_type"], "photo")
        self.assertEqual(foundation.ALLOWED_MIME_TYPES["video/mp4"]["media_type"], "video")
        self.assertEqual(foundation.ALLOWED_MIME_TYPES["audio/ogg"]["media_type"], "voice")

    def test_a_photo_still_validates_exactly_as_before(self):
        resolved = foundation.validate_media_request(_request(
            media_type="photo", mime_type="image/jpeg", filename="snap.jpg",
            size_bytes=1024,
        ))
        self.assertEqual(resolved["media_type"], "photo")
        self.assertEqual(resolved["extension"], "jpg")
        self.assertEqual(resolved["max_size_bytes"], foundation.max_size_for("photo"))

    def test_the_voice_alias_still_resolves(self):
        resolved = foundation.validate_media_request(_request(
            media_type="voice", mime_type="audio/x-m4a", filename="note.m4a",
            size_bytes=2048,
        ))
        self.assertEqual(resolved["mime_type"], "audio/mp4")


if __name__ == "__main__":
    unittest.main()
