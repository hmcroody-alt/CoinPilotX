import AsyncStorage from "@react-native-async-storage/async-storage";
import { absoluteApiUrl, PULSE_API_BASE_URL } from "./config";
import { pulseApi } from "./pulseApi";
import { profileTargetFromAuthor } from "./profileTarget";
import { CanonicalMediaRecord, hasRenderableImage, hasRenderableMediaUrl, mediaRecordForCache } from "../media/mediaContract";
import { buildCommentTree } from "../social/commentTree";
import { observeSavedState } from "../social/savedStore";

const FEED_CACHE_PREFIX = "pulsesoc.native.feed.";
const POST_CACHE_PREFIX = "pulsesoc.native.post.";

/**
 * Client-side page size for post comments. The server clamps to
 * `pulse_feed_engine.COMMENT_PAGE_LIMIT_MAX` (120), so asking for more is
 * silently reduced rather than rejected; 20 is chosen to make the first page
 * cheap and the "load more" affordance real instead of decorative.
 */
export const POST_COMMENT_PAGE_SIZE = 20;

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
  account_type?: "PERSON" | "PULSESOC_AUTOMATED" | string;
  automated?: boolean;
  official_system_account?: boolean;
};

export type PulseMedia = CanonicalMediaRecord;

/**
 * Post-level attached-music descriptor. Mirrors the reel/status music shapes so
 * a single audio policy (`resolvePostAudioPolicy`) can drive playback identically
 * across every surface. The backend hydrates this on video posts that had a track
 * attached at publish time (see feed-video hydration in bot.py).
 */
export type PulsePostMusic = {
  id?: number;
  track_id?: number | string;
  title?: string;
  artist?: string;
  audio_url?: string;
  attached_audio_url?: string;
  audio_start_time?: number;
  audio_volume?: number;
  /** Music is already digitally mixed into the uploaded MP4; retain attribution without double playback. */
  audio_baked_in?: boolean;
  original_audio_muted?: boolean;
};

export type PulseComment = {
  id: number;
  comment_id: number;
  post_id?: number;
  user_id?: number;
  parent_comment_id?: number;
  body: string;
  content?: string;
  text?: string;
  created_at?: string;
  author?: PulseAuthor;
  user?: PulseAuthor;
  can_edit?: boolean;
  can_delete?: boolean;
  can_moderate?: boolean;
  edited_at?: string;
  deleted_at?: string;
  moderation_status?: string;
  like_count?: number;
  reaction_counts?: Record<string, number>;
  viewer_reaction?: string;
  replies?: PulseComment[];
  reply_count?: number;
};

