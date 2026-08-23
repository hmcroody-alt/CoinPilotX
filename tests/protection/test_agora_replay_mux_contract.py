import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
RECORDING = (ROOT / "services" / "agora_cloud_recording_service.py").read_text(encoding="utf-8")
MUX = (ROOT / "services" / "mux_live_service.py").read_text(encoding="utf-8")
WORKER = (ROOT / "media_worker.py").read_text(encoding="utf-8")


def test_private_r2_recording_is_prepared_for_mux_without_public_objects():
    assert "prepare_private_mux_input" in RECORDING
    assert 'Config(signature_version="s3v4")' in RECORDING
    assert 'ContentType="application/vnd.apple.mpegurl"' in RECORDING
    assert "generate_presigned_url" in RECORDING
    assert "get_object(Bucket=bucket, Key=segment_key)" not in RECORDING
    assert "upload_fileobj" not in RECORDING
    assert "create_mux_asset_from_private_recording" in MUX


def test_end_live_enqueues_exactly_one_replay_job_without_moving_video_in_flask():
    endpoint = BOT[BOT.index("def api_pulse_live_end"):BOT.index("def pulse_creator_status_page")]
    assert "finalize_live_replay" in endpoint
    assert "prepare_private_mux_input" not in endpoint
    assert "create_mux_asset_from_private_recording" not in endpoint
    assert "agora_cloud_recording_service.stop(" not in endpoint
    assert "status IN ('pending','processing')" in endpoint


def test_existing_media_worker_finalizes_and_reconciles_live_replay():
    assert '"finalize_live_replay"' in WORKER
    assert "_process_live_replay_job" in WORKER
    assert "prepare_private_mux_input" in WORKER
    assert "create_mux_asset_from_private_recording" in WORKER
    assert "create_mux_asset_from_live_recording" in WORKER
    assert "mark_live_feed_replay_ready" in WORKER
    assert "reconcile_live_replay_backlog" in WORKER
    assert "timedelta(minutes=10)" in WORKER
    assert "terminal_posts_repaired" in WORKER
    assert "recording_status='replay_failed'" in WORKER
    assert "'replay_unavailable','replay_failed'" in WORKER
    assert "'pending',0,5" in WORKER
    assert 'MEDIA_WORKER_INTERVAL_SECONDS", "5"' in WORKER


def test_mux_ready_stores_replay_identity_and_duration_before_reel_creation():
    webhook = BOT[BOT.index("def api_pulse_live_mux_webhook"):BOT.index("def pulse_live_audio_v2_env_flag")]
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
    assert "pulse_feed_engine.create_post" not in creator
    assert "mark_live_feed_replay_ready" in creator
    assert 'f"Live Replay: {title}"' not in creator
    assert "Replay of the live broadcast" not in creator


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
