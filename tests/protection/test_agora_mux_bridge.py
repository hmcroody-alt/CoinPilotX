import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services import agora_media_push_service


class _Response:
    status = 201

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_bridge_fails_closed_without_rest_credentials(monkeypatch):
    monkeypatch.setenv("AGORA_APP_ID", "app")
    monkeypatch.delenv("AGORA_REST_CUSTOMER_ID", raising=False)
    monkeypatch.delenv("AGORA_REST_CUSTOMER_SECRET", raising=False)
    result = agora_media_push_service.start_mux_bridge(
        live_id=7, channel_name="pulse-live-7", rtmp_url="rtmp://mux/key", host_uid=42
    )
    assert result == {
        "ok": False,
        "reason": "not_configured",
        "message": "Agora Media Push REST credentials are not configured.",
    }


def test_bridge_uses_transcoded_all_publisher_audio_and_server_mux_target(monkeypatch):
    monkeypatch.setenv("AGORA_APP_ID", "app")
    monkeypatch.setenv("AGORA_REST_CUSTOMER_ID", "customer")
    monkeypatch.setenv("AGORA_REST_CUSTOMER_SECRET", "secret")
    observed = {}

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["headers"] = dict(request.headers)
        observed["payload"] = json.loads(request.data)
        return _Response({"data": {"converter": {"id": "converter-1", "state": "connecting"}}})

    monkeypatch.setattr(agora_media_push_service, "urlopen", fake_urlopen)
    result = agora_media_push_service.start_mux_bridge(
        live_id=7, channel_name="pulse-live-7", rtmp_url="rtmp://mux/hidden-key", host_uid=42
    )
    converter = observed["payload"]["converter"]
    assert result == {"ok": True, "converter_id": "converter-1", "state": "connecting"}
    assert converter["transcodeOptions"]["rtcChannel"] == "pulse-live-7"
    assert "rtcStreamUids" not in converter["transcodeOptions"]["audioOptions"]
    assert converter["transcodeOptions"]["videoOptions"]["layoutType"] == 1
    assert converter["transcodeOptions"]["videoOptions"]["vertical"]["maxResolutionUid"] == 42
    assert converter["rtmpUrl"] == "rtmp://mux/hidden-key"
    assert "hidden-key" not in observed["url"]


def test_stop_bridge_is_idempotent_without_converter_id():
    assert agora_media_push_service.stop_mux_bridge("") == {"ok": True, "already_stopped": True}


def test_native_live_responses_do_not_expose_mux_publish_credentials():
    source = (Path(__file__).resolve().parents[2] / "bot.py").read_text(encoding="utf-8")
    start_response = source[source.index('return jsonify({\n            "ok": True,\n            "message": "Live session created."'):source.index('@webhook_app.route("/api/pulse/live/mux/create"')]
    get_route = source[source.index('@webhook_app.route("/api/pulse/live/mux/<mux_live_stream_id>"'):source.index('@webhook_app.route("/api/pulse/live/mux/disable"')]
    assert '"stream_key":' not in start_response
    assert '"rtmp_url":' not in start_response
    assert 'safe["stream_key"]' not in get_route
    assert 'safe["rtmp_url"]' not in get_route
