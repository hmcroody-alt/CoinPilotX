/**
 * Backend adapters for the settings platform.
 *
 * Transport rules:
 *  - The preference read never throws. A failed GET returns `null` and the
 *    caller falls back to the persisted local snapshot, so Settings always
 *    renders. This is safe because a stale-but-real snapshot is a truthful
 *    answer to "what are my settings".
 *  - List reads (blocked, muted, sessions) DO throw. There is no local snapshot
 *    to fall back on, so swallowing the error would render an empty list — and
 *    an empty list here is a positive claim: "you have blocked nobody", "no
 *    other device is signed in". Telling a user their account has no other
 *    active sessions when we simply couldn't reach the server is the wrong
 *    failure mode on a security surface. Callers catch and show a retry.
 *  - Writes do throw. The store needs the failure in order to roll back the
 *    optimistic update and surface an error to the user.
 */

import { pulseApi, PulseApiError } from "../api/pulseApi";
import { normalizePreferences, Preferences } from "./schema";

const SETTINGS_PATH = "/api/pulse/mobile/settings";

export type RemoteSettingsEnvelope = {
  preferences: Preferences;
  /** Server revision used for last-write-wins conflict detection. */
  revision: number;
  updatedAt: string | null;
};

function readRevision(payload: Record<string, unknown>): number {
  const value = payload.revision ?? payload.version ?? 0;
  const parsed = typeof value === "number" ? value : Number.parseInt(String(value), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function toEnvelope(payload: unknown): RemoteSettingsEnvelope {
  const raw = (payload && typeof payload === "object" ? payload : {}) as Record<string, unknown>;
  // Accept both `{preferences: {...}}` and a bare preferences object so the
  // client keeps working if the endpoint is ever flattened.
  const source = raw.preferences && typeof raw.preferences === "object" ? raw.preferences : raw;
  const updatedAt = typeof raw.updated_at === "string" ? raw.updated_at : typeof raw.updatedAt === "string" ? raw.updatedAt : null;
  return {
    preferences: normalizePreferences(source),
    revision: readRevision(raw),
    updatedAt
  };
}

/** Fetch the authoritative preference set. Returns `null` on any failure. */
export async function fetchRemotePreferences(): Promise<RemoteSettingsEnvelope | null> {
  try {
    const payload = await pulseApi<unknown>(SETTINGS_PATH, { method: "GET" });
    return toEnvelope(payload);
  } catch {
    return null;
  }
}

export class PreferenceSyncError extends Error {
  /** True when retrying the same request is pointless (validation, auth). */
  permanent: boolean;
  status: number;

  constructor(message: string, status: number, permanent: boolean) {
    super(message);
    this.name = "PreferenceSyncError";
    this.status = status;
    this.permanent = permanent;
  }
}

/**
 * What each status means to somebody looking at a settings screen.
 *
 * The server's own prose is written for whoever is holding the failing request,
 * which for most of these is a developer. Passing it through unedited is how
 * "The requested PulseSoc service was not found." — the generic 404 body from
 * `bot.py` — ended up in front of a user who had done nothing but tap a switch.
 * It is accurate and completely unactionable.
 *
 * Two rules govern this table. Where the user can do something, say what. Where
 * they cannot, say that plainly instead of implying they can, and never dress a
 * server-side defect up as something they got wrong.
 */
function settingsMessageFor(status: number, serverMessage: string): string {
  switch (status) {
    case 401:
      return "Your session has expired. Sign in again to change this setting.";
    case 403:
      return "This account isn't allowed to change that setting.";
    case 404:
      // Deliberately not softened into "try again later". A 404 here means the
      // settings service is not answering on the server this build is pointed
      // at, which no amount of retrying will fix and which the user cannot
      // cause. Naming it is what turns a mysterious banner into a bug report.
      return "Settings can't be saved right now — this version of PulseSoc can't reach the settings service. Your change was not saved.";
    case 409:
      return "This setting was changed on another device. Pull down to refresh, then try again.";
    case 422:
      return serverMessage || "That value isn't valid for this setting.";
    case 429:
      return "Too many changes at once. We'll finish saving in a moment.";
    default:
      if (status >= 500) return "PulseSoc had a problem saving that. We'll retry automatically.";
      return serverMessage || "Could not save your change.";
  }
}

function toSyncError(error: unknown): PreferenceSyncError {
  if (error instanceof PulseApiError) {
    // 4xx means the payload or the session is the problem and repeating the
    // request cannot help. 408 and 429 are the exceptions: both are invitations
    // to try the same request again later.
    const permanent = error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 429;
    const message = settingsMessageFor(error.status, String(error.message || "").trim());
    if (error.status === 404) {
      // Loud on purpose. This is the one status in this function that indicates
      // a broken deployment rather than a broken request, and it is otherwise
      // indistinguishable in the logs from an ordinary rejected write.
      console.error(
        `[settings] ${error.status} from the settings service — the endpoint is missing on the server this build calls. ` +
          "This is a deployment defect, not a user error."
      );
    }
    return new PreferenceSyncError(message, error.status, permanent);
  }
  return new PreferenceSyncError("You appear to be offline. We'll retry automatically.", 0, false);
}

/**
 * Persist a partial preference patch.
 *
 * Sends only the changed groups rather than the whole document — this keeps two
 * devices editing different sections from clobbering each other, and keeps the
 * request small enough to succeed on poor connections.
 */
export async function pushPreferencePatch(patch: Partial<Preferences>, revision: number): Promise<RemoteSettingsEnvelope> {
  try {
    const payload = await pulseApi<unknown>(SETTINGS_PATH, {
      method: "PATCH",
      body: JSON.stringify({ preferences: patch, revision })
    });
    return toEnvelope(payload);
  } catch (error) {
    throw toSyncError(error);
  }
}

/* -------------------------------------------------------------------------- */
/*                       Relationship lists (block / mute)                     */
/* -------------------------------------------------------------------------- */

export type RelationshipUser = {
  id: number;
  username: string;
  displayName: string;
  avatarUrl: string | null;
  /** ISO timestamp of when the block/mute was applied. */
  since: string | null;
};

function toRelationshipUser(raw: unknown): RelationshipUser | null {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const id = Number(value.id ?? value.user_id ?? 0);
  if (!Number.isFinite(id) || id <= 0) return null;
  const username = String(value.username ?? value.handle ?? "").trim();
  return {
    id,
    username,
    displayName: String((value.display_name ?? value.displayName ?? value.name ?? username) || `User ${id}`).trim(),
    avatarUrl: typeof value.avatar_url === "string" ? value.avatar_url : typeof value.avatarUrl === "string" ? value.avatarUrl : null,
    since: typeof value.created_at === "string" ? value.created_at : typeof value.since === "string" ? value.since : null
  };
}

function toRelationshipList(payload: unknown): RelationshipUser[] {
  const raw = (payload && typeof payload === "object" ? payload : {}) as Record<string, unknown>;
  const list = Array.isArray(raw.users) ? raw.users : Array.isArray(raw.items) ? raw.items : Array.isArray(payload) ? payload : [];
  return list.map(toRelationshipUser).filter((entry): entry is RelationshipUser => Boolean(entry));
}

export async function fetchBlockedUsers(): Promise<RelationshipUser[]> {
  try {
    return toRelationshipList(await pulseApi<unknown>("/api/pulse/mobile/settings/blocked", { method: "GET" }));
  } catch (error) {
    throw toSyncError(error);
  }
}

export async function fetchMutedUsers(): Promise<RelationshipUser[]> {
  try {
    return toRelationshipList(await pulseApi<unknown>("/api/pulse/mobile/settings/muted", { method: "GET" }));
  } catch (error) {
    throw toSyncError(error);
  }
}

export async function setBlocked(userId: number, blocked: boolean): Promise<void> {
  try {
    await pulseApi("/api/pulse/mobile/settings/blocked", {
      method: blocked ? "POST" : "DELETE",
      body: JSON.stringify({ user_id: userId })
    });
  } catch (error) {
    throw toSyncError(error);
  }
}

export async function setMuted(userId: number, muted: boolean): Promise<void> {
  try {
    await pulseApi("/api/pulse/mobile/settings/muted", {
      method: muted ? "POST" : "DELETE",
      body: JSON.stringify({ user_id: userId })
    });
  } catch (error) {
    throw toSyncError(error);
  }
}

/* -------------------------------------------------------------------------- */
/*                            Sessions and devices                             */
/* -------------------------------------------------------------------------- */

export type ActiveSession = {
  id: string;
  deviceName: string;
  platform: string;
  location: string | null;
  ipAddress: string | null;
  lastActiveAt: string | null;
  current: boolean;
};

function toSession(raw: unknown): ActiveSession | null {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const id = String(value.id ?? value.session_id ?? "").trim();
  if (!id) return null;
  return {
    id,
    deviceName: String(value.device_name ?? value.deviceName ?? value.device ?? "Unknown device").trim(),
    platform: String(value.platform ?? value.os ?? "").trim(),
    location: typeof value.location === "string" ? value.location : null,
    ipAddress: typeof value.ip_address === "string" ? value.ip_address : typeof value.ip === "string" ? value.ip : null,
    lastActiveAt:
      typeof value.last_active_at === "string"
        ? value.last_active_at
        : typeof value.lastActiveAt === "string"
        ? value.lastActiveAt
        : null,
    current: Boolean(value.current ?? value.is_current ?? false)
  };
}

export async function fetchActiveSessions(): Promise<ActiveSession[]> {
  try {
    const payload = await pulseApi<unknown>("/api/pulse/mobile/settings/sessions", { method: "GET" });
    const raw = (payload && typeof payload === "object" ? payload : {}) as Record<string, unknown>;
    const list = Array.isArray(raw.sessions) ? raw.sessions : Array.isArray(payload) ? payload : [];
    return list.map(toSession).filter((entry): entry is ActiveSession => Boolean(entry));
  } catch (error) {
    throw toSyncError(error);
  }
}

export async function revokeSession(sessionId: string): Promise<void> {
  try {
    await pulseApi("/api/pulse/mobile/settings/sessions/revoke", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId })
    });
  } catch (error) {
    throw toSyncError(error);
  }
}

export const __testing = { toEnvelope, toSyncError, toRelationshipList, toSession, settingsMessageFor, SETTINGS_PATH };
