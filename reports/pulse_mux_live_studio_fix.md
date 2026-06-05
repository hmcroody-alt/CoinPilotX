Pulse Mux Live Studio Fix

Completed:
- Removed misleading “Ready to go live” copy.
- Added OBS/RTMP mode instructions.
- Added copy buttons for ingest URL, stream key, and RTMP URL.
- Added “Check Mux Status” button.
- Added 10–15 second Mux status polling while Studio is open.
- Mux active/live status updates Pulse as live.
- Mux idle/disabled status updates Pulse as idle.
- Public viewer shows waiting state when idle instead of a broken player.
- Public viewer prefers Mux HLS playback URL when playback ID exists.

Not implemented:
- Browser-to-Mux broadcasting.
- WebRTC/WHIP publishing.
- RTMP bridge.
- Calling/WebRTC group calling.
