import { normalizePost, PulseAuthor, PulseMedia, PulsePost } from "../api/feed";
import { normalizeReel, PulseReel, PulseReelAudio } from "../api/reels";
import { normalizeStatus, PulseStatus, PulseStatusMusic, StatusType } from "../api/status";
import { ComposerMusicTrack } from "../api/composerMusic";
import { NativeMediaAsset, NativeMediaUploadResult, uploadResultMediaId } from "../media/nativeMediaUpload";
import { GlobalNavigationIdentity } from "../navigation/GlobalNavigation";

/**
 * Draft-to-canonical content normalization layer.
 *
 * This is the linchpin of the True-to-Publish preview: it converts the
 * composer's in-flight draft state into the SAME canonical content models
 * (`PulsePost` / `PulseReel` / `PulseStatus`) that the production feed
 * produces after a successful publish, by routing everything through the
 * exact `normalizePost` / `normalizeReel` / `normalizeStatus` functions the
 * live feed uses. Because the preview renders these canonical objects through
 * the production renderers, "if the preview looks correct, the published
 * result looks the same" holds by construction — the only intended
 * differences are (a) media points at local device URIs until upload and
 * (b) interaction is disabled via the `preview` flag on the renderer.
 *
 * This module is intentionally PURE (no React, no async, no I/O) so it can be
 * unit-tested deterministically.
 */

export type ComposerDraftMode = "post" | "status" | "reel" | "poll" | "scam_report";
export type ComposerDraftVisibility = "public" | "followers" | "private";

export type ComposerDraftMediaItem = {
  asset: NativeMediaAsset;
  result: NativeMediaUploadResult | null;
};

export type ComposerDraftInput = {
  mode: ComposerDraftMode;
  body: string;
  visibility: ComposerDraftVisibility;
  topic?: string;
  musicTrack?: ComposerMusicTrack | null;
  media: ComposerDraftMediaItem[];
  identity?: GlobalNavigationIdentity;
  /** Deterministic clock for tests; defaults to Date.now(). */
  now?: number;
};

export type PreviewContent =
  | { kind: "post"; post: PulsePost }
  | { kind: "reel"; reel: PulseReel }
  | { kind: "status"; status: PulseStatus };

const PREVIEW_ID = -1;

function draftAuthor(identity?: GlobalNavigationIdentity): PulseAuthor {
  return {
    id: 0,
    user_id: 0,
    display_name: identity?.displayName || "You",
    name: identity?.displayName || "You",
    username: identity?.username || "",
    handle: identity?.username || "",
    avatar_url: identity?.avatarUrl || "",
    verified: Boolean(identity?.verified),
    premium: Boolean(identity?.premium)
  };
}

/**
 * Build a canonical media record for preview. Prefers a completed upload
 * result (real server URL + processing status) so the preview matches the
 * published pipeline exactly; falls back to the local device URI so the user
 * can preview before upload completes.
 */
function draftMediaRecord(item: ComposerDraftMediaItem): PulseMedia {
  const uploaded = item.result?.media;
  const uploadedId = uploadResultMediaId(item.result || {});
  const isVideo = item.asset.mediaType === "video";
  if (uploaded && (uploadedId || uploaded.media_url || uploaded.playback_url)) {
    return {
      ...uploaded,
      media_id: uploadedId || uploaded.media_id,
      id: uploadedId || uploaded.id,
      media_type: uploaded.media_type || item.asset.mediaType,
      // A processing upload should still preview from the local file.
      media_url: uploaded.media_url || uploaded.playback_url || item.asset.uri,
      poster_url: uploaded.poster_url || uploaded.thumbnail_url || (isVideo ? undefined : item.asset.uri),
      width: uploaded.width || item.asset.width,
      height: uploaded.height || item.asset.height,
      has_audio: isVideo ? uploaded.has_audio ?? true : uploaded.has_audio
    };
  }
  return {
    media_type: item.asset.mediaType,
    media_url: item.asset.uri,
    // For images, the file itself is a valid poster; local videos have no
    // separate poster frame so the renderer's <Video> shows the first frame.
    poster_url: isVideo ? undefined : item.asset.uri,
    width: item.asset.width,
    height: item.asset.height,
    duration_ms: typeof item.asset.duration === "number" ? Math.round(item.asset.duration * 1000) : undefined,
    file_size: item.asset.size,
    has_audio: isVideo ? true : undefined,
    // Local drafts are, by definition, ready to display.
    processing_status: "ready"
  };
}

