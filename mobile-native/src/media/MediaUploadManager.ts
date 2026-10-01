import AsyncStorage from "@react-native-async-storage/async-storage";
import { File } from "expo-file-system";
import { pulseApi, PulseApiError } from "../api/pulseApi";
import type { NativeMediaAsset, NativeMediaUploadOptions, NativeMediaUploadResult, UploadProgress } from "./nativeMediaUpload";

const STORAGE_PREFIX = "pulsesoc.media-upload.v2.";
const PARALLEL_PARTS = 4;
const MAX_RETRIES = 5;

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
function sleep(ms: number) { return new Promise((resolve) => setTimeout(resolve, ms)); }

// Normalize a local media URI for `fetch()` without double-encoding or stripping an
// existing scheme. AVFoundation/expo emit `file://…` already; a bare `/var/…` path gets a
// `file://` prefix. `content://`, `ph://`, `http(s)://` are passed through untouched.
function toFetchableUri(uri: string) {
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(uri) || uri.startsWith("content://") || uri.startsWith("ph://")) return uri;
  return `file://${uri.startsWith("/") ? "" : "/"}${uri}`;
}

// Obtain a React Native native-backed Blob that streams from the filesystem. The blob is a
// descriptor (blobId + offset + size) — the bytes stay in native memory and never enter JS,
// so there is no ArrayBuffer/Uint8Array round-trip. `blob.slice()` returns a zero-copy view
// over the same native data, which is what makes multipart part uploads memory-safe.
async function nativeBlobFromUri(uri: string): Promise<Blob> {
  const response = await fetch(toFetchableUri(uri));
  return response.blob();
}

async function persist(state: PersistedUpload) {
  await AsyncStorage.setItem(keyFor(state.session.upload_id), JSON.stringify(state));
}

async function removePersisted(uploadId: string) {
  await AsyncStorage.removeItem(keyFor(uploadId)).catch(() => undefined);
}

function transientStatus(status: number) {
  return status === 0 || status === 408 || status === 429 || status >= 500;
}

async function withRetry<T>(operation: () => Promise<T>, onRetry: (attempt: number) => void, isCancelled: () => boolean) {
  let lastError: unknown;
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
    if (isCancelled()) throw new Error("Upload cancelled.");
    try { return await operation(); } catch (error) {
      lastError = error;
      const status = error instanceof PulseApiError ? error.status : Number((error as { status?: number })?.status || 0);
      if (attempt >= MAX_RETRIES || !transientStatus(status)) throw error;
      onRetry(attempt + 1);
      const jitter = Math.floor(Math.random() * 350);
      await sleep(Math.min(8000, 500 * (2 ** attempt)) + jitter);
    }
  }
  throw lastError;
}

function uploadBlob(
  url: string,
  blob: Blob,
  mimeType: string,
  onBytes: (loaded: number) => void,
  register: (xhr: XMLHttpRequest | null) => void
): Promise<{ etag: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(); register(xhr);
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", mimeType);
    xhr.upload.onprogress = (event) => onBytes(event.loaded);
    xhr.onerror = () => reject(Object.assign(new Error("Upload transport was interrupted."), { status: 0, category: "network" }));
    xhr.onabort = () => reject(new Error("Upload cancelled."));
    xhr.onload = () => {
      register(null);
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(Object.assign(new Error(`Storage rejected upload (${xhr.status}).`), { status: xhr.status, category: "storage" }));
        return;
      }
      resolve({ etag: String(xhr.getResponseHeader("ETag") || xhr.getResponseHeader("etag") || "").replace(/^W\//, "") });
    };
    xhr.send(blob);
  });
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
    let session: UploadSession;
    if (resumable) {
      try {
        const server = await pulseApi<UploadSession & { ok: boolean }>(`/api/pulse/media/uploads/${resumable.session.upload_id}`);
        session = { ...resumable.session, ...server, completed_parts: resumable.session.completed_parts || server.completed_parts || [] };
        onProgress?.({ stage: "resuming", percent: 2, message: "Resuming upload." });
      } catch {
        await removePersisted(resumable.session.upload_id);
        session = await pulseApi<UploadSession & { ok: boolean }>("/api/pulse/media/uploads", { method: "POST", body: JSON.stringify({ filename: asset.name, mime_type: asset.mimeType, file_size_bytes: actualSize, context_type: options.contextType, context_id: options.contextId || "native-draft" }) });
      }
    } else {
      session = await pulseApi<UploadSession & { ok: boolean }>("/api/pulse/media/uploads", { method: "POST", body: JSON.stringify({ filename: asset.name, mime_type: asset.mimeType, file_size_bytes: actualSize, context_type: options.contextType, context_id: options.contextId || "native-draft" }) });
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
      let cursor = 0;
      const worker = async () => {
        while (cursor < pending.length) {
          const number = pending[cursor++];
          const start = (number - 1) * session.part_size_bytes;
          const end = Math.min(actualSize, start + session.part_size_bytes);
          await withRetry(async () => {
            const signed = await pulseApi<{ parts: Array<{ part_number: number; upload_url: string }> }>(`/api/pulse/media/uploads/${session.upload_id}/parts/sign`, { method: "POST", body: JSON.stringify({ part_numbers: [number] }) });
            const part = signed.parts[0];
            const result = await uploadBlob(part.upload_url, (uploadBody as Blob).slice(start, end, asset.mimeType), asset.mimeType, (loaded) => { uploadedByPart.set(number, loaded); report("uploading", "Uploading media"); }, register);
            if (!result.etag) throw Object.assign(new Error("Storage did not return part integrity metadata."), { status: 502 });
            completed.set(number, result.etag); uploadedByPart.set(number, end - start);
            state.session.completed_parts = [...completed].map(([part_number, etag]) => ({ part_number, etag })); state.updatedAt = Date.now(); await persist(state);
          }, retry, isCancelled);
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
