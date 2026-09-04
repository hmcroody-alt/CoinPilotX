/**
 * Document picker owner (Phase 18) — shared validation for attachments.
 *
 * Screens call `pickDocument` with a policy instead of importing
 * expo-document-picker directly. Validation covers MIME, extension, and
 * size before anything is handed to the upload pipeline.
 */
import * as DocumentPicker from "expo-document-picker";

export type DocumentPolicy = {
  /** Allowed MIME types (exact or prefix like "image/"). Empty = any. */
  mimeTypes?: string[];
  /** Lowercase extensions without dot, e.g. ["pdf","docx"]. Empty = any. */
  extensions?: string[];
  /** Max size in bytes. Default 50 MB. */
  maxBytes?: number;
};

export type PickedDocument = {
  uri: string;
  name: string;
  size: number;
  mimeType: string;
};

export type PickResult =
  | { ok: true; document: PickedDocument }
  | { ok: false; reason: "cancelled" | "too_large" | "bad_type" | "error" };

const DEFAULT_MAX_BYTES = 50 * 1024 * 1024;

export function validateDocument(
  doc: { name?: string | null; size?: number | null; mimeType?: string | null },
  policy: DocumentPolicy = {}
): "ok" | "too_large" | "bad_type" {
  const size = typeof doc.size === "number" ? doc.size : 0;
  if (size > (policy.maxBytes ?? DEFAULT_MAX_BYTES)) return "too_large";

  const name = String(doc.name ?? "").toLowerCase();
  const ext = name.includes(".") ? name.split(".").pop() ?? "" : "";
  const mime = String(doc.mimeType ?? "").toLowerCase();

  if (policy.extensions?.length && !policy.extensions.includes(ext)) return "bad_type";
  if (policy.mimeTypes?.length) {
    const match = policy.mimeTypes.some((allowed) =>
      allowed.endsWith("/") ? mime.startsWith(allowed) : mime === allowed
    );
    if (!match) return "bad_type";
  }
  return "ok";
}

export async function pickDocument(policy: DocumentPolicy = {}): Promise<PickResult> {
  try {
    const result = await DocumentPicker.getDocumentAsync({
      type: policy.mimeTypes?.length ? policy.mimeTypes.map((m) => (m.endsWith("/") ? `${m}*` : m)) : "*/*",
      copyToCacheDirectory: true,
      multiple: false
    });
    if (result.canceled || !result.assets?.length) return { ok: false, reason: "cancelled" };
    const asset = result.assets[0];
    const verdict = validateDocument(asset, policy);
    if (verdict !== "ok") return { ok: false, reason: verdict };
    return {
      ok: true,
      document: {
        uri: asset.uri,
        name: asset.name ?? "document",
        size: asset.size ?? 0,
        mimeType: asset.mimeType ?? "application/octet-stream"
      }
    };
  } catch {
    return { ok: false, reason: "error" };
  }
}
