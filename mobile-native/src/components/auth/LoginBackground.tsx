import { StyleSheet } from "react-native";
import { LinearGradient } from "expo-linear-gradient";

/**
 * Full-screen deep-blue backdrop for the auth screens (Login + Signup), matched
 * to the canonical PulseSoc brand mark's own background so the transparent logo
 * blends in with no visible image boundary. Colors are sampled directly from
 * assets/brand/pulsesoc-logo-master.png: a soft #021058 glow fading into the
 * #010730 navy field and a deeper #000520 base.
 *
 * This intentionally paints over the shared root `PulseBackground` — the first
 * screen anyone sees is brand-navy, not the in-app atmosphere.
 */
export function LoginBackground() {
  return (
    <LinearGradient
      colors={["#021058", "#010730", "#000520"]}
      locations={[0, 0.45, 1]}
      start={{ x: 0.5, y: 0 }}
      end={{ x: 0.5, y: 1 }}
      style={StyleSheet.absoluteFill}
      pointerEvents="none"
    />
  );
}
