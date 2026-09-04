/**
 * The screen-level premium hard lock.
 *
 * Wrap a premium surface's body in this component and three things become true
 * at once: a deep link into the screen hits the same gate as tile navigation
 * (the gate lives inside the screen, not in the navigator), a cached screen
 * re-evaluates on every foreground because the gate reloads the canonical
 * answer when the app becomes active, and the locked branch never mounts the
 * body — so a locked screen fires zero feature requests instead of firing them
 * and painting their failures.
 *
 * Three states, three renders, none of them lies:
 *
 *   resolving     spinner. We have not heard from the server yet.
 *   unavailable   "we can't confirm your membership" + retry. NOT "you are on
 *                 Free" — that copy shown to a paying member during an outage
 *                 is the exact failure `canonicalTier.ts` exists to prevent.
 *   resolved      either the body (with a truthful trial countdown when the
 *                 grant is a trial) or the upsell panel.
 *
 * The decision itself is `tierSatisfies(answer, minimum)` — the same canonical
 * answer the navigation drawer uses, fetched once and shared via
 * `useCanonicalTier`. Nothing here reads `plan`, `premium_status` or any other
 * raw account field; the server's resolver is the only authority and this
 * component is only its renderer.
 */

import { ReactNode, useCallback, useEffect, useState } from "react";
import { ActivityIndicator, AppState, Pressable, ScrollView, Text, View } from "react-native";

import { PremiumUpsellPanel } from "../components/crypto/PremiumUpsellPanel";
import { useTranslation } from "../i18n";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";
import { TierAnswer, Tier, tierSatisfies } from "./canonicalTier";
import { loadCanonicalTier, useCanonicalTier } from "./useCanonicalTier";

/**
 * Whole days left on a trial grant, or null when the answer is not a live
 * trial. Truthful by construction: computed from the server's `expiresAt`
 * against the device clock at render time, floored — a trial with 6 days and
 * 23 hours left says "6 days left", never "7". A trial already past its end
 * returns null (the gate will be showing the upsell by then anyway; a
 * countdown that says "-1 days" helps nobody).
 */
export function trialDaysLeft(answer: TierAnswer | null, now: Date = new Date()): number | null {
  if (!answer || answer.state !== "resolved") return null;
  if (answer.source !== "trial" || !answer.expiresAt) return null;
  const end = Date.parse(answer.expiresAt);
  if (!Number.isFinite(end)) return null;
  const msLeft = end - now.getTime();
  if (msLeft <= 0) return null;
  return Math.floor(msLeft / 86_400_000);
}

export function PremiumFeatureGate({
  children,
  onUpgrade,
  minimum = "PREMIUM",
  body
}: {
  children: ReactNode;
  /** Navigate to the ONE selling surface (the Premium route). */
  onUpgrade: () => void;
  /** Ladder floor for this surface. Premium tile contents use the default. */
  minimum?: Tier;
  /** Upsell copy override; defaults to the generic premium-tile body. */
  body?: string;
}) {
  const { t } = useTranslation();
  const answer = useCanonicalTier();

  // "settled" tells the resolving spinner apart from a real failure: both have
  // state==="unavailable", but only one has actually been answered.
  const [settled, setSettled] = useState(false);

  const resolve = useCallback(() => {
    setSettled(false);
    loadCanonicalTier().finally(() => setSettled(true));
  }, []);

  useEffect(() => {
    resolve();
    // Cached-screen reconcile: an expiry that happened while the app was
    // backgrounded is discovered the moment it returns, not on next cold start.
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") void loadCanonicalTier();
    });
    return () => subscription.remove();
  }, [resolve]);

  if (answer.state !== "resolved") {
    if (!settled) {
      return (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.centerText}>{t("premium:gate.checking")}</Text>
        </View>
      );
    }
    return (
      <View style={styles.center}>
        <Text style={styles.unavailableTitle}>{t("premium:gate.unavailableTitle")}</Text>
        <Text style={styles.centerText}>{t("premium:gate.unavailableBody")}</Text>
        <Pressable accessibilityRole="button" style={styles.retryButton} onPress={resolve}>
          <Text style={styles.retryLabel}>{t("premium:retry")}</Text>
        </Pressable>
      </View>
    );
  }

  if (!tierSatisfies(answer, minimum)) {
    return (
      <ScrollView style={styles.lockedRoot} contentContainerStyle={styles.lockedContent}>
        <PremiumUpsellPanel body={body || t("premium:gate.lockedBody")} onUpgrade={onUpgrade} />
      </ScrollView>
    );
  }

  const daysLeft = trialDaysLeft(answer);
  return (
    <View style={styles.entitledRoot}>
      {daysLeft !== null ? (
        <View style={styles.trialBanner}>
          <Text style={styles.trialText}>
            {daysLeft === 0
              ? t("premium:gate.trialEndsToday")
              : t("premium:gate.trialDaysLeft", { count: daysLeft })}
          </Text>
        </View>
      ) : null}
      {children}
    </View>
  );
}

const styles = createThemedStyles(() => ({
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    gap: 10,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20,
    textAlign: "center"
  },
  entitledRoot: {
    backgroundColor: "transparent",
    flex: 1
  },
  lockedContent: {
    padding: 18
  },
  lockedRoot: {
    backgroundColor: "transparent",
    flex: 1
  },
  retryButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 42,
    justifyContent: "center",
    marginTop: 6,
    paddingHorizontal: 16
  },
  retryLabel: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  trialBanner: {
    backgroundColor: "rgba(37, 208, 167, 0.10)",
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    paddingHorizontal: 18,
    paddingVertical: 8
  },
  trialText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800",
    textAlign: "center"
  },
  unavailableTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900",
    textAlign: "center"
  }
}));