export type PulsePost = {
  id: number;
  post_id: number;
  user_id?: number;
  post_type?: string;
  content_type?: string;
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
  author_public_player_id?: string;
  author_avatar_url?: string;
  media?: PulseMedia[];
  media_assets?: PulseMedia[];
  attachments?: PulseMedia[];
  image_url?: string;
  thumbnail_url?: string;
  video_url?: string;
  music?: PulsePostMusic;
  music_track_id?: string | number;
  attached_audio_url?: string;
  original_audio_muted?: boolean;
  audio_start_time?: number;
  audio_volume?: number;
  audio_title?: string;
  audio_artist?: string;
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
  /**
   * Present when this row is a repost wrapping another post. Both spellings
   * come off the wire (`_public_post` emits `repost` and `original_post`), and
   * a card needs one of them to know which post a Save is actually about — the
   * wrapper and the original are the same content under two ids.
   */
  repost?: { original_post_id?: number; caption?: string; original?: PulsePost | null } | null;
  original_post?: PulsePost | null;
  share_count?: number;
  visibility?: string;
  moderation_status?: string;
  viewer_follows_author?: boolean;
  is_following_author?: boolean;
  live?: {
    live_session_id?: number;
    status?: string;
    playback_url?: string;
    preview_url?: string;
    replay_url?: string;
    viewer_count?: number;
    live_url?: string;
  };
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

export type CreatePostPayload = {
  body: string;
  title?: string;
  post_type?: "text" | "image" | "video" | "gif" | "poll" | "scam_report" | string;
  visibility?: "public" | "followers" | "private";
  media_ids?: number[];
  tags?: string[];
  music_track_id?: string;
  // Defense-in-depth: carry the resolved music metadata on the create payload so
  // playback works even when server-side attachment enrichment is bypassed. The
  // backend remains the source of truth for `music_track_id`; these are additive.
  attached_audio_url?: string;
  original_audio_muted?: boolean;
  audio_start_time?: number;
  audio_volume?: number;
  audio_baked_in?: boolean;
};

export type CreatePostResponse = {
  ok?: boolean;
  post_id?: number;
  post?: PulsePost;
  next_url?: string;
  message?: string;
};

export type PostReactionResponse = {
  ok?: boolean;
  removed?: boolean;
  reaction_type?: string;
  reaction_counts?: Record<string, number>;
  viewer_reaction?: string;
  post?: PulsePost;
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
  // Network truth, so the store may be corrected by it. Deliberately not done
  // in `loadCachedFeed`: a cache is the one payload guaranteed to be older than
  // whatever the store already holds.
  adoptFeedSavedStates(posts);
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
  await AsyncStorage.setItem(`${FEED_CACHE_PREFIX}${feed}`, JSON.stringify(posts.slice(0, 80).map(postForCache)));
}

export async function getPostDetail(postId: number) {
  const data = await pulseApi<PostDetailResponse>(`/api/pulse/posts/${postId}`);
  const post = data.post ? normalizePost(data.post) : undefined;
  if (post) adoptFeedSavedStates([post]);
  // bot.py:77002 returns a FLAT comment list carrying parent_comment_id, so the
  // nesting has to be derived here. Without this transform a reply renders as a
  // sibling of the comment it answers, which is what shipped.
  const comments = buildCommentTree(normalizeComments(data.comments || post?.comments || []));
  const detail = { ...data, post, comments };
  if (post) await cachePostDetail(postId, detail);
  return detail;
}

export async function createPost(payload: CreatePostPayload) {
  const data = await pulseApi<CreatePostResponse>("/api/pulse/posts", {
    method: "POST",
    body: JSON.stringify({
      body: payload.body || "",
      title: payload.title || "",
      post_type: payload.post_type || "text",
      visibility: payload.visibility || "public",
      media_ids: payload.media_ids || [],
      tags: payload.tags || [],
      music_track_id: payload.music_track_id || "",
      // Defense-in-depth music metadata, mirrored from createReel/createStatus so
      // the attached track survives even when server-side attachment is bypassed.
      attached_audio_url: payload.attached_audio_url || "",
      original_audio_muted:
        payload.original_audio_muted ?? Boolean(payload.music_track_id),
      audio_start_time: payload.audio_start_time ?? 0,
      audio_volume: payload.audio_volume ?? 1,
      audio_baked_in: Boolean(payload.audio_baked_in)
    })
  });
  const post = data.post ? normalizePost(data.post) : undefined;
  return { ...data, post, post_id: Number(data.post_id || post?.id || 0) };
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
  await AsyncStorage.setItem(`${POST_CACHE_PREFIX}${postId}`, JSON.stringify({ ...detail, post: detail.post ? postForCache(detail.post) : undefined }));
}

export async function reactToPost(postId: number, reactionType: string) {
  return pulseApi<PostReactionResponse>(
    `/api/pulse/posts/${postId}/react`,
    {
      method: "POST",
      body: JSON.stringify({ reaction_type: reactionType, type: reactionType })
    }
  );
}

// `savePost(postId)` used to live here: a bodyless toggle that asked the server
// to flip whatever it currently held. Removed rather than kept as an alias,
// because a toggle is unsafe to retry — a dropped response followed by a retry
// undoes the save — and because two ways to save a post is how the app ended up
// with screens that disagreed about whether one was saved. Everything now goes
// through `social/saveContract.setSavedOnServer`, which states the wanted state.

export type RepostResponse = {
  ok?: boolean;
  message?: string;
  reposted?: boolean;
  is_reposted?: boolean;
  repost_count?: number;
  post_id?: number;
  original_post_id?: number;
  removed?: boolean;
  post?: PulsePost;
};

/**
 * Repost or un-repost, with the server as the source of truth for both.
 *
 * `undo` maps to DELETE, which the route only gained once the engine could soft
 * delete. Before that this was create-only and returned neither `reposted` nor a
 * count, so the screens rendered a one-way button rather than a toggle that would
 * have claimed an un-repost the server never performed. Both fields come back on
 * every branch now, including the no-op branches — reposting something already
 * reposted, or undoing something already undone — so a caller can always
 * reconcile its optimistic state with the truth instead of guessing.
 */
export async function repostPost(postId: number, options: { body?: string; undo?: boolean } = {}) {
  const undo = Boolean(options.undo);
  return pulseApi<RepostResponse>(`/api/pulse/posts/${postId}/repost`, {
    method: undo ? "DELETE" : "POST",
    body: JSON.stringify({ post_id: postId, body: options.body || "", undo })
  });
}

export async function hidePost(postId: number, reason = "Hidden from Home") {
  return pulseApi<{ ok?: boolean; hidden?: boolean; message?: string }>(`/api/pulse/posts/${postId}/hide`, {
    method: "POST",
    body: JSON.stringify({ post_id: postId, reason })
  });
}

export async function deletePost(postId: number) {
  return pulseApi<{ ok?: boolean; message?: string; post_id?: number }>(`/api/pulse/posts/${postId}`, {
    method: "DELETE"
  });
}

export async function toggleFollowAuthor(post: PulsePost) {
  const author = post.author || post.user || {};
  const target = profileTargetFromAuthor(author as Record<string, unknown>, post as unknown as Record<string, unknown>);
  const publicPlayerId =
    target?.publicPlayerId ||
    target?.username ||
    (!target?.userId ? target?.profileKey : "") ||
    author.public_player_id ||
    post.author_public_player_id ||
    author.username ||
    post.author_username ||
    "";
  const followedUserId = Number(target?.userId || author.user_id || author.id || 0);
  const body: Record<string, string | number> = {
    public_player_id: publicPlayerId || "",
    followed_public_player_id: publicPlayerId || ""
  };
  if (followedUserId > 0) body.followed_user_id = followedUserId;
  return pulseApi<{ ok?: boolean; following?: boolean; message?: string }>("/api/pulse/follows/toggle", {
    method: "POST",
    body: JSON.stringify(body)
  });
}

export async function mutePostAuthor(post: PulsePost, reason = "Muted from Home") {
  const author = post.author || post.user || {};
  const target = profileTargetFromAuthor(author as Record<string, unknown>, post as unknown as Record<string, unknown>);
  const publicPlayerId =
    target?.publicPlayerId ||
    target?.username ||
    (!target?.userId ? target?.profileKey : "") ||
    author.public_player_id ||
    post.author_public_player_id ||
    author.username ||
    post.author_username ||
    "";
  const mutedUserId = Number(target?.userId || author.user_id || author.id || 0);
  const body: Record<string, string | number> = {
    public_player_id: publicPlayerId || "",
    muted_public_player_id: publicPlayerId || "",
    reason
  };
  if (mutedUserId > 0) {
    body.muted_user_id = mutedUserId;
    body.user_id = mutedUserId;
  }
  return pulseApi<{ ok?: boolean; muted?: boolean; muted_user_id?: number; message?: string }>("/api/pulse/users/mute", {
    method: "POST",
    body: JSON.stringify(body)
  });
}

/**
 * Post a comment, or a reply when `parentCommentId` is supplied.
 *
 * The backend has always accepted `parent_comment_id` here — bot.py:77495
 * forwards it straight to `pulse_feed_engine.add_comment`, and generates a
 * `comment_reply` notification instead of a `comment` notification when it is
 * present. The client simply never sent it, which is why feed posts had no
 * replies while Reels did. Sending 0 is equivalent to omitting it, so existing
 * two-argument callers keep their exact previous behavior.
 */
export async function addPostComment(postId: number, body: string, parentCommentId = 0) {
  const parentId = Number(parentCommentId || 0);
  const data = await pulseApi<{ ok?: boolean; comment?: PulseComment; comments?: PulseComment[] }>(`/api/pulse/posts/${postId}/comments`, {
    method: "POST",
    body: JSON.stringify({
      body,
      content: body,
      text: body,
      // Verified against both handlers: bot.py:77504 (posts) and bot.py:76506
      // (reels) each read exactly `parent_comment_id` and nothing else, so this
      // is the only key worth sending.
      parent_comment_id: parentId > 0 ? parentId : 0
    })
  });
  const comment = data.comment ? normalizeComment(data.comment, postId) : undefined;
  return {
    ...data,
    // A server that echoes the comment without the parent it was filed under
    // would make the reply render as a root. Preserve the parent the caller
    // asked for so the optimistic insert and the confirmed insert agree.
    comment: comment && parentId > 0 ? { ...comment, parent_comment_id: Number(comment.parent_comment_id || parentId) } : comment,
    comments: normalizeComments(data.comments || [], postId)
  };
}

export type PostCommentPage = {
  /** Nested for rendering. */
  comments: PulseComment[];
  /**
   * The same rows, still flat, exactly as the server ordered them.
   *
   * A paginating caller needs this. Page 2 can contain a reply whose parent was
   * on page 1, and merging two already-nested pages at the root level would
   * strand that reply as a top-level comment. Accumulating the flat rows and
   * re-running buildCommentTree over the whole accumulation re-parents it
   * correctly. It is also the honest offset counter: `offset` must advance by
   * rows consumed, not by roots rendered.
   */
  flat: PulseComment[];
  total: number;
  hasMore: boolean;
  limit: number;
  offset: number;
};

/**
 * Read one page of a post's comments, nested.
 *
 * `has_more` comes from the server rather than being inferred from page length,
 * because page length is wrong in exactly the case where the total is a multiple
 * of the limit: a full final page looks identical to a full middle page. The
 * server-side contract is proven by tests/test_pulse_comment_pagination.py.
 *
 * `total` is the unpaginated count of visible comments, so a caller can show
 * "12 of 340" on the first page instead of only after fetching all of them.
 */
export async function listPostComments(postId: number, options: { limit?: number; offset?: number } = {}): Promise<PostCommentPage> {
  const limit = Math.max(1, Number(options.limit || POST_COMMENT_PAGE_SIZE));
  const offset = Math.max(0, Number(options.offset || 0));
  const query = `?limit=${encodeURIComponent(String(limit))}&offset=${encodeURIComponent(String(offset))}`;
  const data = await pulseApi<{
    ok?: boolean;
    comments?: PulseComment[];
    items?: PulseComment[];
    total?: number;
    has_more?: boolean;
    limit?: number;
    offset?: number;
  }>(`/api/pulse/posts/${postId}/comments${query}`);
  const flat = normalizeComments(data.comments || data.items || [], postId);
  const resolvedLimit = Number(data.limit || limit);
  const resolvedOffset = Number(data.offset ?? offset);
  const total = Number(data.total ?? flat.length);
  return {
    comments: buildCommentTree(flat),
    flat,
    total,
    // Trust the server's has_more when it says so. Fall back to the arithmetic
    // only for an older server that omits the field entirely — `??` rather than
    // `||` so an explicit `false` is honored instead of being recomputed.
    hasMore: data.has_more ?? resolvedOffset + flat.length < total,
    limit: resolvedLimit,
    offset: resolvedOffset
  };
}

export function pulsePostUrl(postId: number) {
  return `${PULSE_API_BASE_URL}/pulse/post/${postId}`;
}

export function normalizePosts(items: PulsePost[]) {
  return items.map(normalizePost).filter((post) => post.id > 0);
}

/**
 * The id a Save applies to, which for a repost is the post being reposted.
 *
 * Saving a repost has to save the original, or the Saved collection fills with
 * wrappers that vanish when the reposter deletes their share, and the same
 * content shows Saved in one place and Save in another. The server already
 * resolves it this way (`savable_post_id` in services/pulse_feed_engine.py);
 * this is the client half of that agreement, in one place rather than
 * re-spelled inline in every card that renders a Save button.
 */
export function savablePostId(post: PulsePost): number {
  return Number(post.repost?.original_post_id || post.original_post?.id || post.id || 0);
}

/**
 * Teach the save store what a *fresh* fetch just said about these posts.
 *
 * A mounting card may only seed an item the store has never heard of — that is
 * what stops a stale list from reverting a save under the user. The cost of
 * that rule is that nothing would ever correct the store when the user unsaved
 * on the web and then pulled to refresh here. This is the correction: callers
 * that know their payload came from the network, and not from cache, hand it
 * over explicitly. `observeSavedState` still declines to overwrite a mutation
 * in flight, so a refresh racing a tap cannot undo the tap.
 */
export function adoptFeedSavedStates(posts: PulsePost[]) {
  posts.forEach((post) => {
    const id = savablePostId(post);
    if (id > 0) observeSavedState("post", id, Boolean(post.saved));
  });
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
    share_count: Number(item.share_count || 0),
    viewer_follows_author: Boolean(item.viewer_follows_author || item.is_following_author)
  };
}

