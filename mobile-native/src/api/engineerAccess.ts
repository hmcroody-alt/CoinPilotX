import { pulseApi, PulseApiError } from "./pulseApi";
import {
  clearEngineerAccess,
  engineerAccessDeviceId,
  setEngineerAccess
} from "../security/engineerAccessSession";

/**
 * Client half of the engineer-access gate.
 *
 * This module can only *ask*. It holds no passcode, no hash, and no policy: the
 * server decides identity, secret, lockout, and grant lifetime. A patched build
 * that stubs `verifyEngineerAccess` to return `authorized: true` still receives
 * 403s from every protected route, because those routes check the signed grant
 * this endpoint issues — which a client cannot mint.
 */

export type EngineerAccessOutcome =
  | { authorized: true; expiresAt: number; scope: string[] }
  | { authorized: false; retryAfterSeconds: number; requiresReauthentication: boolean; unreachable?: boolean };

type VerifyResponse = {
  ok?: boolean;
  authorized?: boolean;
  grant?: string;
  expires_at?: number;
  scope?: string[];
  retry_after_seconds?: number;
  requires_reauthentication?: boolean;
};

export type EngineerAccessStatus = {
  active: boolean;
  expiresAt: number | null;
  scope: string[];
  lockedSecondsRemaining: number;
  requiresReauthentication: boolean;
};

/**
 * Submit a passcode attempt.
 *
 * `passcode` is a parameter and never a field: it is not stored on any object,
 * not attached to the returned outcome, and not included in any thrown error.
 * The caller is expected to drop its own copy immediately after awaiting.
 */
export async function verifyEngineerAccess(userId: number, passcode: string): Promise<EngineerAccessOutcome> {
  try {
    const response = await pulseApi<VerifyResponse>("/api/internal/engineer-access/verify", {
      method: "POST",
      body: JSON.stringify({ passcode, device_id: engineerAccessDeviceId() })
    });
    if (response.authorized && response.grant) {
      setEngineerAccess(userId, {
        token: response.grant,
        expiresAt: Number(response.expires_at || 0),
        scope: response.scope || []
      });
      return { authorized: true, expiresAt: Number(response.expires_at || 0), scope: response.scope || [] };
    }
    return { authorized: false, retryAfterSeconds: 0, requiresReauthentication: false };
  } catch (error) {
    // A denial arrives as a 403 carrying the lockout countdown. Anything else —
    // offline, 5xx, timeout — is also treated as "not authorized", so losing the
    // network can never be mistaken for a grant.
    const details = error instanceof PulseApiError ? (error.details as VerifyResponse | undefined) : undefined;
    const unreachable = error instanceof PulseApiError && error.status >= 500;
    return {
      authorized: false,
      retryAfterSeconds: Number(details?.retry_after_seconds || 0),
      requiresReauthentication: Boolean(details?.requires_reauthentication),
      unreachable
    };
  }
}

/**
 * Re-check standing with the server. Called when the app returns to the
 * foreground so a revoked grant or a lockout started on another device is
 * reflected without an app restart.
 */
export async function getEngineerAccessStatus(): Promise<EngineerAccessStatus> {
  try {
    const response = await pulseApi<{
      active?: boolean;
      expires_at?: number | null;
      scope?: string[];
      locked_seconds_remaining?: number;
      requires_reauthentication?: boolean;
    }>("/api/internal/engineer-access/session");
    if (!response.active) clearEngineerAccess();
    return {
      active: Boolean(response.active),
      expiresAt: response.expires_at ?? null,
      scope: response.scope || [],
      lockedSecondsRemaining: Number(response.locked_seconds_remaining || 0),
      requiresReauthentication: Boolean(response.requires_reauthentication)
    };
  } catch {
    // Do not clear on a transient failure: a flaky network should not eject an
    // engineer mid-task. The grant still expires on its own, and every protected
    // request is independently checked server-side.
    return { active: false, expiresAt: null, scope: [], lockedSecondsRemaining: 0, requiresReauthentication: false };
  }
}
