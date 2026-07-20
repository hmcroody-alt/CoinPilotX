import { memo } from "react";
import { StyleSheet, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";

/**
 * StaticUFOField
 *
 * A purely decorative LogiNexus background motif: a small fleet of UFO craft that
 * appear to be flying through the interface even though every element is static.
 *
 * The illusion of velocity is composed rather than animated:
 *   - tapered luminous wake trails behind each craft,
 *   - a forward-facing nose glow,
 *   - diagonal trajectory rotation,
 *   - perspective scaling + depth-based opacity,
 *   - asymmetric placement and partial edge cropping.
 *
 * Performance + accessibility contract:
 *   - No animation, no timers, no re-renders during scroll (memoized, static).
 *   - Built from Views + expo-linear-gradient only (no new native deps).
 *   - pointerEvents="none" so it never intercepts touches.
 *   - Hidden from the accessibility tree (decoration only).
 */

type Hue = "teal" | "cyan" | "purple";

type CraftSpec = {
  id: string;
  // Position: any subset of top/left/right as numbers (px) or percent strings.
  top?: number | string;
  left?: number | string;
  right?: number | string;
  bottom?: number | string;
  scale: number;
  hue: Hue;
  angleDeg: number; // trajectory rotation of the whole craft + wake
  opacity: number; // depth-based fade
  trails?: number; // wake line count (default 3)
};

const HUES: Record<Hue, { edge: string; body: string; glow: string; wake: string }> = {
  teal: { edge: "#3DF5CE", body: "#123a4d", glow: "rgba(32, 237, 195, 0.30)", wake: "rgba(90, 245, 210," },
  cyan: { edge: "#57DBFF", body: "#123246", glow: "rgba(37, 207, 255, 0.28)", wake: "rgba(120, 224, 255," },
  purple: { edge: "#B78BFF", body: "#2a1f4d", glow: "rgba(156, 98, 255, 0.28)", wake: "rgba(190, 150, 255," }
};

// Base craft geometry in points. Scaled per-craft via transform for depth.
const BODY_W = 60;
const BODY_H = 17;

// Distant → near composition. Distant craft are smaller, fainter, higher.
// Positions target the visible dark gaps around the translucent LogiNexus cards.
// Foreground overlay: craft fly "through" the interface, kept to open dark zones
// (screen margins, the empty right side of the Status row, the right edge) so
// they never sit over important text.
const FLEET: CraftSpec[] = [
  // Distant craft in the upper-right hero atmosphere, climbing away.
  { id: "distant-hero", top: "14%", right: "7%", scale: 0.55, hue: "cyan", angleDeg: -16, opacity: 0.5, trails: 3 },
  // Craft skimming the left margin, travelling left across the hero.
  { id: "hero-cross", top: "30%", left: "3%", scale: 0.7, hue: "teal", angleDeg: 171, opacity: 0.5, trails: 4 },
  // Partially cropped craft diving in at the right edge, mid-screen.
  { id: "edge-crop", top: "40%", right: -18, scale: 1, hue: "purple", angleDeg: 24, opacity: 0.6, trails: 4 },
  // Small formation in the open dark area to the right of the Status portraits.
  { id: "status-lead", top: "63%", right: "8%", scale: 0.5, hue: "teal", angleDeg: -12, opacity: 0.6, trails: 3 },
  { id: "status-wing", top: "65%", right: "18%", scale: 0.36, hue: "cyan", angleDeg: -12, opacity: 0.45, trails: 2 },
  // Craft drifting past the lower-left, near the composer edge.
  { id: "composer-drift", bottom: "13%", left: "9%", scale: 0.55, hue: "purple", angleDeg: -8, opacity: 0.45, trails: 3 }
];

function Craft({ spec }: { spec: CraftSpec }) {
  const hue = HUES[spec.hue];
  const trails = spec.trails ?? 3;
  const position = {
    top: spec.top as number | undefined,
    left: spec.left as number | undefined,
    right: spec.right as number | undefined,
    bottom: spec.bottom as number | undefined
  };

  return (
    <View
      pointerEvents="none"
      style={[
        styles.craftAnchor,
        position,
        { opacity: spec.opacity, transform: [{ rotate: `${spec.angleDeg}deg` }, { scale: spec.scale }] }
      ]}
    >
      {/* Atmospheric glow halo so the craft reads as lit against deep space. */}
      <View style={[styles.halo, { backgroundColor: hue.glow, shadowColor: hue.edge }]} pointerEvents="none" />

      {/* Wake: tapered luminous trails behind the craft (compressed → speed). */}
      <View style={styles.wake} pointerEvents="none">
        {Array.from({ length: trails }).map((_, index) => {
          const t = index / Math.max(1, trails - 1); // 0 (nearest craft) → 1 (farthest)
          return (
            <LinearGradient
              key={index}
              start={{ x: 0, y: 0.5 }}
              end={{ x: 1, y: 0.5 }}
              colors={["rgba(0,0,0,0)", `${hue.wake}${(0.85 - t * 0.5).toFixed(2)})`]}
              style={[
                styles.wakeLine,
                {
                  width: BODY_W * (1.9 - t * 0.6),
                  top: BODY_H / 2 - 0.75 + (index - (trails - 1) / 2) * 3.2,
                  height: 2 - t
                }
              ]}
            />
          );
        })}
      </View>

      {/* Saucer body with a bright hue edge-light rim. */}
      <View style={styles.body} pointerEvents="none">
        <LinearGradient
          start={{ x: 0, y: 0 }}
          end={{ x: 0, y: 1 }}
          colors={[hue.body, "#0a1626", "#050d18"]}
          style={styles.bodyFill}
        />
        <View style={[styles.bodyEdge, { borderColor: hue.edge }]} />
        {/* Dome */}
        <View style={styles.dome}>
          <LinearGradient
            start={{ x: 0, y: 0 }}
            end={{ x: 0, y: 1 }}
            colors={[hue.edge, hue.body]}
            style={styles.domeFill}
          />
        </View>
      </View>

      {/* Forward-facing nose glow at the leading edge (right = front). */}
      <View style={[styles.noseGlow, { backgroundColor: hue.edge, shadowColor: hue.edge }]} pointerEvents="none" />
    </View>
  );
}

export const StaticUFOField = memo(function StaticUFOField() {
  return (
    <View
      style={StyleSheet.absoluteFill}
      pointerEvents="none"
      importantForAccessibility="no-hide-descendants"
      accessibilityElementsHidden
    >
      {FLEET.map((spec) => (
        <Craft key={spec.id} spec={spec} />
      ))}
    </View>
  );
});

const styles = StyleSheet.create({
  craftAnchor: {
    position: "absolute",
    width: BODY_W,
    height: BODY_H,
    alignItems: "center",
    justifyContent: "center"
  },
  halo: {
    position: "absolute",
    width: BODY_W * 1.5,
    height: BODY_W * 0.72,
    left: -(BODY_W * 0.25),
    top: (BODY_H - BODY_W * 0.72) / 2,
    borderRadius: BODY_W * 0.75,
    opacity: 0.9,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.9,
    shadowRadius: 14
  },
  wake: {
    position: "absolute",
    right: BODY_W * 0.7,
    top: 0,
    height: BODY_H
  },
  wakeLine: {
    position: "absolute",
    right: 0,
    borderRadius: 1
  },
  body: {
    width: BODY_W,
    height: BODY_H,
    borderRadius: BODY_H,
    overflow: "hidden",
    justifyContent: "center",
    alignItems: "center"
  },
  bodyFill: {
    ...StyleSheet.absoluteFillObject
  },
  bodyEdge: {
    ...StyleSheet.absoluteFillObject,
    borderRadius: BODY_H,
    borderWidth: 1.25,
    opacity: 0.95
  },
  dome: {
    position: "absolute",
    top: -BODY_H * 0.42,
    width: BODY_W * 0.42,
    height: BODY_H * 0.85,
    borderTopLeftRadius: BODY_W * 0.21,
    borderTopRightRadius: BODY_W * 0.21,
    overflow: "hidden"
  },
  domeFill: {
    ...StyleSheet.absoluteFillObject,
    opacity: 0.8
  },
  noseGlow: {
    position: "absolute",
    right: -4,
    top: BODY_H / 2 - 5,
    width: 10,
    height: 10,
    borderRadius: 5,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 1,
    shadowRadius: 6
  }
});
