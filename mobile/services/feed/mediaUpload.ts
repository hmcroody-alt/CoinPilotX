import { PULSE_API_BASE_URL } from "../config";
import { getSessionCookie } from "../secureSession";
import { UploadResult } from "./types";

export type UploadAsset = {
  uri: string;
  name: string;
  mimeType: string;
};

export function uploadFeedMedia(asset: UploadAsset, onProgress?: (progress: number) => void) {
  return new Promise<UploadResult>((resolve, reject) => {
    const request = new XMLHttpRequest();
    const form = new FormData();
    form.append("context_type", "pulse_post");
    form.append("file", {
      uri: asset.uri,
      name: asset.name,
      type: asset.mimeType
    } as unknown as Blob);

    request.upload.onprogress = event => {
      if (event.lengthComputable && onProgress) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    request.onload = () => {
      try {
        const data = JSON.parse(request.responseText || "{}") as UploadResult;
        if (request.status >= 200 && request.status < 300 && data.ok !== false) resolve(data);
        else reject(new Error(data.message || "Upload failed."));
      } catch {
        reject(new Error("Upload returned an unreadable response."));
      }
    };
    request.onerror = () => reject(new Error("Upload failed. Check your connection and retry."));

    getSessionCookie()
      .then(cookie => {
        request.open("POST", `${PULSE_API_BASE_URL}/api/pulse/media/upload`);
        if (cookie) request.setRequestHeader("Cookie", cookie);
        request.send(form);
      })
      .catch(reject);
  });
}

export function mediaIdFromUpload(result: UploadResult) {
  return Number(result.media?.id || result.media?.media_id || result.media_id || result.id || 0);
}
