/**
 * The screen-side half of the launch gate.
 *
 * A screen asks the gate to run an action instead of navigating directly:
 *
 *   const gate = useLaunchGate();
 *   ...
 *   onPress={() => gate.open("business:events", "Events", () => navigate("BusinessOsEvents"))}
 *   ...
 *   <ComingSoonSheet target={gate.target} onDismiss={gate.dismiss} />
 *
 * The point of routing every tap through one call is that a screen can no longer
 * navigate somewhere gated by forgetting a conditional — the conditional is the
 * navigation. `open` is the only way in, and it decides.
 */

import { useCallback, useState } from "react";
import { AccessibilityInfo } from "react-native";
import { useTranslation } from "../i18n";
import { useTheme } from "../theme/ThemeContext";
import { useLogiNexusReducedMotion as useOsReducedMotion } from "../theme/logiNexusMotion";
import type { ComingSoonTarget } from "./ComingSoonSheet";
import { isLaunchReady, readinessOf, type LaunchModuleId, type ReadinessState } from "./readiness";

export function useLaunchGate() {
  const [target, setTarget] = useState<ComingSoonTarget | null>(null);

  const open = useCallback((id: LaunchModuleId, label: string, run: () => void) => {
    if (isLaunchReady(id)) {
      run();
      return;
    }
    setTarget({ id, label });
    // Screen-reader users get the message spoken even if focus does not move to
    // the modal in time. `accessibilityRole="alert"` on the sheet covers most
    // cases; this covers the rest, and is a no-op when nothing is listening.
    AccessibilityInfo.announceForAccessibility?.(label);
  }, []);

  const dismiss = useCallback(() => setTarget(null), []);

  return { target, open, dismiss };
}

/**
 * Accessibility and badge text for a module, in one place.
 *
 * Two rules the brief is explicit about and that are easy to break separately:
 *
 *   - Readiness is never communicated by colour alone. Every locked card carries
 *     a text badge, and its accessibility label says "Coming soon" outright, so
 *     the state survives greyscale, colour blindness and a screen reader.
 *   - BUILDING and COMING_SOON read differently to a sighted user ("Building" vs
 *     "Coming Soon") but resolve to the same promise. Neither ever says
 *     "unavailable" or "not implemented".
 */
export function useLaunchCopy() {
  const { t } = useTranslation();

  const badge = useCallback(
    (state: ReadinessState) =>
      state === "BUILDING" ? t("commerce:launch.statusBuilding") : t("commerce:launch.statusComingSoon"),
    [t]
  );

  const accessibility = useCallback(
    (id: LaunchModuleId, label: string, blurb?: string) => {
      const state = readinessOf(id);
      if (state === "READY") {
        return { accessibilityLabel: blurb ? `${label}. ${blurb}` : label, accessibilityHint: undefined };
      }
      return {
        // "Seller Analytics. Coming soon." — the state is in the label, not only
        // in a hint, because hints are off by default on iOS.
        accessibilityLabel: t("commerce:launch.lockedLabel", { module: label }),
        accessibilityHint: t("commerce:launch.lockedHint")
      };
    },
    [t]
  );

  return { badge, accessibility };
}

/**
 * Whether decorative motion may run.
 *
 * Combines the two sources that can independently say no: the OS setting
 * (Settings › Accessibility › Motion) and PulseSoc's own in-app preference.
 * Honouring only one of them is the usual bug — the in-app toggle is the one
 * users of this app actually reach for, and the OS one is what an accessibility
 * audit checks.
 */
export function useLaunchMotionEnabled() {
  const theme = useTheme();
  // `theme.reduceMotion` is PulseSoc's own preference (Settings › Accessibility).
  // `useOsReducedMotion` is the system one. The alias is because the hook's name
  // is historical — its body reads `AccessibilityInfo.isReduceMotionEnabled` and
  // subscribes to `reduceMotionChanged`, with nothing subsystem-specific in it,
  // so importing it is better than keeping a third copy of that subscription.
  const osReducedMotion = useOsReducedMotion();
  return !theme.reduceMotion && !osReducedMotion;
}
