/**
 * The live-now banner. Rendered ONLY when a real event is live (the derivation
 * returns null otherwise, so an absent banner is the default). The pulsing red
 * dot is the one motion on the surface; it stills under reduced-motion. Stats are
 * whatever the derivation put on the banner — present only when the live-stats
 * flag is on AND a real number exists, so this component never invents "watching"
 * or "orders" figures.
 */

import { useEffect, useRef } from "react";
import { Animated, Easing, Pressable, StyleSheet, Text, View } from "react-native";
import { eventsLight } from "../../theme/eventsLight";
import type { LiveBanner } from "../../api/eventsManager";

export function LiveNowBanner({
  banner,
  reducedMotion,
  onOpen
}: {
  banner: LiveBanner;
  reducedMotion?: boolean;
  onOpen?: (banner: LiveBanner) => void;
}) {
  const pulse = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reducedMotion) return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 700, easing: Easing.out(Easing.ease), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 700, easing: Easing.in(Easing.ease), useNativeDriver: true })
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [pulse, reducedMotion]);

  const dotScale = pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.6] });
  const dotOpacity = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.5, 0] });

  return (
    <View
      style={styles.banner}
      accessibilityRole="summary"
      accessibilityLiveRegion="assertive"
      accessibilityLabel={banner.a11yAnnouncement}
    >
      <View style={styles.dotWrap}>
        {!reducedMotion ? (
          <Animated.View style={[styles.halo, { transform: [{ scale: dotScale }], opacity: dotOpacity }]} />
        ) : null}
        <View style={styles.dot} />
      </View>

      <View style={styles.body}>
        <View style={styles.labelRow}>
          <Text style={styles.live}>LIVE</Text>
          <Text style={styles.title} numberOfLines={1}>
            {banner.title}
          </Text>
        </View>
        {banner.statsLine ? (
          <Text style={styles.stats} numberOfLines={1}>
            {banner.statsLine}
          </Text>
        ) : null}
      </View>

      <Pressable
        style={styles.openBtn}
        accessibilityRole="button"
        accessibilityLabel={`Open live: ${banner.title}`}
        onPress={() => onOpen?.(banner)}
        hitSlop={6}
      >
        <Text style={styles.openText}>Open</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: eventsLight.live.bannerBg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: eventsLight.live.bannerBorder,
    borderRadius: eventsLight.radius.card,
    paddingVertical: 10,
    paddingHorizontal: 12
  },
  dotWrap: { width: 14, height: 14, alignItems: "center", justifyContent: "center" },
  halo: {
    position: "absolute",
    width: 14,
    height: 14,
    borderRadius: 7,
    backgroundColor: eventsLight.live.dot
  },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: eventsLight.live.dot },
  body: { flex: 1, gap: 2 },
  labelRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  live: { fontSize: 12, fontWeight: "900", color: eventsLight.live.label, letterSpacing: 0.5 },
  title: { flex: 1, fontSize: 14, fontWeight: "800", color: eventsLight.text.primary },
  stats: { fontSize: 12, fontWeight: "600", color: eventsLight.text.muted },
  openBtn: {
    minHeight: 34,
    paddingHorizontal: 14,
    borderRadius: eventsLight.radius.control,
    backgroundColor: eventsLight.live.dot,
    alignItems: "center",
    justifyContent: "center"
  },
  openText: { fontSize: 13, fontWeight: "800", color: "#FFFFFF" }
});
