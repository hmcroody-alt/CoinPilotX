/**
 * The stock/visibility indicator used on every listing row and on the store
 * status strip.
 *
 * The single rule this component exists to enforce: **status is never conveyed
 * by colour alone.** The dot cannot be rendered without a label, because `label`
 * is a required prop and the component renders it. A colour-blind seller, a
 * screen-reader user, and someone glancing at the screen in sunlight all get
 * the same information.
 *
 * The blink on `low_stock` is the one piece of motion here, and it is a genuine
 * signal rather than decoration: it marks the state that is about to become a
 * lost sale. Everything else rests solid.
 */

import { useMemo } from "react";
import { Animated, StyleSheet, Text, View, type StyleProp, type TextStyle } from "react-native";
import { storeLight } from "../../theme/storeLight";
import { STORE_AMBIENT, useStoreAmbient } from "../../theme/storeMotion";
import type { StoreListingHealth } from "../../api/storeDashboard";

const DOT = 8;

/**
 * Colour per health state. `hidden` and `out_of_stock` share the error red
 * because from the buyer's side they are the same thing — the item cannot be
 * bought — and the row's text says which.
 */
const DOT_COLOR: Record<StoreListingHealth, string> = {
  in_stock: storeLight.status.success,
  low_stock: storeLight.status.warning,
  out_of_stock: storeLight.status.error,
  hidden: storeLight.status.error,
  draft: storeLight.status.neutral
};

export type StoreStatusLedProps = {
  health: StoreListingHealth;
  /**
   * Required, and rendered. This is what makes the state readable without
   * colour — e.g. "Only 2 left", "Out of stock — hidden", "Draft".
   */
  label: string;
  reducedMotion: boolean;
  labelStyle?: StyleProp<TextStyle>;
};

export function StoreStatusLed({ health, label, reducedMotion, labelStyle }: StoreStatusLedProps) {
  const blinking = health === "low_stock";
  const pulse = useStoreAmbient(STORE_AMBIENT.ledBlink, reducedMotion, {
    enabled: blinking,
    // Rests fully lit: a stopped blink must not look like a dimmed or broken
    // indicator.
    resetTo: 1,
    pingPong: true
  });

  const color = DOT_COLOR[health];
  const dotStyle = useMemo(
    () => [styles.dot, { backgroundColor: color }, blinking ? { opacity: pulse } : null],
    [blinking, color, pulse]
  );

  return (
    <View style={styles.row}>
      {/* The dot is decorative: the label beside it already carries the meaning,
          so announcing both would read the status twice. */}
      <Animated.View style={dotStyle} accessibilityElementsHidden importantForAccessibility="no" />
      <Text style={[styles.label, { color }, labelStyle]} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

/**
 * The larger dot on the dark status strip, with an expanding ring behind it
 * while the store is open. Same no-colour-alone rule: the caller always renders
 * text beside it.
 */
export function StoreLiveDot({ open, reducedMotion }: { open: boolean; reducedMotion: boolean }) {
  const ping = useStoreAmbient(STORE_AMBIENT.statusPing, reducedMotion, {
    enabled: open,
    resetTo: 0
  });
  const color = open ? storeLight.status.success : storeLight.text.onDarkMuted;

  return (
    <View style={styles.liveWrap} accessibilityElementsHidden importantForAccessibility="no">
      {open ? (
        <Animated.View
          style={[
            styles.ring,
            {
              borderColor: color,
              opacity: ping.interpolate({ inputRange: [0, 1], outputRange: [0.55, 0] }),
              transform: [{ scale: ping.interpolate({ inputRange: [0, 1], outputRange: [1, 2.4] }) }]
            }
          ]}
        />
      ) : null}
      <View style={[styles.liveDot, { backgroundColor: color }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 6 },
  dot: { width: DOT, height: DOT, borderRadius: DOT / 2 },
  label: { fontSize: 12, fontWeight: "600", flexShrink: 1 },
  liveWrap: { width: 10, height: 10, alignItems: "center", justifyContent: "center" },
  liveDot: { width: 8, height: 8, borderRadius: 4 },
  ring: { position: "absolute", width: 8, height: 8, borderRadius: 4, borderWidth: 1 }
});
