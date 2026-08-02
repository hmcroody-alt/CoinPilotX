/**
 * A status pill for campaigns and promotions.
 *
 * Status is never carried by colour alone: the dot has a paired text label that
 * says the same thing, so a delivering campaign reads "● Delivering" to everyone
 * including a screen reader and a colour-blind user. The dot itself is hidden
 * from assistive tech because the word beside it already carries the meaning.
 *
 * The one animated case is a "warning" tone (a limited campaign), which blinks
 * slowly to draw the eye — and settles solid under reduce-motion.
 */

import { Animated, StyleSheet, Text, View } from "react-native";
import { adsLight } from "../../theme/adsLight";
import { STORE_AMBIENT, useStoreAmbient } from "../../theme/storeMotion";
import type { CampaignTone } from "../../api/adsDashboard";

export type AdsStatusPillProps = {
  label: string;
  tone: CampaignTone;
  reducedMotion: boolean;
};

function toneColor(tone: CampaignTone): string {
  switch (tone) {
    case "success":
      return adsLight.status.success;
    case "info":
      return adsLight.chart.axis;
    case "warning":
      return adsLight.status.warning;
    case "error":
      return adsLight.status.error;
    default:
      return adsLight.status.neutral;
  }
}

export function AdsStatusPill({ label, tone, reducedMotion }: AdsStatusPillProps) {
  const color = toneColor(tone);
  const blink = useStoreAmbient(STORE_AMBIENT.ledBlink, reducedMotion, {
    enabled: tone === "warning",
    resetTo: 1,
    pingPong: true
  });

  return (
    <View style={styles.pill} accessible accessibilityLabel={label}>
      <Animated.View
        accessibilityElementsHidden
        importantForAccessibility="no"
        style={[
          styles.dot,
          { backgroundColor: color },
          tone === "warning"
            ? { opacity: blink.interpolate({ inputRange: [0, 1], outputRange: [0.35, 1] }) }
            : null
        ]}
      />
      <Text style={[styles.label, { color }]} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4
  },
  label: {
    fontSize: 12,
    fontWeight: "700"
  }
});
