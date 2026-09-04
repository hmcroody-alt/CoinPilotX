/**
 * Accessibility snapshot owner (Phase 34).
 *
 * One shared reader for OS accessibility state (screen reader, reduce
 * motion) instead of per-screen AccessibilityInfo wiring. Values are
 * cached and kept fresh by OS change events; readers are synchronous so
 * render paths never await.
 */
import { AccessibilityInfo } from "react-native";

type A11ySnapshot = {
  screenReaderEnabled: boolean;
  reduceMotionEnabled: boolean;
};

const snapshot: A11ySnapshot = {
  screenReaderEnabled: false,
  reduceMotionEnabled: false
};

type Listener = (next: A11ySnapshot) => void;
const listeners = new Set<Listener>();
let started = false;

function emit() {
  const copy = { ...snapshot };
  listeners.forEach((listener) => listener(copy));
}

/** Idempotent: safe to call from app bootstrap and from any consumer. */
export function startA11yMonitor(): void {
  if (started) return;
  started = true;
  AccessibilityInfo.isScreenReaderEnabled()
    .then((value) => {
      snapshot.screenReaderEnabled = value;
      emit();
    })
    .catch(() => undefined);
  AccessibilityInfo.isReduceMotionEnabled()
    .then((value) => {
      snapshot.reduceMotionEnabled = value;
      emit();
    })
    .catch(() => undefined);
  AccessibilityInfo.addEventListener("screenReaderChanged", (value) => {
    snapshot.screenReaderEnabled = value;
    emit();
  });
  AccessibilityInfo.addEventListener("reduceMotionChanged", (value) => {
    snapshot.reduceMotionEnabled = value;
    emit();
  });
}

/** Synchronous cached read — call after startA11yMonitor(). */
export function a11ySnapshot(): A11ySnapshot {
  return { ...snapshot };
}

export function onA11yChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Animations should be skipped when the user asked the OS for less motion. */
export function motionAllowed(): boolean {
  return !snapshot.reduceMotionEnabled;
}
