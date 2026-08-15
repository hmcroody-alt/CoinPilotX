import * as Haptics from "expo-haptics";
import { useCallback, useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, AppState, Keyboard } from "react-native";

import {
  spatialMotionEnabled,
  spatialReelsEnabled,
  tiltNavigationEnabled,
  tiltParallaxEnabled
} from "../flags";
import { isCreateConsoleOpen, useCreateConsoleOpen } from "../createConsoleStore";
import { useTheme } from "../../theme/ThemeContext";
import { getDeviceMotionModule, isMotionAvailable } from "./motionAvailability";
import {
  calibrationComplete,
  createMotionMachine,
  processSample,
  setSuspension,
  touchEnd,
  touchStart,
  type MotionMachine,
  type MotionState,
  type TiltDirection
} from "./motionStateMachine";
import { getMotionSettings, hydrateMotionSettings, updateMotionSettings, useMotionSettings } from "./motionSettings";

const SENSOR_INTERVAL_MS = 80;
/** Low-pass smoothing factor (higher = snappier, noisier). */
const LOW_PASS_ALPHA = 0.25;
/** Samples averaged to capture the neutral holding-angle baseline. */
const CALIBRATION_SAMPLES = 12;
const GRAVITY = 9.81;

export interface TiltNavigationOptions {
  /**
   * The surface this hook serves. Typed as the literal `"reels"` on purpose.
   *
   * Reels is the only motion-enabled destination in the product, and this is
   * where that decision is enforced at compile time: mounting this hook from
   * Home, Messages, Create or anywhere else is a type error, not a runtime
   * behavior that has to be caught by review or by a device sitting in
   * somebody's hand. The parameter is otherwise unused — it exists to be
   * impossible to satisfy from the wrong screen.
   */
  surface: "reels";
  /** Surface is focused and its spatial pager is on screen. */
  enabled: boolean;
  /** Screen-level suspension (drawer open, composer open, comments sheet…). */
  suspended?: boolean;
  currentIndex: number;
  pageCount: number;
  /** Commit a tilt navigation to a concrete page index. */
  onCommit: (nextIndex: number, direction: TiltDirection) => void;
}

export interface TiltNavigation {
  /**
   * -1..1 preview progress for restrained parallax (consumer maps to a small
   * translateX). Driven with setValue — never re-renders per sample.
   */
  previewProgress: Animated.Value;
  /** Current machine state, for settings/tutorial UI and tests. */
  state: MotionState;
  /** Touch takeover hooks — wire to the pager's drag begin/end. */
  notifyTouchStart: () => void;
  notifyTouchEnd: () => void;
  /** Drop the stored baseline and recalibrate from the next samples. */
  recalibrate: () => void;
}

/**
 * Phone-tilt navigation for the Reels player.
 *
 * Subscribes to DeviceMotion ONLY when every gate passes: feature flags
 * (including `spatialReelsEnabled`, so motion cannot outlive spatial Reels),
 * user consent (onboarded + mode ≠ swipe-only), screen focus, screen reader
 * off, app foregrounded, keyboard closed, create console closed. All raw sensor
 * data stays in local variables — nothing is persisted or transmitted (mission
 * §20). Everything else is delegated to the pure state machine so behavior
 * stays deterministic and unit-tested.
 *
 * Home Feed once mounted this hook too. It no longer does, and the `surface`
 * option is typed so it cannot again.
 */
