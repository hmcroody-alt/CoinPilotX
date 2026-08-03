/**
 * One card in the hub grid — the second of the three components this mission
 * adds, and the one every rule on the screen passes through.
 *
 * The card is a pure presenter. It receives a title, a static subtitle, an
 * already-resolved state line and an already-decided badge, and it renders them.
 * It computes nothing. That is not stylistic: a card that could compute could
 * disagree with the section it opens, which is the first failure mode this
 * mission exists to prevent.
 *
 * THREE THINGS WORTH KNOWING:
 *
 * 1. `state === null` is a first-class outcome, not an error. It means the card's
 *    source is unavailable or has nothing to say, and the card falls back to its
 *    static subtitle — no spinner, no error chrome, no gap where a line should
 *    be. Nine working cards beside one quiet one is a working screen.
 *
 * 2. `onRefresh` is the partial-availability affordance. It appears ONLY when
 *    that one card's source failed, as a small inline retry on the card itself,
 *    so a broken section is recoverable without reloading a screen where
 *    everything else is fine.
 *
 * 3. The whole card is ONE accessibility element. Title, subtitle, state line
 *    and badge are joined by `cardAccessibilityLabel` into a single
 *    announcement, with urgency arriving as the word "Urgent" rather than as a
 *    colour. A screen-reader user swipes through ten cards, not forty fragments.
 *
 * The urgent treatment — amber border, warm gradient, bobbing clock — is applied
 * only when the resolver marked the line urgent, which today no resolver can do
 * (see `HUB_ORDER_DEADLINES`). If everything is urgent, nothing is.
 */

