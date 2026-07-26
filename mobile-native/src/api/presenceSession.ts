import { AppState, AppStateStatus, Platform } from "react-native";

import { PulseApiError } from "./pulseApi";
import {
  connectPresence,
  disconnectPresence,
  heartbeatPresence,
  PresenceActivity,
  setPresenceActivity as postPresenceActivity
} from "./presence";

/**
 * The device-side heartbeat runner for the unified presence service.
 *
 * This module's only job is to keep a heartbeat flowing. It never decides
 * whether anyone — including the local user — is online. The server derives
 * liveness at read time from unexpired heartbeats, which is what makes mobile
 * tractable: if iOS or Android kills this process without warning, if the
 * network drops, or if the user force-quits, no cleanup message needs to
 * arrive. The session simply lapses and the user becomes offline on its own.
 *
 * `stop()` therefore exists to make that transition *immediate*, not to make it
 * *happen*. Correctness never depends on it running.
 */

const DEFAULT_INTERVAL_SECONDS = 45;
const MIN_INTERVAL_SECONDS = 10;
/** Never let a bad server value stall the heartbeat past any sane grace period. */
const MAX_INTERVAL_SECONDS = 300;

type PresenceRunner = {
  started: boolean;
  sessionId: string;
  deviceId: string;
  intervalSeconds: number;
  timer: ReturnType<typeof setTimeout> | null;
  subscription: { remove: () => void } | null;
  inFlight: Promise<void> | null;
  activity: PresenceActivity;
  activityContext: string;
};

const runner: PresenceRunner = {
  started: false,
  sessionId: "",
  deviceId: "",
  intervalSeconds: DEFAULT_INTERVAL_SECONDS,
  timer: null,
  subscription: null,
  inFlight: null,
  activity: "idle",
  activityContext: ""
};

/**
 * Stable-for-this-launch device identifier.
 *
 * Deliberately not persisted to disk. A fresh id per launch is harmless: the
 * server revokes any prior session for the same (user, device) pair on connect,
 * and sessions expire on their own regardless. Persisting one would buy nothing
 * and would mean writing a tracking identifier to storage.
 */
function ensureDeviceId(): string {
  if (!runner.deviceId) {
    const random = Math.random().toString(36).slice(2, 10);
    runner.deviceId = `native-${Platform.OS}-${Date.now().toString(36)}-${random}`;
  }
  return runner.deviceId;
}

function deviceLabel(): string {
  if (Platform.OS === "ios") return Platform.isPad ? "iPad" : "iPhone";
  if (Platform.OS === "android") return "Android";
  return "Mobile";
}

function platformToken(): string {
  if (Platform.OS === "ios") return Platform.isPad ? "ipad" : "iphone";
  if (Platform.OS === "android") return "android";
  return "mobile";
}

/**
 * Adopt the cadence the *server* asked for.
 *
 * Server-driven timing means the heartbeat rate and the grace period can be
 * retuned centrally without shipping a new build, and it keeps web, iOS and
 * Android in agreement about what "current" means. The clamp guards against a
 * malformed value silently disabling the heartbeat.
 */
function applyInterval(seconds: unknown): void {
  const value = Number(seconds || 0);
  if (!Number.isFinite(value) || value <= 0) return;
  runner.intervalSeconds = Math.min(MAX_INTERVAL_SECONDS, Math.max(MIN_INTERVAL_SECONDS, value));
}

function clearTimer(): void {
  if (runner.timer) {
    clearTimeout(runner.timer);
    runner.timer = null;
  }
}

function scheduleNext(): void {
  clearTimer();
  if (!runner.started) return;
  runner.timer = setTimeout(() => {
    void beat();
  }, runner.intervalSeconds * 1000);
}

async function openSession(): Promise<void> {
  const session = await connectPresence({
    deviceId: ensureDeviceId(),
    deviceLabel: deviceLabel(),
    platform: platformToken()
  });
  runner.sessionId = String(session?.session_id || "");
  applyInterval(session?.heartbeat_interval_seconds);
}

