import * as FileSystem from "expo-file-system";
import * as ImagePicker from "expo-image-picker";
import { PULSE_API_BASE_URL } from "../api/config";
import { PulseMedia, mediaDisplayUrl, mediaKind } from "../api/feed";
import { pulseApi } from "../api/pulseApi";
import { getSessionCookie, setSessionCookie } from "../session/sessionStore";
import { formatFileSize } from "../utils/format";

export type NativeMediaContext =
  | "pulse_status"
  | "pulse_post"
  | "pulse_avatar"
  | "pulse_cover"
  | "pulse_message"
  | "marketplace_product"
  | "creator_studio"
  | "pulse_camera";

export type NativeMediaAsset = {
  uri: string;
  name: string;
  mimeType: string;
  mediaType: "image" | "video";
  size?: number;
  width?: number;
  height?: number;
  duration?: number;
};

export type NativeMediaUploadOptions = {
  contextType: NativeMediaContext | string;
  contextId?: string;
  target?: string;
  mode?: string;
  filterName?: string;
  effectKey?: string;
  compressionPolicy?: string;
  destination?: string;
};

export type NativeMediaUploadResult = {
  ok?: boolean;
  success?: boolean;
  media?: PulseMedia & {
    id?: number;
    media_id?: number;
    processing_status?: string;
    mux_status?: string;
    trace_id?: string;
  };
  media_id?: number;
  media_url?: string;
  processing_status?: string;
  mux_status?: string;
  playback_url?: string;
  trace_id?: string;
  message?: string;
};

export type UploadProgress = {
  stage: "idle" | "validating" | "permission_denied" | "selected" | "uploading" | "processing" | "ready" | "failed" | "cancelled";
  percent: number;
  message: string;
};

export type UploadController = {
  cancel: () => void;
};

// These defaults mirror the authoritative media_service.py limits. The server
// remains authoritative when an environment-specific limit is lower.
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_GIF_BYTES = 8 * 1024 * 1024;
const MAX_VIDEO_BYTES = 150 * 1024 * 1024;
const IMAGE_EXTENSIONS = new Set(["jpg", "jpeg", "png", "webp", "gif"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "mov", "webm"]);

export type CameraCompressionPolicy = {
  key: string;
  imageQuality: number;
  videoQuality: "480p" | "720p" | "1080p";
  maxVideoDurationSeconds: number;
  maxVideoBytes: number;
  serverAuthoritative: true;
  deviceVerified: false;
  note: string;
};

export const mediaUploadIntegrationTargets = [
  "Status creator",
  "Feed composer",
  "Profile avatar/cover",
  "Messenger attachments",
  "Marketplace",
  "Creator Studio"
];

export async function pickNativeImage() {
  const picked = await pickNativeImages(1);
  return { asset: picked.assets[0] || null, progress: picked.progress };
}

export async function pickNativeImages(limit = 4) {
  const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!permission.granted) return { assets: [], progress: deniedProgress("Photo library permission is required. Open Settings to allow photo access.") };
  const result = await ImagePicker.launchImageLibraryAsync({
    allowsEditing: false,
    allowsMultipleSelection: limit > 1,
    mediaTypes: ImagePicker.MediaTypeOptions.Images,
    orderedSelection: limit > 1,
    selectionLimit: Math.max(1, Math.min(4, limit)),
    quality: 0.92
  });
  if (result.canceled || !result.assets?.length) return { assets: [], progress: idleProgress("Image selection cancelled.") };
  const assets = await Promise.all(result.assets.slice(0, limit).map((asset) => normalizePickedAsset(asset, "image")));
  return {
    assets,
    progress: { stage: "selected" as const, percent: 0, message: `${assets.length} image${assets.length === 1 ? "" : "s"} selected.` }
  };
}

export async function pickNativeVideo() {
  const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!permission.granted) return { asset: null, progress: deniedProgress("Photo library permission is required.") };
  const result = await ImagePicker.launchImageLibraryAsync({
    allowsEditing: false,
    mediaTypes: ImagePicker.MediaTypeOptions.Videos,
    quality: 1,
    videoMaxDuration: 180
  });
  if (result.canceled || !result.assets?.[0]) return { asset: null, progress: idleProgress("Video selection cancelled.") };
  const asset = await normalizePickedAsset(result.assets[0], "video");
  return { asset, progress: selectedProgress(asset) };
}

export async function captureNativeMedia(kind: "image" | "video" = "image") {
  const permission = await ImagePicker.requestCameraPermissionsAsync();
  if (!permission.granted) return { asset: null, progress: deniedProgress("Camera permission is required.") };
  const policy = cameraCompressionPolicy(kind === "video" ? "video" : "photo");
  const result = await ImagePicker.launchCameraAsync({
    allowsEditing: false,
    mediaTypes: kind === "video" ? ImagePicker.MediaTypeOptions.Videos : ImagePicker.MediaTypeOptions.Images,
    quality: kind === "image" ? policy.imageQuality : 1,
    videoMaxDuration: policy.maxVideoDurationSeconds
  });
  if (result.canceled || !result.assets?.[0]) return { asset: null, progress: idleProgress("Camera capture cancelled.") };
  const asset = await normalizePickedAsset(result.assets[0], kind);
  return { asset, progress: selectedProgress(asset) };
}

