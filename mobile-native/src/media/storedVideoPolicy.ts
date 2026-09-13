/**
 * Mirror of services/stored_video_policy.py. The server is authoritative; this
 * table exists so the picker can cap a recording and the composer can say why
 * before spending a minute uploading something the server will refuse.
 *
 * Keep the numbers identical to the Python module -- tests/
 * test_stored_video_policy.py parses this file and fails if they drift.
 */

export const MAX_STORED_VIDEO_SECONDS = 90 * 60;
export const SHORT_FORM_SECONDS = 60;
export const MARKETPLACE_SECONDS = 10 * 60;

const SURFACE_SECONDS: Record<string, number> = {
  messenger: MAX_STORED_VIDEO_SECONDS,
  comm_v2: MAX_STORED_VIDEO_SECONDS,
  post: MAX_STORED_VIDEO_SECONDS,
  feed: MAX_STORED_VIDEO_SECONDS,
  reel: MAX_STORED_VIDEO_SECONDS,
  status: SHORT_FORM_SECONDS,
  pulse_status: SHORT_FORM_SECONDS,
  marketplace: MARKETPLACE_SECONDS,
  seller_listing: MARKETPLACE_SECONDS,
  ad_creative: MARKETPLACE_SECONDS
};

const ALIASES: Record<string, string> = {
  photo: "post",
  video: "post",
  pulse: "post",
  pulse_video: "post",
  pulse_post: "post",
  pulse_camera: "post",
  pulse_group: "post",
  creator_studio: "post",
  reels: "reel",
  pulse_reel: "reel",
  chat: "messenger",
  message: "messenger",
  private_message: "messenger",
  private_chat: "messenger",
  pulse_message: "messenger",
  marketplace_product: "marketplace",
  pulse_ad_creative: "ad_creative"
};

function canonical(surface: string): string {
  const name = String(surface || "").trim().toLowerCase();
  if (name.startsWith("pulse_comm_v2")) return "comm_v2";
  return ALIASES[name] || name;
}

export function isKnownSurface(surface: string): boolean {
  return canonical(surface) in SURFACE_SECONDS;
}

export function strictestSeconds(): number {
  return Math.min(...Object.values(SURFACE_SECONDS));
}

/**
 * An unrecognised surface answers the strictest cap, not the most permissive.
 * This is a ceiling: a typo must not buy the caller the longest limit.
 */
export function maxDurationSeconds(surface: string): number {
  const name = canonical(surface);
  return SURFACE_SECONDS[name] ?? strictestSeconds();
}

export function maxDurationMs(surface: string): number {
  return maxDurationSeconds(surface) * 1000;
}

/** True only for a known duration over the ceiling. Absent measurement is not a violation. */
export function exceedsLimit(surface: string, durationSeconds: number | null | undefined): boolean {
  const seconds = Number(durationSeconds || 0);
  if (!Number.isFinite(seconds) || seconds <= 0) return false;
  return Math.floor(seconds) > maxDurationSeconds(surface);
}

/**
 * Mirror of media_service.direct_video_limit_bytes.
 *
 * Derived rather than fixed for the same reason as on the server: a flat 700 MB
 * client ceiling makes a 90-minute upload impossible no matter what the duration
 * table says, so the app would refuse video the server would have accepted.
 */
export const VIDEO_BUDGET_BITS_PER_SECOND = 3_200_000;
export const VIDEO_FLOOR_BYTES = 350 * 1024 * 1024;

export function maxVideoBytes(surface: string): number {
  return Math.max(VIDEO_FLOOR_BYTES, Math.floor((maxDurationSeconds(surface) * VIDEO_BUDGET_BITS_PER_SECOND) / 8));
}

export function limitMessage(surface: string): string {
  const seconds = maxDurationSeconds(surface);
  if (seconds % 60 === 0) {
    const minutes = seconds / 60;
    return `Videos can be up to ${minutes} ${minutes === 1 ? "minute" : "minutes"} long.`;
  }
  return `Videos can be up to ${seconds} seconds long.`;
}