export function useTiltNavigation({
  enabled,
  suspended = false,
  currentIndex,
  pageCount,
  onCommit
}: TiltNavigationOptions): TiltNavigation {
  const settings = useMotionSettings();
  const createConsoleOpen = useCreateConsoleOpen();
  const { reduceMotion } = useTheme();
  const [state, setState] = useState<MotionState>("disabled");
  const [screenReaderOn, setScreenReaderOn] = useState(false);
  // `AppState.currentState` is not always populated at the moment a component
  // mounts — it can read null/undefined before the native module has reported
  // in. Seeding `false` from that would be wrong in the direction that fails
  // silently: motion would never start for a user who opened the app and went
  // straight to Reels, and would only appear after their first background/
  // foreground round trip. A component cannot mount while the app is in the
  // background, so "not yet known" is treated as active; the listener below
  // corrects it the instant the app actually leaves the foreground.
  const [appActive, setAppActive] = useState(
    AppState.currentState === undefined || AppState.currentState === null
      ? true
      : AppState.currentState === "active"
  );
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const [sensorAvailable, setSensorAvailable] = useState<boolean | null>(null);

  const previewProgress = useRef(new Animated.Value(0)).current;
  const machineRef = useRef<MotionMachine | null>(null);
  const filteredRef = useRef({ roll: 0, pitch: 0, instability: 0 });
  const calibrationRef = useRef<number[]>([]);

  const indexRef = useRef(currentIndex);
  indexRef.current = currentIndex;
  const pageCountRef = useRef(pageCount);
  pageCountRef.current = pageCount;
  const onCommitRef = useRef(onCommit);
  onCommitRef.current = onCommit;

  const parallaxOnly = settings.mode === "parallax";
  // `spatialReelsEnabled()` is part of the gate, not just the caller's concern:
  // motion exists to move the Reels pager, so turning spatial Reels off must
  // also stop the sensor rather than leave it feeding a pager that is no longer
  // horizontal. It transitively requires the console master.
  const flagsOn =
    spatialReelsEnabled() &&
    spatialMotionEnabled() &&
    (parallaxOnly ? tiltParallaxEnabled() : tiltNavigationEnabled());
  const consented = settings.onboarded && settings.mode !== "swipe-only";
  // Reduce Motion disables the entire motion layer, matching mission §19.
  const shouldListen =
    flagsOn && consented && enabled && appActive && !screenReaderOn && !reduceMotion;

  useEffect(() => {
    hydrateMotionSettings();
  }, []);

  // Environment listeners — cheap, active regardless of sensor state.
  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isScreenReaderEnabled().then((on) => {
      if (mounted) setScreenReaderOn(on);
    });
    const srSub = AccessibilityInfo.addEventListener("screenReaderChanged", setScreenReaderOn);
    const appSub = AppState.addEventListener("change", (next) => setAppActive(next === "active"));
    const showSub = Keyboard.addListener("keyboardDidShow", () => setKeyboardOpen(true));
    const hideSub = Keyboard.addListener("keyboardDidHide", () => setKeyboardOpen(false));
    return () => {
      mounted = false;
      srSub.remove();
      appSub.remove();
      showSub.remove();
      hideSub.remove();
    };
  }, []);

  useEffect(() => {
    if (!shouldListen) return;
    let mounted = true;
    isMotionAvailable().then((available) => {
      if (mounted) setSensorAvailable(available);
    });
    return () => {
      mounted = false;
    };
  }, [shouldListen]);

  const applyMachine = useCallback(
    (next: MotionMachine) => {
      machineRef.current = next;
      setState((prev) => (prev === next.state ? prev : next.state));
    },
    []
  );

  // External suspension reasons flow into the machine as named reasons.
  useEffect(() => {
    const machine = machineRef.current;
    if (!machine) return;
    let next = machine;
    next = setSuspension(next, "screen", suspended);
    next = setSuspension(next, "keyboard", keyboardOpen);
    next = setSuspension(next, "create-console", createConsoleOpen);
    if (next !== machine) applyMachine(next);
  }, [suspended, keyboardOpen, createConsoleOpen, applyMachine]);

  // Sensor subscription lifecycle.
  useEffect(() => {
    if (!shouldListen || sensorAvailable !== true) {
      machineRef.current = null;
      previewProgress.setValue(0);
      setState(!flagsOn || !consented ? "disabled" : sensorAvailable === false ? "unavailable" : "disabled");
      return;
    }

    const deviceMotion = getDeviceMotionModule();
    if (!deviceMotion) {
      setState("unavailable");
      return;
    }

    let machine = createMotionMachine(
      settings.mode,
      settings.sensitivity,
      true
    );
    // Stored baseline skips calibration; otherwise capture it live.
    let baseline = settings.neutralBaselineRad;
    calibrationRef.current = [];
    if (baseline !== null) machine = calibrationComplete(machine);
    machine = setSuspension(machine, "screen", suspended);
    machine = setSuspension(machine, "keyboard", keyboardOpen);
    machine = setSuspension(machine, "create-console", isCreateConsoleOpen());
    applyMachine(machine);

    deviceMotion.setUpdateInterval(SENSOR_INTERVAL_MS);
    const subscription = deviceMotion.addListener((measurement) => {
      const current = machineRef.current;
      if (!current) return;
      const rotation = measurement.rotation;
      if (!rotation) return;

      // gamma ≈ roll in portrait; beta ≈ pitch. Low-pass to tame jitter.
      const filtered = filteredRef.current;
      filtered.roll = filtered.roll + LOW_PASS_ALPHA * (rotation.gamma - filtered.roll);
      filtered.pitch = filtered.pitch + LOW_PASS_ALPHA * (rotation.beta - filtered.pitch);
      const accel = measurement.accelerationIncludingGravity;
      const magnitude = accel ? Math.sqrt(accel.x * accel.x + accel.y * accel.y + accel.z * accel.z) : GRAVITY;
      const deviation = Math.abs(magnitude - GRAVITY);
      filtered.instability = filtered.instability + LOW_PASS_ALPHA * (deviation - filtered.instability);

      // Calibration: average the first samples as the neutral holding angle.
      if (current.state === "calibrating") {
        calibrationRef.current.push(filtered.roll);
        if (calibrationRef.current.length >= CALIBRATION_SAMPLES) {
          const sum = calibrationRef.current.reduce((total, value) => total + value, 0);
          baseline = sum / calibrationRef.current.length;
          updateMotionSettings({ neutralBaselineRad: baseline });
          applyMachine(calibrationComplete(current));
        }
        return;
      }
      if (baseline === null) return;

      const { machine: next, effects } = processSample(
        current,
        {
          roll: filtered.roll - baseline,
          pitch: filtered.pitch - 0.6, // ≈ natural upright holding pitch
          instability: filtered.instability
        },
        Date.now()
      );
      if (next !== current) applyMachine(next);
      previewProgress.setValue(effects.previewProgress);

      if (effects.commit) {
        const delta = effects.commit === "right" ? 1 : -1;
        const target = indexRef.current + delta;
        if (target >= 0 && target < pageCountRef.current) {
          if (effects.haptic && getMotionSettings().hapticsEnabled) {
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
          }
          onCommitRef.current(target, effects.commit);
        }
      }
    });

    return () => {
      subscription.remove();
      machineRef.current = null;
      previewProgress.setValue(0);
    };
    // Rebuild the subscription when consent/mode/sensitivity/gates change.
  }, [
    shouldListen,
    sensorAvailable,
    settings.mode,
    settings.sensitivity,
    settings.neutralBaselineRad,
    flagsOn,
    consented,
    suspended,
    keyboardOpen,
    applyMachine,
    previewProgress
  ]);

  const notifyTouchStart = useCallback(() => {
    const machine = machineRef.current;
    if (!machine) return;
    previewProgress.setValue(0);
    applyMachine(touchStart(machine));
  }, [applyMachine, previewProgress]);

  const notifyTouchEnd = useCallback(() => {
    const machine = machineRef.current;
    if (!machine) return;
    applyMachine(touchEnd(machine));
  }, [applyMachine]);

  const recalibrate = useCallback(() => {
    updateMotionSettings({ neutralBaselineRad: null });
  }, []);

  return { previewProgress, state, notifyTouchStart, notifyTouchEnd, recalibrate };
}
