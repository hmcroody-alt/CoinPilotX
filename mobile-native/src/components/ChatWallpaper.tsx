import { LinearGradient } from "expo-linear-gradient";
import { memo } from "react";
import { StyleSheet, View, ViewStyle } from "react-native";
import { useTheme } from "../theme/ThemeContext";
import {
  CHAT_WALLPAPER_STARS,
  ChatWallpaperId,
  resolveChatWallpaper
} from "../theme/chatWallpaper";

type Props = {
  /**
   * The viewer's `appearance.wallpaper` for this conversation. Anything
   * unrecognised or absent resolves to the PulseSoc Graphite default, so the
   * caller may pass a raw stored value straight through.
   */
  wallpaper?: ChatWallpaperId | string | null;
  style?: ViewStyle;
  testID?: string;
};

/**
 * The conversation background, and nothing else.
 *
 * Background only: it takes no touches, is hidden from accessibility, and
 * carries no information — a conversation is exactly as usable with the
 * wallpaper replaced by a flat fill, which is what the high-contrast and
 * White-theme paths below actually do.
 *
 * ## What Reduce Transparency is owed here
 *
 * Reduce Transparency used to land in that same flat-fill path, and that was
 * wrong. Its promise is that nothing is layered over anything — it is about
 * *translucency*, not about colour. A wallpaper spec is two opaque layers (an
 * opaque `base` and an opaque `gradient`) and three alpha ones (the soft
 * `shapes`, the `stars`, and the `scrim`). Only the second group is layering.
 * Collapsing the whole thing to `colors.background` also discarded the opaque
 * group, which meant a person who had merely switched off blur lost the
 * approved graphite canvas and got near-black (`#050910`) instead — a palette
 * substitution nobody asked for, and a visible parity break against every
 * other device.
 *
 * So Reduce Transparency now keeps `base` + `gradient` and drops the three
 * alpha layers. The result is strictly opaque, still paints in the first
 * commit, and is the same graphite everyone else sees. High contrast still
 * takes the flat fill, because that mode genuinely *does* substitute the
 * palette (`HIGH_CONTRAST_DARK`) and the graphite ramp is not audited against
 * it.
 *
 * For the default wallpaper the two paths happen to be identical anyway —
 * PulseSoc Graphite has no shapes, no stars and a fully transparent scrim — so
 * this is the rare accessibility branch that costs the user nothing at all.
 *
 * It is also deliberately dumb. No state, no effects, no timers, no listeners,
 * no animation, no image decode. `memo` plus a string prop means a render of
 * the chat screen — which happens on every keystroke in the composer and every
 * arriving message — does not re-render the background at all, and scrolling
 * the message list composites an already-rasterised layer.
 *
 * The opaque `base` colour is on the outermost view on purpose: it is what is
 * on screen for the first commit, so a conversation never opens on black and
 * then fades the background in.
 */
export const ChatWallpaper = memo(function ChatWallpaper({ wallpaper, style, testID = "chat-wallpaper" }: Props) {
  const theme = useTheme();
  const profile = theme.galacticBackground;
  const spec = resolveChatWallpaper(wallpaper);

  // White theme's promise is a plain page, and high contrast replaces the
  // palette outright. Both get the flat theme background: still opaque, still
  // first-commit, just without the wallpaper. Reduce Transparency is
  // deliberately NOT in this condition — see the note above.
  if (!profile.enabled || theme.highContrast) {
    return (
      <View
        testID={testID}
        pointerEvents="none"
        accessibilityElementsHidden
        importantForAccessibility="no-hide-descendants"
        style={[styles.root, { backgroundColor: theme.colors.background }, style]}
      />
    );
  }

  // Light appearances keep a bright page; a navy cosmic field under dark text
  // would be unreadable. Same haze the rest of the light surfaces use.
  if (profile.variant === "light") {
    return (
      <View
        testID={testID}
        pointerEvents="none"
        accessibilityElementsHidden
        importantForAccessibility="no-hide-descendants"
        style={[styles.root, { backgroundColor: theme.colors.background }, style]}
      >
        <LinearGradient colors={["#eef4fb", "#e9f1fa", "#e3edf9"]} locations={[0, 0.48, 1]} style={StyleSheet.absoluteFill} />
      </View>
    );
  }

  /**
   * Reduce Transparency keeps the two opaque layers and drops the three alpha
   * ones. Everything below this line that is conditional on `opaqueOnly` is an
   * alpha layer; the `base` and the `gradient` are not, and so are unaffected.
   */
  const opaqueOnly = theme.reduceTransparency;
  const shapes = opaqueOnly ? [] : spec.shapes;
  const stars = opaqueOnly ? [] : CHAT_WALLPAPER_STARS.slice(0, spec.stars);
  return (
    <View
      testID={testID}
      pointerEvents="none"
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
      style={[styles.root, { backgroundColor: spec.base }, style]}
    >
      <LinearGradient colors={spec.gradient} locations={spec.locations} style={StyleSheet.absoluteFill} />
      {/*
        `intensity` dims the depth only, never the gradient underneath it. The
        Black theme wants a quieter field over true black, not a translucent
        background that lets the window show through.
      */}
      <View style={[styles.depth, { opacity: profile.intensity }]}>
        {shapes.map((shape, index) => (
          <View
            key={`shape-${index}`}
            style={{
              backgroundColor: shape.color,
              borderRadius: shape.radius,
              height: shape.height,
              left: `${shape.x}%`,
              marginLeft: -shape.width / 2,
              marginTop: -shape.height / 2,
              position: "absolute",
              top: `${shape.y}%`,
              transform: shape.rotate ? [{ rotate: `${shape.rotate}deg` }] : undefined,
              width: shape.width
            }}
          />
        ))}
        {stars.map(([left, top, size, opacity]) => (
          <View
            key={`star-${left}-${top}`}
            style={{
              backgroundColor: STAR_COLOR,
              borderRadius: size,
              height: size,
              left: `${left}%`,
              opacity,
              position: "absolute",
              top: `${top}%`,
              width: size
            }}
          />
        ))}
      </View>
      {/*
        Holds the two edges where chrome meets the wallpaper — the header above
        and the composer below — without darkening the middle, which is the part
        that is supposed to look like a wallpaper. It is an alpha layer, so
        Reduce Transparency drops it; the edges it was holding belong to the
        decorated wallpapers, and those have no depth to hold down in this mode.
      */}
      {opaqueOnly ? null : <LinearGradient colors={spec.scrim} style={StyleSheet.absoluteFill} />}
    </View>
  );
});

const STAR_COLOR = "#CDEBFA";

const styles = StyleSheet.create({
  depth: { ...StyleSheet.absoluteFillObject },
  root: { ...StyleSheet.absoluteFillObject, overflow: "hidden" }
});
