-- PulseSoc Reels instant-load indexes.
-- PostgreSQL-compatible and safe to rerun.

CREATE INDEX IF NOT EXISTS idx_pulse_posts_reels_feed
    ON pulse_posts (post_type, visibility, moderation_status, status, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_pulse_reels_post_status
    ON pulse_reels (post_id, status);

CREATE INDEX IF NOT EXISTS idx_pulse_reels_status_score_created
    ON pulse_reels (status, reel_score DESC, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_pulse_reels_user_created
    ON pulse_reels (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pulse_comments_post_visible_created
    ON pulse_comments (post_id, deleted_at, moderation_status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_chat_media_uploads_context_created
    ON chat_media_uploads (context_type, context_id, created_at DESC);
