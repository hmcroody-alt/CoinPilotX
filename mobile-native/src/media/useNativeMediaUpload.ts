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
  const [asset, setAssetState] = useState<NativeMediaAsset | null>(null);
  const [result, setResult] = useState<NativeMediaUploadResult | null>(null);
  const [progress, setProgress] = useState<UploadProgress>(idle);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const controllerRef = useRef<UploadController | null>(null);

  const setAsset = useCallback((nextAsset: NativeMediaAsset | null) => {
    controllerRef.current?.cancel();
    controllerRef.current = null;
    setAssetState(nextAsset);
    setResult(null);
    setError("");
    setUploading(false);
    setProgress(nextAsset
      ? { stage: "selected", percent: 0, message: `${nextAsset.mediaType === "video" ? "Video" : "Image"} selected.` }
      : idle);
  }, []);

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

  const upload = useCallback(async (
    overrideOptions: Partial<NativeMediaUploadOptions> = {},
    overrideAsset: NativeMediaAsset | null = asset
  ) => {
    if (!overrideAsset) {
      setError("Choose media before uploading.");
      return null;
    }
    const validation = validateNativeMedia(overrideAsset);
    if (validation) {
      setError(validation);
      setProgress({ stage: "failed", percent: 0, message: validation });
      return null;
    }
    setError("");
    setUploading(true);
    const options = { ...defaultOptions, ...overrideOptions };
    const uploadTask = uploadNativeMedia(overrideAsset, options, setProgress);
    controllerRef.current = uploadTask.controller;
    try {
      const uploaded = await uploadTask.promise;
      const mediaId = uploadResultMediaId(uploaded);
      const finalResult = mediaId && !options.skipProcessingPoll
        ? await pollNativeMediaProcessing(mediaId, 8, 1500, setProgress)
        : uploaded;
      const result = finalResult || uploaded;
      const media = result.media || {};
      const processingStatus = String(result.processing_status || media.processing_status || "").toLowerCase();
      const muxStatus = String(result.mux_status || media.mux_status || "").toLowerCase();
      const ready = processingStatus === "ready" || ["ready", "asset_ready", "available"].includes(muxStatus);
      setResult(result);
      setProgress(ready
        ? { stage: "ready", percent: 100, message: "Media is ready to publish." }
        : { stage: "processing", percent: 99, message: "Upload complete. Video processing continues." });
      return result;
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
    setAssetState(null);
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
