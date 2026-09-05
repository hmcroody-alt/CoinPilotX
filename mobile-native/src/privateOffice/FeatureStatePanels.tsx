/**
 * The refusal, loading and empty panels the five feature screens share.
 *
 * One component because the refusal vocabulary is one vocabulary: the server
 * distinguishes NOT_ENTITLED / FEATURE_DISABLED / NOT_IMPLEMENTED /
 * UNAVAILABLE, and five screens each hand-rendering those four panels would
 * be five chances to collapse two of them into one banner. The copy lives
 * under `premium:privateOffice.feature.*` and deliberately keeps "we could
 * not look" (unavailable) apart from "your plan does not include this"
 * (notEntitled) — the person most likely to see the first is the person who
 * paid.
 */

import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useTranslation } from "../i18n";
import { colors } from "../theme/colors";

export type FeatureRefusalState =
  | "NOT_ENTITLED"
  | "FEATURE_DISABLED"
  | "NOT_IMPLEMENTED"
  | "UNAVAILABLE"
  | "ERROR";

const PANEL_KEYS: Record<FeatureRefusalState, string> = {
  NOT_ENTITLED: "notEntitled",
  FEATURE_DISABLED: "disabled",
  NOT_IMPLEMENTED: "notImplemented",
  UNAVAILABLE: "unavailable",
  ERROR: "error"
};

const PANEL_ICONS: Record<FeatureRefusalState, keyof typeof Ionicons.glyphMap> = {
  NOT_ENTITLED: "lock-closed-outline",
  FEATURE_DISABLED: "pause-circle-outline",
  NOT_IMPLEMENTED: "construct-outline",
  UNAVAILABLE: "cloud-offline-outline",
  ERROR: "alert-circle-outline"
};

export function FeatureLoadingPanel() {
  const { t } = useTranslation();
  return (
    <View style={styles.panel} accessibilityRole="progressbar">
      <ActivityIndicator color={colors.accent} />
      <Text style={styles.body}>{t("premium:privateOffice.feature.loading")}</Text>
    </View>
  );
}

export function FeatureRefusalPanel({
  state,
  minimumTier,
  onRetry
}: {
  state: FeatureRefusalState;
  minimumTier?: string;
  onRetry?: () => void;
}) {
  const { t } = useTranslation();
  const stem = `premium:privateOffice.feature.${PANEL_KEYS[state]}`;
  const body =
    state === "NOT_ENTITLED"
      ? minimumTier
        ? t(`${stem}.body`, { tier: minimumTier })
        : t(`${stem}.bodyGeneric`)
      : t(`${stem}.body`);
  const retryable = state === "UNAVAILABLE" || state === "ERROR";
  return (
    <View style={styles.panel}>
      <Ionicons name={PANEL_ICONS[state]} size={22} color={colors.warning} />
      <Text style={styles.title}>{t(`${stem}.title`)}</Text>
      <Text style={styles.body}>{body}</Text>
      {retryable && onRetry ? (
        <Pressable style={styles.retry} onPress={onRetry} accessibilityRole="button">
          <Text style={styles.retryText}>{t("premium:privateOffice.feature.retry")}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

export function FeatureEmptyPanel({ title, body }: { title: string; body: string }) {
  return (
    <View style={styles.panel}>
      <Ionicons name="file-tray-outline" size={22} color={colors.muted} />
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.body}>{body}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  panel: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    padding: 18,
    gap: 8,
    alignItems: "flex-start"
  },
  title: { color: colors.text, fontSize: 15, fontWeight: "700" },
  body: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  retry: {
    marginTop: 6,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  retryText: { color: colors.accentStrong, fontSize: 13, fontWeight: "700" }
});
