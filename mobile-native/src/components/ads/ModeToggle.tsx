/**
 * The header segmented control that swaps the Advertising screen between its two
 * ad products — Marketplace ads and Post ads — without a navigation push.
 *
 * It sits on the navy header, so the track is a translucent light-on-dark pill
 * and the selected segment is a solid light chip. The selected segment's label
 * takes the product's accent (gold for Marketplace = money, violet for Post =
 * content) so the colour reinforces which product you are in; the unselected
 * label is muted-on-dark. Colour is never the only signal — the selected state
 * is also carried by the chip fill, the bold weight, and `accessibilityState`.
 *
 * The thumb slides under the active label on selection. The slide is pure
 * affordance: it settles instantly under reduce-motion, and the segments are
 * fully usable before it finishes.
 */

import { useEffect, useRef } from "react";
import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { adsLight } from "../../theme/adsLight";
import { logiNexusMotion } from "../../theme/logiNexusMotion";
import type { AdsMode } from "../../api/adsDashboard";

export type ModeToggleProps = {
  mode: AdsMode;
  onChange: (next: AdsMode) => void;
  reducedMotion: boolean;
  /** Whether Post ads is a flag-gated preview, to append "· Preview" to its label. */
  postIsPreview: boolean;
};

const SEGMENTS: { key: AdsMode; label: string }[] = [
  { key: "marketplace", label: "Marketplace ads" },
  { key: "post", label: "Post ads" }
];

export function ModeToggle({ mode, onChange, reducedMotion, postIsPreview }: ModeToggleProps) {
  const index = mode === "post" ? 1 : 0;
  const slide = useRef(new Animated.Value(index)).current;

  useEffect(() => {
    if (reducedMotion) {
      slide.setValue(index);
      return;
    }
    Animated.timing(slide, {
      toValue: index,
      duration: 180,
      easing: logiNexusMotion.easing.standard,
      useNativeDriver: true
    }).start();
  }, [index, reducedMotion, slide]);

  return (
    <View style={styles.track} accessibilityRole="tablist">
      <Animated.View
        pointerEvents="none"
        style={[
          styles.thumb,
          {
            transform: [
              {
                translateX: slide.interpolate({
                  inputRange: [0, 1],
                  // Each segment is 50% of the track; the thumb is inset 3px on
                  // each side, so it travels by the segment width.
                  outputRange: ["0%", "100%"]
                })
              }
            ]
          }
        ]}
      />
      {SEGMENTS.map((segment) => {
        const selected = segment.key === mode;
        const accent = segment.key === "marketplace" ? adsLight.money.budget : adsLight.post.base;
        return (
          <Pressable
            key={segment.key}
            style={styles.segment}
            onPress={() => onChange(segment.key)}
            accessibilityRole="tab"
            accessibilityState={{ selected }}
            accessibilityLabel={
              segment.key === "post" && postIsPreview ? `${segment.label}, preview` : segment.label
            }
          >
            <Text
              numberOfLines={1}
              style={[
                styles.label,
                selected ? [styles.labelSelected, { color: accent }] : null
              ]}
            >
              {segment.label}
            </Text>
            {segment.key === "post" && postIsPreview ? (
              <View style={styles.previewDot} accessibilityElementsHidden importantForAccessibility="no" />
            ) : null}
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  track: {
    flexDirection: "row",
    height: 40,
    borderRadius: adsLight.radius.pill,
    backgroundColor: "rgba(255,255,255,0.10)",
    padding: 3,
    position: "relative"
  },
  thumb: {
    position: "absolute",
    top: 3,
    left: 3,
    // Half the track minus the 3px padding on the shared inner edge.
    width: "50%",
    bottom: 3,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.bg.card
  },
  segment: {
    flex: 1,
    flexDirection: "row",
    gap: 5,
    alignItems: "center",
    justifyContent: "center"
  },
  label: { fontSize: 13, fontWeight: "700", color: adsLight.text.onDarkMuted },
  labelSelected: { fontWeight: "800" },
  previewDot: {
    width: 5,
    height: 5,
    borderRadius: 2.5,
    backgroundColor: adsLight.post.base
  }
});
