import { LinearGradient } from "expo-linear-gradient";
import { memo } from "react";
import { StyleSheet } from "react-native";
import { colors } from "../theme/colors";
import { profileSurface } from "../theme/profileGraphite";

/**
 * The Profile canvas: one opaque vertical graphite run, and nothing else.
 *
 * ## What this replaced, and why replacing it was the fix
 *
 * Profile used to paint its canvas with `<GalacticAtmosphere variant="profile">`
 * — a near-black `#02050A → #06101C` gradient carrying 23 stars, two drifting
 * nebulae, a planet, a galaxy smear, three dust motes and a closing scrim. That
 * layer *is* the cloudy, smoky appearance the design review rejected. It could
 * not be tuned into graphite: the haze is the nebulae and the scrim, not the
 * gradient under them, and dimming them would have left a dim haze.
 *
 * So the canvas is now a gradient with two stops and no children. Two stops
 * because the approved treatment is a restrained vertical run — the chat surface
 * uses exactly the same pair, from the same shared ramp — and because a flat
 * single colour across a 1000pt scroll reads as a dead sheet rather than depth.
 * The stops are two steps apart on purpose; any further apart and it reads as a
 * graphic instead of as a surface.
 *
 * `GalacticAtmosphere` itself is untouched. Home, Reels, Messages and every
 * other variant keep their atmosphere; this only stops Profile subscribing to it.
 *
 * ## Why this is a background and not a tint
 *
 * It is the bottom-most child of the screen and fully opaque, so nothing is
 * *composited over* the UI — which is the specific failure mode that produced
 * the rejected look. Content draws on top of it, in the ordinary way. There is
 * no `BlurView`, no animation, no shadow and no per-frame work of any kind: two
 * stops resolved once, memoised, and then a static layer for the life of the
 * screen. Reduce Motion and Reduce Transparency have nothing to act on here,
 * which is the strongest form of honouring them.
 *
 * ## Themes
 *
 * The stops come from `profileSurface`, so Black paints its true black
 * and White its plain white — flat, because those palettes resolve both stops to
 * the same value and a gradient between equal colours is a solid fill.
 */
export const ProfileCanvas = memo(function ProfileCanvas({
  testID = "profile-canvas"
}: {
  testID?: string;
}) {
  const surface = profileSurface(colors);
  return (
    <LinearGradient
      testID={testID}
      colors={[surface.canvasTop, surface.canvasBottom]}
      style={StyleSheet.absoluteFill}
      pointerEvents="none"
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    />
  );
});
