# Pulse Repost Media Fix

## Root Cause

Repost rows stored `repost_of_post_id`, but feed hydration only loaded media attached directly to the repost row. Reel reposts therefore showed the repost text/title while the original Reel video stayed attached to the original post and never rendered in the feed card.

## Fix

- Feed hydration now resolves original posts for repost rows.
- Repost payloads expose `repost.original` and `original_post`.
- If the repost row has no direct media, the feed card uses the original post media.
- Original title, description, tags, creator, engagement data, and media metadata are preserved in the original payload.
- Reposts continue to reference original content instead of copying media files.
- Repost owner and original creator attribution now use Pulse public handles instead of raw user IDs.

## Safety

- Deleting a repost removes only the repost row.
- Original Reel media remains attached to the original Reel/post.
- Existing repost notifications still fire through `pulse_reel_reposted` and `post_reposted`.
- Existing `repost_of_post_id` data remains compatible.
