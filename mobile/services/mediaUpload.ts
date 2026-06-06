import { pulseApi } from "./apiClient";

export type LocalUploadAsset = {
  uri: string;
  name: string;
  mimeType: string;
};

export async function uploadPulseMedia(asset: LocalUploadAsset, purpose: "post" | "reel" | "message" | "marketplace" = "post") {
  const formData = new FormData();
  formData.append("purpose", purpose);
  formData.append("file", {
    uri: asset.uri,
    name: asset.name,
    type: asset.mimeType
  } as unknown as Blob);

  return pulseApi<Record<string, unknown>>("/api/pulse/media/upload", {
    method: "POST",
    body: formData,
    skipJsonHeader: true
  });
}

export async function createMuxDirectUpload(filename: string, mimeType: string) {
  return pulseApi<Record<string, unknown>>("/api/pulse/media/mux/direct-upload", {
    method: "POST",
    body: JSON.stringify({ filename, content_type: mimeType })
  });
}
