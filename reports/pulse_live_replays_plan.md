# Pulse Live Replays Plan

Current Live sessions surface in `/pulse/videos?tab=live`. Ended sessions with a Mux recording playback ID or durable replay URL surface under Replays and are indexed as `replay` when the host ends a session.

The viewer receives only playback URLs. Mux stream keys and provider credentials remain server-side. Future work should reconcile webhook-created recordings with the same replay index and add bounded replay retention controls.
