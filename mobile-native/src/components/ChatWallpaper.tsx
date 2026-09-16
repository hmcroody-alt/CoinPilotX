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
 * wallpaper replaced by a flat fill, which is what the Reduce Transparency and
 * White-theme paths below actually do.
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

  // White theme's promise is a plain page, and Reduce Transparency's promise is
  // that nothing is layered over anything. Both get the flat theme background:
  // still opaque, still first-commit, just without the depth.
  if (!profile.enabled || theme.reduceTransparency) {
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

  const stars = CHAT_WALLPAPER_STARS.slice(0, spec.stars);
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
        {spec.shapes.map((shape, index) => (
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
        that is supposed to look like a wallpaper.
      */}
      <LinearGradient colors={spec.scrim} style={StyleSheet.absoluteFill} />
    </View>
  );
});

const STAR_COLOR = "#CDEBFA";

const styles = StyleSheet.create({
  depth: { ...StyleSheet.absoluteFillObject },
  root: { ...StyleSheet.absoluteFillObject, overflow: "hidden" }
});
