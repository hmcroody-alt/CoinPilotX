Pulse Mux Live Publish Pipeline Truth

Current architecture:
- Browser camera preview works locally in Pulse Live Studio.
- Mux Live ingest is RTMP based.
- The current app does not include a browser-to-Mux RTMP bridge, WHIP publisher, or WebRTC-to-RTMP relay.
- A browser MediaStream cannot be pushed directly to Mux RTMP by this UI alone.

Production truth:
- Mode A: OBS/RTMP mode is the functional broadcast path.
- Mode B: Browser Live mode remains future work until a real browser-compatible publish layer exists.

Safe production copy now says:
- “Browser camera preview only — use RTMP/OBS to go live.”
- “Stream has not started yet” for idle public viewers.

Safe env diagnostics:
- mux_token_configured is exposed only as a boolean by service diagnostics.
- mux_webhook_configured is exposed only as a boolean by service diagnostics.
- stream keys remain host/admin-only.
