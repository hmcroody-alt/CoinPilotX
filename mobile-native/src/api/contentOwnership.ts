// Centralized ownership + canonical ID resolution for user-owned content
// (posts, reels, comments) across legacy and current backend field shapes.
//
// The PulseSoc backend has evolved the post/reel payload shape over time,
// so ownership can show up as a top-level `user_id`, a legacy alias
// (`author_id`, `owner_id`, `creator_id`, ...), or nested inside an
// `author`/`user`/`owner`/`creator` object. Some payloads (notably Reels)
// also expose a server-computed permission flag (`can_manage`) which is the
// most authoritative signal when present, since it accounts for
// group-admin overrides that a raw owner-id comparison would miss.

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord | null {
  if (value && typeof value === "object") return value as UnknownRecord;
  return null;
}

function toPositiveInt(value: unknown): number {
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return 0;
  return Math.trunc(num);
}

const DIRECT_OWNER_FIELDS = [
  "user_id",
  "author_id",
  "owner_id",
  "creator_id",
  "uploader_id",
  "posted_by",
  "created_by"
];

const NESTED_OWNER_KEYS = ["author", "user", "owner", "creator", "profile"];
const NESTED_ID_FIELDS = ["user_id", "id", "owner_id", "author_id"];

/**
 * Resolves the canonical numeric owner/author id for a piece of content
 * (post, reel, comment, etc), across every legacy and current field shape
 * the backend has ever emitted. Returns 0 if no owner id can be found.
 */
export function resolveContentOwnerId(content: unknown): number {
  const record = asRecord(content);
  if (!record) return 0;

  for (const field of DIRECT_OWNER_FIELDS) {
    const id = toPositiveInt(record[field]);
    if (id) return id;
  }

  for (const nestedKey of NESTED_OWNER_KEYS) {
    const nested = asRecord(record[nestedKey]);
    if (!nested) continue;
    for (const field of NESTED_ID_FIELDS) {
      const id = toPositiveInt(nested[field]);
      if (id) return id;
    }
  }

  return 0;
}

/**
 * Resolves the canonical numeric id for the content item itself
 * (handles `id` vs legacy `post_id`/`reel_id`/`comment_id` aliases).
 */
export function resolveContentId(content: unknown): number {
  const record = asRecord(content);
  if (!record) return 0;
  const candidates = ["id", "post_id", "reel_id", "comment_id"];
  for (const field of candidates) {
    const id = toPositiveInt(record[field]);
    if (id) return id;
  }
  return 0;
}

const OWNER_FLAG_FIELDS = ["can_manage", "can_delete", "is_owner", "is_mine", "is_author"];

/**
 * Determines whether the current user owns (and therefore may manage) a
 * piece of content. Prefers an explicit server-computed permission flag
 * when present (e.g. Reels' `can_manage`, which already accounts for
 * group-admin overrides), and otherwise falls back to comparing the
 * resolved owner id against the current user's id.
 */
export function isContentOwner(content: unknown, currentUserId: number | null | undefined): boolean {
  const record = asRecord(content);
  if (!record) return false;

  for (const flag of OWNER_FLAG_FIELDS) {
    if (typeof record[flag] === "boolean") {
      return record[flag] as boolean;
    }
  }

  const viewerId = toPositiveInt(currentUserId);
  if (!viewerId) return false;

  const ownerId = resolveContentOwnerId(content);
  if (!ownerId) return false;

  return ownerId === viewerId;
}
