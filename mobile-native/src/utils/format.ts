import { formatShortTimestamp } from "../core/localTime";

/**
 * Localized short timestamp for feeds and lists. Delegates to the central
 * LocalTimeService so every call site converts the stored UTC instant into the
 * viewer's active time zone (device or manual override) with DST handled.
 */
export function formatShortTime(value?: string) {
  return formatShortTimestamp(value);
}

export function formatFileSize(bytes?: number) {
  const size = Number(bytes || 0);
  if (!size) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function compactPreview(value?: string, fallback = "") {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return fallback;
  return text.length > 96 ? `${text.slice(0, 93)}...` : text;
}
