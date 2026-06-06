export type PulseAuthor = {
  public_player_id?: string | null;
  display_name?: string;
  avatar_url?: string;
  rank?: string;
  primary_label?: string;
  premium_mark?: string;
};

export type PulseMedia = {
  id?: number | string;
  type?: string;
  media_type?: string;
  media_url?: string;
  valid_url?: string;
  playback_url?: string;
  mux_hls_url?: string;
  mux_playback_id?: string;
  thumbnail_url?: string;
  poster_url?: string;
  width?: number;
  height?: number;
  aspect_ratio?: number;
  processing_status?: string;
  mux_status?: string;
};

export type PulsePost = {
  id: number;
  post_type: "text" | "image" | "video" | "gif" | "poll" | "replay" | "scam_report" | "arena_result" | "roast_clip" | "live" | "repost" | string;
  title?: string;
  body?: string;
  created_at?: string;
  updated_at?: string;
  author?: PulseAuthor;
  author_public_name?: string;
  author_avatar?: string;
  author_public_player_id?: string;
  media?: PulseMedia[];
  tags?: string[];
  repost?: {
    original_post_id?: number;
    caption?: string;
    original?: PulsePost;
  } | null;
  original_post?: PulsePost | null;
  reaction_counts?: Record<string, number>;
  reactions_count?: number;
  comment_count?: number;
  comments_count?: number;
  viewer_reaction?: string;
  can_delete?: boolean;
  permalink?: string;
};

export type PulseComment = {
  id: number;
  post_id: number;
  user_id?: number;
  parent_comment_id?: number | null;
  body?: string;
  created_at?: string;
  author?: PulseAuthor;
};

export type FeedResponse = {
  ok: boolean;
  feed: string;
  posts: PulsePost[];
  next_offset: number;
  has_more: boolean;
  message?: string;
};

export type UploadResult = {
  ok: boolean;
  media?: { id?: number; media_id?: number; media_type?: string; media_url?: string; playback_url?: string };
  media_id?: number;
  id?: number;
  media_url?: string;
  playback_url?: string;
  message?: string;
};
