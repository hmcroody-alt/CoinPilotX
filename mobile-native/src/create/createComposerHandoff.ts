import AsyncStorage from "@react-native-async-storage/async-storage";
import { NativeMediaAsset } from "../media/nativeMediaUpload";

export type CreateComposerMode = "post" | "status" | "reel";
export type CreateCaptureMode = "photo" | "video";

export type CreateCameraCaptureResult = {
  id: string;
  asset: NativeMediaAsset;
  composerMode: CreateComposerMode;
  captureMode: CreateCaptureMode;
  source: "native_camera";
  savedAt: string;
};

const CREATE_CAMERA_CAPTURE_RESULT_KEY = "pulsesoc.native.create.camera.capture-result.v1";

export async function saveCreateCameraCaptureResult(result: Omit<CreateCameraCaptureResult, "id" | "savedAt" | "source"> & Partial<Pick<CreateCameraCaptureResult, "id" | "savedAt" | "source">>) {
  const payload: CreateCameraCaptureResult = {
    id: result.id || `create-camera-${Date.now()}`,
    asset: result.asset,
    composerMode: normalizeComposerMode(result.composerMode),
    captureMode: result.captureMode === "video" ? "video" : "photo",
    source: "native_camera",
    savedAt: result.savedAt || new Date().toISOString()
  };
  await AsyncStorage.setItem(CREATE_CAMERA_CAPTURE_RESULT_KEY, JSON.stringify(payload));
  return payload;
}

export async function consumeCreateCameraCaptureResult() {
  try {
    const raw = await AsyncStorage.getItem(CREATE_CAMERA_CAPTURE_RESULT_KEY);
    if (!raw) return null;
    await AsyncStorage.removeItem(CREATE_CAMERA_CAPTURE_RESULT_KEY).catch(() => undefined);
    const parsed = JSON.parse(raw) as CreateCameraCaptureResult;
    if (!parsed?.asset?.uri) return null;
    return {
      ...parsed,
      composerMode: normalizeComposerMode(parsed.composerMode),
      captureMode: parsed.captureMode === "video" ? "video" : "photo",
      source: "native_camera" as const
    };
  } catch {
    await AsyncStorage.removeItem(CREATE_CAMERA_CAPTURE_RESULT_KEY).catch(() => undefined);
    return null;
  }
}

export function createComposerModeFromCameraTarget(target?: string, mode?: string): CreateComposerMode {
  const rawTarget = String(target || "").toLowerCase();
  const rawMode = String(mode || "").toLowerCase();
  if (rawTarget === "status" || rawMode === "status") return "status";
  if (rawTarget === "reel" || rawMode === "reel") return "reel";
  return "post";
}

function normalizeComposerMode(value?: string): CreateComposerMode {
  if (value === "status" || value === "reel") return value;
  return "post";
}