export function normalizeComments(items: PulseComment[], fallbackPostId = 0) {
  return items
    .map((item) => normalizeComment(item, fallbackPostId))
    .filter((comment) => comment.id > 0 || comment.body.length > 0);
}

function commentAuthorWithLoadableAvatar(author: PulseAuthor | undefined) {
  if (!author) return undefined;
  const avatar = absoluteApiUrl(author.avatar_url);
  return avatar === author.avatar_url ? author : { ...author, avatar_url: avatar };
}

export function normalizeComment(item: PulseComment, fallbackPostId = 0): PulseComment {
  const id = Number(item.comment_id || item.id || 0);
  return {
    ...item,
    id,
    comment_id: id,
    post_id: Number(item.post_id || fallbackPostId || 0),
    body: String(item.body || item.content || item.text || ""),
    // Only the avatar is touched. A comment author is passed through otherwise
    // unreshaped -- running it through `normalizeAuthor` would invent display
    // names and ids that comment rendering does not expect -- but a relative
    // avatar path is unloadable here exactly as it is on a feed card.
    author: commentAuthorWithLoadableAvatar(item.author || item.user),
    replies: normalizeComments(item.replies || [], Number(item.post_id || fallbackPostId || 0)),
    reply_count: Number(item.reply_count ?? item.replies?.length ?? 0)
  };
}

