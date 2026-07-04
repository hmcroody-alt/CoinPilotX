import { useCallback, useRef, useState } from "react";
import {
  captureNativeMedia,
  NativeMediaAsset,
  NativeMediaUploadOptions,
  NativeMediaUploadResult,
  pickNativeImage,
  pickNativeVideo,
  pollNativeMediaProcessing,
  UploadController,
  UploadProgress,
  uploadNativeMedia,
  uploadResultMediaId,
  validateNativeMedia
} from "./nativeMediaUpload";

const idle: UploadProgress = { stage: "idle", percent: 0, message: "Media upload ready." };

export function useNativeMediaUpload(defaultOptions: NativeMediaUploadOptions) {
  const [asset, setAsset] = useState<NativeMediaAsset | null>(null);
  const [result, setResult] = useState<NativeMediaUploadResult | null>(null);
  const [progress, setProgress] = useState<UploadProgress>(idle);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const controllerRef = useRef<UploadController | null>(null);

  const chooseImage = useCallback(async () => {
    setError("");
    const picked = await pickNativeImage();
    setProgress(picked.progress);
    if (picked.asset) setAsset(picked.asset);
    return picked.asset;
  }, []);

  const chooseVideo = useCallback(async () => {
    setError("");
    const picked = await pickNativeVideo();
    setProgress(picked.progress);
    if (picked.asset) setAsset(picked.asset);
    return picked.asset;
  }, []);

  const openCamera = useCallback(async (kind: "image" | "video" = "image") => {
    setError("");
    const picked = await captureNativeMedia(kind);
    setProgress(picked.progress);
    if (picked.asset) setAsset(picked.asset);
    return picked.asset;
  }, []);

  const upload = useCallback(async (overrideOptions: Partial<NativeMediaUploadOptions> = {}) => {
    if (!asset) {
      setError("Choose media before uploading.");
      return null;
    }
    const validation = validateNativeMedia(asset);
    if (validation) {
      setError(validation);
      setProgress({ stage: "failed", percent: 0, message: validation });
      return null;
    }
    setError("");
    setUploading(true);
    const options = { ...defaultOptions, ...overrideOptions };
    const uploadTask = uploadNativeMedia(asset, options, setProgress);
    controllerRef.current = uploadTask.controller;
    try {
      const uploaded = await uploadTask.promise;
      const mediaId = uploadResultMediaId(uploaded);
      const finalResult = mediaId ? await pollNativeMediaProcessing(mediaId, 8, 1500, setProgress) : uploaded;
      setResult(finalResult || uploaded);
      setProgress({ stage: "ready", percent: 100, message: "Media uploaded." });
      return finalResult || uploaded;
    } catch (uploadError) {
      const message = uploadError instanceof Error ? uploadError.message : "Upload failed.";
      const cancelled = /cancel/i.test(message);
      setError(cancelled ? "" : message);
      setProgress({ stage: cancelled ? "cancelled" : "failed", percent: 0, message });
      return null;
    } finally {
      controllerRef.current = null;
      setUploading(false);
    }
  }, [asset, defaultOptions]);

  const retry = useCallback(() => upload(), [upload]);

  const cancel = useCallback(() => {
    controllerRef.current?.cancel();
    controllerRef.current = null;
    setUploading(false);
    setProgress({ stage: "cancelled", percent: 0, message: "Upload cancelled." });
  }, []);

  const reset = useCallback(() => {
    controllerRef.current?.cancel();
    controllerRef.current = null;
    setAsset(null);
    setResult(null);
    setError("");
    setUploading(false);
    setProgress(idle);
  }, []);

  return {
    asset,
    result,
    progress,
    error,
    uploading,
    chooseImage,
    chooseVideo,
    openCamera,
    upload,
    retry,
    cancel,
    reset,
    setAsset
  };
}
