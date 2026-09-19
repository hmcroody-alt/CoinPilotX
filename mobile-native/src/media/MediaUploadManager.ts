import AsyncStorage from "@react-native-async-storage/async-storage";
import { File } from "expo-file-system";
import { pulseApi } from "../api/pulseApi";
import type { NativeMediaAsset, NativeMediaUploadOptions, NativeMediaUploadResult, UploadProgress } from "./nativeMediaUpload";
import { MAX_RETRIES, PARALLEL_PARTS, nativeBlobFromUri, uploadBlob, withRetry } from "./resumableUploadTransport";

const STORAGE_PREFIX = "pulsesoc.media-upload.v2.";

type UploadSession = {
  upload_id: string;
  object_key: string;
  strategy: "single" | "multipart";
  upload_url?: string;
  part_size_bytes: number;
  file_size_bytes: number;
  completed_parts: Array<{ part_number: number; etag: string }>;
  status: string;
  trace_id?: string;
  // How many part signatures the server will mint in one call. Optional because a
  // session persisted before this field existed is resumed as-is.
  max_parts_per_request?: number;
};

type PersistedUpload = {
  session: UploadSession;
  asset: NativeMediaAsset;
  options: NativeMediaUploadOptions;
  retryCount: number;
  updatedAt: number;
};

type ManagedTask = {
  promise: Promise<NativeMediaUploadResult>;
  cancel: () => void;
};

const activeTasks = new Map<string, ManagedTask>();

function keyFor(uploadId: string) { return `${STORAGE_PREFIX}${uploadId}`; }

async function persist(state: PersistedUpload) {
  await AsyncStorage.setItem(keyFor(state.session.upload_id), JSON.stringify(state));
}

async function removePersisted(uploadId: string) {
  await AsyncStorage.removeItem(keyFor(uploadId)).catch(() => undefined);
}

export class MediaUploadManager {
  upload(asset: NativeMediaAsset, options: NativeMediaUploadOptions, onProgress?: (value: UploadProgress) => void): ManagedTask {
    const identity = `${asset.uri}|${asset.size || 0}|${options.contextType}|${options.contextId || ""}`;
    const existing = activeTasks.get(identity);
    if (existing) return existing;
    let cancelled = false;
    let currentUploadId = "";
    const requests = new Set<XMLHttpRequest>();
    const cancel = () => {
      cancelled = true;
      requests.forEach((xhr) => xhr.abort());
      requests.clear();
      if (currentUploadId) void this.abort(currentUploadId);
    };
    const task: ManagedTask = {
      cancel,
      promise: this.run(asset, options, onProgress, () => cancelled, (uploadId) => { currentUploadId = uploadId; }, (xhr) => {
        if (xhr) requests.add(xhr); else requests.forEach((candidate) => {
          if (candidate.readyState === XMLHttpRequest.DONE) requests.delete(candidate);
        });
      }).finally(() => activeTasks.delete(identity))
    };
    activeTasks.set(identity, task);
    return task;
  }

