import { useCallback, useMemo, useRef, useState } from "react";
import {
  NativeMediaAsset,
  NativeMediaUploadOptions,
  NativeMediaUploadResult,
  pickNativeImages,
  pickNativeVideo,
  pollNativeMediaProcessing,
  UploadController,
  UploadProgress,
  uploadNativeMedia,
  uploadResultMediaId,
  validateNativeMedia
} from "./nativeMediaUpload";

export const COMPOSER_MEDIA_LIMIT = 4;

export type ComposerMediaItem = {
  id: string;
  asset: NativeMediaAsset;
  result: NativeMediaUploadResult | null;
  progress: UploadProgress;
  error: string;
};

type RestoredComposerMedia = Pick<ComposerMediaItem, "id" | "asset" | "result">;

const selectedProgress = (asset: NativeMediaAsset): UploadProgress => ({
  stage: "selected",
  percent: 0,
  message: `${asset.mediaType === "video" ? "Video" : "Image"} selected.`
});

export function useComposerMediaQueue(defaultOptions: NativeMediaUploadOptions) {
  const [items, setItems] = useState<ComposerMediaItem[]>([]);
  const controllers = useRef(new Map<string, UploadController>());
  const uploading = useMemo(() => items.some((item) => ["validating", "uploading", "processing"].includes(item.progress.stage)), [items]);

  const addAssets = useCallback((assets: NativeMediaAsset[]) => {
    setItems((current) => {
      const remaining = Math.max(0, COMPOSER_MEDIA_LIMIT - current.length);
      const next = assets.slice(0, remaining).map((asset, index) => ({
        id: `composer-media-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 7)}`,
        asset,
        result: null,
        progress: selectedProgress(asset),
        error: ""
      }));
      return [...current, ...next];
    });
  }, []);

  const chooseImages = useCallback(async () => {
    const remaining = Math.max(0, COMPOSER_MEDIA_LIMIT - items.length);
    if (!remaining) return [];
    const picked = await pickNativeImages(remaining);
    addAssets(picked.assets);
    return picked.assets;
  }, [addAssets, items.length]);

  const chooseVideo = useCallback(async () => {
    if (items.length >= COMPOSER_MEDIA_LIMIT) return null;
    const picked = await pickNativeVideo();
    if (picked.asset) addAssets([picked.asset]);
    return picked.asset;
  }, [addAssets, items.length]);

  const updateItem = useCallback((id: string, update: Partial<ComposerMediaItem>) => {
    setItems((current) => current.map((item) => item.id === id ? { ...item, ...update } : item));
  }, []);

  const uploadItem = useCallback(async (item: ComposerMediaItem, overrideOptions: Partial<NativeMediaUploadOptions> = {}) => {
    if (item.result && uploadResultMediaId(item.result)) return item.result;
    const validation = validateNativeMedia(item.asset);
    if (validation) {
      updateItem(item.id, { error: validation, progress: { stage: "failed", percent: 0, message: validation } });
      throw new Error(validation);
    }
    updateItem(item.id, { error: "", progress: { stage: "validating", percent: 1, message: "Preparing media." } });
    const task = uploadNativeMedia(item.asset, { ...defaultOptions, ...overrideOptions }, (progress) => updateItem(item.id, { progress }));
    controllers.current.set(item.id, task.controller);
    try {
      const uploaded = await task.promise;
      const mediaId = uploadResultMediaId(uploaded);
      if (!mediaId) throw new Error("Upload completed but media did not attach. Please retry.");
      const finalResult = await pollNativeMediaProcessing(mediaId, 8, 1500, (progress) => updateItem(item.id, { progress }));
      const result = finalResult || uploaded;
      updateItem(item.id, { result, error: "", progress: { stage: "ready", percent: 100, message: "Media is ready." } });
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : "Upload failed.";
      const cancelled = /cancel/i.test(message);
      updateItem(item.id, {
        error: cancelled ? "" : message,
        progress: { stage: cancelled ? "cancelled" : "failed", percent: 0, message }
      });
      throw error;
    } finally {
      controllers.current.delete(item.id);
    }
  }, [defaultOptions, updateItem]);

  const uploadAll = useCallback(async (overrideOptions: Partial<NativeMediaUploadOptions> = {}) => {
    const snapshot = items;
    const results = await Promise.all(snapshot.map((item) => uploadItem(item, overrideOptions)));
    const mediaIds = results.map(uploadResultMediaId);
    if (mediaIds.some((id) => !id) || mediaIds.length !== snapshot.length) {
      throw new Error("Every attachment must finish uploading before publication.");
    }
    return { results, mediaIds };
  }, [items, uploadItem]);

  const retry = useCallback(async (id: string) => {
    const item = items.find((candidate) => candidate.id === id);
    if (!item) return null;
    return uploadItem({ ...item, result: null });
  }, [items, uploadItem]);

  const cancel = useCallback((id: string) => {
    controllers.current.get(id)?.cancel();
    controllers.current.delete(id);
    updateItem(id, { progress: { stage: "cancelled", percent: 0, message: "Upload cancelled." } });
  }, [updateItem]);

  const remove = useCallback((id: string) => {
    controllers.current.get(id)?.cancel();
    controllers.current.delete(id);
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const move = useCallback((id: string, direction: -1 | 1) => {
    setItems((current) => {
      const index = current.findIndex((item) => item.id === id);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }, []);

  const restore = useCallback((restored: RestoredComposerMedia[]) => {
    setItems((restored || []).slice(0, COMPOSER_MEDIA_LIMIT).map((item) => ({
      ...item,
      result: item.result || null,
      error: "",
      progress: item.result && uploadResultMediaId(item.result)
        ? { stage: "ready", percent: 100, message: "Uploaded media restored." }
        : selectedProgress(item.asset)
    })));
  }, []);

  const reset = useCallback(() => {
    controllers.current.forEach((controller) => controller.cancel());
    controllers.current.clear();
    setItems([]);
  }, []);

  return {
    items,
    uploading,
    addAssets,
    chooseImages,
    chooseVideo,
    uploadAll,
    retry,
    cancel,
    remove,
    move,
    restore,
    reset
  };
}
