/**
 * The Selling / Buying segmented control.
 *
 * This is the most consequential control on the screen: it does not navigate,
 * it changes who you are. Everything below the search bar is replaced, and the
 * two sides have different headers, different data and different intent.
 *
 * Two things follow from that, and both are visible in the code below.
 *
 * **It is a tab list, not a pair of buttons.** `accessibilityRole="tab"` inside
 * a `tablist` tells a screen reader that exactly one of two is selected and that
 * choosing the other replaces content in place. Two buttons would announce as
 * two independent actions, which is a different and wrong promise.
 *
 * **The thumb slides.** `useStoreTabIndicator` is reused wholesale rather than
 * reimplemented — the Store filter tabs already solved "measure both, animate
 * left and width between them", and a second copy would drift. The slide is the
 * one piece of motion here that carries meaning: it shows which side you came
 * from, so a mis-tap is legible instead of just surprising.
 */

import { useCallback, useRef, useState } from "react";
import {
  Animated,
  Pressable,
  StyleSheet,
  Text,
  View,
  type LayoutChangeEvent
} from "react-native";
import { storeLight } from "../../theme/storeLight";
import { useStoreTabIndicator } from "../../theme/storeMotion";

export type MarketplaceMode = "selling" | "buying";

export const MARKETPLACE_MODES: readonly MarketplaceMode[] = ["selling", "buying"];

const MODE_LABELS: Record<MarketplaceMode, string> = {
  selling: "Selling",
  buying: "Buying"
};

const MODE_HINTS: Record<MarketplaceMode, string> = {
  selling: "Your items and the offers waiting on you",
  buying: "Items for sale near you"
};

export type ModeToggleProps = {
  mode: MarketplaceMode;
  onChange: (next: MarketplaceMode) => void;
  reducedMotion: boolean;
};

export function ModeToggle({ mode, onChange, reducedMotion }: ModeToggleProps) {
  const layouts = useRef<Partial<Record<MarketplaceMode, { x: number; width: number }>>>({});
  const [target, setTarget] = useState({ x: 0, width: 0 });
  const thumb = useStoreTabIndicator(target, reducedMotion);

  const onSegmentLayout = useCallback(
    (key: MarketplaceMode) => (event: LayoutChangeEvent) => {
      const { x, width } = event.nativeEvent.layout;
      layouts.current[key] = { x, width };
      // Measuring the selected segment on first layout is what puts the thumb
      // under the right word on first paint, instead of sliding in from zero.
      if (key === mode) setTarget({ x, width });
    },
    [mode]
  );

  const select = useCallback(
    (key: MarketplaceMode) => {
      if (key === mode) return;
      const layout = layouts.current[key];
      if (layout) setTarget(layout);
      onChange(key);
    },
    [mode, onChange]
  );

  return (
    <View style={styles.wrap} accessibilityRole="tablist">
      <Animated.View
        pointerEvents="none"
        style={[styles.thumb, { left: thumb.x, width: thumb.width }]}
      />
      {MARKETPLACE_MODES.map((key) => {
        const selected = key === mode;
        return (
          <Pressable
            key={key}
            onLayout={onSegmentLayout(key)}
            onPress={() => select(key)}
            style={styles.segment}
            accessibilityRole="tab"
            accessibilityState={{ selected }}
            accessibilityLabel={MODE_LABELS[key]}
            accessibilityHint={MODE_HINTS[key]}
          >
            <Text style={[styles.label, selected && styles.labelActive]}>{MODE_LABELS[key]}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flexDirection: "row",
    alignSelf: "stretch",
    padding: 3,
    borderRadius: storeLight.radius.pill,
    // Sits on the navy header, so the track is a lightened wash of it rather
    // than a card colour — a white track would read as a second surface.
    backgroundColor: "rgba(255, 255, 255, 0.12)"
  },
  thumb: {
    position: "absolute",
    top: 3,
    bottom: 3,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.pill
  },
  segment: {
    flex: 1,
    minHeight: 34,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 12
  },
  label: { fontSize: 13, fontWeight: "700", color: storeLight.text.onDarkMuted },
  labelActive: { color: storeLight.text.primary }
});
