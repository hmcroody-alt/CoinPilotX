import { CreatePostResponse, PulseMedia, PulsePost, normalizePost } from "./feed";
import { StatusCreateResponse, PulseStatus, normalizeStatus } from "./status";
import { pulseApi } from "./pulseApi";

export type CameraTarget = "feed" | "post" | "status" | "reel" | "message" | "avatar" | "cover" | "creator" | "marketplace";
export type CameraMode = "photo" | "video" | "status" | "reel";

export type PulseCameraConfig = {
  enabled: boolean;
  provider: string;
  target: CameraTarget | string;
  mode: CameraMode | string;
  uploadEndpoint: string;
  configEndpoint: string;
  banuba?: {
    enabled?: boolean;
    token_present?: boolean;
    public_client_token?: string;
  };
  fallback?: {
    enabled?: boolean;
    type?: string;
    accept?: string;
  };
  targets?: string[];
  phase2?: Record<string, string>;
};

export type CameraConfigResponse = {
  ok?: boolean;
  camera?: Partial<PulseCameraConfig>;
  diagnostics?: Record<string, unknown>;
  message?: string;
};

export type CameraPreviewPayload = {
  destination: CameraTarget | string;
  media: Partial<PulseMedia> & { id?: number; media_id?: number };
  caption?: string;
  privacy?: string;
  effect_key?: string;
  beauty_key?: string;
};

export type CameraPreviewResponse = {
  ok?: boolean;
  preview?: Record<string, unknown>;
  preview_token?: string;
  token?: string;
  message?: string;
};

export type CameraPublishResult = {
  ok?: boolean;
  post?: PulsePost;
  status?: PulseStatus;
  post_id?: number;
  reel_id?: number;
  status_id?: number;
  next_url?: string;
  message?: string;
};

export async function getCameraConfig(params: { target?: CameraTarget | string; mode?: CameraMode | string } = {}) {
  const query = new URLSearchParams();
  if (params.target) query.set("target", params.target);
  if (params.mode) query.set("mode", params.mode);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const data = await pulseApi<CameraConfigResponse>(`/api/pulse/camera/config${suffix}`);
  return normalizeCameraConfig(data.camera || {}, params);
}

export async function createCameraPreview(payload: CameraPreviewPayload) {
  return pulseApi<CameraPreviewResponse>("/api/pulse/camera/preview", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function markCameraPreviewPublished(payload: { preview_token?: string; entity_type: string; entity_id?: number }) {
  if (!payload.preview_token) return { ok: true };
  return pulseApi<{ ok?: boolean; message?: string }>("/api/pulse/camera/preview/mark-published", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function createPostFromCamera(payload: {
  media_id?: number;
  media_url?: string;
  body?: string;
  title?: string;
  post_type?: string;
}) {
  const data = await pulseApi<CreatePostResponse>("/api/pulse/posts/create-from-camera", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  return {
    ...data,
    post_id: Number(data.post_id || data.post?.id || data.post?.post_id || 0),
    post: data.post ? normalizePost(data.post) : undefined
  };
}

export async function createReelFromCamera(payload: {
  media_id?: number;
  media_url?: string;
  thumbnail_url?: string;
  caption?: string;
  title?: string;
}) {
  return pulseApi<{ ok?: boolean; reel_id?: number; next_url?: string; message?: string }>("/api/pulse/reels/create-from-camera", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function normalizeCameraPublishResult(input: CameraPublishResult): CameraPublishResult {
  return {
    ...input,
    post: input.post ? normalizePost(input.post) : undefined,
    status: input.status ? normalizeStatus(input.status) : undefined,
    post_id: Number(input.post_id || input.post?.id || 0),
    status_id: Number(input.status_id || input.status?.id || 0),
    reel_id: Number(input.reel_id || 0)
  };
}

function normalizeCameraConfig(input: Partial<PulseCameraConfig>, params: { target?: string; mode?: string }): PulseCameraConfig {
  return {
    enabled: input.enabled !== false,
    provider: String(input.provider || "native_fallback"),
    target: input.target || params.target || "feed",
    mode: input.mode || params.mode || "photo",
    uploadEndpoint: input.uploadEndpoint || "/api/pulse/media/upload",
    configEndpoint: input.configEndpoint || "/api/pulse/camera/config",
    banuba: input.banuba || { enabled: false, token_present: false },
    fallback: input.fallback || { enabled: true, type: "device_file_picker" },
    targets: input.targets || ["status", "reel", "feed", "post", "message", "live"],
    phase2: input.phase2 || {}
  };
}
