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
import { isLaunchReady, type LaunchModuleId, type ReadinessState } from "./readiness";

export function useLaunchGate() {
  const [target, setTarget] = useState<ComingSoonTarget | null>(null);

  /**
   * `bodyKey` overrides only the sheet's second sentence — see
   * `ComingSoonTarget.bodyKey`. Omit it and every gated module gets the one
   * shared wording, which is still the right answer for almost all of them.
   *
   * `run` is optional, and an absent one is a refusal rather than a no-op. That
   * is the caller's way of saying "the gate would allow this, and there is
   * nowhere to send them" — a routeless module in a section landing, say. The
   * alternative shape, a `run` that returns early when it finds no destination,
   * hides the refusal inside the callback where the gate cannot see it and the
   * user gets a dead tap. See `sectionCapabilities.ts`.
   */
  const open = useCallback((id: LaunchModuleId, label: string, run?: () => void, bodyKey?: string) => {
    if (run && isLaunchReady(id)) {
      run();
      return;
    }
    setTarget({ id, label, bodyKey });
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

  /**
   * Takes the state rather than looking it up, because every caller already has
   * one in hand — they compute it to decide whether to draw a lock and which
   * badge to show. Looking it up again here made this the second opinion on the
   * same question, and a second opinion is only ever noticed when it differs.
   * It matters for a row whose state is not `readinessOf(id)` alone: a
   * capability with no destination is locked while its id reads READY.
   */
  const accessibility = useCallback(
    (state: ReadinessState, label: string, blurb?: string) => {
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
