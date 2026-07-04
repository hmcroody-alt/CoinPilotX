import AsyncStorage from "@react-native-async-storage/async-storage";
import { PULSE_API_BASE_URL } from "./config";
import { pulseApi } from "./pulseApi";

const FEED_CACHE_PREFIX = "pulsesoc.native.feed.";
const POST_CACHE_PREFIX = "pulsesoc.native.post.";

export type PulseAuthor = {
  id?: number;
  user_id?: number;
  username?: string;
  handle?: string;
  display_name?: string;
  name?: string;
  avatar_url?: string;
  profile_url?: string;
  public_player_id?: string;
  verified?: boolean;
  premium?: boolean;
  premium_verified?: boolean;
};

export type PulseMedia = {
  id?: number;
  type?: string;
  media_type?: string;
  url?: string;
  media_url?: string;
  thumbnail_url?: string;
  poster_url?: string;
  playback_url?: string;
  mux_playback_id?: string;
  mux_hls_url?: string;
  hls_url?: string;
  valid_url?: string;
  cdn_url?: string;
  playback_mime_type?: string;
  is_available?: boolean;
  has_audio?: boolean;
  attached_audio_url?: string;
  mime_type?: string;
  alt?: string;
  width?: number;
  height?: number;
  status?: string;
};

export type PulseComment = {
  id: number;
  comment_id: number;
  post_id?: number;
  user_id?: number;
  body: string;
  content?: string;
  text?: string;
  created_at?: string;
  author?: PulseAuthor;
  user?: PulseAuthor;
};

export type PulsePost = {
  id: number;
  post_id: number;
  title?: string;
  body: string;
  text?: string;
  content?: string;
  created_at?: string;
  updated_at?: string;
  author?: PulseAuthor;
  user?: PulseAuthor;
  author_name?: string;
  author_username?: string;
  author_avatar_url?: string;
  media?: PulseMedia[];
  media_assets?: PulseMedia[];
  attachments?: PulseMedia[];
  image_url?: string;
  thumbnail_url?: string;
  video_url?: string;
  reaction_counts?: Record<string, number>;
  reactions?: Record<string, number>;
  viewer_reaction?: string;
  my_reaction?: string;
  comment_count?: number;
  comments_count?: number;
  comments?: PulseComment[];
  preview_comments?: PulseComment[];
  saved?: boolean;
  is_saved?: boolean;
  reposted?: boolean;
  is_reposted?: boolean;
  repost_count?: number;
  share_count?: number;
  visibility?: string;
  moderation_status?: string;
};

export type FeedResponse = {
  ok?: boolean;
  posts?: PulsePost[];
  feed?: PulsePost[];
  next_offset?: number;
  has_more?: boolean;
  topic?: string;
  message?: string;
};

export type PostDetailResponse = {
  ok?: boolean;
  post?: PulsePost;
  comments?: PulseComment[];
};

export type FeedParams = {
  feed?: string;
  tab?: string;
  topic?: string;
  profile?: string;
  limit?: number;
  offset?: number;
};

export async function listFeed(params: FeedParams = {}) {
  const query = new URLSearchParams();
  query.set("feed", params.feed || params.tab || "for_you");
  query.set("tab", params.tab || params.feed || "for_you");
  query.set("limit", String(params.limit || 20));
  query.set("offset", String(params.offset || 0));
  if (params.topic) query.set("topic", params.topic);
  if (params.profile) query.set("profile", params.profile);

  const data = await pulseApi<FeedResponse>(`/api/pulse/feed?${query.toString()}`);
  const posts = normalizePosts(data.posts || data.feed || []);
  if (!params.offset) await cacheFeed(params.feed || params.tab || "for_you", posts);
  return {
    ...data,
    posts,
    next_offset: Number(data.next_offset ?? (params.offset || 0) + posts.length),
    has_more: Boolean(data.has_more)
  };
}

export async function loadCachedFeed(feed = "for_you") {
  const key = `${FEED_CACHE_PREFIX}${feed}`;
  try {
    const cached = await AsyncStorage.getItem(key);
    if (!cached) return [];
    return normalizePosts(JSON.parse(cached) as PulsePost[]);
  } catch {
    await AsyncStorage.removeItem(key).catch(() => undefined);
    return [];
  }
}

export async function cacheFeed(feed: string, posts: PulsePost[]) {
  await AsyncStorage.setItem(`${FEED_CACHE_PREFIX}${feed}`, JSON.stringify(posts.slice(0, 80)));
}

export async function getPostDetail(postId: number) {
  const data = await pulseApi<PostDetailResponse>(`/api/pulse/posts/${postId}`);
  const post = data.post ? normalizePost(data.post) : undefined;
  const comments = normalizeComments(data.comments || post?.comments || []);
  const detail = { ...data, post, comments };
  if (post) await cachePostDetail(postId, detail);
  return detail;
}

export async function loadCachedPostDetail(postId: number) {
  const key = `${POST_CACHE_PREFIX}${postId}`;
  try {
    const cached = await AsyncStorage.getItem(key);
    if (!cached) return null;
    const data = JSON.parse(cached) as PostDetailResponse;
    return {
      ...data,
      post: data.post ? normalizePost(data.post) : undefined,
      comments: normalizeComments(data.comments || [])
    };
  } catch {
    await AsyncStorage.removeItem(key).catch(() => undefined);
    return null;
  }
}

export async function cachePostDetail(postId: number, detail: PostDetailResponse) {
  await AsyncStorage.setItem(`${POST_CACHE_PREFIX}${postId}`, JSON.stringify(detail));
}

