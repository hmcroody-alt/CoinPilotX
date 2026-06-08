export function formatRelativeTime(value?: string | null) {
  if (!value) return "now";
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return "recently";
  const diff = Math.max(0, Date.now() - time);
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(time));
}

export function formatDuration(seconds?: number | string | null) {
  const value = Number(seconds || 0);
  if (!Number.isFinite(value) || value <= 0) return "";
  const whole = Math.floor(value);
  const minutes = Math.floor(whole / 60);
  const remainder = String(whole % 60).padStart(2, "0");
  if (minutes < 60) return `${minutes}:${remainder}`;
  const hours = Math.floor(minutes / 60);
  return `${hours}:${String(minutes % 60).padStart(2, "0")}:${remainder}`;
}

export function compactNumber(value?: number | string | null) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number <= 0) return "0";
  if (number < 1000) return String(number);
  if (number < 1000000) return `${(number / 1000).toFixed(number >= 10000 ? 0 : 1)}K`;
  return `${(number / 1000000).toFixed(number >= 10000000 ? 0 : 1)}M`;
}

export function initials(name?: string | null) {
  const parts = String(name || "PulseSoc").trim().split(/\s+/).slice(0, 2);
  return parts.map(part => part[0]?.toUpperCase()).join("") || "P";
}