export function mediaDisplayUrl(media: PulseMedia) {
  // The resolver must cover every field the renderability gate honors
  // (mediaContract.hasRenderableMediaUrl). A record renderable only via
  // `valid_url`/`cdn_url`/`mux_hls_url` used to slip through the gate but resolve
  // to "" here, mounting an <Image uri=""> that reserved its aspect box and never
  // fired onError -- the exact permanent blank rectangle the gate exists to kill.
  // Whitespace-only fields are skipped for the same reason: truthy to JS, blank
  // to a renderer.
  const candidates = [
    media.media_url, media.url, media.playback_url, media.hls_url,
    media.mux_hls_url, media.cdn_url, media.valid_url, media.thumbnail_url, media.poster_url
  ];
  const url = candidates.find((value) => typeof value === "string" && value.trim().length > 0)?.trim() || "";
  if (!url) return "";
  // Absolute URIs (http(s), plus local preview schemes: file:, content:, ph:,
  // asset:, data:, blob:) are returned as-is. Only server-relative paths get the
  // API base prefix. This lets pre-publish previews render local device media
  // through the exact same renderers as published content.
  if (/^[a-z][a-z0-9+.-]*:/i.test(url)) return url;
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

/**
 * Renderability for a feed post's media, kind-aware. Still images must clear the
 * strict `hasRenderableImage` gate (drawable URL, server-available, real
 * dimensions) so a failed/skipped Insight image -- which the serializer still
 * hands back with a populated `media_url` and zeroed dimensions -- never reserves
 * a media box. Video/live keep the URL gate: a clip legitimately renders from a
 * poster while its playback asset is still processing and carries no image dims.
 */
export function feedRenderableMedia(list: readonly PulseMedia[] | null | undefined): PulseMedia[] {
  return (list || []).filter((media) =>
    mediaKind(media) === "image" ? hasRenderableImage(media) : hasRenderableMediaUrl(media)
  );
}

function normalizeAuthor(item: PulsePost): PulseAuthor {
  const author = item.author || item.user || {};
  const authorUserId = Number(author.user_id || author.id || item.user_id || 0);
  return {
    ...author,
    id: authorUserId > 0 ? authorUserId : author.id,
    user_id: authorUserId > 0 ? authorUserId : author.user_id,
    display_name: author.display_name || author.name || item.author_name || item.author_username || "PulseSoc",
    username: author.username || author.handle || item.author_username || "",
    public_player_id: author.public_player_id || item.author_public_player_id || "",
    // Absolutised for the same reason `api/profile.ts` absolutises it: a
    // site-relative avatar path is unloadable by `<Image>` and renders as an
    // empty circle. This also covers payloads already cached on device from
    // before the server started sending absolute URLs, so an existing install
    // shows the account's picture immediately rather than after a refresh.
    avatar_url: absoluteApiUrl(author.avatar_url || item.author_avatar_url || "")
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

function postForCache(post: PulsePost): PulsePost {
  return { ...post, media: (post.media || []).map(mediaRecordForCache) };
}