export async function nativeMediaAssetFromUri(
  uri: string,
  mediaType: "image" | "video",
  metadata: Partial<Pick<NativeMediaAsset, "name" | "mimeType" | "width" | "height" | "duration" | "size">> = {}
): Promise<NativeMediaAsset> {
  const fileInfo = await FileSystem.getInfoAsync(uri).catch(() => ({ exists: false } as FileSystem.FileInfo));
  const name = metadata.name || filenameFor(uri, mediaType);
  return {
    uri,
    name,
    mimeType: metadata.mimeType || mimeTypeFor(name, mediaType),
    mediaType,
    size: Number(metadata.size || ("size" in fileInfo ? fileInfo.size || 0 : 0)),
    width: metadata.width,
    height: metadata.height,
    duration: metadata.duration
  };
}

export function cameraCompressionPolicy(mode: "photo" | "video" | "status" | "reel" = "photo", destination = "feed"): CameraCompressionPolicy {
  const video = mode === "video" || mode === "reel";
  const status = destination === "status" || mode === "status";
  return {
    key: video ? (status ? "native_status_video_v1" : "native_video_v1") : "native_photo_v1",
    imageQuality: status ? 0.86 : 0.9,
    videoQuality: status ? "720p" : "1080p",
    maxVideoDurationSeconds: status ? 60 : 180,
    maxVideoBytes: video ? Math.min(MAX_VIDEO_BYTES, status ? 350 * 1024 * 1024 : 700 * 1024 * 1024) : 0,
    serverAuthoritative: true,
    deviceVerified: false,
    note: "Native client requests efficient capture settings; PulseSoc backend validation, storage, moderation, and processing remain authoritative."
  };
}

export function validateNativeMedia(asset: NativeMediaAsset) {
  const ext = extensionFor(asset.name || asset.uri);
  if (asset.mediaType === "image") {
    if (ext && !IMAGE_EXTENSIONS.has(ext)) return "Choose a JPG, PNG, WEBP, or GIF image. HEIC is not accepted by the production upload service yet.";
    const limit = ext === "gif" ? MAX_GIF_BYTES : MAX_IMAGE_BYTES;
    if (asset.size && asset.size > limit) return `Image is too large. Choose ${ext === "gif" ? "a GIF under 8 MB" : "an image under 5 MB"}.`;
  } else {
    if (ext && !VIDEO_EXTENSIONS.has(ext)) return "Choose an MP4, MOV, or WEBM video.";
    if (asset.size && asset.size > MAX_VIDEO_BYTES) return "Video is too large. Choose a video under 150 MB.";
  }
  if (!asset.uri) return "Media file is missing.";
  return "";
}

export function uploadNativeMedia(
  asset: NativeMediaAsset,
  options: NativeMediaUploadOptions,
  onProgress?: (progress: UploadProgress) => void
): { promise: Promise<NativeMediaUploadResult>; controller: UploadController } {
  const xhr = new XMLHttpRequest();
  const controller = {
    cancel: () => xhr.abort()
  };
  const promise = new Promise<NativeMediaUploadResult>((resolve, reject) => {
    const validation = validateNativeMedia(asset);
    if (validation) {
      reject(new Error(validation));
      return;
    }
    onProgress?.({ stage: "uploading", percent: 2, message: "Starting upload." });
    xhr.open("POST", `${PULSE_API_BASE_URL}/api/pulse/media/upload`);
    getSessionCookie()
      .then((cookie) => {
        if (cookie) xhr.setRequestHeader("Cookie", cookie);
      })
      .finally(() => {
        xhr.upload.onprogress = (event) => {
          if (!event.lengthComputable) {
            onProgress?.({ stage: "uploading", percent: 12, message: "Uploading media." });
            return;
          }
          const percent = Math.max(4, Math.min(92, Math.round((event.loaded / event.total) * 92)));
          onProgress?.({
            stage: "uploading",
            percent,
            message: `Uploading media ${Math.round((event.loaded / event.total) * 100)}% (${formatFileSize(event.loaded)} of ${formatFileSize(event.total)}).`
          });
        };
        xhr.onerror = () => reject(new Error("Upload failed. Check your connection and retry."));
        xhr.onabort = () => reject(new Error("Upload cancelled."));
        xhr.onload = () => {
          const responseCookie = xhr.getResponseHeader("set-cookie");
          if (responseCookie) setSessionCookie(responseCookie).catch(() => undefined);
          const data = parseUploadResponse(xhr.responseText);
          if (xhr.status < 200 || xhr.status >= 300 || data.ok === false) {
            reject(new Error(data.message || "Upload failed."));
            return;
          }
          onProgress?.({ stage: "processing", percent: 96, message: "Checking processing status." });
          resolve(data);
        };
        const form = new FormData();
        form.append("context_type", options.contextType);
        form.append("context_id", options.contextId || "native-draft");
        if (options.target) form.append("target", options.target);
        if (options.mode) form.append("mode", options.mode);
        if (options.filterName) form.append("filter_name", options.filterName);
        if (options.effectKey) form.append("effect_key", options.effectKey);
        if (options.compressionPolicy) form.append("compression_policy", options.compressionPolicy);
        if (options.destination) form.append("destination", options.destination);
        form.append("file", {
          uri: asset.uri,
          name: asset.name,
          type: asset.mimeType
        } as unknown as Blob);
        xhr.send(form);
      });
  });
  return { promise, controller };
}

