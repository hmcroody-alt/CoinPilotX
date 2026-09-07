from services import live_distribution_service


def test_finished_live_uses_durable_replay_url_not_expired_live_url():
    manifest = live_distribution_service.playback_manifest({
        "id": 41,
        "status": "ended",
        "mux_live_status": "idle",
        "playback_url": "https://old.example/live.m3u8",
        "replay_url": "https://stream.mux.com/replay-41.m3u8",
        "mux_playback_id": "live-41",
        "mux_recording_playback_id": "replay-41",
    })

    assert manifest["playback_url"] == "https://stream.mux.com/replay-41.m3u8"
    assert manifest["hls_url"] == "https://stream.mux.com/replay-41.m3u8"
    assert manifest["mux_playback_id"] == "replay-41"
    assert manifest["supports_hls"] is True
    assert manifest["preferred_transport"] == "hls"


def test_finished_live_can_reconstruct_replay_from_recording_playback_id():
    manifest = live_distribution_service.playback_manifest({
        "id": 42,
        "status": "archived",
        "mux_live_status": "idle",
        "mux_recording_playback_id": "replay-42",
    })

    assert manifest["playback_url"] == "https://stream.mux.com/replay-42.m3u8"
    assert manifest["supports_hls"] is True


def test_active_live_still_uses_live_playback_identity():
    manifest = live_distribution_service.playback_manifest({
        "id": 43,
        "status": "live",
        "publish_state": "live",
        "mux_live_status": "live",
        "mux_playback_id": "live-43",
        "mux_recording_playback_id": "replay-must-not-win",
        "replay_url": "https://stream.mux.com/replay-must-not-win.m3u8",
    })

    assert manifest["playback_url"] == "https://stream.mux.com/live-43.m3u8"
    assert manifest["mux_playback_id"] == "live-43"
