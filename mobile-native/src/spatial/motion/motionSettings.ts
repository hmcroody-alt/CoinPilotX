import AsyncStorage from "@react-native-async-storage/async-storage";
import { useSyncExternalStore } from "react";

import type { MotionMode, MotionSensitivity } from "./motionStateMachine";

/**
 * Persisted user preferences for Spatial Motion.
 *
 * Consent model: `mode` defaults to "swipe-only" and `onboarded` defaults to
 * false, so motion NEVER activates silently — sensors stay untouched until the
 * user completes onboarding and explicitly picks a motion mode. Turning the
 * feature flags on ships the capability, not the behavior.
 *
 * Privacy: only these high-level preferences are stored. Raw sensor data is
 * processed in memory and never persisted or transmitted (mission §20).
 *
 * There is no longer a scope preference. Reels is the only surface that runs
 * motion, so "where does tilt apply" is not a question the user is asked. See
 * {@link LegacyMotionScope} for what happens to values already on disk.
 */

/**
 * The `scope` values written by builds that offered Home Feed motion.
 *
 * Retained only to READ old records. Home Feed motion shipped to testers and was
 * then withdrawn as a product decision, so a real device can hold any of these.
 * Migration rules, applied in {@link sanitize} on every read:
 *
 *   - `"feed"` — the user opted into motion on the Feed *and nowhere else*.
 *     Carrying that consent over to Reels would turn tilt on somewhere they
 *     never agreed to, so their mode drops to `swipe-only`. Re-enabling is one
 *     tap in Settings; an unexpected page-turn is not undoable in the same way.
 *   - `"reels"` / `"both"` — Reels motion was already wanted. Mode is kept.
 *   - anything else, including absent — no opinion on record, mode is kept.
 *
 * `onboarded` survives every branch, so nobody is re-prompted for a decision
 * they already made. The migration needs no version marker to stay idempotent:
 * the first write after a read persists an object with no `scope` key, and a
 * record without `scope` takes the no-op branch from then on.
 */
export type LegacyMotionScope = "feed" | "reels" | "both";

export interface MotionSettings {
  mode: MotionMode;
  sensitivity: MotionSensitivity;
  hapticsEnabled: boolean;
  /** Calibrated neutral roll baseline, radians. Null = not calibrated yet. */
  neutralBaselineRad: number | null;
  /** True once the user has completed (or explicitly skipped) onboarding. */
  onboarded: boolean;
}

export const DEFAULT_MOTION_SETTINGS: MotionSettings = {
  mode: "swipe-only",
  sensitivity: "medium",
  hapticsEnabled: true,
  neutralBaselineRad: null,
  onboarded: false
};

const STORAGE_KEY = "pulsesoc.spatialMotion.settings.v1";

let settings: MotionSettings = DEFAULT_MOTION_SETTINGS;
let hydrated = false;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

function sanitize(raw: unknown): MotionSettings {
  if (!raw || typeof raw !== "object") return DEFAULT_MOTION_SETTINGS;
  const value = raw as Partial<MotionSettings> & { scope?: unknown };
  const mode: MotionMode =
    value.mode === "tilt" || value.mode === "parallax" ? value.mode : "swipe-only";
  return {
    // Feed-only consent does not transfer to Reels — see LegacyMotionScope.
    mode: value.scope === "feed" ? "swipe-only" : mode,
    sensitivity:
      value.sensitivity === "low" || value.sensitivity === "high" ? value.sensitivity : "medium",
    hapticsEnabled: value.hapticsEnabled !== false,
    neutralBaselineRad:
      typeof value.neutralBaselineRad === "number" && Number.isFinite(value.neutralBaselineRad)
        ? value.neutralBaselineRad
        : null,
    onboarded: value.onboarded === true
  };
}

/** Load persisted settings once. Safe to call repeatedly. */
export async function hydrateMotionSettings(): Promise<MotionSettings> {
  if (hydrated) return settings;
  try {
    const stored = await AsyncStorage.getItem(STORAGE_KEY);
    if (stored) settings = sanitize(JSON.parse(stored));
  } catch {
    // Corrupt or unavailable storage: stay on safe defaults (swipe-only).
  }
  hydrated = true;
  emit();
  return settings;
}

export function getMotionSettings(): MotionSettings {
  return settings;
}

export async function updateMotionSettings(patch: Partial<MotionSettings>): Promise<MotionSettings> {
  settings = sanitize({ ...settings, ...patch });
  emit();
  try {
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Persistence failure is non-fatal; in-memory value still applies.
  }
  return settings;
}

export function useMotionSettings(): MotionSettings {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => settings,
    () => settings
  );
}

/** Test-only: reset in-memory state without touching storage. */
export function __resetMotionSettingsForTests() {
  settings = DEFAULT_MOTION_SETTINGS;
  hydrated = false;
}