import { useEffect, useRef } from "react";
import { Animated, Easing, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import {
  HUB_CARD_TINTS,
  cardAccessibilityLabel,
  type HubCardKey,
  type HubStateLineResult
} from "../../api/businessHub";
import { hubLight } from "../../theme/hubLight";
import { useStoreBadgePop, useStorePress } from "../../theme/storeMotion";
import { StateLine } from "./StateLine";

export type SectionCardProps = {
  cardKey: HubCardKey;
  title: string;
  /** The card's fixed description. Shown always; it is what the state line sits under. */
  subtitle: string;
  icon: string;
  state: HubStateLineResult;
  /** Already passed through `hubBadge` — this component does not decide badging. */
  badge: number | null;
  /** 0–1, Business Profile only. Renders the completeness bar. */
  progress?: number | null;
  onPress: () => void;
  /** Present only when this card's source failed. Renders the inline retry. */
  onRefresh?: () => void;
  reducedMotion: boolean;
  /** One column at the largest text sizes; see `hubGridColumns`. */
  fullWidth?: boolean;
};

export function SectionCard({
  cardKey,
  title,
  subtitle,
  icon,
  state,
  badge,
  progress,
  onPress,
  onRefresh,
  reducedMotion,
  fullWidth = false
}: SectionCardProps) {
  const press = useStorePress(reducedMotion, 0.97);
  const badgePop = useStoreBadgePop(reducedMotion, badge !== null && badge > 0);
  const tint = HUB_CARD_TINTS[cardKey];
  const urgent = Boolean(state?.urgent);

  const label = cardAccessibilityLabel({ title, subtitle, state, badge });

  const body = (
    <>
      <View style={styles.topRow}>
        <View style={[styles.iconChip, { backgroundColor: `${tint}${hubLight.card.iconChipAlpha}` }]}>
          <Ionicons name={icon as never} size={18} color={tint} />
        </View>
        {urgent ? <UrgentClock reducedMotion={reducedMotion} /> : null}
        {badge !== null && badge > 0 ? (
          <Animated.View style={[styles.badge, { transform: [{ scale: badgePop }] }]}>
            <Text style={styles.badgeText}>{badge > 99 ? "99+" : badge}</Text>
          </Animated.View>
        ) : null}
      </View>

      <Text style={styles.title} numberOfLines={1}>
        {title}
      </Text>
      {/* The subtitle may truncate — it is fixed copy the seller can learn once.
          The state line below it may not; it changes and must be read exactly. */}
      <Text style={styles.subtitle} numberOfLines={2}>
        {subtitle}
      </Text>

      {state ? <StateLine state={state} reducedMotion={reducedMotion} /> : null}

      {typeof progress === "number" ? (
        <ProgressBar value={progress} reducedMotion={reducedMotion} tint={tint} />
      ) : null}

      {onRefresh ? (
        <Pressable
          onPress={onRefresh}
          hitSlop={10}
          style={styles.retry}
          accessibilityRole="button"
          accessibilityLabel={`Retry loading ${title}`}
        >
          <Ionicons name="refresh" size={12} color={hubLight.text.link} />
          <Text style={styles.retryText}>Tap to retry</Text>
        </Pressable>
      ) : null}
    </>
  );

  return (
    <Animated.View style={[styles.wrap, fullWidth ? styles.wrapFull : styles.wrapHalf, press.style]}>
      <Pressable
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        accessibilityRole="button"
        accessibilityLabel={label}
        style={[styles.card, urgent && styles.cardUrgent]}
      >
        {urgent ? (
          <LinearGradient
            colors={[...hubLight.urgent.gradient]}
            style={StyleSheet.absoluteFill}
            pointerEvents="none"
          />
        ) : null}
        {body}
      </Pressable>
    </Animated.View>
  );
}

/**
 * The bobbing clock on an urgent card. Not a loop for its own sake: it is the
 * one thing on a ten-card grid that moves vertically, so the eye finds it
 * without scanning. Unreachable today, and correct that it is.
 */
function UrgentClock({ reducedMotion }: { reducedMotion: boolean }) {
  const bob = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reducedMotion) return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(bob, { toValue: 1, duration: 700, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
        Animated.timing(bob, { toValue: 0, duration: 700, easing: Easing.inOut(Easing.ease), useNativeDriver: true })
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [bob, reducedMotion]);

  return (
    <Animated.View
      style={{ transform: [{ translateY: bob.interpolate({ inputRange: [0, 1], outputRange: [0, -3] }) }] }}
      accessibilityElementsHidden
      importantForAccessibility="no"
    >
      <Ionicons name="alarm-outline" size={16} color={hubLight.tone.warn} />
    </Animated.View>
  );
}

/**
 * The completeness bar, Business Profile only. Fills ONCE on mount and then
 * holds — a bar that re-animates on every refresh turns a static fact into a
 * flicker, and the seller re-reads it every time thinking it changed.
 */
function ProgressBar({
  value,
  reducedMotion,
  tint
}: {
  value: number;
  reducedMotion: boolean;
  tint: string;
}) {
  const fill = useRef(new Animated.Value(reducedMotion ? 1 : 0)).current;
  const played = useRef(false);

  useEffect(() => {
    if (reducedMotion || played.current) return;
    played.current = true;
    Animated.timing(fill, {
      toValue: 1,
      duration: 620,
      easing: Easing.out(Easing.cubic),
      // Width cannot use the native driver, and a scaleX on a 4px bar produces
      // a visibly soft edge. 620ms of JS-driven width, once per mount, is the
      // cheaper trade.
      useNativeDriver: false
    }).start();
  }, [fill, reducedMotion]);

  const width = fill.interpolate({
    inputRange: [0, 1],
    outputRange: ["0%", `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`]
  });

  return (
    <View style={styles.track} accessibilityElementsHidden importantForAccessibility="no">
      <Animated.View style={[styles.fill, { width, backgroundColor: tint }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { marginBottom: hubLight.grid.gap },
  wrapHalf: { width: "48.5%" },
  wrapFull: { width: "100%" },
  card: {
    minHeight: hubLight.card.minHeight,
    backgroundColor: hubLight.bg.card,
    borderRadius: hubLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: hubLight.border.hairline,
    padding: 12,
    gap: 4,
    overflow: "hidden"
  },
  cardUrgent: { borderWidth: 1, borderColor: hubLight.urgent.border },
  topRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  iconChip: {
    width: hubLight.card.iconChip,
    height: hubLight.card.iconChip,
    borderRadius: hubLight.card.iconChip / 2,
    alignItems: "center",
    justifyContent: "center"
  },
  badge: {
    marginLeft: "auto",
    minWidth: 20,
    height: 20,
    paddingHorizontal: 6,
    borderRadius: 10,
    backgroundColor: hubLight.status.warning,
    alignItems: "center",
    justifyContent: "center"
  },
  badgeText: { fontSize: 11, fontWeight: "800", color: "#FFFFFF" },
  title: { fontSize: 14, fontWeight: "800", color: hubLight.text.primary, marginTop: 2 },
  subtitle: { fontSize: 11, color: hubLight.text.muted, lineHeight: 15 },
  track: {
    height: 4,
    borderRadius: 2,
    backgroundColor: hubLight.bg.skeleton,
    overflow: "hidden",
    marginTop: 2
  },
  fill: { height: 4, borderRadius: 2 },
  retry: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: 2, minHeight: 20 },
  retryText: { fontSize: 11, fontWeight: "700", color: hubLight.text.link }
});
