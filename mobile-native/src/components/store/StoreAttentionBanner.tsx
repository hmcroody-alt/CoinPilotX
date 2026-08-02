/**
 * The "something needs fixing" banner.
 *
 * It renders **only** when there is something to fix. The screen passes `null`
 * otherwise and nothing occupies the space. A banner that is always present
 * teaches the seller to look past it, at which point it stops working on the
 * day it matters — so the empty case is no banner rather than a reassuring one.
 *
 * Announced as an alert region so a screen reader reaches it without the user
 * having to swipe through the KPI grid first.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { storeLight } from "../../theme/storeLight";
import { STORE_AMBIENT, useStoreAmbient, useStorePress } from "../../theme/storeMotion";

export type StoreAttentionBannerProps = {
  /** Bold first line, e.g. "3 listings are out of stock". */
  headline: string;
  /** One sentence of detail. */
  detail: string;
  actionLabel?: string;
  onPress: () => void;
  reducedMotion: boolean;
};

export function StoreAttentionBanner({
  headline,
  detail,
  actionLabel = "Fix now",
  onPress,
  reducedMotion
}: StoreAttentionBannerProps) {
  const press = useStorePress(reducedMotion, 0.99);
  const tilt = useStoreAmbient(STORE_AMBIENT.bannerTilt, reducedMotion, {
    resetTo: 0,
    pingPong: true
  });
  const shimmer = useStoreAmbient(STORE_AMBIENT.bannerShimmer, reducedMotion, { resetTo: 0 });

  return (
    <Animated.View style={press.style}>
      <Pressable
        style={styles.banner}
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        accessibilityRole="button"
        // `alert` so it is surfaced ahead of ordinary content rather than in
        // scroll order.
        accessibilityLiveRegion="polite"
        accessibilityLabel={`${headline}. ${detail}`}
        accessibilityHint={`${actionLabel}`}
      >
        {/* Slow diagonal shimmer. Purely atmospheric, so it is the first thing
            reduce-motion removes and it never conveys state. */}
        <Animated.View
          pointerEvents="none"
          style={[
            styles.shimmer,
            {
              opacity: shimmer.interpolate({
                inputRange: [0, 0.35, 0.5, 0.65, 1],
                outputRange: [0, 0, 0.5, 0, 0]
              }),
              transform: [
                {
                  translateX: shimmer.interpolate({
                    inputRange: [0, 1],
                    outputRange: [-240, 240]
                  })
                },
                { rotate: "18deg" }
              ]
            }
          ]}
        />
        <Animated.Text
          style={[
            styles.icon,
            {
              transform: [
                { rotate: tilt.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "9deg"] }) }
              ]
            }
          ]}
          accessibilityElementsHidden
          importantForAccessibility="no"
        >
          ⚠
        </Animated.Text>
        <View style={styles.body}>
          <Text style={styles.headline}>{headline}</Text>
          <Text style={styles.detail}>{detail}</Text>
        </View>
        <Text style={styles.action}>{actionLabel} ›</Text>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    padding: storeLight.space.card,
    borderRadius: storeLight.radius.card,
    backgroundColor: storeLight.bg.warning,
    borderWidth: 1,
    borderColor: storeLight.border.warning,
    minHeight: storeLight.size.tapTarget,
    overflow: "hidden"
  },
  shimmer: {
    position: "absolute",
    top: -40,
    bottom: -40,
    width: 44,
    backgroundColor: "#FFFFFF"
  },
  icon: { fontSize: 18, color: storeLight.status.warning },
  body: { flex: 1, gap: 2 },
  headline: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary },
  detail: { fontSize: 12, color: storeLight.text.muted },
  action: { fontSize: 13, fontWeight: "700", color: storeLight.text.link }
});
