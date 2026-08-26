/**
 * A Business OS grid tile that knows its own readiness.
 *
 * The tile is the same object in both states — same size, same icon, same
 * position in the grid — because the brief's whole premise is that a user should
 * be able to see the shape of the product before all of it exists. What changes
 * when a tile is locked is that it gains a badge, a teal edge, a slow drift and
 * a halo, and its tap opens the Coming Soon message instead of a screen.
 *
 * What it deliberately does NOT do is dim, grey out, or set
 * `accessibilityState.disabled`. A disabled control tells the user they did
 * something wrong. This one is not disabled — it is early.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors } from "../theme/colors";
import { presenceTheme } from "../theme/presenceTheme";
import { createThemedStyles } from "../theme/themedStyles";
import { useLockedMotion } from "./lockedMotion";
import { readinessOf, type LaunchModuleId } from "./readiness";
import { useLaunchCopy } from "./useLaunchGate";

export function LaunchTile({
  id,
  label,
  blurb,
  icon,
  index,
  motionEnabled,
  screenActive,
  onPress
}: {
  id: LaunchModuleId;
  label: string;
  blurb: string;
  icon: string;
  /** Grid position — staggers this tile's drift against its neighbours'. */
  index: number;
  /** False under Reduce Motion (OS or in-app). */
  motionEnabled: boolean;
  /** False when the screen is not focused, so the loop stops off-screen. */
  screenActive: boolean;
  /** Always call the gate, never navigate directly. See `useLaunchGate`. */
  onPress: () => void;
}) {
  const state = readinessOf(id);
  const locked = state !== "READY";
  const { badge, accessibility } = useLaunchCopy();
  const motion = useLockedMotion({
    index,
    active: screenActive,
    // A READY tile never animates: the drift is what marks a tile as pending,
    // so applying it everywhere would make it mean nothing.
    enabled: motionEnabled && locked
  });

  const a11y = accessibility(id, label, blurb);

  return (
    <Animated.View style={[styles.wrap, locked ? motion.cardStyle : null]}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={a11y.accessibilityLabel}
        accessibilityHint={a11y.accessibilityHint}
        testID={`launch-tile-${id}`}
        onPress={onPress}
        onPressIn={locked ? motion.onPressIn : undefined}
        onPressOut={locked ? motion.onPressOut : undefined}
        style={[styles.tile, locked ? styles.tileLocked : null]}
      >
        {locked ? <Animated.View pointerEvents="none" style={[styles.halo, motion.glowStyle]} /> : null}
        <Ionicons name={icon as never} size={20} color={locked ? presenceTheme.teal : colors.accent} />
        <Text style={styles.tileLabel}>{label}</Text>
        <Text style={styles.tileBlurb} numberOfLines={2}>
          {blurb}
        </Text>
        {/*
          The badge is the non-colour channel. Readiness has to survive
          greyscale and colour blindness, so the state is spelled out in words
          on the tile as well as being carried by the teal edge and the halo.
        */}
        {locked ? (
          <View style={styles.badge}>
            <Text style={styles.badgeText}>{badge(state)}</Text>
          </View>
        ) : null}
      </Pressable>
    </Animated.View>
  );
}

const styles = createThemedStyles(() => ({
  badge: {
    alignSelf: "flex-start",
    backgroundColor: presenceTheme.tealSoft,
    borderColor: presenceTheme.tealBorder,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    marginTop: 2,
    paddingHorizontal: 8,
    paddingVertical: 3
  },
  badgeText: {
    color: presenceTheme.teal,
    fontSize: 10,
    fontWeight: "900",
    letterSpacing: 0.6
  },
  /**
   * The halo. Sits behind the tile's content and breathes with the drift.
   * `pointerEvents="none"` so it never intercepts the tap it is decorating.
   */
  halo: {
    backgroundColor: presenceTheme.tealSoft,
    borderRadius: 999,
    height: 96,
    left: -18,
    position: "absolute",
    top: -34,
    width: 96
  },
  tile: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 6,
    overflow: "hidden",
    padding: 12
  },
  tileBlurb: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  tileLabel: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "700"
  },
  tileLocked: {
    borderColor: presenceTheme.tealBorder
  },
  /**
   * The flex basis lives on the wrapper rather than the tile because the
   * wrapper is what the grid lays out — the `Animated.View` is the child of
   * `styles.grid`, and putting the sizing on the inner `Pressable` would let
   * every locked tile collapse to its content width.
   */
  wrap: {
    flexBasis: "47%",
    flexGrow: 1
  }
}));