export async function pollNativeMediaProcessing(mediaId: number, attempts = 8, delayMs = 1500, onProgress?: (progress: UploadProgress) => void) {
  let latest: NativeMediaUploadResult | null = null;
  for (let index = 0; index < attempts; index += 1) {
    latest = await pulseApi<NativeMediaUploadResult>(`/api/pulse/media/${mediaId}/status`);
    const media = latest.media || {};
    const status = String(latest.processing_status || media.processing_status || "").toLowerCase();
    const muxStatus = String(latest.mux_status || media.mux_status || "").toLowerCase();
    if (status === "ready" || ["ready", "asset_ready", "available"].includes(muxStatus)) {
      onProgress?.({ stage: "ready", percent: 100, message: "Media is ready." });
      return latest;
    }
    if (["failed", "error", "errored", "mux_failed", "processing_blocked"].includes(status) || ["failed", "error", "errored"].includes(muxStatus)) {
      throw new Error("Media processing failed.");
    }
    onProgress?.({ stage: "processing", percent: Math.min(99, 96 + index), message: "Media is processing." });
    await sleep(delayMs);
  }
  return latest || { ok: true, media_id: mediaId, processing_status: "processing" };
}

export function uploadResultMediaId(result: NativeMediaUploadResult) {
  return Number(result.media_id || result.media?.id || result.media?.media_id || 0);
}

export function nativeMediaPreviewUrl(asset?: NativeMediaAsset | null, media?: PulseMedia | null) {
  if (asset?.uri) return asset.uri;
  if (media) return mediaDisplayUrl(media);
  return "";
}

export function nativeMediaKind(asset?: NativeMediaAsset | null, media?: PulseMedia | null) {
  if (asset?.mediaType) return asset.mediaType;
  if (media) return mediaKind(media);
  return "file";
}

async function normalizePickedAsset(asset: ImagePicker.ImagePickerAsset, fallbackType: "image" | "video"): Promise<NativeMediaAsset> {
  const uri = asset.uri;
  const fileInfo = await FileSystem.getInfoAsync(uri).catch(() => ({ exists: false } as FileSystem.FileInfo));
  const mediaType = asset.type === "video" || fallbackType === "video" ? "video" : "image";
  const name = filenameFor(uri, mediaType);
  return {
    uri,
    name,
    mimeType: asset.mimeType || mimeTypeFor(name, mediaType),
    mediaType,
    size: "size" in fileInfo ? Number(fileInfo.size || asset.fileSize || 0) : Number(asset.fileSize || 0),
    width: asset.width,
    height: asset.height,
    duration: asset.duration ?? undefined
  };
}

function filenameFor(uri: string, mediaType: "image" | "video") {
  const raw = decodeURIComponent(uri.split("?")[0]?.split("/").pop() || "");
  if (raw && raw.includes(".")) return raw;
  return `pulsesoc-native-${Date.now()}.${mediaType === "video" ? "mp4" : "jpg"}`;
}

function mimeTypeFor(name: string, mediaType: "image" | "video") {
  const ext = extensionFor(name);
  if (mediaType === "video") {
    if (ext === "mov") return "video/quicktime";
    if (ext === "webm") return "video/webm";
    return "video/mp4";
  }
  if (ext === "png") return "image/png";
  if (ext === "webp") return "image/webp";
  if (ext === "gif") return "image/gif";
  return "image/jpeg";
}

function extensionFor(name: string) {
  return String(name || "").split("?")[0]?.split(".").pop()?.toLowerCase() || "";
}

function parseUploadResponse(text: string): NativeMediaUploadResult {
  try {
    return JSON.parse(text || "{}") as NativeMediaUploadResult;
  } catch {
    return { ok: false, message: "PulseSoc returned a non-JSON upload response." };
  }
}

function idleProgress(message: string): UploadProgress {
  return { stage: "idle", percent: 0, message };
}

function deniedProgress(message: string): UploadProgress {
  return { stage: "permission_denied", percent: 0, message };
}

function selectedProgress(asset: NativeMediaAsset): UploadProgress {
  return { stage: "selected", percent: 0, message: `${asset.mediaType === "video" ? "Video" : "Image"} selected.` };
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
