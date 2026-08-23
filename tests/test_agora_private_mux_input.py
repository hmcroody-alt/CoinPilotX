import sys
import types

from services import agora_cloud_recording_service


class _Body:
    def __init__(self, value: bytes):
        self.value = value

    def read(self):
        return self.value


class _R2:
    def __init__(self):
        self.gets = []
        self.puts = []

    def get_object(self, *, Bucket, Key):
        self.gets.append(Key)
        assert Key.endswith("recording.m3u8")
        return {"Body": _Body(b"#EXTM3U\n#EXTINF:2,\npart-1.ts\n#EXTINF:2,\npart-2.ts\n#EXT-X-ENDLIST\n")}

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        return f"https://signed.example/{Params['Key']}?ttl={ExpiresIn}"

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


def test_private_mux_input_rewrites_manifest_without_copying_video(monkeypatch):
    r2 = _R2()
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda *args, **kwargs: r2))
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://r2.example")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_BUCKET", "private")

    result = agora_cloud_recording_service.prepare_private_mux_input("pulsesoc/live-recordings/8", "recording.m3u8")

    assert result["ok"] is True
    assert result["segments"] == 2
    assert r2.gets == ["pulsesoc/live-recordings/8/recording.m3u8"]
    assert len(r2.puts) == 1
    assert r2.puts[0]["Key"].endswith("mux-ingest.m3u8")
    assert r2.puts[0]["ContentType"] == "application/vnd.apple.mpegurl"
    body = r2.puts[0]["Body"].decode("utf-8")
    assert "https://signed.example/pulsesoc/live-recordings/8/part-1.ts" in body
    assert "https://signed.example/pulsesoc/live-recordings/8/part-2.ts" in body
