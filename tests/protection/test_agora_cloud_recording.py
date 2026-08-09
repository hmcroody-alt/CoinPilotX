import json

from services import agora_cloud_recording_service as recording


def test_acquire_uses_server_authority_without_rtc_token(monkeypatch):
    captured = {}
    monkeypatch.setattr(recording, "_request", lambda path, **kwargs: captured.update(path=path, **kwargs) or {"ok": True, "data": {"resourceId": "resource"}})

    result = recording.acquire(live_id=42, channel_name="pulse-live-42")

    assert result["resource_id"] == "resource"
    assert captured["path"] == "cloud_recording/acquire"
    assert captured["payload"]["clientRequest"] == {"scene": 0, "resourceExpiredHour": 24}
    assert "token" not in json.dumps(captured["payload"]).lower()


def test_start_records_composite_hls_to_server_configured_r2(monkeypatch):
    captured = {}
    env = {
        "R2_ENDPOINT_URL": "https://example.r2.cloudflarestorage.com",
        "R2_BUCKET": "private-recordings",
        "R2_ACCESS_KEY_ID": "access",
        "R2_SECRET_ACCESS_KEY": "secret",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(recording, "_rtc_token", lambda *_: "rtc-token")
    monkeypatch.setattr(recording, "_request", lambda path, **kwargs: captured.update(path=path, **kwargs) or {"ok": True, "data": {"sid": "sid"}})

    result = recording.start(live_id=42, channel_name="pulse-live-42", resource_id="resource", recording_uid="3000000042")

    config = captured["payload"]["clientRequest"]
    assert result["sid"] == "sid"
    assert config["recordingFileConfig"] == {"avFileType": ["hls"]}
    assert config["recordingConfig"]["streamTypes"] == 2
    assert config["recordingConfig"]["transcodingConfig"]["mixedVideoLayout"] == 2
    assert config["storageConfig"]["vendor"] == 11
    assert config["storageConfig"]["fileNamePrefix"] == ["pulsesoc", "live-recordings", "42"]
    assert config["storageConfig"]["extensionParams"]["endpoint"] == "example.r2.cloudflarestorage.com"


def test_stop_selects_finalized_hls_manifest(monkeypatch):
    monkeypatch.setattr(recording, "_request", lambda *args, **kwargs: {"ok": True, "data": {"serverResponse": {"uploadingStatus": "uploaded", "fileList": [{"fileName": "recording.m3u8"}]}}})

    result = recording.stop(channel_name="pulse-live-42", resource_id="resource", sid="sid", recording_uid="3000000042")

    assert result["filename"] == "recording.m3u8"
    assert result["uploading_status"] == "uploaded"


def test_recording_url_is_scoped_and_encoded(monkeypatch):
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://media.example.test/")
    assert recording.public_recording_url("pulsesoc/live-recordings/42", "final stream.m3u8") == "https://media.example.test/pulsesoc/live-recordings/42/final%20stream.m3u8"