  private async run(
    asset: NativeMediaAsset,
    options: NativeMediaUploadOptions,
    onProgress: ((value: UploadProgress) => void) | undefined,
    isCancelled: () => boolean,
    onSession: (uploadId: string) => void,
    register: (xhr: XMLHttpRequest | null) => void
  ): Promise<NativeMediaUploadResult> {
    const file = new File(asset.uri);
    const actualSize = Number(asset.size || file.size || 0);
    if (!file.exists || actualSize <= 0) throw new Error("The selected media file is no longer available.");
    onProgress?.({ stage: "validating", percent: 1, message: "Preparing secure upload." });
    const resumable = (await this.resumableUploads()).find((candidate) =>
      candidate.asset.uri === asset.uri && candidate.asset.size === actualSize && candidate.options.contextType === options.contextType
    );
    // One body for both call sites. `duration_ms` is the picker's own unit,
    // forwarded unconverted so the server can refuse an over-long video before it
    // issues a signed URL -- the alternative is discovering the limit after an
    // hour of cellular upload.
    const createBody = JSON.stringify({
      filename: asset.name,
      mime_type: asset.mimeType,
      file_size_bytes: actualSize,
      context_type: options.contextType,
      context_id: options.contextId || "native-draft",
      duration_ms: Math.round(Number(asset.duration || 0))
    });
    let session: UploadSession;
    if (resumable) {
      try {
        const server = await pulseApi<UploadSession & { ok: boolean }>(`/api/pulse/media/uploads/${resumable.session.upload_id}`);
        session = { ...resumable.session, ...server, completed_parts: resumable.session.completed_parts || server.completed_parts || [] };
        onProgress?.({ stage: "resuming", percent: 2, message: "Resuming upload." });
      } catch {
        await removePersisted(resumable.session.upload_id);
        session = await pulseApi<UploadSession & { ok: boolean }>("/api/pulse/media/uploads", { method: "POST", body: createBody });
      }
    } else {
      session = await pulseApi<UploadSession & { ok: boolean }>("/api/pulse/media/uploads", { method: "POST", body: createBody });
    }
    onSession(session.upload_id);
    const state: PersistedUpload = { session, asset: { ...asset, size: actualSize }, options, retryCount: resumable?.retryCount || 0, updatedAt: Date.now() };
    await persist(state);
    const totalParts = session.strategy === "multipart" ? Math.ceil(actualSize / session.part_size_bytes) : 1;
    const uploadedByPart = new Map<number, number>();
    const report = (stage: UploadProgress["stage"], message: string) => {
      const completed = [...uploadedByPart.values()].reduce((sum, value) => sum + value, 0);
      const percent = Math.min(94, Math.max(2, Math.round((completed / actualSize) * 94)));
      onProgress?.({ stage, percent, message: `${message} ${Math.round((completed / actualSize) * 100)}%.` });
    };
    const retry = (attempt: number) => {
      state.retryCount += 1; state.updatedAt = Date.now(); void persist(state);
      onProgress?.({ stage: "resuming", percent: Math.max(2, Math.round(([...uploadedByPart.values()].reduce((a, b) => a + b, 0) / actualSize) * 94)), message: `Upload interrupted. Retrying (${attempt}/${MAX_RETRIES})…` });
    };
    onProgress?.({ stage: "uploading", percent: 2, message: "Uploading media 0%." });
    // Stream the finished (already-on-disk) media as a native-backed RN Blob. Created once
    // and reused across retries and every multipart part, so the mix file is never re-read
    // into JS memory. Only fetched when bytes still need to be sent.
    const uploadBody = session.status !== "completed" ? await nativeBlobFromUri(asset.uri) : null;
    if (session.status !== "completed" && session.strategy === "single") {
      await withRetry(async () => {
        let uploadUrl = state.session.upload_url;
        if (!uploadUrl) {
          const refreshed = await pulseApi<UploadSession & { ok: boolean }>(`/api/pulse/media/uploads/${session.upload_id}/refresh`, { method: "POST", body: "{}" });
          uploadUrl = refreshed.upload_url;
        }
        if (!uploadUrl) throw Object.assign(new Error("Upload authorization expired."), { status: 410 });
        try {
          await uploadBlob(uploadUrl, uploadBody as Blob, asset.mimeType, (loaded) => { uploadedByPart.set(1, loaded); report("uploading", "Uploading media"); }, register);
        } catch (error) {
          if ([401, 403, 410].includes(Number((error as { status?: number })?.status || 0))) state.session.upload_url = undefined;
          throw error;
        }
      }, retry, isCancelled);
    } else if (session.status !== "completed") {
      const completed = new Map((session.completed_parts || []).map((part) => [part.part_number, part.etag]));
      completed.forEach((_etag, number) => uploadedByPart.set(number, Math.min(session.part_size_bytes, actualSize - ((number - 1) * session.part_size_bytes))));
      const pending = Array.from({ length: totalParts }, (_, index) => index + 1).filter((number) => !completed.has(number));
      // Signatures come back a batch per round trip. Signing one part at a time cost a
      // request before every single part: a 350 MB status video at 8 MB parts spent 44
      // extra sequential round trips that moved no bytes. The cap is whatever the server
      // advertises -- `sign_parts` silently drops the overflow, and a dropped part would
      // not surface until `complete` rejected the part list. An older server (or a session
      // persisted before this shipped) omits the field, so fall back to the previous
      // one-at-a-time behaviour rather than guessing a cap.
      const perRequest = Math.max(1, Number(session.max_parts_per_request || 0) || 1);
      const signedUrls = new Map<number, string>();
      const signParts = (numbers: number[]) =>
        withRetry(async () => {
          const signed = await pulseApi<{ parts?: Array<{ part_number: number; upload_url: string }> }>(`/api/pulse/media/uploads/${session.upload_id}/parts/sign`, { method: "POST", body: JSON.stringify({ part_numbers: numbers }) });
          for (const part of signed.parts || []) signedUrls.set(Number(part.part_number), String(part.upload_url || ""));
        }, retry, isCancelled);
      let cursor = 0;
      const worker = async () => {
        while (cursor < pending.length) {
          const batch = pending.slice(cursor, cursor + perRequest);
          cursor += batch.length;
          await signParts(batch);
          for (const number of batch) {
            const start = (number - 1) * session.part_size_bytes;
            const end = Math.min(actualSize, start + session.part_size_bytes);
            await withRetry(async () => {
              const send = async () => {
                if (!signedUrls.get(number)) await signParts([number]);
                const url = signedUrls.get(number);
                if (!url) throw Object.assign(new Error("Upload authorization expired."), { status: 410 });
                return uploadBlob(url, (uploadBody as Blob).slice(start, end, asset.mimeType), asset.mimeType, (loaded) => { uploadedByPart.set(number, loaded); report("uploading", "Uploading media"); }, register);
              };
              let result;
              try {
                result = await send();
              } catch (error) {
                // A batched signature is minted before the whole batch is sent, so the
                // last part of a batch can outlive its URL on a slow link. That reads as
                // 401/403/410, which `transientStatus` deliberately does not retry, so
                // re-sign this one part and send it once more. Only a second rejection is
                // a genuine failure.
                if (![401, 403, 410].includes(Number((error as { status?: number })?.status || 0))) throw error;
                signedUrls.delete(number);
                result = await send();
              }
              if (!result.etag) throw Object.assign(new Error("Storage did not return part integrity metadata."), { status: 502 });
              completed.set(number, result.etag); uploadedByPart.set(number, end - start); signedUrls.delete(number);
              state.session.completed_parts = [...completed].map(([part_number, etag]) => ({ part_number, etag })); state.updatedAt = Date.now(); await persist(state);
            }, retry, isCancelled);
          }
        }
      };
      await Promise.all(Array.from({ length: Math.min(PARALLEL_PARTS, pending.length) }, worker));
      await pulseApi(`/api/pulse/media/uploads/${session.upload_id}/complete`, { method: "POST", body: JSON.stringify({ parts: [...completed].map(([part_number, etag]) => ({ part_number, etag })).sort((a, b) => a.part_number - b.part_number) }) });
    }
    if (session.status !== "completed" && session.strategy === "single") await pulseApi(`/api/pulse/media/uploads/${session.upload_id}/complete`, { method: "POST", body: "{}" });
    onProgress?.({ stage: "finalizing", percent: 97, message: "Finalizing upload." });
    const result = await withRetry(() => pulseApi<NativeMediaUploadResult>(`/api/pulse/media/uploads/${session.upload_id}/finalize`, { method: "POST", body: "{}" }), retry, isCancelled);
    await removePersisted(session.upload_id);
    return result;
  }

  async abort(uploadId: string) {
    await pulseApi(`/api/pulse/media/uploads/${uploadId}/abort`, { method: "POST", body: "{}" }).catch(() => undefined);
    await removePersisted(uploadId);
  }

  async resumableUploads(): Promise<PersistedUpload[]> {
    const keys = (await AsyncStorage.getAllKeys()).filter((key) => key.startsWith(STORAGE_PREFIX));
    const values = await AsyncStorage.multiGet(keys);
    return values.flatMap(([, value]) => { try { return value ? [JSON.parse(value) as PersistedUpload] : []; } catch { return []; } });
  }
}

export const mediaUploadManager = new MediaUploadManager();
export const mediaUploadParallelParts = PARALLEL_PARTS;
