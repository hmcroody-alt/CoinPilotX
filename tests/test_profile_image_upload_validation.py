"""The profile image upload accepts bytes, not claims about bytes.

A filename ending in .jpg and a Content-Type of image/jpeg are both supplied by
the caller, so neither is evidence. The signature check is the only part of the
gate the caller cannot write, which is why it has to be the part that decides.
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.datastructures import FileStorage  # noqa: E402

import bot  # noqa: E402


def upload(payload, filename="avatar.jpg", content_type="image/jpeg"):
    return FileStorage(stream=io.BytesIO(payload), filename=filename, content_type=content_type)


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 28
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBPVP8 " + b"\x00" * 16


class ProfileImageSignatureTest(unittest.TestCase):
    def test_accepts_the_three_formats_the_endpoint_advertises(self):
        for name, payload in (("jpeg", JPEG), ("png", PNG), ("webp", WEBP)):
            with self.subTest(name):
                self.assertTrue(bot._profile_image_upload_signature_allowed(upload(payload)))

    def test_rejects_a_script_wearing_a_jpg_extension_and_an_image_mimetype(self):
        disguised = upload(b"<?php system($_GET['c']); ?>", filename="avatar.jpg", content_type="image/jpeg")
        self.assertFalse(bot._profile_image_upload_signature_allowed(disguised))

    def test_rejects_formats_the_endpoint_does_not_advertise(self):
        self.assertFalse(bot._profile_image_upload_signature_allowed(upload(b"GIF89a" + b"\x00" * 26)))
        self.assertFalse(bot._profile_image_upload_signature_allowed(upload(b"%PDF-1.7" + b"\x00" * 24)))

    def test_rejects_riff_containers_that_are_not_webp(self):
        wav = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVEfmt " + b"\x00" * 16
        self.assertFalse(bot._profile_image_upload_signature_allowed(wav and upload(wav)))

    def test_rejects_an_empty_upload_and_a_missing_one(self):
        self.assertFalse(bot._profile_image_upload_signature_allowed(upload(b"")))
        self.assertFalse(bot._profile_image_upload_signature_allowed(None))

    # save_upload reads the same stream immediately afterwards, so a check that
    # consumed the head would hand the storage layer a truncated file.
    def test_leaves_the_stream_where_it_found_it(self):
        storage = upload(JPEG)
        self.assertTrue(bot._profile_image_upload_signature_allowed(storage))
        self.assertEqual(storage.stream.tell(), 0)
        self.assertEqual(storage.stream.read(), JPEG)

    # The signature is read from wherever the stream currently sits, so an
    # already-consumed upload cannot be waved through on the strength of a
    # header nobody can still see.
    def test_fails_closed_on_a_stream_somebody_else_already_read(self):
        storage = upload(JPEG)
        storage.stream.read(2)
        self.assertFalse(bot._profile_image_upload_signature_allowed(storage))


if __name__ == "__main__":
    unittest.main()
