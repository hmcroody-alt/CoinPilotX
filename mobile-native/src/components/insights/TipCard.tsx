/**
 * The single recommendation at the bottom of Insights.
 *
 * One tip, or none. Which one is chosen by {@link selectTip} in `insightsRules`,
 * against a documented priority order; this component only renders what it is
 * given. If no rule fires the caller renders nothing at all — a card that always
 * has advice teaches the seller to stop reading it.
 *
 * Every figure in the body arrives already formatted by the caller through the
 * localization utilities, and every figure traces to a stated calculation. The
 * weekly rate is trailing revenue over the observed period scaled to seven days,
 * which is why the copy says "was earning" and not "will earn".
 *
 * The dismiss control is deliberately as prominent as the action. A tip the
 * seller cannot silence is an advertisement.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { insightsLight } from "../../theme/insightsLight";
import { useInsightsTipShimmer } from "../../theme/insightsMotion";
import { useStorePress } from "../../theme/storeMotion";

export type TipCardProps = {
  title: string;
  /** Full sentence, numbers already interpolated and formatted. */
  body: string;
  actionLabel: string;
  onAction: () => void;
  onDismiss: () => void;
  /** Where the action goes, for the label: "Opens the listing". */
  destinationHint: string;
  reducedMotion: boolean;
};

export function TipCard({
  title,
  body,
  actionLabel,
  onAction,
  onDismiss,
  destinationHint,
  reducedMotion
}: TipCardProps) {
  const shimmer = useInsightsTipShimmer(reducedMotion, true);
  const press = useStorePress(reducedMotion, 0.97);

  return (
    <LinearGradient
      colors={[insightsLight.tip.from, insightsLight.tip.to]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.card}
      // Announced as a unit when it appears, so a seller using a screen reader
      // hears the whole suggestion rather than four disconnected fragments.
      accessible
      accessibilityLiveRegion="polite"
      accessibilityLabel={`Suggestion. ${title}. ${body}`}
    >
      {/* Ambient sheen. Carries no state, so it is the first thing removed under
          reduce-motion and it stops when the app is backgrounded. */}
      <Animated.View
        pointerEvents="none"
        style={[
          styles.shimmer,
          {
            opacity: shimmer.interpolate({
              inputRange: [0, 0.45, 0.5, 0.55, 1],
              outputRange: [0, 0, 0.35, 0, 0]
            }),
            transform: [
              { translateX: shimmer.interpolate({ inputRange: [0, 1], outputRange: [-240, 420] }) },
              { rotate: "18deg" }
            ]
          }
        ]}
      />

      <View style={styles.head}>
        <Ionicons
          name="bulb-outline"
          size={18}
          color={insightsLight.status.success}
          accessibilityElementsHidden
          importantForAccessibility="no"
        />
        <Text style={styles.title} numberOfLines={2}>
          {title}
        </Text>
        <Pressable
          onPress={onDismiss}
          hitSlop={12}
          style={styles.dismiss}
          accessibilityRole="button"
          accessibilityLabel={`Dismiss this suggestion. ${title}`}
        >
          <Ionicons name="close" size={18} color={insightsLight.text.muted} />
        </Pressable>
      </View>

      <Text style={styles.body}>{body}</Text>

      <Animated.View style={press.style}>
        <Pressable
          style={styles.action}
          onPress={onAction}
          onPressIn={press.onPressIn}
          onPressOut={press.onPressOut}
          accessibilityRole="button"
          accessibilityLabel={`${actionLabel}. ${destinationHint}.`}
        >
          <Text style={styles.actionText}>{actionLabel}</Text>
          <Ionicons
            name="arrow-forward"
            size={15}
            color={insightsLight.cta.text}
            accessibilityElementsHidden
            importantForAccessibility="no"
          />
        </Pressable>
      </Animated.View>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  card: {
    padding: insightsLight.space.card,
    gap: 8,
    borderRadius: insightsLight.radius.card,
    borderWidth: 1,
    borderColor: insightsLight.tip.border,
    overflow: "hidden"
  },
  shimmer: {
    position: "absolute",
    top: -60,
    bottom: -60,
    width: 46,
    backgroundColor: "#FFFFFF"
  },
  head: { flexDirection: "row", alignItems: "center", gap: 8 },
  title: { flex: 1, fontSize: 14, fontWeight: "800", color: insightsLight.text.primary },
  dismiss: {
    minWidth: 32,
    minHeight: 32,
    alignItems: "center",
    justifyContent: "center"
  },
  body: { fontSize: 13, lineHeight: 18, color: insightsLight.text.primary },
  action: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: 4,
    minHeight: insightsLight.size.tapTarget,
    paddingHorizontal: 18,
    borderRadius: insightsLight.radius.pill,
    backgroundColor: insightsLight.cta.from
  },
  actionText: { fontSize: 13, fontWeight: "800", color: insightsLight.cta.text }
});