/**
 * Send one heartbeat, opening or re-opening the session as needed.
 *
 * Concurrent calls collapse onto the same promise so a foreground event landing
 * next to a scheduled tick cannot open two sessions for one device.
 */
async function beat(): Promise<void> {
  if (!runner.started) return;
  if (runner.inFlight) return runner.inFlight;
  runner.inFlight = (async () => {
    try {
      if (!runner.sessionId) {
        await openSession();
        return;
      }
      const session = await heartbeatPresence({
        sessionId: runner.sessionId,
        deviceId: runner.deviceId,
        activity: runner.activity,
        activityContext: runner.activityContext
      });
      applyInterval(session?.heartbeat_interval_seconds);
      // The server reports a lapsed session rather than silently accepting the
      // beat — typically because the app sat in the background past the grace
      // period. Re-connecting right here is what makes reconnect detection
      // immediate instead of costing a full interval.
      if (session?.ok === false || (session as { reconnect?: boolean })?.reconnect) {
        runner.sessionId = "";
        await openSession();
      }
    } catch (error) {
      // 401 means the login is gone; there is nothing left to heartbeat for and
      // retrying would just generate noise. Every other failure is treated as
      // transient: keep the schedule alive and let presence lapse naturally if
      // the outage outlives the grace period.
      if (error instanceof PulseApiError && error.status === 401) {
        runner.started = false;
        runner.sessionId = "";
        clearTimer();
        return;
      }
      runner.sessionId = "";
    } finally {
      runner.inFlight = null;
      scheduleNext();
    }
  })();
  return runner.inFlight;
}

/**
 * Report what the user is doing right now (typing, in a call, hosting a Live).
 *
 * Transient activities carry a short server-side TTL, so a typing indicator
 * cannot stick if the app dies mid-keystroke. Callers should still send "idle"
 * when they can, but correctness does not depend on it — which is precisely why
 * a failure here is swallowed rather than surfaced.
 */
export async function reportPresenceActivity(activity: PresenceActivity, context = ""): Promise<void> {
  runner.activity = activity;
  runner.activityContext = context;
  if (!runner.started || !runner.sessionId) return;
  try {
    await postPresenceActivity({ sessionId: runner.sessionId, activity, activityContext: context });
  } catch {
    // Activity is decorative relative to online/offline. Never surface it.
  }
}

/**
 * Foreground/background transitions.
 *
 * Backgrounding stops the heartbeat instead of disconnecting: a user who checks
 * another app for ten seconds should not flicker offline, and the grace period
 * covers exactly that. A longer absence lets the session lapse on its own,
 * which is the correct outcome and needs no cooperation from the OS.
 */
function onAppStateChange(next: AppStateStatus): void {
  if (!runner.started) return;
  if (next === "active") {
    void beat();
    return;
  }
  clearTimer();
  runner.activity = "idle";
  runner.activityContext = "";
  void reportPresenceActivity("idle", "");
}

/** Begin heart-beating for the signed-in user. Safe to call repeatedly. */
export function startPresenceSession(): void {
  if (runner.started) return;
  runner.started = true;
  ensureDeviceId();
  if (!runner.subscription) {
    runner.subscription = AppState.addEventListener("change", onAppStateChange);
  }
  void beat();
}

/** Stop heart-beating and drop the session immediately, e.g. on sign-out. */
export async function stopPresenceSession(): Promise<void> {
  if (!runner.started) return;
  runner.started = false;
  clearTimer();
  if (runner.subscription) {
    runner.subscription.remove();
    runner.subscription = null;
  }
  const sessionId = runner.sessionId;
  runner.sessionId = "";
  runner.activity = "idle";
  runner.activityContext = "";
  if (!sessionId) return;
  try {
    await disconnectPresence({ sessionId });
  } catch {
    // Best effort. If this never lands the session still expires after the
    // grace period, which is the entire point of expiry-on-read.
  }
}

/** Exposed for diagnostics and tests. */
export function presenceSessionState() {
  return {
    started: runner.started,
    sessionId: runner.sessionId,
    deviceId: runner.deviceId,
    intervalSeconds: runner.intervalSeconds,
    activity: runner.activity
  };
}
