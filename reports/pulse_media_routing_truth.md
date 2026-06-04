# Pulse Media Routing Truth

The previous Reel create route created a normal public `pulse_posts` row, which caused Reels to leak into the regular feed. Reels now use a `reel_only` compatibility visibility unless an explicit future `share_to_feed` request is provided.

Regular posts continue through `/api/pulse/posts`. Only posts containing video media are indexed into `pulse_videos`; photos are intentionally excluded. Status creation remains on its own status tables and does not enter the video index.

No existing media or production rows are deleted by this change.
