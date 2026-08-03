/**
 * The state-line renderer — one of the three components this mission adds.
 *
 * A card's state line is the only live information on it, so this component has
 * one job and two rules.
 *
 * RULE 1: NEVER COLOUR ALONE. The LED's colour repeats what the words already
 * say. A seller who cannot distinguish amber from green reads "3 low stock" and
 * loses nothing; a screen-reader user hears the text, never the tone. The dot is
 * marked decorative for exactly that reason.
 *
 * RULE 2: NEVER TRUNCATE. `numberOfLines` is deliberately absent. At the largest
 * text sizes the line wraps and the card grows — a state line reading "2 orders
 * to fulf…" is worse than no line at all, because the seller cannot tell whether
 * the missing part said "today" or "this week".
 *
 * The blink is a signal, not decoration: it marks a state that is degrading —
 * stock running out, a campaign that cannot deliver, a review still open — and
 * it rests fully lit so a stopped blink never reads as a broken indicator. It
 * pauses when the app is backgrounded and does not exist under reduce-motion,
 * both inherited from `useStoreAmbient`.
 */

import { Animated, StyleSheet, Text, View } from "react-native";
import type { HubStateLine } from "../../api/businessHub";
import { hubLight } from "../../theme/hubLight";
import { useStoreAmbient } from "../../theme/storeMotion";

/**
 * LED periods, in ms, per the motion grammar. Three tempos carrying three
 * meanings: calm, urgent, and "we are working on it".
 */
export const HUB_LED = {
  /** Healthy states — a slow ping, closer to breathing than blinking. */
  greenPing: 2200,
  /** Attention states — faster, because the seller should notice. */
  warnBlink: 1300,
  /** Verification in review — between the two: active, but nothing to do. */
  reviewBlink: 1600
} as const;

function periodFor(tone: HubStateLine["tone"]): number {
  if (tone === "green") return HUB_LED.greenPing;
  if (tone === "review") return HUB_LED.reviewBlink;
  return HUB_LED.warnBlink;
}

export type StateLineProps = {
  state: HubStateLine;
  reducedMotion: boolean;
};

export function StateLine({ state, reducedMotion }: StateLineProps) {
  const color = hubLight.tone[state.tone];
  const pulse = useStoreAmbient(periodFor(state.tone), reducedMotion, {
    enabled: state.blink,
    resetTo: 1,
    pingPong: true
  });

  return (
    // The whole line is hidden from the accessibility tree: the card that owns
    // it announces title, subtitle and state as ONE element (see
    // `cardAccessibilityLabel`), so exposing this separately would make a
    // screen-reader user swipe twice through the same card.
    <View style={styles.row} accessibilityElementsHidden importantForAccessibility="no">
      <Animated.View
        style={[
          styles.dot,
          { backgroundColor: color },
          state.blink ? { opacity: pulse } : null
        ]}
      />
      <Text style={[styles.text, { color }]}>{state.text}</Text>
    </View>
  );
}

const DOT = 7;

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "flex-start", gap: 6 },
  dot: { width: DOT, height: DOT, borderRadius: DOT / 2, marginTop: 5 },
  text: { flex: 1, fontSize: 12, fontWeight: "700", lineHeight: 16 }
});
