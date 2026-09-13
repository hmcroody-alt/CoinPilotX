import sys
import types

from services import agora_cloud_recording_service


class _Body:
    def __init__(self, value: bytes):
        self.value = value
        self.pos = 0

    def read(self, size=None):
        if size is None:
            chunk, self.pos = self.value[self.pos:], len(self.value)
            return chunk
        chunk = self.value[self.pos:self.pos + size]
        self.pos += len(chunk)
        return chunk


class _R2:
    def __init__(self, segments=None, manifest=None):
        self.segments = segments or {
            "pulsesoc/live-recordings/8/part-1.ts": b"\x47" + b"a" * 99,
            "pulsesoc/live-recordings/8/part-2.ts": b"\x47" + b"b" * 99,
        }
        self.manifest = manifest or (
            "#EXTM3U\n#EXTINF:2,\npart-1.ts\n#EXTINF:2,\npart-2.ts\n#EXT-X-ENDLIST\n"
        )
        self.gets = []
        self.puts = []
        self.parts = []
        self.completed = None
        self.aborted = []
        self.existing = {}

    def get_object(self, *, Bucket, Key):
        self.gets.append(Key)
        if Key.endswith(".m3u8"):
            return {"Body": _Body(self.manifest.encode())}
        return {"Body": _Body(self.segments[Key])}

    def head_object(self, *, Bucket, Key):
        if Key in self.segments:
            return {"ContentLength": len(self.segments[Key])}
        if Key in self.existing:
            return {"ContentLength": self.existing[Key]}
        raise KeyError(Key)

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        return f"https://signed.example/{Params['Key']}?ttl={ExpiresIn}"

    def put_object(self, **kwargs):
        self.puts.append(kwargs)

    def create_multipart_upload(self, **kwargs):
        self.multipart_kwargs = kwargs
        return {"UploadId": "upload-1"}

    def upload_part(self, *, Bucket, Key, PartNumber, UploadId, Body):
        self.parts.append({"PartNumber": PartNumber, "size": len(Body), "body": Body})
        return {"ETag": f"etag-{PartNumber}"}

    def complete_multipart_upload(self, *, Bucket, Key, UploadId, MultipartUpload):
        self.completed = MultipartUpload["Parts"]

    def abort_multipart_upload(self, **kwargs):
        self.aborted.append(kwargs)


def _install(monkeypatch, r2):
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda *a, **k: r2))
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://r2.example")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_BUCKET", "private")


def test_private_mux_input_is_one_muxed_ts_file_not_a_playlist(monkeypatch):
    """Mux VOD ingest rejects an .m3u8 with invalid_input, so the input must be a media file."""
    r2 = _R2()
    _install(monkeypatch, r2)

    result = agora_cloud_recording_service.prepare_private_mux_input(
        "pulsesoc/live-recordings/8", "recording.m3u8"
    )

    assert result["ok"] is True
    assert result["segments"] == 2
    assert result["object_key"] == "pulsesoc/live-recordings/8/mux-ingest.ts"
    assert result["bytes"] == 200
    assert result["input_url"].startswith(
        "https://signed.example/pulsesoc/live-recordings/8/mux-ingest.ts"
    )
    assert r2.gets[0] == "pulsesoc/live-recordings/8/recording.m3u8"
    assert len(r2.puts) == 1
    assert r2.puts[0]["ContentType"] == "video/mp2t"
    assert r2.puts[0]["Body"] == b"\x47" + b"a" * 99 + b"\x47" + b"b" * 99


def test_large_recording_uploads_equal_parts_except_the_last(monkeypatch):
    """R2 rejects a multipart upload whose non-final parts differ in size."""
    r2 = _R2(
        segments={
            "pulsesoc/live-recordings/9/part-1.ts": b"a" * (4 * 1024 ** 2),
            "pulsesoc/live-recordings/9/part-2.ts": b"b" * (4 * 1024 ** 2),
            "pulsesoc/live-recordings/9/part-3.ts": b"c" * (4 * 1024 ** 2),
        },
        manifest="#EXTM3U\npart-1.ts\npart-2.ts\npart-3.ts\n#EXT-X-ENDLIST\n",
    )
    _install(monkeypatch, r2)
    monkeypatch.setenv("R2_MUX_INPUT_PART_BYTES", str(5 * 1024 ** 2))

    result = agora_cloud_recording_service.prepare_private_mux_input(
        "pulsesoc/live-recordings/9", "recording.m3u8"
    )

    assert result["ok"] is True
    assert result["bytes"] == 12 * 1024 ** 2
    assert r2.puts == []
    assert r2.multipart_kwargs["ContentType"] == "video/mp2t"
    sizes = [part["size"] for part in r2.parts]
    assert sizes[:-1] == [5 * 1024 ** 2] * (len(sizes) - 1)
    assert sizes[-1] <= 5 * 1024 ** 2
    assert sum(sizes) == 12 * 1024 ** 2
    assert b"".join(part["body"] for part in r2.parts) == b"a" * (4 * 1024 ** 2) + b"b" * (
        4 * 1024 ** 2
    ) + b"c" * (4 * 1024 ** 2)
    assert [p["PartNumber"] for p in r2.completed] == [1, 2, 3]


def test_retry_reuses_the_joined_object_instead_of_rebuilding_it(monkeypatch):
    r2 = _R2()
    r2.existing["pulsesoc/live-recordings/8/mux-ingest.ts"] = 200
    _install(monkeypatch, r2)

    result = agora_cloud_recording_service.prepare_private_mux_input(
        "pulsesoc/live-recordings/8", "recording.m3u8"
    )

    assert result["ok"] is True
    assert r2.puts == []
    assert r2.parts == []


def test_unfinished_upload_is_not_handed_to_mux(monkeypatch):
    r2 = _R2(manifest="#EXTM3U\n#EXTINF:2,\npart-1.ts\n")
    _install(monkeypatch, r2)

    result = agora_cloud_recording_service.prepare_private_mux_input(
        "pulsesoc/live-recordings/8", "recording.m3u8"
    )

    assert result == {
        "ok": False,
        "reason": "recording_upload_pending",
        "message": "The recording is still uploading.",
    }
    assert r2.puts == []


def test_restart_recovers_finalized_manifest_without_restarting_recorder(monkeypatch):
    r2 = _R2()
    r2.list_objects_v2 = lambda **kwargs: {"Contents": [{"Key": "pulsesoc/live-recordings/8/recording.m3u8"}, {"Key": "pulsesoc/live-recordings/8/mux-ingest.m3u8"}]}
    _install(monkeypatch, r2)
    result = agora_cloud_recording_service.find_finalized_recording("pulsesoc/live-recordings/8")
    assert result == {"ok": True, "filename": "pulsesoc/live-recordings/8/recording.m3u8"}
    assert r2.puts == []
