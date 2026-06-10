Pulse Mux Live Publish Pipeline Truth

Current architecture:
- Browser Live publishes camera/microphone tracks into a LiveKit room.
- The backend starts LiveKit Room Composite Egress for that room.
- LiveKit Egress forwards the room to Mux RTMP ingest using the session's Mux stream key.
- Mux HLS remains the public playback/archive layer.

Production truth:
- Primary mode: Browser Live -> LiveKit/WebRTC -> LiveKit Egress -> Mux RTMP/HLS.
- Advanced backup mode: OBS/RTMP can still connect directly to the host ingest URL and stream key.

Safe production copy now says:
- “Browser Live is publishing through LiveKit and forwarding to Mux.”
- “Stream has not started yet” for idle public viewers.

Safe env diagnostics:
- mux_token_configured is exposed only as a boolean by service diagnostics.
- mux_webhook_configured is exposed only as a boolean by service diagnostics.
- stream keys remain host/admin-only and are masked by default in Studio.
