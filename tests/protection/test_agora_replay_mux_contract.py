from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
RECORDING = (ROOT / "services" / "agora_cloud_recording_service.py").read_text(encoding="utf-8")
MUX = (ROOT / "services" / "mux_live_service.py").read_text(encoding="utf-8")


def test_private_r2_recording_is_prepared_for_mux_without_public_objects():
    assert "prepare_private_mux_input" in RECORDING
    assert 'Config(signature_version="s3v4")' in RECORDING
    assert 'ContentType": "video/mp2t"' in RECORDING
    assert "generate_presigned_url" in RECORDING
    assert "create_mux_asset_from_private_recording" in MUX


def test_mux_ready_stores_replay_identity_and_duration_before_reel_creation():
    webhook = BOT[BOT.index("def api_pulse_live_mux_webhook"):BOT.index("def pulse_livekit_config")]
    assert "mux_recording_duration_seconds" in webhook
    assert '"mux_asset_ready" if status == "ready" else "mux_retryable"' in webhook
    assert "pulse_live_publish_replay_reel" in webhook


def test_long_reel_is_linked_to_original_live_and_mux_vod():
    creator = BOT[BOT.index("def pulse_live_publish_replay_reel"):BOT.index("def api_pulse_live_end")]
    for field in ("source_live_id", "duration_seconds", "mux_asset_id", "mux_playback_id"):
        assert field in creator
    assert "COALESCE(replay_reel_id,0)=0" in creator
    assert '"created": False' in creator


def test_long_reel_keeps_original_live_presentation_without_replay_boilerplate():
    creator = BOT[BOT.index("def pulse_live_publish_replay_reel"):BOT.index("def api_pulse_live_end")]
    assert 'caption = title[:2200]' in creator
    assert 'create_post(host_user_id, caption, "video", title[:160]' in creator
    assert 'f"Live Replay: {title}"' not in creator
    assert "Replay of the live broadcast" not in creator