export async function reactToPost(postId: number, reactionType: string) {
  return pulseApi<{ ok?: boolean; post?: PulsePost; reaction_counts?: Record<string, number>; viewer_reaction?: string }>(
    `/api/pulse/posts/${postId}/react`,
    {
      method: "POST",
      body: JSON.stringify({ reaction_type: reactionType, type: reactionType })
    }
  );
}

export async function savePost(postId: number) {
  return pulseApi<{ ok?: boolean; saved?: boolean; is_saved?: boolean; post?: PulsePost }>(`/api/pulse/posts/${postId}/save`, {
    method: "POST",
    body: JSON.stringify({ post_id: postId })
  });
}

export async function repostPost(postId: number, body = "") {
  return pulseApi<{ ok?: boolean; reposted?: boolean; is_reposted?: boolean; post?: PulsePost }>(`/api/pulse/posts/${postId}/repost`, {
    method: "POST",
    body: JSON.stringify({ post_id: postId, body })
  });
}

export async function addPostComment(postId: number, body: string) {
  const data = await pulseApi<{ ok?: boolean; comment?: PulseComment; comments?: PulseComment[] }>(`/api/pulse/posts/${postId}/comments`, {
    method: "POST",
    body: JSON.stringify({ body, content: body, text: body })
  });
  return {
    ...data,
    comment: data.comment ? normalizeComment(data.comment, postId) : undefined,
    comments: normalizeComments(data.comments || [], postId)
  };
}

export async function listPostComments(postId: number) {
  const data = await pulseApi<{ ok?: boolean; comments?: PulseComment[]; items?: PulseComment[] }>(`/api/pulse/posts/${postId}/comments`);
  return normalizeComments(data.comments || data.items || [], postId);
}

export function pulsePostUrl(postId: number) {
  return `${PULSE_API_BASE_URL}/pulse/post/${postId}`;
}

export function normalizePosts(items: PulsePost[]) {
  return items.map(normalizePost).filter((post) => post.id > 0);
}

export function normalizePost(item: PulsePost): PulsePost {
  const id = Number(item.post_id || item.id || 0);
  const media = normalizeMedia(item.media || item.media_assets || item.attachments || [], item);
  const comments = normalizeComments(item.comments || item.preview_comments || [], id);
  return {
    ...item,
    id,
    post_id: id,
    body: String(item.body || item.text || item.content || ""),
    author: normalizeAuthor(item),
    media,
    comments,
    preview_comments: comments.slice(0, 2),
    reaction_counts: normalizeReactionCounts(item.reaction_counts || item.reactions || {}),
    viewer_reaction: item.viewer_reaction || item.my_reaction || "",
    comment_count: Number(item.comment_count ?? item.comments_count ?? comments.length),
    saved: Boolean(item.saved || item.is_saved),
    reposted: Boolean(item.reposted || item.is_reposted),
    repost_count: Number(item.repost_count || 0),
    share_count: Number(item.share_count || 0)
  };
}

export function normalizeComments(items: PulseComment[], fallbackPostId = 0) {
  return items
    .map((item) => normalizeComment(item, fallbackPostId))
    .filter((comment) => comment.id > 0 || comment.body.length > 0);
}

export function normalizeComment(item: PulseComment, fallbackPostId = 0): PulseComment {
  const id = Number(item.comment_id || item.id || 0);
  return {
    ...item,
    id,
    comment_id: id,
    post_id: Number(item.post_id || fallbackPostId || 0),
    body: String(item.body || item.content || item.text || ""),
    author: item.author || item.user || undefined
  };
}

export function mediaDisplayUrl(media: PulseMedia) {
  const url = media.media_url || media.url || media.playback_url || media.hls_url || media.thumbnail_url || media.poster_url || "";
  if (!url) return "";
  if (/^https?:\/\//i.test(url)) return url;
  if (url.startsWith("/")) return `${PULSE_API_BASE_URL}${url}`;
  return `${PULSE_API_BASE_URL}/${url}`;
}

export function mediaKind(media: PulseMedia) {
  const type = String(media.media_type || media.type || media.mime_type || "").toLowerCase();
  const url = mediaDisplayUrl(media).toLowerCase();
  if (type.includes("image") || /\.(jpg|jpeg|png|webp|gif)(\?|$)/.test(url)) return "image";
  if (type.includes("video") || /\.(mp4|mov|m3u8|webm)(\?|$)/.test(url)) return "video";
  return "file";
}

function normalizeAuthor(item: PulsePost): PulseAuthor {
  const author = item.author || item.user || {};
  return {
    ...author,
    display_name: author.display_name || author.name || item.author_name || item.author_username || "PulseSoc",
    username: author.username || author.handle || item.author_username || "",
    avatar_url: author.avatar_url || item.author_avatar_url || ""
  };
}

function normalizeMedia(items: PulseMedia[], item: PulsePost) {
  const normalized = [...items];
  if (item.image_url) normalized.push({ media_type: "image", media_url: item.image_url, thumbnail_url: item.thumbnail_url });
  if (item.video_url) normalized.push({ media_type: "video", media_url: item.video_url, thumbnail_url: item.thumbnail_url });
  return normalized.filter((media) => Boolean(mediaDisplayUrl(media)));
}

function normalizeReactionCounts(counts: Record<string, number>) {
  return Object.fromEntries(Object.entries(counts || {}).map(([key, value]) => [key, Number(value || 0)]));
}
