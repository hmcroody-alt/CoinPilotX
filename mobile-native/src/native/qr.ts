/**
 * QR identity payloads (Phases 4-5) + scanned-payload validation (Phase 41).
 *
 * QR codes encode canonical PulseSoc deep links only — never opaque vendor
 * links. Scanned payloads are validated before any navigation: unknown
 * schemes, foreign hosts, and javascript/data URIs are rejected.
 */

export type QrEntityKind =
  | "profile"
  | "business"
  | "group"
  | "community"
  | "event"
  | "marketplace"
  | "invite"
  | "post"
  | "reel";

const PATHS: Record<QrEntityKind, string> = {
  profile: "profile",
  business: "business",
  group: "group",
  community: "community",
  event: "event",
  marketplace: "marketplace/item",
  invite: "invite",
  post: "post",
  reel: "reel"
};

const WEB_ORIGIN = "https://pulsesoc.com";

/** Canonical shareable link for a PulseSoc entity (web form — universal link). */
export function qrLink(kind: QrEntityKind, id: string | number): string {
  const cleanId = encodeURIComponent(String(id).trim());
  if (!cleanId) throw new Error("qrLink requires an id");
  return `${WEB_ORIGIN}/${PATHS[kind]}/${cleanId}`;
}

export type ScannedPayload =
  | { kind: "pulsesoc_link"; url: string; path: string }
  | { kind: "external_url"; url: string }
  | { kind: "text"; text: string }
  | { kind: "rejected"; reason: "empty" | "dangerous_scheme" | "malformed" };

/**
 * Classify a scanned QR/barcode payload. Only `pulsesoc_link` may be
 * auto-routed; `external_url` requires explicit user confirmation;
 * `rejected` must never be acted upon.
 */
export function classifyScannedPayload(raw: string): ScannedPayload {
  const text = String(raw ?? "").trim();
  if (!text) return { kind: "rejected", reason: "empty" };
  if (/^(javascript|data|file|vbscript|blob):/i.test(text)) {
    return { kind: "rejected", reason: "dangerous_scheme" };
  }
  if (/^pulsesoc:\/\//i.test(text)) {
    const path = text.replace(/^pulsesoc:\/\//i, "");
    if (!path || /[\s<>"']/.test(path)) return { kind: "rejected", reason: "malformed" };
    return { kind: "pulsesoc_link", url: text, path };
  }
  if (/^https?:\/\//i.test(text)) {
    let parsed: URL;
    try {
      parsed = new URL(text);
    } catch {
      return { kind: "rejected", reason: "malformed" };
    }
    const host = parsed.hostname.toLowerCase();
    if (host === "pulsesoc.com" || host.endsWith(".pulsesoc.com")) {
      return { kind: "pulsesoc_link", url: text, path: parsed.pathname.replace(/^\//, "") };
    }
    return { kind: "external_url", url: text };
  }
  return { kind: "text", text };
}