function reelAudioFromTrack(track: ComposerMusicTrack): PulseReelAudio {
  return {
    track_id: Number(track.id) || undefined,
    title: track.title,
    artist: track.artist,
    attached_audio_url: track.previewUrl,
    preview_url: track.previewUrl,
    audio_start_time: 0,
    audio_volume: 1,
    // Attaching approved music mutes the clip's original audio (matches the
    // production ATTACHED_MUSIC_AUDIO_PRIORITY policy). No fallback.
    original_audio_muted: true
  };
}

function statusMusicFromTrack(track: ComposerMusicTrack): PulseStatusMusic {
  return {
    track_id: track.id,
    music_id: track.id,
    title: track.title,
    artist: track.artist,
    audio_title: track.title,
    audio_artist: track.artist,
    attached_audio_url: track.previewUrl,
    preview_url: track.previewUrl,
    duration_seconds: track.durationSeconds
  };
}

/**
 * Convert composer draft state into a canonical, renderer-ready content model.
 * Returns `null` when the draft has nothing publishable (no body, no media,
 * no attached music) so callers can gate the preview/publish action.
 */
export function draftToContentModel(input: ComposerDraftInput): PreviewContent | null {
  const body = (input.body || "").trim();
  const hasMedia = input.media.length > 0;
  const hasMusic = Boolean(input.musicTrack);
  if (!body && !hasMedia && !hasMusic) return null;

  const author = draftAuthor(input.identity);
  const media = input.media.map(draftMediaRecord);
  const createdAt = new Date(input.now ?? Date.now()).toISOString();
  const track = input.musicTrack || null;

  if (input.mode === "reel") {
    const reel = normalizeReel({
      id: PREVIEW_ID,
      reel_id: PREVIEW_ID,
      caption: input.body,
      body: input.body,
      author,
      media,
      audio: track ? reelAudioFromTrack(track) : undefined,
      visibility_state: input.visibility,
      processing_status: "ready",
      created_at: createdAt,
      reaction_counts: {},
      reactions_count: 0,
      comments_count: 0,
      view_count: 0
    } as PulseReel);
    return { kind: "reel", reel };
  }

  if (input.mode === "status") {
    const hasVideo = input.media.some((item) => item.asset.mediaType === "video");
    const statusType: StatusType = hasVideo ? "video" : hasMedia ? "photo" : hasMusic ? "music" : "text";
    const status = normalizeStatus({
      id: PREVIEW_ID,
      status_id: PREVIEW_ID,
      user_id: 0,
      author,
      status_type: statusType,
      body: input.body,
      visibility: input.visibility,
      media,
      music: track ? statusMusicFromTrack(track) : undefined,
      created_at: createdAt,
      view_count: 0,
      reaction_count: 0,
      reply_count: 0,
      share_count: 0
    } as PulseStatus);
    return { kind: "status", status };
  }

  // Feed post (also covers poll / scam_report — these publish as posts).
  const hasVideo = input.media.some((item) => item.asset.mediaType === "video");
  const postType = hasVideo ? "video" : hasMedia ? "image" : input.mode === "poll" ? "poll" : input.mode === "scam_report" ? "scam_report" : "text";
  const post = normalizePost({
    id: PREVIEW_ID,
    post_id: PREVIEW_ID,
    body: input.body,
    post_type: postType,
    visibility: input.visibility,
    author,
    media,
    music_track_id: track?.id,
    tags: [input.topic].filter(Boolean) as string[],
    created_at: createdAt,
    reaction_counts: {},
    comment_count: 0
  } as PulsePost);
  return { kind: "post", post };
}
