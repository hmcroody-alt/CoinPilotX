/**
 * Haptics owner (Phase 3) — the single haptic feedback service.
 *
 * Import this module instead of expo-haptics. The regression guard fails
 * if new direct expo-haptics imports appear outside the legacy baseline.
 *
 * Honors the user's accessibility.hapticFeedback preference: the settings
 * store calls `setHapticsEnabled` whenever the snapshot resolves, so every
 * call site gets the preference for free.
 */
import * as ExpoHaptics from "expo-haptics";
import { Platform } from "react-native";

export type HapticTone = "light" | "medium" | "success" | "warning" | "error" | "selection";

let enabled = true;

/** Wired from the preferences store; do not call from screens. */
export function setHapticsEnabled(value: boolean): void {
  enabled = value;
}

export function hapticsEnabled(): boolean {
  return enabled;
}

/** Fire-and-forget; never throws, never blocks UI. */
export function haptic(tone: HapticTone): void {
  if (!enabled || Platform.OS === "web") return;
  try {
    switch (tone) {
      case "light":
        void ExpoHaptics.impactAsync(ExpoHaptics.ImpactFeedbackStyle.Light);
        break;
      case "medium":
        void ExpoHaptics.impactAsync(ExpoHaptics.ImpactFeedbackStyle.Medium);
        break;
      case "success":
        void ExpoHaptics.notificationAsync(ExpoHaptics.NotificationFeedbackType.Success);
        break;
      case "warning":
        void ExpoHaptics.notificationAsync(ExpoHaptics.NotificationFeedbackType.Warning);
        break;
      case "error":
        void ExpoHaptics.notificationAsync(ExpoHaptics.NotificationFeedbackType.Error);
        break;
      case "selection":
        void ExpoHaptics.selectionAsync();
        break;
    }
  } catch {
    // Haptics must never break a user flow.
  }
}
